"""Kafka fallback path — verify the compatibility stub degrades to Redis
Streams (or no-op) when Kafka isn't configured.

Covers the 3 fallback modes documented in ``kafka_producer.py``:
  1. KAFKA_BROKERS unset → Redis Streams path (StreamProducer delegation)
  2. KAFKA_BROKERS set + confluent_kafka not importable → Redis Streams
  3. KAFKA_BROKERS set + confluent_kafka importable but Producer ctor
     raises → Redis Streams

These tests do NOT require a running Kafka broker — that's the point of
the compatibility stub. The Kafka-primary path is exercised by the
optional ``docker-compose.yml`` ``kafka`` service (operator-driven).
"""
from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import patch

import pytest


def _reload_kafka_module():
    """Force a fresh import of kafka_producer so module-level env-var
    reads pick up the current state. Module-level state is set in
    ``__init__`` of the class, but the env-var read helper
    ``_kafka_brokers()`` reads at call time — this reload is belt-and-
    suspenders for tests that flip the env var between cases.
    """
    if "src.stream.kafka_producer" in sys.modules:
        return importlib.reload(sys.modules["src.stream.kafka_producer"])
    return importlib.import_module("src.stream.kafka_producer")


@pytest.fixture
def clean_env(monkeypatch):
    """Strip KAFKA_BROKERS from the env for the duration of the test."""
    monkeypatch.delenv("KAFKA_BROKERS", raising=False)
    yield
    monkeypatch.delenv("KAFKA_BROKERS", raising=False)


def test_fallback_when_env_unset(clean_env):
    """KAFKA_BROKERS unset → transport is redis_streams, publish delegates."""
    from src.stream.kafka_producer import KafkaProducer
    # No redis_url either — pure no-op path (StreamProducer(None) is a
    # no-op publisher).
    producer = KafkaProducer(redis_url=None)
    assert producer.kafka_enabled is False
    assert producer.transport == "redis_streams"
    # publish returns None — StreamProducer(None).publish is a no-op.
    result = producer.publish("risk.scores", {"order_id": "TEST-001"})
    assert result is None
    producer.close()


def test_fallback_when_kafka_not_installed(clean_env, monkeypatch):
    """KAFKA_BROKERS set + confluent_kafka not importable → Redis path."""
    from src.stream.kafka_producer import KafkaProducer
    monkeypatch.setenv("KAFKA_BROKERS", "kafka-fake:9092")
    # Force the importability check to return False (simulates
    # confluent-kafka not in the venv).
    with patch("src.stream.kafka_producer._kafka_importable", return_value=False):
        producer = KafkaProducer(redis_url=None)
        assert producer.kafka_enabled is True  # env var is set
        # But transport falls back because confluent_kafka isn't there.
        assert producer.transport == "redis_streams"
        result = producer.publish("risk.scores", {"k": "v"})
        assert result is None  # Redis path is also no-op (no redis_url)
    producer.close()


def test_fallback_when_producer_ctor_raises(clean_env, monkeypatch):
    """KAFKA_BROKERS set + confluent_kafka importable + Producer ctor
    raises → Redis path takes over."""
    monkeypatch.setenv("KAFKA_BROKERS", "kafka-fake:9092")

    # Fake the importability check + a Producer class that raises on
    # construction. We patch the import path inside kafka_producer's
    # _kafka_producer() method.
    import src.stream.kafka_producer as kp

    class _RaisingProducer:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("simulated broker unreachable")

    def _fake_importable():
        return True

    def _fake_import(name, *args, **kwargs):
        if name == "confluent_kafka":
            class _FK:
                Producer = _RaisingProducer
            return _FK
        raise ImportError(name)

    with patch.object(kp, "_kafka_importable", _fake_importable), \
         patch("builtins.__import__", side_effect=_fake_import):
        producer = kp.KafkaProducer(redis_url=None)
        assert producer.kafka_enabled is True
        # _kafka_producer() returns None (ctor raised → caught).
        assert producer._kafka_producer() is None
        assert producer.transport == "redis_streams"
        result = producer.publish("risk.scores", {"k": "v"})
        assert result is None
    producer.close()


def test_publish_delegates_to_stream_producer(clean_env, monkeypatch):
    """When KAFKA_BROKERS unset, publish() delegates to the wrapped
    StreamProducer.publish() — verified by monkey-patching the
    StreamProducer's publish method."""
    monkeypatch.delenv("KAFKA_BROKERS", raising=False)
    from src.stream.kafka_producer import KafkaProducer

    producer = KafkaProducer(redis_url=None)
    # Monkey-patch the wrapped StreamProducer's publish to record calls.
    calls = []
    original_publish = producer._redis.publish

    def _record(stream, fields):
        calls.append((stream, dict(fields)))
        return "redis-fake-id"

    producer._redis.publish = _record  # type: ignore[method-assign]
    result = producer.publish("risk.scores", {"order_id": "ORD-1", "score": 0.42})
    assert result == "redis-fake-id"
    assert len(calls) == 1
    assert calls[0][0] == "risk.scores"
    assert calls[0][1]["order_id"] == "ORD-1"
    # Restore for clean close.
    producer._redis.publish = original_publish  # type: ignore[method-assign]
    producer.close()


def test_flush_noop_on_redis_path(clean_env):
    """flush() is a no-op when Kafka isn't configured (Redis XADD is
    synchronous — nothing to flush)."""
    from src.stream.kafka_producer import KafkaProducer
    producer = KafkaProducer(redis_url=None)
    # Should not raise + should return None.
    producer.flush(timeout=0.5)
    producer.close()


def test_close_is_idempotent(clean_env):
    """close() can be called multiple times without raising."""
    from src.stream.kafka_producer import KafkaProducer
    producer = KafkaProducer(redis_url=None)
    producer.close()
    producer.close()  # second call must not raise
    producer.close()  # third call must not raise


def test_same_interface_as_stream_producer(clean_env):
    """KafkaProducer exposes the same publish/close contract as
    StreamProducer — drop-in replacement verification."""
    from src.stream.producer import StreamProducer
    from src.stream.kafka_producer import KafkaProducer

    sp = StreamProducer(None)
    kp = KafkaProducer(redis_url=None)
    # Both expose publish(stream, fields) -> str|None + close() -> None.
    assert hasattr(kp, "publish") and callable(kp.publish)
    assert hasattr(kp, "close") and callable(kp.close)
    # Signatures: publish takes (stream, fields), close takes ().
    import inspect
    sp_params = list(inspect.signature(sp.publish).parameters.keys())
    kp_params = list(inspect.signature(kp.publish).parameters.keys())
    assert sp_params == kp_params == ["stream", "fields"]
    sp.close()
    kp.close()
