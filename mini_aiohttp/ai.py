"""Chat helper for OpenAI-compatible model APIs.

Most hosted and open-model providers expose the same ``/chat/completions`` endpoint:
Groq, Google Gemini (its OpenAI-compatible endpoint), OpenRouter, and local servers
such as Ollama. The helpers here only build requests and read replies, so no provider
SDK is needed.

    async with ClientSession() as session:
        ai = groq(session, api_key=os.environ["GROQ_API_KEY"], model="<model name>")
        print(await ai.chat([{"role": "user", "content": "Hello"}]))

        async for piece in ai.stream([{"role": "user", "content": "Tell me a story"}]):
            print(piece, end="")

Model names change often, so every preset requires ``model=``. Check the provider's
model list for current names.
"""
from __future__ import annotations

import json

_MAX_ERROR_TEXT = 300


class AIError(Exception):
    """The provider returned an error, or a reply that could not be understood.

    ``status`` is the HTTP status code. The API key is never included in the message.
    """

    def __init__(self, status: int, message: str):
        super().__init__(f"AI provider error {status}: {message}")
        self.status = status
        self.message = message


def _provider_message(resp) -> str:
    raw = getattr(resp, "body", b"") or b""
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:_MAX_ERROR_TEXT]
        if isinstance(error, str):
            return error[:_MAX_ERROR_TEXT]
    return raw.decode("utf-8", "replace")[:_MAX_ERROR_TEXT]


class ChatClient:
    """Send chat requests to one OpenAI-compatible endpoint.

    ``base_url`` is the API root, for example ``https://api.groq.com/openai/v1``.
    ``api_key`` is sent as a Bearer token. Pass ``None`` for local servers that need no key.
    Extra keyword arguments to ``chat()`` and ``stream()`` (``temperature``,
    ``max_tokens`` and so on) are sent unchanged in the request body.
    """

    def __init__(
        self,
        session,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout: float = 60.0,
        headers: dict | None = None,
    ):
        if not base_url.startswith(("https://", "http://")):
            raise ValueError("base_url must start with https:// or http://")
        if not model:
            raise ValueError("model is required")
        self._session = session
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self._model = model
        self._timeout = timeout
        self._headers = dict(headers or {})

    def _request_headers(self) -> dict:
        out = {"Content-Type": "application/json", **self._headers}
        if self._key:
            out["Authorization"] = f"Bearer {self._key}"
        return out

    def _body(self, messages, stream: bool, options: dict) -> dict:
        body = {"model": self._model, "messages": list(messages), "stream": stream}
        body.update(options)
        return body

    async def chat(self, messages, **options) -> str:
        """Return the full reply text for ``messages``."""
        resp = await self._session.request(
            "POST", self._url, headers=self._request_headers(),
            json=self._body(messages, False, options), timeout=self._timeout,
        )
        if resp.status != 200:
            raise AIError(resp.status, _provider_message(resp))
        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIError(resp.status, "unexpected response format") from exc

    async def stream(self, messages, **options):
        """Yield reply text pieces as the provider produces them."""
        resp = await self._session.request(
            "POST", self._url, headers=self._request_headers(),
            json=self._body(messages, True, options), timeout=self._timeout, stream=True,
        )
        if resp.status != 200:
            try:
                await resp.aread()
                message = _provider_message(resp)
            finally:
                await resp.aclose()
            raise AIError(resp.status, message)
        try:
            buffer = b""
            async for chunk in resp.iter_chunks():
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == b"[DONE]":
                        return
                    try:
                        event = json.loads(payload)
                    except ValueError as exc:
                        raise AIError(resp.status, "invalid stream event") from exc
                    piece = _delta_text(event)
                    if piece:
                        yield piece
        finally:
            await resp.aclose()


def _delta_text(event) -> str:
    try:
        delta = event["choices"][0].get("delta") or {}
        return delta.get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


def groq(session, *, api_key: str, model: str, **options) -> ChatClient:
    """Groq's OpenAI-compatible endpoint."""
    return ChatClient(session, base_url="https://api.groq.com/openai/v1",
                      api_key=api_key, model=model, **options)


def gemini(session, *, api_key: str, model: str, **options) -> ChatClient:
    """Google Gemini through its OpenAI-compatible endpoint."""
    return ChatClient(session, base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                      api_key=api_key, model=model, **options)


def openrouter(session, *, api_key: str, model: str, **options) -> ChatClient:
    """OpenRouter, which routes to many open and hosted models."""
    return ChatClient(session, base_url="https://openrouter.ai/api/v1",
                      api_key=api_key, model=model, **options)


def ollama(session, *, model: str, host: str = "http://127.0.0.1:11434", **options) -> ChatClient:
    """A local Ollama server. No API key is needed."""
    return ChatClient(session, base_url=host.rstrip("/") + "/v1",
                      api_key=None, model=model, **options)


__all__ = ["AIError", "ChatClient", "groq", "gemini", "openrouter", "ollama"]
