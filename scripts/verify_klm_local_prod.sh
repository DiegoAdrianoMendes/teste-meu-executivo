#!/usr/bin/env bash
# scripts/verify_klm_local_prod.sh
#
# Smoke test HTTP externo pós-subida da Fase 1 (docs/RUNBOOK_AWS_FASE1.md seção 9).
# Ao contrário de scripts/verify_klm_local.py / scripts/verify_local_stack_live.py
# (que assumem localhost e acesso à rede interna), este script só faz chamadas HTTP
# de fora para dentro, como um usuário real faria. Não requer credenciais AWS nem
# acesso à VPC.
#
# Uso:
#   scripts/verify_klm_local_prod.sh https://api.desburocratize.com https://desburocratize.com
#
# Saída: exit 0 se tudo saudável; exit 1 no primeiro erro, com a checagem que falhou.

set -euo pipefail

API_URL="${1:-}"
FRONTEND_URL="${2:-}"

if [[ -z "$API_URL" || -z "$FRONTEND_URL" ]]; then
  echo "Uso: $0 <API_URL> <FRONTEND_URL>" >&2
  echo "Exemplo: $0 https://api.desburocratize.com https://desburocratize.com" >&2
  exit 2
fi

fail() {
  echo "FALHOU: $1" >&2
  exit 1
}

echo "== Verificação pós-subida Fase 1 =="

echo "-- Backend /health ($API_URL/health)"
status=$(curl -fsS -o /tmp/klm_prod_health.json -w "%{http_code}" "$API_URL/health" || echo "000")
if [[ "$status" != "200" ]]; then
  fail "GET $API_URL/health retornou HTTP $status (esperado 200)"
fi
echo "OK — HTTP 200"
cat /tmp/klm_prod_health.json
echo

echo "-- Frontend ($FRONTEND_URL)"
status=$(curl -fsS -o /dev/null -w "%{http_code}" "$FRONTEND_URL" || echo "000")
if [[ "$status" != "200" ]]; then
  fail "GET $FRONTEND_URL retornou HTTP $status (esperado 200)"
fi
echo "OK — HTTP 200"

echo "-- TLS válido em ambos os domínios"
for url in "$API_URL" "$FRONTEND_URL"; do
  host=$(echo "$url" | sed -E 's#^https?://##; s#/.*$##')
  if ! echo | openssl s_client -servername "$host" -connect "$host:443" 2>/dev/null | openssl x509 -noout -checkend 86400 >/dev/null 2>&1; then
    fail "Certificado TLS de $host inválido, expirando em <24h, ou host inacessível na porta 443"
  fi
  echo "OK — TLS de $host válido"
done

echo "== Tudo saudável =="
