"""SQLAlchemy 2.x async engine with one session per request.

    pip install "mini-aiohttp[sqlalchemy]" asyncpg
    DATABASE_URL=postgresql+asyncpg://user:pass@localhost/app python examples/sqlalchemy_app.py
"""
import asyncio
import os

from sqlalchemy import text

from mini_aiohttp import Response, Server, serve
from mini_aiohttp.db import SQLAlchemyEngine

db = SQLAlchemyEngine(os.environ["DATABASE_URL"], pool_size=10)
server = Server()


@server.route("/users/{user_id}")
async def get_user(req):
    async with db.session() as session:
        result = await session.execute(
            text("SELECT name FROM users WHERE id = :id"), {"id": int(req.params["user_id"])}
        )
        row = result.first()
    if row is None:
        return Response(404, "not found")
    return Response.json({"name": row[0]})


async def main():
    await db.open()
    await serve(server, "127.0.0.1", 8080, shutdown=[db.close])


if __name__ == "__main__":
    asyncio.run(main())
