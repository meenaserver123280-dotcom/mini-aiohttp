"""Database adapters: each one opens a client, hands it to your handlers and closes it on shutdown.

Drivers are imported only when open() runs, so mini-aiohttp keeps no dependencies. Install
the extra for the driver you use:

    pip install "mini-aiohttp[asyncpg]"     # PostgreSQL via asyncpg
    pip install "mini-aiohttp[redis]"       # Redis via redis.asyncio
    pip install "mini-aiohttp[sqlalchemy]"  # SQLAlchemy 2.x async engine
    pip install "mini-aiohttp[mongo]"       # MongoDB via motor

FirebaseRTDB needs no extra: it calls the Realtime Database REST API through a ClientSession.

    db = PostgresPool(os.environ["DATABASE_URL"])
    await db.open()
    await serve(server, shutdown=[db.close])

Every adapter has open() and close() and works as an async context manager.
"""
from __future__ import annotations

import importlib
from urllib.parse import quote

from .retry import RetryPolicy


def _driver(module: str, extra: str):
    """Import a driver module, or raise ImportError with the install command."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(
            f'{module} is not installed; run: pip install "mini-aiohttp[{extra}]"'
        ) from exc


class _Adapter:
    async def open(self):  # pragma: no cover - overridden
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    async def __aenter__(self):
        return await self.open()

    async def __aexit__(self, *exc_info) -> None:
        await self.close()


class PostgresPool(_Adapter):
    """asyncpg connection pool. Use `db.pool.acquire()` in handlers."""

    def __init__(self, dsn: str, **options):
        self.dsn = dsn
        self.options = options
        self.pool = None

    async def open(self):
        if self.pool is None:
            asyncpg = _driver("asyncpg", "asyncpg")
            self.pool = await asyncpg.create_pool(self.dsn, **self.options)
        return self

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None


class RedisClient(_Adapter):
    """redis.asyncio client. `client` is available after open()."""

    def __init__(self, url: str = "redis://localhost:6379/0", **options):
        self.url = url
        self.options = options
        self.client = None

    async def open(self):
        if self.client is None:
            redis_asyncio = _driver("redis.asyncio", "redis")
            self.client = redis_asyncio.from_url(self.url, **self.options)
        return self

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None


class SQLAlchemyEngine(_Adapter):
    """SQLAlchemy 2.x async engine. Use `async with db.session() as session:` in handlers."""

    def __init__(self, url: str, **options):
        self.url = url
        self.options = options
        self.engine = None
        self._sessions = None

    async def open(self):
        if self.engine is None:
            sa_async = _driver("sqlalchemy.ext.asyncio", "sqlalchemy")
            self.engine = sa_async.create_async_engine(self.url, **self.options)
            self._sessions = sa_async.async_sessionmaker(self.engine, expire_on_commit=False)
        return self

    def session(self):
        if self._sessions is None:
            raise RuntimeError("call open() before session()")
        return self._sessions()

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None
            self._sessions = None


class MongoDatabase(_Adapter):
    """motor client for one MongoDB database. `db.db["collection"]` is available after open()."""

    def __init__(self, uri: str, database: str, **options):
        self.uri = uri
        self.name = database
        self.options = options
        self.client = None

    async def open(self):
        if self.client is None:
            motor = _driver("motor.motor_asyncio", "mongo")
            self.client = motor.AsyncIOMotorClient(self.uri, **self.options)
        return self

    @property
    def db(self):
        if self.client is None:
            raise RuntimeError("call open() before using db")
        return self.client[self.name]

    async def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None


class FirebaseError(Exception):
    def __init__(self, status: int):
        super().__init__(f"Firebase request failed with HTTP {status}")
        self.status = status


class FirebaseRTDB:
    """Firebase Realtime Database over its REST API, through an existing ClientSession.

        db = FirebaseRTDB(session, "https://your-project-default-rtdb.firebaseio.com",
                          auth_token=os.environ.get("FIREBASE_TOKEN"))
        await db.put("users/42", {"name": "Ada"})
        user = await db.get("users/42")

    auth_token is optional (an ID token or a database secret). Errors raise FirebaseError with
    the HTTP status only, so the token never appears in logs.
    """

    def __init__(self, session, database_url: str, *, auth_token: str | None = None,
                 retry: RetryPolicy | None = None):
        if not database_url.startswith("https://"):
            raise ValueError("database_url must start with https://")
        self._session = session
        self._base = database_url.rstrip("/")
        self._auth = auth_token
        self._retry = retry or RetryPolicy(max_retries=2, base_delay=0.25)

    def _url(self, path: str) -> str:
        segments = [quote(seg, safe="") for seg in path.split("/") if seg]
        url = f"{self._base}/{'/'.join(segments)}.json" if segments else f"{self._base}/.json"
        if self._auth:
            url += "?auth=" + quote(self._auth, safe="")
        return url

    async def _request(self, method: str, path: str, body=None):
        kwargs = {} if body is None else {"json": body}
        response = await self._retry.send(self._session, method, self._url(path), **kwargs)
        if response.status >= 400:
            raise FirebaseError(response.status)
        if method == "DELETE":
            return None
        return response.json()

    async def get(self, path: str):
        return await self._request("GET", path)

    async def put(self, path: str, data):
        return await self._request("PUT", path, data)

    async def patch(self, path: str, data):
        return await self._request("PATCH", path, data)

    async def push(self, path: str, data):
        """Append a child with a generated key. Returns {"name": key}."""
        return await self._request("POST", path, data)

    async def delete(self, path: str) -> None:
        await self._request("DELETE", path)
