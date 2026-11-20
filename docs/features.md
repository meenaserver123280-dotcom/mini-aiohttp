# mini-aiohttp features: route limits, background work, webhooks, databases

## Route-level rate limits and middleware

Rate limit one route, or wrap it with any middleware. Global middleware still runs first.

```python
from mini_aiohttp import RateLimiter, Server, route_limit, route_middleware

app = Server()
login_limiter = RateLimiter(rate=0.2, burst=5)  # 5 quick tries, then 1 every 5 s

@app.route("/login", method="POST")
@route_limit(login_limiter)
async def login(req):
    ...

@app.route("/admin")
@route_middleware(require_admin, audit_log)   # runs in the order listed
async def admin(req):
    ...
```

## Background tasks, worker queue and graceful shutdown

```python
import asyncio
from mini_aiohttp import BackgroundTasks, TaskQueue, Server, serve

background = BackgroundTasks()
background.spawn(refresh_cache(), name="cache")

async def handle_job(job):
    await send_email(job)

queue = TaskQueue(handle_job, workers=4, maxsize=1000, tasks=background)

@app.route("/signup", method="POST")
async def signup(req):
    await queue.put({"to": req.json()["email"]})   # returns at once
    return Response.json({"ok": True})

# Blocks until SIGINT or SIGTERM, then: stop accepting, drain requests, wait for
# background work, cancel leftovers after `grace`, and run hooks in the order given.
await serve(app, "0.0.0.0", 8080, grace=10, background=background,
            shutdown=[queue.close, db.close])
```

`serve()` takes an `stop_event` (an asyncio.Event) for tests or custom stop logic. On Windows,
signal handlers are not installed, so use `stop_event`.

## Telegram webhook

```python
from mini_aiohttp import ClientSession, TelegramWebhook, update_message

async def on_update(update):
    message = update_message(update)
    if message and message.get("text"):
        ...

session = ClientSession()
bot = TelegramWebhook(session, TOKEN, on_update, secret_token=TG_SECRET)
await bot.set_webhook("https://example.com/telegram", allowed_updates=["message"])
bot.attach(app, "/telegram")
```

Requests with a wrong or missing secret get 403. Updates are answered at once and handled in the
background, and duplicate `update_id`s are skipped. `bot.close()` waits for running handlers.
Use either polling (`TelegramPoller`) or the webhook, not both.

## Database adapters

Drivers are optional extras. The core library still has no dependencies.

| Adapter | Extra | Use |
|---|---|---|
| `PostgresPool` | `asyncpg` | `db.pool.acquire()` |
| `RedisClient` | `redis` | `db.client` (redis.asyncio) |
| `SQLAlchemyEngine` | `sqlalchemy` | `async with db.session() as s:` |
| `MongoDatabase` | `mongo` | `db.db["collection"]` (motor) |
| `FirebaseRTDB` | none | `get`, `put`, `patch`, `push`, `delete` over REST |

Full examples: `examples/postgres_asyncpg.py`, `examples/redis_cache.py`,
`examples/sqlalchemy_app.py`, `examples/mongo_and_firebase.py`.

```bash
pip install "mini-aiohttp[asyncpg,redis,sqlalchemy,mongo]"
```
