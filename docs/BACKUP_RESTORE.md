# Backup e Restore — política e drill

> Sprint: Operação de produção (load test + drill de restore + observabilidade),
> worktree `klm-wt-ops-prod`. Drill executado localmente contra Postgres
> efêmero em 2026-07-23 — **nenhum comando contra RDS/AWS foi rodado**.

## 1. Política de backup

| Item | Decisão | Origem |
|---|---|---|
| Mecanismo | RDS **backup automático** (snapshot diário gerenciado) | `docs/RUNBOOK_AWS_FASE1.md` §3.5 |
| Frequência | 1x/dia, janela fora do horário comercial de Brasília | `docs/RUNBOOK_AWS_FASE1.md` §3.5 |
| Retenção | Mínimo 7 dias (padrão RDS automated backups) | `docs/RUNBOOK_AWS_FASE1.md` §3.5 |
| Point-in-time recovery | Disponível dentro da janela de retenção (recurso nativo do RDS automated backup, não precisa de configuração extra) | RDS default |
| Snapshot manual pré-migração destrutiva | Obrigatório antes de qualquer migração Alembic que altere/derrube coluna com dado | `docs/PLANO_META_MAXIMO_2026-07-23.md` L239 ("backup antes de qualquer migração destrutiva") |
| Verificação de restaurabilidade | Este drill (`scripts/backup_restore_drill.sh`), rodar antes de cada go-live e periodicamente (recomendação: mensal) contra Postgres efêmero | Este sprint |

### RTO / RPO estimados (Fase 1)

Estes números são **estimativas de engenharia para a Fase 1** (1 instância
RDS `db.t4g.micro`, Multi-AZ desligado, ver `docs/RUNBOOK_AWS_FASE1.md` §3),
não um SLA contratual:

- **RPO (Recovery Point Objective) ≈ 24h.** Com backup automático diário e
  sem Multi-AZ, o pior caso de perda de dado é "desde o último snapshot
  diário" — até ~24h de escrita perdida num desastre total da instância.
  Point-in-time recovery do RDS reduz isso na prática (RDS grava
  transaction logs continuamente dentro da janela de retenção), mas o número
  conservador para efeito de planejamento é 24h.
- **RTO (Recovery Time Objective) ≈ 30-60 minutos.** Tempo estimado para:
  criar uma instância RDS a partir do snapshot/PITR (10-20 min, depende do
  tamanho do banco), apontar `DATABASE_URL`/`DATABASE_URL_SYNC` da instância
  EC2 para o novo endpoint, rodar `alembic upgrade head` se necessário, e
  validar com este drill antes de liberar tráfego. Não inclui tempo de
  decisão/triagem do incidente.
- Para reduzir o RPO significativamente (minutos, não horas) seria necessário
  Multi-AZ ou réplica de leitura com failover — decisão explicitamente
  adiada na Fase 1 por custo (dobra o custo do RDS, ver política de pricing
  em `docs/PRICING_BILLING_POLITICA_2026-07.md` §3.3). Reavaliar quando o
  volume de escritórios justificar.

## 2. Drill de restore — `scripts/backup_restore_drill.sh`

O que o script faz, contra um container Postgres **efêmero e descartável**
(mesmo padrão de `scripts/verify_postgresql_qa.sh`):

1. Sobe `postgres:17-alpine` num container Docker isolado (porta aleatória
   do host, credenciais geradas com `openssl rand`).
2. Roda `alembic upgrade head` até a revisão mais recente.
3. Popula um seed mínimo (usuário, workspace, membership, 5 clientes, 5
   casos) via ORM direto — deliberadamente **não** usa
   `app/services/klm_local_seed_service.py` completo, porque esse bootstrap
   de demonstração tem um bug pré-existente contra Postgres no seed do
   catálogo de medicamentos (`MedicationPricingService.seed_catalog`,
   `NotNullViolationError` em `medication_references.created_at` — o modelo
   depende de `server_default`/trigger que não está presente na migração;
   funciona em SQLite por coincidência de driver, não em Postgres). **Isto é
   uma pendência de correção fora do escopo deste sprint** (não é permitido
   tocar `app/**` nesta worktree) — sinalizado aqui para o dono do backlog.
4. Conta linhas de 5 tabelas-chave (`users`, `workspaces`,
   `workspace_memberships`, `clients`, `matters`) na origem.
5. Roda `pg_dump | gzip` para um arquivo em
   `.local/qa-evidence/backup-restore-drill/`.
6. Cria um **segundo banco vazio** no mesmo servidor Postgres.
7. Restaura o dump nele (`psql < dump`).
8. Compara a contagem de linhas das mesmas 5 tabelas entre origem e restore.
9. Reporta `OK` (contagens idênticas em todas as tabelas) ou `FALHA` (sai
   com código 1) e preserva o dump para inspeção.

Container e bancos são descartados ao final (`docker rm -f`), exceto se
`KLM_KEEP_BACKUP_DRILL=1` for exportado antes de rodar.

### Resultado real (2026-07-23, rodado nesta sessão)

```
==> Contando linhas das tabelas-chave na origem...
    users                        1
    workspaces                   2
    workspace_memberships        1
    clients                      5
    matters                      5
==> Gerando dump (pg_dump | gzip)...
    dump salvo em .local/qa-evidence/backup-restore-drill/dump-47358.sql.gz (80K)
==> Criando banco de destino vazio...
==> Restaurando dump no banco de destino...
==> Comparando contagens origem x restore...
    OK    users                        origem=1      restore=1
    OK    workspaces                   origem=2      restore=2
    OK    workspace_memberships        origem=1      restore=1
    OK    clients                      origem=5      restore=5
    OK    matters                      origem=5      restore=5

RESULTADO: OK — dump, restore e contagem de linhas validados para 5 tabelas-chave.
```

(`workspaces=2` porque uma das migrações do schema insere um workspace de
sistema por padrão — comportamento esperado e idêntico em origem e restore.)

### Como rodar

```bash
scripts/backup_restore_drill.sh
# ou, para manter o container Postgres e inspecionar manualmente:
KLM_KEEP_BACKUP_DRILL=1 scripts/backup_restore_drill.sh
```

Pré-requisitos: Docker rodando localmente, `alembic`/`pytest` no `.venv`
(já presentes no repo).

## 3. Comando de restore de emergência — Fase 1 AWS (RDS)

Este é o procedimento **para o proprietário rodar manualmente** num
incidente real na Fase 1 (EC2 + RDS `db.t4g.micro`, sa-east-1, sem NAT — ver
`docs/RUNBOOK_AWS_FASE1.md`). Nenhum destes comandos foi executado por esta
sessão — é literal, para colar/adaptar na hora.

1. **Identificar o snapshot/ponto de recuperação** no console RDS
   (Snapshots automáticos, ou "Restore to point in time" se dentro da janela
   de retenção).

2. **Restaurar para uma NOVA instância** (o RDS não permite restore
   in-place; sempre cria uma instância nova):
   ```bash
   aws rds restore-db-instance-to-point-in-time \
     --source-db-instance-identifier klm-hub-prod \
     --target-db-instance-identifier klm-hub-prod-restore-$(date +%Y%m%d%H%M) \
     --restore-time "2026-07-23T12:00:00Z" \
     --db-instance-class db.t4g.micro \
     --db-subnet-group-name <subnet-group-fase1> \
     --vpc-security-group-ids <klm-rds-sg-id> \
     --no-publicly-accessible
   ```
   (ou `aws rds restore-db-instance-from-db-snapshot` se for restaurar de um
   snapshot automático específico em vez de PITR.)

3. **Aguardar `available`**:
   ```bash
   aws rds wait db-instance-available \
     --db-instance-identifier klm-hub-prod-restore-<timestamp>
   ```

4. **Validar a instância restaurada com este drill**, antes de apontar
   produção para ela — adaptando o script para conectar na instância restaurada
   em vez de subir um container local (ou, mais simples: rodar
   `pg_dump`/contagem de linhas manualmente contra o endpoint RDS restaurado
   usando as mesmas 5 tabelas-chave de `scripts/backup_restore_drill.sh`).

5. **Trocar `DATABASE_URL`/`DATABASE_URL_SYNC`** no `.env` de produção da
   instância EC2 para o novo endpoint RDS (`*.rds.amazonaws.com`), mantendo
   os usuários separados `klm_migrator`/`klm_app` (ver
   `docs/RUNBOOK_AWS_FASE1.md` §3.10).

6. **Reiniciar a stack**:
   ```bash
   docker compose -f docker-compose.prod.yml up -d --force-recreate api worker beat
   ```

7. **Confirmar `/health` e um smoke test manual** (login + dashboard) antes
   de considerar o incidente resolvido; só então desligar/deletar a instância
   RDS antiga (manter por alguns dias como rede de segurança, não deletar no
   mesmo dia).

8. **Post-mortem**: anotar RPO real observado (diferença entre o `restore-time`
   usado e o momento do incidente) e RTO real (tempo total do passo 1 ao 7)
   para recalibrar as estimativas da seção 1.
