# Auditoria de Referências para o Flow Studio Nativo

**Data:** 2026-07-24
**Escopo:** decidir se os repositórios de referência (Workflow Builder / Synergy Codes, Node-RED, chat-flow-builder) servem para reconstruir o Flow Studio do KLM de forma nativa, o que é adotável como código, o que é só padrão, e o que deve ser descartado.
**Base auditada:** `klm-legal-hub-backend-real` (runtime de flows em `app/`, editores em `frontend/src/`).
**Método:** integração de quatro análises de agentes, resolução de contradições, e verificação direta no disco dos pontos materiais.

---

## 1. Sumário executivo

**Sim, dá para usar de padrão e refazer nativo, e essa é a única rota recomendada.** O runtime do KLM (Celery + Postgres, `app/workers/flow_tasks.py`, 3.653 linhas, com idempotência, checkpoints e pausa fail-closed) é maduro e fica **intocado**: nenhum dos repositórios entra no backend. O valor das referências está na **arquitetura de frontend**, não no código de UI: o Workflow Builder (Apache 2.0) prova os contratos que faltam ao KLM (painel de inspeção dirigido por schema, registro de plugins, taxonomia de eventos de execução SSE), e o Node-RED (Apache 2.0) prova dois padrões maduros (catálogo/config co-localizado por tipo de nó e subfluxos). O chat-flow-builder é **descartado por completo** (sem licença, cadeia de direitos quebrada). A recomendação é adotar **padrões**, copiar apenas alguns módulos pequenos e sem dependências do Workflow Builder sob obrigação Apache 2.0, manter `@xyflow/react` (MIT) como canvas e o executor atual como está. Estima-se corte de 30 a 45 por cento do esforço de design/arquitetura do rebuild de frontend, e zero no backend.

---

## 2. Premissas verificadas

| # | Afirmação do plano / das análises | O que é verdade (disco / web) | Veredito |
|---|---|---|---|
| P1 | React Flow (`@xyflow/react`) é MIT e Pro não é necessário para uso comercial | Confirmado na web (reactflow.dev). No disco, `frontend/package.json` já traz `@xyflow/react ^12.10.2`; o master plan (linha 141) registra "Pro não necessário" | **VERDADE** |
| P2 | Workflow Builder tem LICENSE Apache 2.0 | Confirmado: `LICENSE` Apache 2.0, copyright 2025-2026 Synergia Pro Sp. z o.o. (marca Synergy Codes) | **VERDADE** |
| P3 | Node-RED é Apache 2.0 (OpenJS Foundation) | Confirmado | **VERDADE** |
| P4 | chat-flow-builder não tem licença e é clone de obra de terceiro | Confirmado: sem arquivo de licença; README indica clone de obra de outro autor (Denish). Repo sem licença = autor retém todos os direitos (GitHub docs); cadeia de direitos quebrada, nem "compra" é confiável | **VERDADE, DESCARTAR** |
| P5 | Existem dois editores de flow no frontend hoje | Confirmado no disco: `frontend/src/pages/FlowBuilder.tsx` (usa `useNodesState`/`useEdgesState`) e `frontend/src/klm/pages/FlowsPage.tsx` (usa `applyNodeChanges` + `NodeConfigPanel`), ambos sobre `@xyflow/react`. Master plan linha 135 confirma "existem dois editores frontend hoje" | **VERDADE** |
| P6 | O painel de configuração de nó é bespoke (formulário por tipo) | Confirmado: `frontend/src/components/flow/NodeConfigPanel.tsx`, 735 linhas, `switch (node.type)` na linha 170 com casos manuais por tipo (`trigger`, `send_message`, `condition`, `router`, `agent`, ...) | **VERDADE** |
| P7 | A validação do grafo é um switch por tipo, e o config é um modelo chapado | Confirmado: `app/schemas/flow.py:53` `validate_flow_graph` (cadeia de `if node_type == ...`) e `app/schemas/flow.py:247` `FlowNodeConfig` (um único modelo com ~90 campos opcionais para todos os tipos) | **VERDADE** |
| P8 | O registro de nós está em v1 (evoluível para v2) | Confirmado: `app/services/flow_node_registry.py` grava `config_version: 1` em todo spec (linha 50); `FlowNodeCatalogRead.schema_version = 1`. O `FlowNodeSpec` carrega metadados de execução mas **não** carrega o schema de config do nó | **VERDADE** |
| P9 | Subfluxos são lacuna da base | Confirmado: `grep` por `subflow`/`subfluxo` em `app/` retorna vazio; não há nó de subfluxo. Master plan (linhas 179-182) lista "nó nativo de subfluxo" como item a construir | **VERDADE (lacuna)** |
| P10 | O executor é maduro e separável do editor | Confirmado: `app/workers/flow_tasks.py` tem `idempotencyKey`, `sequence` (`FLOW_EXTERNAL_INTENT_SEQUENCE_KEY`), `revisionId`, checkpoints e `_pause_flow_for_uncertain_external_intent` (pausa fail-closed em entrega incerta). Nenhuma dependência de Temporal | **VERDADE (preservar)** |
| P11 | Workflow Builder serializa `apiKey` no JSON do grafo (`ai-tools-control`) | Reportado pela análise como antipadrão. **A base do KLM NÃO faz isso**: `FlowNodeConfig` não tem campo de segredo, e nós externos são `execution_mode: gated_external` com credencial resolvida server-side. A base já está correta; a lição é **não regredir** copiando esse controle | **VERDADE, EVITAR o antipadrão** |
| P12 | Versão do Workflow Builder | Contradição entre fontes: dado verificado diz **v2.0.0** (produto/monorepo), análise do agente diz **SDK v2.2.0** (pacote `@workflowbuilder/sdk`). Não é material para o veredito; provável defasagem entre versão do produto e do pacote | **DISCREPÂNCIA MENOR, checar no spike** |

---

## 3. Por repositório

### 3.1 Workflow Builder (Synergy Codes / Synergia Pro Sp. z o.o.) — Apache 2.0

**Veredito: referência-padrão + poucos módulos copiáveis. Não adotar o pacote inteiro.**

**ADOTÁVEL como código (obrigação Apache 2.0, ver seção 6):** módulos pequenos, sem dependências ou quase, que já vêm prontos e não arrastam a lib de UI deles:
- Adapters de plugin/decorator: `packages/sdk/src/features/plugins-core/adapters/*` (adapter-components, adapter-functions, adapter-i18n) mais `adapters/utils/register-decorator.ts` e `sort-by-priority.ts`. Cerca de 250 LOC, dependency-free, dropável.
- Validador CSP-safe do grafo: `packages/sdk/src/utils/validation/workflow-builder-validator.ts` e `get-node-errors.ts` (sem `eval`).
- Plugin de undo/redo por snapshot: `apps/demo/src/app/plugins/undo-redo/*` (snapshot de nodes/edges/layoutDirection, teto de 100, watch de drag).
- Taxonomia de eventos de execução como schema inicial: `packages/types/src/workflow-execution/execution-events.ts` (contrato SSE com `node_waiting`, `sequence`, `lastSequence`).

**Só PADRÃO a reimplementar (não copiar, imitar a arquitetura):**
- Inspector dirigido por schema (o maior ganho): `NodeDefinition<T>` com `{schema, uischema, outputSchema}` renderizado por JsonForms (`packages/sdk/src/node/node-data.ts`, `features/properties-bar/.../node-properties.tsx`, `features/json-form/json-form.tsx`). Encaixa direto no `flow_node_registry.py` e aposenta o `NodeConfigPanel.tsx` bespoke.
- Registro de renderers custom de JsonForms com testers (`features/json-form/extension-registry.ts`): cada input especial (seletor de tribunal, campo CNJ) vira um renderer com tester.
- Fronteira editor->host por estratégia `props`/`onDataSave` (`packages/sdk/src/types/integration.ts`): o React entrega o JSON serializável ao endpoint do KLM; trivial de reimplementar.
- Overlay de execução no canvas por SSE (`apps/ai-studio/src/hooks/use-backend-execution.ts`): `EventSource` sobre o contrato de eventos, mapeando quase 1:1 na semântica de pausa/idempotência/dedup já existente em `flow_tasks.py`.

**DESCARTAR:**
- Todo o runtime de execução: `packages/execution-core/*`, `apps/backend/*`, `apps/execution-worker/*` (adaptador Temporal). O KLM tem Celery/Postgres; não importar nada disso.
- Os componentes visuais: 73 de 156 arquivos `.tsx` do SDK importam `@synergycodes/overflow-ui` e `index.css` puxa `tokens.css` (`--ax-*`). Adotar a UI arrastaria essa design-language beta e brigaria com o design system do KLM. O visual é o passivo, não o ativo.
- Auto-layout ELK: no repo aberto é placeholder (`apps/demo/src/app/plugins/elk-layout/plugin-exports.ts` exporta função vazia); o layout real é plugin pago. Reimplementar com `elkjs`/`dagre` se necessário.
- O controle `ai-tools-control` que serializa `apiKey` no grafo: antipadrão, ver P11.

### 3.2 Node-RED (OpenJS Foundation) — Apache 2.0

**Veredito: referência-padrão de comportamento. Espelhar os padrões, jamais o runtime Node.js (o KLM tem Celery/Postgres).**

**Só PADRÃO a reimplementar (dois padrões maduros e diretamente aplicáveis):**
- **Config co-localizado por tipo de nó:** o schema de configuração vive junto da definição do tipo, não num modelo global chapado. Aplicado ao KLM, cada `FlowNodeSpec` em `app/services/flow_node_registry.py` passa a expor o próprio `{schema, uischema}`, aposentando o `FlowNodeConfig` de ~90 campos (`schemas/flow.py:247`) e o switch de `validate_flow_graph` (`schemas/flow.py:53`). Efeito: adicionar um nó vira "um arquivo", e o editor gera o formulário a partir do registro.
- **Subfluxos (a maior lacuna do KLM, P9):** um nó de subfluxo que referencia um fluxo publicado por revisão pinada, com I/O tipado. Corroborado pelo master plan (linhas 179-182).

**DESCARTAR:** o runtime Node.js do Node-RED inteiro e seu modelo de deploy. Nada de código entra; só a forma dos padrões.

*Ressalva de escopo:* das análises recebidas, o detalhamento do Node-RED chegou parcial; os dois padrões acima são os transmitidos e confirmados pelo master plan. Padrões adicionais de catálogo/palette devem ser levantados no spike da seção 7 antes de virarem compromisso.

### 3.3 chat-flow-builder — sem licença

**Veredito: DESCARTAR por completo. Não ler para copiar, não usar como referência de código.**
Sem arquivo de licença, o autor retém todos os direitos: ninguém pode reproduzir nem derivar (GitHub docs). Pior, o README indica ser clone de obra de terceiro (Denish), então a cadeia de direitos está quebrada e nem uma eventual "compra" seria confiável. O master plan (linha 144) já determina "não usar código do repo sem licença". Serve, no máximo, como inspiração visual passiva de longe, nunca como fonte de código ou de arquitetura.

---

## 4. A base atual do KLM

### 4.1 Preservar INTACTO (runtime maduro)
- **Executor:** `app/workers/flow_tasks.py`. Idempotência por `idempotencyKey` + `sequence`, checkpoints, `revisionId`, e `_pause_flow_for_uncertain_external_intent` (fail-closed em entrega externa incerta). É o ativo mais valioso e não deve ser tocado pelo rebuild. Zero por cento de reescrita.
- **Modelo de credencial:** segredos server-side, nós externos como `gated_external`. A base já faz certo o que o Workflow Builder faz errado (P11). Não regredir.
- **Contratos de execução já expostos:** `FlowRunRead`, `MatterFlowJourneyRead`, `FlowDryRunRead` em `app/schemas/flow.py`. O overlay SSE nativo deve consumir esses contratos, não recriá-los.
- **Canvas:** `@xyflow/react` (MIT) já em uso, mesma base do Workflow Builder. Mantém-se.

### 4.2 Evoluir
- **Registro v1 -> v2:** `app/services/flow_node_registry.py`. Hoje o `FlowNodeSpec` (`config_version: 1`) descreve execução e portas, mas **não** carrega o schema de config. A evolução é acrescentar `{schema, uischema, outputSchema}` por tipo (padrão Node-RED + Workflow Builder) e subir `schema_version`/`config_version`, migrando de forma versionada.
- **Aposentar o config chapado:** substituir o `FlowNodeConfig` de ~90 campos (`schemas/flow.py:247`) e o switch de `validate_flow_graph` (`schemas/flow.py:53`) por validação derivada do schema por tipo. Manter compatibilidade de leitura dos grafos existentes.
- **Consolidar os dois editores:** unificar `frontend/src/pages/FlowBuilder.tsx` e `frontend/src/klm/pages/FlowsPage.tsx` em um único Flow Studio, matando os catálogos hardcoded divergentes e o `NodeConfigPanel.tsx` bespoke (735 linhas) em favor de painel dirigido por schema. O master plan (linha 197) já prevê fazer isso atrás de flag/rota paralela, com o editor atual vivo e migração incremental.

---

## 5. Recomendação de abordagem para o rebuild nativo

1. **Canvas:** manter `@xyflow/react` (MIT), sem React Flow Pro.
2. **Do Workflow Builder, adotar PADRÕES:** painel de inspeção dirigido por schema (via `@jsonforms/react`, que hoje **não** é dependência do frontend e precisará ser adicionada) e registro de plugins/decorators. Copiar como código apenas os módulos sem dependências listados em 3.1 (adapters de plugin, validador CSP-safe, plugin undo-redo, taxonomia de eventos), sob obrigação Apache 2.0.
3. **Do Node-RED, adotar PADRÕES:** config co-localizado por tipo (alimenta o registro v2) e nó de subfluxo com revisão pinada e I/O tipado.
4. **Não importar Temporal nem qualquer runtime de terceiro.** O executor atual (`flow_tasks.py`, Celery/Postgres) é preservado. A reconstrução do executor ("Trilha B") fica **fora do MVP**.
5. **Sequenciamento seguro:** Flow Studio atrás de flag/rota paralela, editor atual segue funcionando, migração incremental (não trocar o editor de forma atômica). Alinhado ao master plan (ONDA 2).
6. **Fonte da verdade única:** o registro v2 passa a dirigir catálogo, palette, validação e formulário, eliminando os dois catálogos divergentes de hoje.

---

## 6. Riscos e obrigações de licença

**Apache 2.0 (Workflow Builder), se copiarmos código:**
- Cláusula 4(a): incluir uma cópia da licença Apache 2.0 junto ao código derivado.
- Cláusula 4(b): reter avisos de copyright. Os arquivos do SDK não têm header por-arquivo nem SPDX (grep vazio), então há pouco a reter no nível de arquivo; retém-se o copyright do projeto.
- Cláusula 4(d): **não há arquivo NOTICE no repo do Workflow Builder**, logo **não** existe obrigação de propagar NOTICE. Ainda assim, como boa prática, criar um `NOTICE`/`THIRD_PARTY` no KLM (hoje inexistente, verificado) creditando "Workflow Builder SDK (c) Synergia Pro Sp. z o.o., Apache-2.0" para os módulos copiados, e registrar no inventário existente `docs/OSS_ADAPTATION_LEDGER.md`.
- Cláusula 3 (patente): grant de patente presente, favorável.
- **Marca:** não usar as marcas "Workflow Builder" nem "Synergy Codes" no produto.
- Gatilho prático baixo: o KLM é SaaS privado e não distribui fonte; a obrigação principal se resume a manter licença + atribuição dos trechos copiados.

**Risco jurídico do chat-flow-builder:** repo sem licença com cadeia de direitos quebrada. Qualquer uso de código é violação de direitos autorais. Mitigação: descarte total (seção 3.3) e não deixar o repo entrar na árvore.

**Riscos técnicos do rebuild:**
- **Nova dependência de UI:** adotar painel dirigido por schema traz `@jsonforms/react` para o frontend. Avaliar peso, manutenção e compatibilidade com React em uso antes de comprometer.
- **Divergência de versões de estado:** o SDK de referência usa `zustand 5`; o KLM está em `zustand ^4.5.4`. Se algum módulo copiado tocar store, alinhar versão ou isolar.
- **Migração de grafos existentes:** aposentar `FlowNodeConfig`/switch exige camada de compatibilidade de leitura para os flows já persistidos; não quebrar revisões antigas.
- **Regressão de credencial:** ao portar padrões do Workflow Builder, não arrastar o `ai-tools-control` (segredo no grafo). Manter o modelo server-side atual (crítico para LGPD em SaaS jurídico).

---

## 7. Próximo passo recomendado (spike de 1 a 2 dias, ANTES de qualquer rebuild)

**Objetivo:** provar o padrão schema-driven ponta a ponta em um único tipo de nó, sem tocar no executor.

**Escopo do spike:**
1. Escolher **um** tipo de nó já existente (sugestão: `condition`, por ter validação e portas `true`/`false`) e acrescentar `{schema, uischema}` ao seu `FlowNodeSpec` em `app/services/flow_node_registry.py`, expondo no catálogo (`FlowNodeCatalogRead`).
2. No frontend, renderizar o formulário desse nó via `@jsonforms/react` a partir do schema, num painel isolado, em paralelo ao `NodeConfigPanel.tsx` atual (sem removê-lo).
3. Copiar apenas o validador CSP-safe (`workflow-builder-validator.ts`) e um adapter de plugin do Workflow Builder, validar que rodam dependency-free e registrar a atribuição Apache 2.0 num `THIRD_PARTY` de teste.
4. Fazer um round-trip: editar o nó no painel schema-driven, salvar via `onDataSave` para o endpoint atual, confirmar que `validate_flow_graph` aceita e que um dry-run (`FlowDryRunRead`) roda sem regressão.
5. Resolver a discrepância de versão do Workflow Builder (P12) e confirmar o inventário de arquivos realmente sem dependências antes de qualquer cópia em massa.

**Critério de saída:** um nó configurado inteiramente por schema, persistido e validado pelo backend atual, com atribuição de licença registrada e o executor intocado. Se passar, libera a ONDA 2 incremental; se falhar, o custo foi de 1 a 2 dias e nada do runtime foi arriscado.

---

*Fontes: análises dos quatro agentes (Workflow Builder, Node-RED, chat-flow-builder, base atual); verificação direta em `app/services/flow_node_registry.py`, `app/schemas/flow.py`, `app/workers/flow_tasks.py`, `frontend/src/pages/FlowBuilder.tsx`, `frontend/src/klm/pages/FlowsPage.tsx`, `frontend/src/components/flow/NodeConfigPanel.tsx`, `frontend/package.json`, `docs/PLANO_MESTRE_2026-07-24.md`, `docs/OSS_ADAPTATION_LEDGER.md`; confirmação web de reactflow.dev e GitHub docs.*
