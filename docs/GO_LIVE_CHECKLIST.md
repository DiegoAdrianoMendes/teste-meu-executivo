# Checklist de Go-Live — Fase 1

> Checklist único e literal para o **proprietário** marcar antes de abrir cada
> gate externo (registro de domínio real em produção, primeiro cliente pagante,
> exposição pública do backend em AWS). Reúne os gates já produzidos pelos
> sprints anteriores — não substitui nenhum documento, só consolida.
> Nenhum item aqui foi marcado por um agente: é o proprietário quem decide
> que a evidência é suficiente e assina.

## Como usar

Cada item tem: **o que verificar**, **onde está a evidência**, e uma caixa
`[ ]` para o proprietário marcar manualmente. Não desmarque o item de volta
"porque parece que está ok" — reabra a evidência e confira de novo.

---

## 1. Suítes de teste verdes

- [ ] Suíte completa de testes (`pytest`) verde, zero skip — evidência em
      `docs/QA_TOTAL_2026-07-17.md` e rodadas de continuação mais recentes
      (`docs/QA_LOCAL_CONTINUACAO_2026-07-23.md`, `docs/QA_OMNICHANNEL_V7_2026-07-21.md`,
      `docs/QA_SAAS_LOCAL_V5_2026-07-20.md`, `docs/QA_OSS_NATIVE_2026-07-18.md`).
- [ ] Suíte PostgreSQL com RLS habilitado verde
      (`scripts/verify_postgresql_qa.sh --all`), zero skip — evidência em
      `docs/POSTGRESQL_RLS.md` e nas rodadas QA acima.
- [ ] `scripts/verify_ruff_debt.py` e `scripts/verify_junit_clean.py` sem
      pendência aberta.
- [ ] Nenhum teste com `xfail`/`skip` não justificado introduzido desde a
      última auditoria (`docs/AUDIT_REMEDIATION_MATRIX.md`).

## 2. Load test

- [ ] Load test dos 4 endpoints quentes (login, inbox, dashboard, tracking)
      rodado e resultado documentado — `docs/LOAD_TEST_FASE1.md` (rodado
      nesta sprint, 2026-07-23, contra SQLite local).
- [ ] Meta de p95 < 300ms confirmada nos GETs quentes (inbox/dashboard/
      tracking) — **confirmado localmente até concorrência 10**; login usa
      meta separada (ver `docs/LOAD_TEST_FASE1.md` § Leitura do resultado —
      bcrypt + piso de temporização uniforme são desenho de segurança
      intencional, não falha de performance).
- [ ] **Pendência a fechar antes do go-live:** rodar o mesmo load test contra
      a stack `docker-compose.prod.yml` com Postgres real (não rodado nesta
      sessão — não há Postgres de produção local disponível). Se possível,
      também rodar `tests/load/k6_hot_endpoints.js` com k6 instalado para
      segunda fonte de verdade.

## 3. Drill de restore de backup

- [ ] Drill de dump→restore→contagem de linhas rodado com resultado `OK` —
      `docs/BACKUP_RESTORE.md` § 2 (rodado nesta sprint, 2026-07-23, Postgres
      efêmero via `scripts/backup_restore_drill.sh`, 5 tabelas-chave
      validadas linha a linha).
- [ ] Backup automático do RDS habilitado (retenção ≥ 7 dias) — configurar no
      passo 3.5 de `docs/RUNBOOK_AWS_FASE1.md` no momento da criação do RDS
      (**ainda não criado** — Fase 1 não subiu para AWS).
- [ ] Procedimento de restore de emergência revisado pelo proprietário —
      `docs/BACKUP_RESTORE.md` § 3 (comando `aws rds restore-db-instance-*`
      literal, pronto para colar).
- [ ] RTO/RPO estimados (~30-60 min / ~24h) aceitos como adequados para a
      Fase 1, ou decisão explícita de subir para Multi-AZ antes do go-live
      se o RPO de 24h for inaceitável para o volume de clientes esperado.

## 4. Backup configurado

- [ ] RDS criado com backup automático habilitado desde o primeiro dia
      (não é algo para configurar depois — ver `docs/RUNBOOK_AWS_FASE1.md` §3.5).
- [ ] Snapshot manual tirado antes de qualquer migração destrutiva em
      produção (regra permanente, `docs/PLANO_META_MAXIMO_2026-07-23.md` L239).

## 5. Alertas / observabilidade

- [ ] Uptime check externo do `/health` configurado (UptimeRobot ou Better
      Uptime, free tier) — `docs/OBSERVABILIDADE_FASE1.md` § 2.
- [ ] Rotina de checagem do error tracking interno definida (manual na
      primeira semana, depois 2-3x/semana) — `docs/OBSERVABILIDADE_FASE1.md` § 3.
- [ ] Rotina de checagem de custo de IA por workspace definida (semanal) —
      `docs/OBSERVABILIDADE_FASE1.md` § 4.
- [ ] AWS Budget com alerta por e-mail configurado no Billing Console —
      `docs/OBSERVABILIDADE_FASE1.md` § 4 (teto sugerido: acima de
      R$ 450-500/mês estimado, ex. R$ 600).

## 6. Runbook

- [ ] `docs/RUNBOOK_AWS_FASE1.md` revisado passo a passo pelo proprietário
      (rede, EC2, RDS, S3/CloudFront, Route 53, ACM, deploy) — nenhum passo
      executado por agente, é 100% manual por desenho.
- [ ] Credenciais de produção seguem `docs/ENV_PRODUCAO_TEMPLATE.md`, geradas
      na hora, nunca reaproveitadas de ambiente local/dev.
- [ ] IAM de deploy configurado sem usuário root, MFA habilitado
      (`docs/RUNBOOK_AWS_FASE1.md` § Pré-requisitos).

## 7. Dossiê tech provider

- [ ] `docs/DOSSIE_TECH_PROVIDER.md` atualizado e revisado — dossiê técnico
      usado para due diligence de fornecedores/parceiros/investidores.

## 8. Sign-off final

- [ ] Todos os itens acima marcados.
- [ ] Proprietário decidiu explicitamente sobre as duas pendências abertas
      (load test contra Postgres real; RPO de 24h aceitável ou não).
- [ ] Data e assinatura do proprietário: ______________________ / ____/____/____
