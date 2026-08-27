// k6 load profile for the Risk API. Run: k6 run tests/load/risk_api_load.js
// Requires k6 binary (https://k6.io). Thresholds gate CI per Missing-Assets brief.
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE_URL || "http://localhost:8000";
const KEY = __ENV.SCORER_KEY || "score-demo-key";
const PARAMS = {
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${KEY}` },
};

function order(i) {
  return JSON.stringify({
    order_id: `LOAD-${i}`,
    amount_inr: 500 + (i % 20) * 750,
    category: ["Fashion", "Electronics", "Health"][i % 3],
    customer_id: `CUST-${i % 500}`,
    address_quality: ["complete", "partial", "vague"][i % 3],
    city_tier: `tier_${(i % 3) + 1}`,
    payment_method: i % 2 ? "COD" : "Prepaid",
    prior_orders: i % 7,
    prior_returns: i % 3,
  });
}

export const options = {
  scenarios: {
    steady: { executor: "constant-vus", vus: 50, duration: "2m" },
    ramp: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "1m", target: 200 },
        { duration: "1m", target: 200 },
        { duration: "30s", target: 0 },
      ],
      startTime: "2m",
    },
    spike: {
      executor: "ramping-arrival-rate",
      startRate: 10,
      timeUnit: "1s",
      preAllocatedVUs: 100,
      stages: [{ duration: "30s", target: 400 }],
      startTime: "4m",
    },
  },
  thresholds: {
    http_req_duration: ["p(50)<120", "p(95)<250", "p(99)<400"],
    http_req_failed: ["rate<0.01"],
  },
};

export default function () {
  const res = http.post(`${BASE}/risk/score`, order(__ITER), PARAMS);
  check(res, {
    "status is 200": (r) => r.status === 200,
    "has decision": (r) => r.json("decision") !== undefined,
  });
}
