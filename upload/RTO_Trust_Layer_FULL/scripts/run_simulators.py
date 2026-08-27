#!/usr/bin/env python3
"""Demo script: run all 4 multi-source ingest simulators in parallel.

Day 4 Track M — Microsoft Fabric multi-source fraud-detection reference
(https://learn.microsoft.com/en-us/fabric/real-time-intelligence/architectures/fraud-detection)
calls out 4 ingest channels: mobile banking, ATM, e-commerce, call
center. This script runs all 4 simulators in parallel threads for 60
seconds (default) to demonstrate multi-channel ingestion into the RTO
Trust Layer /v1/risk/score endpoint.

Usage:
    # Start the API first (terminal 1):
    uvicorn src.api.routes:create_app --factory --port 8000

    # Then run the simulators (terminal 2):
    python scripts/run_simulators.py --duration 60

    # Or shorter for a quick demo:
    python scripts/run_simulators.py --duration 15

The script:
  * Spins up 4 threads (one per channel simulator).
  * Each thread posts mock orders to http://localhost:8000/risk/score
    with the appropriate ``X-Channel`` header.
  * After ``--duration`` seconds, signals all threads to stop via a
    shared ``threading.Event`` + waits for them to drain.
  * Prints a per-channel summary (orders posted, errors).

The 4 channels:
  * ``ecommerce``  — the existing REST path. This simulator posts
                     mock e-commerce orders at 2/sec.
  * ``mobile``      — mobile banking simulator. 2/sec (matches mobile
                     UPI peak hour throughput).
  * ``atm``         — ATM simulator. Daily batch — runs once at start
                     with 100 mock ATM transactions.
  * ``callcenter``  — call center simulator. 1/5sec (matches ~720
                     flags/day for a mid-size e-commerce merchant).

Total demo volume at defaults: ~240 mobile + ~240 e-commerce + 100 ATM
+ 12 call center ≈ 590 orders over 60s.

Env vars:
    RTO_SCORER_KEY  — the scorer-scope API key (default: score-demo-key,
                       matches the docker-compose api service default).
    RTO_API_URL     — the /risk/score endpoint URL (default:
                       http://localhost:8000/risk/score).

The script exits 0 on success (all threads drained cleanly) or 1 on
hard failure (e.g. the requests library isn't installed).

Source: Microsoft Fabric fraud-detection reference (the 4 channels
mirror their reference architecture's ingest layer).
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time

# Make ``src/`` importable when running as a script (no install needed).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ingest import atm, callcenter, ecommerce, mobile  # noqa: E402


def _run_one(
    name: str,
    fn,
    duration_s: float,
    api_url: str,
    scorer_key: str,
    stop_event: threading.Event,
    results: dict,
) -> None:
    """Run one simulator + capture its posted count."""
    try:
        posted = fn(
            duration_s=duration_s,
            api_url=api_url,
            scorer_key=scorer_key,
            stop_event=stop_event,
        )
        results[name] = {"posted": posted, "error": None}
    except Exception as e:
        results[name] = {"posted": 0, "error": f"{type(e).__name__}: {e}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--duration", type=float, default=60.0,
        help="Seconds to run the simulators (default: 60).",
    )
    parser.add_argument(
        "--api-url", default=os.environ.get("RTO_API_URL", "http://localhost:8000/risk/score"),
        help="The /risk/score endpoint URL (default: http://localhost:8000/risk/score).",
    )
    parser.add_argument(
        "--scorer-key", default=os.environ.get("RTO_SCORER_KEY", "score-demo-key"),
        help="The scorer-scope API key (default: score-demo-key).",
    )
    parser.add_argument(
        "--channels", default="ecommerce,mobile,atm,callcenter",
        help="Comma-separated channel list to run (default: all 4).",
    )
    args = parser.parse_args(argv)

    # Make sure ``requests`` is importable (the simulators lazy-import it).
    try:
        import requests  # noqa: F401
    except ImportError as e:
        print(f"ERROR: requests library not installed — pip install requests: {e}", file=sys.stderr)
        return 1

    # Channel registry — maps CLI name → callable. The e-commerce
    # channel doesn't have a run() in src/ingest/ecommerce.py (it's the
    # reference REST path); the demo uses the inline ``ecommerce_run``
    # below which generates mock e-commerce orders at 2/sec.
    def ecommerce_run(duration_s, api_url, scorer_key, stop_event=None):
        """Post mock e-commerce orders at 2/sec — mirrors the merchant web checkout."""
        import random
        import time as _time

        import requests as _r
        rng = random.Random(0)
        headers = {
            "Authorization": f"Bearer {scorer_key}",
            "X-Channel": ecommerce.CHANNEL_ECOMMERCE,
            "Content-Type": "application/json",
        }
        start = _time.monotonic()
        posted = 0
        i = 0
        while _time.monotonic() - start < duration_s:
            if stop_event is not None and stop_event.is_set():
                break
            order = {
                "order_id": f"EC-ORDER-{i:06d}",
                "amount_inr": round(rng.uniform(500, 25000), 2),
                "category": rng.choice(["Fashion", "Electronics", "Home", "Books", "Grocery"]),
                "customer_id": f"CUST-EC-{i:06d}",
                "payment_method": rng.choice(["COD", "Prepaid"]),
                "city_tier": rng.choice(["tier_1", "tier_2", "tier_3"]),
                "address_quality": rng.choice(["complete", "partial", "vague"]),
                "prior_orders": rng.randint(0, 30),
                "prior_returns": rng.randint(0, 5),
                "items": rng.randint(1, 5),
                "order_hour": rng.randint(8, 23),
                "device": rng.choice(["Web", "Android App", "iOS App"]),
            }
            try:
                resp = _r.post(api_url, json=order, headers=headers, timeout=5)
                if resp.status_code == 200:
                    posted += 1
                    body = resp.json()
                    print(
                        f"[ecommerce] {order['order_id']} → "
                        f"{body.get('decision')} (p={body.get('probability')})"
                    )
                else:
                    print(f"[ecommerce] {order['order_id']} → HTTP {resp.status_code}")
            except Exception as e:
                print(f"[ecommerce] {order['order_id']} → ERROR {type(e).__name__}: {e}")
            i += 1
            _time.sleep(0.5)
        return posted

    channel_fns = {
        "ecommerce": ecommerce_run,
        "mobile": mobile.run,
        "atm": atm.run,
        "callcenter": callcenter.run,
    }

    selected = [c.strip() for c in args.channels.split(",") if c.strip()]
    unknown = [c for c in selected if c not in channel_fns]
    if unknown:
        print(f"ERROR: unknown channel(s): {unknown}. Valid: {list(channel_fns)}", file=sys.stderr)
        return 1

    # Shared stop event — signal handler sets it on SIGINT/SIGTERM so
    # the threads drain cleanly (in-flight posts finish, then exit).
    stop_event = threading.Event()

    def _signal_handler(signum, frame):
        print(f"\n[run_simulators] received signal {signum} — draining...", file=sys.stderr)
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    print(f"[run_simulators] starting {len(selected)} channels for {args.duration}s")
    print(f"[run_simulators] api_url={args.api_url}")
    print(f"[run_simulators] channels={selected}")

    results: dict = {}
    threads: list[threading.Thread] = []
    for channel in selected:
        t = threading.Thread(
            target=_run_one,
            args=(
                channel,
                channel_fns[channel],
                args.duration,
                args.api_url,
                args.scorer_key,
                stop_event,
                results,
            ),
            name=f"ingest-{channel}",
            daemon=True,
        )
        t.start()
        threads.append(t)

    # Wait for the duration (or until all threads finish early — ATM
    # finishes in <1s since it's a batch).
    start = time.monotonic()
    while time.monotonic() - start < args.duration:
        if all(not t.is_alive() for t in threads):
            break
        time.sleep(0.5)

    # Signal any still-running threads to stop.
    stop_event.set()
    # Wait for all threads to drain (max 10s).
    for t in threads:
        t.join(timeout=10.0)

    # Print summary.
    print("\n" + "=" * 60)
    print("Multi-channel ingest summary")
    print("=" * 60)
    total_posted = 0
    for channel in selected:
        r = results.get(channel, {"posted": 0, "error": "thread didn't report"})
        posted = r.get("posted", 0)
        total_posted += posted
        err = r.get("error")
        if err:
            print(f"  {channel:12} → ERROR: {err}")
        else:
            print(f"  {channel:12} → {posted} orders posted")
    print(f"  {'TOTAL':12} → {total_posted} orders posted")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
