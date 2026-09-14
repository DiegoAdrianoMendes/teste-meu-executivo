# KLM Hub - Meta e Plano Omnichannel V6

**Data:** 20 de julho de 2026  
**Status:** `APROVADO_EM_EXECUCAO_LOCAL`  
**Gate externo:** fechado  
**AWS:** fora do escopo  
**Regra de produto:** acrescentar sem retirar ou remodelar funcao aprovada, salvo
instrucao expressa do proprietario.

Este documento amplia o
[`KLM_HUB_META_E_PLANO_SAAS_LOCAL_V5_2026-07-19.md`](./KLM_HUB_META_E_PLANO_SAAS_LOCAL_V5_2026-07-19.md)
para inbox, social, anuncios, formularios e Copilot. O V5 continua valido para
SaaS, billing, playbooks, agentes, OSS, operacao juridica e Control Plane. Em
conflito apenas sobre omnichannel, este V6 prevalece.

O benchmark que fundamenta as decisoes esta em
[`BENCHMARK_OMNICHANNEL_KOMMO_HELENA_V6_2026-07-20.md`](./BENCHMARK_OMNICHANNEL_KOMMO_HELENA_V6_2026-07-20.md).

## 1. Meta ajustada

### Meta

Entregar o estado `LOCAL_OMNICHANNEL_APPROVAL_READY_V6`: um KLM Hub local no
qual cada pessoa/lead possua uma identidade canonica, um card juridico e uma
unica conversa continua, mesmo quando interage por WhatsApp, Instagram,
Messenger, TikTok, e-mail, formulario do site, formulario de anuncio, comentario
ou chamada; humanos, equipes, flows e agentes operam essa conversa por uma SPA
nativa, auditavel, responsiva e sem qualquer efeito externo.

### North Star

**Percentual de leads elegiveis que recebem o proximo passo correto dentro do
SLA, na conversa canonica certa, sem duplicacao, vazamento, janela de canal
violada ou acao externa nao autorizada.**

### Resultados-chave obrigatorios

1. 100% das ingestões simuladas de canais convergem para `OmnichannelThread` e
   produzem no maximo uma mensagem/evento por idempotency key.
2. 100% dos canais de um mesmo identificador verificado aparecem na mesma
   timeline; identidades ambiguas nunca sao mescladas automaticamente.
3. WhatsApp suporta localmente modos `cloud_api` e `coexistence`, multiplos
   numeros, capability matrix, janela, template, custo, qualidade e transporte
   selecionado, sem alegar conexao real.
4. Instagram, Messenger e TikTok possuem conexao, webhook, DM, comentario,
   resposta, moderacao e limites modelados; a UI desabilita o que a capability
   matrix nao autoriza.
5. Formularios Meta, TikTok e site/Lovable criam receipt, identidade, card,
   thread, atribuicao e follow-up sem duplicar em replays.
6. Comentarios podem acionar flows com filtro por post/anuncio/palavra-chave,
   resposta publica, resposta privada, like/moderacao, atraso e opt-out, sempre
   limitados pela plataforma.
7. Usuarios podem criar equipes e grupos, atribuir membros, transferir uma
   conversa, assumir, devolver a fila, concluir e reabrir; cada transicao gera
   evento append-only.
8. Mensagens podem ser agendadas, pausadas, canceladas e reconciliadas por thread,
   canal e numero, com quiet hours, janela, template, lease e retry.
9. Uma chamada possui solicitacao, permissao, estado, participante, timestamps,
   gravacao/transcricao apenas quando configurada, custo e vinculo ao caso.
10. O Copilot resume, sugere resposta, reescreve, traduz, detecta perguntas sem
    resposta, extrai tarefa/reuniao, consulta disponibilidade e responde sobre
    metricas; toda sugestao e editavel e auditada.
11. A inbox oferece `Novos`, `Meus`, `Equipe`, `Outros`, `Nao lidos`,
    `Favoritos`, filtros por canal/numero/time/agente/status/SLA/tag/origem e
    views salvas.
12. O painel direito continua exibindo Caso 360 completo, arquivos, contratos,
    procuracao, proposta, agenda, financeiro, RAG, timeline, logs, agentes e
    links internos da SPA.
13. A timeline diferencia mensagem ao cliente, comentario publico, DM, nota
    interna, conversa de agentes, evento de CRM, chamada, agenda e automacao.
14. O CRM Agent recebe comandos tipados para qualificar, etiquetar, alterar
    etapa, atribuir valor, registrar objecao e proxima acao; nunca edita por texto
    livre sem audit trail.
15. Attribution liga origem, UTM, gclid, fbclid, ttclid, campanha, conjunto,
    anuncio, criativo, formulario, palavra-chave, conversa, proposta, contrato e
    receita.
16. Dashboard mede aquisicao, fila, atendimento, comercial, automacao, IA,
    canais, custos e conversao com comparativo de periodo e drill-down.
17. Zero leitura, escrita, busca, cache, RAG, arquivo, evento ou analytics cruza
    workspace; a suite cobre duas empresas e dois casos com dados semelhantes.
18. p95 local dos endpoints quentes fica abaixo de 300 ms no perfil documentado;
    abertura da conversa usa no maximo quatro requests iniciais e cache coerente.
19. Todas as acoes visiveis possuem efeito local, estado desabilitado explicito
    ou mensagem de capacidade; zero botao decorativo.
20. QA final fecha com zero P0/P1, suites verdes, manifesto por SHA, screenshots
    desktop/tablet/mobile e revisao independente.

## 2. Definicao de pronto

Uma capacidade so conta como pronta quando os sete itens existem:

1. modelo ou estado persistente;
2. contrato de dominio e API;
3. experiencia nativa na SPA;
4. permissao e isolamento;
5. idempotencia, erro e reconciliacao;
6. metrica/log/auditoria;
7. teste automatizado e evidencia local.

Adapter, launcher, card informativo, dado sintetico ou sidecar isolado nao contam
como implementacao nativa.

## 3. Arquitetura-alvo

### 3.1 Agregados canonicos

| Agregado | Responsabilidade | Regra de identidade |
|---|---|---|
| `OmnichannelIdentity` | pessoa/lead canonico do workspace | uma por pessoa conhecida; pode existir sem cliente/matter |
| `OmnichannelEndpoint` | endereco externo: telefone, IG-scoped ID, PSID, TikTok ID, e-mail | unico por workspace, provider, conta e external ID normalizado |
| `OmnichannelThread` | conversa continua e estado operacional | uma thread ativa primaria por identidade/card, com reabertura/versionamento |
| `OmnichannelBinding` | liga uma conversa do provider a thread canonica | unico por workspace, provider, connection e external thread ID |
| `OmnichannelMessage` | texto, midia, nota ou mensagem de sistema | payload integral idempotente e audiencia explicita |
| `ChannelEvent` | comentario, like, story, formulario, chamada, status e automacao | append-only; evento original nunca e sobrescrito |
| `RoutingEvent` | atribuicao, transferencia, takeover, conclusao, reabertura | append-only e causal |
| `LeadCaptureReceipt` | ingestao Meta/TikTok/site | chave do provider + payload hash + resultado materializado |
| `AutomationTriggerReceipt` | garante uma execucao por gatilho/flow/revisao | chave do evento + flow revision + action key |
| `OmnichannelSchedule` | envio futuro | lease, janela, template, cancelamento, retry e reconciliacao |
| `CallSession` | chamada de canal/voz | maquina de estados e custo observavel |

### 3.2 Merge de identidade

Ordem de prova para merge automatico:

1. endpoint ja vinculado;
2. cliente/card informado por token first-party assinado;
3. telefone E.164 verificado;
4. e-mail verificado;
5. provider-scoped ID previamente ligado por humano;
6. nenhum dos anteriores: criar identidade provisoria e fila de possivel
   duplicidade.

Nome, avatar, username parecido, texto da conversa ou sugestao de IA nunca sao
suficientes para merge automatico. Merge manual exige permissao, preview de
impacto, optimistic lock, evento e possibilidade de desfazer por compensacao.

### 3.3 Event envelope

Todo webhook ou evento interno e normalizado para:

```json
{
  "schemaVersion": 1,
  "workspaceId": "uuid",
  "provider": "whatsapp|instagram|messenger|tiktok|email|site|internal",
  "connectionId": "uuid",
  "eventType": "message.received",
  "externalEventId": "provider-id",
  "occurredAt": "ISO-8601",
  "receivedAt": "ISO-8601",
  "actor": {"externalId": "scoped-id", "displayName": "nome"},
  "subject": {"externalThreadId": "thread-id"},
  "correlation": {"campaignId": null, "adId": null, "formId": null},
  "payload": {},
  "payloadSha256": "hex"
}
```

Chave de dedupe: `workspace/provider/connection/externalEventId`. Quando o
provider nao fornece ID, usar fingerprint versionado de campos imutaveis. Mesmo
ID com hash diferente vai para quarentena, nunca atualiza silenciosamente.

### 3.4 Transport router

O envio recebe intencao canonica e escolhe adapter apenas depois de:

1. policy de autonomia;
2. permissao do usuario/agente;
3. capability matrix da conexao;
4. opt-out/bloqueio;
5. janela, template e categoria;
6. quiet hours e limite de frequencia;
7. qualidade/saude/rate limit do numero;
8. budget/custo;
9. idempotency key;
10. kill switch e gate externo.

Nesta fase, o resultado termina em `local_simulated` ou `blocked_external`.

## 4. Modelo de dados detalhado

### 4.1 Identidade e canal

`OmnichannelIdentity`:

- `workspace_id`, `client_id`, `matter_id`, `display_name`, `canonical_phone`,
  `canonical_email`, `identity_state`, `merge_version`, `source`, `metadata`;
- indices tenant-safe e unicidade condicional para identificadores verificados.

`OmnichannelEndpoint`:

- `identity_id`, `channel`, `provider`, `connection_id`, `external_account_id`,
  `external_user_id`, `address`, `normalized_address`, `verified_at`,
  `consent_state`, `blocked_at`, `last_seen_at`, `capabilities`;
- unique por workspace/provider/connection/external user.

`OmnichannelBinding`:

- `thread_id`, `endpoint_id`, `provider`, `connection_id`,
  `external_thread_id`, `binding_state`, `last_cursor`, `last_synced_at`;
- unique por workspace/provider/connection/external thread.

### 4.2 Thread e routing

Extensoes de `OmnichannelThread`:

- `identity_id`, `assigned_team_id`, `queue`, `priority`, `favorite`,
  `preferred_channel`, `version`, `last_inbound_at`, `last_outbound_at`,
  `first_response_due_at`, `resolution_due_at`, `closed_at`, `closed_reason`;
- `policy_state` e `cost_state` estruturados, sem usar metadata para ownership.

`WorkspaceTeam` e `WorkspaceTeamMember`:

- nome, descricao, tipo, cor, capacidade, round-robin, horario, status;
- membro, papel, peso, prioridade, disponibilidade e limite simultaneo;
- equipe e membro pertencem ao mesmo workspace por FK composta.

`RoutingEvent`:

- from/to user, from/to team, action, reason, actor, source, occurred_at,
  expected_thread_version e correlation_id;
- append-only; atualizacao da thread e evento na mesma transacao.

### 4.3 Mensagem, evento e audiencia

Extensoes de `OmnichannelMessage`:

- `endpoint_id`, `binding_id`, `reply_to_message_id`, `audience`,
  `content_type`, `language`, `provider_status`, `transport`, `cost_amount`,
  `cost_currency`, `window_expires_at`, `template_id`, `scheduled_message_id`;
- `audience`: `client_private`, `public_social`, `internal_team`,
  `internal_agents`, `system_audit`;
- original e traducao ficam separados; nota interna nunca muda de audiencia.

`ChannelEvent`:

- tipos: comentario, resposta, like, hide, mention, story, form, ad entry,
  delivery, read, opt-out, block, call, workflow, CRM e system;
- `parent_event_id`, `public_url`, `media_id`, `ad_id`, `visibility`,
  `actionability`, `deadline_at`, `raw_payload_ref` e `event_metadata`.

### 4.4 Captura e atribuicao

`LeadCaptureReceipt`:

- provider, connection, external lead/form/event IDs, payload hash, state,
  identity/thread/matter IDs, mapping version, received/completed timestamps,
  reconciliation state e error code.

Campos de atribuicao normalizados:

- source, medium, campaign, content, term;
- account, campaign, ad set/group, ad, creative, form;
- gclid, wbraid, gbraid, fbclid, fbc, fbp, ttclid;
- first touch, last touch e touches intermediarios imutaveis;
- eventos de funil com valor e moeda em formato decimal.

### 4.5 Scheduler e chamada

`OmnichannelScheduledMessage`:

- thread, endpoint, connection, channel, body/template/attachments,
  scheduled_at, timezone, state, lease owner/expiry, attempts, next attempt,
  dedupe key, approval, cancellation e provider receipt.

`OmnichannelCallSession`:

- direction, connection, endpoint, requested/approved/started/answered/ended,
  state, failure reason, provider call ID, permission state, transcript object,
  recording object, summary, duration, cost e handoff.

## 5. Contratos de API locais

### 5.1 Inbox e identidade

- `GET /api/v1/klm/inbox/views`
- `GET /api/v1/klm/inbox/threads`
- `GET /api/v1/klm/inbox/threads/{id}`
- `GET /api/v1/klm/inbox/threads/{id}/timeline`
- `POST /api/v1/klm/inbox/threads/{id}/messages`
- `POST /api/v1/klm/inbox/threads/{id}/notes`
- `POST /api/v1/klm/inbox/threads/{id}/transfer`
- `POST /api/v1/klm/inbox/threads/{id}/takeover`
- `POST /api/v1/klm/inbox/threads/{id}/complete`
- `POST /api/v1/klm/inbox/threads/{id}/reopen`
- `POST /api/v1/klm/inbox/identities/{id}/merge-preview`
- `POST /api/v1/klm/inbox/identities/{id}/merge`

Listagens usam cursor, filtros tipados e ETag. Mutacoes de routing e merge exigem
`expectedVersion`; conflito retorna `409` com estado atual.

### 5.2 Equipes e filas

- CRUD de `/api/v1/klm/teams` e membros;
- `/api/v1/klm/queues/summary`, `/capacity` e `/rebalance-preview`;
- historico de routing paginado e exportavel;
- nenhuma equipe e sintetizada a partir de papel ou metadata.

### 5.3 Social e comentarios

- webhooks locais versionados para Instagram, Messenger e TikTok;
- `GET /social/events`, `POST /reply-public`, `POST /reply-private`,
  `POST /like`, `POST /hide`, `POST /unhide`;
- cada acao retorna `capability`, `deadline`, `intent` e `localResult`;
- operacao proibida nao cria falso sucesso.

### 5.4 Ads, forms e site

- `POST /webhooks/meta/leadgen`
- `POST /webhooks/tiktok/leadgen`
- `POST /public/v1/lead-capture/{sourceKey}`
- `POST /public/v1/chat-entry/{sourceKey}`
- `GET /integrations/lead-sources`
- CRUD de mapping e preview/test fixture;
- `POST /attribution/events` grava conversao local; export futuro e intent.

O contrato do site/Lovable inclui `schemaVersion`, `sourceKey`, nonce, timestamp,
UTMs, click IDs, consent/opt-out declarado, campos e checksum. O endpoint aplica
rate limit, honeypot, HMAC quando configurado, replay protection e allowlist de
origem; dados obrigatorios sao definidos por formulario versionado.

### 5.5 Agenda, scheduler e chamada

- availability/booking existente permanece e ganha extracao da conversa;
- CRUD de `/inbox/threads/{id}/scheduled-messages`;
- `POST /calls/permission-intent`, `/calls`, `/{id}/answer`, `/{id}/end`;
- adapters externos permanecem bloqueados.

### 5.6 Copilot

- `POST /copilot/suggest-reply`
- `POST /copilot/rewrite`
- `POST /copilot/translate`
- `POST /copilot/summarize`
- `POST /copilot/unanswered-questions`
- `POST /copilot/extract-actions`
- `POST /copilot/analytics-query`
- `POST /copilot/feedback`

Respostas incluem fontes, playbook/version, confidence, policy decision,
latency/tokens estimados e `actionIntents`. Analytics aceita apenas metricas e
dimensoes registradas na semantic layer; nao gera SQL livre contra o tenant.

## 6. Automacao estilo ManyChat, sem fragilidade no-code

O editor visual continua drag-and-drop, mas publica um DSL tipado, versionado e
validado. Nodes novos:

### Triggers

- comentario criado, palavra-chave, mention/story, DM, mensagem, formulario,
  anuncio click-to-message, lead ad, stage changed, SLA, schedule, no-show,
  proposta, assinatura, pagamento, opt-out e block.

### Condicoes

- canal, conta/numero, post/anuncio/formulario, palavra-chave/regex segura,
  follower status quando documentado, tags, score, area, subtema, documento,
  etapa, horario, janela, consentimento, frequencia e playbook.

### Acoes

- responder publico, responder privado, like/moderar, enviar mensagem/template,
  aguardar, digitar, fragmentar, criar/atualizar card, tag, qualificar,
  transferir, agendar, criar tarefa, solicitar documento, iniciar Copilot,
  chamar agente, criar approval, cobrar e emitir intent de conversao.

### Garantias

- schema e tipos validados antes de publicar;
- revisao imutavel, simulacao, dry-run, diff e rollback;
- no maximo uma execucao por receipt/flow revision;
- limites de iteracao, timeout, concorrencia e budget;
- pausa global, por workspace, flow, canal e thread;
- human takeover suspende apenas acoes conflitantes configuradas;
- IA e flow podem coexistir com arbitration policy explicita;
- dead-letter e replay manual auditado.

## 7. Experiencia nativa da SPA

### 7.1 Layout desktop

1. **Coluna de filas:** views, busca, filtro, canal, numero, time, SLA, favorito,
   unread, origem, preview, owner e prioridade.
2. **Timeline:** header com identidade, tags, canal atual, status, owner, transferir,
   concluir, chamar e menu; mensagens/eventos com tipo e audiencia; composer.
3. **Inspector:** Caso 360 e Operacao/Copilot em tabs, redimensionavel e
   recolhivel, sem perder contexto.

### 7.2 Composer

O composer oferece:

- texto, emoji, imagem, video, audio, documento e gravacao;
- template/modelo, resposta rapida, mensagem agendada, pagamento, proposta,
  agenda, contrato/procuracao e link seguro;
- nota interna e conversa de agentes como modos visivelmente distintos;
- seletor de canal/numero quando mais de um transporte for elegivel;
- status de janela/template/custo antes de enviar;
- botao de IA para sugerir, traduzir, corrigir, mudar tom/tamanho e simplificar;
- undo do rascunho, atalhos e confirmacao para acao de maior impacto.

### 7.3 Timeline

- cores aprovadas permanecem: cliente neutro/claro, bot cinza, advogado verde e
  nota interna amarela;
- badge mostra canal, agente/usuario autor, transporte e delivery;
- comentario publico nao se parece com DM;
- eventos de card, flow, agenda, chamada, proposta e pagamento aparecem como
  marcos compactos expansíveis;
- midia possui preview, OCR/transcricao, RAG e links para SPA;
- original e traducao podem ser alternados.

### 7.4 Mobile

- navegacao em tres telas empilhadas: fila, conversa, Caso/Copilot;
- header fixo, composer acessivel, safe areas e teclado virtual;
- transferencia, nota, agenda, ligar e concluir em action sheet;
- nenhuma tabela larga; filtros em sheet e timeline virtualizada.

### 7.5 Configuracoes

- equipes/grupos, membros, capacidade e horarios;
- conexoes por canal, capability matrix, scopes, health e kill switch;
- numeros WhatsApp, modo operacional, templates, qualidade e custo;
- mappings de formulario/anuncio/site;
- automacoes de comentario e limites;
- idioma, Copilot, autonomia, playbook e budget;
- views salvas, SLA, quiet hours e retencao;
- pagina publica de booking e formulario first-party.

## 8. Copilot juridico-comercial

### 8.1 Funcoes one-click

- resumir conversa e dia;
- perguntas ainda sem resposta;
- sugerir resposta e inserir como rascunho;
- corrigir gramatica;
- tornar profissional, acolhedor, objetivo, curto, detalhado ou simples;
- traduzir e preservar original;
- extrair fatos, documentos, urgencia, objecao e proxima acao;
- criar tarefa, follow-up ou agendamento;
- consultar disponibilidade e propor slots;
- qualificar e sugerir etapa/tag;
- preparar briefing para reuniao;
- perguntar sobre KPIs, campanhas, fila e conversao.

### 8.2 Context compiler

Entradas permitidas:

- thread atual e eventos selecionados;
- Card/Caso 360 da propria thread;
- documentos/RAG autorizados do mesmo matter;
- playbook publicado e preferencias efetivas;
- semantic layer de metricas agregadas autorizadas.

Notas internas podem informar o Copilot interno, mas nunca sao citadas ou
reproduzidas ao cliente sem transformacao aprovada. Dados de outra thread/caso
nao entram por similaridade vetorial sem filtro tenant/matter anterior a busca.

### 8.3 Economia de tokens

- resumo incremental por cursor e hash;
- retrieval antes de contexto bruto;
- cache por workspace/thread/playbook/version/purpose;
- modelos locais/fakes para classificacao deterministica na fase local;
- roteamento por classe de tarefa, budget e qualidade;
- nao reenviar anexos ou timeline integral quando nada mudou;
- medir input/output, cache hit, latencia, aceitacao e edicao do rascunho.

## 9. Analytics e KPIs

### Aquisicao

- spend, impressoes, alcance, cliques, CTR, CPC, CPM;
- leads, CPL, leads qualificados, CPQL, CAC, contratos, receita, ROAS;
- por canal, campanha, conjunto, anuncio, criativo, formulario, palavra-chave,
  landing page, dispositivo, regiao e coorte.

### Social e mensagens

- comentarios, respostas, likes, DMs iniciadas, comment-to-DM, DM-to-card,
  comment-to-contract, opt-out, block, erro e custo;
- janela elegivel, template usado, entrega, leitura e resposta.

### Inbox

- backlog inicial/final, novos, concluidos, reabertos;
- first response, wait, handle, resolution, SLA e aging;
- ocupacao/capacidade por time/usuario/agente/numero/canal;
- transferencias, handoffs, takeover, abandono e sem resposta.

### Comercial e juridico

- qualificacao, agenda, show/no-show, proposta, assinatura, fechamento;
- ciclo por etapa, motivos de perda, reativacao, honorarios projetados/realizados;
- area, subtema, medicamento/tratamento e suficiencia documental.

### IA e automacao

- sugestao aceita/editada/rejeitada, perguntas perdidas, erro factual e handoff;
- flow starts/completions/failures/paused, tempo por node e dead-letter;
- tokens, custo estimado, cache hit, latencia e custo por lead/cliente/contrato.

Cada KPI possui ID, nome, descricao, formula, unidade, owner, fonte, freshness,
janela, dimensoes permitidas e teste de reconciliacao. Valores monetarios usam
decimal e formato `R$ 1.000.000,00`, nunca abreviacao `k`.

## 10. Seguranca, tenancy e operacao

1. FKs compostas ou validacao equivalente garantem workspace em todos os vinculos.
2. RLS Postgres cobre novas tabelas; SQLite local recebe testes de service layer.
3. Webhooks verificam assinatura, timestamp, replay e payload maximo antes de
   materializar qualquer dado.
4. Payload bruto sensivel fica referenciado em storage com checksum e retencao,
   nao replicado em logs.
5. Tokens/scopes/capabilities sao criptografados e nunca retornam a SPA.
6. Idempotencia e transacao abrangem receipt, identidade, thread, mensagem, card
   e follow-up.
7. Concorrencia usa optimistic lock na thread e lease nos workers.
8. Rate limit existe por IP, workspace, conexao, endpoint e acao externa futura.
9. Opt-out, block e quiet hours sao hard gates anteriores a IA e flow.
10. Auditoria registra ator, origem, policy, antes/depois, correlation e motivo.
11. Permissoes diferenciam ver dados do lead, responder, nota, transferir,
    moderar social, disparar, chamar, configurar canal, exportar e mergear.
12. Admin pode ocultar PII dos leads por papel/usuario sem quebrar a operacao.

## 11. Performance e resiliencia

### SLOs locais

- p95 inbox list: `< 250 ms` com 10 mil threads;
- p95 thread summary: `< 250 ms`;
- p95 timeline primeira pagina: `< 300 ms` com 100 mil eventos na thread;
- p95 mutation local: `< 350 ms` sem chamada de modelo;
- WebSocket/SSE fan-out local: `< 500 ms` ate a SPA;
- webhook ack simulado: `< 1 s`, processamento pesado assíncrono;
- zero duplicata em 100 replays concorrentes do mesmo evento.

### Tecnicas

- cursor pagination, indices compostos e covering indices;
- timeline virtualizada e queries TanStack deduplicadas;
- aggregate snapshot para lista e detalhes sob demanda;
- outbox transacional e worker idempotente;
- caches curtos com chave tenant-safe e invalidacao por evento;
- midia/OCR/transcricao fora da transacao de webhook;
- backpressure, circuit breaker e dead-letter;
- logs de acesso desligados em testes de carga para evitar PTY backpressure.

## 12. Plano de execucao

### Fase 0 - Baseline e contrato

**Entregas:** benchmark, OCR, plano V6, snapshot de schema/rotas/UI/testes e
manifesto de preservacao.  
**Gate:** fontes e claims classificados; nenhuma funcao atual removida.

### Fase 1 - Nucleo canonico

**Entregas:** modelos, migration, schemas, identity resolver, event normalizer,
thread binding, outbox e testes de idempotencia/tenant.  
**Gate:** WhatsApp/Meta/e-mail/site fixtures convergem para uma timeline.

### Fase 2 - Equipes, filas e operacao

**Entregas:** teams/members, views, routing events, takeover, complete/reopen,
scheduler canonico e call sessions.  
**Gate:** dois usuarios e duas equipes operam concorrentes sem perder ownership.

### Fase 3 - Social, comentarios e ads

**Entregas:** TikTok, Messenger e Instagram normalizados; comment actions;
leadgen receipts; attribution e site/Lovable capture.  
**Gate:** fixtures oficiais e replays criam um card/thread e um flow run.

### Fase 4 - Flow runtime

**Entregas:** triggers/nodes novos, DSL, simulator, realtime trace, pause/resume,
arbitration IA-flow, dead-letter e replay.  
**Gate:** cenarios comment-to-DM, lead-ad-to-booking e no-show recovery verdes.

### Fase 5 - Copilot

**Entregas:** comandos one-click, context compiler, unanswered questions,
translation, actions, schedule e analytics query; fake/local adapter.  
**Gate:** suite dourada, isolamento, provenance e feedback completos.

### Fase 6 - SPA premium

**Entregas:** nova inbox responsiva, composer, timeline, inspector, teams,
integrations, social workspace, flows e dashboards.  
**Gate:** zero botao decorativo; desktop/tablet/mobile e dark mode aprovados.

### Fase 7 - Backfill e compatibilidade

**Entregas:** bridge de WhatsApp, Meta e e-mail legados, mapping persistente,
duas sincronizacoes seguidas, reconciliacao e relatorio de divergencias.  
**Gate:** segunda execucao nao duplica; rotas anteriores continuam funcionais.

### Fase 8 - Hardening e QA

**Entregas:** seguranca, carga, concorrencia, falha, accessibility, visual,
production bundle, matriz de botoes e independent review.  
**Gate:** zero P0/P1, suites verdes e evidencia por SHA.

## 13. Matriz de QA obrigatoria

### Unitario

- normalizacao de telefone/e-mail/provider IDs;
- merge e ambiguity rules;
- idempotency keys e payload hash;
- policies de canal, janela, template, opt-out e custo;
- reducers da timeline, routing state e flow DSL;
- metric definitions e formatos monetarios.

### Integracao

- receipt -> identity -> endpoint -> thread -> binding -> event/message -> card;
- transferencia concorrente com optimistic lock;
- scheduler lease/retry/cancel/reconcile;
- webhook replay, hash conflict e dead-letter;
- RAG indexado somente no matter correto;
- outbox e cache invalidation.

### E2E

- navegar todas as views e filtros;
- responder como humano, bot, nota e comentario publico;
- anexar cada midia, usar template, agendar, cancelar e reenviar;
- transferir por usuario/equipe, takeover, concluir e reabrir;
- iniciar/pausar/desativar flows em conjunto com IA;
- comment-to-DM, lead form e site form;
- Copilot: resumo, rewrite, translate, question gap, task, schedule, analytics;
- Caso 360 e todos os deep links;
- permissions/PII hidden;
- desktop, tablet, mobile e dark mode.

### Nao funcionais

- 10 mil threads, 100 mil eventos numa thread e 100 webhooks concorrentes;
- provider timeout, duplicate, out-of-order, token expired e rate limit;
- browser offline/reconnect e worker restart;
- dependency scanning, secrets scan, SAST e tenant attack suite;
- axe/accessibility, keyboard, focus, contrast e reduced motion;
- screenshots e checks de sobreposicao/text overflow.

## 14. APIs e acessos futuros

Somente depois da aprovacao local serao solicitados:

- Meta Business, app, WABA, numeros, tokens, verify token, templates, Instagram,
  Pages, Messenger, Lead Ads, ad account, dataset/pixel e CAPI;
- TikTok Business/Ads, developer app, Business Messaging, comments, advertiser,
  Instant Forms, pixel/events e webhooks;
- dominio/site Lovable ou repositorio do site, contrato do formulario e origem;
- Google/Microsoft Calendar, ZapSign, Asaas/Stripe, e-mail e ElevenLabs;
- provider(s) de IA com projeto, budget e chaves segregadas.

O onboarding futuro executa OAuth/Embedded Signup quando disponivel, capability
probe, fixture de teste, webhook challenge, health check e dry-run. Nenhuma chave
sera enviada em chat, commitada ou mostrada no frontend.

## 15. Fora do escopo desta autorizacao

- chamadas reais a Meta, TikTok, WhatsApp, Lovable ou qualquer provider;
- publicacao de app, App Review, verificacao empresarial ou numero;
- envio real, disparo, comentario, like, DM, ligacao ou conversao;
- AWS, DNS, certificado, RDS, Redis, S3 ou observabilidade externa;
- afirmacao de producao, disponibilidade, custo real ou compliance certificado.

## 16. Pacote final para aprovacao

1. SPA local aberta no painel lateral;
2. seed deterministico com WhatsApp, IG, Messenger, TikTok, e-mail, comentario,
   formulario, anuncio, chamada, equipes e flows;
3. relatorio de migration/backfill e reconciliacao;
4. manifesto de capacidades com estado e evidencia;
5. QA por suite, acao, viewport e SHA;
6. benchmark final com gaps residuais honestos;
7. checklist de APIs/acessos da fase seguinte;
8. revisao independente de arquitetura, seguranca, UX e testes.

Somente esse pacote fecha `LOCAL_OMNICHANNEL_APPROVAL_READY_V6` e devolve ao
proprietario a decisao de iniciar a homologacao das APIs.
