// Load test k6 dos endpoints "quentes" do KLM Hub — login, inbox/conversas
// (klm/omnichannel/collaboration), dashboard/klm e tracking/summary.
//
// k6 NÃO está instalado neste ambiente de execução do agente (verificado em
// 2026-07-23: `which k6` -> not found). Este script é entregue pronto para o
// proprietário rodar assim que instalar o k6 (`brew install k6`); o resultado
// real medido nesta sessão está em scripts/load/run_hot_endpoints.py e
// documentado em docs/LOAD_TEST_FASE1.md.
//
// Uso:
//   1) Subir o backend local em outra porta livre (nunca a 8010 se o repo
//      principal já estiver rodando lá):
//        .venv/bin/python scripts/run_local.py --host 127.0.0.1 --port 8011
//   2) Semear dados + senha de teste (guardado, só roda em runtime local):
//        .venv/bin/python scripts/seed.py
//        .venv/bin/python scripts/load/set_local_test_password.py
//   3) Rodar:
//        BASE_URL=http://127.0.0.1:8011 \
//        LOGIN_EMAIL=local@klmadvogados.com.br \
//        LOGIN_PASSWORD='LoadTestLocal!2026#klm' \
//        k6 run tests/load/k6_hot_endpoints.js
//
// Meta: p95 < 300ms em cada endpoint quente, com 0% de erro HTTP.

import http from "k6/http";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8011";
const LOGIN_EMAIL = __ENV.LOGIN_EMAIL || "local@klmadvogados.com.br";
const LOGIN_PASSWORD = __ENV.LOGIN_PASSWORD || "LoadTestLocal!2026#klm";

const loginTrend = new Trend("klm_login_duration", true);
const inboxTrend = new Trend("klm_inbox_duration", true);
const dashboardTrend = new Trend("klm_dashboard_duration", true);
const trackingTrend = new Trend("klm_tracking_duration", true);

export const options = {
  scenarios: {
    hot_endpoints: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "20s", target: 10 },
        { duration: "40s", target: 20 },
        { duration: "20s", target: 0 },
      ],
    },
  },
  thresholds: {
    klm_login_duration: ["p(95)<300"],
    klm_inbox_duration: ["p(95)<300"],
    klm_dashboard_duration: ["p(95)<300"],
    klm_tracking_duration: ["p(95)<300"],
    http_req_failed: ["rate<0.01"],
  },
};

export default function () {
  const loginRes = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({ email: LOGIN_EMAIL, password: LOGIN_PASSWORD }),
    { headers: { "Content-Type": "application/json" }, tags: { name: "login" } },
  );
  loginTrend.add(loginRes.timings.duration);
  check(loginRes, { "login 200": (r) => r.status === 200 });

  let token = "";
  if (loginRes.status === 200) {
    token = loginRes.json("accessToken");
  }
  const authHeaders = { headers: { Authorization: `Bearer ${token}` } };

  const inboxRes = http.get(
    `${BASE_URL}/api/v1/klm/omnichannel/events?limit=50`,
    { ...authHeaders, tags: { name: "inbox" } },
  );
  inboxTrend.add(inboxRes.timings.duration);
  check(inboxRes, { "inbox not-5xx": (r) => r.status < 500 });

  const dashboardRes = http.get(`${BASE_URL}/api/v1/klm/dashboard`, {
    ...authHeaders,
    tags: { name: "dashboard" },
  });
  dashboardTrend.add(dashboardRes.timings.duration);
  check(dashboardRes, { "dashboard 200": (r) => r.status === 200 });

  const trackingRes = http.get(`${BASE_URL}/api/v1/klm/tracking/summary`, {
    ...authHeaders,
    tags: { name: "tracking" },
  });
  trackingTrend.add(trackingRes.timings.duration);
  check(trackingRes, { "tracking 200": (r) => r.status === 200 });

  sleep(0.2);
}
