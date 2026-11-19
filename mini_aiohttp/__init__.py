"""mini-aiohttp: a small asyncio HTTP client and server toolkit."""
from ._optional import use_uvloop
from .asgi import ASGIApp
from .auto import AutoClientSession
from .batch import fetch_all, gather_requests
from .client import ClientError, ClientSession
from .compression import gzip_middleware
from .cookies import CookieJar
from .forms import FormError, parse_urlencoded
from .http2 import HTTP2Client, HTTP2Server
from .http3 import HTTP3Client, HTTP3Server
from .multipart import UploadedFile
from .proxy import HeaderRotator, merge_headers, socks5_url, tor_proxy
from .response import Request, Response
from .retry import RetryPolicy
from .security import RateLimiter, cors, ip_policy, require_json, security_headers, set_cookie
from .server import Server
from .sessions import Session, SessionMiddleware
from .static import StaticFiles
from .telegram import TelegramError, TelegramPoller
from .templates import Template, TemplateError, TemplateLoader, html_response
from .traffic import TrafficRecorderMiddleware
from .webhook import WebhookRouter
from .websocket import WebSocket, WebSocketError, WebSocketServer

__version__ = "0.10.0"
__all__ = [
    "ClientSession", "ClientError", "CookieJar", "Server", "Request", "Response",
    "UploadedFile", "RateLimiter", "cors", "ip_policy", "require_json",
    "security_headers", "set_cookie", "gather_requests", "fetch_all",
    "WebSocket", "WebSocketServer", "WebSocketError", "HTTP2Client", "HTTP2Server",
    "HTTP3Client", "HTTP3Server", "AutoClientSession", "use_uvloop", "gzip_middleware",
    "ASGIApp",
    "StaticFiles", "Session", "SessionMiddleware", "FormError", "parse_urlencoded",
    "Template", "TemplateLoader", "TemplateError", "html_response",
    "RetryPolicy", "socks5_url", "tor_proxy", "HeaderRotator", "merge_headers",
    "TrafficRecorderMiddleware", "WebhookRouter", "TelegramPoller", "TelegramError",
]
