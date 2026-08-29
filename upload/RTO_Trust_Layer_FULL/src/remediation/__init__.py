"""Remediation package — auto-heal skeleton (Pham et al. FSE'24, ArXiv 2405.09330).

Public API: see ``src.remediation.auto_heal``.
"""
from src.remediation.auto_heal import (  # noqa: F401
    AutoHealService,
    HealEvent,
    HANDLER_REGISTRY,
)

__all__ = ["AutoHealService", "HealEvent", "HANDLER_REGISTRY"]
