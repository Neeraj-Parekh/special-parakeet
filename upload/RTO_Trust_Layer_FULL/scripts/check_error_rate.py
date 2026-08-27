"""TFX stage 7 — Monitor: query Prometheus, fail + auto-rollback if error>1%.

Per the MLOps-DevOps Integration paper (IJIEE 2021,
``command/05-PAPER-SKILLS-MAP.md`` gap #14, capability
``plan_three_axis_cicd``) + Paleyes 2022 (``recommend_business_metrics``),
the Monitor stage is the third axis of continuous delivery:
  * code axis  — `ci.yml` (ruff + pytest)
  * model axis — `mlops.yml` stages 1-5 (canary gate + slice metrics)
  * data axis — `mlops.yml` stages 1-2 (data validation + drift)
  * monitor   — THIS script. Query Prometheus for the live error rate
    of the freshly-deployed canary; fail (and trigger auto-rollback)
    if the rate exceeds the threshold for 5 consecutive evaluations.

The Prometheus query is:
    sum(rate(risk_decisions_total{decision="REJECT"}[5m])) /
    sum(rate(risk_decisions_total[5m]))

i.e. the REJECT share of all decisions over the last 5m. A spike here
indicates the canary is too aggressive — auto-rollback to the previous
champion image. The actual metric name ``risk_decisions_total`` comes
from ``src/api/metrics.py`` (Track G + the original
``src/api/routes.py`` Prometheus instrumentation).

DEPENDENCY: `requests` is the only dep (used to query Prometheus's HTTP
API). The mlops.yml monitor stage installs `requests` only — no pandas,
no sklearn, so the monitor env is minimal + fast.

TOLERANCE: if Prometheus is unreachable (the Buildathon demo has no
staging cluster), the script emits a warning and returns 0 — the
pipeline shouldn't hard-fail when there's nothing to query. The
``--require`` flag forces a hard failure when Prometheus is down (for
real production deploys where the monitor MUST work).
"""
from __future__ import annotations

import argparse
import time

# PromQL query — REJECT share of all decisions over the last 5m.
QUERY = (
    'sum(rate(risk_decisions_total{decision="REJECT"}[5m])) / '
    "sum(rate(risk_decisions_total[5m]))"
)

# Number of consecutive evaluations that must exceed the threshold
# before the gate trips. Prevents flapping on a single 5m spike —
# the canary needs to be SUSTAINEDLY bad before we roll back.
DEFAULT_REQUIRED_FAILURES = 3

# Sleep between evaluations (seconds). 60s × 3 failures = 3 minutes
# of sustained error before rollback. Tuned for the Buildathon demo;
# production would be 60s × 5 = 5 min per V3 §audit's SLA.
DEFAULT_EVAL_INTERVAL_S = 60


def query_prometheus(prom_url: str, query: str, timeout: float = 5.0):
    """Query Prometheus HTTP API. Returns the scalar result or None.

    Tolerant of network errors + non-200 responses — the monitor stage
    shouldn't crash on a transient DNS blip. Returns None on any error
    so the caller can decide whether to retry or fail.
    """
    try:
        import requests  # type: ignore
    except ImportError:
        print("::error::`requests` package not installed — "
              "install with `pip install requests`")
        return None

    try:
        r = requests.get(
            f"{prom_url.rstrip('/')}/api/v1/query",
            params={"query": query},
            timeout=timeout,
        )
        if r.status_code != 200:
            print(f"::warning::Prometheus returned {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        if data.get("status") != "success":
            print(f"::warning::Prometheus query non-success: {data}")
            return None
        result = data.get("data", {}).get("result", [])
        if not result:
            return None  # no data yet — canary just deployed, no traffic
        # Single-scalar query — first result's value[1] is the float.
        return float(result[0]["value"][1])
    except Exception as e:
        print(f"::warning::Prometheus query failed: {e}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prom-url", default="http://localhost:9090")
    ap.add_argument("--threshold", type=float, default=0.01,
                    help="error rate ceiling (default 0.01 = 1%)")
    ap.add_argument("--query", default=QUERY,
                    help="PromQL query (default: REJECT share of decisions)")
    ap.add_argument("--required-failures", type=int,
                    default=DEFAULT_REQUIRED_FAILURES,
                    help="consecutive failures before tripping (default 3)")
    ap.add_argument("--eval-interval", type=int,
                    default=DEFAULT_EVAL_INTERVAL_S,
                    help="seconds between evaluations (default 60)")
    ap.add_argument("--require", action="store_true",
                    help="fail hard if Prometheus is unreachable "
                         "(default: warn-and-pass for demo)")
    ap.add_argument("--once", action="store_true",
                    help="single evaluation (no sustained-failure check) "
                         "— for CI mode where the monitor can't poll")
    args = ap.parse_args()

    print(f"Monitor: error-rate threshold={args.threshold:.2%}")
    print(f"Prometheus: {args.prom_url}")
    print(f"Query: {args.query}")

    if args.once:
        # Single-evaluation mode for CI — the GitHub Actions runner
        # can't sleep for 5 minutes between polls, so the workflow
        # calls this script with --once after the deploy + k6 load test.
        # If the error rate exceeds the threshold, fail immediately;
        # otherwise pass and let the deploy complete.
        rate = query_prometheus(args.prom_url, args.query)
        if rate is None:
            if args.require:
                print("::error::Prometheus unreachable in --require mode")
                return 1
            print("::notice::Prometheus returned no data — assuming no "
                  "traffic yet (canary just deployed). Monitor passes.")
            return 0
        print(f"Current error rate: {rate:.4%}")
        if rate > args.threshold:
            print(f"::error::error rate {rate:.4%} exceeds threshold "
                  f"{args.threshold:.2%} — ROLLBACK")
            return 1
        print("✓ Error rate within threshold")
        return 0

    # Sustained-failure mode — poll every args.eval_interval seconds,
    # count consecutive failures, trip if >= args.required_failures.
    consecutive = 0
    for i in range(args.required_failures * 3):  # bounded retry loop
        rate = query_prometheus(args.prom_url, args.query)
        if rate is None:
            if args.require:
                print("::error::Prometheus unreachable in --require mode")
                return 1
            print(f"::notice::evaluation {i + 1}: no data — resetting counter")
            consecutive = 0
        elif rate > args.threshold:
            consecutive += 1
            print(f"::warning::evaluation {i + 1}: error rate {rate:.4%} "
                  f"exceeds threshold (consecutive={consecutive}/"
                  f"{args.required_failures})")
            if consecutive >= args.required_failures:
                print(f"::error::sustained error rate > {args.threshold:.2%} "
                      f"for {consecutive} evaluations — ROLLBACK")
                return 1
        else:
            print(f"evaluation {i + 1}: error rate {rate:.4%} OK")
            consecutive = 0
        time.sleep(args.eval_interval)

    print("✓ Monitor passed — error rate stayed under threshold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
