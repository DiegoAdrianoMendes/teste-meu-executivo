# KLM Hub - OSS Adaptation Ledger

Nenhuma função do escopo público OSS foi removida dos checkouts pinados. Áreas
proprietárias continuam fora da materialização e submódulos privados não são
inicializados. O KLM usa APIs e padrões públicos dos motores, preserva os
sidecars para execução e diagnóstico e oferece a experiência primária na própria
SPA. O core não incorpora código upstream por cópia direta.

## Estado atual

| Upstream | Ref local | Capacidades nativas | Papel no KLM | Estado verificável |
|---|---|---:|---|---|
| Chatwoot | `v4.15.1` | 39 | motor omnichannel; conta vinculada exclusivamente ao workspace | 39 rotas KLM; 2 leituras locais com ressalva |
| Rocket.Chat | fonte `8.6.1`; runtime `6.13.1` | 33 | colaboração e salas; servidor e salas limitados ao workspace | 33 rotas KLM; risco de compatibilidade declarado |
| ChatterMate | `v1.0.14` | 27 | referência e motor de agentes, handoff, conhecimento e flows | 27 rotas KLM; organização e recursos isolados |
| BottleCRM | `0.46` | 36 | motor CRM auxiliar e referência para auditoria, RLS e MCP | 36 rotas KLM; organização vinculada exclusivamente |
| EmbeddedChat | `v0.2.3` | 0 | referência preservada, fora da matriz aprovada | não incorporado |

## Registro nativo V4

O registro atual contém 135 famílias classificadas, sem duplicidade e sem item
sem decisão. Todas possuem rota estável
`/app/recursos/{provider}/{capability}`, documento autenticado e experiência
schema-driven no KLM:

| Medida | Resultado |
|---|---:|
| Experiências nativas verificadas | 135 |
| Rotas nativas | 135 |
| Deep links como experiência primária | 0 |
| Capacidades com contrato local validado | 121 |
| Configurações bloqueadas por gate | 12 |
| Substituições responsivas | 2 |
| Ocorrências de ações nativas | 511 |
| Comandos técnicos únicos | 360 |
| Efeitos externos executados segundo a aplicação | 0 |
| Marcadores explícitos de dry-run validados | 511 |

Distribuição por motor: Chatwoot 39, Rocket.Chat 33, ChatterMate 27 e
BottleCRM 36. Níveis de prova: 108 `contract_and_native_read`, 13
`local_equivalent`, 12 `configuration_only` e 2 `responsive_substitution`.

Cada recurso aparece dentro da SPA com conteúdo específico, indicadores, tabelas
ou detalhes, ações tipadas, confirmação, permissões, estados de erro/vazio/gate,
auditoria e diagnóstico secundário do motor. Não há iframe nem payload técnico
bruto na visualização padrão. O launcher do produto upstream permanece apenas
como ferramenta secundária de administração e diagnóstico.

Fontes da implementação:

- `app/services/oss_capability_registry.py`: classificação e verdade das 135 capacidades;
- `app/services/oss_native_document_service.py`: documento e ações nativas;
- `app/services/oss_native_state_service.py`: estado, versão, idempotência e auditoria;
- `app/services/oss_native_*_service.py`: adapters isolados por motor;
- `app/data/oss_native_verification.json`: manifesto de QA vinculado ao código e ao contrato;
- `frontend/src/klm/components/NativeCapabilityWorkspace.tsx`: workspace nativo;
- `frontend/e2e/oss-native-capabilities.spec.ts`: matriz de rotas e ações;
- `scripts/verify_klm_local.py`: prova autenticada do runtime local.

Manifesto vigente: digest
`d2e5af57d5f9726ae8be841ce63e882663d9a9af7335cfe68b93d4e568be1c7d`
sobre 906 arquivos, com 1.538 testes backend, 176 testes frontend, 205 cenários
E2E e 11 testes PostgreSQL/RLS aprovados. O smoke pós-finalização confirmou
135 experiências, 121 capacidades contratuais, 511 ações e zero efeito externo
reportado pela aplicação. A prova não inclui captura independente de tráfego.
O manifesto 1.2 vincula as cinco suítes ao mesmo inventário e a um snapshot de
conteúdo determinístico, em vez de presumir que um worktree sujo pode ser
reproduzido somente pelo commit Git.

## Limites declarados

`nativeExperienceVerified` prova a experiência dentro do KLM por manifesto
compatível com o código; não significa que
todo efeito de rede do produto upstream está liberado. As 12 capacidades de
configuração externa permitem configuração, validação e simulação, mas mantêm o
efeito real bloqueado. As duas substituições responsivas são
`chatwoot.billing-cloud` e `bottlecrm.mobile-app`.

O runtime Rocket.Chat `6.13.1` permanece abaixo da fonte auditada `8.6.1` porque
o build comunitário mais novo encontrou dependência privada. Esse desvio deve
ser resolvido antes de produção. As leituras Chatwoot `reports-csat` e
`profile-mfa` respondem no KLM, mas o health local ainda informa ressalva; não
são contabilizadas como efeito externo nem como prova de entrega.

## Materialização verificável

- `docs/oss-upstreams.lock.json` registra origem HTTPS, tag, objeto da tag, commit resolvido, evidência de licença por blob Git, sentinelas e exclusões proprietárias.
- `scripts/fetch_klm_upstreams.sh` busca somente a tag explícita com partial clone `blob:none`, configura a exclusão antes do checkout e mantém `HEAD` destacado no commit pinado.
- `scripts/verify_klm_upstreams.sh` valida origem, tag/ref, commit, worktree limpo, cobertura do checkout, sentinelas, blobs de licença, submódulos não inicializados e ausência de blobs exclusivos das áreas proprietárias no object store.
- `.vendor/upstreams/*` é cache gerenciado e não é local de adaptação. Alterações devem ocorrer apenas em código KLM com o registro de importação abaixo.

## EmbeddedChat: licença e React

No pin `v0.2.3`, `packages/react/package.json` identifica `@embeddedchat/react@0.2.3`, declara licença `ISC`, usa React e React DOM `^17.0.2` no desenvolvimento e restringe ambos os peers a `>=17.0.2 <19.0.0`. Assim, o pacote cobre React 17/18 e exclui React 19; a alegação anterior de microfrontend React 19 estava incorreta.

O repositório não contém `LICENSE` na raiz. A declaração `ISC` é evidência do pacote React, não conclusão de licença para todo o monorepo. Nenhuma importação do EmbeddedChat fica liberada antes de revisão explícita desse escopo.

## Registro por importação

Cada módulo ou arquivo deverá registrar:

- aprovação correspondente nesta conversa;
- upstream, ref e commit;
- caminho original e caminho local;
- licença e aviso preservado;
- função original preservada;
- adaptação KLM realizada;
- dependências alteradas;
- testes upstream preservados;
- testes KLM adicionados;
- data e responsável;
- estratégia de sincronização;
- limitações e risco de conflito.

## Estados de feature

- `preserved`;
- `proposed_now`;
- `proposed_later`;
- `blocked_license`;
- `blocked_dependency`;
- `approved`;
- `implemented`.

## Restrições de importação

Permanecem fora do escopo OSS e não podem ser baixados, copiados ou ativados sem gate próprio:

- `chatwoot/enterprise/**` não é materializado pelo fetch OSS;
- `chattermate/backend/app/enterprise` e `chattermate/frontend/src/modules/enterprise` são gitlinks privados e não são inicializados;
- `rocketchat/apps/meteor/ee/**` não é materializado pelo fetch OSS;
- `rocketchat/ee/**` não é materializado pelo fetch OSS;
- arquivos sem licença verificável;
- secrets ou dados reais;
- qualquer comportamento que registre credenciais;
- qualquer ferramenta de agente sem capability KLM.

## QA vigente

O laudo de 17/07 foi substituído como evidência vigente. A rodada específica da
experiência nativa, com comandos executados, contagens finais, skips e riscos
residuais, está registrada em `docs/QA_OSS_NATIVE_2026-07-18.md`. A suíte
local integral terminou com 1.538 testes backend, 176 testes frontend e 205
testes E2E aprovados, sem falhas; nenhum efeito externo foi liberado e a
aplicação reportou zero execução nos 511 contratos instrumentados.

## Importação: validador de grafo/schema CSP-safe (Flow Studio)

Entrada registrada segundo a seção "Registro por importação" acima.

- **Aprovação:** contrato do spike Flow Studio, `docs/AUDITORIA_FLOW_REFERENCIAS_2026-07-24.md` seção 7 (Tarefa B — vendoring legal do validador CSP-safe).
- **Upstream / ref / commit:** Workflow Builder SDK (`@workflowbuilder/sdk` v2.2.0), `https://github.com/synergycodes/workflowbuilder`. Referência de trabalho local em `scratchpad/flow_ref/workflowbuilder` (checkout sem commit fixado disponível no snapshot).
- **Caminho original → caminho local:**
  - `packages/sdk/src/utils/validation/workflow-builder-validator.ts` → `frontend/src/vendor/workflowbuilder/csp-safe-schema-validator.ts`
  - `packages/sdk/src/utils/validation/get-node-errors.ts` + `flat-errors.ts` → `frontend/src/vendor/workflowbuilder/get-node-errors.ts`
- **Licença e aviso preservado:** Apache License 2.0. Copyright © 2025-2026 Synergia Pro Sp. z o.o. retido nos cabeçalhos dos arquivos derivados, em `THIRD_PARTY.md` (raiz) e em `frontend/src/vendor/workflowbuilder/README.md`. Os arquivos de origem não têm header de copyright por arquivo; o aviso vem do `LICENSE` do upstream.
- **Função original preservada:** validação de propriedades de nó contra JSON Schema **sem `eval`/`new Function`** (CSP-safe); contrato de erro Ajv-compatível (`keyword`, `instancePath`, `schemaPath`, `message`, `params.missingProperty`) e mensagem de `required` idêntica.
- **Adaptação KLM realizada:** **reimplementação da lógica pura** (não cópia byte-a-byte). O original não é dependency-free: embrulha `@cfworker/json-schema` (motor) e usa tipos de `ajv`; o `get-node-errors` original ainda puxa o store de paleta (zustand) e o modelo de nó do SDK. Reescrito como interpretador de subconjunto draft-07 (`type`, `required`, `properties`, `enum`, `items`) **sem imports externos**, com tipos de nó mínimos e locais e API de nomes neutros (`cspSafeValidator`, `getNodeErrors`, `getGraphErrors`). Marca do upstream só na atribuição.
- **Dependências alteradas:** nenhuma adicionada (0 pacotes novos). Não incorpora `@cfworker/json-schema` nem `ajv`.
- **Testes upstream preservados:** os casos do `get-node-errors.spec.ts` (required + params.missingProperty, guarda de `undefined`) foram portados/adaptados.
- **Testes KLM adicionados:** `frontend/src/vendor/workflowbuilder/csp-safe-schema-validator.test.ts` (prova estática de dependency-free e ausência de `eval`/`new Function`, contrato de erro, detecção de grafo inválido simples).
- **Data e responsável:** 2026-07-24, sessão do spike Flow Studio (Tarefa B).
- **Estratégia de sincronização:** divergência intencional (reimplementação); sem merge automático. Mudanças de comportamento no upstream reavaliadas manualmente contra este subconjunto.
- **Limitações e risco de conflito:** cobre apenas o subconjunto de JSON Schema usado pelos schemas de propriedade de nó (não é validador draft-07 completo). Módulo isolado em `frontend/src/vendor/`, sem tocar o painel atual nem o executor; sem conflito esperado com a sessão paralela.
- **Estado da feature:** `implemented`.
