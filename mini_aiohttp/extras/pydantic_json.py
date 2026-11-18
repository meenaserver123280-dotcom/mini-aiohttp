"""Typed JSON validation with pydantic (optional).

Requires: pip install "mini-aiohttp[pydantic]"   (pydantic v2)

    from pydantic import BaseModel
    from mini_aiohttp.extras.pydantic_json import validated_json

    class User(BaseModel):
        name: str
        age: int

    @app.route("/users", method="POST")
    @validated_json(User)
    async def create(req):
        user = req.model            # a validated User instance
        return Response.json({"ok": True, "name": user.name})

Invalid input gets 422 with pydantic's error list. Invalid JSON gets 400.
"""
from __future__ import annotations

from ..response import Response


def validated_json(model):
    """Decorator: parse the JSON body into `model`, store it as req.model, else reply 4xx."""
    try:
        import pydantic
    except ImportError as exc:
        raise RuntimeError('pydantic validation needs: pip install "mini-aiohttp[pydantic]"') from exc

    def decorator(handler):
        async def wrapper(req):
            try:
                data = req.json()
            except ValueError:
                return Response(400, "Body must be valid JSON")
            try:
                req.model = model.model_validate(data)
            except pydantic.ValidationError as exc:
                # include_context=False: 'ctx' can hold Python exception objects, which are
                # not JSON-serializable and would break the 422 response.
                errors = exc.errors(include_url=False, include_context=False)
                return Response.json({"detail": errors}, status=422)
            return await handler(req)

        wrapper.__name__ = getattr(handler, "__name__", "handler")
        return wrapper

    return decorator
