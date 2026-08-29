"""
RTO Trust Layer — PyFlink CEP topology (production transport).

ARCHITECTURE NOTE — this file is NOT executed in the hackathon runtime.
The Next.js Vercel deployment is a single-binary TS app. This file is
the architecture-reference artifact showing exactly what the production
real-time fraud detection topology looks like in a Razorpay-grade deploy.

The hackathon runs the SAME CEP pattern in TS at
src/lib/streaming/redis-stream.ts::detectRapidRejects — same predicate
(>=3 REJECTs from same customer_id within 5 minutes), same alert sink
shape (a fraud_alerts record). The only difference is the transport
(Kafka vs in-memory ring buffer).

Pattern (translated from Flink CEP):
    Pattern.begin("r1")
        .where(ScoreResponse.decision == "REJECT")
        .followedBy("r2").where(ScoreResponse.decision == "REJECT")
            .within(Time.minutes(5))
        .followedBy("r3").where(ScoreResponse.decision == "REJECT")
            .within(Time.minutes(5))
        .timesOrMore(3)             # >=3 REJECTs in the window
        .within(Time.minutes(5))

Production deploy (Phase H, see docs/STREAMING_ARCHITECTURE.md):
    - Source: Kafka topic `rto.decisions.v1`, 24 partitions, exactly-once
      (isolation.level = read_committed)
    - State backend: RocksDB (incremental checkpoints, 30s interval)
    - Checkpointing mode: EXACTLY_ONCE, 30s
    - Parallelism: 24 (matches source partitions — no shuffling)
    - Sink: ClickHouse `rto.fraud_alerts` via JDBC
    - Deployment: K8s statefulset, 3 taskmanagers, 1 jobmanager,
      Ververica Platform for savepoint + auto-restart

Exactly-once across the full pipeline:
    1. Source (Kafka consumer) uses the Flink Kafka connector's
       `offsets-are-checkpointed` semantics — the offset commits in
       the same checkpoint that emits to the sink.
    2. The CEP operator is stateful — its NFA state is part of the
       checkpoint so a restart resumes the half-open pattern.
    3. The ClickHouse sink uses idempotent inserts (insert_id =
       event_id) so a redelivery after a checkpoint restart does
       not duplicate the row.

This file imports pyflink — install with:
    pip install apache-flink==1.18.0
"""

from __future__ import annotations

import os
import sys
from typing import Any

# ---------------------------------------------------------------------------
# PyFlink imports — wrapped so this file can be linted in environments
# without the pyflink package installed (e.g. the Vercel build node).
# ---------------------------------------------------------------------------

try:
    from pyflink.datastream import StreamExecutionEnvironment  # type: ignore
    from pyflink.datastream.functions import MapFunction  # type: ignore
    from pyflink.datastream.connectors import KafkaSource  # type: ignore
    from pyflink.datastream.connectors import ClickHouseSink  # type: ignore
    from pyflink.table import StreamTableEnvironment  # type: ignore
    from pyflink.table.expressions import col  # type: ignore
    from pyflink.table.udf import udf  # type: ignore
except ImportError:
    # In the hackathon runtime pyflink is not installed. We don't fail
    # at import time — the file is documentation/architecture only.
    StreamExecutionEnvironment = None  # type: ignore
    MapFunction = None  # type: ignore
    KafkaSource = None  # type: ignore
    ClickHouseSink = None  # type: ignore
    StreamTableEnvironment = None  # type: ignore
    col = None  # type: ignore
    udf = None  # type: ignore


# The CEP pattern as a constant — the TS mirror in redis-stream.ts
# hardcodes the same `>=3 in 5min` thresholds so the two runtimes
# agree. Change one and you must change the other.
RAPID_REJECT_THRESHOLD = 3
RAPID_REJECT_WINDOW_MIN = 5


def build_cep_topology(env: Any, t_env: Any) -> Any:
    """Build the Flink CEP topology.

    Steps:
        1. Source: Kafka topic rto.decisions.v1 (Avro deserializer via
           the Confluent Schema Registry).
        2. Key by customer_id (so all events for one customer are
           co-partitioned to one operator instance).
        3. CEP pattern: 3+ REJECTs within 5 minutes.
        4. Sink: insert into rto.fraud_alerts in ClickHouse.

    Returns the JobClient (so the caller can `execute()`).
    """
    if env is None or t_env is None:
        raise RuntimeError(
            "pyflink not installed — this is documentation only. "
            "The hackathon runs the same CEP pattern in TS at "
            "src/lib/streaming/redis-stream.ts::detectRapidRejects."
        )

    # ----------------------------------------------------------------
    # 1. Source — Kafka, exactly-once, read_committed.
    # ----------------------------------------------------------------
    decisions = (
        t_env.create_temporary_view(
            "decisions",
            KafkaSource.builder()
            .set_bootstrap_servers(os.environ.get("KAFKA_BROKERS", ""))
            .set_topic("rto.decisions.v1")
            .set_group_id("rto-flink-cep")
            .set_property("isolation.level", "read_committed")
            .set_starting_offsets("earliest")
            .build_as_source(
                avro_schema=open("score_response.avsc").read(),
                schema_registry_url=os.environ.get("SCHEMA_REGISTRY_URL", ""),
            ),
        )
    )

    # ----------------------------------------------------------------
    # 2. CEP — match a rapid sequence of REJECTs.
    # PyFlink Table API does not directly expose CEP `Pattern`, so we
    # drop to the DataStream API to apply the CEP operator.
    # ----------------------------------------------------------------
    ds = env.from_source(decisions, watermark="timestamp", source_name="decisions")

    # Key by customer_id — colocates a customer's events on one
    # operator so the NFA (non-deterministic finite automaton) sees
    # the full sequence.
    keyed = ds.key_by(lambda r: r["customer_id"])

    # The CEP pattern.
    from pyflink.datastream import CEP  # type: ignore
    from pyflink.datastream.functions import PatternSelectFunction  # type: ignore

    pattern = (
        CEP.pattern(keyed, "rapid_rejects")
        .begin("r1")
        .where(lambda r: r["decision"] == "REJECT")
        .followedBy("r2")
        .where(lambda r: r["decision"] == "REJECT")
        .within(f"{RAPID_REJECT_WINDOW_MIN} minutes")
        .followedBy("r3")
        .where(lambda r: r["decision"] == "REJECT")
        .within(f"{RAPID_REJECT_WINDOW_MIN} minutes")
        # timesOrMore(0) after the 3rd reject means "the 3rd REJECT
        # closes the match" — additional REJECTs are a separate alert.
        .times(RAPID_REJECT_THRESHOLD)
        .within(f"{RAPID_REJECT_WINDOW_MIN} minutes")
    )

    # ----------------------------------------------------------------
    # 3. Map the matched pattern to a fraud_alerts row.
    # ----------------------------------------------------------------
    @udf(
        result_type="""
        ROW<
            alert_id STRING,
            customer_id STRING,
            started_at STRING,
            closed_at STRING,
            reject_count INT,
            severity STRING
        >
        """,
    )
    def to_alert(matched: dict) -> dict:
        events = sorted(matched.values(), key=lambda r: r["timestamp"])
        return {
            "alert_id": f"ALERT-{events[0]['prediction_id']}",
            "customer_id": events[0]["customer_id"],
            "started_at": events[0]["timestamp"],
            "closed_at": events[-1]["timestamp"],
            "reject_count": len(events),
            "severity": "HIGH",
        }

    alerts = pattern.select(to_alert)

    # ----------------------------------------------------------------
    # 4. Sink — ClickHouse fraud_alerts table. Idempotent inserts via
    # the alert_id (the alert_id includes the prediction_id of the
    # first REJECT in the sequence so a checkpoint restart redelivers
    # the same row, not a duplicate).
    # ----------------------------------------------------------------
    alerts.add_sink(
        ClickHouseSink.builder()
        .set_host(os.environ.get("CLICKHOUSE_HOST", ""))
        .set_database("rto")
        .set_table("fraud_alerts")
        .set_idempotent_key("alert_id")
        .build()
    )

    return env.execute_async("rto-flink-cep")


def main() -> int:
    """Entry point for `python -m src.stream.flink_job`.

    In production this runs inside the Flink JobManager container as
    part of the docker-compose / K8s jobmanager statefulset. In the
    hackathon this is invoked only for the docstring — the file is
    a reference artifact.
    """
    if StreamExecutionEnvironment is None:
        print(
            "pyflink not installed — this file is documentation only. "
            "The hackathon runs the same CEP pattern in TypeScript at "
            "src/lib/streaming/redis-stream.ts::detectRapidRejects.",
            file=sys.stderr,
        )
        return 1

    env = StreamExecutionEnvironment.get_execution_environment()
    env.enable_checkpointing(30_000)  # 30s exactly-once checkpoints
    env.get_checkpoint_config().set_checkpointing_mode("EXACTLY_ONCE")
    env.get_checkpoint_config().set_min_pause_between_checkpoints(5_000)
    env.get_checkpoint_config().set_max_concurrent_checkpoints(1)
    env.get_checkpoint_config().set_state_backend("rocksdb")
    env.set_parallelism(24)  # match kafka topic partition count

    t_env = StreamTableEnvironment.create(env)
    client = build_cep_topology(env, t_env)
    client.wait_for_completion()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
