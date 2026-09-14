# KLM Hub - Meta e Plano SaaS Local V5

**Data:** 19 de julho de 2026

**Status:** `APROVADO_EM_EXECUCAO_LOCAL`

**Escopo:** maturidade SaaS, operacao juridica/comercial, incorporacao OSS, IA,
performance e QA, exclusivamente no ambiente local.

**Gate externo:** APIs reais, credenciais, canais externos e AWS permanecem
bloqueados ate uma aprovacao posterior e separada do proprietario.

Este documento consolida e corrige o
[`PLANO_CORRECAO_SPRINTS_2026-07-19.md`](./PLANO_CORRECAO_SPRINTS_2026-07-19.md),
o [`KLM_HUB_PLANO_INCORPORACAO_OSS_V3.md`](./KLM_HUB_PLANO_INCORPORACAO_OSS_V3.md)
e os achados do QA de 19/07. Os documentos anteriores permanecem como historico.

### Matriz de supersessao

| Documento anterior | Absorvido | Adiado/proibido nesta fase |
|---|---|---|
| `PLANO_CORRECAO_SPRINTS_2026-07-19.md` | seguranca, performance, gateway, frontend, containers, observabilidade e QA | remocao de funcoes e qualquer decisao/acao AWS |
| `KLM_HUB_PLANO_INCORPORACAO_OSS_V3.md` | KLM canonico, experiencia nativa, ledger, sidecars e gates | criterio de familia substituido pelo censo granular de funcoes |
| `KLM_HUB_PLANO_ADAPTATION_FIRST_V2.md` | licencas, pins, bridges, RBAC, contexto e reconciliacao | launcher/full app nao conta como experiencia final |
| `KLM_HUB_PLANO_E_APROVACAO_LOCAL.md` | baseline e limites da entrega local anterior | status de conclusao nao se estende a esta nova meta |

Em caso de conflito, este V5 prevalece. Planos anteriores nao autorizam execucao,
remocao, API externa ou AWS.

## Fontes de decisao

| ID | Evidencia |
|---|---|
| S1 | Diretriz do proprietario: "Ajustar tudo que falta pra ficar um saas perfeito." |
| S2 | Diretriz do proprietario: criar playbook para agentes, objecoes, marca, propostas e escolha entre reuniao humana ou fechamento autonomo. |
| S3 | Diretriz do proprietario: incorporar nativamente todas as capacidades e funcoes de BottleCRM, ChatterMate, Rocket.Chat, Chatwoot e demais referencias aprovadas, preservando funcoes. |
| S4 | Diretriz do proprietario: concluir localmente; APIs externas antes da AWS; AWS somente depois de nova aprovacao. |
| S5 | O KLM permanece fonte canonica de pessoa, caso, documentos, RAG, honorarios, decisoes e auditoria juridica (`KLM_HUB_PLANO_INCORPORACAO_OSS_V3.md`). |
| S6 | O ledger vigente registra 135 familias OSS, 121 com contrato local, 12 bloqueadas por gate externo e 2 substituicoes responsivas (`OSS_ADAPTATION_LEDGER.md`). Esse numero e baseline de familias, nao prova de que todas as funcoes publicas foram censadas individualmente. |
| S7 | O QA reporta 1.538 testes backend, 176 frontend, 205 E2E e 511 acoes nativas instrumentadas, mas os resultados precisam ser materializados por SHA e manifesto reproduzivel. |
| S8 | O QA identificou oportunidades concretas em autenticacao, Redis, bcrypt, inbox, dashboard, contexto de IA, telemetria, cache, frontend e imagens. |

## 0. Resumo executivo

O KLM ja possui uma base funcional ampla, mas sua maturidade esta desigual: ha
muitos recursos e adapters, enquanto playbook, preferencias, autonomia, billing,
entitlements e operacao dos motores OSS ainda nao sao governados por uma unica
camada de produto. Essa e a restricao central.

A meta desta fase nao e prometer software sem defeitos. E produzir uma versao
local comprovadamente pronta para homologacao de APIs: multiempresa, configuravel,
white-label, AI-first, segura por workspace, economicamente observavel e capaz de
executar jornadas completas com provedores simulados. Toda funcao publica e
comunitaria, permitida pela licenca, dos upstreams fixados devera possuir contrato,
estado, acao e experiencia nativa no KLM. Os sidecars continuarao preservados como
oraculos de paridade, fallback e referencia ate equivalencia comprovada e aprovacao
expressa para qualquer aposentadoria, mas nao serao uma UI obrigatoria para o uso.

O trabalho sera conduzido em cinco esforcos, cada um com gate objetivo. Nenhuma
funcao existente sera removida ou remodelada de forma destrutiva sem aprovacao.
O fechamento exige QA total, simulacoes de agentes, prova de isolamento,
benchmark de custo/latencia e uma ultima analise competitiva baseada em fontes
atuais. Somente depois disso o proprietario recebera o pacote para decidir sobre
APIs externas; AWS continuara fora do escopo.

## 1. Meta ajustada

### Meta

Entregar o estado `LOCAL_SAAS_APPROVAL_READY_V5`: um KLM Hub local no qual um
escritorio possa ser criado, configurado, personalizado e operado de ponta a
ponta por humanos e agentes, com todas as capacidades e funcoes comunitarias
licenciadas dos upstreams fixados acessiveis nativamente na SPA, custos e qualidade
mensuraveis, zero efeito externo e evidencia reproduzivel.

### North Star operacional

**Percentual de leads elegiveis conduzidos ao proximo passo correto dentro do
SLA, sem vazamento entre clientes, promessa indevida, perda de contexto ou acao
externa nao autorizada.**

### Resultados-chave obrigatorios

1. **SaaS:** cadastro, trial, planos, quotas, upgrade/downgrade, suspensao,
   reativacao e encerramento funcionam integralmente em simulador local.
2. **Tenancy:** zero leitura, escrita, busca, RAG, cache, evento ou arquivo entre
   workspaces nos testes API, worker, WebSocket, MCP e sidecar.
3. **Configuracao:** marca, preferencias, papeis, area juridica, playbook,
   autonomia, propostas, agenda, canais e notificacoes sao versionados por
   workspace; preferencias pessoais sao separadas das politicas do escritorio.
4. **Vendas:** cada conversa registra versao do playbook, etapa, qualificacao,
   objecao, proxima acao, responsavel e motivo de handoff ou fechamento.
5. **Autonomia:** o escritorio pode escolher humano, IA supervisionada ou IA
   autorizada a fechar dentro de limites; toda excecao e auditada e reversivel.
6. **OSS nativo total:** as 135 familias atuais sao o piso. Um novo censo granular
   cobre 100% das funcoes publicas e comunitarias encontradas; cada unidade possui
   implementacao KLM nativa verificada ou bloqueio proprietario/licenciario provado.
7. **IA:** 100% das chamadas passam pelo gateway; custo, tokens, latencia,
   playbook, contexto e qualidade ficam atribuiveis por workspace, caso e agente.
8. **Eficiencia estrutural local:** reduzir em pelo menos 40% o input mediano de
   tokens das jornadas longas contra baseline versionado, sem queda superior a 2
   pontos na conformidade da suite dourada. Custo/qualidade de modelos pagos so
   serao afirmados na homologacao futura das APIs.
9. **Performance local:** p95 menor que 300 ms nos endpoints quentes com perfil de
   carga documentado; Home usa no maximo cinco requisicoes e o browser nao faz
   preflight no perfil equivalente a producao.
10. **Qualidade:** zero P0/P1 aberto, zero controle decorativo, zero efeito externo,
    suites verdes e pacote de evidencia ligado ao snapshot exato do codigo.

## 2. Classificacao e restricao

**Cynefin:** o programa e predominantemente **Complicado**, pois arquitetura,
seguranca, SaaS e migracao possuem praticas conhecidas. O comportamento autonomo
dos agentes e **Complexo**: sera calibrado por experimentos locais, dataset dourado
e replay, nunca por confianca intuitiva.

**Theory of Constraints:** a restricao vinculante e a ausencia de um nucleo
configuravel que traduza estrategia do escritorio em comportamento consistente
de usuarios, agentes, propostas, automacoes e motores OSS. Adicionar mais telas,
prompts ou bridges antes desse nucleo aumentaria divergencia e retrabalho.

## 3. Decisoes e padroes adotados

| Tema | Padrao desta meta | Pode ser alterado pelo proprietario |
|---|---|---|
| Autonomia inicial | IA qualifica e prepara; humano aprova o fechamento | Sim, por workspace, area, etapa, valor e risco |
| Reuniao | Politica `sempre_humano`, `quando_necessario` ou `dispensavel` | Sim |
| White-label | Marca completa por workspace, mantendo KLM como marca da plataforma administrativa | Sim |
| Area inicial | Negativas de plano de saude; arquitetura plug-and-play para novas areas | Sim |
| OSS | Todas as funcoes comunitarias licenciadas recebem contrato e experiencia KLM; sidecars ficam preservados ate paridade, migracao e aprovacao de aposentadoria | Nao remover sem aprovacao |
| Planos e precos SaaS | Catalogo e simulador editaveis; nenhum preco externo publicado nesta fase | Sim |
| Integracoes | Fake adapters e contract tests; nenhuma credencial real | Liberacao futura separada |
| Infra local | PostgreSQL + pgvector, Redis, object storage S3-compatible, workers e proxy same-origin | ADR antes de consolidar bancos |

### Regras de execucao que valem para todos os esforcos

1. Preservar a ordem aprovada: primeiro contrato de produto e experiencia SPA com
   fixtures deterministicas; depois backend/persistencia; por ultimo agentes e tools.
2. Acrescentar sem retirar ou remodelar comportamento aprovado, salvo instrucao
   expressa do proprietario. Migracoes de UX precisam preservar rota, contexto e
   capacidade anterior ate o novo caminho passar em regressao.
3. A aprovacao deste plano libera o escopo das funcoes comunitarias dos cinco pins,
   nao libera codigo proprietario, nova referencia, efeito externo ou remocao.
4. Cada bloco passa por executor e revisor independente. Nenhum gate fecha com
   teste vermelho, placeholder, controle sem efeito ou evidencia somente verbal.
5. Toda escrita externa futura nasce como `ExternalActionIntent` local, idempotente,
   auditavel e bloqueada por kill switch ate a fase de APIs.

## 4. Plano priorizado

### P1 - Censo, fundacao de IA e nucleo operacional do SaaS

**Por que:** remove a restricao central e impede que playbook, agentes e OSS criem
fontes paralelas de verdade.

**Entrega:** denominador OSS verificavel, fundacao comum de policy/IA e um Control
Plane local versionado para tenant, assinatura, entitlement, configuracao,
identidade, auditoria e lifecycle.

**Como**

1. Criar snapshot curado do estado atual: inventario de arquivos, segredos,
   licencas e proveniencia; excluir caches/quarentena; separar commits por dominio.
2. Gerar `oss-function-units.lock.json` por extratores especificos: rotas e jobs
   Rails/Vue do Chatwoot; REST/DDP, permissoes, apps e UI do Rocket.Chat; OpenAPI,
   nodes, tools e frontend do ChatterMate; Django/DRF, Svelte, Celery e MCP do
   BottleCRM; exports, props, eventos e API do EmbeddedChat.
3. Reconciliar extracao estatica, schemas/runtime e testes upstream; congelar o
   denominador por commit; revisar manualmente duplicatas e exclusoes proprietarias;
   fazer a CI falhar se uma unidade nao tiver disposicao ou se o pin mudar sem diff.
4. Entregar antes dos agentes o gateway local unico, policy engine deterministico,
   contrato do Context Compiler, telemetria e fake model adapter. P2 usa somente
   essa fundacao, sem chamada de modelo externa.
5. Completar lifecycle local: signup, verificacao simulada, convite, checkout e
   portal de billing simulados, trial, subscription, quotas, dunning, suspensao,
   reativacao, exportacao e encerramento.
6. Aplicar entitlements em toda escrita relevante: usuarios, numeros, agentes,
   conversas IA, audio, arquivos, automacoes, armazenamento e recursos premium.
7. Implementar centro de configuracoes em quatro escopos: plataforma, workspace,
   papel e usuario, com heranca explicita, preview, auditoria e rollback.
8. Preparar provisionamento idempotente dos recursos KLM/OSS, sem criar conta
   externa: apenas planos de execucao, fake adapters e reconciliacao local.
9. Aplicar o hardening valido do QA: rate limit e resposta temporal uniforme em
   login/reset, MFA e sessoes administrativas, JWT com `aud`/`iss`, PyJWT, HMAC e
   replay protection nos webhooks simulados, renderer de templates com allowlist,
   subchaves por finalidade e protecao de uploads/SSRF.

**Configuracoes obrigatorias**

- marca, logos por tema, favicon, cores, tipografia, assinatura, papel timbrado,
  dominio, contatos, dados juridicos e biblioteca de prova social;
- idioma, fuso, moeda, formato de data, densidade, tema, pagina inicial, filtros,
  notificacoes, resumo diario e preferencias de acessibilidade por usuario;
- papeis, permissoes, ocultacao de dados sensiveis, delegacao, dispositivos,
  sessoes, MFA simulada, aprovadores e suporte break-glass temporario/auditado,
  sem acesso padrao da plataforma ao conteudo do cliente;
- planos, limites, uso, feature flags, kill switches, status, suporte e agente de TI;
- upload local S3-compatible, antivirus simulado, retencao, versao, checksum,
  exclusao, exportacao e restore de documentos.

Billing do SaaS e financeiro do escritorio sao dominios distintos: assinatura e
uso da plataforma nunca se misturam com honorarios, recebiveis ou cobranca do caso.

**Gate P1:** o lock de funcoes possui denominador e cobertura reproduziveis para os
cinco pins; registry reconhece os cinco providers; zero item sem disposicao;
lifecycle e quotas passam por testes multi-tenant; nenhuma configuracao critica
depende apenas de JSON opaco; gateway/policy/telemetria passam com fake adapter;
baseline possui SHA, manifesto, comandos e relatorios.

**Confianca:** alta para a fundacao; o censo define o tamanho das fases seguintes.
**Fonte:** S1, S3-S7.

### P2 - Playbook Studio e operacao comercial dos agentes

**Por que:** transforma conhecimento do escritorio em politica executavel,
explicavel e ajustavel sem editar prompts soltos.

**Entrega:** editor nativo, versionado, testavel e publicavel de playbooks por area,
canal, etapa, agente e oferta.

A SPA tera biblioteca em `/app/playbooks`, editor por abas, diff de versao,
preview de marca/proposta, sandbox de conversa, painel de metricas e fluxo de
aprovar/publicar/reverter. Conversas, Agentes, Flows, Propostas e Areas exibem a
politica efetiva e abrem o mesmo registro, sem copias divergentes.

**Conteudo que o playbook obrigatoriamente cobre**

| Bloco | Conteudo minimo |
|---|---|
| Identidade e voz | posicionamento, tom, vocabulario, formalidade, tamanho, saudacao, despedida, claims permitidos/proibidos e exemplos |
| Publico e intencao | personas, dores, urgencia, origem, maturidade, sinais de elegibilidade e exclusao |
| Descoberta | perguntas obrigatorias e condicionais, ordem, quando nao repetir e como confirmar entendimento |
| Triagem juridica | area, subtema, medicamento/tratamento/Home Care/cirurgia/exame, negativa, urgencia, documentos, suficiencia e consulta ao Juridico |
| Qualificacao | score, motivo, capacidade de entrada, forma de pagamento, prazo, decisor, risco, proxima acao e evidencias usadas |
| Oferta e honorarios | ticket inicial minimo de R$ 4.000,00, sem teto; alternativa no exito; honorario intermediario; obrigacao de fazer; concessoes, validade e gates humanos configuraveis |
| Objecoes | preco, sem entrada, preciso pensar, falar com familiar, comparar escritorios, medo, desconfianca, distancia, reuniao online, falta de documentos, prazo, garantia, audio e silencio |
| Contorno de objecao | diagnostico da causa, pergunta de confirmacao, resposta permitida, prova aplicavel, proximo melhor passo, limite de insistencia e criterio de escalada |
| Propostas | versao PDF em cinco paginas e vertical para WhatsApp, blocos, marca, caso, dor, timeline, escopo, valores, CTA, validade, aprovacao, versao e analytics |
| Follow-up | cadencias por etapa, atraso, horario, canal, typing delay, fragmentacao, recuperacao, no-show, proposta, assinatura, pagamento, perdido e remarketing |
| Handoff | resumo obrigatorio, documentos, campos faltantes, objecoes, compromisso assumido, destino e SLA; o Orquestrador coordena handoffs normais e somente Secretaria → Hermes pode abrir a fila humana |
| Pos-venda | contrato, procuracao, documentos, peticao, aprovacao, andamento, cobranca, atualizacoes e WhatsApp disponivel |
| Experimentacao | variantes, grupo de controle, metrica, periodo, criterio de parada e rollback |

O contexto de execucao possui duas fontes autorizadas e rastreaveis: o Card/Caso
360, que referencia dados e documentos, e a conversa omnichannel com seus
metadados e historico. E-mail, WhatsApp, social, audio e reuniao alimentam a mesma
timeline e o mesmo RAG do caso, sem transformar nota interna em mensagem externa.
Consultas entre SDR, Juridico e demais agentes ficam em threads internas visiveis
ao administrador. O Orquestrador coordena o retorno operacional; somente a
Secretaria pode delegar ao Hermes, e somente o Hermes pode abrir a fila humana
depois dessa cadeia verificável. Isso não cria atalho automático: um usuário
autorizado pode assumir a conversa diretamente na SPA, enquanto indisponibilidade
do Hermes ou hard gate mantém a automação bloqueada e a exceção registrada.

Follow-up usa a politica configuravel `lead_never_unanswered`. Por padrao, ele
continua ate resposta, conversao, inelegibilidade confirmada, opt-out/bloqueio ou
interrupcao humana registrada. Cadencia, horario, numero de WhatsApp, canal,
typing indicator, atraso semelhante ao humano e limite anti-spam sao versionados.

**Modos de autonomia**

1. `human_only`: IA apenas resume e recomenda.
2. `ai_triage_human_meeting`: IA atende e qualifica; toda conversao agenda humano.
3. `ai_close_guarded`: IA pode fechar oferta pre-aprovada dentro de limites de
   valor, desconto, risco, documentos e area.
4. `ai_autonomous_supervised`: IA executa acoes reversiveis permitidas, com revisao
   posterior, limite financeiro, budget e kill switch.

Cada modo pode variar por etapa. Assinatura, pagamento, protocolo, peticao,
orientacao juridica conclusiva, concessao fora da politica, conflito, reclamacao,
alto risco, incerteza documental ou baixa confianca exigem aprovacao humana previa.
O gate e do policy engine, nao de uma classificacao de Hermes. Takeover e
break-glass permanecem visiveis em toda conversa. O runtime grava a versao do
playbook, fontes consultadas, regra disparada e motivo da decisao.

**Agentes cobertos:** SDR, Juridico, Closer, Secretaria, Hermes, CRM, Paralegal,
Peticao, Andamento, Financeiro, Cobranca, Marketing, Track, Infoproduto, Audio e TI.
Cada agente recebe objetivo, entradas, ferramentas, permissoes, SLA, criterios de
sucesso, proibicoes, handoffs e fallback.

Cada area plug-and-play empacota taxonomia, campos, documentos, criterios de
qualificacao, grupo de agentes, pipeline, playbook, objecoes, templates, propostas,
honorarios, flows, RAG, aprovacoes, KPIs e testes. Ativar uma nova area nao pode
exigir alterar o core nem expor dados de outra area/workspace.

**Metricas do playbook e do SaaS:** aquisicao/campanha/keyword, CPL, CAC, ROAS,
speed-to-lead, qualificacao, agendamento, show/no-show, proposta, assinatura,
fechamento, motivo de perda, reativacao, velocidade do funil, honorarios previstos
e realizados, SLA, suficiencia documental, handoff, autonomia, aderencia,
afirmacao sem fonte, tokens, custo por lead/cliente, MRR, trial, conversao, churn,
expansao, margem de IA, uso por plano e saude de cada canal. Toda metrica tera
definicao, owner, formula, fonte, janela e dimensoes; nenhum KPI sera apenas visual.

**Gate P2:** um administrador consegue clonar, editar, simular, aprovar, publicar e
reverter playbooks; 100% das respostas simuladas apontam sua versao e politica;
os quatro modos percorrem a jornada de saude sem acao externa.

**Confianca:** media-alta; comportamento exige calibracao. **Dependencia:** P1.
**Fonte:** S1, S2, S5.

### P3 - Convergencia nativa dos motores OSS

**Por que:** reduz dependencia e fragmentacao sem jogar fora maturidade upstream.

**Entrega:** todas as funcoes publicas comunitarias licenciadas independentes da
UI dos sidecars, com contratos e experiencia KLM; adapters e fallbacks preservados
ate prova de paridade.

**Definicao de completude**

Uma `FunctionUnit` e qualquer comportamento publico observavel por usuario,
administrador, API, MCP, worker, webhook, relatorio, importacao/exportacao,
automacao, configuracao, permissao ou integracao. Helpers internos sem contrato
publico nao contam como produto; o comportamento que entregam conta.

O censo sera gerado e revisado diretamente nos pins locais de Chatwoot `v4.15.1`,
ChatterMate `v1.0.14`, BottleCRM `0.46`, Rocket.Chat `8.6.1` e
Rocket EmbeddedChat `v0.2.3`. Para cada unidade, o ledger registra:

- upstream, ref, commit, arquivo/rota original, licenca e dependencia;
- ator, permissao, entradas, saidas, estado, evento, erro e efeito externo;
- entidade canonica KLM, rota SPA, API/application service, agent tool e auditoria;
- modo local, fixture, teste upstream preservado, teste KLM e prova visual;
- estado `discovered`, `mapped`, `native_shadow`, `native_primary`,
  `native_verified`, `blocked_proprietary` ou `blocked_license`.

Nao existe `not_applicable` para funcao comunitaria licenciada dentro dos cinco
pins. Funcoes que dependem de WhatsApp, e-mail, calendario, pagamento, assinatura,
audio, anuncios ou outro provider recebem adapter falso e simulacao integral;
ficam bloqueadas apenas para o efeito de rede. Arvores enterprise excluidas nao
serao copiadas. No EmbeddedChat, o pacote React pode ser reutilizado somente se a
revisao do escopo ISC for positiva; caso contrario, o comportamento publico sera
implementado no KLM sem copiar codigo de licenca incerta.

| Motor | Capacidades a tornar nativas no KLM | Criterio para reduzir dependencia |
|---|---|---|
| BottleCRM | contatos, leads, oportunidades, campos, dedupe, funil, tarefas, atividades, produtos, propostas, invoices, SLA, routing, audit, import/export e MCP | leitura/escrita identica por SPA, API, MCP e reconciliacao; KLM continua canonico |
| Chatwoot | inboxes, multiplos numeros, contatos, conversas, anexos, notas, labels, times, atribuicao, macros, respostas, automacoes, campanhas, SLA, CSAT e relatorios | jornada omnichannel completa sem abrir Chatwoot; transporte continua adapter |
| Rocket.Chat | canais, salas do caso, DMs, threads, mencoes, reacoes, arquivos, busca, pins, presenca, agentes e incidentes | colaboracao interna completa na SPA, com isolamento e historico promovido ao Card somente por acao explicita |
| ChatterMate | agentes, prompts, tools, knowledge, ingestao, flows, preview, execucao realtime, handoff, revisoes, rollback e traces | flow e agente executam por runtime KLM; sidecar vira referencia/fallback administrativo |
| Rocket EmbeddedChat | componentes, mensagens, threads, anexos, reacoes, composer, eventos e customizacao expostos pelo pacote publico | comportamento equivalente nativo; reutilizacao de codigo condicionada ao escopo de licenca |

Os cinco pins acima formam o inventario fechado desta meta. Qualquer outra
referencia localizada depois entra primeiro no lockfile, passa por licenca e censo
e volta ao proprietario para aprovacao; nao existe incorporacao silenciosa.

Aplicar padrao strangler por capacidade: `preserved -> mapped -> native_shadow ->
native_primary -> native_verified -> sidecar_fallback -> retirement_candidate`.
O gate desta meta exige `native_verified`. Funcao de usuario/administrador recebe
UI KLM real em desktop/mobile; API/MCP, worker, webhook e persistencia recebem o
perfil de prova proprio e aparecem no KLM por configuracao, auditoria ou monitoracao,
sem exigir uma pagina artificial por endpoint. Toda unidade possui comportamento,
permissoes, erros e teste com sidecar desligado.
Nenhuma capacidade chega aos estados posteriores sem comparacao funcional,
migracao, rollback, ledger de licenca e aprovacao do proprietario. Nao copiar
codigo enterprise ou sem licenca.

Para cada entidade e unidade, o cutover registra fonte canonica, ownership de
campos, transformacao, esquema de evento, direcao de sync, outbox/inbox,
idempotencia, consistencia esperada, shadow read/dual-write quando inevitavel,
backfill, reconciliacao, conflito, cutover, rollback e teste sidecar-off.

EmbeddedChat recebe slice proprio: censo do pacote/API, decisao documentada sobre
o escopo ISC, provider no registry e suite dedicada. Rocket.Chat `6.13.1` nao sera
aceito como oraculo de `8.6.1`: o gate exige runtime comunitario equivalente ou
prova source-level/upstream por unidade quando o build for tecnicamente bloqueado.

**Gate P3:** censo completo ligado aos cinco commits pinados; 100% das
`FunctionUnit` licenciadas em `native_verified`; zero `discovered`, `mapped` ou
`native_shadow`; zero launcher como unico caminho; zero divergencia apos
replay/restart; e sidecars desligados nao impedem as funcoes equivalentes.

**Confianca:** media. **Esforco:** estimado por `FunctionUnit` somente depois do
lock de P1. **Dependencia:** P1. **Fonte:** S3, S5, S6.

### P4 - Convergencia do runtime, RAG, custo e performance

**Por que:** autonomia sem governanca aumenta custo, latencia e risco.

**Entrega:** todos os chamadores convergidos para a fundacao P1, contexto minimo
suficiente, RAG isolado, eficiencia mensurada e stack local equivalente a producao.

1. Encaminhar chat, audio, imagem, insights, classificacao e agentes pelo gateway
   de P1, com catalogo versionado, caps, budget, circuit breaker e telemetria.
2. Completar o `Context Compiler`: estado estruturado do Card + janela recente da
   conversa + resumo rolante + documentos recuperados; nunca enviar historico
   completo por padrao.
3. Usar busca hibrida e filtros obrigatorios de workspace/caso/documento;
   registrar proveniencia e recusar resposta quando a evidencia exigida faltar.
4. Aplicar cache apenas a conteudo reutilizavel nao sensivel, prompt caching,
   batch para classificacoes, compressao de tool results e modelo por tarefa
   validado pela suite de avaliacao, nao apenas por preco.
5. Corrigir caminhos quentes ja auditados: Redis singleton, bcrypt fora do event
   loop, N+1 do inbox, fan-out Celery, dashboard agregado, `SCAN`, memoizacao do
   frontend, debounce/backoff, AbortController, videos e same-origin.
6. Rodar localmente PostgreSQL/pgvector, Redis, object storage S3-compatible,
   Mailpit, fake providers, workers e proxy. Bancos so serao consolidados apos ADR,
   testes de versao, carga e restore.
7. Enxugar o runtime local com `.dockerignore`, builds multi-stage, imagens
   reproduziveis, limites de CPU/memoria, healthchecks, rotacao de logs e profiles
   por motor; preservar datastores separados quando versao ou blast radius exigir.

O benchmark local versiona dataset, jornadas, prompts, playbook, fake model,
tokenizer, versao das bibliotecas, seeds, temperatura, perfil da maquina, rubrica,
scorer e intervalo estatistico. A meta de 40% mede eficiencia estrutural do
contexto. Nao estima preco, latencia ou qualidade de OpenAI/Anthropic/Gemini; isso
sera um gate proprio na fase de APIs, antes da AWS.

**Gate P4:** 100% das chamadas atribuidas; budget nunca ultrapassado por corrida;
reducao de tokens e SLOs atingidos; qualidade nao inferior ao limite definido;
restart nao duplica mensagem, tarefa, cobranca ou mudanca de CRM.

**Confianca:** media-alta. **Dependencias:** P1-P3.
**Fonte:** S2, S7, S8.

### P5 - QA total, simulacoes e benchmark final

**Por que:** o produto so pode ser entregue pela evidencia do comportamento, nao
pela quantidade de codigo ou pela existencia de rotas.

**Entrega:** laudo local reproduzivel, pacote visual e backlog competitivo final.

**Perfis obrigatorios de prova**

| Superficie | Prova primaria |
|---|---|
| UI e controle de usuario/admin | Playwright, visual, acessibilidade e acao observavel |
| API e MCP | schema, contrato, auth, RBAC, idempotencia e fuzz de payload |
| Worker, scheduler e flow | integracao, time travel, concorrencia, replay e restart |
| Webhook e adapter | assinatura simulada, receipt, replay, timeout e reconciliacao |
| Persistencia, busca e RAG | PostgreSQL real, RLS, ACL, migracao, integridade e restore |
| Jornada entre modulos | E2E desktop/mobile e simulacao de agentes |

Dispositivos, temas, papeis, erros e estados usam matriz pairwise; jornadas
criticas e controles financeiros/juridicos cobrem todas as combinacoes de risco.
Isso evita um produto cartesiano artificial sem reduzir a cobertura relevante.

**Matriz complementar**

- unitarios, schemas, migrations, API, WebSocket, workers, MCP, adapters e contratos;
- Playwright para todas as rotas e controles de UI descobertos, desktop, tablet e
  mobile, light/dark, teclado, foco, hover, offline, vazio, erro e sem permissao;
- testes RLS/RBAC/IDOR e RAG com dois workspaces e casos adversariais;
- concorrencia, idempotencia, retry, replay, restart, timeout, sidecar indisponivel,
  fila acumulada, restore e rollback;
- carga com dataset documentado e query budgets por endpoint;
- SAST, dependencias, secrets, SBOM, licencas, uploads e headers;
- bloqueio e captura independente de egress da stack de QA, provando em nivel de
  rede que nenhum provider ou AWS foi alcancado;
- pelo menos 1.000 conversas sinteticas deterministicas cobrindo subtemas,
  objecoes, documentos faltantes, audio, no-show, follow-up, handoff, fechamento,
  cobranca, opt-out e bloqueio;
- replay de jornadas douradas para cada modo de autonomia e cada agente;
- validacao de propostas PDF/WhatsApp, marca, valores, links e versoes;
- varredura automatica de botoes sem acao, rotas quebradas, placeholders, dados
  compactados como `K` e efeitos externos diferentes de zero.

**Criterios finais:** zero falha; zero P0/P1; zero vazamento; zero acao externa;
100% dos controles com efeito, feedback ou bloqueio verdadeiro; 100% dos cenarios
de alto risco com handoff correto; conformidade estrutural do playbook >= 95%;
nenhuma afirmacao juridica sem fonte nos cenarios que a exigem. Fluencia e qualidade
do modelo externo permanecem explicitamente nao homologadas nesta fase.

Depois do QA, realizar o ultimo benchmarking com fontes atuais e evidencias por
funcao contra, no minimo, AdvHub, SabioAdv, Guimoo, WaLeads, ManyChat, Chatwoot,
ChatterMate, Rocket.Chat, BottleCRM e referencias maduras de legal CRM,
omnichannel e automacao. Comparar jornada, autonomia, CRM, atendimento, agenda,
propostas, analytics, marketing, flows, governanca, mobile, custo e tempo de
operacao. Gaps entram no backlog; nenhuma mudanca regressiva e feita sem aprovacao.

**Gate P5:** relatorio ligado ao digest/commit, comandos, ambiente, logs, JUnit,
Playwright, screenshots, videos, metricas, matriz competitiva e riscos residuais.

**Confianca:** alta para QA; media para superioridade de mercado, que depende de
evidencia atual. **Dependencias:** P1-P4. **Fonte:** S1,
S4, S6, S7.

## 5. Sequencia e estimativa

| Horizonte | Esforco | Gate |
|---|---|---|
| Agora | P1 Censo, fundacao IA e Nucleo SaaS | denominador, tenancy, policy, configuracao e evidencia |
| Em seguida | P2 Playbook Studio | operacao comercial simulada e versionada |
| Depois | P3 Convergencia OSS | todas as funcoes licenciadas nativas e fallback comprovado |
| Depois | P4 IA e performance | custo, qualidade, latencia e resiliencia |
| Fechamento | P5 QA e benchmark | pacote para aprovacao do proprietario |

Nao ha prazo global honesto antes do lock granular de P1. Depois do censo, o plano
recebe estimativa bottom-up por `FunctionUnit`, jornada vertical, migracao, QA e
revisao, com caminho critico e capacidade real. Prometer paridade total antes de
conhecer o denominador seria artificial.

### Nao fazer nesta meta

- nao ativar WhatsApp, e-mail, Google Calendar, Teams, ElevenLabs, ZapSign,
  Asaas/Stripe, pixels, modelos pagos ou qualquer outro provider real;
- nao provisionar, alterar ou desligar recursos AWS;
- nao commitar `.vendor`, quarentena, secrets ou artefatos sem inventario;
- nao remover endpoint, tela, upstream ou funcao por parecer sem uso;
- nao fundir bancos/Redis ou abandonar sidecar sem ADR, migracao, prova e aprovacao;
- nao declarar paridade, seguranca, conformidade ou superioridade sem evidencia.

## 6. Pre-mortem

| Falha presumida | Sinal precoce | Mitigacao |
|---|---|---|
| Escopo cresce e nenhuma jornada fecha | muitas capacidades `native_shadow`, poucos gates completos | limitar WIP a uma jornada vertical por vez |
| UI parece nativa, mas depende silenciosamente do sidecar | queda do motor quebra a acao principal | chaos test e fallback por capacidade |
| Playbook vira prompt grande e caro | tokens crescem com cada regra nova | regras estruturadas, retrieval seletivo e compilacao de contexto |
| IA fecha fora da politica | concessao ou claim sem regra citada | policy engine deterministico, limites e kill switch |
| Dados divergem entre KLM e OSS | segunda reconciliacao altera registros | KLM canonico, outbox/inbox, idempotencia e comparador |
| QA numeroso mascara pouca cobertura | testes passam sem validar efeito | cobertura por risco, estado e efeito, nao por contagem |
| Repo fica irreproduzivel | resultado depende do worktree local | snapshot curado, lockfiles, digest e manifests assinados |

## 7. Briefs para os proximos artefatos

1. **`$pop-deliver-prd-pt`:** transformar P1-P4 em PRD tecnico-funcional com
   historias, regras de negocio e criterios Given/When/Then, preservando todos os
   gates e o bloqueio externo deste plano.
2. **`$pop-measure-instrumentation-spec`:** especificar eventos, propriedades,
   identidades, funis e metricas de playbook, agentes, custo, handoff, propostas,
   integracoes e SaaS, sempre com `workspace_id` e regras de privacidade.
3. **`$pop-deliver-edge-cases`:** produzir matriz de abuso, concorrencia,
   idempotencia, isolamento, billing, RAG, documentos, canais, autonomia e
   indisponibilidade que alimentara P5.

## 8. Evidencia e aprovacao

| Decisao | Fonte |
|---|---|
| Trabalho exclusivamente local | S4 |
| KLM como fonte canonica | S5 |
| Incorporar sem apagar funcoes | S3, S6 |
| Playbook, marca e autonomia configuraveis | S2 |
| Priorizar custo, latencia e qualidade | S1, S8 |
| QA final e evidencia reproduzivel | S7 |

### Pacote que volta ao proprietario

- demonstracao local das jornadas completas e dos quatro modos de autonomia;
- matriz SaaS, playbook, agentes, permissoes, OSS e integracoes simuladas;
- comparativo de custo, tokens, latencia e qualidade antes/depois;
- censo completo de `FunctionUnit`, incluindo as 135 familias atuais,
  proveniencia, licencas, paridade e dependencia residual;
- laudo QA, benchmark competitivo, screenshots/videos e riscos residuais;
- lista separada e exata das APIs/credenciais a solicitar na proxima fase;
- plano AWS ainda nao executado, recalculado somente depois da homologacao das APIs.

O status somente muda para `LOCAL_SAAS_APPROVAL_READY_V5` quando todos os gates
P1-P5 estiverem aprovados. Ate la, qualquer afirmacao de conclusao e parcial.

### Revisao independente do plano

Em 19/07/2026, uma revisao tecnica independente apontou dez lacunas iniciais em
censo, EmbeddedChat, versao do Rocket.Chat, supersessao, ordem do gateway,
handoff, QA, estimativa, cutover e mensuracao de tokens. Todas foram incorporadas.
A segunda passagem aprovou este documento como plano de execucao local sem P0/P1.
Isso nao equivale a aprovar a implementacao nem substitui a decisao do proprietario.
