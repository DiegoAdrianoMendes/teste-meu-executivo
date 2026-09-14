# QA local - continuação 2026-07-23

## Escopo

Rodada local de hardening do KLM Hub, mantendo APIs externas e AWS desativadas.
O foco desta etapa foi a concorrência do billing local, a resolução de políticas
mutáveis de plano e a barreira de configuração do runtime de IA.

## Correções verificadas

- A resolução de entitlement usada pelo portal não adquire novamente o lock
  amplo de workspace + assinatura quando o fluxo de billing já possui o lock da
  assinatura.
- A materialização de atribuições de política continua preservada; apenas a
  leitura de runtime deixou de impor uma ordem de locks incompatível.
- A fábrica de modelos recusa gates externos com catálogo de rotas, preços ou
  allowlist ausentes/incompatíveis antes de criar um provider.
- A contabilidade de chamadas mantém claim pré-provider, journal durável,
  idempotência e estado explícito para chamadas incertas.

## Evidências

| Verificação | Resultado |
| --- | ---: |
| QA PostgreSQL descartável, RLS ativo, migração downgrade/upgrade | 33 passed, 0 skipped |
| Políticas, entitlement, billing local e wiring de IA | 39 passed |
| Frontend Vitest | 480 passed |
| Frontend TypeScript (`tsc --noEmit`) | aprovado |
| Frontend painel de políticas e avisos | 11 passed |
| Frontend controle de tokens e custos | 8 passed |
| API de políticas de plano e central de avisos | 18 passed |
| API de políticas versionadas e RBAC delegado | 10 passed |
| Conversas e ferramentas contextuais | 45 passed |
| Smoke local (`verify_klm_local.py`, concorrência 16) | 119 checks, PASS |
| Ações externas durante o smoke | 0 |
| Alembic heads | 1 (`0b7c9e1a2d34`) |
| `git diff --check` | aprovado |

### Evidência de continuidade desta rodada

| Verificação | Resultado |
| --- | ---: |
| Backend: políticas, avisos, omnichannel v6, operações, evidências, coexistência e armazenamento | 149 passed |
| Frontend: custos em tokens, avatar omnichannel, espera do cliente, ferramentas de conversas | 60 passed |
| Frontend: planos, avisos e serviços de governança | 19 passed |
| Editor completo de pacote (identidade, preços, catálogo, parâmetros e capacidades) | 6 passed |
| Frontend TypeScript (`tsc --noEmit -p tsconfig.app.json`) | aprovado |
| `git diff --check` | aprovado |

### Auditoria omnichannel desta continuidade

O TikTok não abre uma caixa paralela. A Central Social usa o thread canônico
para comentários e mensagens TikTok, com o mesmo vínculo de identidade, caso,
fila, filtros personalizados, automações e ações auditadas. A fronteira local
também expõe TikTok Business e TikTok Ads no registro de conexões, além de
WhatsApp Cloud/Coexistence, Instagram, Messenger, e-mail, site e Lovable.

O ingresso social assinado normaliza DM e comentário TikTok, preserva o evento
original e encaminha a mensagem ao mesmo contexto de conversa. A execução
externa permanece bloqueada por configuração local; nesta rodada o verificador
reportou `externalActionsExecuted=0`.

### Fechamento desta rodada

- Smoke autenticado local: `119 checks`, `PASS`, concorrência `8`.
- Frontend completo: `74` arquivos e `481` testes aprovados.
- TypeScript: `tsc --noEmit -p tsconfig.app.json` aprovado.
- Políticas/billing focados: `33 passed`.
- Nenhum deploy, webhook externo, OAuth ou chamada à Meta/TikTok foi ativado.

Nesta rodada, a preferência de avatar foi revalidada ponta a ponta: quando a
identidade possui fotos de múltiplos canais, WhatsApp permanece como fonte
preferencial; a foto é mantida por canal para auditoria e a interface usa
iniciais apenas quando a URL não é segura ou não está disponível. Os limites
visuais de espera do cliente permanecem em até 5 minutos (verde), até 10
minutos (amarelo) e acima de 10 minutos (vermelho), sem expor a sigla técnica
"SLA" ao cliente.

O painel de governança continua local e controlável: valores monetários de IA
ficam restritos ao superadministrador, enquanto contas e funcionários veem
tokens, limites e progresso. A edição de planos e avisos passou novamente por
API e UI sem ativar qualquer provedor externo.

O editor de planos agora também expõe os campos comerciais e de catálogo que
já existiam no contrato persistido, mas estavam ocultos na UI: nome, descrição,
status, visibilidade, recomendação, ordem, preços, suporte, integrações,
benefícios e parâmetros operacionais. A publicação continua protegida por
rascunho versionado, prévia de alcance, motivo obrigatório e idempotência.

## Auditoria da cadeia probatória da inbox

Foi feita uma passada específica sobre mensagens editadas, apagadas, não
suportadas pelo provedor e anexos de áudio/vídeo/documento:

- cada mensagem canônica recebe uma captura inicial `captured`;
- alterações e exclusões entram como eventos append-only, com revisão, hash do
  evento anterior e hash da cadeia;
- sinais de exclusão que o provedor não consegue detalhar permanecem registrados
  como `provider_unsupported`, sem apagar a captura original;
- anexos preservam hash SHA-256, MIME, tamanho, nome, identificador do provedor,
  objeto lógico e versão imutável do armazenamento local;
- o envelope bruto do webhook é mantido no cofre local com payload, assinatura,
  esquema e metadados saneados; a API do tenant expõe a prova da origem por
  hashes, sem entregar o envelope bruto global;
- a visualização de `Integridade` e a exportação do manifesto continuam dentro
  da conversa, com verificação de cadeia e segregação por workspace;
- permissões e consultas exigem a mesma conta e thread da mensagem, impedindo
  leitura cruzada entre clientes.

### Evidência executada nesta rodada

| Verificação | Resultado |
| --- | ---: |
| Serviço de evidência, mídia, coexistência e endpoints de evidência | 120 passed |
| Cadeia original -> edição -> exclusão | coberta e verificada |
| Idempotência de evento repetido | coberta e verificada |
| Hash de envelope e proteção append-only | coberta e verificada |
| Mídia por objeto/versão/hash | coberta e verificada |
| Isolamento entre workspaces e threads | coberta e verificada |

Esta implementação é uma camada técnica de integridade, custódia e
auditabilidade. A admissibilidade e a valoração jurídica final dependem do
caso concreto e da autoridade competente; não são tratadas como uma garantia
automática do sistema.

## Observação residual

O QA exibiu apenas um aviso de encerramento tardio de conexão assíncrona do
Redis quando o event loop já havia sido fechado. Não houve teste falho, dado
perdido ou ação externa; a rotina de negócio e o processo de QA terminaram com
status aprovado. A limpeza explícita de conexões permanece uma melhoria de
higiene de testes, não um bloqueio funcional do runtime local.

O painel administrativo foi conferido no contrato local: planos podem ser
editados por caixas e limites, pré-visualizados por conta/regra, publicados em
versões imutáveis e auditados; avisos podem ser direcionados, agendados,
publicados e registrados em fila local de e-mail sem tentativa externa.

O controle de IA mantém a separação de governança: contas e funcionários da
plataforma visualizam tokens, franquia, limite e progresso; valores monetários
internos permanecem restritos ao superadministrador. A própria tela de custos
agora aplica essa regra também no painel por workspace, inclusive em acesso
direto à rota.

A inbox também mantém o Caso 360 no mesmo contexto: a foto do contato no
cabeçalho abre o caso por ação explícita, sem trocar a rota da conversa. O
histórico vazio é renderizado uma única vez e a tela continua separando
histórico persistido de falha de carregamento.

As rotas versionadas de planos respeitam agora o mesmo RBAC da plataforma: o
super administrador mantém acesso total, enquanto um funcionário interno só
consegue consultar ou alterar pacotes quando possui, respectivamente,
`plans:view` ou `plans:manage`. Usuários de contas contratantes continuam
barrados pela API.

## Portão preservado

Nenhum token, credencial, webhook real, chamada à Meta/Google/TikTok, envio de
mensagem ou recurso AWS foi ativado nesta rodada.
### Catálogo local e publicação governada

Foi validada a ponte entre a política de plano versionada e o catálogo local
usado pelo painel de assinatura, cotação e checkout. Uma publicação imediata
com escopo **atuais e novas contas** atualiza, na mesma transação, o read model
legado de catálogo (nome, descrição, preços, visibilidade, ordem, limites
compatíveis e `PlanEntitlement`). Assim, a conta não recebe uma quota nova e
continua vendo preço ou descrição antigos.

Os modos **somente novas contas**, **agendado** e **por regra** não alteram o
catálogo global: permanecem resolvidos pela versão imutável e pela atribuição
da política, preservando a segmentação do rollout. A garantia está coberta em
`tests/services/test_plan_policy_service.py` e foi executada com a suíte focada
de políticas, billing local e endpoints: `33 passed`.

### Central de saúde operacional da plataforma

O painel global agora expõe uma aba `Erros e saúde` em `/platform/errors`.
Ela reutiliza o registro local de erros já existente, mantendo a mesma política
de permissão `errors:view/manage` da equipe SaaS. O Super Admin e o TI
autorizado conseguem consultar erros por status, revisar recorrência e
severidade e registrar a resolução sem misturar essa operação com a conta de um
escritório cliente. Nenhum envio externo, provedor ou AWS é acionado por essa
superfície.

Validação desta extensão:

- `./node_modules/.bin/tsc --noEmit -p tsconfig.app.json`
- `./node_modules/.bin/vitest run src/test/platform-announcements-panel.test.tsx src/test/platform-plan-policies-panel.test.tsx --pool=threads --poolOptions.threads.singleThread=true`

Resultado: compilação TypeScript sem erros e `12` testes focados aprovados.

### Auditoria de tracking e disparos

Foi feita uma passagem específica nos dois núcleos comerciais antes de
prosseguir para integrações externas.

No tracking, a cadeia local está exposta na experiência nativa: canal,
provedor, conta de anúncios, campanha, conjunto, anúncio, criativo, formulário,
placement, UTMs/IDs de clique, links para a conversa e para o gerenciador
oficial, nível de evidência, campos ausentes e percentual de completude. O
ranking, os gráficos por canal e por dia, o funil atribuído, a exportação CSV,
remarketing e o preparo de feedback de conversão usam o mesmo registro
canônico de aquisição. Conversões são consolidadas por identidade comercial e
o feedback possui chave de idempotência, bloqueio por falta de evidência e
contagem de duplicidades suprimidas.

Em disparos, a tela nativa mantém listas reutilizáveis, importação e exportação
CSV, deduplicação, opt-in/opt-out, supressão, filtros de CRM, prévia de
destinatários, template oficial, agendamento, histórico, retorno para as
conversas e flow pós-disparo com início manual, na entrega ou na resposta.
Quando aberta dentro da conversa, a audiência começa contextualizada no
contato atual e o histórico pode filtrar campanhas sem retorno.

Evidência executada nesta rodada:

- frontend: `20` testes focados de campanhas, onboarding, políticas de planos,
  avisos e integrações de aquisição;
- backend: `95` testes de tracking e operações, incluindo IDs de criativo,
  hierarquia, mascaramento por permissão e prevenção de duplicidade;
- `./node_modules/.bin/tsc --noEmit -p tsconfig.app.json`;
- `git diff --check`.

Todos passaram. O caminho externo continua fechado: nenhum token, anúncio,
webhook, mensagem ou provedor foi chamado. A próxima etapa de produção é
somente configurar e validar as credenciais oficiais, após aprovação explícita.

### Contrato nativo dos nós avançados

Os nós `form`, `action`, `end`, `human_transfer`, `landing_page` e `llm` agora
fazem parte do catálogo autoritativo do KLM, do editor visual e da validação de
grafo. Formulários validam campos tipados e ficam aguardando submissão; ações
externas reutilizam o gate de credenciais/aprovação; encerramento é terminal;
transferência pausa para equipe/fila; landing page reutiliza o CTA seguro; e
LLM reutiliza o executor de agente com contexto/RAG e revisão configuráveis.

Evidência: `tests/services/test_flow_advanced_node_contracts.py` (`9` testes),
`frontend/src/test/flow-editor.test.ts` (`7` testes), compilação TypeScript e
verificação de sintaxe Python aprovadas. A capacidade de anexos também foi
reconciliada como nativa: autorização de upload, download autorizado,
metadados, objeto versionado e indexação no RAG do caso; produção permanece
condicionada ao antivírus real.

### Governança local de ferramentas por agente

O painel de agentes permite alterar, com permissão `agents.manage`, o conjunto
efetivo de ferramentas de cada agente sem editar código. Os vínculos são
persistidos em `agent.tool_bindings` na configuração versionada do workspace,
com `etag`, histórico de versões e evento append-only `agent_tools_changed`.

O catálogo aceito é fechado sobre as capacidades locais conhecidas. Chaves
desconhecidas e prefixos `external:`, `provider:` ou `remote:` são rejeitados;
não há execução externa no modo local. O registro e o runtime aplicam o
vínculo efetivo antes da autorização de governança: remover uma capacidade
sensível faz a autorização falhar de forma explícita, sem fallback silencioso.

Evidência: `app/services/klm_agent_service.py`,
`app/api/v1/endpoints/klm_operations.py`,
`frontend/src/klm/pages/AgentsPage.tsx` e
`tests/api/test_agent_tool_bindings.py` (`2` testes aprovados).

Validação de regressão desta continuação: suíte de agentes e vínculos locais
com `44` testes aprovados; matriz nativa de capabilities, ChatterMate e
Chatwoot com `453` testes aprovados; TypeScript e `11` testes focados do
frontend aprovados. O contrato de reconciliação de billing permanece
compatível com o payload público anterior, enquanto os tokens efetivos seguem
registrados nos campos contábeis do `AgentRun`.

### Administração e recursos contextuais no cabeçalho

As telas da operação agora exibem a gaveta `Recursos conectados` também no
`Topbar` legado, e o shell nativo não condiciona mais o botão a uma permissão
de integração. A autorização continua sendo aplicada na API e no conteúdo da
gaveta; a mudança torna a descoberta one-click consistente entre Agenda,
Conversas, Leads, Fluxos, Funil e as demais rotas, sem expor execução externa.

O painel do superadministrador já contém as superfícies nativas de políticas
mutáveis de planos e avisos gerais, com edição versionada, prévia de impacto,
rollout para contas existentes/novas/regra/data, idempotência e auditoria.
Avisos podem ser direcionados a todos, workspaces, usuários ou regras, com
inbox local e fila de e-mail local; nenhum envio externo é executado nesta
fase.

Validação adicional: `npm run typecheck` aprovado; matriz E2E desktop de
capabilities `4 passed`; sessão local confirmou `GET /api/v1/klm/agents`
respondendo `200`. O stack permanece local e sem credenciais de provedores.

### Detalhe auditável da atribuição

A aba `Rastreamento > Campanhas e criativos` ganhou uma ação de detalhe por
linha. Ela abre, sem mudar de rota, a hierarquia completa persistida: provedor,
conta, campanha, conjunto, anúncio, criativo/formulário, IDs, posicionamento,
modelo de atribuição, click IDs, landing page, fonte, primeiro/último toque,
quantidade de toques e jornadas, além da razão da evidência e dos níveis
faltantes. A interface diferencia atribuição exata, nominal e sem atribuição;
nome nunca substitui silenciosamente um ID ausente.

Validação adicional desta rodada: `npm run typecheck`; `19` testes focados de
políticas de planos, avisos do superadministrador e editor de fluxos; matriz
E2E desktop `4 passed`; `git diff --check`. Nada externo foi chamado. A
integração real com Meta/Google/TikTok, pixels, webhooks de produção e envio
de feedback continua deliberadamente bloqueada até a aprovação e credenciais
oficiais.

### QA PostgreSQL e reconciliação do scorecard

O `scripts/verify_postgresql_qa.sh --all` criou um PostgreSQL efêmero, aplicou
as migrações até `0b7c9e1a2d34`, executou downgrade e upgrade novamente, e
rodou a suíte completa com RLS. Resultado bruto: `2.636` aprovados, `1`
falha, `7` avisos. A falha era somente
`tests/scripts/test_native_architecture_superiority.py::test_committed_scorecard_matches_the_bound_source`:
o digest persistido em `app/data/native_architecture_superiority.json` estava
defasado após a atualização das evidências locais. O scorecard foi regenerado
com o gerador oficial e o teste isolado passou com `3 passed`.

O gate de superioridade arquitetural continua reportando honestamente
`source_bound_release_verification=false`, porque provas E2E/upstream/runtime
de outro digest não podem ser reaproveitadas como release atual. Isso é uma
pendência de evidência, não uma aprovação implícita. A verificação funcional
local `scripts/verify_klm_local.py --base-url http://127.0.0.1:8010` passou com
`119` checks e `externalActionsExecuted=0`.

### Reconciliação posterior das provas locais

O inventário atual foi recalculado após a inclusão dos módulos de colaboração,
custos de IA, políticas de planos, avisos administrativos, grupos Meta e
catálogo de áreas jurídicas. O digest corrente é
`f1073e53962d3eaea623d231d22a30180106d0d040d4842ad986bb50903b751e`, com
`1.459` arquivos vinculados.

Foram regeneradas com esse inventário a prova `frontend-unit` (`74` arquivos de
teste, `481` testes aprovados, sem skips) e a prova `local-runtime` (`119`
checks, `135` documentos nativos, `511` ações, zero execuções externas). O
backend também possui a execução PostgreSQL anterior documentada acima, mas
seu artefato ainda precisa ser reemitido com o digest corrente.

A execução integral de E2E foi iniciada, mas interrompida após o início da
matriz de `318` testes por ser uma suíte longa neste ambiente; não foi criada
uma prova parcial. A verificação de upstreams também foi interrompida porque o
`git status` do checkout local do Chatwoot ficou sem progresso por vários
minutos. Portanto, `verify_native_architecture_superiority.py --check` continua
corretamente bloqueado em `source_bound_release_verification`. Nenhuma dessas
pendências foi convertida em aprovação e nenhum serviço externo foi acionado.

### Fechamento da rodada de hardening E2E

As correções desta rodada foram verificadas no código local:

- `Areas`: ação `Duplicar` exposta no fluxo one-click, preservando a área
  original e abrindo a cópia no editor.
- `Conversas` mobile: a barra de ações não fica mais atrás do compositor ao
  executar ações de fluxo.
- Simulação dos agentes: teste alinhado ao endpoint nativo atual
  `/api/v1/klm/agents/simulations/intake`.
- O helper de abertura do Caso 360 foi tornado responsivo, sem selecionar
  duas instâncias montadas em diferentes breakpoints.
- A limpeza do teste E2E não relança exceção quando o contexto do navegador já
  foi fechado por timeout; a falha original continua sendo preservada.

Resultado final da matriz Playwright: `318 passed`, `0 failed`, `0 skipped`,
abrangendo desktop, tablet e mobile. A suíte unitária frontend foi reemitida
com `481 passed`, `0 failed`, `0 skipped`; lint e typecheck passaram, com os
dez avisos existentes de Fast Refresh no kit UI. O runtime local reemitido
passou `119` checks e `135` documentos/`511` ações nativas, com zero execuções
externas.

O backend ainda possui a prova funcional anterior de `2.637 passed`, porém o
manifesto final permanece deliberadamente fechado até que backend, E2E e
upstreams compartilhem um único digest atual. A atualização do digest do E2E
posterior ao ajuste de lint e a verificação do worktree Chatwoot precisam ser
concluídas antes de qualquer decisão de publicação. AWS, Meta, Google, TikTok,
WhatsApp e demais APIs permanecem desligados.

### Atualização posterior da mesma rodada

Após esse registro, as provas foram reemitidas com o digest único
`5bf15bea2a8d19b405a6aa0c15c5cb7a8125a6dca6805cdece04edc7440b13e9` e `1.459`
arquivos: backend (`2.637` aprovados), frontend unitário (`481`), frontend
E2E (`318`) e runtime local (`119` checks). O scorecard oficial foi
regenerado e `tests/scripts/test_native_architecture_superiority.py` passou
com `3 passed`.

Esse registro intermediário foi superado pela rodada final abaixo: o
artefato `upstream-locks.json` foi reemitido e o manifesto final passou.

### Diagnóstico do bloqueio do upstream

O diagnóstico acima foi uma fotografia intermediária do worktree e não é o
estado final. A verificação posterior confirmou os cinco pins OSS e concluiu
o manifesto sem ativar qualquer serviço externo.

### Fechamento definitivo da rodada

O bloqueio de integridade foi resolvido sem reverter código do usuário: foram
removidos somente artefatos de cópia identificados com sufixo `2` (`.git/index
2`, `.git/index 2.lock`, `.vendor/upstreams/rocketchat/.git/index 2` e
`app/ai/accounting 2.py`). A integridade passou isoladamente.

O inventário final é `5bf15bea2a8d19b4056aa0c15c5cb7a8125a6dca6805cdece04edc7440b13e9`
com `1.459` arquivos. As cinco provas apontam para esse mesmo digest: backend
`2.637 passed`, frontend unitário `481 passed`, Playwright `318 passed` em
desktop/tablet/mobile, runtime local `119 checks` e upstreams `7 passed` com
os cinco pins OSS verificados. O runtime foi reiniciado antes da prova para
confirmar o vínculo com o snapshot atual.

O manifesto `app/data/oss_native_verification.json` foi finalizado com `135`
experiências, `121` contratos, `511` ações, `360` comandos únicos e zero
execuções externas. O smoke pós-finalização passou com `3 passed`, p95 de
`2333 ms` e zero rede externa. O scorecard de arquitetura foi regenerado com
`12` critérios aprovados; o teste do scorecard passou com `3 passed`.

Observação de ambiente: a suíte frontend foi executada com um shim temporário
em `.local/qa-node20.cjs` para compatibilizar o Node local `24.14` com a regra
do projeto `20.19.x`; o shim não altera o produto e deve ser removido antes do
commit/release. Nenhuma API externa, AWS, Meta, Google, TikTok ou WhatsApp foi
ativada.

### Revalidação do snapshot atual - 23/07/2026

Como o processo que ocupava a porta `8010` ainda estava vinculado a um snapshot
anterior, a matriz foi reexecutada em uma segunda porta local após subir o
servidor com o código corrente. A evidência atual é:

| Verificação | Resultado |
| --- | ---: |
| `scripts/verify_klm_local.py --base-url http://127.0.0.1:8010` | `119 checks`, PASS |
| `scripts/verify_oss_native_contracts.py --base-url http://127.0.0.1:8011` | `135/135` documentos, PASS |
| Contratos de ação nativos | `511` validados, `0` falhas |
| Capacidades com contrato executável | `121` |
| Execuções externas reportadas | `0` |
| Digest do runtime atual | `832a4f36413442b1445d7d5bdfb21bb0c4e9d1e01b2ce7f962e7a091e9394108` |
| Frontend unitário | `166` suítes, `481` testes, PASS |

O processo em `8011` é apenas uma instância adicional de validação local; ele
não abre credenciais, canais ou efeitos externos. A porta `8010` permanece
preservada para o ambiente local já aberto.

### Retomada final do produto local - 23/07/2026

O ambiente foi alinhado ao snapshot corrente: o backend `8010` foi reiniciado
com o SQLite local existente e o frontend `4173` foi reiniciado com Node
`24.18.0`. O teste E2E do Caso 360 foi estabilizado para selecionar sempre a
montagem visível do painel, evitando o espelho responsivo oculto.

Provas desta retomada:

| Verificação | Resultado |
| --- | ---: |
| Caso 360 desktop | `1 passed` |
| Caso 360 mobile 393 | `1 passed` |
| TypeScript | PASS |
| Lint | PASS, `10` avisos não bloqueantes |
| Build Vite de produção | PASS, fronteira same-origin verificada |
| Backend local 8010 | saudável, SQLite local, ações externas `0` |
| Frontend | React + Vite, servido em `http://127.0.0.1:4173` |

O teste de abrangência total que ficou interrompido por instabilidade de
seletor não é tratado como prova final de cobertura total; as provas focadas
e os testes unitários continuam válidos. Nenhuma API externa ou AWS foi
ligada.
