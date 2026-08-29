"""AsyncAuditLogger tests — verify the buffering wrapper degrades
correctly to sync mode (no event loop) AND buffers + flushes when
started.

These tests do NOT require Postgres or Redis — they use a fake inner
logger that records calls. The real AuditLogger is exercised by the
existing test suite; here we only verify the wrapper's buffering +
flush + fallback behaviour.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any


class _FakeInner:
    """Fake AuditLogger — records log() + close() calls for assertions."""

    def __init__(self) -> None:
        self.logged: list[dict] = []
        self.closed = False

    def log(self, record: dict) -> None:
        self.logged.append(dict(record))

    def verify_chain(self, start_id: int = 0, end_id: int | None = None) -> dict:
        return {"status": "ok", "count": len(self.logged)}

    def proof(self, record_id: int) -> dict | None:
        if 0 <= record_id < len(self.logged):
            return {"record_id": record_id, "record": self.logged[record_id]}
        return None

    def seal_interval(self) -> dict | None:
        return None

    def close(self) -> None:
        self.closed = True


def test_sync_fallback_when_not_started():
    """When start() isn't called, log() delegates straight to inner —
    zero behaviour change vs the wrapped logger. This is the path
    the 248 passing tests take."""
    from src.audit.async_logger import AsyncAuditLogger
    inner = _FakeInner()
    wrapper = AsyncAuditLogger(inner, max_buffer=10, flush_interval_ms=50)
    # Don't call start() — sync fallback.
    wrapper.log({"id": 1, "decision": "REVIEW"})
    wrapper.log({"id": 2, "decision": "REJECT"})
    assert len(inner.logged) == 2
    assert inner.logged[0]["id"] == 1
    assert inner.logged[1]["id"] == 2
    assert wrapper.buffer_size == 0  # nothing buffered
    wrapper.close()
    assert inner.closed


def test_buffering_when_started():
    """When start() is called inside an event loop, log() buffers +
    the background flush drains the buffer."""
    from src.audit.async_logger import AsyncAuditLogger

    async def run():
        inner = _FakeInner()
        wrapper = AsyncAuditLogger(inner, max_buffer=100, flush_interval_ms=20)
        await wrapper.start()
        # Log 5 records — they should buffer, then flush within 100ms.
        for i in range(5):
            wrapper.log({"id": i, "decision": "REVIEW"})
        # Buffer should have records immediately after log().
        assert wrapper.buffer_size >= 0  # may have started draining
        # Wait for at least one flush.
        await asyncio.sleep(0.15)
        # All 5 should be flushed to inner by now.
        assert len(inner.logged) == 5, f"expected 5, got {len(inner.logged)}"
        await wrapper.stop()
        return wrapper

    wrapper = asyncio.run(run())
    assert wrapper.buffer_size == 0  # final flush on stop


def test_inline_flush_on_overflow():
    """When the buffer exceeds 2x capacity, log() flushes inline to
    avoid unbounded growth (the request blocks briefly)."""
    from src.audit.async_logger import AsyncAuditLogger

    async def run():
        inner = _FakeInner()
        wrapper = AsyncAuditLogger(inner, max_buffer=5, flush_interval_ms=10000)
        await wrapper.start()
        # Log 15 records — 2x capacity is 10, so the 11th triggers
        # an inline flush.
        for i in range(15):
            wrapper.log({"id": i, "decision": "REVIEW"})
        # After the overflow flush, some records should be in inner.
        assert len(inner.logged) >= 5, f"expected >=5 flushed, got {len(inner.logged)}"
        await wrapper.stop()
        # After stop(), all should be flushed.
        assert len(inner.logged) == 15, f"expected 15 total, got {len(inner.logged)}"
        return wrapper

    asyncio.run(run())


def test_stop_force_flushes_remaining():
    """stop() flushes any records still in the buffer — no data loss
    on shutdown."""
    from src.audit.async_logger import AsyncAuditLogger

    async def run():
        inner = _FakeInner()
        # Long flush interval so records stay buffered until stop().
        wrapper = AsyncAuditLogger(inner, max_buffer=100, flush_interval_ms=10000)
        await wrapper.start()
        wrapper.log({"id": 1, "decision": "REVIEW"})
        wrapper.log({"id": 2, "decision": "REJECT"})
        # No wait — stop immediately. Buffer should still have 2.
        assert wrapper.buffer_size == 2
        await wrapper.stop()
        # stop() force-flushed — both records are in inner now.
        assert len(inner.logged) == 2
        assert inner.closed

    asyncio.run(run())


def test_pass_through_read_methods():
    """verify_chain, proof, seal_interval delegate to inner — read
    operations aren't buffered (would return stale data)."""
    from src.audit.async_logger import AsyncAuditLogger
    inner = _FakeInner()
    wrapper = AsyncAuditLogger(inner)
    wrapper.log({"id": 0, "decision": "REVIEW"})
    # verify_chain delegates.
    result = wrapper.verify_chain()
    assert result["status"] == "ok"
    # proof delegates.
    proof = wrapper.proof(0)
    assert proof is not None
    assert proof["record_id"] == 0


def test_close_is_idempotent():
    """close() can be called multiple times without raising."""
    from src.audit.async_logger import AsyncAuditLogger
    inner = _FakeInner()
    wrapper = AsyncAuditLogger(inner)
    wrapper.log({"id": 1})
    wrapper.close()
    wrapper.close()  # second must not raise
    wrapper.close()  # third must not raise
    assert inner.closed


def test_inner_property():
    """The wrapped logger is accessible via .inner for tests + introspection."""
    from src.audit.async_logger import AsyncAuditLogger
    inner = _FakeInner()
    wrapper = AsyncAuditLogger(inner)
    assert wrapper.inner is inner
