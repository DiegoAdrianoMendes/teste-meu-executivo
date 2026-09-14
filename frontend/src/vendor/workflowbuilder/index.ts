/**
 * Módulo vendored: validador de grafo/schema CSP-safe.
 * Derivado do Workflow Builder SDK (c) 2025-2026 Synergia Pro Sp. z o.o.,
 * Apache-2.0. Atribuição em ./README.md, THIRD_PARTY.md e
 * docs/OSS_ADAPTATION_LEDGER.md.
 *
 * A marca do upstream aparece apenas na atribuição; a API pública tem nomes
 * neutros (cspSafeValidator, getNodeErrors, getGraphErrors).
 */
export {
  createCspSafeValidator,
  cspSafeValidator,
  REQUIRED_PROPERTY_REGEX,
} from './csp-safe-schema-validator';
export type {
  CspSafeValidator,
  JsonSchema,
  JsonSchemaType,
  ValidateFunction,
  ValidationError,
} from './csp-safe-schema-validator';
export {
  flatErrors,
  getGraphErrors,
  getNodeErrors,
  validateProperties,
} from './get-node-errors';
export type {
  FlatError,
  FlowNodeLike,
  NodeGraphError,
  SchemaByNodeType,
} from './get-node-errors';
