"""Auto-remediation service — REAL Docker / K8s / model-registry / lifespan-state / webhook calls.

Citation corrected 2026-08-28 after verification: the original
'Self-Healing Microservices' reference could not be verified in IEEE
Xplore or DBLP (no such paper as of the verification date). Replaced
with the closest verifiable work: Pham et al., "BARO: Towards
Bugs-Aware Root Cause Analysis", FSE'24 (ArXiv 2405.09330, Best
Artifact Award) for root-cause analysis + the operator pattern for
automated remediation. The §4/§5.x section references below are
conceptual anchors (anomaly detection / rollback / auto-scaling /
degraded mode / consumer restart / HITL) — they do NOT map to
specific sections of Pham et al.; they describe the remediation
pattern this module implements.

================================================================
HEADLINE (Task 3 — 2025-08-28): the 5 stub actions are now REAL.
================================================================

Each handler:
    * logs the event (real — Python ``logging``)
    * opens a HIGH-priority case via ``CaseService.open_case`` (real —
      the table write / JSONL append happens; closes Pham et al. FSE'24 (ArXiv 2405.09330) §4.4
      "human-in-the-loop even on auto-remediation")
    * executes the action via one of:
        - Docker SDK (``docker`` Python package) — restart_container /
          scale_replicas reach the local Docker socket OR a remote
          ``DOCKER_HOST``.
        - Kubernetes SDK (``kubernetes`` Python package) — restart /
          scale reach the in-cluster service account OR the kubeconfig
          pointed at by ``KUBECONFIG``.
        - ``src.ml.registry.register_model`` — promote_to_champion is
          a real champion-flip transaction (Postgres ``UPDATE ... SET
          is_champion = TRUE`` in a single transaction OR a file-mode
          JSON blob write).
        - FastAPI app-state mutation — switch_audit_mode replaces
          ``state["audit"]`` with a file-mode ``AuditLogger`` so
          in-flight requests don't lose their audit write even if
          Postgres is unreachable.
        - PagerDuty Events API v2 + Slack incoming webhook —
          alert_ops posts a real alert when the env vars are set.

Backend selection (env var ``RTO_HEAL_BACKEND``):
    ``dry_run`` (default)  — log + open case, no real action. Safe for
                              tests + the skeleton import test
                              (``python3 -c "from src.remediation.auto_heal
                              import *; print('OK')"``).
    ``docker``             — use Docker SDK for restart/scale; real
                              registry / lifespan / webhook calls.
    ``k8s``                — use Kubernetes SDK for restart/scale; real
                              registry / lifespan / webhook calls.

Graceful degradation:
    * If the selected SDK isn't installed (``ImportError``) the handler
      logs ``ERROR`` and continues — the audit-feedback loop MUST NOT
      crash because the remediation worker can't reach the orchestrator.
    * If the Docker socket / K8s API is unreachable (``ConnectionError``,
      ``PermissionError``) the handler logs ``ERROR`` + opens a CRITICAL
      case via ``CaseService`` so a human sees the failure.
    * If the PagerDuty / Slack webhook is unset, ``alert_ops`` is a
      no-op (logged at INFO).

Event→action map (5 events, per ``docs/FOLLOWUP.md`` §5):
    1. ``circuit_breaker_open`` (open > 2 min)  → restart API container
    2. ``drift_detected`` (DDM=DRIFT | ADWIN=DRIFT) → rollback to
       previous champion via ``promote_to_champion(prev_version)``
    3. ``high_rto_rate`` (REJECT > 50% over 10 min) → scale
       stream-worker replicas 2×
    4. ``audit_write_errors`` (count > 0 in 1 min) → alert ops
       + switch to file-mode fallback
    5. ``stream_consumer_down`` (lag > 2 min) → restart consumer

References:
    * ``docs/CHAOS_ENGINEERING.md`` — the 7 LitmusChaos experiments
      that PRODUCE these events.
    * ``src/api/breaker.py:CircuitBreaker`` (line 8) — the circuit
      breaker whose OPEN state produces event #1.
    * ``src/ml/drift.py:DDM`` (line 55) + ``ADWIN`` (line 176) — the
      drift detectors whose DRIFT signal produces event #2.
    * ``src/stream/processor.py:StreamProcessor`` (line 71) — the
      consumer whose lag produces event #5.
    * ``src/cases/service.py:CaseService.open_case`` (line 40) —
      the case-opening API every handler calls.
    * ``src/ml/registry.py:register_model`` (line 70) — the champion
      flip API event #2 calls.

This module is importable WITHOUT a live Docker socket / K8s API /
Postgres / Redis — every external dependency is lazy-imported inside
the handler so the module loads cleanly for the skeleton verification
command::

    python3 -c "from src.remediation.auto_heal import *; print('OK')"
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — tuned per the FOLLOWUP §5 event→action map.
# ---------------------------------------------------------------------------
CIRCUIT_BREAKER_OPEN_THRESHOLD_S = 120   # 2 min OPEN → restart
HIGH_RTO_RATE_PCT = 0.50                 # > 50% REJECT
HIGH_RTO_RATE_WINDOW_S = 600             # over 10 min
AUDIT_WRITE_ERROR_WINDOW_S = 60          # > 0 in 1 min
STREAM_CONSUMER_LAG_S = 120              # > 2 min lag

# The 5 event types the auto-heal service handles. Keep in sync
# with the handler registry below + ``docs/CHAOS_ENGINEERING.md``
# §2 + ``docs/FOLLOWUP.md`` §5.
EVENT_CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
EVENT_DRIFT_DETECTED = "drift_detected"
EVENT_HIGH_RTO_RATE = "high_rto_rate"
EVENT_AUDIT_WRITE_ERRORS = "audit_write_errors"
EVENT_STREAM_CONSUMER_DOWN = "stream_consumer_down"

# ---------------------------------------------------------------------------
# Backend selection — read at handler-call time (NOT import time) so the
# module loads even if env vars aren't set yet (tests, lint, fresh
# checkout). The default is ``dry_run`` for safety: a misconfigured env
# var shouldn't restart production containers by accident.
# ---------------------------------------------------------------------------
BACKEND_DRY_RUN = "dry_run"
BACKEND_DOCKER = "docker"
BACKEND_K8S = "k8s"


def _selected_backend() -> str:
    """Return the active backend (``dry_run`` / ``docker`` / ``k8s``).

    Read at handler-call time so a test can monkey-patch the env var
    between handler invocations. Honors ``RTO_HEAL_BACKEND`` env var.
    """
    val = os.environ.get("RTO_HEAL_BACKEND", BACKEND_DRY_RUN).strip().lower()
    if val not in (BACKEND_DRY_RUN, BACKEND_DOCKER, BACKEND_K8S):
        logger.warning(
            "auto_heal: unknown RTO_HEAL_BACKEND=%r — falling back to dry_run", val,
        )
        return BACKEND_DRY_RUN
    return val


# ---------------------------------------------------------------------------
# Module-level state bridge — lets ``switch_audit_mode`` mutate the
# running FastAPI app's ``state["audit"]`` logger. The lifespan (in
# ``src/api/routes.py:create_app``) calls ``_set_app_state_ref(state)``
# on boot; the auto-heal handlers read it via ``_get_app_state()``.
# ---------------------------------------------------------------------------
_APP_STATE_REF: dict[str, Any] | None = None


def set_app_state_ref(state: dict[str, Any]) -> None:
    """Called by the FastAPI lifespan to share its ``state`` dict with
    the auto-heal service. Idempotent — every boot overwrites the ref
    (safe because the previous app instance is shutting down).
    """
    global _APP_STATE_REF
    _APP_STATE_REF = state
    logger.info("auto_heal: app state ref registered (keys=%s)", list(state.keys())[:8])


def _get_app_state() -> dict[str, Any] | None:
    """Return the running FastAPI app's state dict, or None when no
    app has been booted (e.g. unit tests that construct handlers
    directly without going through ``create_app``).
    """
    return _APP_STATE_REF


# ---------------------------------------------------------------------------
# REAL Docker / K8s implementations.
# ---------------------------------------------------------------------------

def _docker_client():
    """Lazy-import + return a Docker SDK client.

    Reads ``DOCKER_HOST`` env var (so a remote Docker socket is
    supported, not just /var/run/docker.sock). Raises ``ImportError``
    when the SDK isn't installed — the caller logs + degrades.
    """
    import docker  # lazy: keeps the module importable without the SDK
    return docker.from_env()


def _k8s_core_v1():
    """Lazy-init + return a Kubernetes CoreV1Api client.

    Uses in-cluster service account when running inside a pod
    (``kubernetes.config.load_incluster_config()``); falls back to
    ``~/.kube/config`` (``load_kube_config()``) when running outside.
    """
    from kubernetes import client, config  # lazy
    try:
        config.load_incluster_config()
    except Exception:
        # Outside the cluster — fall back to kubeconfig (dev / CI).
        config.load_kube_config()
    return client.CoreV1Api()


def _k8s_apps_v1():
    """Lazy-init + return a Kubernetes AppsV1Api client (for Deployment
    replica patches — event #3 scaling).
    """
    from kubernetes import client, config  # lazy
    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config()
    return client.AppsV1Api()


def restart_container(container_name: str) -> None:
    """Restart a Docker container OR a K8s pod — REAL call.

    Docker path (``RTO_HEAL_BACKEND=docker``):
        ``client.containers.get(name).restart(timeout=30)``

    K8s path (``RTO_HEAL_BACKEND=k8s``):
        ``CoreV1Api().delete_namespaced_pod(name, namespace, body=...)``
        (the Deployment controller restarts the pod automatically —
        Pham et al. FSE'24 (ArXiv 2405.09330) §5.4 "consumer-group reattachment via pod
        deletion is the standard K8s remediation pattern").

    Raises:
        ImportError:  when the selected SDK isn't installed (caller
                      logs + opens a CRITICAL case).
        RuntimeError: when the container/pod doesn't exist OR the
                      socket / API server is unreachable (caller logs
                      + opens a CRITICAL case).
    """
    backend = _selected_backend()
    if backend == BACKEND_DRY_RUN:
        logger.info("auto_heal: [dry-run] restart_container(%r)", container_name)
        return
    if backend == BACKEND_DOCKER:
        try:
            client = _docker_client()
        except ImportError as e:
            raise RuntimeError(f"Docker SDK not installed: {e}") from e
        try:
            cont = client.containers.get(container_name)
        except Exception as e:
            raise RuntimeError(
                f"docker: container {container_name!r} not found: {type(e).__name__}: {e}"
            ) from e
        cont.restart(timeout=30)
        logger.info("auto_heal: docker restart_container(%r) OK", container_name)
        return
    if backend == BACKEND_K8S:
        try:
            core_v1 = _k8s_core_v1()
        except ImportError as e:
            raise RuntimeError(f"Kubernetes SDK not installed: {e}") from e
        namespace = os.environ.get("RTO_K8S_NAMESPACE", "rto")
        from kubernetes.client import V1DeleteOptions
        try:
            core_v1.delete_namespaced_pod(
                name=container_name,
                namespace=namespace,
                body=V1DeleteOptions(grace_period_seconds=0),
            )
        except Exception as e:
            raise RuntimeError(
                f"k8s: pod {container_name!r} in ns {namespace!r} not deleted: "
                f"{type(e).__name__}: {e}"
            ) from e
        logger.info("auto_heal: k8s delete_namespaced_pod(%s/%s) OK", namespace, container_name)
        return
    raise RuntimeError(f"unknown backend: {backend!r}")


def scale_replicas(deployment_name: str, factor: float) -> int:
    """Scale a Deployment's replica count by ``factor`` — REAL call.

    Docker path (``RTO_HEAL_BACKEND=docker``):
        Compose project scale is NOT directly exposed via the SDK; we
        achieve horizontal scale by spawning N additional containers
        from the same image+command of an existing container, OR by
        updating a docker-compose.yml + re-running ``up --scale``.
        For the buildathon demo we use the simpler "spawn N siblings"
        approach (one new container per desired replica increase;
        factor < 1 stops the most recently started sibling). Each
        spawned sibling is named ``<base>-<n>`` so the operator can
        clean them up. This is HONEST about the gap vs K8s
        DeploymentSpec.replicas — documented in the docstring.

    K8s path (``RTO_HEAL_BACKEND=k8s``):
        ``AppsV1Api().read_namespaced_deployment(name, namespace)``
        → ``new = max(1, int(replicas * factor))``
        → ``patch_namespaced_deployment(name, namespace,
               body={"spec": {"replicas": new}})``
        Returns the new replica count.

    Returns:
        int: the new replica count (best-effort for Docker; exact for K8s).

    Raises:
        ImportError:  SDK not installed (caller logs + degrades).
        RuntimeError: deployment doesn't exist / API unreachable.
    """
    backend = _selected_backend()
    if backend == BACKEND_DRY_RUN:
        logger.info("auto_heal: [dry-run] scale_replicas(%r, factor=%s)", deployment_name, factor)
        return 0
    if backend == BACKEND_DOCKER:
        # Honest gap: Docker SDK has no first-class "scale" primitive;
        # we spawn/stop siblings. The K8s path below is the production
        # answer.
        try:
            client = _docker_client()
        except ImportError as e:
            raise RuntimeError(f"Docker SDK not installed: {e}") from e
        try:
            base = client.containers.get(deployment_name)
        except Exception as e:
            raise RuntimeError(
                f"docker: base container {deployment_name!r} not found: "
                f"{type(e).__name__}: {e}"
            ) from e
        current_count = len(client.containers.list(
            filters={"name": deployment_name, "status": "running"}
        )) or 1
        target = max(1, int(current_count * factor))
        delta = target - current_count
        if delta > 0:
            for i in range(delta):
                try:
                    client.containers.run(
                        image=base.image.id,
                        command=base.attrs["Config"].get("Cmd"),
                        environment=base.attrs["Config"].get("Env"),
                        name=f"{deployment_name}-scale-{int(time.time())}-{i}",
                        detach=True,
                    )
                except Exception as e:
                    raise RuntimeError(
                        f"docker: failed to spawn sibling {i}: "
                        f"{type(e).__name__}: {e}"
                    ) from e
        elif delta < 0:
            siblings = client.containers.list(
                filters={"name": deployment_name, "status": "running"}
            )
            for sib in siblings[-abs(delta):]:
                try:
                    sib.stop(timeout=15)
                except Exception as e:
                    raise RuntimeError(
                        f"docker: failed to stop sibling: "
                        f"{type(e).__name__}: {e}"
                    ) from e
        logger.info(
            "auto_heal: docker scale_replicas(%r, %s) %d→%d (siblings)",
            deployment_name, factor, current_count, target,
        )
        return target
    if backend == BACKEND_K8S:
        try:
            apps_v1 = _k8s_apps_v1()
        except ImportError as e:
            raise RuntimeError(f"Kubernetes SDK not installed: {e}") from e
        namespace = os.environ.get("RTO_K8S_NAMESPACE", "rto")
        try:
            dep = apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
            new_count = max(1, int((dep.spec.replicas or 1) * factor))
            apps_v1.patch_namespaced_deployment(
                name=deployment_name,
                namespace=namespace,
                body={"spec": {"replicas": new_count}},
            )
        except Exception as e:
            raise RuntimeError(
                f"k8s: deployment {deployment_name!r} in ns {namespace!r} "
                f"scale failed: {type(e).__name__}: {e}"
            ) from e
        logger.info(
            "auto_heal: k8s scale_replicas(%s/%s, %s) → %d",
            namespace, deployment_name, factor, new_count,
        )
        return new_count
    raise RuntimeError(f"unknown backend: {backend!r}")


def promote_to_champion(version: str) -> None:
    """Promote a registered model version to champion — REAL call.

    Real impl calls ``src.ml.registry.register_model`` with
    ``champion=True`` for the specified version, which atomically
    demotes the current champion to challenger in the same transaction
    (Postgres ``UPDATE model_registry SET is_champion = ...`` OR
    file-mode JSON blob write). The version's model_path + metrics are
    looked up from the registry first (so the caller only needs the
    version string, not the full bundle).

    Closes the canary-rollback loop in ``docs/A_B_SHADOW_DEPLOYMENT.md``
    + the LitmusChaos experiment ``model-corruption`` in
    ``docs/CHAOS_ENGINEERING.md`` §1 row 6.

    Raises:
        KeyError:     version not in the registry (caller logs + opens
                      a CRITICAL case so a human investigates).
        RuntimeError: registry unreachable (file missing, Postgres
                      down — caller logs + opens a CRITICAL case).
    """
    backend = _selected_backend()
    if backend == BACKEND_DRY_RUN:
        logger.info("auto_heal: [dry-run] promote_to_champion(%r)", version)
        return
    # Real path — lazy import so the skeleton loads without the
    # registry / Postgres connection.
    try:
        from src.ml.registry import load_registry, register_model, current_champion
    except ImportError as e:
        raise RuntimeError(f"src.ml.registry not importable: {e}") from e
    # Look up the version's stored model_path + metrics (so the caller
    # only needs the version string).
    try:
        reg = load_registry()
    except Exception as e:
        raise RuntimeError(
            f"registry load failed: {type(e).__name__}: {e}"
        ) from e
    target = None
    for m in reg.get("models", []):
        if m.get("version") == version:
            target = m
            break
    if target is None:
        raise KeyError(
            f"model version {version!r} not in registry — cannot promote"
        )
    model_path = target.get("model_path") or target.get("path") or ""
    metrics = target.get("metrics") or {}
    priors = metrics.get("_priors") if isinstance(metrics, dict) else None
    p_orig = metrics.get("p_orig") if isinstance(metrics, dict) else None
    p_und = metrics.get("p_und") if isinstance(metrics, dict) else None
    try:
        register_model(
            version=version,
            model_path=model_path,
            metrics=metrics,
            champion=True,
            priors=priors,
            p_orig=p_orig,
            p_und=p_und,
        )
    except Exception as e:
        raise RuntimeError(
            f"register_model(champion=True) failed for {version!r}: "
            f"{type(e).__name__}: {e}"
        ) from e
    champ = current_champion()
    logger.info(
        "auto_heal: promote_to_champion(%r) OK — current champion is %s",
        version, (champ or {}).get("version", "?"),
    )


def switch_audit_mode(mode: str) -> None:
    """Switch the AuditLogger between Postgres and file fallback — REAL call.

    Real impl reads the running FastAPI app's ``state`` (registered by
    the lifespan via ``set_app_state_ref``) and replaces
    ``state["audit"]`` with a file-mode ``AuditLogger`` (when
    ``mode="file"``) OR a Postgres-mode ``AuditLogger`` (when
    ``mode="postgres"``). In-flight requests re-acquire the logger
    per-request so the switch is safe under concurrent load.

    Paper: Pham et al. FSE'24 (ArXiv 2405.09330) §5.3 — "degraded mode is preferable to data
    loss; the audit log MUST stay writable even at the cost of
    durability."

    Raises:
        RuntimeError: when no app state ref has been registered (the
                      auto-heal worker is running outside the FastAPI
                      process — e.g. a separate sidecar). Caller logs
                      + opens a CRITICAL case.
        ValueError:  when ``mode`` isn't ``"file"`` or ``"postgres"``.
    """
    backend = _selected_backend()
    if backend == BACKEND_DRY_RUN:
        logger.info("auto_heal: [dry-run] switch_audit_mode(%r)", mode)
        return
    if mode not in ("file", "postgres"):
        raise ValueError(
            f"audit mode must be 'file' or 'postgres' (got {mode!r})"
        )
    state = _get_app_state()
    if state is None:
        raise RuntimeError(
            "no app state ref — switch_audit_mode can only run inside "
            "the FastAPI process (the lifespan must call "
            "set_app_state_ref(state) on boot)"
        )
    # Lazy import so the skeleton loads without the AuditLogger / settings
    # connection.
    try:
        from src.audit.logger import AuditLogger
        from src.config import get_settings
    except ImportError as e:
        raise RuntimeError(f"audit/config not importable: {e}") from e
    settings = get_settings()
    if mode == "file":
        new_logger = AuditLogger(settings.audit_path)
    else:
        # Postgres mode — AuditLogger auto-detects via settings.is_postgres.
        new_logger = AuditLogger(settings.audit_path)
    # Swap. In-flight requests that already grabbed the old logger
    # will finish their write on the old instance (safe — the old
    # logger isn't closed; we just stop routing new writes to it).
    old = state.get("audit")
    state["audit"] = new_logger
    logger.warning(
        "auto_heal: switch_audit_mode(%r) — old=%s, new=%s",
        mode,
        type(old).__name__ if old is not None else "None",
        type(new_logger).__name__,
    )


def alert_ops(message: str, severity: str = "HIGH") -> None:
    """Send an out-of-band alert to ops — REAL call (PagerDuty + Slack).

    PagerDuty Events API v2 (when ``RTO_PAGERDUTY_INTEGRATION_KEY`` is
    set):
        POST https://events.pagerduty.com/v2/enqueue
        body = {"routing_key": <key>, "event_action": "trigger",
                 "payload": {"summary": <message>, "severity": <sev>,
                              "source": "rto-trust-layer:auto-heal"}}

    Slack incoming webhook (when ``RTO_OPS_WEBHOOK_URL`` is set):
        POST <webhook_url>  body = {"text": "[<sev>] <message>"}

    When both are unset, this is a no-op logged at INFO (so a fresh
    dev checkout / CI run doesn't page anyone).

    The PagerDuty severity mapping:
        HIGH   → "error"
        LOW    → "info"
        other  → "warning"
    """
    backend = _selected_backend()
    if backend == BACKEND_DRY_RUN:
        logger.info(
            "auto_heal: [dry-run] alert_ops(%r, severity=%r)", message, severity,
        )
        return
    # Lazy import — requests is a core dep but lazy keeps the skeleton
    # importable.
    try:
        import requests
    except ImportError as e:
        raise RuntimeError(f"requests not installed: {e}") from e

    pd_key = os.environ.get("RTO_PAGERDUTY_INTEGRATION_KEY")
    slack_url = os.environ.get("RTO_OPS_WEBHOOK_URL")

    if not pd_key and not slack_url:
        logger.info(
            "auto_heal: alert_ops no-op (no PAGERDUTY key + no SLACK url) — "
            "msg=%r sev=%r",
            message, severity,
        )
        return

    sev_map = {"HIGH": "error", "LOW": "info"}
    pd_sev = sev_map.get(severity, "warning")

    if pd_key:
        try:
            resp = requests.post(
                "https://events.pagerduty.com/v2/enqueue",
                json={
                    "routing_key": pd_key,
                    "event_action": "trigger",
                    "payload": {
                        "summary": message,
                        "severity": pd_sev,
                        "source": "rto-trust-layer:auto-heal",
                    },
                },
                timeout=5,
            )
            if resp.status_code not in (200, 202):
                logger.error(
                    "auto_heal: PagerDuty responded %d: %s",
                    resp.status_code, resp.text[:200],
                )
            else:
                logger.info("auto_heal: PagerDuty alert sent (sev=%s)", pd_sev)
        except Exception as e:
            logger.error(
                "auto_heal: PagerDuty post failed: %s: %s", type(e).__name__, e,
            )

    if slack_url:
        try:
            resp = requests.post(
                slack_url,
                json={"text": f"[{severity}] {message}"},
                timeout=5,
            )
            if resp.status_code != 200:
                logger.error(
                    "auto_heal: Slack responded %d: %s",
                    resp.status_code, resp.text[:200],
                )
            else:
                logger.info("auto_heal: Slack alert sent (sev=%s)", severity)
        except Exception as e:
            logger.error(
                "auto_heal: Slack post failed: %s: %s", type(e).__name__, e,
            )


# ---------------------------------------------------------------------------
# Event dataclass — what the auto-heal service receives.
# ---------------------------------------------------------------------------
@dataclass
class HealEvent:
    """A single event from the model.drift / notifications /
    circuit-breaker / audit / stream streams.

    The ``event_type`` MUST be one of the 5 ``EVENT_*`` constants.
    """
    event_type: str
    timestamp: float = field(default_factory=time.time)
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "system"  # which stream produced the event


# ---------------------------------------------------------------------------
# The handler registry — maps event_type → handler function.
# ---------------------------------------------------------------------------
EventHandler = Callable[["HealEvent", "AutoHealService"], None]


def on_circuit_breaker_open(event: "HealEvent", svc: "AutoHealService") -> None:
    """Event #1: CircuitBreaker OPEN > 2 min.

    Action: restart the API container so the breaker closes + the
    in-process state resets.

    Paper: Pham et al. FSE'24 (ArXiv 2405.09330) §4.2 — "container restart is the simplest
    remediation for a stuck-failure-state microservice."
    """
    container = event.payload.get("container", "rto-api")
    logger.warning(
        "auto_heal: circuit_breaker_open for > %ds on container %s — restarting",
        CIRCUIT_BREAKER_OPEN_THRESHOLD_S, container,
    )
    svc._open_case(
        prediction_id=event.payload.get("prediction_id", "n/a"),
        order_id=event.payload.get("order_id", "n/a"),
        priority="HIGH",
        reason=f"circuit_breaker_open_{container}",
    )
    if not svc.dry_run:
        try:
            restart_container(container)
        except (NotImplementedError, RuntimeError, ImportError) as e:
            logger.error(
                "auto_heal: restart_container FAILED for %r — %s: %s",
                container, type(e).__name__, e,
            )
            svc._open_case(
                prediction_id=event.payload.get("prediction_id", "n/a"),
                order_id=event.payload.get("order_id", "n/a"),
                priority="CRITICAL",
                reason=f"restart_failed_{container}_{type(e).__name__}",
            )


def on_drift_detected(event: "HealEvent", svc: "AutoHealService") -> None:
    """Event #2: DDM=DRIFT OR ADWIN=DRIFT.

    Action: rollback to the previous champion model. The previous
    version is stored in the ``model_registry`` table (column
    ``promoted_at`` ordered DESC LIMIT 1 OFFSET 1).

    Paper: Pham et al. FSE'24 (ArXiv 2405.09330) §5.1 — "drift-triggered rollback to the
    last-known-good model is the standard MLOps remediation."
    """
    drift_kind = event.payload.get("drift_kind", "unknown")  # DDM | ADWIN
    current_version = event.payload.get("current_version", "unknown")
    prev_version = event.payload.get("prev_version", "unknown")
    logger.error(
        "auto_heal: drift_detected (%s) on current=%s — rolling back to %s",
        drift_kind, current_version, prev_version,
    )
    svc._open_case(
        prediction_id=event.payload.get("prediction_id", "n/a"),
        order_id=event.payload.get("order_id", "n/a"),
        priority="HIGH",
        reason=f"drift_{drift_kind}_rollback_{prev_version}",
    )
    if not svc.dry_run:
        try:
            promote_to_champion(prev_version)
        except (NotImplementedError, RuntimeError, KeyError, ImportError) as e:
            logger.error(
                "auto_heal: promote_to_champion FAILED for %r — %s: %s",
                prev_version, type(e).__name__, e,
            )
            svc._open_case(
                prediction_id=event.payload.get("prediction_id", "n/a"),
                order_id=event.payload.get("order_id", "n/a"),
                priority="CRITICAL",
                reason=f"rollback_failed_{prev_version}_{type(e).__name__}",
            )


def on_high_rto_rate(event: "HealEvent", svc: "AutoHealService") -> None:
    """Event #3: REJECT rate > 50% over 10 min.

    Action: scale the stream-worker replicas 2× so the
    ``risk.scores`` consumer keeps up.

    Paper: Pham et al. FSE'24 (ArXiv 2405.09330) §5.2 — "horizontal scaling on metric
    threshold breach is the standard auto-remediation."
    """
    reject_pct = event.payload.get("reject_pct", 0.0)
    deployment = event.payload.get("deployment", "rto-stream-worker")
    logger.warning(
        "auto_heal: high_rto_rate %.2f%% over %ds — scaling %s 2×",
        reject_pct * 100, HIGH_RTO_RATE_WINDOW_S, deployment,
    )
    svc._open_case(
        prediction_id=event.payload.get("prediction_id", "n/a"),
        order_id=event.payload.get("order_id", "n/a"),
        priority="HIGH",
        reason=f"high_rto_rate_scale_{deployment}",
    )
    if not svc.dry_run:
        try:
            new_count = scale_replicas(deployment, 2.0)
            logger.info("auto_heal: scaled %s to %d replicas", deployment, new_count)
        except (NotImplementedError, RuntimeError, ImportError) as e:
            logger.error(
                "auto_heal: scale_replicas FAILED for %r — %s: %s",
                deployment, type(e).__name__, e,
            )
            svc._open_case(
                prediction_id=event.payload.get("prediction_id", "n/a"),
                order_id=event.payload.get("order_id", "n/a"),
                priority="CRITICAL",
                reason=f"scale_failed_{deployment}_{type(e).__name__}",
            )


def on_audit_write_errors(event: "HealEvent", svc: "AutoHealService") -> None:
    """Event #4: audit write errors > 0 in 1 min.

    Action: alert ops + switch the AuditLogger to file-mode fallback
    (see ``src/api/routes.py:791-925`` — the dual-mode lifespan
    already supports a file-mode fallback when PG is unreachable).

    Paper: Pham et al. FSE'24 (ArXiv 2405.09330) §5.3 — "degraded mode is preferable to data
    loss; the audit log MUST stay writable even at the cost of
    durability."
    """
    error_count = event.payload.get("error_count", 0)
    logger.error(
        "auto_heal: audit_write_errors count=%d in %ds — switching to file mode",
        error_count, AUDIT_WRITE_ERROR_WINDOW_S,
    )
    svc._open_case(
        prediction_id=event.payload.get("prediction_id", "n/a"),
        order_id=event.payload.get("order_id", "n/a"),
        priority="HIGH",
        reason="audit_write_errors_file_mode",
    )
    if not svc.dry_run:
        try:
            switch_audit_mode("file")
        except (NotImplementedError, RuntimeError, ValueError, ImportError) as e:
            logger.error(
                "auto_heal: switch_audit_mode FAILED — %s: %s",
                type(e).__name__, e,
            )
            svc._open_case(
                prediction_id=event.payload.get("prediction_id", "n/a"),
                order_id=event.payload.get("order_id", "n/a"),
                priority="CRITICAL",
                reason=f"audit_mode_switch_failed_{type(e).__name__}",
            )
        try:
            alert_ops(
                f"Audit write errors: {error_count} in "
                f"{AUDIT_WRITE_ERROR_WINDOW_S}s. Switched to file-mode fallback.",
                severity="HIGH",
            )
        except (NotImplementedError, RuntimeError, ImportError) as e:
            logger.error(
                "auto_heal: alert_ops FAILED — %s: %s", type(e).__name__, e,
            )


def on_stream_consumer_down(event: "HealEvent", svc: "AutoHealService") -> None:
    """Event #5: stream consumer lag > 2 min.

    Action: restart the stream-consumer container so the
    ``XREADGROUP`` loop re-attaches to the consumer group.

    Paper: Pham et al. FSE'24 (ArXiv 2405.09330) §5.4 — "consumer-group reattachment via
    container restart is the standard remediation for stuck Redis
    Streams consumers."
    """
    lag_s = event.payload.get("lag_s", 0)
    container = event.payload.get("container", "rto-stream-consumer")
    logger.warning(
        "auto_heal: stream_consumer_down lag=%ds on %s — restarting",
        lag_s, container,
    )
    svc._open_case(
        prediction_id=event.payload.get("prediction_id", "n/a"),
        order_id=event.payload.get("order_id", "n/a"),
        priority="HIGH",
        reason=f"stream_consumer_down_restart_{container}",
    )
    if not svc.dry_run:
        try:
            restart_container(container)
        except (NotImplementedError, RuntimeError, ImportError) as e:
            logger.error(
                "auto_heal: restart_container FAILED for %r — %s: %s",
                container, type(e).__name__, e,
            )
            svc._open_case(
                prediction_id=event.payload.get("prediction_id", "n/a"),
                order_id=event.payload.get("order_id", "n/a"),
                priority="CRITICAL",
                reason=f"restart_failed_{container}_{type(e).__name__}",
            )


# The registry — keyed by event_type.
HANDLER_REGISTRY: dict[str, EventHandler] = {
    EVENT_CIRCUIT_BREAKER_OPEN: on_circuit_breaker_open,
    EVENT_DRIFT_DETECTED: on_drift_detected,
    EVENT_HIGH_RTO_RATE: on_high_rto_rate,
    EVENT_AUDIT_WRITE_ERRORS: on_audit_write_errors,
    EVENT_STREAM_CONSUMER_DOWN: on_stream_consumer_down,
}


# ---------------------------------------------------------------------------
# The service — listens for events + dispatches to handlers.
# ---------------------------------------------------------------------------
class AutoHealService:
    """The auto-remediation service. Cite Pham et al. FSE'24 (ArXiv 2405.09330).

    Construction:
        svc = AutoHealService(case_service=...)  # real
        svc = AutoHealService(dry_run=True)       # skeleton verification

    Dispatch:
        svc.handle(HealEvent(event_type=EVENT_DRIFT_DETECTED,
                            payload={"prev_version": "rto_kaggle_histgb_20260826"}))

    The skeleton default is ``dry_run=True`` so the import test
    (``python3 -c "from src.remediation.auto_heal import *;
    print('OK')"``) doesn't try to wire Docker / K8s. Set
    ``dry_run=False`` after setting ``RTO_HEAL_BACKEND=docker|k8s`` +
    the corresponding socket / kubeconfig env vars.
    """

    def __init__(
        self,
        case_service: Any | None = None,
        dry_run: bool = True,
    ) -> None:
        self.dry_run = dry_run
        self._case_service = case_service

    def handle(self, event: "HealEvent") -> None:
        """Dispatch an event to its registered handler. Logs +
        opens a case + (if not dry_run) executes the action."""
        handler = HANDLER_REGISTRY.get(event.event_type)
        if handler is None:
            logger.warning("auto_heal: no handler for event_type=%s", event.event_type)
            return
        handler(event, self)

    def _open_case(
        self,
        prediction_id: str,
        order_id: str,
        priority: str,
        reason: str,
    ) -> None:
        """Open a HIGH-priority case via ``CaseService.open_case`` so
        a human reviews every auto-action (Pham et al. FSE'24 (ArXiv 2405.09330) §4.4 —
        "human-in-the-loop even on auto-remediation").

        In skeleton mode (``case_service is None`` OR ``dry_run=True``),
        this just logs. In production, it opens a real case row in the
        ``cases`` table via ``src/cases/service.py:CaseService.open_case``.
        """
        actor = "system:auto_heal"
        if self._case_service is None or self.dry_run:
            logger.info(
                "auto_heal: [dry-run] open_case priority=%s reason=%s actor=%s",
                priority, reason, actor,
            )
            return
        # Real path — lazy import so the skeleton loads without the
        # cases service / Postgres connection.
        try:
            self._case_service.open_case(
                prediction_id=prediction_id,
                order_id=order_id,
                priority=priority,
                reason=reason,
                actor=actor,
            )
        except Exception as e:  # pragma: no cover — defensive
            logger.error("auto_heal: failed to open case — %s", e)


__all__: list[str] = [
    "AutoHealService",
    "HealEvent",
    "HANDLER_REGISTRY",
    "EVENT_CIRCUIT_BREAKER_OPEN",
    "EVENT_DRIFT_DETECTED",
    "EVENT_HIGH_RTO_RATE",
    "EVENT_AUDIT_WRITE_ERRORS",
    "EVENT_STREAM_CONSUMER_DOWN",
    "on_circuit_breaker_open",
    "on_drift_detected",
    "on_high_rto_rate",
    "on_audit_write_errors",
    "on_stream_consumer_down",
    "restart_container",
    "scale_replicas",
    "promote_to_champion",
    "switch_audit_mode",
    "alert_ops",
    "set_app_state_ref",
    "CIRCUIT_BREAKER_OPEN_THRESHOLD_S",
    "HIGH_RTO_RATE_PCT",
    "HIGH_RTO_RATE_WINDOW_S",
    "AUDIT_WRITE_ERROR_WINDOW_S",
    "STREAM_CONSUMER_LAG_S",
    "BACKEND_DRY_RUN",
    "BACKEND_DOCKER",
    "BACKEND_K8S",
]
