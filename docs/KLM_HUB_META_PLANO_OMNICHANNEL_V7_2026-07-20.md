# KLM Hub - Meta e Plano Omnichannel V7

**Data:** 20 de julho de 2026  
**Status:** `EM_EXECUCAO_LOCAL`  
**Gate externo:** fechado  
**AWS:** proibida nesta fase  
**Regra de preservacao:** acrescentar e corrigir; nenhuma funcao aprovada pode
ser removida ou remodelada sem ordem expressa do proprietario.

Este V7 substitui o V6 como plano de execucao omnichannel. O V5 continua valido
para SaaS, billing, painel de plataforma, pacotes, playbooks, agentes, OSS e
operacao juridica. O benchmark correspondente esta em
[`BENCHMARK_OMNICHANNEL_KOMMO_HELENA_V7_2026-07-20.md`](./BENCHMARK_OMNICHANNEL_KOMMO_HELENA_V7_2026-07-20.md).

## 1. Meta ajustada

Entregar `LOCAL_OMNICHANNEL_APPROVAL_READY_V7`: uma versao local do KLM Hub em
que WhatsApp Cloud API e Coexistence, Instagram, Messenger, TikTok, e-mail,
site/Lovable, formularios de anuncios, comentarios e chamadas convergem para uma
experiencia nativa, operavel e testada, com IA e automacoes, sem efeitos
externos.

### 1.1 Definicao precisa de conversa unica

- `Pessoa`: identidade canonica que agrega endpoints verificados.
- `Caso`: fronteira juridica de documentos, RAG, proposta, financeiro e decisoes.
- `Thread de caso`: unidade segura de atendimento e operacao.
- `Feed da pessoa`: visao continua que apresenta todas as threads autorizadas,
  com divisores de caso e troca explicita de contexto.

Assim, o usuario percebe uma jornada unica do lead, mas o sistema nunca mistura
duas demandas juridicas, mesmo quando telefone ou perfil social sao iguais.

### 1.2 North Star

**Percentual de contatos elegiveis que recebem o proximo passo correto dentro do
SLA, no caso e canal certos, sem duplicacao, vazamento, violacao de janela,
entrega ficticia ou efeito externo nao autorizado.**

### 1.3 Resultados-chave

1. Zero P0/P1 aberto na revisao final.
2. 100% dos eventos simulados possuem receipt, idempotency key e estado final
   reconciliavel.
3. 100% das mensagens externas seguem `draft -> queued -> accepted -> sent ->
   delivered/read` ou estado de falha real; nunca nascem como `sent`.
4. A mesma pessoa com dois casos mantem RAG, arquivos, proposta, mensagens e
   permissoes isolados.
5. Replays concorrentes de webhook e lead form materializam um unico resultado.
6. WhatsApp oferece rotas Cloud API e Coexistence na mesma inbox, multiplos
   numeros e eligibility preview por janela, template, custo e saude.
7. Instagram, Messenger e TikTok oferecem DMs, comentarios, resposta publica,
   resposta privada, Stories/mentions quando aplicavel, follower condition,
   icebreakers e moderacao limitada pela capability matrix.
8. Meta Lead Ads, TikTok Instant Forms e site/Lovable criam identidade, card,
   thread, atribuicao, SLA e proximo passo sem duplicar.
9. Flows acionam comentario, DM, formulario, mensagem, agenda, no-show e
   follow-up, com trace em tempo real, pausa, retomada e compensacao.
10. O Copilot executa actions confirmaveis para resposta, traducao, tarefa,
    agenda, qualificacao, etiqueta, etapa, resumo, perguntas pendentes e metricas.
11. Desktop e mobile permitem responder, anexar, usar interativos, agendar,
    transferir, anotar, chamar, concluir e ativar flow em ate dois gestos.
12. Dashboard liga investimento, campanha, lead, atendimento, proposta,
    contrato, receita, CAC, ROAS, conversao e qualidade.
13. p95 local permanece abaixo de 300 ms nos endpoints quentes documentados.
14. Zero botao decorativo e zero nome de fornecedor OSS no frontend final.
15. Suites unitarias, integracao, E2E, tenant attack, carga, visual,
    acessibilidade e release proofs ficam verdes no mesmo SHA.

## 2. Definicao de pronto por capacidade

Uma feature so recebe estado `native_verified` quando possui:

1. modelo persistente e migration;
2. contrato de dominio tipado;
3. API local autenticada ou webhook publico assinado;
4. permissao, tenant scope e mascaramento;
5. idempotencia, erro, retry, receipt e reconciliacao;
6. UI nativa responsiva com loading, vazio, erro e disabled reason;
7. auditoria, metrica e custo quando aplicavel;
8. testes unitario, integracao e E2E;
9. fixture deterministica;
10. evidencia de release vinculada ao SHA.

Adapter, card de status, mock sem persistencia, launcher isolado, sidecar ou
texto de UI nao contam como entrega.

## 3. Arquitetura final

### 3.1 Identidade e casos

- `OmnichannelIdentity` nao possui ownership exclusivo de matter.
- `OmnichannelEndpoint` representa telefone, e-mail, IG SID, PSID, TikTok ID ou
  identificador do site, sempre escopado por workspace/provider/conta.
- `OmnichannelThread` pertence a identidade e a no maximo um matter ativo.
- uma identidade pode ter varias threads de caso e uma thread provisoria sem
  matter;
- `OmnichannelBinding` liga uma conversa externa a uma thread especifica;
- conflitos viram `IdentityReviewCase`; nunca reatribuicao silenciosa;
- merge e split exigem preview, permissao, optimistic lock, audit event e
  compensacao.

### 3.2 Mensageria e entrega

Estados canonicos:

`draft`, `scheduled`, `queued`, `dispatching`, `provider_accepted`, `sent`,
`delivered`, `read`, `failed_retryable`, `failed_terminal`, `cancelled`,
`blocked_policy`, `blocked_external`, `local_simulated`.

Regras:

- texto criado na SPA nao e prova de envio;
- receipt atualiza mensagem canonica e legado na mesma transacao/outbox;
- evento fora de ordem nao retrocede estado;
- falha preserva codigo, categoria, proxima tentativa e rota;
- reconciliador consulta/adapta apenas na futura fase externa;
- custo previsto e custo confirmado ficam separados.

### 3.3 Ingresso publico

Fronteiras distintas:

- WhatsApp/Meta webhook;
- Instagram/Messenger webhook;
- TikTok Messaging/Comments webhook;
- Meta Lead Ads webhook;
- TikTok Lead Generation webhook;
- site/Lovable form webhook;
- e-mail inbound.

Cada fronteira exige assinatura/challenge, connection resolver tenant-safe,
timestamp/replay window, body bruto, payload hash, receipt, ack rapido, outbox,
quarentena e logs sem PII.

### 3.4 Scheduler canonico

- uma tabela e uma maquina de estados;
- lease com owner e expiracao;
- `SKIP LOCKED` ou equivalente no Postgres;
- cancelamento concorrente seguro;
- policy reavaliada no momento do envio;
- quiet hours, timezone, janela e template;
- retry com backoff e limite;
- dead-letter e replay manual auditado;
- atualizacao realtime na conversa.

### 3.5 Automacao compilada

O editor visual gera DSL versionada e validada. O runtime executa nos
primitivos tipados do KLM, sem interpretar texto livre.

Triggers: mensagem, DM, comentario, keyword, Story, follower, lead form, site
form, agenda, no-show, etapa, tarefa, documento, pagamento, SLA e timer.

Condicoes: canal, conta, numero, post, anuncio, campanha, palavra, seguidor,
consentimento, horario, score, subtema, documentos, etapa, owner, equipe,
pergunta pendente e autonomia.

Acoes: resposta publica, DM, mensagem/template/interativo, tag, qualificacao,
etapa, owner/equipe, tarefa, agenda, proposta, documento, cobranca local,
Copilot, agente, wait, branch, webhook futuro e handoff.

Garantias: revision pinning, compile-time validation, idempotencia por node,
trace realtime, pause/resume, retry, compensation, dead-letter, budget e kill
switch.

## 4. Plano de execucao detalhado

### Fase 0 - Contrato, benchmark e baseline

Entregas:

- benchmark V7 e OCR funcional;
- manifesto de preservacao das funcoes existentes;
- inventario rota/modelo/API/componente/teste;
- revisoes independentes de UX e threat model;
- baseline de schema, performance, suites e screenshots.

Gate: nenhuma capacidade presumida e todos os P1/P2 registrados.

### Fase 1 - Isolamento pessoa/caso

Entregas:

- remover acoplamento exclusivo identity/matter;
- permitir varias threads por identidade, uma por contexto juridico;
- resolver binding antes de inferir thread;
- impedir captura de lead de sobrescrever atribuicao anterior;
- fila de conflito, merge e split auditados;
- migration e backfill sem perda.

Testes:

- mesma pessoa, dois matters, mesmos nomes de arquivos e termos;
- novo lead form com telefone ja existente;
- merge ambiguo negado;
- split e compensacao;
- duas workspaces com endpoints iguais.

Gate: zero contaminacao de mensagem, documento, RAG ou atribuicao.

### Fase 2 - Entrega, receipts e eligibility

Entregas:

- estados canonicos completos;
- delivery receipt convergente;
- preview de rota no backend e composer;
- janela, template, idioma, categoria, custo, numero e saude;
- numero/rota alternativos com motivo;
- ledger append-only de custo previsto e confirmado.

Gate: nenhuma mensagem aparece como enviada sem receipt compativel.

### Fase 3 - Webhooks e captacao first-party

Entregas:

- ingressos assinados Meta, TikTok e Lovable/site;
- challenge e resolver de conexao;
- receipt/dedupe/hash conflict/quarantine;
- field mapping versionado;
- identidade, card, thread, SLA, equipe e attribution;
- endpoint de teste local com assinatura valida e invalida.

Gate: 100 replays concorrentes geram uma materializacao e uma auditoria.

### Fase 4 - Scheduler e chamadas

Entregas:

- worker V7 consumindo `OmnichannelScheduledMessage`;
- cancelar, pausar, reagendar, retry e dead-letter;
- chamada com capability, consentimento, provider receipt e estados validos;
- transcript/gravacao somente quando configurados;
- custo e deep link no Caso 360.

Gate: restart do worker nao duplica envio; chamada impossivel nao vira ativa.

### Fase 5 - WhatsApp completo

Entregas:

- Cloud API e Coexistence no mesmo seletor;
- multiplos numeros e policy por funil/equipe;
- templates, Flow payload, lista, botoes e carrossel;
- anexo de imagem, video, audio e documento;
- campanha segura, opt-out e qualidade;
- digitando/cadencia configuravel e anti-fragmentacao;
- historico e transporte exibidos na timeline.

Gate: cada tipo possui preview, persistencia, policy e teste E2E.

### Fase 6 - Social e ManyChat juridico

Entregas:

- Instagram/Messenger/TikTok DM e comentario;
- resposta publica, privada, like, hide/unhide/delete quando permitido;
- keyword por post/anuncio, follower condition e icebreakers;
- Story mention/reply;
- comment-to-DM e comment-to-card;
- rate limits, janelas e receipts por acao;
- campanhas de recuperacao e remarketing com consentimento/policy.

Gate: provider/account/event/target ficam fortemente vinculados e auditados.

### Fase 7 - Site, Ads e atribuicao

Entregas:

- SDK/formulario first-party para Lovable;
- Meta e TikTok lead forms;
- UTM, gclid, fbclid, ttclid, campaign/adset/ad/creative/form;
- dedupe de browser/server event;
- timeline de touches e modelo de atribuicao declarado;
- feedback de qualificacao, proposta, contrato e receita em modo local;
- dashboards CAC, CPL, CPQL, ROAS, conversao e payback.

Gate: drill-down reconcilia anuncio -> lead -> conversa -> contrato -> receita.

### Fase 8 - Copilot acionavel

Entregas:

- resumo e meeting brief com fontes;
- resposta sugerida, rewrite, traducao e texto original;
- perguntas sem resposta e gate de conclusao;
- action intents para tarefa, agenda, tag, score, etapa e owner;
- disponibilidade e reagendamento;
- analytics conversacional em semantic layer;
- feedback aceito/editado/rejeitado;
- token budget, cache semantico e modelo por complexidade.

Gate: nenhuma mutacao ocorre sem permissao, preview/confirmacao quando exigida e
audit event.

### Fase 9 - Inbox one-click

Desktop:

- tres paineis redimensionaveis;
- views Novos, Meus, Equipe, Outros, Nao lidos e Favoritos;
- filtros por canal, numero, equipe, owner, status, SLA, tag, origem e caso;
- composer com anexo, template, interativo, nota e agendamento;
- rota com janela, template, custo, qualidade e disabled reason;
- timeline tipada e Caso 360 completo;
- painel operacional e Copilot acionavel.

Mobile:

- lista, conversa e detalhes como rotas focadas;
- action sheet para transferir, nota, agenda, chamada, flow e concluir;
- composer fixo sem sobreposicao;
- anexos e interativos em bottom sheet;
- contexto do caso em drawer com deep links.

Gate: acoes frequentes em ate dois gestos, teclado/foco/reader e zero overflow.

### Fase 10 - Equipes, permissoes e privacidade

Entregas:

- equipes, grupos, membros, filas, rounds e capacidade;
- RBAC + ABAC por tenant, caso, equipe e audiencia;
- `lead_data:view` reaplicado a contextos agregados;
- PII hidden consistente em lista, thread, export, log, realtime e Copilot;
- acesso de suporte break-glass com motivo e expiracao;
- tenant-safe composite FKs e RLS comportamental.

Gate: suite de ataque comprova que nenhuma rota, cache, websocket, RAG ou
arquivo cruza tenant/caso.

### Fase 11 - Analytics e SaaS observavel

Entregas:

- semantic layer com definicao e proveniencia;
- atendimento: backlog, SLA, FRT, TTR, transferencias e capacidade;
- social: comentarios, DM, resposta, conversao e opt-out;
- aquisicao: spend, impressions, clicks, forms, CPL, CPQL, CAC e ROAS;
- comercial: etapas, conversao, ciclo, no-show, ticket e honorarios;
- IA/flows: automacao, handoff, qualidade, tokens, custo e falhas;
- canal: volume, janela, template, custo, qualidade e entrega;
- comparativos, metas, cohort, funil e drill-down.

Gate: toda metrica possui formula, grain, timezone e filtros reproduziveis.

### Fase 12 - Migracao, compatibilidade e limpeza de marca

Entregas:

- bridge do legado para os agregados canonicos;
- duas sincronizacoes idempotentes;
- relatorio de divergencias e registros orfaos;
- preservacao de URLs/deep links;
- remocao de nomes de fornecedores do frontend;
- sidecars apenas como referencia/test fixture, nunca superficie operacional.

Gate: nenhuma funcao anterior some e nenhuma fonte externa aparece como motor
visivel do produto.

### Fase 13 - QA total e release proof

Executar:

- typecheck, lint e production build;
- unitarios backend/frontend;
- integracao API/banco/worker/realtime;
- migrations em banco limpo e upgrade de snapshot;
- E2E de cada botao, menu, tab, filtro, modal e deep link;
- desktop, tablet, mobile, dark e reduced motion;
- concorrencia, replay, out-of-order, timeout, rate limit e restart;
- carga de 10 mil threads e timeline longa;
- acessibilidade e navegacao por teclado;
- tenant attack, PII/log scan e dependency/secret scan;
- screenshots e verificacao de sobreposicao;
- revisao independente final.

Gate final: zero skipped, zero xfail escondido, zero P0/P1, suites verdes e
provas de arquitetura geradas no mesmo SHA.

## 5. Matriz de cenarios obrigatorios

1. WhatsApp Cloud recebe e Coexistence continua na mesma thread.
2. Mensagem fora da janela exige template e explica custo.
3. Pessoa envia Instagram DM depois de WhatsApp e aparece no feed correto.
4. Mesma pessoa abre segundo caso e nenhum dado do primeiro entra no segundo.
5. Comentario com keyword recebe resposta publica e DM, sem duplicar no replay.
6. Nao seguidor entra em branch diferente do seguidor.
7. Story mention cria evento e proximo passo elegivel.
8. TikTok Instant Form e Meta Lead Form criam card e SLA uma unica vez.
9. Formulario Lovable preserva UTM e click IDs.
10. Transferencia concorrente possui um vencedor e trilha completa.
11. Mensagem agendada e enviada pelo worker uma vez apos restart.
12. Cancelamento vence dispatch quando executado antes do lease final.
13. Copilot cria rascunho, tarefa e agenda somente apos confirmacao aplicavel.
14. Arquivo anexado fica no caso correto e respeita PII hidden.
15. Mobile permite nota, transferencia, agenda, chamada, flow e conclusao.
16. Evento de entrega fora de ordem nao regride `read` para `sent`.
17. Assinatura invalida e evento antigo sao rejeitados sem logar payload sensivel.
18. Usuario sem `lead_data:view` nao ve corpo, anexos, transcript ou custo
    sensivel no agregado.
19. Gate externo fechado impede qualquer rede e deixa estado honesto.
20. Dashboard reconcilia investimento, lead, contrato e receita.

## 6. Acessos da proxima fase

Somente depois da aprovacao local serao solicitados Meta Business/WABA/numeros,
Instagram/Pages/Messenger, ad accounts/pixel/dataset/CAPI, TikTok Business/Ads/
Messaging/Comments/Lead Generation, site Lovable, calendarios, e-mail, ZapSign,
Asaas/Stripe, ElevenLabs e providers de IA.

O onboarding futuro devera executar OAuth/Embedded Signup, capability probe,
webhook challenge, fixture de teste, health check, dry-run, verificacao de
permissoes e reconciliacao. Nenhuma chave sera enviada em chat, commitada ou
exposta no frontend.

## 7. Pacote de aprovacao

1. SPA aberta no painel lateral em desktop e mobile;
2. seed deterministico cobrindo todos os canais e casos;
3. migrations e backfill comprovados;
4. manifesto de capacidade com estado e evidencia;
5. relatorio QA por suite e viewport;
6. benchmark final e gaps residuais honestos;
7. checklist das APIs da fase de homologacao;
8. parecer independente de arquitetura, seguranca, UX e testes.

Somente esse pacote permite marcar a meta como concluida. Ate la, o status
permanece `EM_EXECUCAO_LOCAL`.
