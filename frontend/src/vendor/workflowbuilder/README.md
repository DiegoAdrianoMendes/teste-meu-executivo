# Vendored — validador de grafo/schema CSP-safe

Este diretório contém código **derivado** do **Workflow Builder SDK**, incorporado
sob a Apache License 2.0. É código vendored e claramente marcado; não é código
original do KLM Hub.

## Atribuição

- **Upstream:** Workflow Builder SDK (`@workflowbuilder/sdk`)
- **Copyright:** © 2025-2026 Synergia Pro Sp. z o.o.
- **Licença:** Apache License 2.0 — https://www.apache.org/licenses/LICENSE-2.0
- **Repositório:** https://github.com/synergycodes/workflowbuilder
- **Homepage:** https://www.workflowbuilder.io/

Arquivos de referência no upstream:

- `packages/sdk/src/utils/validation/workflow-builder-validator.ts`
- `packages/sdk/src/utils/validation/get-node-errors.ts`
- `packages/sdk/src/utils/validation/flat-errors.ts`

> Os arquivos originais **não** trazem cabeçalho de copyright por arquivo; o aviso
> de copyright do projeto vive no `LICENSE` (apêndice Apache). Ele foi retido nos
> cabeçalhos dos arquivos derivados abaixo.

## O que foi feito: reimplementação, não cópia direta

O validador original **não é dependency-free**. Ele resolve o problema de CSP
(política sem `unsafe-eval`) **embrulhando o pacote `@cfworker/json-schema`**
(para substituir o Ajv, que usa `new Function()` em `compile()`), e usa os **tipos
de `ajv`**. Nenhum desses pacotes está instalado neste frontend.

O `get-node-errors.ts` original, além disso, importa o store de paleta
(`getPaletteData`, baseado em zustand) e o modelo de nó completo do SDK — não é
portável isoladamente.

Por isso, aqui há uma **reimplementação mínima da lógica pura de validação**,
adaptada ao TypeScript do projeto e **sem nenhum import de pacote externo**:

| Arquivo | Deriva de | Conteúdo |
|---|---|---|
| `csp-safe-schema-validator.ts` | `workflow-builder-validator.ts` | Interpretador de JSON Schema (draft-07, subconjunto: `type`, `required`, `properties`, `enum`, `items`) sem `eval`/`new Function`; interface `compile`/`validate` com cache WeakMap e guarda contra `undefined`. |
| `get-node-errors.ts` | `get-node-errors.ts` + `flat-errors.ts` | Validação de propriedades de nó e de grafo, com tipos de nó mínimos e locais (sem store de paleta). |
| `index.ts` | — | Barrel de reexport com **nomes neutros de API**. |
| `csp-safe-schema-validator.test.ts` | `get-node-errors.spec.ts` (adaptado) | Prova dependency-free + CSP-safe + detecção de grafo inválido. |

### Contrato preservado do upstream

- Formato de erro Ajv-compatível: `{ keyword, instancePath, schemaPath, message, params }`.
- Mensagem de `required`: `Instance does not have required property "X".`.
- `params.missingProperty` populado (usado para destacar o campo com erro na UI).
- Guarda contra `undefined` retornando `false` (como o Ajv), em vez de lançar.

## Marca

A marca **"Workflow Builder" / "Synergy Codes"** aparece **apenas nesta
atribuição**. A API pública usa nomes neutros (`cspSafeValidator`,
`getNodeErrors`, `getGraphErrors`) e não expõe a marca ao produto.

## Sincronização

Divergência intencional do upstream (reimplementação). Não há fluxo de merge
automático; mudanças de comportamento no upstream devem ser reavaliadas
manualmente contra este subconjunto. Ver `docs/OSS_ADAPTATION_LEDGER.md`.
