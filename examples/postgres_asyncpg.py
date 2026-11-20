"""PostgreSQL with asyncpg: one pool shared by all handlers, closed on shutdown.

    pip install "mini-aiohttp[asyncpg]"
    DATABASE_URL=postgresql://user:pass@localhost/app python examples/postgres_asyncpg.py
"""
import asyncio
import os

from mini_aiohttp import Response, Server, serve
from mini_aiohttp.db import PostgresPool

db = PostgresPool(os.environ["DATABASE_URL"], min_size=1, max_size=10)
server = Server()


@server.route("/users/{user_id}")
async def get_user(req):
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, name FROM users WHERE id = $1", int(req.params["user_id"]))
    if row is None:
        return Response(404, "not found")
    return Response.json({"id": row["id"], "name": row["name"]})


async def main():
    await db.open()
    await serve(server, "127.0.0.1", 8080, grace=10, shutdown=[db.close])


if __name__ == "__main__":
    asyncio.run(main())
