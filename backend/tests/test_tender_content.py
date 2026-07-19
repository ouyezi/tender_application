import asyncio
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import main as app_main
from app.models import Base, WorkspaceFile
from app.services import tender_content
from app.services.tender_content import (
    TenderContentError,
    TenderContentProvider,
    TenderContentStopped,
    pending_operation_count,
    shutdown_pending_operations,
)


@pytest_asyncio.fixture
async def content_db(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'content.db'}",
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    yield session_factory
    await engine.dispose()


async def add_file(session_factory, file_id, status, md_path=None, task_id="task"):
    async with session_factory() as session:
        session.add(
            WorkspaceFile(
                id=file_id,
                task_id=task_id,
                label="tender",
                original_filename="tender.docx",
                stored_path="/tmp/tender.docx",
                ext=".docx",
                parse_status=status,
                md_path=str(md_path) if md_path is not None else None,
            )
        )
        await session.commit()


async def set_status(session_factory, file_id, status, md_path=None):
    async with session_factory() as session:
        row = await session.get(WorkspaceFile, file_id)
        row.parse_status = status
        row.md_path = str(md_path) if md_path is not None else None
        await session.commit()


def provider(timeout=0.5, poll=0.01):
    return TenderContentProvider(timeout_seconds=timeout, poll_seconds=poll)


async def wait_for_pending_count(expected):
    for _ in range(100):
        if pending_operation_count() == expected:
            return
        await asyncio.sleep(0.002)
    assert pending_operation_count() == expected


@pytest.mark.asyncio
async def test_application_lifespan_shuts_down_pending_operations(
    tmp_path, monkeypatch
):
    release = asyncio.Event()
    cancellation_handled = asyncio.Event()

    async def no_op():
        return None

    class LifespanSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, model, file_id):
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancellation_handled.set()
                await release.wait()

    monkeypatch.setattr(app_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(app_main, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(app_main, "init_db", no_op)
    monkeypatch.setattr(app_main, "seed_configs_if_empty", no_op)
    monkeypatch.setattr(app_main, "recover_interrupted_tasks", no_op)
    monkeypatch.setattr(app_main, "recover_interrupted_parse_jobs", no_op)
    monkeypatch.setattr(
        "app.services.checklist_service.recover_checklist_publications", no_op
    )
    monkeypatch.setattr("app.services.parse_scheduler.kick", no_op)
    monkeypatch.setattr("app.services.index_scheduler.kick", no_op)
    monkeypatch.setattr("app.db.SessionLocal", LifespanSession)

    try:
        async with app_main.lifespan(app_main.app):
            with pytest.raises(TenderContentError, match="timeout"):
                await provider(timeout=0.01, poll=0.002).wait_for_markdown(
                    "task", "lifespan-pending", stop_requested=lambda: False
                )
            await asyncio.wait_for(cancellation_handled.wait(), timeout=0.1)
            assert pending_operation_count() == 1

        assert pending_operation_count() == 0
    finally:
        release.set()
        await shutdown_pending_operations(timeout_seconds=0.05)


def test_pending_registries_are_isolated_between_event_loops(monkeypatch):
    monkeypatch.setattr(tender_content, "MAX_PENDING_OPERATIONS", 1)
    loop_one = asyncio.new_event_loop()
    loop_two = asyncio.new_event_loop()

    async def create_orphan(label):
        release = asyncio.Event()
        finished = asyncio.Event()
        cancellation_handled = asyncio.Event()

        class LoopSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                finished.set()

            async def get(self, model, file_id):
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancellation_handled.set()
                    await release.wait()

        monkeypatch.setattr("app.db.SessionLocal", LoopSession)
        with pytest.raises(TenderContentError, match="timeout"):
            await provider(timeout=0.01, poll=0.002).wait_for_markdown(
                "task", label, stop_requested=lambda: False
            )
        await asyncio.wait_for(cancellation_handled.wait(), timeout=0.1)
        assert pending_operation_count() == 1
        return release, finished

    async def cleanup(release, finished):
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=0.1)
        await wait_for_pending_count(0)
        await shutdown_pending_operations(timeout_seconds=0.05)

    async def count_pending():
        return pending_operation_count()

    loop_one_state = None
    loop_two_state = None
    try:
        loop_one_state = loop_one.run_until_complete(create_orphan("loop-one"))
        loop_two_state = loop_two.run_until_complete(create_orphan("loop-two"))

        assert loop_one.run_until_complete(count_pending()) == 1
        assert loop_two.run_until_complete(count_pending()) == 1
    finally:
        if loop_two_state is not None:
            loop_two.run_until_complete(cleanup(*loop_two_state))
        if loop_one_state is not None:
            loop_one.run_until_complete(cleanup(*loop_one_state))
        loop_two.close()
        loop_one.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["succeeded", "partial"])
async def test_returns_full_utf8_markdown_for_success_status(
    content_db, tmp_path, status
):
    markdown = "# 招标文件\n\n真实内容"
    path = tmp_path / f"{status}.md"
    path.write_text(markdown, encoding="utf-8")
    await add_file(content_db, status, status, path)

    result = await provider().wait_for_markdown(
        "task", status, stop_requested=lambda: False
    )

    assert result == markdown


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_status", ["pending", "running"])
async def test_waits_until_processing_status_succeeds(
    content_db, tmp_path, monkeypatch, initial_status
):
    path = tmp_path / "ready.md"
    path.write_text("ready", encoding="utf-8")
    await add_file(content_db, initial_status, initial_status)
    initial_query_finished = asyncio.Event()

    class ObservedSession:
        async def __aenter__(self):
            self.session_context = content_db()
            self.session = await self.session_context.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.session_context.__aexit__(*args)

        async def get(self, model, file_id):
            row = await self.session.get(model, file_id)
            initial_query_finished.set()
            return row

    async def complete():
        await initial_query_finished.wait()
        await set_status(content_db, initial_status, "succeeded", path)

    monkeypatch.setattr("app.db.SessionLocal", ObservedSession)
    completion = asyncio.create_task(complete())
    try:
        result = await provider().wait_for_markdown(
            "task", initial_status, stop_requested=lambda: False
        )
        assert result == "ready"
    finally:
        completion.cancel()
        await asyncio.gather(completion, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "category"),
    [
        ("failed", "parse-failed"),
        ("skipped", "parse-skipped"),
        ("mystery", "invalid-status-mystery"),
    ],
)
async def test_rejects_failed_and_unknown_statuses(content_db, status, category):
    await add_file(content_db, status, status)

    with pytest.raises(TenderContentError) as error:
        await provider().wait_for_markdown(
            "task", status, stop_requested=lambda: False
        )

    assert str(error.value) == f"{status}: {category}"


@pytest.mark.asyncio
async def test_rejects_missing_database_file(content_db):
    with pytest.raises(TenderContentError, match="missing"):
        await provider().wait_for_markdown(
            "task", "missing", stop_requested=lambda: False
        )


@pytest.mark.asyncio
async def test_hides_file_owned_by_another_task(content_db, tmp_path):
    path = tmp_path / "other-task.md"
    path.write_text("must not leak", encoding="utf-8")
    await add_file(
        content_db,
        "other-task-file",
        "succeeded",
        path,
        task_id="other-task",
    )

    with pytest.raises(TenderContentError) as error:
        await provider().wait_for_markdown(
            "task", "other-task-file", stop_requested=lambda: False
        )

    assert str(error.value) == "other-task-file: file-not-found"


@pytest.mark.asyncio
async def test_rejects_missing_markdown_path(content_db):
    await add_file(content_db, "no-path", "succeeded")

    with pytest.raises(TenderContentError, match="no-path"):
        await provider().wait_for_markdown(
            "task", "no-path", stop_requested=lambda: False
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("entity_kind", ["missing", "directory"])
async def test_rejects_missing_or_non_file_markdown_entity(
    content_db, tmp_path, entity_kind
):
    path = tmp_path / entity_kind
    if entity_kind == "directory":
        path.mkdir()
    await add_file(content_db, entity_kind, "succeeded", path)

    with pytest.raises(TenderContentError, match=entity_kind):
        await provider().wait_for_markdown(
            "task", entity_kind, stop_requested=lambda: False
        )


@pytest.mark.asyncio
async def test_rejects_fifo_without_blocking(content_db, tmp_path):
    path = tmp_path / "named-pipe"
    os.mkfifo(path)
    await add_file(content_db, "fifo", "succeeded", path)

    with pytest.raises(TenderContentError) as error:
        await asyncio.wait_for(
            provider(timeout=0.2).wait_for_markdown(
                "task", "fifo", stop_requested=lambda: False
            ),
            timeout=0.1,
        )

    assert str(error.value) == "fifo: markdown-not-file"


@pytest.mark.asyncio
async def test_rejects_whitespace_only_content(content_db, tmp_path):
    path = tmp_path / "empty.md"
    path.write_text(" \n\t", encoding="utf-8")
    await add_file(content_db, "empty", "succeeded", path)

    with pytest.raises(TenderContentError, match="empty"):
        await provider().wait_for_markdown(
            "task", "empty", stop_requested=lambda: False
        )


@pytest.mark.asyncio
async def test_rejects_non_utf8_content(content_db, tmp_path):
    path = tmp_path / "binary.md"
    path.write_bytes(b"\xff\xfe")
    await add_file(content_db, "binary", "succeeded", path)

    with pytest.raises(TenderContentError, match="binary"):
        await provider().wait_for_markdown(
            "task", "binary", stop_requested=lambda: False
        )


@pytest.mark.asyncio
async def test_rejects_read_oserror(content_db, tmp_path, monkeypatch):
    path = tmp_path / "unreadable.md"
    path.write_text("secret body", encoding="utf-8")
    await add_file(content_db, "unreadable", "succeeded", path)

    def fail_open(*args, **kwargs):
        raise OSError("sensitive operating system detail")

    monkeypatch.setattr(tender_content.os, "open", fail_open)
    with pytest.raises(TenderContentError) as error:
        await provider().wait_for_markdown(
            "task", "unreadable", stop_requested=lambda: False
        )

    assert "unreadable" in str(error.value)
    assert "secret body" not in str(error.value)
    assert "sensitive operating system detail" not in str(error.value)


@pytest.mark.asyncio
async def test_times_out_while_waiting(content_db):
    await add_file(content_db, "slow", "pending")

    with pytest.raises(TenderContentError, match="slow"):
        await provider(timeout=0.03).wait_for_markdown(
            "task", "slow", stop_requested=lambda: False
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["stop", "timeout"])
async def test_interrupts_database_query_that_never_returns(monkeypatch, outcome):
    query_started = asyncio.Event()
    query_cancelled = asyncio.Event()
    never = asyncio.Event()
    stop = False

    class HangingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, model, file_id):
            query_started.set()
            try:
                await never.wait()
            finally:
                query_cancelled.set()

    monkeypatch.setattr("app.db.SessionLocal", HangingSession)
    operation = asyncio.create_task(
        provider(timeout=0.03, poll=0.005).wait_for_markdown(
            "task", "hanging-query", stop_requested=lambda: stop
        )
    )
    try:
        await asyncio.wait_for(query_started.wait(), timeout=0.2)
        if outcome == "stop":
            stop = True
            expected_error = TenderContentStopped
        else:
            expected_error = TenderContentError

        with pytest.raises(expected_error) as error:
            await asyncio.wait_for(operation, timeout=0.2)

        if outcome == "timeout":
            assert str(error.value) == "hanging-query: timeout"
        await asyncio.wait_for(query_cancelled.wait(), timeout=0.2)
    finally:
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["stop", "timeout"])
async def test_returns_promptly_when_query_delays_cancellation(monkeypatch, outcome):
    query_started = asyncio.Event()
    release_query = asyncio.Event()
    session_cleaned = asyncio.Event()
    stop = False

    class CancellationResistantSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            session_cleaned.set()

        async def get(self, model, file_id):
            query_started.set()
            try:
                await release_query.wait()
            except asyncio.CancelledError:
                await release_query.wait()
            return SimpleNamespace(
                id=file_id,
                task_id="task",
                parse_status="failed",
            )

    monkeypatch.setattr("app.db.SessionLocal", CancellationResistantSession)
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    operation = asyncio.create_task(
        provider(timeout=0.02, poll=0.002).wait_for_markdown(
            "task", "slow-cancel-query", stop_requested=lambda: stop
        )
    )
    try:
        await asyncio.wait_for(query_started.wait(), timeout=0.1)
        if outcome == "stop":
            stop = True
            started_at = loop.time()
            expected_error = TenderContentStopped
        else:
            expected_error = TenderContentError

        with pytest.raises(expected_error) as error:
            await asyncio.wait_for(operation, timeout=0.1)

        assert loop.time() - started_at < 0.1
        if outcome == "timeout":
            assert str(error.value) == "slow-cancel-query: timeout"
    finally:
        release_query.set()
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        await asyncio.wait_for(session_cleaned.wait(), timeout=0.1)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["stop", "timeout"])
async def test_returns_promptly_when_session_exit_blocks(monkeypatch, outcome):
    exit_started = asyncio.Event()
    release_exit = asyncio.Event()
    exit_finished = asyncio.Event()
    stop = False

    class SlowExitSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            exit_started.set()
            try:
                await release_exit.wait()
            except asyncio.CancelledError:
                await release_exit.wait()
            finally:
                exit_finished.set()

        async def get(self, model, file_id):
            return SimpleNamespace(
                id=file_id,
                task_id="task",
                parse_status="failed",
            )

    monkeypatch.setattr("app.db.SessionLocal", SlowExitSession)
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    safety_release = loop.call_later(0.15, release_exit.set)
    operation = asyncio.create_task(
        provider(timeout=0.02, poll=0.002).wait_for_markdown(
            "task", "slow-session-exit", stop_requested=lambda: stop
        )
    )
    try:
        await asyncio.wait_for(exit_started.wait(), timeout=0.1)
        if outcome == "stop":
            stop = True
            started_at = loop.time()
            expected_error = TenderContentStopped
        else:
            expected_error = TenderContentError

        with pytest.raises(expected_error) as error:
            await asyncio.wait_for(operation, timeout=0.1)

        assert loop.time() - started_at < 0.1
        if outcome == "timeout":
            assert str(error.value) == "slow-session-exit: timeout"
    finally:
        safety_release.cancel()
        release_exit.set()
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        await asyncio.wait_for(exit_finished.wait(), timeout=0.1)


@pytest.mark.asyncio
async def test_detached_operations_are_registered_and_reclaimed(monkeypatch):
    release = asyncio.Event()
    finished = asyncio.Event()
    finished_count = 0

    class ResistantSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            nonlocal finished_count
            finished_count += 1
            if finished_count == 3:
                finished.set()

        async def get(self, model, file_id):
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return None

    monkeypatch.setattr("app.db.SessionLocal", ResistantSession)
    try:
        for index in range(3):
            with pytest.raises(TenderContentError, match="timeout"):
                await provider(timeout=0.01, poll=0.002).wait_for_markdown(
                    "task", f"orphan-{index}", stop_requested=lambda: False
                )

        assert pending_operation_count() == 3
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=0.1)
        await wait_for_pending_count(0)


@pytest.mark.asyncio
async def test_shutdown_drains_cooperative_pending_operations(monkeypatch, caplog):
    release = asyncio.Event()
    initial_cancellation_handled = asyncio.Event()

    class CooperativeOnShutdownSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, model, file_id):
            try:
                await release.wait()
            except asyncio.CancelledError:
                initial_cancellation_handled.set()
                await release.wait()

    monkeypatch.setattr("app.db.SessionLocal", CooperativeOnShutdownSession)
    with pytest.raises(TenderContentError, match="timeout"):
        await provider(timeout=0.01, poll=0.002).wait_for_markdown(
            "task", "shutdown-cooperative", stop_requested=lambda: False
        )
    assert pending_operation_count() == 1
    await asyncio.wait_for(initial_cancellation_handled.wait(), timeout=0.1)

    await shutdown_pending_operations(timeout_seconds=0.05)

    assert pending_operation_count() == 0
    assert "still pending" not in caplog.text


@pytest.mark.asyncio
async def test_shutdown_is_bounded_for_noncooperative_operation(
    monkeypatch, caplog
):
    release = asyncio.Event()
    finished = asyncio.Event()

    class NoncooperativeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            finished.set()

        async def get(self, model, file_id):
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue

    monkeypatch.setattr("app.db.SessionLocal", NoncooperativeSession)
    loop = asyncio.get_running_loop()
    try:
        with pytest.raises(TenderContentError, match="timeout"):
            await provider(timeout=0.01, poll=0.002).wait_for_markdown(
                "task", "shutdown-resistant", stop_requested=lambda: False
            )

        started_at = loop.time()
        await shutdown_pending_operations(timeout_seconds=0.02)

        assert loop.time() - started_at < 0.1
        assert pending_operation_count() == 1
        assert "still pending" in caplog.text
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=0.1)
        await wait_for_pending_count(0)


@pytest.mark.asyncio
async def test_pending_operation_limit_rejects_new_work(monkeypatch):
    release = asyncio.Event()
    finished = asyncio.Event()
    finished_count = 0

    class ResistantSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            nonlocal finished_count
            finished_count += 1
            if finished_count == 2:
                finished.set()

        async def get(self, model, file_id):
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

    monkeypatch.setattr("app.db.SessionLocal", ResistantSession)
    monkeypatch.setattr(tender_content, "MAX_PENDING_OPERATIONS", 2)
    try:
        for index in range(2):
            with pytest.raises(TenderContentError, match="timeout"):
                await provider(timeout=0.01, poll=0.002).wait_for_markdown(
                    "task", f"limited-{index}", stop_requested=lambda: False
                )

        with pytest.raises(TenderContentError) as error:
            await provider(timeout=0.1).wait_for_markdown(
                "task", "over-limit", stop_requested=lambda: False
            )

        assert str(error.value) == "over-limit: pending-operation-limit"
        assert pending_operation_count() == 2
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=0.1)
        await wait_for_pending_count(0)


@pytest.mark.asyncio
async def test_late_exception_is_safely_logged(monkeypatch, caplog):
    release = asyncio.Event()
    finished = asyncio.Event()

    class LateFailureSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            finished.set()

        async def get(self, model, file_id):
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            raise RuntimeError("/secret/path.md: sensitive body")

    monkeypatch.setattr("app.db.SessionLocal", LateFailureSession)
    with pytest.raises(TenderContentError, match="timeout"):
        await provider(timeout=0.01, poll=0.002).wait_for_markdown(
            "task", "late-failure", stop_requested=lambda: False
        )
    assert pending_operation_count() == 1

    release.set()
    await asyncio.wait_for(finished.wait(), timeout=0.1)
    await wait_for_pending_count(0)

    assert "pending-operation-late-error" in caplog.text
    assert "secret" not in caplog.text
    assert "sensitive body" not in caplog.text
    assert "RuntimeError" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["stop", "timeout"])
async def test_interrupts_blocked_thread_read(
    content_db, tmp_path, monkeypatch, outcome
):
    path = tmp_path / "blocked.md"
    path.write_text("content", encoding="utf-8")
    await add_file(content_db, "blocked", "succeeded", path)
    read_started = threading.Event()
    release_read = threading.Event()
    read_finished = threading.Event()
    stop = False

    def blocked_read(read_path):
        read_started.set()
        try:
            release_read.wait()
            return "content"
        finally:
            read_finished.set()

    monkeypatch.setattr(tender_content, "_read_utf8_markdown", blocked_read)
    operation = asyncio.create_task(
        provider(timeout=0.03, poll=0.005).wait_for_markdown(
            "task", "blocked", stop_requested=lambda: stop
        )
    )
    try:
        assert await asyncio.to_thread(read_started.wait, 0.2)
        if outcome == "stop":
            stop = True
            expected_error = TenderContentStopped
        else:
            expected_error = TenderContentError

        with pytest.raises(expected_error) as error:
            await asyncio.wait_for(operation, timeout=0.2)

        if outcome == "timeout":
            assert str(error.value) == "blocked: timeout"
    finally:
        release_read.set()
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        assert await asyncio.to_thread(read_finished.wait, 0.2)


@pytest.mark.asyncio
async def test_discards_old_content_when_file_is_reparsed_during_read(
    content_db, tmp_path, monkeypatch
):
    old_path = tmp_path / "old.md"
    new_path = tmp_path / "new.md"
    old_path.write_text("old content", encoding="utf-8")
    new_path.write_text("new stable content", encoding="utf-8")
    await add_file(content_db, "reparsed", "succeeded", old_path)
    old_read_started = threading.Event()
    release_old_read = threading.Event()
    revalidated_pending = asyncio.Event()
    query_count = 0
    real_read = tender_content._read_utf8_markdown

    def controlled_read(path):
        if path == old_path:
            old_read_started.set()
            release_old_read.wait()
            return "old content"
        return real_read(path)

    class ObservedSession:
        async def __aenter__(self):
            self.session_context = content_db()
            self.session = await self.session_context.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.session_context.__aexit__(*args)

        async def get(self, model, file_id):
            nonlocal query_count
            query_count += 1
            row = await self.session.get(model, file_id)
            if query_count == 2 and row.parse_status == "pending":
                revalidated_pending.set()
            return row

    monkeypatch.setattr(tender_content, "_read_utf8_markdown", controlled_read)
    monkeypatch.setattr("app.db.SessionLocal", ObservedSession)
    operation = asyncio.create_task(
        provider(timeout=0.5, poll=0.005).wait_for_markdown(
            "task", "reparsed", stop_requested=lambda: False
        )
    )
    try:
        assert await asyncio.to_thread(old_read_started.wait, 0.2)
        await set_status(content_db, "reparsed", "pending", new_path)
        release_old_read.set()
        await asyncio.wait_for(revalidated_pending.wait(), timeout=0.2)
        await set_status(content_db, "reparsed", "succeeded", new_path)

        result = await asyncio.wait_for(operation, timeout=0.2)
        assert result == "new stable content"
    finally:
        release_old_read.set()
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.parametrize("invalid", [0, -1, float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("parameter", ["timeout", "poll"])
def test_rejects_non_finite_or_non_positive_timing_values(invalid, parameter):
    timeout = invalid if parameter == "timeout" else 1
    poll = invalid if parameter == "poll" else 0.1

    with pytest.raises(ValueError):
        TenderContentProvider(timeout_seconds=timeout, poll_seconds=poll)
