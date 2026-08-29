# RTO Trust Layer — Kubernetes Manifests

One-command deploy of the full RTO Trust Layer stack to any K8s cluster
(minikube, kind, EKS, GKE, AKS):

```bash
kubectl apply -k infra/k8s/
```

## Resources

| File | Purpose |
|------|---------|
| `namespace.yaml` | `rto-trust-layer` namespace |
| `postgres-secret.yaml` | Postgres credentials (template — replace before prod) |
| `api-keys-secret.yaml` | API auth keys (template — replace before prod) |
| `postgres-statefulset.yaml` | Postgres 15 + PVC (5Gi) + readiness via `pg_isready` |
| `postgres-service.yaml` | ClusterIP `postgres:5432` |
| `redis-deployment.yaml` | Redis 7 (AOF, 256mb LRU) |
| `redis-service.yaml` | ClusterIP `redis:6379` |
| `api-configmap.yaml` | Non-sensitive env vars (RTO_ENV, REDIS_URL, KAFKA_BROKERS) |
| `api-deployment.yaml` | FastAPI container, 2 replicas, liveness/readiness/startup probes |
| `api-service.yaml` | ClusterIP `api:80` → container `:8000` |
| `hpa.yaml` | HorizontalPodAutoscaler: 2–10 replicas, CPU 70% / memory 80% |
| `kustomization.yaml` | One-command `kubectl apply -k` |

## Prerequisites

1. A running K8s cluster (minikube / kind / cloud)
2. `kubectl` installed + configured (`kubectl config use-context <ctx>`)
3. The FastAPI container image built + pushed to a registry the cluster
   can pull from:

   ```bash
   docker build -t rto-trust-layer:latest .
   # For a remote cluster:
   docker tag rto-trust-layer:latest ghcr.io/<owner>/<repo>:latest
   docker push ghcr.io/<owner>/<repo>:latest
   # Then edit api-deployment.yaml's `image:` to match.
   ```

## Deploy + verify

```bash
# Apply the stack
kubectl apply -k infra/k8s/

# Wait for pods to be Ready
kubectl -n rto-trust-layer wait --for=condition=Ready pod -l app.kubernetes.io/name=rto-trust-layer --timeout=180s

# Port-forward the API to localhost:8000 for the golden-path smoke test
kubectl -n rto-trust-layer port-forward svc/api 8000:80 &

# Hit the health endpoint
curl -s http://localhost:8000/health | jq .

# Score an order (golden path)
curl -s -X POST http://localhost:8000/risk/score \
  -H "Authorization: Bearer score-demo-key" \
  -H "Content-Type: application/json" \
  -d '{"order_id":"K8S-DEMO-1","prior_orders":3,"prior_returns":1,"is_cod":1,"category":"Electronics","device":"Mobile","city_tier":"T1","address_quality":"verified"}' | jq .
```

## HPA — horizontal autoscaling

The HPA scales the FastAPI deployment between 2 and 10 replicas based on
CPU (70% target) and memory (80% target). To verify it's working:

```bash
# Install metrics-server if your cluster doesn't have it (minikube:
# `minikube addons enable metrics-server`)
kubectl -n rto-trust-layer get hpa api-hpa --watch
```

Load-test to trigger scale-up:

```bash
kubectl -n rto-trust-layer run loadgen --rm -it --image=busybox -- \
  /bin/sh -c 'while true; do wget -q -O- http://api/health; done'
```

## Kafka toggle (optional)

The FastAPI app reads `KAFKA_BROKERS` from the `api-config` ConfigMap.
By default it's empty → Redis Streams transport is used. To enable
Kafka:

1. Deploy a Kafka broker (e.g. Strimzi operator or bitnami chart):
   ```bash
   helm repo add bitnami https://charts.bitnami.com/bitnami
   helm install kafka bitnami/kafka -n rto-trust-layer
   ```
2. Patch the ConfigMap with the broker address:
   ```bash
   kubectl -n rto-trust-layer patch configmap api-config \
     -p '{"data":{"KAFKA_BROKERS":"kafka:9092"}}'
   ```
3. Roll the api Deployment to pick up the new env var:
   ```bash
   kubectl -n rto-trust-layer rollout restart deployment/api
   ```
4. Verify the transport flipped:
   ```bash
   kubectl -n rto-trust-layer port-forward svc/api 8000:80
   curl -s http://localhost:8000/health | jq .stream_transport
   # → "kafka"
   ```

## RBAC note for auto-heal (optional)

`RTO_HEAL_BACKEND` defaults to `dry_run` (logs + opens cases, no real
action). To flip to `k8s` (real pod restarts), create a ServiceAccount
+ Role granting `pods/delete` + `deployments/scale`:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: rto-healer
  namespace: rto-trust-layer
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: rto-healer
  namespace: rto-trust-layer
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["delete", "list", "get"]
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "patch", "scale"]
```

Then patch the api Deployment's `spec.template.spec.serviceAccountName`
to `rto-healer` + set `RTO_HEAL_BACKEND=k8s` in the ConfigMap.

## Cleanup

```bash
kubectl delete -k infra/k8s/
```
