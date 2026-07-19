import asyncio
import inspect
import logging
import math
import os
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from app import db
from app.models import WorkspaceFile

WAITING_STATUSES = frozenset({"pending", "running"})
USABLE_STATUSES = frozenset({"succeeded", "partial"})
FAILED_STATUS = "failed"
SKIPPED_STATUS = "skipped"
MAX_PENDING_OPERATIONS = 8

_T = TypeVar("_T")
logger = logging.getLogger(__name__)
_LOOP_REGISTRY_ATTRIBUTE = "_tender_content_pending_operations"


class TenderContentError(RuntimeError):
    pass


class TenderContentStopped(Exception):
    pass


def _pending_operations_for_loop(
    loop: asyncio.AbstractEventLoop,
) -> set[asyncio.Future[object]]:
    registry = getattr(loop, _LOOP_REGISTRY_ATTRIBUTE, None)
    if registry is None:
        registry = set()
        setattr(loop, _LOOP_REGISTRY_ATTRIBUTE, registry)
    return registry


def _finish_pending_operation(
    operation: asyncio.Future[object],
    registry: set[asyncio.Future[object]],
) -> None:
    if operation not in registry:
        return
    registry.discard(operation)
    try:
        error = operation.exception()
    except asyncio.CancelledError:
        return
    if error is not None:
        logger.warning(
            "Tender content background operation failed "
            "(category=pending-operation-late-error)"
        )


def pending_operation_count() -> int:
    registry = _pending_operations_for_loop(asyncio.get_running_loop())
    for operation in tuple(registry):
        if operation.done():
            _finish_pending_operation(operation, registry)
    return len(registry)


async def shutdown_pending_operations(timeout_seconds: float = 0.1) -> None:
    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        raise ValueError("timeout_seconds must be finite and non-negative")

    loop = asyncio.get_running_loop()
    registry = _pending_operations_for_loop(loop)
    # The application must call this before its owning loop terminates.
    # Registries are intentionally loop-local; a different loop must never
    # inspect or cancel tasks owned by this one.
    operations: set[asyncio.Future[object]] = set()
    for operation in tuple(registry):
        if operation.done():
            _finish_pending_operation(operation, registry)
            continue
        operation.cancel()
        operations.add(operation)

    if operations and timeout_seconds:
        done, _ = await asyncio.wait(
            operations,
            timeout=timeout_seconds,
        )
        for operation in done:
            _finish_pending_operation(operation, registry)

    pending_operation_count()
    if registry:
        logger.warning(
            "Tender content background operations still pending "
            "(category=pending-operation-shutdown-timeout)"
        )


def _read_utf8_markdown(path: Path) -> str:
    descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TenderContentError("markdown-not-file")
        source = os.fdopen(descriptor, "r", encoding="utf-8")
        descriptor = None
        with source:
            content = source.read()
        if not content.strip():
            raise TenderContentError("markdown-empty")
        return content
    except UnicodeDecodeError:
        raise TenderContentError("markdown-not-utf8") from None
    except OSError:
        raise TenderContentError("markdown-unreadable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


class TenderContentProvider:
    def __init__(self, timeout_seconds: float, poll_seconds: float = 0.1):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError("poll_seconds must be finite and positive")
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds

    async def wait_for_markdown(
        self,
        task_id: str,
        file_id: str,
        *,
        stop_requested: Callable[[], bool],
    ) -> str:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_seconds

        while True:
            workspace_file = await self._load_file(
                task_id,
                file_id,
                stop_requested=stop_requested,
                loop=loop,
                deadline=deadline,
            )
            if workspace_file is None:
                raise self._error(file_id, "file-not-found")

            status = workspace_file.parse_status
            if status in USABLE_STATUSES:
                if not workspace_file.md_path:
                    raise self._error(file_id, "markdown-path-missing")

                snapshot = (
                    workspace_file.parse_status,
                    workspace_file.md_path,
                    workspace_file.updated_at,
                )
                read_error = None
                try:
                    content = await self._run_controlled(
                        asyncio.to_thread(
                            _read_utf8_markdown, Path(workspace_file.md_path)
                        ),
                        file_id=file_id,
                        stop_requested=stop_requested,
                        loop=loop,
                        deadline=deadline,
                    )
                except TenderContentError as exc:
                    read_error = str(exc)
                    content = None

                latest = await self._load_file(
                    task_id,
                    file_id,
                    stop_requested=stop_requested,
                    loop=loop,
                    deadline=deadline,
                )
                if latest is None:
                    raise self._error(file_id, "file-not-found")
                if latest.parse_status in WAITING_STATUSES:
                    continue
                self._raise_for_unusable_status(file_id, latest.parse_status)
                latest_snapshot = (
                    latest.parse_status,
                    latest.md_path,
                    latest.updated_at,
                )
                if latest_snapshot != snapshot:
                    continue

                if read_error is not None:
                    raise self._error(file_id, read_error) from None
                assert content is not None
                return content

            self._raise_for_unusable_status(file_id, status)

            self._raise_if_stopped_or_timed_out(
                file_id, stop_requested, loop, deadline
            )
            remaining = deadline - loop.time()
            await asyncio.sleep(min(self.poll_seconds, max(0, remaining)))
            self._raise_if_stopped_or_timed_out(
                file_id, stop_requested, loop, deadline
            )

    async def _load_file(
        self,
        task_id: str,
        file_id: str,
        *,
        stop_requested: Callable[[], bool],
        loop: asyncio.AbstractEventLoop,
        deadline: float,
    ) -> WorkspaceFile | None:
        self._raise_if_stopped_or_timed_out(
            file_id, stop_requested, loop, deadline
        )
        workspace_file = await self._run_controlled(
            self._query_file(file_id),
            file_id=file_id,
            stop_requested=stop_requested,
            loop=loop,
            deadline=deadline,
        )
        if workspace_file is None or workspace_file.task_id != task_id:
            return None
        return workspace_file

    @staticmethod
    async def _query_file(file_id: str) -> WorkspaceFile | None:
        async with db.SessionLocal() as session:
            return await session.get(WorkspaceFile, file_id)

    async def _run_controlled(
        self,
        awaitable: Awaitable[_T],
        *,
        file_id: str,
        stop_requested: Callable[[], bool],
        loop: asyncio.AbstractEventLoop,
        deadline: float,
    ) -> _T:
        registry = _pending_operations_for_loop(loop)
        try:
            self._raise_if_stopped_or_timed_out(
                file_id, stop_requested, loop, deadline
            )
            for pending in tuple(registry):
                if pending.done():
                    _finish_pending_operation(pending, registry)
            if len(registry) >= MAX_PENDING_OPERATIONS:
                raise self._error(file_id, "pending-operation-limit")
        except BaseException:
            self._discard_awaitable(awaitable)
            raise

        operation = asyncio.ensure_future(awaitable)
        try:
            while True:
                self._raise_if_stopped_or_timed_out(
                    file_id, stop_requested, loop, deadline
                )
                remaining = deadline - loop.time()
                done, _ = await asyncio.wait(
                    {operation},
                    timeout=min(self.poll_seconds, remaining),
                )
                if operation in done:
                    self._raise_if_stopped_or_timed_out(
                        file_id, stop_requested, loop, deadline
                    )
                    return operation.result()
        finally:
            if not operation.done():
                operation.cancel()
            if operation.done():
                self._consume_task_result(operation)
            else:
                # Cancelling asyncio.to_thread only stops this awaiter; Python
                # cannot forcibly terminate the worker thread itself.
                registry.add(operation)
                operation.add_done_callback(
                    lambda completed: _finish_pending_operation(
                        completed, registry
                    )
                )

    @staticmethod
    def _discard_awaitable(awaitable: Awaitable[object]) -> None:
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        elif isinstance(awaitable, asyncio.Future):
            awaitable.cancel()

    @staticmethod
    def _consume_task_result(operation: asyncio.Future[object]) -> None:
        try:
            operation.exception()
        except asyncio.CancelledError:
            pass

    @classmethod
    def _raise_for_unusable_status(cls, file_id: str, status: str) -> None:
        if status in WAITING_STATUSES:
            return
        if status == FAILED_STATUS:
            raise cls._error(file_id, "parse-failed")
        if status == SKIPPED_STATUS:
            raise cls._error(file_id, "parse-skipped")
        if status not in USABLE_STATUSES:
            raise cls._error(file_id, f"invalid-status-{status}")

    @staticmethod
    def _error(file_id: str, category: str) -> TenderContentError:
        return TenderContentError(f"{file_id}: {category}")

    @staticmethod
    def _raise_if_stopped_or_timed_out(
        file_id: str,
        stop_requested: Callable[[], bool],
        loop: asyncio.AbstractEventLoop,
        deadline: float,
    ) -> None:
        if stop_requested():
            raise TenderContentStopped(file_id)
        if loop.time() >= deadline:
            raise TenderContentError(f"{file_id}: timeout")
