/**
 * Validador de JSON Schema CSP-safe (sem `eval` / sem `new Function`).
 * =====================================================================
 *
 * Trabalho derivado de:
 *   Workflow Builder SDK (c) 2025-2026 Synergia Pro Sp. z o.o.
 *   Licenciado sob Apache License 2.0.
 *   https://github.com/synergycodes/workflowbuilder
 *   (packages/sdk/src/utils/validation/workflow-builder-validator.ts)
 *
 * Ver termos completos em frontend/src/vendor/workflowbuilder/README.md,
 * THIRD_PARTY.md (raiz) e docs/OSS_ADAPTATION_LEDGER.md.
 *
 * -----------------------------------------------------------------------------
 * Motivo do vendoring (reimplementação, não cópia):
 *
 * O arquivo original resolve o problema de CSP (Content Security Policy sem
 * `unsafe-eval`) embrulhando o pacote `@cfworker/json-schema` para substituir o
 * Ajv (que usa `new Function()` em `compile()`). Ou seja, a lógica original NÃO
 * é dependency-free: ela delega a validação a `@cfworker/json-schema` e usa os
 * tipos de `ajv`. Nenhum desses pacotes está instalado neste frontend.
 *
 * Para manter o requisito de "rodar dependency-free", esta é uma reimplementação
 * mínima e portável da lógica pura de validação, cobrindo o subconjunto de
 * JSON Schema draft-07 efetivamente usado pelos schemas de propriedades de nó
 * (type, required, properties, enum, items). Preserva o contrato de erro do
 * original que o restante do código depende:
 *   - formato Ajv-compatível: { keyword, instancePath, schemaPath, message, params }
 *   - mensagem de "required": `Instance does not have required property "X".`
 *   - `params.missingProperty` populado (usado para destacar o campo com erro)
 *   - interface duck-typed `compile(schema)` / `validate(schema, data)` com cache
 *     por referência (WeakMap) e guarda contra `undefined`.
 *
 * NÃO usa nenhum import de pacote externo: só JavaScript padrão. Isso garante
 * comportamento CSP-safe (nenhum `eval`, nenhum `new Function`) por construção.
 */

/** Tipos primitivos de JSON Schema suportados por este validador. */
export type JsonSchemaType =
  | 'object'
  | 'array'
  | 'string'
  | 'number'
  | 'integer'
  | 'boolean'
  | 'null';

/** Subconjunto de JSON Schema draft-07 que este validador interpreta. */
export type JsonSchema = {
  type?: JsonSchemaType | JsonSchemaType[];
  properties?: Record<string, JsonSchema>;
  required?: string[];
  enum?: unknown[];
  items?: JsonSchema;
  // Palavras-chave não suportadas são ignoradas (semântica padrão de JSON Schema
  // para keywords desconhecidas), nunca fabricam configuração nem passam silenciosamente
  // aquilo que as keywords suportadas reprovariam.
  [keyword: string]: unknown;
};

/** Objeto de erro compatível com o formato do Ajv (`ErrorObject`). */
export type ValidationError = {
  keyword: string;
  instancePath: string;
  schemaPath: string;
  message: string;
  params: Record<string, unknown>;
};

/**
 * Função de validação no mesmo contrato do `ValidateFunction` do Ajv:
 * chamável que retorna boolean e expõe `.errors` após cada chamada.
 */
export type ValidateFunction = {
  (data: unknown): boolean;
  errors: ValidationError[] | null;
};

/** Interface duck-typed equivalente à parte do Ajv que consumimos. */
export type CspSafeValidator = {
  compile: (schema: JsonSchema) => ValidateFunction;
  validate: (schema: JsonSchema, data: unknown) => boolean;
};

/**
 * Extrai o nome do campo ausente da mensagem de "required" — mantido idêntico
 * ao formato do original para não quebrar consumidores que fazem o parse.
 * Ex.: `Instance does not have required property "description".` -> `"description"`
 */
const REQUIRED_PROPERTY_REGEX = /required property "(.+)"/;

/** Retorna o tipo JSON Schema de um valor concreto. */
function jsonTypeOf(value: unknown): JsonSchemaType {
  if (value === null) {
    return 'null';
  }
  if (Array.isArray(value)) {
    return 'array';
  }
  const nativeType = typeof value;
  if (nativeType === 'number') {
    return Number.isInteger(value as number) ? 'integer' : 'number';
  }
  if (nativeType === 'boolean') {
    return 'boolean';
  }
  if (nativeType === 'string') {
    return 'string';
  }
  // object (não-nulo e não-array) e qualquer outra coisa cai aqui.
  return 'object';
}

/**
 * Verifica se o tipo concreto satisfaz o(s) tipo(s) esperado(s).
 * Regra: um inteiro também satisfaz `number` (como no JSON Schema).
 */
function matchesType(actual: JsonSchemaType, expected: JsonSchemaType[]): boolean {
  if (expected.includes(actual)) {
    return true;
  }
  return actual === 'integer' && expected.includes('number');
}

/** Compara dois valores para a keyword `enum` (igualdade estrutural simples). */
function enumEquals(a: unknown, b: unknown): boolean {
  if (a === b) {
    return true;
  }
  // Comparação estrutural rasa via JSON para objetos/arrays literais de enum.
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}

/**
 * FAIL-CLOSED: um schema malformado (não-objeto) lança erro claro em vez de
 * validar de forma permissiva ou de deixar o WeakMap estourar com mensagem opaca.
 */
function assertSchema(schema: unknown, schemaPath: string): asserts schema is JsonSchema {
  if (schema === null || typeof schema !== 'object' || Array.isArray(schema)) {
    throw new TypeError(
      `Schema inválido em "${schemaPath}": esperado um objeto JSON Schema, recebido ${jsonTypeOf(schema)}.`,
    );
  }
}

/**
 * Percorre o schema recursivamente e acumula os erros encontrados.
 */
function collectErrors(
  schema: JsonSchema,
  data: unknown,
  instancePath: string,
  schemaPath: string,
  errors: ValidationError[],
): void {
  assertSchema(schema, schemaPath);

  const actualType = jsonTypeOf(data);

  // 1) type
  if (schema.type !== undefined) {
    const expected = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!matchesType(actualType, expected)) {
      errors.push({
        keyword: 'type',
        instancePath,
        schemaPath: `${schemaPath}/type`,
        message: `Instance type "${actualType}" is invalid. Expected "${expected.join('", "')}".`,
        params: { type: schema.type },
      });
      // Se o tipo já está errado, não faz sentido descer em properties/items.
      return;
    }
  }

  // 2) enum
  if (Array.isArray(schema.enum)) {
    const allowed = schema.enum;
    if (!allowed.some((candidate) => enumEquals(candidate, data))) {
      errors.push({
        keyword: 'enum',
        instancePath,
        schemaPath: `${schemaPath}/enum`,
        message: 'Instance does not match any of the allowed enum values.',
        params: { allowedValues: allowed },
      });
    }
  }

  // 3) required + properties (apenas para objetos)
  if (actualType === 'object') {
    const record = data as Record<string, unknown>;

    if (Array.isArray(schema.required)) {
      for (const requiredProp of schema.required) {
        if (!Object.prototype.hasOwnProperty.call(record, requiredProp)) {
          errors.push({
            keyword: 'required',
            instancePath,
            schemaPath: `${schemaPath}/required`,
            message: `Instance does not have required property "${requiredProp}".`,
            params: { missingProperty: requiredProp },
          });
        }
      }
    }

    if (schema.properties) {
      for (const [propName, propSchema] of Object.entries(schema.properties)) {
        if (Object.prototype.hasOwnProperty.call(record, propName)) {
          collectErrors(
            propSchema,
            record[propName],
            `${instancePath}/${propName}`,
            `${schemaPath}/properties/${propName}`,
            errors,
          );
        }
      }
    }
  }

  // 4) items (apenas para arrays)
  if (actualType === 'array' && schema.items) {
    (data as unknown[]).forEach((item, index) => {
      collectErrors(
        schema.items as JsonSchema,
        item,
        `${instancePath}/${index}`,
        `${schemaPath}/items`,
        errors,
      );
    });
  }
}

/**
 * Cria uma `ValidateFunction` para um schema, no mesmo contrato do Ajv.
 * A raiz usa instancePath vazio ("") e schemaPath "#", como no Ajv/@cfworker.
 */
function createValidateFunction(schema: JsonSchema): ValidateFunction {
  const validateFn = ((data: unknown): boolean => {
    const errors: ValidationError[] = [];
    collectErrors(schema, data, '', '#', errors);
    validateFn.errors = errors.length === 0 ? null : errors;
    return errors.length === 0;
  }) as ValidateFunction;

  validateFn.errors = null;

  return validateFn;
}

/**
 * Fábrica do validador CSP-safe. Estrutura derivada de
 * `createWorkflowBuilderValidator` do SDK original (cache por referência de
 * schema via WeakMap + guarda contra `undefined`).
 */
export function createCspSafeValidator(): CspSafeValidator {
  const cache = new WeakMap<object, ValidateFunction>();

  return {
    /** Equivalente a `ajv.compile(schema)`. */
    compile(schema: JsonSchema): ValidateFunction {
      // FAIL-CLOSED antes do WeakMap: schema precisa ser objeto para ser chave
      // e para ser interpretável; caso contrário, erro claro em vez de opaco.
      assertSchema(schema, '#');
      let validateFn = cache.get(schema);
      if (!validateFn) {
        validateFn = createValidateFunction(schema);
        cache.set(schema, validateFn);
      }
      return validateFn;
    },

    /**
     * Equivalente a `ajv.validate(schema, data)`.
     * Guarda contra `undefined` retornando `false` (como o Ajv faz), em vez de
     * lançar como faria um interpretador estrito.
     */
    validate(schema: JsonSchema, data: unknown): boolean {
      if (data === undefined) {
        return false;
      }
      return this.compile(schema)(data);
    },
  };
}

/**
 * Instância única do validador CSP-safe.
 * Nome neutro de propósito: a marca do upstream fica só na atribuição, nunca
 * na API exposta ao produto.
 */
export const cspSafeValidator: CspSafeValidator = createCspSafeValidator();

// Reexportado apenas para consumidores que ainda fazem parse da mensagem de
// "required" (paridade com o original). Novo código deve ler `params.missingProperty`.
export { REQUIRED_PROPERTY_REGEX };
