# Third-Party Notices — KLM Hub

Este arquivo lista software de terceiros incorporado (vendored) ou derivado no
KLM Hub, com a atribuição exigida pelas respectivas licenças. Ele complementa,
não substitui, os textos de licença de cada dependência instalada via gerenciador
de pacotes.

Cada dependência distribuída sob Apache License 2.0 é acompanhada da atribuição
abaixo (cláusula 4 da licença). O texto integral da Apache License 2.0 está em
https://www.apache.org/licenses/LICENSE-2.0.

---

## Workflow Builder SDK — Apache License 2.0

- **Componente:** Workflow Builder SDK (`@workflowbuilder/sdk`)
- **Copyright:** © 2025-2026 Synergia Pro Sp. z o.o.
- **Licença:** Apache License 2.0 (https://www.apache.org/licenses/LICENSE-2.0)
- **Origem:** https://github.com/synergycodes/workflowbuilder
- **Homepage:** https://www.workflowbuilder.io/

### O que foi incorporado

Trabalho **derivado** (reimplementação da lógica pura de validação, não cópia
byte-a-byte), em `frontend/src/vendor/workflowbuilder/`:

| Arquivo derivado | Arquivo(s) de origem no upstream |
|---|---|
| `csp-safe-schema-validator.ts` | `packages/sdk/src/utils/validation/workflow-builder-validator.ts` |
| `get-node-errors.ts` | `packages/sdk/src/utils/validation/get-node-errors.ts` + `flat-errors.ts` |

Motivo da reimplementação: o validador original depende de `@cfworker/json-schema`
(motor de validação) e dos tipos de `ajv`, e o `get-node-errors` original depende
do store de paleta (zustand) e do modelo de nó do SDK. Para manter o módulo
**dependency-free** e CSP-safe, a lógica pura de validação foi reescrita sem
imports externos, preservando o contrato de erro do upstream.

### Modificações

Trabalho modificado em relação ao original, conforme permitido e exigido pela
cláusula 4(b) da Apache License 2.0. Os cabeçalhos dos arquivos derivados retêm o
aviso de copyright do upstream e indicam que se trata de trabalho derivado. A
marca "Workflow Builder"/"Synergy Codes" é usada somente nesta atribuição, não na
API nem na interface do produto.

Registro operacional em `docs/OSS_ADAPTATION_LEDGER.md`.
