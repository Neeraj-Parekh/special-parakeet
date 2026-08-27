"""Multi-source ingest simulators for the RTO Trust Layer.

Day 4 Track M — per the Microsoft Fabric real-time fraud-detection
reference architecture
(https://learn.microsoft.com/en-us/fabric/real-time-intelligence/architectures/fraud-detection),
a real fraud-detection platform ingests from MULTIPLE channels (mobile
banking, ATM, e-commerce, call center). The RTO Trust Layer's /v1/risk/score
endpoint is the unified entry point; these simulators normalize each
channel's source format to the unified ``OrderIn`` Pydantic model (in
``src/api/routes.py``) + post to the API with a ``X-Channel`` header so
the audit record carries the channel discriminator (per Kandula 2021
paper's "Payment_Type as a discriminator feature" insight — here we use
``channel`` as the discriminator for per-channel drift detection via
TFX ``generate_data_statistics``).

Modules:
  * ``ecommerce``  — the existing REST /v1/risk/score path. Documented as
                     the "e-commerce channel" — no simulator needed.
  * ``mobile``     — mobile banking simulator: a Kafka topic consumer
                     pattern (mock — uses requests loop + posts to
                     /v1/risk/score with ``X-Channel: mobile``).
  * ``atm``        — ATM simulator: batch CSV ingest from "ATM switch
                     logs" (mock CSV). Daily batch.
  * ``callcenter`` — call center simulator: webhook receiver pattern.
                     When a call center agent flags an order, it posts to
                     /v1/risk/score with ``X-Channel: call_center``.

The simulators don't require Kafka / Redis Stream / webhook infrastructure
to run — they generate mock data + post to /v1/risk/score directly. The
``scripts/run_simulators.py`` script runs all 4 in parallel for 60s as a
demo of multi-channel ingestion.

Kandula 2021 source: see `docs/research/INDEX.md` for the paper
distillation. The key insight is that fraud patterns vary by channel
(mobile fraud has different signatures than ATM fraud), so per-channel
drift detection is more sensitive than aggregate drift detection. The
``channel`` field in the audit record enables this via a
``GROUP BY channel`` slice on the TFX generate_data_statistics job in
``src/stream/processor.py``.
"""
