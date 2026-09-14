# Plano mestre — Meu Executivo (2026-07-24)

Consolida, em ondas sequenciadas com portões (gates) explícitos, três frentes:
(0) limpar a fundação, (1) proteção real da aplicação, (2) reconstrução do Flow
Studio. Cada onda só começa quando a anterior fecha o gate. Nada de big-bang,
nada de árvore contaminada, nada na AWS nesta fase.

## Princípios inegociáveis (valem para TODAS as ondas)

1. **Fail-closed sempre.** Erro, incerteza ou dado faltando → estado seguro
   (bloqueado / `waiting_human`), nunca valor fabricado. Vale para IA, para
   autorização e para publicação de fluxo.
2. **O backend é a autoridade.** Frontend é sempre não-confiável. Toda leitura,
   escrita, exportação e download é re-autorizada no servidor, independente do
   que a tela apresenta.
3. **Tudo local antes de subir.** Sem AWS, sem APIs externas reais nesta fase.
   Gates externos (`KLM_MODEL_APIS_ENABLED`, `KLM_ONLINE_APPROVED`,
   `KLM_EXTERNAL_ACTIONS_ENABLED`) permanecem fechados e respeitados.
4. **O menu lateral esquerdo é intocável** (AppSidebar + zona de navegação do
   KlmShell). Ordem do titular.
5. **Nada da UI atual é removido.** Só melhora apresentação/estrutura; nenhuma
   funcionalidade ou elemento some sem paridade provada antes.
6. **Sem código de terceiros sem licença.** Repos sem licença = referência
   visual/comportamental apenas; permissivos (MIT/Apache 2.0) com atribuição no
   inventário técnico.
7. **Uma leva, uma árvore limpa.** Nenhuma onda grande começa sobre trabalho
   concorrente uncommitted. Contract tests que cruzam frontend/API/worker são
   **bloqueantes**.
8. **Word/entregáveis nunca com placeholder** e demais regras do titular
   permanecem.

---

## ONDA 0 — Fundação limpa (pré-requisito de tudo)

Baixo risco, isolada, destrava o resto. Sem ela, qualquer onda grande recria o
pesadelo de entrelaçamento.

### 0.1 Correção do schema duplicado (bug real)
- `app/db/local_schema.py` tem **duas** entradas `"whatsapp_connections"`
  (linhas ~103 e ~208); a segunda sobrescreve a primeira silenciosamente e
  colunas se perdem. Unificar numa única entrada com `transport_mode`,
  `app_secret_encrypted`, `webhook_verify_token_encrypted`, **na ordem que o
  backfill de `transport_mode` exige**.
- Provar qual coluna some hoje (o `test_whatsapp_post_webhook_accepts_valid_signature`
  é o sinal de sucesso — deve voltar ao verde).
- Teste de upgrade legado: tabela sem as colunas → `ensure_local_sqlite_schema`
  duas vezes → as 3 colunas criadas, default de `transport_mode` aplicado,
  idempotente, dados preservados.
- **Teste AST** que varre o literal `table_definitions` e falha se qualquer
  tabela aparecer mais de uma vez (guarda permanente).

### 0.2 Higiene de artefatos
- Remover o lixo `frontend/src/components/mensagens/MessageInput 2.tsx`
  (duplicata que quebra `test_critical_trees_have_no_duplicate_suffix_artifacts`).
- Regenerar localmente o scorecard (`.local/qa-evidence/oss-native/native_architecture_superiority.json`); evidência de QA não é fonte versionada.
  vinculado ao snapshot de fonte atual (fecha o `test_committed_scorecard_matches_the_bound_source`).

### 0.3 Fechar a onda concorrente do Codex
- A onda de copiloto/responsivo do Codex está uncommitted e vermelha (21 vitest
  + parte das 6 falhas backend). **Ela precisa ser commitada ou revertida pelo
  Codex antes** de qualquer onda grande minha. Coordenar, não sobrescrever.

### 0.4 Decisão de posse do copiloto (bloqueante para a Onda 2)
- Existem hoje, em construção, `FlowCopilot` + `flow_copilot_service` e um
  `OperationalCopilot` inteiro. **Decidir um dono único**: o copiloto do Flow
  Studio é a evolução do do Codex, ou é descartado. Dois copilotos meio-feitos
  na mesma árvore = retrabalho garantido.

**Gate 0:** suíte backend completa verde (hoje 3065/6 → 3071/0), vitest verde
(após Codex fechar), árvore commitada e limpa, dono do copiloto definido.

---

## ONDA 1 — Proteção real da aplicação

Filosofia correta: **não prometer bloqueio de DevTools/F12/botão-direito**
(contornável e só atrapalha usuário legítimo). Proteger nas fronteiras reais.
Primitivas já existem no código: `auth_epoch` (revogação de sessão) e o padrão
`_BACKEND: memory→redis` (reuso para rate-limit). Diferença crítica frente ao
plano original: **enforcement nasce em shadow mode.**

### 1.1 Frontend (endurecer o que já existe, sem cosmético)
- `sourcemap: false` explícito no build de produção.
- O verificador de bundle **falha** se achar `.map`, segredo, credencial,
  fixture, URL interna ou dado pessoal.
- Preservar CSP, anti-iframe, `nosniff`, permissions-policy e cache privado.
- **Zero** bloqueio cosmético de botão-direito/F12/seleção. Nada removido da UI.

### 1.2 Backend e sessão
- Re-autorizar no servidor toda leitura/escrita/exportação/download.
- Rate-limit por rota combinando workspace + usuário + sessão + IP + dispositivo,
  com store `memory` local / `redis` prod (reusar o padrão do gateway de IA).
- Detecção de abuso: enumeração sequencial, scraping, volume anormal, repetição
  de falha, exportação em massa, acesso cross-tenant.
- **Shadow mode primeiro:** detecta + registra incidente + alerta o
  superadmin, **sem bloquear**. Observar tráfego real, calibrar limiar, e só
  então ligar enforcement.
- Ao confirmar abuso (definição conservadora): incidente imutável, revoga
  sessão + refresh (bump `auth_epoch`), bloqueio de origem 24h, notifica
  superadmin.
- IP compartilhado nunca vira bloqueio permanente automático; permanência ou
  desbloqueio antecipado exige decisão administrativa. **No início, TODO
  bloqueio passa por confirmação humana** (não só IP compartilhado).
- Confiar em `X-Forwarded-For` só do proxy oficial (evita spoof do IP).

### 1.3 Arquivos e documentos
- Download legítimo por autorização server-side + ticket curto vinculado a
  usuário/workspace/objeto/finalidade; registrar criação/uso/expiração/falha.
- Sem listagem direta de bucket, sem acesso público à origem.
- Exportação completa exige permissão específica + auditoria.

### 1.4 Painel de incidentes (superadmin)
- IP/faixa mascarada, usuário/workspace/sessão, evidências, motivo, data,
  duração, estado; ações prorrogar/permanente/desbloquear, restritas a
  superadmin + TI com permissão explícita, tudo auditado.
- Nenhum endpoint de segurança acessível só por ocultação no frontend.

### 1.5 AWS (DIFERIDO — não nesta fase)
- WAF no CloudFront + ALB, regras gerenciadas + rate-limit + challenge, ALB só
  nas rotas esperadas, S3 privado com Origin Access Control, sync de bloqueios
  de alta confiança com o WAF. **Planejado, executado só no go-live.**

**Gate 1:** IP falsificado não aciona bloqueio; usuário bloqueado perde sessão
e API; colega no mesmo workspace/IP não é bloqueado indevidamente; superadmin
consulta e desbloqueia; ticket de documento expira e não vale em outro
workspace; build de produção sem source map nem dado proibido; CSP/auth/
isolamento de tenant/fluxos normais intactos; enforcement começou em shadow.

---

## ONDA 2 — Flow Studio (reconstrução nativa)

Arquitetura correta: **registro backend v2 como fonte única de verdade**,
matando os catálogos hardcoded divergentes (existem dois editores frontend hoje:
`klm/pages/FlowsPage.tsx` e `pages/FlowBuilder.tsx`). Diferença crítica frente ao
plano original: **nada de "troca atômica"** — flag + rota paralela + migração
incremental nó a nó.

### 2.1 Base e licenças
- Núcleo `@xyflow/react` (MIT) já usado; React Flow Pro **não** necessário.
- Referências permissivas (só arquitetura/comportamento, sem copiar código):
  Workflow Builder (Apache 2.0), Node-RED (Apache 2.0). Registrar no inventário.
- **Não** usar código do repo sem licença do Azim.

### 2.2 Registro de nós v2 (o núcleo de valor e de risco)
- Evoluir o registro dos 29 tipos para v2: config schema + presentation schema,
  defaults + versão, portas tipadas + cardinalidade, categoria/ícone/descrição/
  exemplos, permissões/capacidades, tipo de efeito externo + suporte a dry-run +
  rota de erro, id do executor + migrador de config.
- **Contract tests bloqueantes**: frontend, API e worker entendem o MESMO
  registro; o v2 de cada nó bate com o que o worker realmente faz (senão =
  quebra silenciosa).

### 2.3 Experiência (Flow Studio único)
- Menu lateral esquerdo intacto. Área operacional em altura total: biblioteca
  recolhível à esquerda, canvas central prioritário, inspetor contextual à
  direita, gaveta inferior (validação/execução/logs/testes), copiloto em popup
  flutuante. Painéis redimensionáveis/recolhíveis com preferência persistida.
- Nós com hierarquia tipográfica clara, ícones consistentes, portas legíveis,
  cor reservada a função/status (adeus aparência infantil — a tipografia já foi
  corrigida na auditoria de UI; aqui é o acabamento completo).
- Canvas: arrastar/clicar-para-adicionar/conectar/reconectar, seleção múltipla,
  laço, alinhamento/distribuição/agrupamento, copiar/colar/duplicar/excluir,
  undo/redo, menus de contexto, duplo-clique, atalhos, grade/encaixe/minimapa/
  zoom/ajuste, auto-layout preservando ramificações, conexões rotuladas
  (verdadeiro/falso/rota/sucesso/falha/fallback), inserção rápida por porta,
  busca por nome/categoria/capacidade/integração + favoritos/recentes.

### 2.4 Configuração, validação e revisões
- Inspetor **gerado pelo schema** publicado pelo backend (elimina formulário
  duplicado). Erros no campo, no nó, no painel e no minimapa.
- Validação contínua + `POST /flows/{flow_id}/validate` (erros/alertas com
  referência a nó/conexão/campo).
- Rascunho local por workspace/usuário/fluxo/revisão-base; salvar explícito cria
  revisão no backend com controle de conflito. Fluxos antigos abrem sem
  alteração, migram só ao salvar nova revisão.

### 2.5 Runtime, SSE e subfluxos
- Eventos persistidos em tempo real via `GET /flows/runs/{run_id}/events` (SSE,
  sequência monotônica, retomada após reconexão sem perda/duplicação).
- Nó nativo de subfluxo: referência a fluxo+revisão publicada, I/O tipado,
  detecção de dependência circular, revisão fixada na execução.
- Compilar cada revisão publicada num plano imutável antes de executar.
- Preservar filas/retries/reconciliação/pausas/aprovações/idempotência/proteção
  contra envio duplicado. Bloquear publicação com erro estrutural, conexão
  ausente, permissão insuficiente ou credencial inválida. Revisões antigas
  imutáveis e executáveis.

### 2.6 Copiloto (depende do Gate 0.4)
- Copiloto conhece playbooks/conexões/agentes/áreas/políticas; aceita texto,
  imagem e documento (sem áudio); produz proposta + diff visual + riscos +
  dependências + testes sugeridos; **nunca publica/executa sozinho**; alteração
  vai para fila de aprovação. Pode criar/reorganizar/corrigir/explicar.

### 2.7 Troca segura (nada de atômico)
- Flow Studio atrás de **flag / rota paralela**; editor atual segue vivo; migrar
  nó a nó; virar por workspace. Retirar código duplicado **só depois** de
  paridade funcional provada. URLs e dados atuais preservados.

**Gate 2:** unit de cada nó/porta/migração/validação/comando; contract tests
frontend/API/worker; E2E completo (criar/editar/conectar/copiar/undo-redo/
validar/salvar/conflito/dry-run/executar/pausar/aprovar/retomar/cancelar); SSE
sem perda/duplicação; isolamento de workspace/permissão/credencial/revisão;
idempotência (mensagens/pagamentos/assinaturas/conectores); regressão visual
desktop/notebook/tablet/mobile; 100/300/500 nós sem travar; zero texto cortado/
overflow/controle inacessível; nenhuma função atual sumiu; nenhum fluxo publica
com erro estrutural; QA independente. Só então a troca interna.

---

## Ordem geral e por que nesta ordem

```
ONDA 0 (limpar) ──► ONDA 1 (segurança, shadow→enforce) ──► ONDA 2 (Flow Studio, faseado)
       │                        │                                    │
   destrava o resto      protege a fronteira real           só sobre fundação limpa
   e evita entrelaçar    antes de expor mais superfície      + dono do copiloto definido
```

- **0 antes de tudo:** a chave duplicada e a árvore suja quebram qualquer coisa
  grande construída em cima.
- **1 antes de 2:** endurecer a fronteira antes de expandir a superfície do
  editor; e a segurança é menor/mais contida que o Flow Studio.
- **2 por último e faseado:** é o maior investimento e o maior risco; só
  começa com fundação limpa, copiloto com dono, e nunca por troca atômica.
- **AWS no fim de tudo**, quando você mandar subir — WAF e cutover são fase de
  go-live, não desta.

## O que NÃO fazer (erros do plano original que este corrige)

- Não bundlar o fix de 30 min (schema) com o subsistema de segurança de semanas.
- Não auto-bloquear no dia 1 com heurística (shadow mode primeiro).
- Não trocar o editor de forma atômica (flag + incremental).
- Não iniciar o Flow Studio com dois copilotos meio-feitos na árvore.
- Não executar a parte AWS na fase local.
