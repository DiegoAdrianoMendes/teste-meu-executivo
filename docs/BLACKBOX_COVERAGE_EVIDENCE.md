# Blackbox — evidência de cobertura backend → frontend

Fonte de verdade operacional: `scripts/audit_blackbox_coverage.py`.

## Estados

- `parcial`: a rota administrativa aparece no contrato OpenAPI e possui consumidor no frontend; ainda exige prova própria de autorização, estados e E2E.
- `ausente`: a rota existe no backend, mas não foi encontrada numa superfície frontend.
- `nao_interativo`: webhook, callback ou operação interna agendada que não deve ganhar uma tela artificial.
- `nao_aplicavel`: compatibilidade explicitamente obsoleta, sem consumidor no Blackbox.

## Uso

```bash
.venv/bin/python scripts/audit_blackbox_coverage.py
.venv/bin/python scripts/audit_blackbox_coverage.py --json
.venv/bin/python scripts/audit_blackbox_coverage.py --strict
```

O modo `--strict` é o gate de liberação: retorna erro enquanto existir endpoint administrativo interativo sem consumidor. O relatório simples é deliberadamente não bloqueante durante a implementação e não deve ser usado como prova de cobertura integral.

## Fotografia local atualizada em 28/07/2026 — onda atual

### Auditoria canônica — inventário administrativo fechado

O inventário OpenAPI contém `149` operações não-GET sob `/api/v1/platform`:
`144` comandos, `4` consultas POST explicitamente allowlisted e `1` endpoint
legado de lote marcado como deprecated. Os `144/144` comandos possuem adapter
de snapshot; nenhum comando permanece com `snapshotAdapter: none`.

O gate central `tests/api/test_platform_mutation_registry.py` falha quando uma
nova escrita administrativa não possui descritor, quando um comando não possui
adapter ou quando uma rota tenta entrar silenciosamente na allowlist de query ou
deprecated. Essa contagem prova o contrato estrutural e não substitui os testes
de domínio, E2E ou prova de provider.

Além dos domínios já descritos abaixo, esta rodada fechou:

- Comunicados: seis comandos canônicos; o corpo é representado somente por
  tamanho e SHA-256, replay idempotente não duplica evento e falha da auditoria
  desfaz o rascunho;
- Acesso assistido: solicitação, ativação aprovada e entrada root direta usam
  snapshots sem token, sessões `support_approved` e `root_direct`, e transação
  única para estado local + auditoria;
- Continuidade da equipe: preview classificado como consulta, aplicação e
  restauração versionadas, idempotência e rollback por falha de auditoria;
- catálogos internos: rate cards Meta e pacotes comerciais de tokens com diff
  canônico e rollback local;
- WhatsApp oficial: conexão manual, Embedded Signup, ativação, desconexão e
  teste possuem snapshots sem credenciais, corpo ou telefone completo. O estado
  local e o evento são atômicos.

O sanitizador também passou a distinguir credenciais de métricas: `accessToken`
e `refreshToken` continuam redigidos, enquanto quantidade, uso, quota, limite e
custo de tokens permanecem disponíveis para o diff operacional.

Provas direcionadas desta extensão: `8 passed` em Comunicados, `14 passed` em
acesso assistido, `18 passed` em continuidade da equipe, `19 passed` em
catálogos + sanitização e `15 passed` em WhatsApp oficial e gates de entrega.
O recibo real de Meta/WhatsApp continua fora desta execução. A chamada ao
provider ocorre antes da persistência local nessas rotas legadas; portanto uma
falha posterior pode exigir compensação externa e não é tratada como rollback
do provider. A migração completa intenção → outbox → recibo observado permanece
um gate externo separado.

### Incremento Blackbox 100 — organização, tarefas e lotes 2.0

O inventário estrutural atual contém `279` operações administrativas: nenhuma
operação interativa está sem consumidor frontend e nenhum módulo RBAC está sem
superfície declarada. Esse resultado continua significando presença estrutural,
não homologação funcional automática.

Nesta onda foram acrescentados:

- responsabilidades e marcos de onboarding na Conta 360, com seletores humanos;
- assignments operacionais e revisões de publicação/configuração;
- cadastro operacional de releases no painel de governança;
- restauração de lotes por snapshot congelado, com nova prévia, bloqueio por
  alteração posterior, confirmação, job persistido e auditoria por item;
- nova tentativa somente para falhas, conflitos e itens ignorados, sempre por
  uma nova seleção e uma nova prévia;
- exportação assíncrona do resultado de lote pela Central de Downloads;
- rota `/blackbox/operations` incorporada ao smoke do shell e uma jornada E2E
  de criação, seleção sem UUID, execução e preparação de restauração.

Provas concluídas nesta execução: `12 passed` no serviço de lotes e `28 passed`
no conjunto de auditoria, lotes, dashboards, exportações, organização e
restauração. Ruff, compilação Python, typecheck da aplicação, grafo Alembic com
head único `f19a7b3c4d22`, gate de cobertura `279/0` e `git diff --check`
encerraram sem erro. O Vitest e o Playwright novos foram iniciados, mas não
concluíram sob a pressão de memória desta máquina; portanto permanecem como gate
pendente e não são contabilizados como prova verde.

O hardening subsequente encontrou e corrigiu dois defeitos transversais:

- snapshots com `datetime`, UUID, enum, decimal, sets, bytes ou objetos não
  serializáveis podiam impedir a gravação da auditoria e reverter uma mutação;
  a sanitização canônica agora normaliza tipos seguros e recusa serialização
  implícita de objetos desconhecidos;
- diferenças de timezone entre SQLite e UTC faziam a Central de Tarefas
  reprojetar itens idênticos; a comparação de timestamps agora é canônica e o
  reconciliador permanece idempotente.

Também foram removidos atalhos de incidente para uma rota inexistente. KPIs,
alertas e work items agora convergem em `/blackbox/infrastructure` e o parâmetro
`incident` destaca o registro correto. Provas adicionais: `14 passed` em
auditoria + governança, `3 passed` na Central de Tarefas, `22 passed` em busca,
inbox e lotes legados, `5 passed` nos painéis reais dos 14 papéis e `93 passed`
no gate OpenAPI de bypass do `platform_owner`. O typecheck concluiu sem erros
após alinhar os novos tipos de lookup e o contrato versionado de layout.

O fechamento do item “zero UUID interno” removeu os dois identificadores que
ainda eram digitados na sessão assistida. Caso e tarefa agora são selecionados
por rótulo humano usando somente as coleções permitidas pelo grant temporário,
pelo workspace fixo e pelos escopos de leitura correspondentes. Sem esses
escopos, a escrita fica bloqueada de forma explícita. O filtro de conta do
suporte também deixou de aceitar ID manual quando o perfil não pode consultar
clientes. O novo gate `verify:blackbox-ux` varre as superfícies operacionais e
falha se textos voltarem a solicitar UUID ou ID interno; IDs de provedores e
detalhes técnicos readonly exigem marcação explícita. Gate UX, `git diff
--check`, typecheck e o gate root com `93 passed` concluíram sem erro. O Vitest
direcionado não coletou testes neste host porque Node `24.14.1` é inferior ao
`24.18.0` exigido e o `jsdom` falhou durante a inicialização; a jornada foi
adicionada, mas permanece pendente de execução no runtime suportado.

A exportação da Central de SAC deixou de baixar o CSV síncrono legado e agora
cria um job `tickets` no motor administrativo, preservando busca, conta,
status, fila, severidade, tipo, responsável e condição de SLA dos filtros
ativos. O operador é encaminhado à Central de Downloads, onde progresso,
cancelamento, expiração, autorização de download e auditoria já são tratados.
A rota CSV antiga permanece por um ciclo apenas como `deprecated` e é
classificada `nao_aplicavel` pelo inventário, sem consumidor Blackbox. A prova
do adapter com todos os filtros e exclusão da descrição privada concluiu com
`3 passed`; o gate estrutural permaneceu `279/0`. A suíte ampliada de suporte
teve `18 passed` e uma falha preexistente na expectativa de limpeza de release
legada (`esperava 422`, recebeu `200`), fora da alteração de exportação; ela não
foi contabilizada como gate verde.

Na auditoria comercial, as cinco mutações de assinatura deixaram de confirmar a
transação antes do evento canônico. O endpoint agora captura o bundle comercial
real (`plano`, status, valor, ciclo, desconto, cortesia, trial e períodos), grava
o diff com o motivo do operador e só então permite o commit único da requisição.
O catálogo de planos também removeu o `commit` interno e passou a capturar o
plano por chave antes/depois. A prova de falha forçada do storage de auditoria
confirma rollback simultâneo da assinatura e do `SubscriptionEvent`; a prova de
catálogo confirma diff `maxUsers: 4 → 5`. Resultado: `16 passed` no Centro de
Comando de billing, `1 passed` na jornada de plano/workspace e `22 passed` no
conjunto billing + métricas.

A trilha canônica foi estendida para as duas famílias seguintes de maior risco:

- equipe interna: criação/ativação, alteração de pacote, grants, denials,
  suspensão e revogação de sessões agora compartilham a transação do evento
  canônico; alterações e encerramento de sessões exigem motivo, o snapshot inclui
  a autoridade efetiva e o `authEpoch`, e a falha forçada da auditoria reverte
  a alteração de autoridade;
- contas: criação e atualização administrativa capturam status, ativação,
  onboarding, plano, modo de setup e notas de lançamento. Alterações exigem
  justificativa na API e na interface e só são confirmadas depois do evento
  canônico; a falha forçada da auditoria mantém a conta em seu estado anterior.

As provas direcionadas concluíram com `21 passed` para staff + governança,
`12 passed` para staff isolado, `7 passed` para contas + lote legado e `12 passed`
na suíte completa do control plane. Typecheck, o gate `verify:blackbox-ux`, Ruff
direcionado e `git diff --check` permaneceram verdes. Os dois testes antigos que
simulavam root somente por flags legadas foram corrigidos para usar também o
pacote exclusivo `platform_owner`; a proteção do backend não foi afrouxada.

A gestão de usuários das contas e convites também passou para a trilha canônica
atômica. Bloqueio, reativação, pacote de acesso, revogação de sessões e
transferência de titularidade exigem motivo tanto na API quanto nas duas
superfícies operacionais (Pessoas e Conta 360). Os snapshots registram vínculo,
papel, grants, denials, estado, pacote e sessões; a titularidade usa um bundle de
todos os membros para que o diff mostre promoção e rebaixamentos na mesma ação.
Criação, reenvio e cancelamento de convite registram o ciclo de vida canônico e
continuam idempotentes. A variante administrativa de reenvio não confirma uma
expiração dentro do serviço: qualquer falha do comando ou da auditoria agora é
revertida pela transação da requisição.

As provas desta etapa concluíram com `28 passed`, incluindo rollback forçado da
mudança de autoridade e da criação de convite quando o storage de auditoria
falha. Compilação Python, typecheck, `verify:blackbox-ux`, `git diff --check` e o
gate estrutural `279/0` passaram. A prova de componente que antes estava aberta
foi repetida com o binário oficial Node `24.18.0`: Conta 360, Pessoas, Growth CRM
e Inbox concluíram com `26 passed`, e o typecheck encerrou sem erros. A falha
anterior correspondia ao Node `24.14.1` do host e não é mais usada como evidência
contra o projeto.

O Estúdio de Configuração passou pelo mesmo hardening. Criar, editar, publicar
e restaurar rascunhos agora grava evento canônico com o estado anterior e
posterior na mesma transação do documento e do alvo nativo. O snapshot inclui
namespace, tipo, chave, título, status, publicação, versão, ETag e definição;
definições acima de 16 KiB são representadas por tamanho e SHA-256 para não
duplicar conteúdo sem limite. A publicação de flows ganhou modo transacional sem
commit interno, preservando o comportamento padrão das demais APIs.

Provas: `14 passed` no Estúdio, incluindo rollback forçado tanto na criação do
rascunho quanto na publicação do flow nativo; `41 passed` na API geral de flows;
e `26 passed` em auditoria, governança e control plane. Typecheck e o gate UX
permaneceram verdes. Publicações repetidas produzem evento de no-op com
`changedPaths=[]`, em vez de inventar uma mudança.

O CRM comercial foi migrado em seguida. Criação e edição de lead, movimentação
de estágio, atividade e conversão em conta deixaram de executar commits dentro
do serviço. O evento canônico agora registra estágio, origem, responsável,
workspace convertido, valor, próxima ação, campanha, UTMs e proposta sem copiar
notas ou dados de contato. A criação de atividade usa recurso próprio. A
conversão mantém prospect, nova conta, plano, titular, vínculo e convite na mesma
transação da auditoria.

Provas: `15 passed` na API Growth, incluindo diff de estágio e falha forçada da
auditoria que reverte simultaneamente conta, convite e conversão; `19 passed` no
serviço Growth + integração com a Inbox. Compilação Python, typecheck e `git
diff --check` concluíram sem erro.

A governança interna também foi migrada para o evento canônico transacional.
Feature flags, documentos versionados de governança, incidentes e revogação de
sessões agora capturam snapshots próprios e diff no backend. A atualização de
incidente possui prova de falha forçada: se a auditoria não puder persistir, o
novo estado, a timeline e a resolução não são confirmados. A revogação de sessão
confirma a mutação e o evento antes da desconexão WebSocket, mantendo o efeito
externo posterior ao registro local. Provas direcionadas: `15 passed` no conjunto
governança + serviço de auditoria.

A Biblioteca Criativa deixou de confirmar upsert e arquivamento dentro do
serviço. Ambas as ações compartilham agora a transação da versão de configuração
e do evento canônico, com snapshot seguro da configuração e rollback integral
quando o storage de auditoria falha. Provas direcionadas: `13 passed` na API dos
Blackbox Studios, incluindo sequência de eventos, diff e falha forçada.

Durante o gate integrado foi identificado um defeito no adapter de snapshot do
Estúdio: a consulta havia sido declarada, mas o documento não era materializado,
fazendo o evento cair no identificador genérico da rota. O adapter foi completado
e a jornada de publicação voltou a comprovar o recurso correto, o diff semântico
e o no-op de republicação. Esse achado invalida qualquer contagem anterior que
tratasse apenas a presença do evento genérico como prova do snapshot canônico.

As exceções comerciais por conta foram endurecidas na mesma rodada. O conjunto
de overrides de entitlement é tratado como bundle determinístico, bloqueado para
edição, com before/after e um único evento canônico por requisição; a auditoria
legada de cada item permanece complementar. O bônus mensal de harmonização
financeira passou a exigir motivo explícito na Conta 360 e registra competência,
uso, bônus, autoria e instante da concessão antes do commit. Falha forçada da
auditoria reverte tanto o override e seus eventos legados quanto a criação ou
alteração da quota. Provas direcionadas: `13 passed` na API de Account 360,
overrides e bônus, além de typecheck direto aprovado.

O núcleo local da Inbox passou a respeitar o mesmo limite transacional. Criar
conversa, marcar como lida, classificar, atribuir, vincular conta/lead/ticket,
criar lead, abrir ticket e registrar nota ou resposta local agora produzem
eventos canônicos com recurso e snapshots seguros. O corpo da mensagem e o
endereço do contato não são duplicados na auditoria. A leitura por `GET` deixou
de zerar silenciosamente o contador; a interface chama a ação explícita
`POST /conversations/{id}/read`, que é autorizada e auditada. Repetições
idempotentes geram no-op, e falhas forçadas da auditoria revertem classificação
e nota interna. Provas direcionadas: `13 passed`, typecheck, UX sem UUID e gate
estrutural `280/0`.

O recibo real de WhatsApp, Meta e e-mail continua fora desta prova. Quando o
adaptador externo está habilitado, a confirmação observada do provider ainda
precisa convergir para o padrão intenção/outbox/recibo; portanto a atomicidade
acima comprova apenas a operação local e o estado `not_configured`, não uma
entrega externa.

O segundo executor de lote, síncrono e sem prévia, foi encerrado. A rota de
compatibilidade `/platform/workspace-bulk-actions` permanece apenas como contrato
`deprecated` e responde `428 PLATFORM-BULK-PREVIEW-REQUIRED` para o root; nenhum
registro é alterado. O consumidor frontend órfão foi removido. Toda operação em
massa iniciada pelo Blackbox passa, portanto, por seleção materializada, prévia e
job persistido do motor 2.0. A proteção foi comprovada junto da suíte do motor com
`14 passed`; o gate OpenAPI permaneceu em `280/0` e classificou a rota antiga como
`nao_aplicavel`.

### Fotografia anterior preservada para rastreabilidade

- 226 operações administrativas registradas no OpenAPI;
- 224 operações com consumidor frontend localizado e classificadas como `parcial`;
- 2 operações internas classificadas como `nao_interativo`;
- 0 operações interativas sem consumidor localizado;
- 0 módulos RBAC sem superfície própria ou compartilhada declarada.

O gate estrutural de presença está verde. As operações continuam `parcial` até que cada
jornada tenha autorização, estados de interface, teste E2E e evidência por ambiente. O
inventário não substitui revisão visual, homologação ou prova de produção.

### Fotografia histórica do lote síncrono substituído

Esta seção preserva a primeira implementação para rastreabilidade, mas não
descreve mais a rota operacional vigente. O comando síncrono permitia suspender,
reativar, concluir ou reabrir onboarding e alterar plano em até 100 contas por
lote. Ele deduplicava IDs, exigia justificativa, apresentava resultado individual
e preservava sucesso parcial. Cada lote gerava um evento de
segurança pesquisável com ação, motivo, contas afetadas e contadores.

A Conta 360 passou a iniciar a sessão `root_direct` pela própria ficha da conta,
mantendo o ator real e o banner administrativo já implementados. A timeline de
auditoria agora normaliza módulo, ação, workspace, recurso, correlação, motivo,
resultado e diff antes/depois quando esses campos existem, mantendo o contexto
técnico completo expansível.

Provas direcionadas desta onda: `4 passed` no contrato backend do lote, incluindo
falha parcial e negação explícita ao `platform_admin`; `12 passed` no conjunto
lote + governança; `9 passed` na Conta 360; typecheck direto e lint direcionado
sem erros. O gate estrutural foi reexecutado com `226` operações e `0` ausentes.
O script oficial de npm continua bloqueando comandos compostos nesta máquina
porque o runtime disponível é Node `24.14.x`, abaixo do `24.18.0` fixado pelo
projeto; a verificação TypeScript e o Vitest foram executados diretamente sem
alterar ou mascarar esse requisito.

### Prova integrada da superfície Blackbox

Execução local isolada em `27/07/2026`, com backend real em `127.0.0.1:8011`,
frontend Blackbox em `127.0.0.1:4175` e sessão `platform_owner` obtida pela API:

```bash
KLM_E2E_PRODUCTION=1 \
KLM_E2E_PLATFORM=1 \
KLM_E2E_EXTERNAL_SERVER=1 \
KLM_API_URL=http://127.0.0.1:8011 \
KLM_FRONTEND_URL=http://127.0.0.1:4175 \
node_modules/.bin/playwright test e2e/blackbox.spec.ts --project=desktop-1440
```

Resultado: `41 passed` em `18.7s`. A suíte percorreu as 40 rotas declaradas no
shell, exigiu resposta HTTP abaixo de 400, identidade visual renderizada, ausência
de bloqueio de módulo, ausência de erro de JavaScript, ausência de overflow
horizontal e rolagem interna funcional. A 41ª prova confirmou que `/app` não é
servido pelo bundle interno.

Na rodada consolidada, foram aprovados `238` testes backend direcionados à
plataforma, autenticação e suporte e a suíte frontend integral, com `142` arquivos
e `820` testes. Typecheck, lint sem erros, builds isolados de `/app` e `/blackbox`,
verificação dos dois bundles e o gate estrito de cobertura com `0` endpoint
interativo ausente também passaram. Esses números são uma prova local
reproduzível, não uma homologação de providers nem uma liberação de produção.

### Fechamento do bloqueio de MFA da Blackbox

O bloqueio histórico que devolvia `AUTH-BLACKBOX-MFA-NOT-READY` para qualquer
funcionário em produção foi removido. A política atual é fail-closed por pessoa:

- funcionário da plataforma sem MFA recebe
  `AUTH-BLACKBOX-MFA-ENROLLMENT-REQUIRED` e nenhuma sessão;
- funcionário com MFA recebe um challenge TOTP e somente recebe a sessão após a
  segunda etapa;
- o segredo TOTP é criptografado em repouso com chave dedicada;
- o código aceita janela limitada, registra tentativas e impede reutilização do
  mesmo contador;
- o segredo e o URI `otpauth://` aparecem somente durante a habilitação;
- códigos de recuperação continuam de uso único;
- migração `c3e6a0d4b982` adiciona o segredo criptografado e o contador antirreplay.

Provas direcionadas: login Blackbox TOTP em perfil de produção, habilitação TOTP,
ausência de segredo no challenge de login, recuperação, sessão administrativa,
grafo Alembic com head único e renderização do provisionamento no frontend.
Resultado desta rodada: `10` testes backend direcionados e `4` testes do painel
de segurança aprovados, além do typecheck.

### Gestão explícita de incidentes

Além dos incidentes inferidos pelas observações de status, o Blackbox agora possui
registro operacional persistente para abrir, priorizar, acompanhar e resolver
incidentes. Cada registro contém severidade `SEV1` a `SEV4`, estado, componentes,
responsável, autoria, timeline e data de resolução. As mutações usam o módulo
`security:manage`, preservam o bypass exclusivo do root e entram na auditoria
global. A migração `d4f7b1e5c093` cria a persistência correspondente.

As três novas operações administrativas possuem consumidor no painel de
Infraestrutura. A jornada backend de abertura, monitoramento, resolução e
auditoria e a jornada frontend de abertura/resolução foram aprovadas.

### Prova gerada do bypass exclusivo do Super Admin

`tests/api/test_platform_root_surface.py` deriva do OpenAPI todas as coleções
administrativas de leitura sem parâmetros de caminho e as executa com uma sessão
real `platform_owner`. Isso evita manter uma lista manual que possa divergir das
rotas registradas. Em `27/07/2026`, o gate percorreu `80` operações e confirmou
que nenhuma respondeu `401` ou `403`; a própria coleta também exige a presença do
manifesto da sessão e da gestão de incidentes. Resultado: `80 passed` em `43.07s`.

Esse gate prova o bypass root nas superfícies GET catalogadas. As mutações seguem
com provas de autorização específicas por jornada, porque executá-las em massa
sem dados válidos produziria efeitos colaterais e não provaria o contrato de cada
comando.

## Gates de evidência

| Gate | Evidência mínima | Estado |
|---|---|---|
| Local integrado | API real, PostgreSQL/RLS, persistência, RBAC, testes backend/frontend e E2E | Aprovado nesta onda |
| Homologação real | provedores sandbox/oficiais, webhooks públicos e rollback | Não executado |
| Produção real | smoke controlado e eventos reais por integração | Não executado |

Nenhuma integração externa ou capacidade de produção deve ser considerada comprovada apenas porque sua rota, tela ou configuração existe.

O gate PostgreSQL foi executado com `scripts/verify_postgresql_qa.sh` em um
PostgreSQL 15 efêmero. A cadeia completa de migrations chegou ao head
`d4f7b1e5c093`, passou por downgrade/upgrade e foi validada com o papel runtime e
RLS habilitado. Resultado: `34 passed`, `0 skipped`, `0 failures`; o JUnit foi
preservado em `.local/qa-evidence/postgresql-qa/junit.xml`.

### Hardening encontrado pela suíte ampliada

A ampliação do gate além do recorte Blackbox encontrou e corrigiu quatro falhas
que poderiam distorcer a operação administrativa:

- fixtures HTTP agora reproduzem commit/rollback por requisição, impedindo que um
  `403` ou `404` posterior reverta uma mutação anterior no mesmo teste;
- relatórios financeiros usam a data UTC contra timestamps UTC, evitando KPIs
  zerados na transição do dia em fusos brasileiros;
- checkout Stripe só materializa a assinatura local depois que o gate externo e
  o provider aceitam a criação da sessão;
- falhas retryable do webhook Stripe não preservam recibos `processing` no banco
  local, enquanto PostgreSQL mantém a proteção concorrente por savepoint.

A inbox oficial também foi registrada explicitamente como tabela do plano de
controle: seu `workspace_id` é vínculo opcional com a conta convertida, não
propriedade tenant. O registro exige RBAC de plataforma e controles do ingresso
público. Provas direcionadas após as correções: `63 passed`; inventário SQLite de
RLS: `10 passed`; PostgreSQL/RLS: `34 passed`, sem skips.

A suíte de serviços foi executada integralmente em três lotes determinísticos:
`380 passed`, `1034 passed` e `585 passed`, totalizando `1999 passed` sem
falhas. Os conjuntos de scripts, redistribuição de funcionários, suporte e
workers acrescentaram `331 passed` sem falhas. A divisão em lotes evita que uma
interrupção do processo monolítico seja registrada indevidamente como aprovação
da suíte completa; os resultados acima correspondem apenas a processos que
chegaram ao exit code `0`.

O restante da suíte backend também foi concluído por partições sem sobreposição:
`727 passed` nos módulos de agentes, IA, core, banco SQLite, evals, integrações,
carga, MCP, migrations, OSS, pagamentos, runtime e schemas; `1125 passed` na
suíte integral de API; e `117 passed` nos testes de raiz. Assim, o gate backend
local totaliza `4299 passed` nos processos gerais, além dos `34 passed` do gate
PostgreSQL/RLS isolado. Os dez arquivos que exigem URLs administrativas/runtime
do PostgreSQL foram executados somente pelo provisionador oficial do projeto e
não foram contados novamente no lote genérico.

O frontend foi validado com o Node `24.18.0` exigido por `.nvmrc`: `142` arquivos
Vitest e `820` testes passaram, o typecheck encerrou sem erros e o lint encerrou
com `0 errors` (`36 warnings` não bloqueantes já catalogáveis). Os bundles
isolados de `/app` e `/blackbox` foram gerados fora da árvore versionada e
aprovados pelo verificador de produção como same-origin e sem dados dev/test.

O E2E integrado do Blackbox executou `41 passed` contra API local real, sessão
`platform_owner`, manifesto de capacidades e proxy same-origin. A matriz abriu
as 40 rotas administrativas sem bloqueio de permissão, erro de console ou
overflow horizontal, validou o WebSocket autenticado do suporte e confirmou que
`/app` não é servido pelo bundle interno. O `playwright.config.ts` mantém agora
projetos distintos para workspace e Blackbox, e o alvo do proxy Vite pode ser
isolado por `KLM_DEV_API_TARGET` sem alterar seu padrão local.

## Fechamento local integrado atual — 28/07/2026

Esta seção substitui, para fins de estado atual, as contagens históricas das
seções anteriores sem apagar a trilha de execução. O gate local mais recente
confirmou:

- frontend no Node exato `24.18.0`: `146` arquivos Vitest e `836` testes;
- typecheck, gate de UUID técnico, build isolado do Blackbox e verificador do
  bundle de produção aprovados;
- cobertura estrutural OpenAPI para frontend: `280` operações catalogadas e
  `0` ausentes; a rota síncrona legada de lote está marcada como não aplicável;
- recorte backend Blackbox: `176 passed`;
- compatibilidade do schema SQLite local: `2 passed`, incluindo evolução
  idempotente de MFA e do enum de entitlements sem perda de linhas;
- E2E real de browser: `60 passed` numa execução combinada de smoke, 14 papéis e
  operações, mais `1 passed` específico de acessibilidade da Central de
  Comandos;
- os 14 papéis receberam sessão própria, painel correspondente, KPI e fila
  reais, ação permitida, prova de bloqueio direto da API e verificação de
  ausência de conteúdo privado na home;
- a criação real de workspace voltou a responder `201` depois da evolução do
  CHECK de entitlements e a leitura de MFA respondeu `200` depois da adição das
  colunas locais legadas.

O ambiente integrado desta prova usou frontend em `127.0.0.1:3000`, API em
`127.0.0.1:8011`, Redis isolado em `127.0.0.1:16379` e storage privado local. A
execução do lote utilizou um harness compatível com o serviço do worker,
processando o mesmo job persistido pelo `PlatformBulkService`. Em uma prova
separada, o processo Celery real iniciou com pool `solo`, conectou ao mesmo Redis,
registrou as tasks de lote, exportação e restauração, declarou `ready` e respondeu
`pong` ao controle remoto. Em seguida, a task de produção
`platform_bulk_tasks.recover` foi enviada pelo broker contra um SQLite descartável
e vazio e terminou em `SUCCESS` com resultado `0`. A rodada E2E de lote não deve
ser apresentada como execução pelo processo Celery, mas boot, conectividade e
execução de task do runtime distribuído local deixaram de ser pendência.

Também permanecem fora desta prova as ações e comprovações dependentes de Meta,
Google, SMTP, billing provider ou outros provedores externos. O estado atual é
local integrado e funcional dentro desse recorte, não homologação externa nem
produção.

## Correção da cobertura operacional — auditoria canônica por mutação

O inventário OpenAPI → frontend acima é um gate de presença, não uma declaração
de operação integral. A auditoria quantitativa dos descritores de mutação
encontrou inicialmente `103` rotas interativas com `snapshotAdapter=none`.
Depois do domínio Site, esse total caiu para `97`. Nesta continuação, os `17`
comandos do Blog passaram a possuir adapter explícito; o inventário atual contém
`150` mutações administrativas, `69` com snapshot canônico declarado e `81`
ainda sem adapter. Portanto, o Blackbox ainda não atingiu a meta de 100%.

### Site público

As seis mutações de configurações, home, FAQ, mídia, documentos legais e
publicação agora:

- exigem motivo normalizado de 8 a 500 caracteres;
- transportam o motivo percent-encoded no header `X-Operation-Reason`, evitando
  conteúdo operacional em URLs e access logs;
- capturam before/after sanitizado e diff semântico;
- gravam mutação e evento canônico na mesma transação;
- retornam rótulo e versão do recurso no Audit Center;
- revertem a mutação quando a persistência da auditoria falha.

Provas desta continuação: `11 passed` no recorte inicial de Site e auditoria,
teste de transporte frontend aprovado e typecheck aprovado.

### Blog e operação editorial

Os 17 comandos internos do domínio Blog agora declaram adapters para
`blog_post`, `public_site_document`, `site_media_asset`,
`google_business_connection` ou `google_business_publish_job`. Posts receberam
revisão monotônica, com migration `f2ab4c6d8e30` e evolução idempotente do SQLite
local. Criação, edição, publicação, agendamento, arquivamento, duplicação, SEO,
assistência editorial, geração local de capa, documentos globais, mídia,
configuração do Google Business e intenção de publicação exigem motivo e geram
evento canônico atribuído ao operador real.

O frontend não habilita essas mutações sem motivo válido. A tela do Google deixa
explícito que configuração e intenção são auditadas, enquanto a entrega externa
continua dependente do provider e não é considerada comprovada.

Provas locais desta continuação:

- `12 passed` para Blog, Site, versionamento, diff e rollback atômico;
- `10 passed` para head Alembic único e compatibilidade SQLite;
- `2 passed` no contrato de transporte frontend;
- typecheck frontend aprovado;
- gate de UUID técnico aprovado;
- auditor estrutural reexecutado com `280` operações e `0` ausentes.

### Suporte e Inbox omnichannel

As seis mutações antes descobertas no núcleo de Suporte passaram a declarar e
produzir snapshots canônicos para configurações de disponibilidade e
escalonamento, emissão segura de ticket WebSocket, criação administrativa de
ticket, filtros pessoais e incorporação do legado técnico. Motivo digitado é
obrigatório para configuração, criação de ticket e migração em massa; operações
rotineiras usam motivo de sistema identificado. O replay idempotente de ticket
é registrado como no-op (`before == after`) e não como nova criação.

As seis mutações da Inbox já possuíam snapshots e atomicidade no comando, mas
continuavam classificadas como `none` no descritor OpenAPI. O registro agora
expõe `platform_inbox_conversation` ou `platform_inbox_message`, em convergência
com os eventos efetivamente persistidos. A jornada local comprova criação,
replay, leitura, triagem, mensagem, conversão em lead/ticket, rollback quando a
auditoria falha e ausência de entrega externa simulada.

Provas locais adicionais:

- `28 passed` no recorte completo de ticket, escalonamento e WebSocket de
  Suporte, incluindo atomicidade e contratos OpenAPI;
- `14 passed` na Inbox, incluindo jornada integral e dois rollbacks atômicos;
- `3 passed` nos contratos frontend sem DOM para Site/Blog e configurações do
  Suporte;
- typecheck e gate de UUID técnico aprovados;
- o teste UI de migração foi atualizado para exigir motivo, mas sua execução
  local está bloqueada antes da coleta por incompatibilidade instalada entre
  Node 24 e `jsdom@20`; o lint isolado também não inicia por incompatibilidade
  instalada entre ESLint e Ajv. Esses dois resultados não são contabilizados
  como prova verde.

O inventário OpenAPI atual normalizado contém `149` mutações administrativas:
`81` possuem adapter canônico declarado e `68` ainda usam `none`. A diferença de
uma operação em relação à leitura anterior decorre do OpenAPI regenerado do
checkout atual e não é convertida artificialmente em cobertura.

Próxima prioridade quantitativa: remover os `68` adapters ausentes, começando
por benefícios (`11`), organização (`8`), lotes (`7`), governança (`6`) e
comunicados (`6`).

## Fechamento da cobertura estrutural de mutações — 28/07/2026

Esta seção substitui a prioridade quantitativa imediatamente anterior. O
registro canônico foi concluído para o OpenAPI atual do plano de controle:

- `149` operações não-GET registradas;
- `144` comandos administrativos interativos;
- `4` consultas POST deliberadamente não mutantes (`search/query`, dois
  lookups e preview de redistribuição);
- `1` rota síncrona legada de lote explicitamente deprecated;
- `144/144` comandos com `snapshotAdapter` diferente de `none`;
- `0` comando administrativo novo fora do gate automatizado.

O gate em `tests/api/test_platform_mutation_registry.py` falha quando uma nova
operação de plataforma não é classificada, quando um comando permanece sem
adapter ou quando uma rota root deixa de respeitar a exclusividade de
`platform_owner`. Essa cobertura comprova contrato e instrumentação; não
substitui o teste transacional e E2E de cada jornada.

Os últimos domínios incorporados foram comunicados, acesso assistido e entrada
root direta, redistribuição de funcionários, rate cards Meta, pacotes de tokens
de IA e WhatsApp oficial. Os snapshots do WhatsApp não contêm token, telefone
integral, destinatário ou corpo de mensagem. Efeitos já aceitos pelo provedor
antes de uma falha local continuam exigindo outbox, recibo observado e ação
compensatória; não são apresentados como atomicidade externa.

Provas locais desta etapa:

- regressão integrada backend: `145 passed`;
- sessão Blackbox isolada: `28 passed`, após atualização da fixture para o
  estado RBAC válido `platform_admin`;
- cobertura OpenAPI → frontend: `280` operações e `0` ausentes;
- typecheck frontend aprovado;
- gate de UX aprovado, sem UUID interno solicitado ao operador;
- lint do código-fonte, E2E e configurações: `0 errors` e `37 warnings`
  preexistentes não bloqueantes;
- recorte Vitest Blackbox: `5` arquivos e `18 passed`.

O runtime de testes frontend foi corrigido para o Node `24.18.0`: `jsdom@20`,
incompatível com esse runtime, foi substituído por `jsdom@30`; o Vitest agora
usa o mesmo adapter de `react-remove-scroll` do bundle; e o carregador CJS
experimental foi desabilitado explicitamente apenas no comando do ESLint. O
Vitest foi atualizado para a linha corrigida `3.2.7`, removendo a vulnerabilidade
crítica do servidor UI presente antes de `3.2.6`.

O `npm audit` atual não contém vulnerabilidade crítica e ainda reporta `9`
achados (`3` moderados e `6` altos) ligados a atualizações major de Vite, ESLint
e React Router. Eles não foram mascarados por `--force`: exigem uma onda própria
de migração e regressão. A suíte Vitest integral não é contabilizada nesta
rodada, porque a máquina entrou em forte compressão de memória e o sample do
worker mostrou bloqueio síncrono em leitura de arquivo; o processo foi
interrompido, enquanto o recorte Blackbox terminou normalmente.

O worktree também possui um pack Git preexistente truncado
(`pack-2ff1cbc27e1240a19be52366b6aeb0036eac991b.pack`). Por isso, `git diff` e
`git diff --check` não são evidência disponível até uma reparação explícita do
repositório; nenhum objeto foi removido ou reescrito nesta execução.

## Restauração de vínculos e diff semântico — 28/07/2026

O motor de restauração contextual passou a reconhecer o recurso canônico
`customer_membership`, usado pelas mutações de bloqueio e pacote de acesso dos
usuários das contas. A prévia e a execução tratam como bundle único o papel, o
status, o pacote, os grants e os denials. A restauração revalida a existência e
a compatibilidade do pacote, impede a remoção do último titular ativo, incrementa
o `authEpoch`, revoga as sessões do usuário dentro do workspace afetado e grava
um novo evento auditado ligado ao evento de origem. Revogação de sessão e
transferência de titularidade permanecem deliberadamente não restauráveis.

O Audit Center agora:

- oferece restauração para eventos de `customer_membership` autorizados;
- pagina pela chave opaca `(occurred_at, id)` fornecida pelo backend;
- distingue a quantidade da página de um total inexistente;
- recebe um `fieldDiffs` semântico calculado no backend, com caminho, rótulo,
  tipo da alteração e indicação de campo sensível;
- mantém qualquer segredo somente como metadado redigido, inclusive no diff.

Também foram removidas duas dependências de listas truncadas de contas no
frontend: o filtro do Estúdio de Desempenho usa lookup pesquisável server-side,
e os nomes de contas no Estúdio de Benefícios são resolvidos em lote. Um registro
fora dos primeiros 100/200 resultados deixa de aparecer como UUID para o
operador.

Provas locais desta continuação:

- `16 passed` no recorte de auditoria, restauração, APIs e registro de mutações;
- `9 passed` no recorte específico anterior de restauração e contrato OpenAPI;
- `6` arquivos e `24 passed` no recorte frontend Blackbox;
- regressão ampla de serviços e APIs de plataforma: `280 passed`;
- typecheck e lint dos arquivos alterados aprovados;
- gate de UX aprovado, sem identificador interno solicitado ao operador;
- auditor estrutural: `280` operações e `0` ausentes.

### Fechamento adicional da trilha de auditoria e runtime local

O contrato HTTP da auditoria passou a expor filtros tipados para módulo,
resultado, conta, ator, papel, ação, tipo de recurso, `correlationId`,
`operationId`, período e restaurabilidade. A decisão `restorable` deixou de ser
inferida no navegador: é calculada pelo backend a partir do tipo suportado, do
resultado, do snapshot anterior e da natureza da ação. Revogação de sessão,
transferência de titularidade e convites continuam não restauráveis.

Foi adicionada uma jornada Playwright integrada para a sequência:

```text
criar conta
→ alterar descrição com motivo
→ localizar evento no Audit Center
→ conferir diff semântico
→ gerar prévia de restauração
→ executar restauração
→ conferir novo evento canônico
```

A jornada foi executada contra frontend, API, Redis e Celery isolados. O recorte
`@operations` comprovou exportação assíncrona, lote com prévia e restauração,
sucesso parcial por conflito concorrente, exportação do resultado parcial e
restauração somente do item compatível, além da restauração contextual pelo
Audit Center. O conjunto Blackbox desktop completo concluiu com `63 passed`,
incluindo:

- as 43 rotas root do backoffice;
- isolamento do bundle interno;
- Central de Comandos por teclado;
- os 14 painéis por papel, com decisão positiva e negativa de autorização;
- exportação, lote, conflito parcial e restauração processados pelo worker real.

O primeiro ensaio isolado usou uma porta que não integra a allowlist local de
origens e revelou corretamente um handshake WebSocket `403`. A prova final foi
repetida em origem local autorizada, incluindo o canal em tempo real de suporte,
sem flexibilizar CORS para acomodar o teste.

O servidor SQLite local também deixou de usar `NullPool` fora de testes. Sob o
processo antigo, o backend acumulou dezenas de descritores do banco e passou a
responder `500` com `sqlite3.OperationalError: unable to open database file`.
O runtime corrigido usa `AsyncAdaptedQueuePool` limitado a 5 conexões e 5 de
overflow. A prova isolada executou `150` sessões de plataforma, com `0` falhas e
`4` descritores do banco ao final. Dois testes de regressão garantem que:

- servidor SQLite local usa `AsyncAdaptedQueuePool`;
- execução explícita de testes preserva `NullPool`, necessário para isolamento
  entre event loops.

Provas adicionais:

- auditoria, restauração, busca e registro de mutações: `22 passed`;
- estratégia de pool: `2 passed`;
- frontend Blackbox focal: `6` arquivos e `20 passed`;
- browser E2E integrado: `63 passed` em uma execução completa;
- cancelamento determinístico com worker pausado: `1 passed` adicional, provando
  transição `queued -> cancelled` e repetição idempotente do comando; esta
  prova roda separadamente porque as demais jornadas de operações exigem o
  worker ativo;
- paginação e seleção global: `1 passed` adicional com `251` contas isoladas
  por `runId`, páginas de `100`, estado de filtro/página na URL, promoção da
  página para todos os resultados, duas exclusões, snapshot materializado de
  `249` alvos e confirmação antes de invalidar a seleção ao trocar o filtro;
- typecheck aprovado;
- lint com `0` erros e avisos preexistentes;
- gate de UX aprovado: nenhum UUID interno solicitado;
- `npm audit`: `9` achados (`3` moderados, `6` altos, `0` críticos), todos os
  reparos restantes exigem migração major de Vite/ESLint ou atualização
  estrutural do React Router; nenhum `--force` foi aplicado.

O build Blackbox transformou `2.630` módulos após a reparação de pacotes locais
que estavam materializados como arquivos vazios. A emissão final ainda fica
bloqueada pelo estado do filesystem: sete mídias em `frontend/public/site-media`
estão marcadas pelo macOS como `dataless`, e o Vite aguarda indefinidamente ao
copiar a primeira delas. Isso é registrado como bloqueio de hidratação local,
não como build aprovado e tampouco como falha funcional do Blackbox.

### Escala de contas, fila integral e exportações privadas

A listagem paginada de contas deixou de montar o resumo completo de consumo e
quotas para cada linha. O plano compacto é obtido das relações carregadas em
lote e expõe somente `tier`, nome e estado da assinatura; o uso detalhado
continua na Conta 360. Um teste com 25 contas assinantes limita a página inteira
a no máximo sete consultas SQL, eliminando o N+1 sem converter uso ausente em
zero.

A Central de Tarefas passou a ter prova idempotente das nove projeções internas
que implementam as oito categorias do plano (convites de conta e de funcionário
são fontes distintas): ticket, incidente, acesso assistido, onboarding,
cobrança, os dois convites, publicação e configuração. A segunda reconciliação
não cria nem atualiza itens, e os snapshots seguros continuam sem corpo de
ticket, documento, contexto técnico, segredo de configuração ou corpo editorial.

As exportações administrativas ganharam testes de ciclo de vida no contrato
HTTP: arquivo vencido retorna `410`, é apagado do storage privado e produz
evento canônico `expired`; uma permissão de exportação revogada depois da
geração impede o download já pronto com `403`. A suíte de serviço mantém a prova
de neutralização de fórmulas CSV e de exclusão da descrição privada do ticket.

Provas focais desta etapa:

- paginação/escala de contas e Central de Tarefas: `6 passed`;
- ciclo de vida HTTP e serviço de exportação: `8 passed`;
- reconciliação das nove fontes operacionais: `5 passed`;
- typecheck frontend aprovado.

### Fechamento de autoridade, restauração e jornadas por papel

O catálogo de exportações passou a aceitar a equivalência prevista no contrato
para cobrança: `billing:export` **ou** `finance:export`. A mesma decisão é usada
na API, no worker e na superfície de seleção; retirar a permissão depois da
geração continua bloqueando o download. Nenhum outro dataset herdou essa
equivalência.

A restauração de autoridade agora revoga as sessões ativas ao restaurar bundles
de funcionário ou de vínculo de cliente. O comportamento foi comprovado para
`staff_authority`, `customer_membership`, catálogo de plano, termos comerciais
locais e configuração versionada. Termos comerciais restaurados não simulam
reconciliação nem efeito no provedor externo. O sanitizador de auditoria também
foi corrigido para distinguir métricas como `maxTokensPerMonth` de credenciais:
limites de produto permanecem restauráveis, enquanto `accessToken` e
`refreshToken` continuam redigidos.

As ações rápidas dos painéis de Compliance e Produto foram alinhadas às rotas
canônicas `/blackbox/governance/security` e
`/blackbox/governance/feature_flags`. Um teste de regressão impede a volta dos
atalhos inexistentes anteriores.

Provas locais consolidadas desta rodada:

- regressão de serviços e APIs de plataforma: `290 passed`;
- auditoria e restauração focal: `16 passed`;
- exportações focal: `9 passed`;
- frontend Blackbox: `10` arquivos e `41 passed`;
- jornadas de painel: `14 passed`, uma por papel, cada uma comprovando KPIs,
  fila, saúde das fontes, ação autorizada navegável, API proibida com `403` e
  ausência de conteúdo privado;
- auditor estrutural OpenAPI → frontend: `280` operações e `0` ausentes;
- gate de UX: nenhum UUID interno solicitado ao operador;
- typecheck frontend aprovado.

As métricas acima são provas locais. Integrações externas continuam marcadas
como não configuradas ou não comprovadas, conforme a exclusão expressa do plano.

O bloqueio de build descrito anteriormente foi eliminado para a superfície
interna. O build de plataforma agora copia apenas os ativos públicos necessários
ao Blackbox e à identidade Meu Executivo, excluindo `site-media` e
`proposal-media`, que pertencem às superfícies públicas e continham arquivos
`dataless` no macOS. O bundle continua incluindo wordmark, marca, favicons,
manifest e logos, e o verificador de produção comprovou fronteira interna,
same-origin e ausência de fixtures de desenvolvimento.

Prova final desta continuação:

- build Blackbox de produção: `2.630` módulos transformados, concluído e
  verificado por `verify:bundle:blackbox`;
- browser E2E integrado com roles, operações e paginação: `64 passed` e `1`
  skip deliberado do cancelamento, cuja prova determinística exige o worker
  pausado e já é executada separadamente;
- cancelamento enfileirado: `1 passed` isolado, com repetição idempotente;
- typecheck, lint e gate de UUID aprovados após a separação dos ativos públicos.

### Atualização controlada do runtime frontend

Vite, o plugin React, Vitest e React Router foram atualizados em conjunto para
as linhas compatíveis com Node `24.18.0`. Isso removeu os achados do servidor de
desenvolvimento Vite/esbuild e os avisos anteriores de open redirect do Router.
O build Blackbox no runtime novo transformou `2.399` módulos e passou novamente
no verificador de produção. A migração também passou no typecheck, lint, nos
testes focais Blackbox e na repetição integral de `64 passed` no browser.

O ESLint 10 foi avaliado e descartado desta migração: além de não encerrar de
forma confiável neste checkout, a nova configuração recomendada ativou 211
erros de regras React que exigem refatoração transversal fora do Blackbox. O
lint funcional permanece no ESLint 9, com `0 errors` e `36 warnings` já
identificados. O audit passou de 9 achados para 7, sem severidades moderadas ou
críticas: cinco pertencem exclusivamente à cadeia de desenvolvimento do ESLint
9 e dois ao advisory de modo RSC do React Router. A aplicação usa
`BrowserRouter` client-side e não contém `ServerRouter`, `HydratedRouter`,
`StaticRouterProvider` ou handler RSC; essa limitação arquitetural foi
confirmada no código, mas o advisory continua registrado, não suprimido.

A suíte Vitest completa foi tentada em um único worker. Ela não é contabilizada
como verde porque o filesystem ainda possui `6.980` arquivos `dataless` dentro
de `node_modules`; as leituras bloquearam sucessivamente em dependências que não
fazem parte dos recortes Blackbox. Os pacotes necessários ao lint, build e aos
testes focais foram materializados sem alterar código-fonte. Permanecem como
provas válidas no runtime atualizado: `22 passed` nos sete arquivos focais,
build verificado e `64 passed` no E2E integrado.

### Busca global convertida em Central de Comandos operacional

A auditoria de continuidade identificou que a busca global já consultava as
sete entidades principais com isolamento por permissão, mas o bloco chamado de
“comandos” ainda era composto por quatro links estáticos. Essa lacuna foi
fechada com um catálogo tipado e filtrado no backend em
`GET /api/v1/platform/commands/catalog`. O catálogo não oferece executor
genérico: cada mutação rápida chama uma rota de domínio existente, que revalida
o RBAC e produz sua própria auditoria canônica.

Os resultados agora carregam `allowedCommands` calculados pelo backend e os
metadados mínimos necessários à operação, incluindo a versão otimista do
ticket. A Central executa, com confirmação, atribuição de ticket, lead,
conversa e incidente. O comando root de conta ativa a sessão `root_direct`,
exige motivo digitado, preserva a identidade do Super Admin e encaminha para o
workspace com o banner de acesso direto; `platform_admin` não recebe esse
comando.

Provas desta etapa:

- autorização e busca no backend: `9 passed`;
- regressão conjunta de busca, acesso assistido e dashboards: `29 passed`;
- serviço frontend de comandos e acesso: `3` arquivos e `48 passed`;
- browser E2E: catálogo autorizado, ação rápida real de assumir lead e acesso
  root direto com motivo e banner, `3 passed`;
- typecheck e lint focal aprovados;
- auditor estrutural atualizado: `281` operações e `0` ausentes.

As ações externas continuam fora desta prova. O catálogo apresenta apenas
ações internas autorizadas; nenhum comando genérico ou chamada a provedor foi
introduzido.

### Paginação real da equipe e dos convites internos

A lista de funcionários deixou de depender da leitura integral do navegador.
`GET /api/v1/platform/staff/page` agora aplica busca e filtros no servidor,
retorna total real, ordenação determinística, `offset`, `limit` e `hasMore`.
A contagem de sessões ativas e o último acesso são agregados em uma única
consulta para os funcionários da página, eliminando o N+1 dessa superfície. A
tela da Equipe usa páginas de 50 registros e diferencia resultado vazio de
falha da fonte.

Os convites internos receberam contrato equivalente em
`GET /api/v1/platform/organization/staff-invitations/page`, com busca, status,
total real e desempate por ID. O cartão operacional solicita apenas convites
pendentes e pagina cinco por vez; não chama mais a coleção limitada a 200 para
representar o total.

O vínculo de gestor e membro também deixou de carregar a coleção legada de
funcionários. Ambos usam o lookup pesquisável, paginado e autorizado; para essa
finalidade o backend exclui o `platform_owner` e identidades inativas, inclusive
quando a API de lookup é chamada diretamente.

Provas desta etapa:

- paginação de funcionários com 55 registros, filtros combinados e páginas
  disjuntas: `2 passed` no recorte de equipe;
- paginação de convites com 52 pendentes entre 55 registros: `2 passed` no
  recorte de organização;
- contratos frontend paginados: `3 passed` em dois arquivos de serviço;
- typecheck e lint: `0 errors`; os `36 warnings` preexistentes permanecem
  registrados;
- gate de UX: nenhum identificador interno solicitado ao operador;
- browser integrado da rota e jornada do seletor pesquisável, com prova de que
  `/platform/staff` não foi chamado: `2 passed`;
- auditor estrutural: `283` operações e `0` ausentes.

Este fechamento não transforma automaticamente as demais coleções em listas
paginadas. Benefícios, cupons e outras coleções ainda precisam ser avaliados
individualmente pelo critério de lista primária antes de declarar o item 6 em
100%.

### Paginação real de benefícios, razão e cupons

As quatro coleções que alimentam a operação diária dos Studios deixaram os
limites implícitos ou a leitura integral:

- concessões: `GET /platform/saas-benefits/grants/page`;
- razão imutável: `GET /platform/saas-benefits/ledger/page`;
- catálogo de cupons: `GET /platform/saas-benefits/coupons/page`;
- resgates: `GET /platform/saas-benefits/coupons/redemptions/page`.

Todas retornam total real, ordenação com desempate por ID, filtros de domínio,
`offset`, `limit` e `hasMore`. Os Studios navegam em páginas de 20 itens. KPIs
de cupons usam os totais agregados do backend, não a quantidade da página. Uma
falha de fonte é exibida como indisponível, e nunca como zero.

Provas desta etapa:

- 55 concessões, 55 lançamentos, 55 cupons e 55 resgates, com filtros e páginas
  disjuntas: `2 passed` no recorte backend;
- serialização dos quatro contratos frontend: `2 passed`;
- browser integrado dos Studios de Benefícios e Cupons: `2 passed`;
- auditor estrutural: `287` operações e `0` ausentes;
- typecheck, lint sem erros e gate de UUID aprovados.

As coleções auxiliares também foram retiradas da leitura integral:

- histórico de política: `GET /platform/saas-benefits/policy/history/page`;
- indicações: `GET /platform/saas-benefits/referrals/page`;
- parceiros: `GET /platform/saas-benefits/partners/page`;
- auditoria individual do cupom:
  `GET /platform/saas-benefits/coupons/{coupon_id}/audit/page`.

Esses contratos preservam total real, filtro, ordenação determinística,
`offset`, `limit` e `hasMore`. Histórico, indicações, parceiros e auditoria
passaram a possuir controles próprios de página e estados honestos de falha. O
botão de auditoria de cada cupom recebeu nome acessível específico, permitindo
identificar inequivocamente o registro também por teclado e leitor de tela.

Provas adicionais:

- 55 políticas, indicações, vínculos de parceiro e eventos de auditoria, com
  páginas disjuntas e totais reais: `1 passed` no backend;
- contratos frontend das oito coleções de benefícios e cupons: `3 passed`;
- jornada browser da auditoria paginada, incluindo seleção do cupom, resposta
  real da API e total `1–1 de 1`: `1 passed`;
- smoke integrado dos dois Studios após a migração: `2 passed`;
- auditor estrutural: `291` operações e `0` ausentes;
- typecheck, lint com `0 errors` e gate de UUID aprovados.

O subdomínio de benefícios e cupons está paginado. O gate geral do item 6
permanece aberto até a reauditoria das demais listas primárias, sem presumir
que uma coleção pequena ou atualmente limitada seja operacionalmente completa.

### Paginação real de usuários e assinaturas

A lista de usuários das contas já aceitava `limit` e `offset` no backend, mas o
Blackbox fixava 100 registros e não fornecia navegação. A tela agora consulta
50 vínculos por página, usa o total real do servidor, diferencia `Anterior` e
`Próxima`, reinicia a página quando a busca muda e apresenta falha da fonte sem
inventar total zero. A ordenação ganhou desempate por ID.

A lista de assinaturas tinha uma inconsistência mais grave: `count` era apenas
o tamanho da página. O serviço agora calcula `total` sobre os filtros, mantém
`count` compatível com o total, retorna `hasMore` e ordena por atualização mais
ID. O painel financeiro navega em páginas de 50 e preserva filtros, ações e
drill-down da Conta 360.

Provas desta etapa:

- backend com 55 usuários e 55 assinaturas, três páginas, totais reais,
  `hasMore` e conjuntos disjuntos: `3 passed` no recorte integrado;
- painel de usuários: `8 passed`, incluindo navegação até `offset=50`;
- painel financeiro: `4 passed`, incluindo navegação até `offset=50`;
- typecheck aprovado;
- browser integrado confirmou os contratos reais
  `/platform/people?limit=50&offset=0` e
  `/platform/subscriptions?limit=50&offset=0`, seus totais e `hasMore`:
  `1 passed`.

O teste do painel financeiro isola a biblioteca gráfica com um stub de teste;
isso evita a leitura bloqueada dos arquivos `dataless` do checkout e não altera
o runtime. O gate geral de paginação continua aberto para a auditoria de leads,
planos e demais listas explicitamente enumeradas no plano mestre.

### Kanban comercial paginado por estágio

O CRM ainda consumia `GET /platform/growth/pipeline`, que devolvia o funil
inteiro sem paginação. O Kanban agora consulta cada uma das seis colunas ativas
por `GET /platform/growth/prospects`, com filtro de estágio, busca no servidor,
25 registros por página e `offset` independente. Cada coluna possui total real,
navegação própria e falha isolada; uma origem indisponível não derruba nem zera
as demais.

O contrato de prospects passou a retornar `total` e `hasMore`, preservando
`count` para compatibilidade. A ordenação por atualização recebeu desempate por
ID. A antiga rota de pipeline permanece compatível para consumidores antigos,
mas o Blackbox não a utiliza mais.

Provas desta etapa:

- backend com 55 prospects, três páginas, busca e estágio combinados,
  conjuntos disjuntos e total real: `2 passed` no recorte integrado;
- painel do CRM: `9 passed`, incluindo avanço independente da coluna Prospect
  para `offset=25`;
- browser integrado: seis respostas reais com `limit=25`, `offset=0`, total e
  `hasMore`, e nenhuma chamada a `/platform/growth/pipeline`: `1 passed`;
- a suíte frontend transpila e executa o componente atualizado; o typecheck
  global posterior ficou bloqueado em arquivos `date-fns` marcados como
  `dataless` pelo filesystem local, sem diagnóstico TypeScript emitido.

O gate geral permanece aberto para planos e para a classificação final das
demais listas administrativas.

### Planos, versões e incidentes paginados

O catálogo operacional de políticas de plano passou a usar
`GET /platform/plan-policies/page`, com total, `limit`, `offset` e `hasMore`.
O histórico, que pode crescer a cada publicação, deixou de carregar todas as
versões e usa `GET /platform/plan-policies/{plan_id}/versions/page`. Catálogo e
histórico possuem navegação própria e preservam o editor, ETag, prévia e
publicação existentes.

Incidentes deixaram o limite fixo de 200. A API agora usa cursor opaco composto
por `(started_at, id)`, ordenação determinística, leitura de 25 itens e
`openCount` calculado sobre a plataforma — não apenas sobre a página. Cursor
inválido falha com `422`, e o painel mantém histórico de navegação para voltar à
página anterior.

Provas desta etapa:

- plano real editado, publicado, listado no catálogo paginado e recuperado no
  histórico paginado: `1 passed` no backend;
- serviço e painel de políticas: `11 passed` em dois arquivos;
- incidentes com 55 registros no mesmo timestamp, três páginas sem colisão e
  cursor inválido rejeitado: `2 passed` no backend;
- painel de incidentes, incluindo avanço e retorno por cursor: `3 passed`;
- browser integrado dos planos e incidentes: `2 passed`;
- módulos Python alterados compilados;
- auditor estrutural: `293` operações e `0` ausentes.

Durante a validação foi corrigida uma regressão de infraestrutura interna: o
inventário de provas OSS filtrava `node_modules` somente depois de `rglob` já
ter percorrido seus milhares de diretórios. A implementação agora poda os
diretórios excluídos durante `os.walk`; o recorte backend voltou de mais de um
minuto bloqueado para aproximadamente dez segundos de execução completa.

O typecheck global ainda não é contabilizado depois dessas mudanças porque o
processo bloqueia ao abrir declarações `date-fns` marcadas como `dataless` pelo
filesystem. Os componentes modificados foram transpilados e executados pelas
suítes Vitest e Playwright; isso não será promovido a equivalência de typecheck.

### Conversas, logs, tickets e jobs operacionais

A Inbox própria e os logs operacionais deixaram de usar coleções limitadas ou
paginação por `offset`. As duas APIs agora emitem cursor opaco composto por
`(timestamp, id)`, ordenam com o mesmo desempate e leem um item adicional para
calcular `hasMore`. Os painéis possuem avanço, retorno, reinício ao mudar filtros
e erro explícito. Conversas sem `last_message_at` usam `created_at` como chave
temporal, sem criar um zero ou uma data fictícia.

O SAC já possuía cursor, mas ele continha somente `created_at`; chamados com o
mesmo timestamp podiam ser omitidos. O cursor passou ao mesmo contrato opaco e
collision-safe, com aceitação temporária do cursor ISO antigo. A criação e o
filtro de tickets também deixaram de baixar 200 contas e 200 usuários: ambos
usam lookups pesquisáveis e permissionados. A resposta do SAC passou a carregar
`workspaceName`, eliminando o fallback visual para UUID quando a conta não
estava no lote carregado.

A Central de Operações em Massa deixou de exibir somente os 50 jobs mais
recentes. O histórico agora mantém página explícita e envia `limit/offset` ao
backend, que já fornecia total real.

Provas desta etapa:

- 55 conversas e 55 logs com timestamps idênticos, três páginas disjuntas e
  cursor inválido rejeitado: `3 passed` no recorte backend;
- inbox e contrato de logs no frontend: `9 passed`;
- 55 tickets no mesmo timestamp, sem perda ou duplicação: `1 passed`;
- abertura administrativa do ticket com nome humano da conta: `1 passed`;
- browser integrado confirmou `limit=50`, `hasMore` e `nextCursor` na Inbox e
  nos logs: `1 passed`;
- browser integrado confirmou o lookup do SAC e nenhuma leitura legada de 200
  contas ou usuários: `1 passed`;
- smokes das cinco rotas alteradas, sem bloqueio root nem overflow: `5 passed`;
- regressões frontend anteriores do SAC e dos vínculos de evidência: `10 passed`
  antes da última alteração de seletores;
- auditor estrutural atual: `293` operações e `0` ausentes.

A nova asserção estática dos seletores do SAC foi adicionada e o mesmo gate de
fonte executado diretamente em Node passou. A repetição pelo Vitest ficou
bloqueada na leitura de dependências `dataless` do filesystem local antes de
executar os testes. Os testes backend, a compilação Python 3.12, o parse dos
cinco componentes via esbuild e os E2E browser direcionados estão verdes; o
bloqueio do Vitest não é contabilizado como prova dessa suíte. O gate geral do
item 6 fica fechado para as listas enumeradas no plano mestre, sem estender essa
conclusão às coleções editoriais ou técnicas secundárias ainda não auditadas.

### Seleção integral de contas e paginação técnica secundária

Os últimos seletores de conta que ainda dependiam do array legado de 100 ou 200
workspaces foram migrados para `PlatformEntityCombobox`, com lookup POST,
autorização por finalidade e resolução de rótulo humano. Isso cobre:

- política de custos de IA;
- conciliação de cobrança de agentes;
- liberação de pacotes de tokens;
- acesso assistido e acesso direto root;
- convite de usuário de cliente.

A home técnica também deixou de carregar 100 contas. O card de saúde consulta
somente oito registros por meio do contrato paginado, e a lista de contas usa a
própria página server-side. A entrada direta pela Conta 360 usa a conta já
carregada pelo agregado 360; portanto, uma conta na página 3 não precisa ser
reencontrada em uma coleção paralela truncada.

Sessões ativas de segurança agora usam cursor opaco `(last_seen_at, id)`, com
50 itens por página, total global independente e navegação de retorno. Releases
receberam `/platform/governance/releases/records/page`, com total, offset,
`hasMore` e desempate por ID. Comunicados passaram a consumir os parâmetros
`offset/limit` já existentes, em páginas de 25. Solicitações de acesso assistido
receberam `/platform/support-access/requests/page`, também com total e ordenação
determinística.

Provas desta etapa:

- 55 sessões com timestamp idêntico, sem perda ou duplicação, e cursor inválido
  rejeitado: `2 passed` no recorte de segurança;
- contrato paginado de releases e regressão de auditoria: `3 passed` no recorte
  de governança;
- contrato paginado de acesso assistido: `1 passed` no fluxo de idempotência;
- TypeScript global `--noEmit`: verde após esta onda;
- browser integrado dos três seletores de IA/suporte e ausência da coleção
  legada: `1 passed`;
- browser integrado de sessão de segurança: `1 passed`;
- browser integrado de releases: `1 passed`;
- browser integrado de comunicados: `1 passed`;
- convite de usuário por lookup e contratos paginados de usuários/assinaturas:
  `1 passed`;
- smokes de home, contas, acesso assistido, tokens, IA e pessoas: `6 passed`.

Uma execução direcionada do Vitest iniciou, mas permaneceu bloqueada antes da
coleta de testes no filesystem local. Ela foi interrompida e não é contada como
prova. Os mesmos fluxos modificados foram validados por typecheck, backend real
e browser integrado.

### Fechamento operacional: 14 perfis, coleções secundárias e jobs honestos

O gate dos painéis por função foi executado com contextos autenticados novos
para todos os 14 pacotes. Cada jornada validou papel e rótulo da home, entre
três e seis métricas reais, fila, saúde das fontes, primeira ação autorizada,
ausência de conteúdo privado e rejeição de uma API root-only para todos os
papéis não root. Resultado: `14 passed`.

As coleções editoriais secundárias também passaram a ter contrato operacional
explícito:

- posts do Blog: total, offset, `hasMore` e ordenação determinística;
- biblioteca de mídia: páginas de 24 itens;
- jobs do Google Business: páginas de 25 itens;
- contas autorizadas do Estúdio assistido: busca e paginação sem UUID;
- equipes internas: total real e páginas server-side; os seletores de
  responsabilidade usam busca paginada, e a coleção integral antiga ficou
  explicitamente `deprecated`, sem consumidor no Blackbox;
- logs de erro: páginas de 25 itens, estado indisponível explícito e mutação
  de status com ator real, motivo obrigatório e diff canônico sanitizado.

O último adapter de snapshot ausente foi removido da mutação de status dos
logs de erro. A varredura OpenAPI agora encontra `150` operações não-GET do
plano de controle: `145` comandos canônicos, quatro consultas POST e uma rota
deprecated, com zero comando usando `snapshotAdapter: none`.

Durante o E2E de operações, API e worker estavam inicialmente em brokers
locais diferentes. Isso expôs um estado enganoso: a intenção era persistida,
o despacho falhava, mas o job permanecia `queued`. Exportações, restaurações
e lotes agora convertem falha de publicação em estado terminal `failed`, com
`ASYNC_WORKER_UNAVAILABLE`, conclusão observável e novo evento de auditoria.
Nos lotes, cada item pendente também recebe erro estruturado, permitindo nova
prévia pela retentativa de falhas e evitando qualquer execução futura
surpreendente quando o broker voltar.

Provas consolidadas desta etapa:

- regressão backend ampla: `184 passed` em dashboard, root, auditoria,
  operações, exportações/restaurações, busca, tarefas, organização,
  governança, editorial, Estúdio e erros;
- contratos de equipes e convites paginados: `4 passed`;
- despacho indisponível, lotes, exportações, restaurações e organização:
  `14 passed` no recorte final;
- registro central de mutações: `2 passed`;
- smoke desktop integral: `59 passed`, incluindo 43 rotas e os contratos
  reais de paginação, cursor e lookup;
- jornadas dos 14 papéis: `14 passed`;
- exportação, lote simples, sucesso parcial e restauração pelo browser:
  `4 passed`;
- paginação e seleção global sobre 251 contas: `1 passed`;
- cancelamento enfileirado, seguro e idempotente com consumo da fila isolada
  pausado e reativado após a prova: `1 passed`;
- compilação Python dos endpoints e schemas finais: verde;
- auditor estrutural final: `300` operações e `0` ausentes.

O typecheck global havia passado depois da onda de erros/editorial/Estúdio.
A repetição posterior à paginação de equipes bloqueou novamente ao ler
dependências `dataless` do filesystem local e foi interrompida; portanto, essa
segunda execução não é contabilizada como prova. O arquivo final foi exercitado
no browser real, e o backend/schema correspondente passou nos testes. O E2E
de cancelamento cooperativo foi executado separadamente com o consumidor da
fila isolada pausado de forma determinística e reativado imediatamente depois.

Os provedores externos continuam fora do aceite desta execução. Nenhuma tela,
credencial configurada ou estado local foi promovido a prova de Meta, Google,
SMTP, billing provider ou produção.

### Fechamento das jornadas root e dos seletores de equipe

A Central de Comandos passou a provar duas operações além da abertura das
homes: assumir um lead por comando tipado e entrar diretamente numa conta como
Super Admin. O acesso root revelou dois defeitos reais, ambos corrigidos:

- timestamps do grant saíam sem fuso no SQLite local; o navegador em São Paulo
  interpretava a sessão como futura e recusava a credencial depois do `200`;
- a rota da experiência assistida estava dentro do guard de identidade da
  plataforma e, depois da ativação, rejeitava corretamente a nova superfície
  `support`; ela agora é isolada do shell, preserva o ator e retorna o root ao
  `/blackbox` ao encerrar.

Os três formulários que atribuem uma equipe — membro da organização,
responsabilidade de conta e responsabilidade operacional — deixaram de baixar
a coleção integral. O novo `PlatformTeamCombobox` consulta 20 opções por busca,
hidrata a seleção salva por ID no servidor e trata equipe removida ou
indisponível sem pedir UUID ao operador. O endpoint integral foi mantido apenas
por compatibilidade e marcado como `deprecated`.

Provas finais desta extensão:

- jornadas por função e Central de Comandos: `16 passed`;
- regressão browser completa do arquivo Blackbox: `62 passed`, `20 skipped`
  por dependerem de flags de cenários pesados já executados separadamente;
- operações assíncronas, sucesso parcial e restauração: `4 passed` na execução
  dedicada;
- paginação global sobre 251 contas: `1 passed` na execução dedicada;
- cancelamento seguro: `1 passed` na execução dedicada;
- seletor paginado de equipes e ausência da rota integral: `1 passed`;
- regressão backend de todos os arquivos `test_platform_*` mais acesso
  assistido: `274 passed`;
- contrato UTC do acesso root: `1 passed` direcionado e incluído na regressão;
- lint dos arquivos alterados: verde;
- build de produção do Blackbox e verificação de fronteira/same-origin: verde;
- gate de UX: zero identificador interno solicitado ao operador;
- auditor estrutural: `300` operações e `0` ausentes.

O `npm run typecheck` foi novamente interrompido porque o TypeScript permaneceu
sem saída ao ler arquivos `dataless` do filesystem local. Esse processo não é
contado como prova. O bundle completo transformou 2.401 módulos, o lint
direcionado passou e as superfícies alteradas foram exercitadas no browser.

### Fechamento das filas paginadas e da visão multiworkspace de IA

As duas coleções ainda truncadas na Administração das Filas deixaram de usar
listas com limite fixo de 500. Responsabilidades operacionais e revisões agora
possuem contratos `/page` com total real, filtros, offset, `hasMore`, ordenação
determinística e desempate por ID. A interface consome dez registros por página,
exibe o intervalo global e separa explicitamente Anterior/Próxima. As rotas
integrais permanecem somente como compatibilidade `deprecated` e não são
chamadas pelo Blackbox.

A revisão dos logs integrados também encontrou uma falha real na visão root de
modelos de IA: o agregador fixava sucessivamente workspaces diferentes na mesma
sessão protegida pelo guard de tenant. Em bancos com mais de uma conta isso
produzia `WorkspaceContextError` e resposta 500. Cada leitura tenant agora abre
sessão e transação próprias, mantendo o request root como agregador e sem expor
segredos. O teste passou a criar dois workspaces BYOK e confirma as duas máscaras
sem retornar ciphertext.

Provas deste fechamento:

- páginas de atribuições e revisões com 55 registros cada: `2 passed`;
- regressão completa de organização: `6 passed`;
- visão de IA com dois workspaces isolados: `1 passed`;
- browser real para filas paginadas e visão root de IA: `2 passed`;
- regressão browser integral do Blackbox: `64 passed`, `20 skipped` por flags
  dos cenários pesados já comprovados em execuções dedicadas;
- regressão administrativa ampliada, incluindo modelos de IA: `285 passed`;
- lint direcionado: verde;
- build Blackbox: 2.401 módulos, bundle e fronteira de produção verificados;
- gate de UX: zero identificador interno solicitado ao operador;
- auditor estrutural: `302` operações e `0` ausentes.

O typecheck global não foi repetido nesta extensão devido ao mesmo bloqueio de
filesystem `dataless` já documentado e não é contabilizado como prova nova.

### Fechamento dos responsáveis operacionais sem coleções integrais

Suporte, continuidade de equipe, Inbox e CRM deixaram de consumir coleções
integrais de funcionários para filtrar ou atribuir responsáveis. Todos usam
agora `PlatformEntityCombobox` e o contrato paginado de lookup, com uma
finalidade explícita por jornada:

- `support_ticket_filter` exige `support:view` e retorna somente metadados
  mínimos de funcionários ativos;
- `support_assignee` exige capacidade operacional de gestão;
- `staff_reassignment_source` e `staff_reassignment_target` exigem
  `staff_reassignment:manage`, excluem o root e diferenciam origem inativa de
  destino obrigatoriamente ativo;
- `inbox_assignee` retorna apenas quem pode responder suporte ou administrar
  vendas;
- `growth_assignee` retorna apenas quem pode administrar vendas.

A paginação dos responsáveis da Inbox e do CRM é aplicada depois da validação
da capacidade de cada candidato, por cursor de banco em streaming. Isso evita
materializar o diretório no frontend e preserva grants e denials personalizados.
As três rotas integrais antigas permanecem somente por compatibilidade e foram
marcadas como `deprecated`. O frontend não realiza mais GET nessas coleções.

A mesma revisão removeu os clientes frontend não utilizados das coleções
integrais de benefícios, grants, ledger, parceiros, cupons, resgates, histórico
de política, auditoria de cupom, posts, mídia e jobs do Google Business. As
telas já operavam pelos contratos `/page`; as rotas integrais foram preservadas
e marcadas como `deprecated` para consumidores antigos, sem reintroduzi-las no
runtime Blackbox.

A Inbox também passou a alinhar a visibilidade das ações ao backend: perfis
somente leitura não recebem criação, atribuição, classificação, vinculação,
resposta, criação de lead ou ticket. O backend continua revalidando todas as
ações.

Provas desta extensão:

- regras de lookup, autorização e filtragem operacional: `12 passed`;
- regressão unitária de Inbox e CRM: `13 passed`;
- serviços paginados de benefícios/site: `5 passed` e regressão backend
  benefícios/blog/registro de mutações: `21 passed`;
- navegador focado em suporte, continuidade, Inbox e CRM: `4 passed`;
- regressão administrativa completa: `287 passed`, `10 warnings` de depreciação
  Pydantic já conhecidos;
- regressão browser integral, com API direta explicitamente isolada em `8011`:
  `66 passed`, `20 skipped` por flags de cenários pesados;
- lint direcionado e Ruff dos arquivos alterados: verdes;
- build Blackbox: 2.401 módulos, bundle e fronteira de produção verificados;
- gate de UX: zero identificador interno solicitado ao operador;
- auditor estrutural: `302` operações e `0` ausentes.

O aviso do Vite sobre resolução do `mark-white.svg` permanece não fatal: o
asset foi emitido no bundle e a verificação de produção passou. O typecheck
global não foi repetido por causa do bloqueio `dataless` já documentado. Nenhuma
integração externa foi promovida a prova nesta extensão.

### Fechamento das métricas SEO sem coleção integral

O último consumidor administrativo da coleção integral de posts foi removido.
O painel de SEO calculava indexáveis, presença no sitemap, presença no
`llms.txt` e score médio baixando todos os posts no navegador. Agora
`GET /api/v1/platform/blog/posts/metrics` executa a agregação na base,
preserva a autorização do módulo de conteúdo e devolve total, cobertura de
score e estado explícito da fonte.

A interface diferencia três situações: carregamento, indisponibilidade e dados
insuficientes. Quando não existem posts ou scores, não apresenta zero como se
fosse uma medição observada. A rota integral de posts continua apenas como
compatibilidade `deprecated`; o runtime Blackbox usa a página para listagem e o
agregado para KPIs.

Durante o gate de produção, o verificador também detectou duas identidades de
fixture compiladas no bundle geral. O provider passou a consumir o alias de
fixture que o Vite troca pela implementação inerte de produção. Isso preserva
a simulação local sem transportar os IDs de teste para o artefato publicado.

Provas desta extensão:

- endpoint agregado e contrato de mutações do blog: `8 passed`;
- transporte frontend e estados honestos do painel SEO: `5 passed`;
- lint direcionado: verde;
- build geral: bundle verificado sem dados dev/test;
- build Blackbox: bundle, identidade visual, fronteira e same-origin
  verificados;
- gate de UX: zero identificador interno solicitado ao operador;
- auditor estrutural: `303` operações e `0` ausentes.

O agregado é interno e não constitui prova de indexação por Google ou outro
provedor externo. Essas comprovações permanecem fora do aceite desta execução.

### Fechamento da publicação editorial sem fixture nem mutação de leitura

O serviço público de blog não cria mais o artigo demonstrativo quando a base
está vazia. Uma instalação sem conteúdo agora devolve coleção vazia, destaque
ausente e métricas com estado `insufficient_data`; o runtime não fabrica
conteúdo para aparentar operação.

A publicação agendada também deixou de depender de alguém abrir uma rota GET.
O Celery Beat chama a tarefa `blog_publication_tasks.publish_due` a cada minuto,
em lotes limitados e com ordenação determinística. Cada post publicado recebe
um evento canônico de sistema com snapshots anterior e posterior, versão,
correlação e `changedPaths`. A publicação e a auditoria pertencem ao mesmo
commit; as rotas de leitura não publicam nem executam commit.

Provas desta extensão:

- blog público e administrativo, inclusive base vazia e auditoria: `7 passed`;
- registro da tarefa, limite do worker e saúde das rotinas periódicas:
  `9 passed`;
- Ruff restrito a imports, nomes e erros de execução: verde;
- tarefa registrada no Celery e agenda periódica observada localmente.

### Fechamento da resolução exata no Estúdio da Conta

Depois da escolha feita pelo combobox paginado, o Estúdio relia até 100 contas
por busca de slug para atualizar o escopo temporário. Isso foi substituído por
`GET /api/v1/platform/configuration-studio/workspaces/{id}/access`, que resolve
somente a conta exata e revalida ator, atividade, grant, escopos e expiração.
Uma negação ou expiração torna a seleção imediatamente indisponível na tela;
o frontend não conserva a permissão anterior como fallback.

A rota integral antiga de workspaces do Estúdio e a rota integral de convites
de funcionários foram marcadas como `deprecated`. Os clientes frontend sem
consumidor para essas coleções, para workspaces gerais e para o pipeline de
crescimento foram removidos. Descoberta continua paginada e operações usam
identificadores obtidos por seletores, não por campos técnicos.

Provas focadas:

- Estúdio, organização e serviço organizacional: `24 passed`;
- transporte do Estúdio, convite com lookup real e paginação organizacional:
  `11 passed`;
- ESLint direcionado e Ruff restrito: verdes.

### Fechamento da paginação de recibos e histórico de Comunicados

A Central de Comunicados deixou de cortar recibos nos primeiros 500 e eventos
nos primeiros 200. Recibos agora devolvem `limit`, `offset`, `total` e
`hasMore`; a trilha ganhou o contrato `/audit/page`, com a mesma semântica e
ordenação determinística. A interface navega em páginas de 25 e informa o total
real, sem chamar uma coleção integral.

O contrato também passou a devolver os nomes do destinatário e do ator. O
operador vê rótulos humanos; UUIDs permanecem apenas nos dados técnicos e não
são solicitados nem apresentados como identidade principal.

Provas focadas:

- API de Comunicados, incluindo limites, totais e `hasMore`: `8 passed`;
- transporte e painel de Comunicados: `10 passed`;
- ESLint direcionado e Ruff restrito: verdes.

Regressão consolidada depois destes fechamentos:

- backend administrativo ampliado: `349 passed`, com os `10 warnings` de
  depreciação Pydantic já conhecidos;
- frontend Blackbox/plataforma: `27` arquivos e `106 passed`;
- browser real em `3000` + API isolada em `8011`: `2 passed` para paginação de
  Comunicados e resolução exata do Estúdio;
- build Blackbox: `2.401` módulos, bundle, fronteira, same-origin e ausência de
  dados dev/test verificados;
- auditor estrutural: `305` operações e `0` ausentes;
- gate de UX: zero identificador interno solicitado ao operador.

A suíte frontend global também foi executada, mas não é contabilizada como
verde: seis testes fora do Blackbox falharam em Campanhas, calendário,
colaboração e varredura de papéis do `/app`. Os `106` testes do recorte
Blackbox/plataforma passaram isoladamente; essas falhas alheias permanecem
registradas, sem serem atribuídas nem alteradas nesta extensão.

### Fechamento da paginação de rascunhos e versões do Estúdio

Rascunhos e histórico imutável do Estúdio deixaram de carregar coleções
integrais. Os contratos `/drafts/page` e `/versions/page` agora devolvem
`items`, `total`, `limit`, `offset` e `hasMore`, com ordenação determinística.
A interface navega em páginas de 10, preserva o rascunho selecionado, continua
exigindo ETag para publicação e restauração e não transforma falha de leitura
em estado vazio. As rotas integrais antigas permanecem temporariamente por
compatibilidade, marcadas como `deprecated`, sem consumidor no Blackbox.

A nova leitura paginada usa a mesma autorização por conta: o
`platform_owner` mantém o bypass raiz e funcionários não root precisam de
grant ativo com escopo de configurações. A prova no navegador também falha se
o frontend voltar a chamar a coleção integral.

Provas focadas desta extensão:

- API do Estúdio, incluindo root, grant assistido, paginação, versões e
  restauração: `14 passed`;
- transporte frontend paginado: `3 passed`;
- ESLint direcionado e Ruff restrito: verdes;
- browser real em `3000` + API isolada em `8011`: `1 passed`;
- build Blackbox: `2.401` módulos e bundle sem dados dev/test;
- auditor estrutural: `307` operações e `0` ausentes;
- gate de UX: nenhum identificador interno solicitado ao operador.

### Fechamento da trilha paginada de acesso assistido

As trilhas de acesso assistido da plataforma e do workspace deixaram de
carregar somente uma janela invertida dos últimos 500 eventos. Os novos
contratos `/events/page` devolvem página mais recente primeiro, total completo,
`limit`, `offset` e `hasMore`, com desempate determinístico por ID. As rotas
integrais anteriores permanecem apenas para compatibilidade e estão marcadas
como `deprecated`.

O Blackbox e a tela de aprovação do cliente navegam em páginas de 25. Sessões
ativas continuam atualizando a página aberta a cada cinco segundos; erro,
carregamento e ausência de eventos permanecem estados distintos. A autorização
não foi compartilhada indevidamente: a leitura interna exige operador de
suporte autorizado e a leitura tenant exige owner/admin do workspace ativo.

O E2E também revelou e corrigiu um problema de seleção: uma conta encontrada
pelo lookup global era apagada quando não pertencia à coleção inicial do
painel. A seleção pesquisável agora é a fonte de verdade, permitindo operar
qualquer conta autorizada sem voltar ao limite da lista carregada.

Provas focadas desta extensão:

- backend de acesso assistido, inclusive 506 eventos, root e owner/admin:
  `14 passed`;
- serviço e painéis frontend: `56 passed`;
- regressão adicional do painel interno após a correção do lookup: `9 passed`;
- E2E real da criação root, seleção pesquisável e `/events/page`: `1 passed`;
- ESLint direcionado e Ruff restrito: verdes;
- build Blackbox: `2.401` módulos e bundle sem dados dev/test;
- auditor estrutural: `308` operações e `0` ausentes;
- gate de UX: nenhum identificador interno solicitado ao operador.

Complemento do fluxo de aprovação: a fila tenant de solicitações também foi
migrada de uma coleção limitada a 100 registros para
`/workspaces/support-access/requests/page`. O owner/admin navega em páginas de
25, recebe total real, filtra por estado no servidor e mantém a atualização de
grants ativos. A rota integral anterior ficou `deprecated`. A mesma regressão
de acesso assistido permaneceu verde (`14` backend e `56` frontend), com lint
sem avisos e novo build Blackbox verificado.

### Fechamento dos históricos globais e de governança

Os históricos imutáveis das Configurações globais e dos oito controles de
governança deixaram de carregar todas as revisões em uma única resposta. Os
novos contratos `/configurations/{namespace}/versions/page` e
`/governance/{module}/versions/page` devolvem `items`, `total`, `limit`,
`offset` e `hasMore`, ordenados por versão e ID no servidor. As rotas integrais
anteriores permanecem somente para compatibilidade e estão marcadas como
`deprecated`.

As duas telas navegam em páginas de 10, distinguem erro de histórico vazio,
preservam ETag e restauração como nova revisão e voltam à primeira página após
publicação ou rollback. O E2E falha se qualquer uma delas voltar a consumir a
coleção antiga.

Provas focadas desta extensão:

- configurações e governança no backend: `23 passed`, com os `10 warnings` de
  depreciação Pydantic já conhecidos;
- contrato de transporte frontend: `7 passed`;
- ESLint direcionado, Ruff dos contratos/serviço e compilação dos endpoints:
  verdes;
- browser real em `3000` + API isolada em `8011`: `1 passed` para os dois
  históricos paginados e rejeição dos endpoints legados;
- build Blackbox: `2.401` módulos, bundle, fronteira e same-origin verificados;
- auditor estrutural: `310` operações e `0` ausentes;
- gate de UX: nenhum identificador interno solicitado ao operador.

### Fechamento das listas operacionais da Conta 360

Responsáveis por onboarding, CS, cobrança e suporte e os marcos operacionais da
conta deixaram de ser coleções integrais. A Conta 360 agora consome
`/assignments/page` e `/milestones/page`, com total real, limites, offset,
`hasMore` e ordenação determinística. As rotas anteriores permanecem
temporariamente como compatibilidade `deprecated`.

O painel navega em páginas de cinco, mantém erro diferente de vazio, volta à
primeira página após uma alteração e não solicita UUID ao operador. A prova de
API cobre quatro responsabilidades e sete marcos; a prova de browser abre uma
Conta 360 real e falha caso o frontend consulte os endpoints integrais.

Provas focadas desta extensão:

- organização/Conta 360 no backend: `7 passed`, com os `10 warnings` Pydantic
  já conhecidos;
- serviço e Conta 360 frontend: `11 passed`;
- Ruff, compilação dos endpoints e ESLint direcionado: verdes;
- browser real em `3000` + API isolada em `8011`: `1 passed`;
- build Blackbox: `2.401` módulos e bundle verificado;
- auditor estrutural: `312` operações e `0` ausentes;
- gate de UX: nenhum identificador interno solicitado ao operador.

### Fechamento da fila de pacotes adicionais de tokens de IA

A fila de solicitações de pacotes de tokens deixou de carregar todo o histórico
de uma conta. O Blackbox agora consome
`/workspaces/{workspace_id}/ai-token-package-requests/page`, com filtro opcional
de estado, total real, `limit`, `offset`, `hasMore` e ordenação determinística
por criação e ID. A coleção integral anterior permanece apenas como
compatibilidade `deprecated`.

Aprovação, recusa e revogação agora exigem motivo operacional útil, com 8 a
500 caracteres após remoção de espaços. Aprovação e revogação produzem eventos
canônicos com snapshots `before` e `after`, diff calculado no backend, ator,
conta e motivo, na mesma transação da alteração. A interface não habilita as
ações sem motivo, distingue erro de fila vazia e navega em páginas de 10.

Provas focadas desta extensão:

- custos e pacotes de IA no backend: `7 passed`, incluindo envelope paginado,
  rejeição de motivo ausente/em branco, snapshots e diff de aprovação e
  revogação;
- contrato de transporte frontend: `3 passed`;
- Ruff, compilação dos endpoints e ESLint direcionado: verdes;
- browser real em `3000` + API isolada em `8011`: `1 passed`, com detecção da
  rota legada e do campo de motivo;
- build Blackbox: `2.401` módulos e bundle sem dados dev/test;
- auditor estrutural: `313` operações e `0` ausentes;
- gate de UX: nenhum identificador interno solicitado ao operador.

### Fechamento do histórico de Continuidade da Equipe

O histórico de redistribuições deixou de ocultar registros além da janela fixa
de 200. As superfícies do workspace e do Blackbox agora expõem `/page`, com
total real, `limit`, `offset`, `hasMore` e desempate por criação e ID. As rotas
integrais anteriores permanecem somente como compatibilidade `deprecated`.

No Blackbox, o total é calculado depois da política de visibilidade do papel:
Suporte Técnico conta apenas transferências de tickets, Comercial apenas as de
prospects, e somente `platform_owner` vê todos os escopos. A tela navega em
páginas de 10, distingue erro de vazio e retorna à primeira página após aplicar
ou restaurar uma transferência.

Provas focadas desta extensão:

- continuidade no backend: `14 passed` no recorte combinado, mais `7 passed`
  no contrato canônico, incluindo total filtrado por escopo do gestor;
- serviço e painel frontend: `7 passed`;
- Ruff, compilação dos endpoints e ESLint direcionado: verdes;
- browser real em `3000` + API isolada em `8011`: `1 passed`, rejeitando a
  coleção legada do Blackbox;
- build Blackbox: `2.401` módulos e bundle verificado;
- auditor estrutural: `314` operações e `0` ausentes;
- gate de UX: nenhum identificador interno solicitado ao operador.

### Fechamento dos eventos de Operação de IA

O endpoint de eventos do gateway já possuía `offset`, mas o Blackbox ignorava a
paginação: buscava 30 registros e descartava 18 no navegador. O contrato agora
expõe `hasMore`, usa ordenação determinística por horário e ID e a tela navega
em páginas de 12. Alternar entre fallbacks e todos os eventos volta ao início e
reaplica o filtro no servidor.

Provas focadas desta extensão:

- observabilidade de IA no backend: `5 passed`, incluindo página parcial com
  `hasMore` verdadeiro e filtro de fallback;
- painel e transporte frontend: `6 passed`;
- Ruff, compilação e ESLint direcionado: verdes;
- regressão browser conjunta das três últimas jornadas: `3 passed`;
- build Blackbox: `2.401` módulos e bundle verificado;
- auditor estrutural: `314` operações e `0` ausentes;
- gate de UX: nenhum identificador interno solicitado ao operador.

### Fechamento do histórico financeiro da assinatura

O drawer financeiro da Conta 360 deixou de depender de `recent_events`, um
recorte fixo de dez registros no agregado que ainda era reduzido para seis no
navegador. O Blackbox agora consome
`/subscriptions/{workspace_id}/events/page`, com total real, `limit`, `offset`,
`hasMore` e ordenação determinística por horário e ID. A coleção integral
anterior permanece somente como compatibilidade `deprecated`.

A tela navega em páginas de dez, distingue carregamento, indisponibilidade e
histórico vazio, mostra ator e motivo e resume somente os campos comerciais
alterados com rótulos humanos. O bootstrap local da Blackbox passou a criar uma
assinatura comercial e seu primeiro evento de forma idempotente; isso torna a
prova de browser persistida e elimina a dependência de mocks ou de uma base
previamente preparada.

Provas focadas desta extensão:

- financeiro administrativo no backend: `21 passed`, incluindo total,
  `hasMore`, segunda página, camelCase e compatibilidade da rota anterior;
- bootstrap local e contrato paginado: `2 passed`, incluindo uma única
  assinatura e um único evento mesmo após abrir as sessões dos 14 papéis;
- painel financeiro frontend: `5 passed`, incluindo rejeição do recorte legado
  e navegação até o offset 10;
- ESLint direcionado, compilação Python e contrato Ruff novo: verdes;
- browser real em `3000` + API isolada em `8011`: `1 passed`, abrindo uma conta
  real e recusando a rota de eventos legada;
- build Blackbox: `2.401` módulos e bundle sem dados dev/test;
- auditor estrutural: `315` operações e `0` ausentes;
- gate de UX: nenhum identificador interno solicitado ao operador.

### Fechamento dos usuários do drawer financeiro

A Conta 360 financeira também deixou de reduzir usuários com
`people.items.slice(0, 8)`. O drawer agora consulta a página autoritativa de
`/platform/people` com `workspaceId`, total real, oito registros por página e
navegação independente do histórico da assinatura. Loading, erro, vazio e
resultado válido permanecem estados distintos.

Na mesma correção, o agregado `/workspace-360/{workspace_id}` deixou de
procurar a assinatura da conta dentro das 200 assinaturas mais recentes. O
serviço carrega exatamente o `workspace_id` solicitado; um teste bloqueia
explicitamente qualquer regressão para a janela global. A revisão independente
também removeu consultas concorrentes sobre a mesma `AsyncSession`: as fontes
continuam isoladas contra falha, mas são lidas em sequência transacionalmente
segura.

Os eventos canônicos passam a registrar o nome da conta no momento da mutação.
O Audit Center usa esse rótulo e não exibe mais prefixos de UUID como identidade
operacional; eventos legados sem snapshot mostram apenas “Conta vinculada”.

Provas focadas desta extensão:

- financeiro administrativo no backend: `21 passed`, incluindo o teste que
  rejeita a consulta global de assinaturas;
- painel financeiro frontend: `5 passed`, navegando separadamente eventos e
  usuários;
- browser real em `3000` + API isolada em `8011`: `1 passed`, comprovando
  `workspaceId`, `limit=8`, `offset=0` e a página de eventos;
- auditoria financeira comprova `workspace_label_snapshot` com o nome real;
- ESLint e compilação direcionados: verdes.

### Fechamento da paginação dos Relatórios Avançados

Os Relatórios Avançados deixaram de tratar o limite da resposta como se fosse
o conjunto analítico integral. O backend agora aceita `limit` e `offset`,
calcula `total` e `rowsWithData` sobre todo o conjunto autorizado e filtrado e
só então materializa a página solicitada. A ordenação permanece determinística
e o contrato informa `hasMore`.

O gráfico e a tabela representam explicitamente a página atual. A interface
navega em blocos de 25, reinicia a paginação ao trocar filtros, mostra o
intervalo real e separa indisponibilidade de valor zero. Uma fonte sem dados
válidos aparece como “—”/indisponível, nunca como KPI fictício igual a zero.

Provas focadas desta extensão:

- serviço e endpoints dos Studios no backend: `14 passed`, incluindo páginas
  estáveis e contagem integral antes do recorte;
- relatório frontend: `2 passed`, incluindo navegação por offset e ausência
  honesta de dados;
- browser real em `3000` + API isolada em `8011`: `1 passed`, comprovando a
  página analítica autoritativa;
- ESLint e compilação direcionados: verdes;
- build Blackbox: `2.401` módulos e bundle verificado;
- auditor estrutural: `315` operações e `0` ausentes;
- gate de UX: nenhum identificador interno solicitado ao operador.

### Fechamento dos rankings do Estúdio de Performance

Os três rankings — equipe dos escritórios, equipe do SaaS e benchmark — agora
possuem `total`, `limit`, `offset` e `hasMore`. A classificação é calculada e
ordenada sobre o conjunto integral antes do recorte, preservando a posição
global mesmo ao navegar. A interface usa páginas de 25 e o gráfico declara que
representa as primeiras posições da página atual.

Provas focadas desta extensão:

- Studios no backend: `15 passed`, incluindo páginas consecutivas disjuntas e
  total autoritativo;
- componente do Estúdio de Performance: `1 passed`, navegando do offset 0 ao
  25 e preservando a posição 26;
- browser real em `3000` + API isolada em `8011`: `1 passed`, comprovando os
  três endpoints com `limit=25` e `offset=0`;
- ESLint e compilação direcionados: verdes;
- build Blackbox: `2.401` módulos e bundle verificado;
- gate de UX: nenhum identificador interno solicitado ao operador.

### Fechamento da Biblioteca Criativa

A leitura operacional da Biblioteca Criativa deixou de consumir o documento
integral. A nova página filtra por situação e busca antes do recorte, ordena
por atualização e ID e transporta a versão/ETag necessários para concorrência
otimista. A coleção anterior permanece `deprecated` para compatibilidade, mas
o Blackbox não a chama.

A tela usa o total do servidor, páginas de 25 e estados explícitos de loading,
erro e vazio. Salvar ou arquivar retorna ao início antes de revalidar a página,
evitando uma página órfã quando o item deixa o filtro ativo.

Provas focadas desta extensão:

- Studios no backend: `16 passed`, incluindo filtro anterior à paginação,
  páginas disjuntas e versão canônica;
- componente da Biblioteca Criativa: `1 passed`, navegando do offset 0 ao 25;
- browser real em `3000` + API isolada em `8011`: `1 passed`, rejeitando a
  coleção legada;
- regressão conjunta dos três Studios frontend: `4 passed` em três arquivos;
- ESLint, compilação e `git diff --check` direcionados: verdes;
- build Blackbox: `2.401` módulos e bundle verificado;
- auditor estrutural: `316` operações e `0` ausentes;
- gate de UX: nenhum identificador interno solicitado ao operador.

### Endurecimento do gate de auditoria universal

O auditor estrutural já exigia um descritor OpenAPI para cada mutação, mas não
reprovava um comando cujo descritor declarasse `snapshotAdapter: none`. O modo
`--strict` agora trata essa situação como falha de CI e o JSON de evidência
expõe `mutationSnapshotAdaptersMissing` separadamente.

Provas focadas desta extensão:

- registro de mutações e novo gate: `3 passed`;
- `316` operações administrativas inventariadas;
- `0` rotas ausentes, `0` descritores ausentes e `0` comandos sem adapter de
  snapshot;
- nova varredura browser dos painéis por papel após os Studios: `14 passed`.

### Regressão desktop integral após os Studios

O smoke completo percorreu as 43 rotas root e todas as jornadas desktop de
paginação, cursor, lookup e coleções autoritativas. A primeira execução revelou
dois seletores E2E globais ambíguos em páginas com mais de um paginador; os
asserts foram escopados aos cards de Logs e Sessões, passaram isoladamente e a
suíte integral foi repetida sem retry funcional.

Resultado final: `72 passed` em browser real, em execução serial e sem mocks de
resposta do frontend.

### Prova integrada do motor assíncrono

A regressão operacional detectou que a API local estava usando o Redis padrão
em `6379`, enquanto o worker isolado da tarefa usava `16379`. O processo da API
foi reiniciado com o mesmo broker e backend de resultados do worker e o ping do
Celery confirmou o nó online. Essa correção é de configuração da prova local;
nenhuma indisponibilidade foi convertida em sucesso simulado.

Os asserts de lote também passaram a ser escopados ao card “Job em
acompanhamento”. Assim, um job histórico com estado `partial` ou `succeeded`
não pode mais satisfazer a jornada do job recém-criado.

Provas integradas:

- worker Celery: `1 node online`, com resposta `pong`;
- E2E operacional real: `4 passed`;
- exportação assíncrona criada e acompanhada;
- lote simples concluído e com prévia de restauração compatível;
- lote concorrente concluído como `partial`, com `1` sucesso e `1` conflito;
- resultado parcial exportado e somente o alvo compatível oferecido para
  restauração;
- Audit Center exibindo diff e restauração finalizada como `succeeded`;
- lint sem erros e gate de UUID interno verde.

### Regressão integrada dirigida do plano de controle

Após a prova dos jobs, o conjunto backend do Blackbox foi executado numa única
partição abrangendo APIs, serviços, autorização root/RBAC, auditoria, busca,
organização, dashboard, inbox, lotes, work items, exportações, restaurações,
contas, suporte e Studios. O frontend dirigido reuniu os serviços e painéis
correspondentes numa única execução.

Resultado:

- backend Blackbox: `363 passed`;
- frontend Blackbox: `38` arquivos e `187 passed`;
- auditor OpenAPI → frontend: `316` operações, `0` ausentes;
- módulos RBAC sem superfície: `0`;
- mutações sem descritor: `0`;
- mutações de comando sem snapshot adapter: `0`;
- migrations: cabeça Alembic única;
- build Blackbox: `2.401` módulos e bundle de produção verificado;
- acessibilidade da Central de Comandos: `1 passed` por teclado, foco e anúncio
  de estado;
- `compileall`, `git diff --check` e gate de UUID interno: verdes.

O build ainda emite o aviso conhecido de resolução em runtime do SVG branco da
marca, mas o próprio artefato contém
`dist-blackbox/brand/meu-executivo/mark-white.svg` e o verificador do bundle
confirma a fronteira da superfície, same-origin e ausência de dados de
desenvolvimento/teste.

### Fechamento de todas as jornadas E2E condicionais

A execução desktop única, com papéis e jobs reais habilitados, percorreu `95`
casos: `93 passed` e apenas os dois testes que exigem modos de runtime
mutuamente exclusivos foram ignorados nessa execução. Ambos foram executados
logo depois no ambiente apropriado:

- paginação e seleção global com mais de 250 contas: `1 passed`;
- cancelamento de job ainda enfileirado e segunda chamada idempotente:
  `1 passed`.

Para o cancelamento, a API e o frontend temporários usaram um banco lógico de
broker sem consumidor, mantendo o worker principal ativo. Isso tornou o estado
`queued` determinístico, comprovou as duas respostas `202 Accepted` e evitou
pausar ou interferir no worker da regressão integrada. O runtime temporário foi
encerrado e seu diretório foi movido para a Lixeira após a prova.

Com isso, os `95/95` cenários desktop definidos no arquivo do Blackbox possuem
execução verde nos modos locais exigidos, sem retry funcional e sem respostas
mockadas pelo frontend.
