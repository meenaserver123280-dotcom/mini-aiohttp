"""Check that an installed wheel loads its native extension and exposes the public API."""
import importlib
import sys

native = importlib.import_module("mini_aiohttp._native")
package = importlib.import_module("mini_aiohttp")

assert hasattr(native, "parse_request_head"), "native extension is missing parse_request_head"
assert hasattr(native, "serialize_response"), "native extension is missing serialize_response"
assert package.ASGIApp is not None, "ASGIApp is not exported"
print(f"mini-aiohttp {package.__version__} OK on Python {sys.version_info.major}.{sys.version_info.minor}")
