# V1 greenfield, contrato PostgreSQL e recuperação por snapshot RDS

Status: procedimento operacional com decisão humana. Ele não autoriza deploy,
restore ou troca de tráfego. A role OIDC comum não deve receber permissões de
recuperação. Use uma sessão break-glass curta, registrada e aprovada por duas
pessoas.

## Decisão desta candidata: greenfield, sem rollback binário legado

A revisão `a671ed1` não é rollback-compatible com o boundary PostgreSQL seguro.
Seu auditor exige exatamente uma policy por tabela (`app/db/rls.py` daquela
revisão, linhas 931-950) e exige owner dos resolvers com `BYPASSRLS` (linhas
987-994). A revisão `f2c4d6e8a031` faz deliberadamente o oposto: owner dedicado
`NOLOGIN NOBYPASSRLS`, cinco policies `klm_resolver_owner_select` (a quinta, em
`integration_connections`, veio com `b8d2f4a6c9e1`), funções e
ACLs exatas. Não reintroduzir `BYPASSRLS`, não omitir policies e não configurar
`KLM_PREVIOUS_APP_REF` para obter um verde artificial.

O workflow manual desta candidata é somente `greenfield-prepare`:

1. constrói e publica uma imagem por digest;
2. prova a ausência do bootstrap efêmero;
3. cria e aguarda o snapshot pré-migração;
4. executa a migration bloqueante até o head `e8c0a2d4f617`;
5. reconcilia e audita grants como migrator, sem master e sem RuntimeSecret;
6. grava evidência com `runtime_state=PREPARED`,
   `ecs_services_mutated=false`, `frontends_published=false` e
   `dns_cutover=false`;
7. encerra sem `ecs update-service`, publicação frontend, smoke externo ou DNS.

### Preflight do titular da plataforma

O head inclui a revisão `b4e6f8a0c253`, que instala um índice parcial único que impede mais de um
`platform_owner`. A revisão histórica `e7a8c2f6d105` promoveu todos os antigos
`is_platform_admin`; por isso uma base com múltiplos candidatos falha antes de
qualquer reclassificação ou criação do índice. Não escolha o primeiro registro,
não use `LIMIT 1` e não altere a revisão histórica.

Em uma base já ocupada, o operador deve consultar os candidatos, validar a
identidade do titular fora do banco e fornecer à task de migration somente o
UUID confirmado:

```sql
SELECT id,
       email,
       is_active,
       deleted_at,
       is_internal_staff,
       platform_access_active,
       platform_role_package
FROM users
WHERE platform_role_package = 'platform_owner'
   OR is_platform_admin = true
ORDER BY id;
```

```bash
: "${KLM_PLATFORM_OWNER_USER_ID:?UUID aprovado do titular ausente}"
alembic upgrade head
unset KLM_PLATFORM_OWNER_USER_ID
```

O seletor não é necessário em uma base greenfield sem usuários nem quando já
existe exatamente um owner válido. O UUID selecionado precisa representar um
admin legado ativo, interno, não excluído, com acesso Blackbox e sem membership
de workspace. Os demais owners históricos são reclassificados como
`platform_admin`; nenhum grant comum reproduz o bypass raiz. Se qualquer
condição não puder ser provada, a migration termina sem alteração e a
reconciliação deve ocorrer sob procedimento humano auditado.

A promoção `RuntimeState=ENABLED` é uma operação posterior: change set IaC
humano, revisado, usando exatamente o digest registrado. Enquanto isso, rollback
real significa **não executar o cutover**. Se a migration precisar ser desfeita,
restaure o snapshot para uma nova instância e aponte o change set apenas depois
da validação humana. Nunca reative `a671ed1` contra o banco migrado.

## Gate de ausência do bootstrap efêmero

Imediatamente antes do snapshot e duas vezes na task de migration, o gate exige
ausência simultânea de: sentinel SSM, quatro roles IAM, state machine efêmera,
task definitions ECS `ACTIVE`, tasks ECS `RUNNING`/`PENDING` e security groups
com tags exatas `Project`, `Environment` e `Ephemeral=database-bootstrap`.
Qualquer erro diferente de `ParameterNotFound`, `NoSuchEntity` ou
`StateMachineDoesNotExist`, ou qualquer array não vazio, bloqueia a candidata.

A role OIDC não possui `cloudformation:*`. Portanto a ausência da stack do
bootstrap é prova humana obrigatória no change set/ticket, não uma alegação do
script. Registrar stack name, status final de delete e horário sem imprimir
credenciais. O CI local e este runbook não provam estado AWS de produção.

A AWS documenta que restore de snapshot cria uma nova instância, nunca altera a
instância existente. Também documenta que renomear uma instância muda seu
endpoint e reinicia a instância. Por isso este runbook não depende de rename nem
presume estabilidade de DNS:

- https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_RestoreFromSnapshot.html
- https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_RenameInstance.html

## Inventário protegido obrigatório

O comandante do incidente registra estes valores no ticket restrito, e não no
repositório ou no log do shell:

- região AWS e conta confirmadas;
- `AWS_RDS_DB_IDENTIFIER`, `RDS_SNAPSHOT_ID` e novo
  `RESTORED_DB_IDENTIFIER`;
- classe, subnet group, parameter group e security groups privados do RDS;
- `AWS_ECS_CLUSTER` e os targets `api`, `worker` e `beat`;
- ARN externo do segredo runtime e ARN externo do segredo migration;
- SHA e task definitions que estavam estáveis antes do deploy;
- desired count original de cada serviço;
- versão `AWSCURRENT` original de cada segredo;
- horário de bloqueio de escrita, RPO aceito e aprovadores.

`RuntimeSecretArn` e `MigrationSecretArn` são parâmetros NoEcho externos da
stack, não outputs. O segredo runtime contém `DATABASE_USERNAME`,
`DATABASE_PASSWORD`, `DATABASE_URL` e `DATABASE_URL_SYNC` do `klm_app`, além dos
demais segredos da aplicação. O segredo migration contém os mesmos quatro campos
do `klm_migrator`. Nunca use `DatabaseMasterSecretArn` em API, worker, beat,
migration recorrente ou canary.

Antes de continuar, o shell break-glass deve falhar fechado se qualquer valor
operacional estiver ausente:

```bash
: "${AWS_REGION:?inventário protegido ausente}"
: "${AWS_RDS_DB_IDENTIFIER:?inventário protegido ausente}"
: "${RDS_SNAPSHOT_ID:?snapshot aprovado ausente}"
: "${RESTORED_DB_IDENTIFIER:?identificador isolado ausente}"
: "${RESTORE_DB_INSTANCE_CLASS:?classe validada ausente}"
: "${RESTORE_DB_SUBNET_GROUP_NAME:?subnet group privado ausente}"
: "${RESTORE_DB_PARAMETER_GROUP_NAME:?parameter group ausente}"
: "${RESTORE_VPC_SECURITY_GROUP_IDS:?security groups privados ausentes}"
: "${AWS_ECS_CLUSTER:?cluster validado ausente}"
: "${AWS_ECS_SERVICE_TARGETS:?targets validados ausentes}"
: "${RUNTIME_SECRET_ARN:?ARN runtime protegido ausente}"
: "${MIGRATION_SECRET_ARN:?ARN migration protegido ausente}"
```

## Fase 0: declarar e conter o incidente

1. Nomear comandante, operador AWS e segundo aprovador.
2. Bloquear o deploy automático e registrar o SHA, a imagem, a execução Step
   Functions e o snapshot pré-migração.
3. Na candidata greenfield, confirmar `RuntimeState=PREPARED`, ausência de ECS
   Services e ausência de cutover DNS. Não criar nem restaurar task definitions.
   O snapshot é o único boundary de reversão do banco migrado.
4. Declarar explicitamente a perda máxima de dados aceita: todo write posterior
   ao snapshot ficará fora da instância restaurada e exigirá reconciliação.

## Fase 1: validar o snapshot e preparar o restore isolado

O snapshot precisa estar `available`, criptografado, ser PostgreSQL e pertencer
à instância esperada. O target não pode existir:

```bash
aws rds describe-db-snapshots \
  --db-snapshot-identifier "$RDS_SNAPSHOT_ID" \
  --query 'DBSnapshots[0].{status:Status,source:DBInstanceIdentifier,engine:Engine,encrypted:Encrypted,kms:KmsKeyId,created:SnapshotCreateTime}' \
  --output json | tee rds-snapshot-evidence.json

jq -e --arg source "$AWS_RDS_DB_IDENTIFIER" \
  '.status == "available" and .source == $source and .engine == "postgres" and .encrypted == true and (.kms | length > 0)' \
  rds-snapshot-evidence.json >/dev/null

if aws rds describe-db-instances \
  --db-instance-identifier "$RESTORED_DB_IDENTIFIER" >/dev/null 2>&1; then
  echo "O identificador de restore já existe; interrompa e investigue." >&2
  exit 1
fi
```

Depois da segunda aprovação, restaurar para uma instância nova, privada e com
proteção contra exclusão. `RESTORE_VPC_SECURITY_GROUP_IDS` é uma lista JSON de
IDs já validada no inventário:

```bash
mapfile -t RESTORE_SECURITY_GROUP_ARGS < <(
  jq -r '.[]' <<<"$RESTORE_VPC_SECURITY_GROUP_IDS"
)
test "${#RESTORE_SECURITY_GROUP_ARGS[@]}" -gt 0

aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier "$RESTORED_DB_IDENTIFIER" \
  --db-snapshot-identifier "$RDS_SNAPSHOT_ID" \
  --db-instance-class "$RESTORE_DB_INSTANCE_CLASS" \
  --db-subnet-group-name "$RESTORE_DB_SUBNET_GROUP_NAME" \
  --db-parameter-group-name "$RESTORE_DB_PARAMETER_GROUP_NAME" \
  --vpc-security-group-ids "${RESTORE_SECURITY_GROUP_ARGS[@]}" \
  --no-publicly-accessible \
  --deletion-protection \
  --copy-tags-to-snapshot \
  --tags Key=RecoveryIncident,Value="$RECOVERY_INCIDENT_ID"

aws rds wait db-instance-available \
  --db-instance-identifier "$RESTORED_DB_IDENTIFIER"

RESTORED_DB_ENDPOINT="$(aws rds describe-db-instances \
  --db-instance-identifier "$RESTORED_DB_IDENTIFIER" \
  --query 'DBInstances[0].Endpoint.Address' --output text)"
RESTORED_DB_PORT="$(aws rds describe-db-instances \
  --db-instance-identifier "$RESTORED_DB_IDENTIFIER" \
  --query 'DBInstances[0].Endpoint.Port' --output text)"
test -n "$RESTORED_DB_ENDPOINT"
test "$RESTORED_DB_ENDPOINT" != "None"
```

Não execute migration na instância restaurada. Primeiro prove a revisão
`alembic_version` do snapshot e que ela corresponde ao ponto pré-migração. Não
execute `a671ed1` contra o banco migrado nem use downgrade Alembic em incidente.

## Fase 2: preparar versões de segredo sem imprimir SecretString

Use uma sessão efêmera com `umask 077`. Os arquivos abaixo nunca entram no
workspace, artefatos de CI, histórico ou Obsidian:

```bash
umask 077
RECOVERY_SECRET_DIR="$(mktemp -d)"
cleanup_recovery_secrets() {
  rm -f -- "$RECOVERY_SECRET_DIR"/*.json
  rmdir "$RECOVERY_SECRET_DIR"
}
trap cleanup_recovery_secrets EXIT INT TERM

aws secretsmanager get-secret-value --secret-id "$RUNTIME_SECRET_ARN" \
  --query SecretString --output text > "$RECOVERY_SECRET_DIR/runtime-current.json"
aws secretsmanager get-secret-value --secret-id "$MIGRATION_SECRET_ARN" \
  --query SecretString --output text > "$RECOVERY_SECRET_DIR/migration-current.json"
chmod 600 "$RECOVERY_SECRET_DIR"/*.json
```

Reconstrua as URLs preservando scheme, database e query TLS atuais, alterando
somente credencial, endpoint e porta. O programa não imprime conteúdo secreto:

```bash
python3 - "$RECOVERY_SECRET_DIR" "$RESTORED_DB_ENDPOINT" "$RESTORED_DB_PORT" <<'PY'
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

root = Path(sys.argv[1])
host = sys.argv[2]
port = int(sys.argv[3])

for role in ("runtime", "migration"):
    source = root / f"{role}-current.json"
    target = root / f"{role}-pending.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    username = str(payload["DATABASE_USERNAME"])
    password = str(payload["DATABASE_PASSWORD"])
    for key in ("DATABASE_URL", "DATABASE_URL_SYNC"):
        current = urlsplit(str(payload[key]))
        netloc = f"{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"
        payload[key] = urlunsplit((current.scheme, netloc, current.path, current.query, ""))
    target.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.chmod(target, 0o600)
PY
```

Crie versões `AWSPENDING` e registre seus IDs sem mover `AWSCURRENT`:

```bash
RUNTIME_TOKEN="$(python3 -c 'import uuid; print(uuid.uuid4())')"
MIGRATION_TOKEN="$(python3 -c 'import uuid; print(uuid.uuid4())')"

RUNTIME_PENDING_VERSION="$(aws secretsmanager put-secret-value \
  --secret-id "$RUNTIME_SECRET_ARN" \
  --client-request-token "$RUNTIME_TOKEN" \
  --secret-string "file://$RECOVERY_SECRET_DIR/runtime-pending.json" \
  --version-stages AWSPENDING --query VersionId --output text)"
MIGRATION_PENDING_VERSION="$(aws secretsmanager put-secret-value \
  --secret-id "$MIGRATION_SECRET_ARN" \
  --client-request-token "$MIGRATION_TOKEN" \
  --secret-string "file://$RECOVERY_SECRET_DIR/migration-pending.json" \
  --version-stages AWSPENDING --query VersionId --output text)"
```

## Fase 3: canary obrigatório, ainda sem tráfego

As fases 3 e 4 descrevem recuperação de uma implantação futura já habilitada.
Na preparação greenfield atual não há ECS Service, task anterior ou tráfego para
recuperar: mantenha `PREPARED`, valide o restore isolado e submeta um novo change
set humano. Não simule infraestrutura anterior.

Uma role humana de recuperação registra uma task revision temporária que aponta
os quatro campos de banco para `AWSPENDING`. Ela mantém imagem, task role,
execution role, subnets e security group da revisão anterior. O canary deve:

1. autenticar como `klm_app` e provar `NOSUPERUSER`, `NOCREATEDB`,
   `NOCREATEROLE`, `NOINHERIT`, `NOREPLICATION` e `NOBYPASSRLS`;
2. provar que `current_user = session_user = klm_app`;
3. ler `alembic_version` e compará-la com a revisão registrada no snapshot;
4. executar somente a imagem compatível com a revisão catalogal observada e um
   write transacional com rollback; para a candidata greenfield ainda não existe
   serviço anterior a reativar;
5. executar `/health`, login controlado e uma leitura tenant com RLS;
6. produzir logs sem URL, senha, token, conteúdo de cliente ou SecretString.

Falha em qualquer item remove apenas `AWSPENDING`, preserva `AWSCURRENT`, encerra
o canary e bloqueia o switchover. A role OIDC comum não pode executar esse fluxo.

## Fase 4: write freeze e troca controlada

Capture task definitions e desired counts em `ecs-before-recovery.json`, coloque
API, worker e beat em zero e aguarde estabilidade. Frontends podem continuar
publicados, mas o ALB deve responder manutenção até o fim da decisão:

```bash
aws ecs describe-services \
  --cluster "$AWS_ECS_CLUSTER" \
  --services $(jq -r '.[].service' <<<"$AWS_ECS_SERVICE_TARGETS") \
  --query 'services[].{service:serviceName,taskDefinition:taskDefinition,desiredCount:desiredCount}' \
  --output json > ecs-before-recovery.json

while read -r service; do
  aws ecs update-service --cluster "$AWS_ECS_CLUSTER" \
    --service "$service" --desired-count 0 >/dev/null
done < <(jq -r '.[].service' <<<"$AWS_ECS_SERVICE_TARGETS")

aws ecs wait services-stable --cluster "$AWS_ECS_CLUSTER" \
  --services $(jq -r '.[].service' <<<"$AWS_ECS_SERVICE_TARGETS")
```

Somente depois de confirmar zero writers, mova `AWSCURRENT` nos dois segredos.
Se o segundo movimento falhar, reverta o primeiro antes de iniciar qualquer task:

```bash
RUNTIME_CURRENT_VERSION="$(aws secretsmanager list-secret-version-ids \
  --secret-id "$RUNTIME_SECRET_ARN" \
  --query 'Versions[?contains(VersionStages, `AWSCURRENT`)].VersionId | [0]' --output text)"
MIGRATION_CURRENT_VERSION="$(aws secretsmanager list-secret-version-ids \
  --secret-id "$MIGRATION_SECRET_ARN" \
  --query 'Versions[?contains(VersionStages, `AWSCURRENT`)].VersionId | [0]' --output text)"

aws secretsmanager update-secret-version-stage \
  --secret-id "$RUNTIME_SECRET_ARN" --version-stage AWSCURRENT \
  --move-to-version-id "$RUNTIME_PENDING_VERSION" \
  --remove-from-version-id "$RUNTIME_CURRENT_VERSION"
aws secretsmanager update-secret-version-stage \
  --secret-id "$MIGRATION_SECRET_ARN" --version-stage AWSCURRENT \
  --move-to-version-id "$MIGRATION_PENDING_VERSION" \
  --remove-from-version-id "$MIGRATION_CURRENT_VERSION"
```

Restaure as task definitions e desired counts anteriores. `force-new-deployment`
é obrigatório porque secrets são injetados no início da task:

```bash
jq -c '.[]' ecs-before-recovery.json | while read -r service_state; do
  service="$(jq -r '.service' <<<"$service_state")"
  task_definition="$(jq -r '.taskDefinition' <<<"$service_state")"
  desired_count="$(jq -r '.desiredCount' <<<"$service_state")"
  aws ecs update-service --cluster "$AWS_ECS_CLUSTER" \
    --service "$service" \
    --task-definition "$task_definition" \
    --desired-count "$desired_count" \
    --force-new-deployment >/dev/null
done

aws ecs wait services-stable --cluster "$AWS_ECS_CLUSTER" \
  --services $(jq -r '.[].service' <<<"$AWS_ECS_SERVICE_TARGETS")
```

Libere tráfego apenas depois de smokes de API, app e Blackbox, verificação de
workers/filas, RLS tenant A/B e reconciliação dos writes posteriores ao snapshot.

## Rollback do switchover

Se a instância restaurada falhar depois de iniciar tasks:

1. recolocar API, worker e beat em zero e aguardar estabilidade;
2. mover `AWSCURRENT` de runtime e migration para os VersionIds originais;
3. restaurar task definitions e desired counts de `ecs-before-recovery.json`;
4. aguardar estabilidade e repetir os smokes;
5. manter as duas instâncias com deletion protection durante a janela aprovada;
6. registrar RPO, RTO, writes reconciliados, evidência e decisão humana.

Não apague, renomeie ou migre nenhuma das instâncias no mesmo incidente. Uma
ação posterior de descarte exige ticket separado, snapshot final, retenção
confirmada e aprovação humana.
