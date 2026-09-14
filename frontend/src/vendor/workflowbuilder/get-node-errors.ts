/**
 * Erros de validação por nó de fluxo (versão pura, dependency-free).
 * =====================================================================
 *
 * Trabalho derivado de:
 *   Workflow Builder SDK (c) 2025-2026 Synergia Pro Sp. z o.o.
 *   Licenciado sob Apache License 2.0.
 *   https://github.com/synergycodes/workflowbuilder
 *   (packages/sdk/src/utils/validation/get-node-errors.ts + flat-errors.ts)
 *
 * -----------------------------------------------------------------------------
 * O `get-node-errors.ts` original NÃO é portável: ele importa `getNodeDefinition`
 * (que puxa `getPaletteData` do store de paleta, baseado em zustand) e o modelo
 * de nó completo do SDK (`WorkflowBuilderNode`, `PaletteItem`, `FlatError`).
 *
 * Aqui extraímos apenas a lógica pura: validar `node.data.properties` contra o
 * schema da definição e achatar os erros. Os tipos de nó são mínimos e locais,
 * sem acoplar o store de paleta nem os tipos do SDK. Assim o módulo roda
 * dependency-free e serve o nó 'condition' (e qualquer outro) do KLM Flow.
 */

import { cspSafeValidator } from './csp-safe-schema-validator';
import type { JsonSchema, ValidationError } from './csp-safe-schema-validator';

/** Erro achatado, formato consumido pela UI (derivado de `flat-errors.ts`). */
export type FlatError = {
  keyword: string;
  instancePath: string;
  schemaPath: string;
  message: string;
};

/** Nó de fluxo mínimo: só o que a validação precisa ler. */
export type FlowNodeLike = {
  id?: string;
  data?: {
    type?: string;
    properties?: unknown;
  };
};

/** Mapa de schema por tipo de nó (ex.: { condition: <schema>, delay: <schema> }). */
export type SchemaByNodeType = Record<string, JsonSchema>;

/** Erros de um nó dentro de um grafo, indexados pelo id do nó. */
export type NodeGraphError = {
  nodeId: string;
  errors: FlatError[];
};

/**
 * Achata os erros do validador no formato consumido pela UI.
 * Derivado de `flatErrors` do SDK original (sem o campo `schema`, que a nossa
 * `ValidationError` não expõe e nenhum consumidor do KLM usa).
 */
export function flatErrors(errors: ValidationError[] | null | undefined): FlatError[] {
  return errors
    ? errors.map((error) => ({
        keyword: error.keyword,
        instancePath: error.instancePath,
        schemaPath: error.schemaPath,
        message: error.message,
      }))
    : [];
}

/**
 * Valida um conjunto de propriedades contra um schema e retorna os erros
 * achatados (vazio quando válido). É a versão dependency-free e reutilizável
 * do miolo do `getNodeErrors` original.
 */
export function validateProperties(schema: JsonSchema, properties: unknown): FlatError[] {
  const validate = cspSafeValidator.compile(schema);
  if (validate(properties)) {
    return [];
  }
  return flatErrors(validate.errors);
}

/**
 * Erros de validação de um único nó, dado o schema da sua definição.
 * Se não houver schema (definição desconhecida), retorna `[]` — como o original,
 * que não valida nós sem definição.
 */
export function getNodeErrors(node: FlowNodeLike | undefined, schema?: JsonSchema): FlatError[] {
  if (!node || !schema) {
    return [];
  }
  return validateProperties(schema, node.data?.properties);
}

/**
 * Valida um grafo inteiro: para cada nó, resolve o schema pelo seu tipo e
 * coleta os erros. Retorna somente os nós que têm ao menos um erro.
 * Derivado (puro) de `getNodesWithErrors` do SDK original.
 */
export function getGraphErrors(
  nodes: FlowNodeLike[],
  schemaByType: SchemaByNodeType,
): NodeGraphError[] {
  const result: NodeGraphError[] = [];

  nodes.forEach((node, index) => {
    const type = node.data?.type ?? '';
    const schema = schemaByType[type];
    const errors = getNodeErrors(node, schema);
    if (errors.length > 0) {
      result.push({
        nodeId: node.id ?? `#${index}`,
        errors,
      });
    }
  });

  return result;
}
