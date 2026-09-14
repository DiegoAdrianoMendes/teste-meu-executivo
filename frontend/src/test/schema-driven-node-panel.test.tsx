import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { JsonSchema, UISchemaElement } from '@jsonforms/core';

import { SchemaDrivenNodePanel } from '@/components/flow/SchemaDrivenNodePanel';

// Espelha o shape REAL que a Tarefa A expõe no catálogo do runtime para o nó
// `condition` (app/services/flow_node_registry.py -> GET /api/v1/flows/catalog):
// {schema, uischema} co-localizados, incluindo a regra HIDE do campo "value"
// para operadores unários (condição baseada em enum).
const conditionSchema = {
  $schema: 'http://json-schema.org/draft-07/schema#',
  type: 'object',
  title: 'Condição',
  description: 'Decisão binária por campo e operador tipado.',
  properties: {
    field: {
      type: 'string',
      title: 'Campo',
      description: 'Caminho no contexto da execução a avaliar (ex.: lead.status).',
      minLength: 1,
    },
    operator: {
      type: 'string',
      title: 'Operador',
      description: 'Comparação aplicada ao campo.',
      enum: ['eq', 'contains', 'gt', 'gte', 'lt', 'lte', 'exists', 'not_empty'],
      default: 'eq',
    },
    value: {
      type: 'string',
      title: 'Valor de comparação',
      description: "Valor comparado ao campo. Ignorado por 'exists' e 'not_empty'.",
    },
  },
  required: ['field', 'operator'],
  additionalProperties: true,
} as unknown as JsonSchema;

const conditionUischema = {
  type: 'VerticalLayout',
  elements: [
    { type: 'Control', scope: '#/properties/field', options: { placeholder: 'lead.status' } },
    { type: 'Control', scope: '#/properties/operator' },
    {
      type: 'Control',
      scope: '#/properties/value',
      rule: {
        effect: 'HIDE',
        condition: {
          scope: '#/properties/operator',
          schema: { enum: ['exists', 'not_empty'] },
        },
      },
    },
  ],
} as unknown as UISchemaElement;

describe('SchemaDrivenNodePanel (nó condition)', () => {
  it('renderiza limites numéricos e gates booleanos como controles guiados', async () => {
    const onChange = vi.fn();
    const runtimeSchema = {
      type: 'object',
      properties: {
        max_hops: { type: 'integer', title: 'Máximo de escaladas', minimum: 1, maximum: 8 },
        external_gate: { type: 'boolean', title: 'Exigir gate externo' },
      },
    } as unknown as JsonSchema;
    const runtimeUi = {
      type: 'VerticalLayout',
      elements: [
        { type: 'Control', scope: '#/properties/max_hops' },
        { type: 'Control', scope: '#/properties/external_gate' },
      ],
    } as unknown as UISchemaElement;

    render(<SchemaDrivenNodePanel schema={runtimeSchema} uischema={runtimeUi} data={{ max_hops: 4, external_gate: false }} onChange={onChange} />);

    const hops = screen.getByTestId('schema-field-max_hops');
    expect(hops).toHaveAttribute('type', 'number');
    expect(hops).toHaveAttribute('min', '1');
    expect(hops).toHaveAttribute('max', '8');
    expect(screen.getByTestId('schema-field-external_gate')).toHaveAttribute('role', 'switch');

    fireEvent.change(hops, { target: { value: '6' } });
    await waitFor(() => expect(onChange.mock.calls.at(-1)?.[0]).toMatchObject({ max_hops: 6 }));
    fireEvent.click(screen.getByTestId('schema-field-external_gate'));
    await waitFor(() => expect(onChange.mock.calls.at(-1)?.[0]).toMatchObject({ external_gate: true }));
  });

  it('renderiza o formulário a partir do schema do catálogo', () => {
    render(
      <SchemaDrivenNodePanel
        schema={conditionSchema}
        uischema={conditionUischema}
        data={{ field: 'lead.status', operator: 'eq', value: 'novo' }}
        onChange={() => {}}
      />,
    );

    expect(screen.getByTestId('schema-driven-node-panel')).toBeInTheDocument();
    // Controles derivados do schema/uischema do catálogo.
    expect(screen.getByTestId('schema-field-field')).toBeInTheDocument();
    expect(screen.getByTestId('schema-field-operator')).toBeInTheDocument();
    // Label derivado do título do schema (escala canônica: label text-sm) e
    // associado ao controle; valor atual populado.
    expect(screen.getByLabelText(/Campo/)).toHaveValue('lead.status');
    // Placeholder vem do uischema (options.placeholder).
    expect(screen.getByPlaceholderText('lead.status')).toBeInTheDocument();
  });

  it('dispara o save (onChange) com o payload atualizado ao editar o campo', async () => {
    const onChange = vi.fn();
    render(
      <SchemaDrivenNodePanel
        schema={conditionSchema}
        uischema={conditionUischema}
        data={{ field: 'lead.status', operator: 'eq', value: 'novo' }}
        onChange={onChange}
      />,
    );

    fireEvent.change(screen.getByTestId('schema-field-field'), {
      target: { value: 'lead.score' },
    });

    // O JsonForms faz debounce (~10ms) do onChange de topo; aguardamos o emit.
    await waitFor(() => {
      const lastPayload = onChange.mock.calls.at(-1)?.[0];
      expect(lastPayload).toMatchObject({ field: 'lead.score', operator: 'eq' });
    });
  });

  it('avalia a regra HIDE via validador CSP-safe: value visível só para operador binário', () => {
    const { rerender } = render(
      <SchemaDrivenNodePanel
        schema={conditionSchema}
        uischema={conditionUischema}
        data={{ field: 'lead.status', operator: 'eq', value: 'novo' }}
        onChange={() => {}}
      />,
    );
    // operador binário 'eq' -> campo "Valor de comparação" visível.
    expect(screen.getByText('Valor de comparação', { exact: false })).toBeInTheDocument();

    // operador unário 'exists' -> regra HIDE oculta o campo "value".
    rerender(
      <SchemaDrivenNodePanel
        schema={conditionSchema}
        uischema={conditionUischema}
        data={{ field: 'lead.status', operator: 'exists' }}
        onChange={() => {}}
      />,
    );
    expect(screen.queryByText('Valor de comparação', { exact: false })).not.toBeInTheDocument();
  });
});
