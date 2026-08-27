"""Auto port configuration — detects available ports and configures services.

Day 7 Track 12-d. Closes the "port 3000/3001 occupied → services fail to
start" class of issues by probing TCP ports at startup and writing a JSON
config file that every other service can read on boot.

Usage::

    from src.config.ports import find_free_port, auto_configure_ports
    port = find_free_port(start=8000, end=8099)  # first free port in range
    config = auto_configure_ports()             # dict: service -> port

CLI entry point: ``python scripts/auto_configure.py`` (writes
``out/port_config.json``).

Design notes:
- The Next.js dev server is **MANDATORY** on port 3000 per the project's
  system rules — this module NEVER reassigns it. Grafana therefore must
  NOT collide with 3000, so the Grafana default is 3001 with a special-case
  guard that bumps it to ``find_free_port(3001, 3010)`` if 3001 is taken.
- ``is_port_free`` opens a ``SO_REUSEADDR`` socket and binds it; this is the
  same primitive ``uvicorn`` / ``postgres`` / ``grafana`` use to claim a port,
  so a free probe here means the port will be bindable when the service
  actually starts (modulo a TOCTOU race — see ``_WARNING`` below).
- ``read_port_config`` falls back to ``DEFAULT_PORTS`` if the JSON file is
  missing (e.g. first run, or a CI worker that didn't run the auto-configure
  step), so importing the module never raises.

The function set is intentionally small so it composes cleanly with the
existing dual-mode Settings object — ``read_port_config()`` is the read side,
``write_port_config()`` is the write side, and ``auto_configure_ports()`` is
the policy that decides which service gets which port.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

# Default ports per service. The Next.js dev server is NOT in this dict
# because its port (3000) is MANDATORY per the project system rules — the
# auto-configurer does not own it. Grafana is 3001 by default (NOT 3000) to
# avoid the historical collision with Next.js.
DEFAULT_PORTS: dict[str, int] = {
    "fastapi": 8000,
    "postgres": 5432,
    "redis": 6379,
    "grafana": 3001,  # NOT 3000 — conflicts with Next.js
    "prometheus": 9090,
    "otel_collector_grpc": 4317,
    "otel_collector_http": 4318,
    "k6": 0,  # k6 doesn't need a port — placeholder for symmetry
}

# Where the auto-detected port config is written. Other services read this
# on startup via ``read_port_config()``. Relative to the project root
# (``upload/RTO_Trust_Layer_FULL/``) so docker-compose + uvicorn + tests
# all agree on the path.
DEFAULT_CONFIG_PATH = "out/port_config.json"


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is available for binding.

    Opens a ``SO_REUSEADDR`` socket and attempts to ``bind`` — if it
    succeeds, the port is free for the calling process to claim. Mirrors
    what ``uvicorn`` / ``postgres`` / ``grafana`` do to acquire a port, so
    a free probe here is a reliable signal the port is bindable.

    A port of 0 means "no port needed" (e.g. k6) — treated as free.
    """
    if port == 0:
        return True
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return True
    except (OSError, socket.error):
        return False


def find_free_port(start: int, end: int, host: str = "127.0.0.1") -> int:
    """Find the first free port in the inclusive range ``[start, end]``.

    Returns ``-1`` if no port in the range is bindable. The range is
    inclusive on both ends to match the convention in the rest of the
    codebase (e.g. ``range(default, default + 10)`` finds up to 11 ports).
    """
    for port in range(start, end + 1):
        if is_port_free(port, host):
            return port
    return -1


def auto_configure_ports() -> dict[str, int]:
    """Auto-detect free ports for every service in ``DEFAULT_PORTS``.

    Policy:
    1. Try the default port first. If free, use it.
    2. If the default is taken, scan the next 10 ports (``[default,
       default+10]``) and pick the first free one.
    3. If no port in that range is free (very unusual — 10 consecutive
       ports all in use), fall back to the default and let the operator
       see the "IN USE" warning when ``auto_configure.py`` prints the
       table.
    4. Special case: Grafana must NEVER be 3000 (conflicts with the
       Next.js dev server, which is mandatory per system rules). If the
       default-then-scan somehow lands on 3000, bump to
       ``find_free_port(3001, 3010)``.

    Returns a dict ``{service_name: port}`` — same shape as
    ``DEFAULT_PORTS``.
    """
    config: dict[str, int] = {}
    for service, default_port in DEFAULT_PORTS.items():
        if default_port == 0:
            # k6 / no-port services — skip the probe entirely.
            config[service] = 0
            continue
        if is_port_free(default_port):
            config[service] = default_port
        else:
            free = find_free_port(default_port, default_port + 10)
            config[service] = free if free != -1 else default_port
    # Grafana special case: NEVER 3000 (Next.js conflict). If the scan
    # above somehow lands on 3000 (e.g. 3001-3010 are all taken AND
    # 3000 is somehow free), explicitly bump to the 3001-3010 range.
    if config.get("grafana") == 3000:
        bumped = find_free_port(3001, 3010)
        config["grafana"] = bumped if bumped != -1 else 3001
    return config


def write_port_config(path: str = DEFAULT_CONFIG_PATH) -> dict[str, int]:
    """Auto-detect ports + write the result to ``path`` as JSON.

    Other services read this file at startup via ``read_port_config()``.
    The ``out/`` directory is created if missing so a fresh checkout
    works without a manual ``mkdir``.
    """
    config = auto_configure_ports()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(config, f, indent=2, sort_keys=True)
    return config


def read_port_config(path: str = DEFAULT_CONFIG_PATH) -> dict[str, int]:
    """Read the port config written by ``write_port_config``.

    Falls back to ``DEFAULT_PORTS`` (a copy) if the file is missing —
    e.g. a fresh checkout that hasn't run ``scripts/auto_configure.py``
    yet, or a CI worker that skipped the auto-configure step. This means
    importing the module + calling ``read_port_config`` never raises, so
    the FastAPI app's lifespan can call it unconditionally.
    """
    p = Path(path)
    if p.exists():
        try:
            with open(p) as f:
                data: dict[str, Any] = json.load(f)
            # Coerce to int — JSON keys are strings, but our values
            # should always be ints. Defensive in case a hand-edit
            # wrote a string.
            return {k: int(v) for k, v in data.items()}
        except (json.JSONDecodeError, ValueError, OSError):
            # Corrupt file — fall through to defaults rather than
            # crashing the caller (which is usually app startup).
            pass
    return DEFAULT_PORTS.copy()


__all__ = [
    "DEFAULT_PORTS",
    "DEFAULT_CONFIG_PATH",
    "is_port_free",
    "find_free_port",
    "auto_configure_ports",
    "write_port_config",
    "read_port_config",
]
