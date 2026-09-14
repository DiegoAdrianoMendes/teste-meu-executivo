# KLM Hub - runtime OSS local

## Escopo

O runtime executa os quatro produtos OSS completos como sidecars locais. Os
checkouts pinados permanecem imutáveis; integrações são feitas por gateway,
API, webhook ou bridge no core KLM. Nenhum provedor externo, canal real, API de
modelo ou recurso AWS é habilitado nesta fase.

| Perfil | Produto e serviços | URL local |
|---|---|---|
| `channels` | Chatwoot web/worker, PostgreSQL pgvector, Redis, bootstrap e gateway | `http://127.0.0.1:3100` |
| `collaboration` | Rocket.Chat, MongoDB replica set, bootstrap e gateway | `http://127.0.0.1:3200` |
| `automation` | ChatterMate API/worker/frontend, PostgreSQL, Redis, bootstrap e gateway | `http://127.0.0.1:3300` |
| `crm` | BottleCRM API/worker/beat/frontend, PostgreSQL, Redis e gateway | `http://127.0.0.1:3400` |

## Versões fixadas

| Produto | Fonte materializada | Runtime local |
|---|---|---|
| Chatwoot | tag `v4.15.1`, commit `97bb8ecd326299c46eacea74863976427c96fe94` | imagem oficial `chatwoot/chatwoot:v4.15.1` |
| Rocket.Chat | referência `8.6.1`, commit `bfd782302d20cbd672c785ac8c0d072d7cc6b0db` | compatibilidade local `6.13.1`, MongoDB `6.0.26-ubi8` |
| ChatterMate | tag `v1.0.14`, commit `9770df5b4d4909084a3b3a00ff2404d25724de90` | imagens locais construídas do pin `1.0.14` |
| BottleCRM | ref `0.46`, commit `3ab79d75ece005a414d9d3ef729b7240545d1b41` | imagens locais construídas do commit pinado |

O Rocket.Chat `8.6.1` permanece como fonte auditada. Sem licença, o artefato
oficial air-gapped dessa linha ficou somente leitura; a tentativa de build FOSS
atual encontrou dependência privada de workspace. Para manter a colaboração
local funcional sem fingir equivalência, o Compose usa `6.13.1` e registra a
política `local-compatibility`. Produção exige um build FOSS atual auditado ou
licença offline expressamente aprovada.

As demais dependências usam tags exatas, incluindo pgvector `0.8.1-pg16`, Redis
`7.4.2-alpine`, PostgreSQL `16.8-alpine` e Nginx `1.29.5-alpine`. Diretórios
proprietários excluídos pelo lock não são materializados nem montados.

## Configuração e inicialização

Pré-requisitos: Docker Compose v2, Git, Python 3.10+ e fontes materializadas por
`scripts/fetch_klm_upstreams.sh`.

Validação sem pull ou start:

```bash
./scripts/start_oss_sidecars.sh all --config-only
```

O script reconcilia `.local/oss.env` com permissão `0600`, preserva os segredos
já existentes e valida o Compose. Para iniciar um perfil ou todos:

```bash
./scripts/start_oss_sidecars.sh channels
./scripts/start_oss_sidecars.sh collaboration
./scripts/start_oss_sidecars.sh automation
./scripts/start_oss_sidecars.sh crm
./scripts/start_oss_sidecars.sh all
```

Os bootstraps são idempotentes: criam ou reconciliam o administrador local, a
organização, contas, canais e webhooks necessários. Um perfil pode reiniciar
sem interromper os demais.

Para parar sem apagar volumes:

```bash
./scripts/stop_oss_sidecars.sh channels
./scripts/stop_oss_sidecars.sh collaboration
./scripts/stop_oss_sidecars.sh automation
./scripts/stop_oss_sidecars.sh crm
./scripts/stop_oss_sidecars.sh all
```

Os volumes nomeados preservam bancos, filas, anexos e arquivos de cada produto:

- Chatwoot: `chatwoot-postgres-data`, `chatwoot-redis-data`, `chatwoot-storage`;
- Rocket.Chat: `rocket-mongo-data`;
- ChatterMate: `chattermate-config`, `chattermate-model-cache`,
  `chattermate-postgres-data`, `chattermate-redis-data`, `chattermate-temp` e
  `chattermate-uploads`;
- BottleCRM: `bottlecrm-media`, `bottlecrm-postgres-data`,
  `bottlecrm-redis-data` e `bottlecrm-static`.

## Verificação

```bash
python3 scripts/verify_oss_sidecars.py --env-file .local/oss.env --profile all --preflight
python3 scripts/verify_oss_sidecars.py --env-file .local/oss.env --profile all --runtime
```

Em 19 de julho de 2026, a verificação estática e de runtime aprovou todos os
serviços e endpoints. A projeção autenticada em
`GET /api/v1/oss-adapters/status` marcou os quatro produtos como `ready`, com
autenticação `connected_local`:

- o Chatwoot pode informar `data_services=failing` no `/api` antes de uma
  conexão Rails preguiçosa, mas a leitura autenticada da conta no mesmo probe
  confirma banco e token; esse sinal mais forte evita falso estado degradado;
- o Rocket.Chat responde `6.13` no `/api/info`, mantém dez canais jurídicos,
  salas por caso e webhook bidirecional;
- o ChatterMate expõe API, worker e interface completa local, com recursos de
  agente, handoff, knowledge, preview e editor preservados;
- o BottleCRM expõe interface, API, workers, auditoria e MCP do produto local.

## Pontes com o KLM

Na página `Ferramentas e integrações`, `Abrir completo` emite um bilhete HMAC de
curta duração, preso ao único workspace provisionado no sidecar e de uso único.
O consumo é registrado em ledger local `0600`, protegido por lock de arquivo e
preservado entre processos e restarts. O gateway troca o bilhete por uma sessão
local do produto; replay ou workspace divergente é recusado. ChatterMate e
BottleCRM podem ser incorporados, mas as quatro interfaces completas também
abrem separadamente para preservar UX e funcionalidades upstream.

As projeções hoje implementadas são seletivas e reconciliáveis:

- Chatwoot: contatos, conversas externas e mensagens de cliente, sem expor
  notas internas ou threads órfãs;
- Rocket.Chat: canais do escritório, salas privadas por caso e mensagens
  internas, com ingestão bidirecional, deduplicação e recuperação de registros
  externos apagados;
- BottleCRM: organização, contatos, leads, oportunidades, casos e tarefas, com
  vínculo único de organização, idempotência e reparo de criação parcial;
- ChatterMate: runtime completo e catálogo disponíveis; o KLM continua dono da
  definição jurídica versionada dos flows e não replica dados sem contrato.

## Defaults fail-closed

- apenas gateways stateless publicam portas, sempre em `127.0.0.1`;
- bancos e filas não publicam portas no host e as redes de dados são internas;
- segredos locais são obrigatórios, aleatórios, ignorados pelo Git e não saem
  de `.local/oss.env`;
- signup público, telemetria, cloud registration, push relay e integrações de
  marketplace permanecem desativados quando configuráveis;
- as variáveis opcionais dos conectores upstream continuam preservadas para uma
  futura ativação, mas permanecem vazias; nenhuma credencial de AWS, S3, Meta,
  Google, OpenAI, Stripe ou outro provedor está ativa no runtime OSS local;
- a CSP do gateway ChatterMate local aceita somente recursos e conexões do
  próprio host. Os destinos Firebase, Google, Shopify e demais provedores ficam
  bloqueados até existir aprovação online e uma configuração separada; o
  verificador também consulta o header CSP servido pelo container vivo, não
  apenas o arquivo de configuração;
- webhooks e tickets exigem segredo ou token, escopo de workspace, deduplicação e
  rejeição de payload conflitante;
- cada conta Chatwoot e organização BottleCRM ativas possuem vínculo mestre
  exato e globalmente único com um workspace KLM;
- ativar APIs de modelos no futuro não habilita canais, assinatura, cobrança,
  anúncios ou AWS.

## Limites locais

Este ambiente não é configuração de produção: não há TLS, alta disponibilidade,
backup automatizado, SSO corporativo nem teste de carga de produção. O Mongo usa
replica set de nó único. Não devem ser colocados dados jurídicos reais nem
credenciais de provedores neste runtime de aprovação.

Os gateways usam uma rede de publicação não interna exclusivamente para expor
portas em `127.0.0.1`; processos com dados permanecem apenas na rede interna.
As provas automatizadas verificam configuração, credenciais vazias e relatos
dry-run da aplicação. Elas não equivalem a captura independente de pacotes ou
observação de todos os sockets do host.

As portas podem ser alteradas por `KLM_OSS_CHATWOOT_PORT`,
`KLM_OSS_ROCKETCHAT_PORT`, `KLM_OSS_CHATTERMATE_PORT` e
`KLM_OSS_BOTTLECRM_PORT` em `.local/oss.env`, sempre com valores distintos. Em
falha, os scripts exibem `docker compose ps` e logs recentes sem remover volumes.
