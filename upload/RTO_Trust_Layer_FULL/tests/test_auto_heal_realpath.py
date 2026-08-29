"""Tests for src/remediation/auto_heal.py REAL code paths.

These tests prove the "real" Docker / K8s code paths actually invoke the
SDK methods (docker.from_env().containers.get().restart(), kubernetes
CoreV1Api().delete_namespaced_pod(), AppsV1Api().patch_namespaced_deployment())
— not just log "would have called". The SDKs are mocked via unittest.mock
so the tests run without a real Docker socket or K8s cluster.

Citation note (mirrors the module docstring): the original module cited
"IEEE ICSE 2025, Self-Healing Microservices" — that paper could not be
verified in IEEE Xplore/DBLP. Corrected to Pham et al., BARO, FSE'24
(ArXiv 2405.09330, Best Artifact Award). The §4/§5.x references in
auto_heal.py are conceptual anchors, not paper sections.

Anti-hallucination: these tests exist specifically to refute the claim
"the auto-heal code is wired" by PROVING the SDK methods are called.
Without these tests, the "real" code path is just a docstring claim.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def restore_env():
    """Save + restore RTO_HEAL_BACKEND so tests don't pollute env."""
    old_backend = os.environ.get("RTO_HEAL_BACKEND")
    old_ns = os.environ.get("RTO_K8S_NAMESPACE")
    yield
    if old_backend is None:
        os.environ.pop("RTO_HEAL_BACKEND", None)
    else:
        os.environ["RTO_HEAL_BACKEND"] = old_backend
    if old_ns is None:
        os.environ.pop("RTO_K8S_NAMESPACE", None)
    else:
        os.environ["RTO_K8S_NAMESPACE"] = old_ns


def test_restart_container_dry_run_logs_and_returns(restore_env):
    """Default backend (dry_run) logs + returns without calling any SDK."""
    os.environ["RTO_HEAL_BACKEND"] = "dry_run"
    from src.remediation.auto_heal import restart_container

    # Should not raise — dry_run is a pure log + return.
    restart_container("api-server")


def test_restart_container_docker_realpath_calls_restart(restore_env):
    """Docker backend: verify client.containers.get(name).restart(timeout=30)
    is actually called — not just logged."""
    os.environ["RTO_HEAL_BACKEND"] = "docker"
    mock_container = MagicMock()
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    with patch("docker.from_env", return_value=mock_client):
        from src.remediation.auto_heal import restart_container

        restart_container("api-server")
    mock_client.containers.get.assert_called_once_with("api-server")
    mock_container.restart.assert_called_once_with(timeout=30)


def test_restart_container_k8s_realpath_calls_delete_pod(restore_env):
    """K8s backend: verify CoreV1Api().delete_namespaced_pod is called
    with the correct name + namespace + grace_period_seconds=0."""
    os.environ["RTO_HEAL_BACKEND"] = "k8s"
    os.environ["RTO_K8S_NAMESPACE"] = "rto"
    mock_core_v1 = MagicMock()
    with patch("kubernetes.config.load_incluster_config"), \
         patch("kubernetes.config.load_kube_config"), \
         patch("kubernetes.client.CoreV1Api", return_value=mock_core_v1):
        from src.remediation.auto_heal import restart_container

        restart_container("api-server-pod")
    mock_core_v1.delete_namespaced_pod.assert_called_once()
    kwargs = mock_core_v1.delete_namespaced_pod.call_args.kwargs
    assert kwargs["name"] == "api-server-pod"
    assert kwargs["namespace"] == "rto"
    # grace_period_seconds=0 means immediate kill — the Deployment
    # controller will spin up a replacement pod (the "restart").
    assert kwargs["body"].grace_period_seconds == 0


def test_scale_replicas_k8s_realpath_patches_replicas(restore_env):
    """K8s backend: verify read_namespaced_deployment + patch with the
    correct new replica count (current * factor)."""
    os.environ["RTO_HEAL_BACKEND"] = "k8s"
    os.environ["RTO_K8S_NAMESPACE"] = "rto"
    mock_deployment = MagicMock()
    mock_deployment.spec.replicas = 3  # current 3 replicas
    mock_apps_v1 = MagicMock()
    mock_apps_v1.read_namespaced_deployment.return_value = mock_deployment
    with patch("kubernetes.config.load_incluster_config"), \
         patch("kubernetes.config.load_kube_config"), \
         patch("kubernetes.client.AppsV1Api", return_value=mock_apps_v1):
        from src.remediation.auto_heal import scale_replicas

        new_count = scale_replicas("stream-worker", factor=2.0)
    assert new_count == 6  # 3 * 2.0
    mock_apps_v1.read_namespaced_deployment.assert_called_once_with(
        name="stream-worker", namespace="rto"
    )
    mock_apps_v1.patch_namespaced_deployment.assert_called_once()
    patch_kwargs = mock_apps_v1.patch_namespaced_deployment.call_args.kwargs
    assert patch_kwargs["name"] == "stream-worker"
    assert patch_kwargs["namespace"] == "rto"
    assert patch_kwargs["body"]["spec"]["replicas"] == 6


def test_restart_container_docker_sdk_missing_raises_runtime_error(restore_env):
    """If Docker SDK import fails (ImportError), the handler wraps it in
    RuntimeError so the caller (the stream processor) can catch + open a
    CRITICAL case instead of crashing the remediation worker."""
    os.environ["RTO_HEAL_BACKEND"] = "docker"
    with patch("docker.from_env", side_effect=ImportError("no docker module")):
        from src.remediation.auto_heal import restart_container

        with pytest.raises(RuntimeError, match="Docker SDK not installed"):
            restart_container("api-server")


def test_restart_container_unknown_backend_falls_back_to_dry_run(restore_env):
    """An unknown backend value does NOT crash the remediation worker —
    it logs a WARNING + falls back to dry_run. This is the safe behavior
    for a remediation service: a config typo must not crash the
    auto-heal loop (which would defeat the purpose of auto-healing).
    The trade-off is the action becomes a no-op until the config is
    fixed, which the WARNING log surfaces."""
    os.environ["RTO_HEAL_BACKEND"] = "bogus-backend"
    from src.remediation.auto_heal import restart_container

    # Must NOT raise — graceful degradation is the contract.
    restart_container("api-server")


def test_sdks_importable_from_requirements():
    """The docker + kubernetes SDKs are installed (per requirements.txt).
    This guards against the 'code calls SDK but SDK not installed' gap
    that the user flagged as a hallucination risk."""
    import docker
    import kubernetes

    assert hasattr(docker, "from_env"), "docker SDK missing from_env"
    assert hasattr(kubernetes, "client"), "kubernetes SDK missing client"
    assert hasattr(kubernetes.client, "CoreV1Api")
    assert hasattr(kubernetes.client, "AppsV1Api")
