"""Model registry (champion/challenger metadata) + PSI drift metric."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np


def load_registry(path: str = "out/model_registry.json") -> dict:
    p = Path(path)
    if not p.exists():
        return {"models": []}
    return json.loads(p.read_text())


def register_model(
    version: str,
    model_path: str,
    metrics: dict,
    champion: bool = True,
    registry_path: str = "out/model_registry.json",
) -> dict:
    reg = load_registry(registry_path)
    if champion:
        for m in reg["models"]:
            m["is_champion"] = False
    entry = {
        "version": version,
        "model_path": model_path,
        "metrics": metrics,
        "is_champion": champion,
        "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    reg["models"].append(entry)
    Path(registry_path).parent.mkdir(parents=True, exist_ok=True)
    Path(registry_path).write_text(json.dumps(reg, indent=2))
    return entry


def current_champion(registry_path: str = "out/model_registry.json") -> dict | None:
    reg = load_registry(registry_path)
    champions = [m for m in reg["models"] if m.get("is_champion")]
    return champions[-1] if champions else None


def psi(expected: list[float], actual: list[float], bins: int = 10) -> float:
    """Population Stability Index. <0.1 stable, 0.1-0.25 shift, >0.25 retrain."""
    e, a = np.asarray(expected, dtype=float), np.asarray(actual, dtype=float)
    e, a = e[~np.isnan(e)], a[~np.isnan(a)]
    if len(e) == 0 or len(a) == 0:
        return 0.0
    edges = np.unique(np.quantile(e, np.linspace(0, 1, bins + 1)))
    if len(edges) < 2:
        return 0.0
    ep = np.histogram(e, edges)[0] / len(e)
    ap = np.histogram(a, edges)[0] / len(a)
    eps = 1e-6
    return float(np.sum((ap - ep) * np.log((ap + eps) / (ep + eps))))
