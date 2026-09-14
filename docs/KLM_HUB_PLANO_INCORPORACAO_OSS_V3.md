# KLM Hub - Plano de Incorporacao OSS V3

> **Status:** `SUPERSEDED_NON_EXECUTABLE`. A meta vigente esta em
> [`KLM_HUB_META_E_PLANO_SAAS_LOCAL_V5_2026-07-19.md`](./KLM_HUB_META_E_PLANO_SAAS_LOCAL_V5_2026-07-19.md).
> Este V3 permanece como historico e fonte de requisitos, nao como autorizacao
> independente de execucao.

**Estado historico em 17/07/2026:** Ondas 0-4 concluidas e Onda 5 em hardening local
**Data:** 17 de julho de 2026
**Escopo:** Chatwoot, Rocket.Chat, ChatterMate e BottleCRM dentro da experiencia KLM
**Gate:** APIs reais, canais externos, credenciais e AWS continuam bloqueados

## 1. Meta

Incorporar de forma visivel, nativa e testada na SPA do KLM Hub as capacidades
comunitarias aprovadas de Chatwoot, Rocket.Chat, ChatterMate e BottleCRM,
preservando os upstreams e usando-os como motores por bridges, APIs,
microfrontends ou deep links autenticados.

A entrega somente sera considerada concluida quando:

- cada capacidade publica encontrada estiver classificada no ledger;
- cada capacidade aprovada estiver acessivel pela navegacao ou pelo contexto do KLM;
- cada funcao tiver comportamento local real, permissao, estado de erro e teste;
- nenhuma funcao for declarada integrada apenas porque o sidecar esta online;
- o KLM permanecer como fonte canonica de pessoa, caso, documentos, RAG,
  honorarios, decisoes e auditoria juridica;
- todas as integracoes externas continuarem em simulacao local ate nova aprovacao.

## 2. Correcao de status

O estado anterior era infraestrutura local operacional com incorporacao parcial:

- quatro sidecars executavam e podiam ser abertos pela SPA;
- Chatwoot, Rocket.Chat e BottleCRM possuiam bridges seletivas;
- ChatterMate possuia runtime e interface completos, mas sem sync nativo KLM;
- a area `Ferramentas > Modulos integrados` funcionava principalmente como
  launcher, health e controle de sincronizacao;
- muitas capacidades dos upstreams nao apareciam nas rotas operacionais KLM.

Portanto, `sidecar_ready`, `bridge_ready` e `frontend_integrated` passam a ser
estados independentes. A meta V3 corrige essa lacuna.

## 3. Restricao principal

O gargalo atual nao e disponibilidade dos produtos. E a ausencia de uma camada
de incorporacao que transforme capacidades externas em operacoes coerentes com
o caso juridico, o RBAC, a navegacao e a linguagem visual do KLM.

Enquanto essa camada nao existir, aumentar o numero de bridges ou sidecars cria
mais caminhos isolados sem melhorar proporcionalmente a operacao diaria.

## 4. Regra de nenhum recurso silenciosamente dormente

Cada capacidade tera um unico estado verificavel:

| Estado | Significado |
|---|---|
| `discovered` | encontrada no pin, ainda nao decidida |
| `approved_native` | sera exposta em componente KLM sobre API/bridge |
| `approved_embedded` | sera carregada como microfrontend autenticado |
| `approved_full_app` | permanecera no produto completo, com entrada contextual KLM |
| `implemented` | acessivel e testada no KLM |
| `not_applicable` | preservada, mas sem uso no escritorio; justificativa obrigatoria |
| `blocked_license` | depende de licenca nao disponivel |
| `blocked_dependency` | depende de versao, infraestrutura ou compatibilidade |
| `blocked_external_gate` | depende de API, credencial ou efeito externo ainda proibido |

Nao podera existir capacidade em `discovered` no pacote final. Recursos nao
aplicaveis nao serao apagados: continuarao preservados no upstream e visiveis no
catalogo administrativo, com sua justificativa.

## 5. Arquitetura de incorporacao

### 5.1 Shell KLM

A SPA continua controlando:

- navegacao, tema, responsividade e acessibilidade;
- sessao, workspace, usuario, papel e permissoes;
- `person_id`, `matter_id`, `conversation_id`, `flow_run_id` e `agent_run_id`;
- feature flags, kill switches, health e circuit breaker;
- telemetria, autoria, confirmacoes e trilha de auditoria;
- retorno ao mesmo lead/caso depois de abrir um modulo completo.

### 5.2 Tres formas permitidas de incorporar

1. **Nativo sobre API/bridge:** caminho padrao para tarefas frequentes e
   contextualizadas no caso.
2. **Microfrontend:** permitido para ChatterMate e BottleCRM quando isolamento,
   autenticacao, tema e error boundary forem comprovados.
3. **Produto completo autenticado:** usado para administracao avancada ou quando
   headers/licenca/compatibilidade tornam o embed inadequado. Deve abrir pelo
   contexto KLM e voltar ao mesmo registro.

Um simples link generico em Ferramentas nao satisfaz nenhum desses criterios.

## 6. Superficies finais

| Motor | Superficie primaria KLM | Entradas contextuais |
|---|---|---|
| Chatwoot | Conversas e Omnichannel | Caso 360, contato, campanha, flow e agenda |
| Rocket.Chat | Equipe e Interno | sala do caso, consulta entre agentes, TI e incidentes |
| ChatterMate | Flows e Automacao IA | conversa, caso, agente, knowledge e execucao realtime |
| BottleCRM | CRM, Funil e Administracao | lead, cliente, oportunidade, tarefa, proposta e financeiro |

`Ferramentas > Modulos integrados` permanecera como console tecnico: status,
versao, sync, logs, recursos, licenca, dependencia, upgrade e abertura completa.
Nao sera mais o unico caminho para usar os produtos.

## 7. Plano de execucao

### Onda 0 - Baseline e ledger funcional

**Entregas**

- inventario automatizado e revisao manual das capacidades comunitarias dos pins;
- matriz por recurso, tela, endpoint, permissao, dependencia e estado;
- mapa do que ja existe no frontend, backend, sidecar ou somente no checkout;
- nova terminologia de status aplicada aos endpoints e ao console tecnico;
- baseline reproduzivel dos testes e screenshots atuais.

**Gate G0:** 100% das capacidades classificadas, sem alegacao de equivalencia
integral baseada apenas em health check.

### Onda 1 - Plataforma comum de incorporacao

**Entregas**

- registry tipado de modulos e capacidades;
- sessao local assinada, contexto juridico e retorno contextual;
- RBAC unico e mapeamento de papeis para cada sidecar;
- component shell para nativo, embed e full app;
- health, timeout, retry, offline, erro parcial e fallback;
- auditoria de lancamento, leitura, escrita, sync e reconciliacao;
- menu e busca global incluindo recursos incorporados.

**Gate G1:** nenhum modulo acessa outro workspace ou perde o contexto do caso;
indisponibilidade de um sidecar nao derruba a SPA.

### Onda 2 - Chatwoot no Atendimento

**Capacidades operacionais**

- inboxes e multiplos numeros;
- contatos, conversas, busca, filtros, status e prioridade;
- timeline, composer, anexos, midia, templates e respostas salvas;
- notas privadas, labels, atribuicao, times e takeover humano;
- autoria de bot, agente e advogado;
- horario de atendimento, SLA, CSAT e historico;
- macros, automacoes, campanhas, webhooks e relatorios comunitarios;
- e-mail e anexos entrando no Card/RAG por bridge idempotente.

**Adaptacao KLM**

- painel juridico lateral, taxonomia, documentos e suficiencia;
- ferramentas comerciais, proposta, agenda, flow e handoff;
- regra de resposta pelo inbox de origem;
- visibilidade de agente, modo IA e aprovacao humana;
- links diretos entre conversa, pessoa, caso e sala interna.

**Gate G2:** jornada local completa em dois numeros simulados e uma mailbox,
incluindo nota interna, takeover, anexo/RAG, retry e zero duplicidade.

### Onda 3 - Rocket.Chat no trabalho interno

**Capacidades**

- canais, salas privadas, DMs, threads, mencoes e reacoes;
- arquivos, busca, pins, favoritos, edicao e tombstones;
- presenca, notificacoes e administracao comunitaria aplicavel;
- bots dos agentes com identidade e capabilities;
- salas por caso e canais Comercial, Juridico, Secretaria, Paralegal,
  Peticoes, Financeiro, Marketing, Track, TI e Incidentes.

**Adaptacao KLM**

- painel compacto da sala dentro de Conversas e Caso 360;
- workspace completo em `Equipe/Interno`;
- mencao que cria consulta ou tarefa de agente;
- promocao controlada de decisao para Card/RAG;
- trilha SDR -> Juridico -> CRM -> Hermes visivel e auditavel.

**Gate G3:** conversa bidirecional, thread, arquivo, mencao de agente, mudanca de
membro e promocao ao Card funcionam sem vazamento entre casos.

### Onda 4 - ChatterMate em Flows e Automacao IA

**Capacidades**

- editor visual, nodes, connections, forms, conditions e preview;
- publicacao, revisoes, versoes e rollback;
- handoff, transfer agent, structured output e mensagens intermediarias;
- knowledge search, ingestao, proveniencia e escopo;
- catalogo de provedores/modelos em simulacao local;
- execucao realtime no grafo com logs, inputs, outputs e erros.

**Adaptacao KLM**

- DSL tipada e nos juridicos;
- modos deterministic, copiloto, IA com gate, autonomo e somente humano;
- ativar, pausar, retomar, desativar e cancelar definicoes e execucoes;
- resource locks para flows simultaneos;
- handoff Chatwoot/Rocket.Chat e tools KLM por capability;
- RAG isolado por workspace e caso.

**Gate G4:** flow de follow-up e flow de recuperacao executam em conjunto com e
sem IA, mostram o grafo em tempo real e retomam corretamente apos restart.

### Onda 5 - BottleCRM no CRM juridico

**Execução em 17/07/2026:** sync reconciliável e os quatro contextos nativos
(Funil, Caso 360, Agenda e Financeiro) estão implementados e cobertos em desktop
e mobile. A matriz diferencia as leituras parciais das famílias integralmente
implementadas. O gate permanece aberto para paridade de escrita por MCP,
deduplicação e movimentos de boards do próprio BottleCRM.

**Capacidades**

- contatos, leads, oportunidades, casos, tarefas e atividades;
- custom fields, deteccao e unificacao de duplicados;
- importacao/exportacao, kanban e notas internas;
- business hours, SLA, routing, aprovacao e auditoria;
- produtos, propostas, invoices e relatorios quando aderentes ao dominio;
- MCP, tokens escopados, RLS, RBAC e suspicious activity.

**Adaptacao KLM**

- entidades genericas mapeadas para Pessoa, Lead, Matter, Proposta, Honorario,
  Pagamento e Tarefa sem criar segunda fonte juridica;
- CRM Agent operando pela mesma application layer da SPA;
- sync reconciliavel com external IDs, idempotencia e recuperacao;
- recursos administrativos long-tail acessiveis pelo produto completo.

**Gate G5:** criar, atualizar, deduplicar e mover um lead pelo kanban produz o
mesmo estado pela SPA, API, MCP e sidecar apos duas reconciliacoes consecutivas.

### Onda 6 - Jornadas unificadas AI-first

**Entregas**

- negativa de plano de saude de ponta a ponta;
- WhatsApp/e-mail -> triagem -> Juridico -> CRM -> Closer -> Secretaria;
- reuniao -> proposta -> contrato/procuracao -> Peticao -> Andamento;
- marketing, track, financeiro e cobranca em simulacao;
- timeline unica por pessoa/caso, sem misturar notas internas e mensagens externas;
- realtime, atividades, alertas e KPIs com fonte identificada;
- autoria e contexto preservados entre todos os motores.

**Gate G6:** a jornada completa e reproduzivel por fixture e nao exige abrir
manualmente Ferramentas para continuar o trabalho.

### Onda 7 - UX, mobile e operacao

**Entregas**

- desktop, tablet e mobile com light/dark mode;
- rotas, drawers e atalhos consistentes;
- loading, vazio, erro, offline, sem permissao e degradacao parcial;
- teclado, foco, contraste e leitores de tela;
- performance, lazy loading e isolamento de bundle;
- observabilidade por sidecar, bridge, feature e jornada.

**Gate G7:** zero sobreposicao ou scroll horizontal acidental nos breakpoints,
zero botao sem acao e nenhuma falha de sidecar bloqueia o restante do KLM.

### Onda 8 - Hardening e aprovacao

**Entregas**

- contratos, integracao, E2E, seguranca, concorrencia e reconciliacao;
- restart/chaos, indisponibilidade, timeout, token expirado e rollback;
- regressao visual desktop/mobile e verificacao de acessibilidade;
- SBOM, licencas, proveniencia, diff de upstream e ensaio de upgrade;
- pacote de aprovacao com matriz funcional e riscos residuais.

**Gate G8:** todas as metas quantitativas abaixo atendidas. Somente entao o
produto volta ao proprietario para aprovacao das APIs, ainda sem ativa-las.

## 8. Resultados-chave

1. **Cobertura funcional:** 100% das capacidades comunitarias classificadas e
   0 itens `discovered` no ledger final.
2. **Visibilidade:** 100% das capacidades aprovadas acessiveis pela SPA como
   nativa, embedded ou full app contextual; 0 modulo restrito a um card launcher.
3. **Contexto:** 100% das operacoes de negocio carregam workspace e, quando
   aplicavel, pessoa, caso, conversa, flow e agente.
4. **Confiabilidade:** zero mensagens, tarefas ou atualizacoes duplicadas nos
   cenarios de replay, retry, restart e duas reconciliacoes consecutivas.
5. **Isolamento:** zero leitura ou escrita cross-workspace/case nos testes RLS,
   RBAC, API, MCP, bridge e microfrontend.
6. **UX:** zero controles decorativos; toda acao produz efeito, feedback ou
   bloqueio explicado. Desktop e mobile passam pela matriz visual.
7. **Testes:** manter toda a baseline verde e adicionar no minimo 160 cenarios
   orientados aos novos contratos e jornadas, sem usar quantidade como substituto
   de cobertura de risco.
8. **Resiliencia:** cada sidecar pode parar e voltar sem derrubar a SPA, perder
   contexto ou repetir efeitos.
9. **Proveniencia:** 100% dos recursos importados/adaptados registram projeto,
   pin, commit, licenca, caminho, alteracao, testes e estrategia de atualizacao.
10. **Gate externo:** zero chamada real de WhatsApp, e-mail, agenda, assinatura,
    pagamento, modelo de IA ou AWS antes de autorizacao expressa.

## 9. Matriz minima de testes novos

| Familia | Minimo |
|---|---:|
| registry, shell, sessao, contexto e RBAC | 20 |
| Chatwoot e jornada omnichannel | 35 |
| Rocket.Chat e colaboracao por caso | 25 |
| ChatterMate, flows, knowledge e handoff | 30 |
| BottleCRM, sync, MCP, RLS e audit | 25 |
| jornadas cruzadas e agentes | 15 |
| E2E e regressao desktop/mobile | 10 |

Os minimos nao dispensam testes adicionais para qualquer risco descoberto.

## 10. Fora desta meta

- ativar WhatsApp Cloud, mailbox real, Google Calendar, ZapSign, Asaas,
  ElevenLabs, provedores de modelo ou pixels;
- provisionar AWS ou qualquer ambiente online;
- importar codigo enterprise ou sem licenca verificada;
- tornar Chatwoot, Rocket.Chat, ChatterMate ou BottleCRM fonte canonica do caso;
- duplicar no KLM uma tela upstream inteira quando uma incorporacao segura e
  contextual resolver a mesma necessidade;
- esconder uma capacidade sem registra-la e justificar sua disposicao.

## 11. Pacote final de aprovacao

O pacote precisa conter:

- ledger funcional completo e comparativo antes/depois;
- mapa de rotas, entradas contextuais e permissoes;
- matriz endpoint/bridge/evento/sync;
- matriz desktop/mobile e light/dark;
- relatorio de testes com comandos, resultados e cobertura de risco;
- screenshots e gravacoes das quatro incorporacoes;
- relatorio de licenca, SBOM, provenance e upgrade;
- riscos residuais e itens bloqueados;
- checklist separado das APIs e acessos futuros.

Essa e a unica condicao para declarar que nada relevante ficou silenciosamente
dormindo dentro dos produtos baixados.
