"""Benchmark mini-aiohttp against aiohttp with the same load generator.

The load generator uses raw asyncio sockets and keep-alive HTTP/1.1, so it does not
depend on either library. Both servers answer the same JSON route.

Usage:
    pip install aiohttp
    python benchmarks/bench.py run [--duration 10] [--concurrency 64]
    python benchmarks/bench.py run --only mini

Internal (used by 'run' to start each server in its own process):
    python benchmarks/bench.py serve mini|aiohttp PORT

Notes:
- Python load generators can become the bottleneck at high RPS. For final numbers,
  point an external tool (wrk, oha, hey) at the same servers and compare.
- Run on an idle machine. Results vary with CPU, Python version and load.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import socket
import statistics
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOST = "127.0.0.1"
PATH = "/"
PORTS = {"mini": 18080, "aiohttp": 18081}


# ---------- servers (run in a child process) ----------

def serve_mini(port: int) -> None:
    sys.path.insert(0, ROOT)
    from mini_aiohttp import Response, Server

    app = Server()

    @app.route(PATH)
    async def home(req):
        return Response.json({"ok": True})

    async def main():
        await app.start(HOST, port)
        try:
            await asyncio.Event().wait()
        finally:
            await app.close()

    asyncio.run(main())


def serve_aiohttp(port: int) -> None:
    from aiohttp import web

    async def home(request):
        return web.json_response({"ok": True})

    async def main():
        app = web.Application()
        app.router.add_get(PATH, home)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, HOST, port)
        await site.start()
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    asyncio.run(main())


# ---------- load generator ----------

async def _worker(port: int, stop_at: float, latencies: list[float], errors: list[str]) -> None:
    reader, writer = await asyncio.open_connection(HOST, port)
    request = f"GET {PATH} HTTP/1.1\r\nHost: {HOST}:{port}\r\n\r\n".encode("ascii")
    try:
        while time.perf_counter() < stop_at:
            started = time.perf_counter()
            writer.write(request)
            await writer.drain()
            head = await reader.readuntil(b"\r\n\r\n")
            status_line = head.split(b"\r\n", 1)[0]
            if b" 200 " not in status_line:
                errors.append(status_line.decode("latin-1", "replace"))
                raise RuntimeError(f"unexpected status: {status_line!r}")
            length = None
            for line in head.split(b"\r\n")[1:]:
                name, _, value = line.partition(b":")
                lname = name.strip().lower()
                if lname == b"content-length":
                    length = int(value.strip())
                elif lname == b"transfer-encoding":
                    raise RuntimeError("chunked responses are not supported by this load generator")
            if length is None:
                raise RuntimeError("response has no Content-Length")
            await reader.readexactly(length)
            latencies.append(time.perf_counter() - started)
    finally:
        writer.close()


async def _measure(port: int, duration: float, concurrency: int, warmup: float):
    # Warmup on the same server so the first requests do not skew the numbers.
    warm_latencies: list[float] = []
    warm_errors: list[str] = []
    await asyncio.gather(*(
        _worker(port, time.perf_counter() + warmup, warm_latencies, warm_errors)
        for _ in range(concurrency)
    ))

    latencies: list[float] = []
    errors: list[str] = []
    started = time.perf_counter()
    stop_at = started + duration
    await asyncio.gather(*(
        _worker(port, stop_at, latencies, errors) for _ in range(concurrency)
    ))
    elapsed = time.perf_counter() - started
    return latencies, errors, elapsed


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return float("nan")
    index = min(len(sorted_values) - 1, int(round(pct / 100 * (len(sorted_values) - 1))))
    return sorted_values[index]


# ---------- orchestration ----------

def _wait_for_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            if sock.connect_ex((HOST, port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"server on port {port} did not start")


def _run_one(name: str, duration: float, concurrency: int, warmup: float) -> dict:
    port = PORTS[name]
    child = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "serve", name, str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port)
        latencies, errors, elapsed = asyncio.run(_measure(port, duration, concurrency, warmup))
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
        if child.stderr is not None:
            stderr = child.stderr.read().decode("utf-8", "replace").strip()
        else:
            stderr = ""

    latencies.sort()
    return {
        "name": name,
        "requests": len(latencies),
        "errors": len(errors),
        "rps": len(latencies) / elapsed if elapsed else 0.0,
        "p50_ms": _percentile(latencies, 50) * 1000,
        "p99_ms": _percentile(latencies, 99) * 1000,
        "server_stderr": stderr[-500:] if stderr and errors == [] and len(latencies) == 0 else "",
    }


def _print_table(results: list[dict], duration: float, concurrency: int) -> None:
    print(f"\nduration={duration}s concurrency={concurrency} path={PATH}")
    print(f"{'library':<12}{'RPS':>12}{'p50 ms':>10}{'p99 ms':>10}{'requests':>12}{'errors':>8}")
    for r in results:
        print(
            f"{r['name']:<12}{r['rps']:>12.0f}{r['p50_ms']:>10.2f}{r['p99_ms']:>10.2f}"
            f"{r['requests']:>12}{r['errors']:>8}"
        )
        if r["server_stderr"]:
            print(f"  server stderr (tail): {r['server_stderr']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the benchmark against each server")
    run.add_argument("--duration", type=float, default=10.0)
    run.add_argument("--concurrency", type=int, default=64)
    run.add_argument("--warmup", type=float, default=2.0)
    run.add_argument("--repeat", type=int, default=3)
    run.add_argument("--only", choices=sorted(PORTS), help="benchmark a single library")

    serve = sub.add_parser("serve", help="start one server (used internally)")
    serve.add_argument("name", choices=sorted(PORTS))
    serve.add_argument("port", type=int)

    args = parser.parse_args()
    if args.command == "serve":
        (serve_mini if args.name == "mini" else serve_aiohttp)(args.port)
        return

    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    names = [args.only] if args.only else ["mini", "aiohttp"]
    trials = {name: [] for name in names}
    for trial in range(args.repeat):
        order = names if trial % 2 == 0 else list(reversed(names))
        for name in order:
            result = _run_one(name, args.duration, args.concurrency, args.warmup)
            trials[name].append(result)
            print(
                f"trial={trial + 1}/{args.repeat} {name}: "
                f"{result['rps']:.0f} RPS, p50={result['p50_ms']:.2f} ms, "
                f"p99={result['p99_ms']:.2f} ms, errors={result['errors']}"
            )
    results = []
    for name, samples in trials.items():
        results.append({
            "name": name,
            "requests": int(statistics.median(item["requests"] for item in samples)),
            "errors": sum(item["errors"] for item in samples),
            "rps": statistics.median(item["rps"] for item in samples),
            "p50_ms": statistics.median(item["p50_ms"] for item in samples),
            "p99_ms": statistics.median(item["p99_ms"] for item in samples),
            "server_stderr": next((item["server_stderr"] for item in samples if item["server_stderr"]), ""),
        })
    print("\nMedian across", args.repeat, "trials:")
    _print_table(results, args.duration, args.concurrency)


if __name__ == "__main__":
    main()