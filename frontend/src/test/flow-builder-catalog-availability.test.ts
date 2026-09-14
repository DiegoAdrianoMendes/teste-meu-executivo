/** @vitest-environment jsdom */
import { describe, expect, it } from 'vitest';

import { paletteItemFromCatalog } from '@/pages/FlowBuilder';
import type { KlmFlowNodeCatalogEntry } from '@/klm/api';

function catalogNode(overrides: Partial<KlmFlowNodeCatalogEntry> = {}): KlmFlowNodeCatalogEntry {
  return {
    type: 'runtime_action',
    label: 'Ação do runtime',
    description: 'Contrato autoritativo do runtime.',
    category: 'Ferramentas internas',
    execution_mode: 'native',
    side_effect: 'local',
    input_ports: [{ key: 'input', label: 'Entrada' }],
    output_ports: [{ key: 'output', label: 'Próximo' }],
    supports_dry_run: true,
    supports_error_route: true,
    config_version: 3,
    schema: { type: 'object', properties: { title: { type: 'string' } } },
    uischema: {},
    outputSchema: { type: 'object' },
    availability: 'available',
    ...overrides,
  };
}

describe('FlowBuilder runtime catalog availability', () => {
  it('allows authoring every available schema-driven runtime node, including unknown future types', () => {
    const item = paletteItemFromCatalog(catalogNode({ type: 'future_native_operation' }));

    expect(item.runtimeAvailable).toBe(true);
    expect(item.authorable).toBe(true);
    expect(item.defaultData.label).toBe('Ação do runtime');
  });

  it('never makes an unavailable capability authorable, even when the backend exposes its schema for explanation', () => {
    const item = paletteItemFromCatalog(catalogNode({
      type: 'finance_payment',
      availability: 'unavailable',
      availabilityReason: 'Provedor financeiro não configurado.',
    }));

    expect(item.runtimeAvailable).toBe(false);
    expect(item.authorable).toBe(false);
    expect(item.availabilityReason).toBe('Provedor financeiro não configurado.');
  });

  it('does not invent a form for an available capability that has no backend authoring contract', () => {
    const item = paletteItemFromCatalog(catalogNode({
      type: 'future_untyped_operation',
      schema: undefined,
      uischema: undefined,
      outputSchema: undefined,
    }));

    expect(item.runtimeAvailable).toBe(true);
    expect(item.authorable).toBe(false);
  });
});
