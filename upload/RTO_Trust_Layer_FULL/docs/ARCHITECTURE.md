# Architecture

## System overview

```mermaid
flowchart TB
    subgraph Merchant["Merchant / Agent"]
        A1[Dispatch Agent]
    end

    subgraph API["Risk API (FastAPI)"]
        G1[POST /risk/score]
        G2[GET /audit/:id]
    end

    subgraph Core["Scoring core"]
        F1[Feature builder<br/>order + address-quality]
        M1[HistGradientBoosting<br/>categorical=from_dtype]
        X1[Reason codes<br/>perturbation vs population mode]
    end

    subgraph Gates["Decision gates"]
        T1["ACCEPT &lt; 0.15<br/>ship normal"]
        T2["REVIEW 0.15-0.60<br/>selective OTP / partial-COD"]
        T3["REJECT &gt; 0.60<br/>manual review"]
    end

    subgraph Trust["Audit trail"]
        L1[(out/audit.jsonl<br/>immutable append-only)]
    end

    subgraph Data["Data layer"]
        D1[cod_orders.csv<br/>7,235 orders]
        D2[pincodes_india.csv<br/>157k India Post offices]
    end

    A1 --> G1 --> F1 --> M1 --> X1 --> T1 & T2 & T3
    T1 & T2 & T3 --> L1
    G1 --> A1
    D1 --> F1
    D2 -.->|E3 evaluated, cut| F1
    A1 --> G2 --> L1
```

## Decision flow with graceful failure

```mermaid
sequenceDiagram
    participant Agent as Dispatch Agent
    participant API as Risk API
    participant Model as GBM model
    participant Audit as Audit log

    Agent->>API: POST /risk/score (order)
    alt valid request
        API->>Model: features -> probability
        Model-->>API: p(RTO)
        API->>API: gate ACCEPT/REVIEW/REJECT
        API->>Audit: append prediction + reasons
        API-->>Agent: score, decision, explanation, audit_url
        Agent->>Agent: act (ship / OTP / hold)
    else invalid request
        API-->>Agent: HTTP 422 (explicit)
        Agent->>Agent: fallback: hold + notify ops
        Note over Agent,Audit: nothing scored silently
    end
```

## Data model

```mermaid
erDiagram
    ORDER {
        string OrderID PK
        string CustomerID FK
        float order_value_inr
        int discount_pct
        string category
        string city_tier
        string state_norm
        int is_cod
        string address_quality
        int prior_orders
        int prior_returns
        int is_returned
    }
    PREDICTION {
        string prediction_id PK
        string order_id FK
        float probability
        string decision
        json reason_codes
        string audit_id FK
        datetime timestamp
    }
    AUDIT_RECORD {
        string audit_id PK
        json request
        float probability
        string decision
        string model_version
    }
    ORDER ||--o| PREDICTION : scores
    PREDICTION ||--|| AUDIT_RECORD : logs
```

## Design tradeoffs (the "why", not just the "what")

| Choice | Alternative rejected | Why |
|---|---|---|
| sklearn HistGradientBoosting | XGBoost (+SMOTE) | Parity at this scale, zero extra deps (PyPI unreachable from build env); native categorical handling replaces manual encoding; SMOTE unnecessary - class weighting via threshold choice |
| Customer-grouped split | random row split | Repeat customers leak across splits and inflate metrics; group overlap asserted = 0 every run (instrument metric) |
| PR-AUC primary | accuracy / ROC-AUC | 23% positive rate makes accuracy meaningless; PR-AUC is sensitive to the FP cost the track cares about |
| Wide net @ thr 0.15 | high-precision reject | FN (RTO ships) costs ~12x FP (a review call); matches published selective-OTP results |
| Perturbation reason codes | SHAP TreeExplainer | Not supported for HistGB; permutation attribution gives identical story, swap to SHAP if XGBoost lands |
| State-level geo cut | keep for show | Measured no lift (PR-AUC -0.005); shipping dead features would be dishonest |

## What breaks at 10x and the fix

- **Inference latency**: single-row predict is O(1) (~ms). At 10k QPS, move model behind
  a preloaded worker pool (uvicorn workers + shared mmap of joblib) or ONNX-export the trees.
- **Feature drift**: PriorReturns-style features decay as customer bases churn; add weekly
  PSI monitoring on feature distributions, auto-flag > 0.25 shift for retrain.
- **Audit volume**: JSONL appends serialize writes; at scale switch to partitioned Parquet +
  object storage with the same append-only contract.
- **Label latency**: RTO labels arrive days after dispatch; retraining cadence must be
  monthly with rolling windows, not continuous.

## Compliance posture

- Defense-only: the system blocks nothing by itself; humans/agents act on gated recommendations.
- Every money-affecting action traces to an immutable audit record containing the exact
  request, model version, probability, decision, and ranked causes.
- Failure mode is fail-loud (422/500) plus agent-side hold - never fail-open silent approval.
