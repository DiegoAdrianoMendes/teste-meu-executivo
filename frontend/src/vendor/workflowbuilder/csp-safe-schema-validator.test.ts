import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  cspSafeValidator,
  getGraphErrors,
  getNodeErrors,
  type FlowNodeLike,
  type JsonSchema,
  type SchemaByNodeType,
} from './index';

const HERE = dirname(fileURLToPath(import.meta.url));

/**
 * Schema do nó 'condition' do KLM Flow (subconjunto): exige uma `expression`
 * não vazia e um operador dentre os permitidos.
 */
const conditionSchema: JsonSchema = {
  type: 'object',
  properties: {
    label: { type: 'string' },
    expression: { type: 'string' },
    operator: { type: 'string', enum: ['equals', 'notEquals', 'contains'] },
  },
  required: ['expression', 'operator'],
};

describe('csp-safe schema validator — dependency-free', () => {
  it('não importa nenhum pacote externo (prova estática de dependency-free)', () => {
    // Se o validador puxasse @cfworker/json-schema ou ajv (não instalados neste
    // frontend), o próprio import acima já teria falhado. Aqui reforçamos por
    // inspeção estática: todo import dos módulos vendored é relativo.
    const sources = ['csp-safe-schema-validator.ts', 'get-node-errors.ts', 'index.ts'].map(
      (file) => readFileSync(join(HERE, file), 'utf8'),
    );

    const importSpecifier = /(?:import|export)[^;]*?from\s+['"]([^'"]+)['"]/g;
    for (const source of sources) {
      for (const match of source.matchAll(importSpecifier)) {
        const specifier = match[1];
        expect(
          specifier.startsWith('.'),
          `import externo proibido no módulo vendored: "${specifier}"`,
        ).toBe(true);
      }
    }
  });

  it('não usa eval nem new Function (CSP-safe por construção)', () => {
    const raw = readFileSync(join(HERE, 'csp-safe-schema-validator.ts'), 'utf8');
    // Remove comentários (bloco e linha) antes de procurar chamadas reais — o
    // texto explicativo do arquivo cita "new Function()" só descritivamente.
    const code = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
    expect(/\beval\s*\(/.test(code)).toBe(false);
    expect(/new\s+Function\s*\(/.test(code)).toBe(false);
  });
});

describe('csp-safe schema validator — contrato de erro', () => {
  it('popula params.missingProperty e a mensagem de required (paridade com o upstream)', () => {
    const schema: JsonSchema = {
      type: 'object',
      required: ['name'],
      properties: { name: { type: 'string' } },
    };
    const validate = cspSafeValidator.compile(schema);
    const isValid = validate({});

    expect(isValid).toBe(false);
    expect(validate.errors).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          keyword: 'required',
          instancePath: '',
          schemaPath: '#/required',
          message: 'Instance does not have required property "name".',
          params: { missingProperty: 'name' },
        }),
      ]),
    );
  });

  it('retorna false para data undefined sem lançar', () => {
    expect(cspSafeValidator.validate({ type: 'object' }, undefined)).toBe(false);
  });

  it('aceita dados válidos', () => {
    const validate = cspSafeValidator.compile(conditionSchema);
    expect(validate({ expression: 'valor > 10', operator: 'equals' })).toBe(true);
    expect(validate.errors).toBeNull();
  });

  it('FAIL-CLOSED: schema malformado lança erro claro em vez de validar permissivo', () => {
    expect(() => cspSafeValidator.compile(null)({})).toThrow(/Schema inválido/);
  });
});

describe('detecção de grafo inválido', () => {
  const schemaByType: SchemaByNodeType = { condition: conditionSchema };

  it('detecta um grafo simples com um nó condition inválido', () => {
    const nodes: FlowNodeLike[] = [
      // válido
      {
        id: 'cond-ok',
        data: { type: 'condition', properties: { expression: 'x == 1', operator: 'equals' } },
      },
      // inválido: falta `expression` e operador fora do enum
      {
        id: 'cond-bad',
        data: { type: 'condition', properties: { operator: 'maior_que' } },
      },
    ];

    const graphErrors = getGraphErrors(nodes, schemaByType);

    // Apenas o nó inválido aparece.
    expect(graphErrors).toHaveLength(1);
    expect(graphErrors[0].nodeId).toBe('cond-bad');

    const keywords = graphErrors[0].errors.map((error) => error.keyword);
    expect(keywords).toContain('required'); // expression ausente
    expect(keywords).toContain('enum'); // operador inválido
  });

  it('getNodeErrors retorna [] para nó sem schema (definição desconhecida)', () => {
    const node: FlowNodeLike = { id: 'x', data: { type: 'desconhecido', properties: {} } };
    expect(getNodeErrors(node, undefined)).toEqual([]);
  });
});
