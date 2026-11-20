"""Background tasks, a worker queue and graceful shutdown.

    background = BackgroundTasks()
    background.spawn(refresh_cache())          # tracked: shutdown waits for it

    async def handle_job(job): ...
    queue = TaskQueue(handle_job, workers=4, tasks=background)

    server = Server()
    await serve(
        server, "0.0.0.0", 8080,
        background=background,
        shutdown=[queue.close, db.close],   # run in this order after the server stops
        grace=10.0,                          # seconds allowed for in-flight work
    )

serve() installs SIGINT/SIGTERM handlers where the platform supports them and serves until
one arrives. Then it stops accepting connections and drains requests, waits for background
tasks, cancels whatever is still running after `grace`, and runs the shutdown hooks.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import signal

log = logging.getLogger("mini_aiohttp.lifecycle")


class BackgroundTasks:
    """Track fire-and-forget coroutines so that shutdown can wait for them."""

    def __init__(self) -> None:
        self._tasks: set = set()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def spawn(self, coro, *, name: str | None = None) -> asyncio.Task:
        if self._closed:
            coro.close()
            raise RuntimeError("cannot spawn: background tasks are closing")
        task = asyncio.ensure_future(coro)
        if name is not None:
            task.set_name(name)
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return task

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("background task %s failed", task.get_name(), exc_info=task.exception())

    def __len__(self) -> int:
        return len(self._tasks)

    async def close(self, grace: float = 5.0) -> None:
        """Refuse new tasks, wait up to `grace` seconds for running ones, then cancel the rest."""
        self._closed = True
        if not self._tasks:
            return
        _, pending = await asyncio.wait(set(self._tasks), timeout=grace)
        for task in pending:
            task.cancel()
        if pending:
            log.warning("cancelled %d background task(s) still running after %.1fs", len(pending), grace)
            await asyncio.gather(*pending, return_exceptions=True)


class TaskQueue:
    """A bounded queue drained by a fixed number of worker tasks.

    handler(job) runs once per job. An exception is logged and the worker keeps going.
    Workers start on the first put(), so the queue can be created before the loop runs.
    """

    def __init__(
        self,
        handler,
        *,
        workers: int = 4,
        maxsize: int = 1000,
        tasks: BackgroundTasks | None = None,
    ):
        if workers < 1:
            raise ValueError("workers must be at least 1")
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        self._handler = handler
        self._queue: asyncio.Queue = asyncio.Queue(maxsize)
        self._tasks = tasks if tasks is not None else BackgroundTasks()
        self._worker_count = workers
        self._workers: list = []
        self._closed = False

    def _ensure_workers(self) -> None:
        if not self._workers:
            self._workers = [
                self._tasks.spawn(self._work(), name=f"queue-worker-{i}")
                for i in range(self._worker_count)
            ]

    async def put(self, job) -> None:
        if self._closed:
            raise RuntimeError("queue is closed")
        self._ensure_workers()
        await self._queue.put(job)

    def put_nowait(self, job) -> None:
        if self._closed:
            raise RuntimeError("queue is closed")
        self._ensure_workers()
        self._queue.put_nowait(job)

    def qsize(self) -> int:
        return self._queue.qsize()

    async def join(self) -> None:
        """Wait until every queued job has been handled."""
        await self._queue.join()

    async def _work(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._handler(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("queue job failed")
            finally:
                self._queue.task_done()

    async def close(self, grace: float = 5.0) -> None:
        """Refuse new jobs, wait up to `grace` seconds for queued ones, then stop the workers."""
        self._closed = True
        try:
            await asyncio.wait_for(self._queue.join(), grace)
        except asyncio.TimeoutError:
            log.warning("%d queued job(s) were not finished", self._queue.qsize())
        for task in self._workers:
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []


async def shutdown_gracefully(server, background=None, hooks=(), *, grace: float = 5.0) -> None:
    """Stop the server, wait for background work, then run shutdown hooks in the given order.

    A hook may be a plain function or a coroutine function. A failing hook is logged and the
    remaining hooks still run.
    """
    await server.close(grace=grace)
    if background is not None:
        await background.close(grace=grace)
    for hook in hooks:
        try:
            result = hook()
            if inspect.isawaitable(result):
                await result
        except Exception:
            log.exception("shutdown hook %r failed", getattr(hook, "__name__", hook))


async def serve(
    server,
    host: str = "127.0.0.1",
    port: int = 8080,
    *,
    grace: float = 5.0,
    background: BackgroundTasks | None = None,
    shutdown=(),
    stop_event: asyncio.Event | None = None,
    signals=(signal.SIGINT, signal.SIGTERM),
    **start_kwargs,
) -> None:
    """Run `server` until SIGINT/SIGTERM (or until `stop_event` is set), then shut down cleanly.

    Extra keyword arguments (reuse_port, ssl) go to Server.start().
    """
    stop = stop_event if stop_event is not None else asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = []
    try:
        for sig in signals:
            try:
                loop.add_signal_handler(sig, stop.set)
                installed.append(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                pass  # Windows, or not the main thread: stop_event is the way to stop
        await server.start(host, port, **start_kwargs)
        log.info("Listening on %s:%s", host, port)
        await stop.wait()
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)
        await shutdown_gracefully(server, background, shutdown, grace=grace)
