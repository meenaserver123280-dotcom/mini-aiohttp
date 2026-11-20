"""MongoDB (motor) and Firebase Realtime Database (REST) in one handler.

    pip install "mini-aiohttp[mongo]"
    MONGO_URI=mongodb://localhost:27017 FIREBASE_URL=https://demo-default-rtdb.firebaseio.com \\
        python examples/mongo_and_firebase.py
"""
import asyncio
import os

from mini_aiohttp import ClientSession, Request, Response, Server, serve
from mini_aiohttp.db import FirebaseRTDB, MongoDatabase

mongo = MongoDatabase(os.environ.get("MONGO_URI", "mongodb://localhost:27017"), "app")
server = Server()
session = None
firebase = None


@server.route("/notes", method="POST")
async def add_note(req: Request):
    doc = req.json()
    result = await mongo.db["notes"].insert_one({"text": doc["text"]})
    await firebase.push("notes", {"id": str(result.inserted_id)})
    return Response.json({"id": str(result.inserted_id)})


async def main():
    global session, firebase
    await mongo.open()
    session = ClientSession()
    firebase = FirebaseRTDB(session, os.environ["FIREBASE_URL"], auth_token=os.environ.get("FIREBASE_TOKEN"))
    await serve(server, "127.0.0.1", 8080, shutdown=[mongo.close])


if __name__ == "__main__":
    asyncio.run(main())
