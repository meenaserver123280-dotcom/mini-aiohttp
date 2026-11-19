"""mini-aiohttp: a small asyncio HTTP client and server toolkit."""
from ._optional import use_uvloop
from .auto import AutoClientSession
from .batch import fetch_all, gather_requests
from .client import ClientError, ClientSession
from .compression import gzip_middleware
from .cookies import CookieJar
from .http2 import HTTP2Client, HTTP2Server
from .http3 import HTTP3Client, HTTP3Server
from .multipart import UploadedFile
from .response import Request, Response
from .security import RateLimiter, cors, ip_policy, require_json, security_headers, set_cookie
from .server import Server
from .websocket import WebSocket, WebSocketError, WebSocketServer

__version__ = "0.5.9"
__all__ = [
    "ClientSession", "ClientError", "CookieJar", "Server", "Request", "Response",
    "UploadedFile", "RateLimiter", "cors", "ip_policy", "require_json",
    "security_headers", "set_cookie", "gather_requests", "fetch_all",
    "WebSocket", "WebSocketServer", "WebSocketError", "HTTP2Client", "HTTP2Server",
    "HTTP3Client", "HTTP3Server", "AutoClientSession", "use_uvloop", "gzip_middleware",
]
