# Runbook AWS V1 — Meu Executivo

Status: contrato de código validável localmente. Os recursos AWS, GitHub e
Cloudflare continuam sem comprovação até autenticação e inventário explícitos.
Este documento não autoriza deploy e não altera o site público.

## Topologia obrigatória

```text
meuexecutivo.com / www.meuexecutivo.com
  -> site público atual, fora deste deploy

app.meuexecutivo.com
  -> CloudFront App -> bucket S3 privado App
                    -> /api/* e /api/v1/ws* -> API/ALB

blackbox.meuexecutivo.com
  -> CloudFront Blackbox -> bucket S3 privado Blackbox
                         -> /api/* e /api/v1/ws* -> API/ALB

api.meuexecutivo.com
  -> ALB HTTPS -> ECS API

ECS
  -> API, worker e beat como serviços separados
  -> migration como task bloqueante antes do rollout
  -> RDS PostgreSQL privado
  -> ElastiCache privado
  -> S3 privado de documentos
```

O `docker-compose.prod.yml` é somente fallback self-hosted. Ele exige arquivos
separados `.env.migration` e `.env.runtime`; a rede de runtime permite saída para
os serviços gerenciados externos, enquanto a exposição de entrada permanece
limitada à rede explícita do proxy.

App e Blackbox são origens distintas. Cada uma possui build, bucket,
distribuição, política de resposta e sessão próprios. O navegador usa `/api`
same-origin; por isso o refresh cookie permanece host-only e nunca utiliza
`Domain=.meuexecutivo.com`.

## Invariantes de segurança

- O site público não compartilha bucket nem distribuição com App ou Blackbox.
- App não entrega `/blackbox`; Blackbox não entrega `/app` nem rotas de cliente.
- Blackbox exige identidade de plataforma e MFA real em produção.
- CORS contém somente as duas origens HTTPS exatas.
- CloudFront redireciona HTTP para HTTPS e aplica uma Response Headers Policy
  com CSP, HSTS, `X-Content-Type-Options`, `Referrer-Policy`, COOP e CORP.
- Buckets são privados e usam Origin Access Control; website hosting público fica
  desativado, Public Access Block fica integral e a policy efetiva não é pública.
- As duas distribuições possuem Web ACL; o deploy falha fechado quando WAF ou o
  baseline real da Response Headers Policy estiver ausente.
- `/api/*` e `/api/v1/ws*` apontam para a origem da API, permitem todos os métodos
  necessários, usam a policy AWS `CachingDisabled`
  (`4135ea2d-6df8-44a3-9df3-4b5a84be39ad`) e encaminham headers, cookies e query
  strings com `AllViewerExceptHostHeader`
  (`b689b0a8-53d0-40ab-baf2-68738e2966ac`).
- ECS usa Task Roles. Não existem access keys em arquivo, imagem ou GitHub.
- GitHub autentica na AWS por OIDC e environment protegido `production`.
- O CI busca o histórico Git completo e executa Gitleaks 8.28 por digest; as
  exceções são limitadas por regra e arquivo no `.gitleaks.toml`.
- Imagens usam tag imutável igual ao SHA do commit.
- RDS recebe snapshot antes da migration.
- Migration precisa terminar com exit code zero e auditoria catalogal completa.
- A primeira candidata é greenfield: permanece `RuntimeState=PREPARED`, não
  altera ECS Services, não publica frontends e não troca DNS.
- `a671ed1` não pode ser reativada contra o head seguro `e8c0a2d4f617`; o antigo
  auditor exige `BYPASSRLS` no owner e exatamente uma policy por tabela.

## GitHub Variables necessárias

| Variável | Conteúdo esperado |
|---|---|
| `AWS_REGION` | Região real dos recursos inventariados |
| `AWS_IMAGE_ROLE_TO_ASSUME` | Role OIDC somente para ECR da imagem |
| `AWS_MIGRATION_ROLE_TO_ASSUME` | Role OIDC somente para registrar/aguardar migration |
| `AWS_RUNTIME_ROLE_TO_ASSUME` | Role OIDC usada nesta fase somente para snapshot |
| `AWS_ECR_REPOSITORY` | Repositório ECR do backend |
| `AWS_ECS_CLUSTER` | Cluster ECS de produção |
| `AWS_ECS_SERVICE_TARGETS` | **Ausente** no `greenfield-prepare`; só é válido em update futuro compatível |
| `AWS_ECS_MIGRATION_TASK_FAMILY` | Família da task Alembic |
| `AWS_ECS_MIGRATION_CONTAINER_NAME` | Container da migration |
| `AWS_ECS_LAUNCH_TYPE` | `FARGATE` ou `EC2` |
| `AWS_RDS_DB_IDENTIFIER` | Instância RDS usada para o snapshot |
| `AWS_APP_BUCKET` | Bucket privado exclusivo do App |
| `AWS_BLACKBOX_BUCKET` | Bucket privado exclusivo da Blackbox |
| `AWS_PUBLIC_SITE_BUCKET` | Bucket do site, usado apenas como trava negativa |
| `AWS_APP_CLOUDFRONT_DISTRIBUTION_ID` | Distribuição exclusiva do App |
| `AWS_BLACKBOX_CLOUDFRONT_DISTRIBUTION_ID` | Distribuição exclusiva da Blackbox |
| `AWS_PUBLIC_SITE_CLOUDFRONT_DISTRIBUTION_ID` | Distribuição pública, somente trava negativa |
| `VITE_META_APP_ID` | Identificador público do app Meta, quando disponível |
| `VITE_META_CONFIG_ID` | Identificador público da configuração Meta |
| `VITE_META_SOLUTION_ID` | Identificador público da solução Meta |

Exemplo de `AWS_ECS_SERVICE_TARGETS`:

```json
[
  {"role":"api","service":"meu-executivo-api","container":"api"},
  {"role":"worker","service":"meu-executivo-worker","container":"worker"},
  {"role":"beat","service":"meu-executivo-beat","container":"beat"}
]
```

Os nomes acima são exemplos. O inventário autenticado deve fornecer os nomes
reais; não renomeie recursos existentes sem plano de migração.

## Configuração AWS anterior ao primeiro deploy

1. Confirmar conta, região e titularidade dos recursos existentes.
2. Confirmar RDS, ElastiCache, ECR, ECS, ALB, Secrets Manager e S3.
3. Criar ou validar serviços separados para API, worker e beat.
4. Validar a task de migration e seus usuários de banco separados.
5. Criar role OIDC restrita ao repositório `MeuExecutivo` e ao environment
   `production`; não permitir branches arbitrárias.
6. Criar buckets privados e distribuições separadas para App e Blackbox.
7. Preservar integralmente o bucket, distribuição e DNS do site público.
8. Configurar certificados para `app`, `blackbox` e `api`.
9. Configurar os comportamentos same-origin da API em ambas as distribuições.
10. Configurar WAF; aplicar controle de acesso adicional à Blackbox quando o fluxo
    de login e callbacks estiver validado.
11. Armazenar segredos no Secrets Manager e associá-los às task definitions.
12. Configurar alarmes de ECS, ALB 5xx, RDS, Redis, filas e orçamento.

## Preparação greenfield da V1

O workflow `.github/workflows/deploy.yml` executa CI em push/PR, mas a preparação
é somente manual, na branch `main`, com `prepare_greenfield_v1` e a frase:

```text
PREPARE_MEU_EXECUTIVO_V1_GREENFIELD
```

O environment `production` deve exigir aprovação humana. A sequência é:

1. validar o modo `greenfield-prepare` e negar `KLM_PREVIOUS_APP_REF` ou targets
   ECS;
2. construir o backend sem credencial AWS;
3. assumir a role de imagem por OIDC, publicar tag única e resolver o digest;
4. assumir a role runtime somente para criar/aguardar snapshot RDS;
5. assumir a role de migration e executar Step Functions bloqueante;
6. dentro da task, executar `alembic upgrade head`, descobrir exatamente um
   runtime pelos ACLs dos quatro resolvers, reconciliar grants e auditar owner,
   body/search path/flags, ACL, policies, FORCE RLS e DML exato;
7. guardar commit, digest, snapshot, task definition e execution ARN junto dos
   marcadores `PREPARED`/sem cutover;
8. encerrar. Não atualizar services, publicar frontends, executar smoke externo
   ou alterar DNS.

A habilitação posterior exige change set IaC humano com o mesmo digest. A role
OIDC desta candidata não prova ausência de stack CloudFormation, porque não
recebe `cloudformation:*`; essa evidência é humana no ticket.

## Reversão antes do cutover

- Se migration falhar, nenhum serviço, frontend ou DNS é alterado.
- Não execute downgrade Alembic e não reative a revisão `a671ed1`.
- Mantenha `RuntimeState=PREPARED` e o cutover bloqueado.
- Restaure o snapshot para nova instância somente com decisão humana, validação
  isolada e RPO/RTO aceitos; veja `RUNBOOK_EXPAND_CONTRACT_RDS_RECOVERY.md`.
- O site público continua fora do escopo e não deve ser tocado.

## Gates que permanecem externos

- autenticação AWS;
- inventário dos recursos e custos;
- configuração OIDC e GitHub Environment;
- certificados, DNS e Cloudflare;
- políticas CloudFront/WAF;
- segredos dos provedores;
- homologação e smoke de produção.

Nada desses itens pode ser declarado pronto apenas porque o código local passou.
