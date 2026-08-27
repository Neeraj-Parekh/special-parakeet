#!/usr/bin/env python3
"""Auto-configure ports for all RTO Trust Layer services.

Day 7 Track 12-d. Run this BEFORE ``docker compose up`` or the FastAPI
server. It probes every service's default port, bumps to a free neighbour
if the default is occupied, and writes the result to
``out/port_config.json`` which all services read on startup via
``src.config.ports.read_port_config``.

The Next.js dev server is NOT touched — port 3000 is MANDATORY per the
project's system rules. Grafana is therefore never 3000 (conflicts).

Usage::

    python scripts/auto_configure.py           # writes out/port_config.json
    python scripts/auto_configure.py --check   # exit 1 on any conflict
"""
from __future__ import annotations

import os
import sys

# Make ``src.*`` importable when the script is run directly (no install).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.ports import (  # noqa: E402  (sys.path tweak above)
    DEFAULT_PORTS,
    auto_configure_ports,
    is_port_free,
    read_port_config,
    write_port_config,
)

# Mandatory per project system rules — the auto-configurer must NEVER
# reassign this. Surfaced here so the printed table reminds the operator.
NEXT_JS_PORT = 3000


def _format_status(port: int) -> str:
    """Pretty-print the FREE / IN USE status for a port."""
    if port == 0:
        return "n/a (no port needed)"
    return "FREE" if is_port_free(port) else "IN USE (will conflict)"


def main() -> int:
    """CLI entry point — returns 0 on success, 1 if any conflict detected."""
    check_only = "--check" in sys.argv
    print("=== RTO Trust Layer — Auto Port Configuration ===")
    print()
    print(f"Next.js dev server (MANDATORY): port {NEXT_JS_PORT}  "
          f"[{_format_status(NEXT_JS_PORT)}]")
    print()

    config = auto_configure_ports()

    # If not check-only, write the config to disk for other services.
    if not check_only:
        config = write_port_config()
        print(f"Port config written to out/port_config.json:")
    else:
        print("CHECK-ONLY mode — no file written. (use without --check to write)")

    print()
    any_conflict = False
    for service, port in config.items():
        status = _format_status(port)
        print(f"  {service:30s} -> {port:5d}  [{status}]")
        # A service on a non-zero port that's IN USE means it will conflict
        # at start time — the auto-configurer bumped it but the next 10
        # ports were also taken (very rare).
        if port != 0 and not is_port_free(port):
            any_conflict = True

    # Grafana-specific guard: NEVER 3000 (Next.js conflict).
    if config.get("grafana") == NEXT_JS_PORT:
        print()
        print("WARNING: Grafana is on port 3000 — conflicts with Next.js!")
        print("   Run this script again or set GF_SERVER_HTTP_PORT=3001 "
              "in docker-compose.yml")
        any_conflict = True

    # Next.js sanity check: 3000 should be FREE for the dev server to grab
    # (we don't manage it, but warn if something is squatting on it).
    if not is_port_free(NEXT_JS_PORT):
        print()
        print(f"WARNING: port {NEXT_JS_PORT} (Next.js, MANDATORY) is IN USE.")
        print("   Stop the process holding it before starting the dashboard:")
        print("     lsof -i :3000   # then kill the offending PID")

    print()
    if any_conflict:
        print("Port config complete with CONFLICTS — see warnings above.")
        return 1

    print("Port config complete. Start services with:")
    print("   docker compose up -d                # postgres + redis + grafana")
    print("   uvicorn src.api.routes:create_app --factory "
          f"--port {config['fastapi']}")
    return 0


if __name__ == "__main__":
    # Allow ``python scripts/auto_configure.py`` AND ``python -m``-style.
    # Note: exit code matters for CI — 0 = clean, 1 = conflict detected.
    sys.exit(main())

# Convenience for ``from scripts.auto_configure import main`` callers.
__all__ = ["main", "read_port_config", "DEFAULT_PORTS"]
