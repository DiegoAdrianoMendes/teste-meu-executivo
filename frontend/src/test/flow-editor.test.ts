import { describe, expect, it } from 'vitest';

import {
  normalizeFlowEdges,
  normalizeFlowNodes,
  serializeExecutableFlowGraph,
  serializeFlowEdges,
  serializeFlowNodes,
} from '@/lib/flow-editor';

describe('flow editor normalization', () => {
  it('normalizes legacy api nodes that only have config', () => {
    const [node] = normalizeFlowNodes([
      {
        id: 'agent-1',
        type: 'agent',
        label: 'SDR customizado',
        position: { x: 120, y: 80 },
        config: {
          agent: 'klm_sdr',
          prompt: 'Seja consultivo',
          model: 'gemini-2.5-flash-lite',
          context_mode: 'full',
          use_rag: true,
          human_review_required: true,
        },
      },
    ]);

    expect(node.data.label).toBe('SDR customizado');
    expect(node.data.prompt).toBe('Seja consultivo');
    expect(node.data.context_mode).toBe('full');
    expect(node.data.human_review_required).toBe(true);
  });

  it('serializes editor nodes back to config format for the backend', () => {
    const [node] = normalizeFlowNodes([
      {
        id: 'signature-1',
        type: 'zapsign_request',
        position: { x: 0, y: 0 },
        data: {
          label: 'Assinatura premium',
          provider: 'docusign',
          doc_type: 'contrato_honorarios',
          auth_mode: 'tokenSms',
          integration_notes: 'Validar email antes de disparar',
        },
      },
    ]);

    const [serialized] = serializeFlowNodes([node]);
    expect(serialized).toMatchObject({
      id: 'signature-1',
      type: 'zapsign_request',
      label: 'Assinatura premium',
      config: {
        provider: 'docusign',
        doc_type: 'contrato_honorarios',
        auth_mode: 'tokenSms',
        integration_notes: 'Validar email antes de disparar',
      },
    });
  });

  it('keeps edge condition metadata when saving', () => {
    const [edge] = normalizeFlowEdges([
      {
        id: 'edge-1',
        source: 'condition-1',
        target: 'agent-1',
        condition_value: 'true',
      },
    ]);

    const [serialized] = serializeFlowEdges([edge]);
    expect(serialized).toMatchObject({
      id: 'edge-1',
      source: 'condition-1',
      target: 'agent-1',
      condition_value: 'true',
      sourceHandle: 'true',
    });
  });

  it('derives condition metadata from the React Flow source handle', () => {
    const [serialized] = serializeFlowEdges([{
      id: 'edge-false',
      source: 'condition-1',
      sourceHandle: 'false',
      target: 'wait-1',
    }]);

    expect(serialized).toMatchObject({
      sourceHandle: 'false',
      condition_value: 'false',
      label: 'Não',
    });
  });

  it('round trips source and target handles without changing the branch', () => {
    const [edge] = normalizeFlowEdges([{
      id: 'edge-roundtrip',
      source: 'condition-1',
      source_handle: 'true',
      target: 'agent-1',
      target_handle: 'input',
      condition_value: 'true',
    }]);

    expect(serializeFlowEdges([edge])[0]).toMatchObject({
      sourceHandle: 'true',
      targetHandle: 'input',
      condition_value: 'true',
    });
  });

  it('round trips dynamic router routes and fallback metadata', () => {
    const [route, fallback] = normalizeFlowEdges([
      {
        id: 'edge-qualified',
        source: 'router-1',
        target: 'agent-1',
        route_key: 'qualificado',
        priority: 10,
      },
      {
        id: 'edge-fallback',
        source: 'router-1',
        target: 'human-1',
        route_key: 'fallback',
        is_fallback: true,
      },
    ]);

    expect(route.sourceHandle).toBe('qualificado');
    expect(fallback.sourceHandle).toBe('fallback');
    expect(serializeFlowEdges([route, fallback])).toEqual(expect.arrayContaining([
      expect.objectContaining({ route_key: 'qualificado', sourceHandle: 'qualificado', priority: 10 }),
      expect.objectContaining({ route_key: 'fallback', sourceHandle: 'fallback', is_fallback: true }),
    ]));
  });

  it('preserves social profile and case filters without inventing follower data', () => {
    const [node] = normalizeFlowNodes([{
      id: 'social-trigger-1',
      type: 'trigger',
      label: 'Comentário qualificado',
      position: { x: 40, y: 80 },
      config: {
        trigger_type: 'keyword_comment',
        keywords: ['Medicamento'],
        excluded_keywords: ['emprego'],
        keyword_mode: 'contains',
        match_mode: 'all',
        case_sensitive: true,
        first_level_only: true,
        follower_status: 'not_follows',
        verified_status: 'any',
        messaging_window: 'open',
        data_availability_policy: 'fail_closed',
      },
    }]);

    expect(serializeFlowNodes([node])[0]).toMatchObject({
      type: 'trigger',
      config: {
        event: 'keyword_comment',
        trigger_type: 'keyword_comment',
        keywords: ['Medicamento'],
        excluded_keywords: ['emprego'],
        case_sensitive: true,
        follower_status: 'not_follows',
        messaging_window: 'open',
        data_availability_policy: 'fail_closed',
      },
    });
  });

  it('serializes a sequence into executable scheduled stages with cumulative offsets', () => {
    const nodes = normalizeFlowNodes([
      { id: 'trigger-1', type: 'trigger', position: { x: 0, y: 0 }, config: { trigger_type: 'manual' } },
      {
        id: 'sequence-1', type: 'send_message', label: 'Cadência', position: { x: 200, y: 0 }, data: {
          label: 'Cadência', is_sequence: true, sequence_stage: true, interrupt_on_reply: true,
          sequence: [
            { id: 'message-1', templateId: 'tpl-1', templateName: 'aviso', templateLanguage: 'pt_BR', body: 'Olá {{1}}', status: 'approved', bindings: [{ fieldKey: 'contact.name', value: 'Ana' }] },
            { id: 'message-2', templateId: 'tpl-2', templateName: 'followup', templateLanguage: 'pt_BR', body: 'Retorno {{1}}', status: 'approved', delayValue: 24, delayUnit: 'hours', bindings: [{ fieldKey: 'contact.name', value: 'Ana' }] },
            { id: 'message-3', templateId: 'tpl-3', templateName: 'ultimo', templateLanguage: 'pt_BR', body: 'Último {{1}}', status: 'approved', delayValue: 24, delayUnit: 'hours', bindings: [{ fieldKey: 'contact.name', value: 'Ana' }] },
          ],
        },
      },
      { id: 'end-1', type: 'end', position: { x: 500, y: 0 }, data: { label: 'Fim' } },
    ]);
    const edges = normalizeFlowEdges([
      { id: 'edge-in', source: 'trigger-1', target: 'sequence-1' },
      { id: 'edge-out', source: 'sequence-1', target: 'end-1' },
    ]);
    const now = new Date('2026-08-19T12:00:00.000Z');
    const payload = serializeExecutableFlowGraph(nodes, edges, now);
    const stages = payload.nodes.filter((node) => node.type === 'scheduled_message');

    expect(stages.map((node) => node.id)).toEqual(['sequence-1-sequence-1', 'sequence-1-sequence-2', 'sequence-1-sequence-3']);
    expect(stages[0]).toMatchObject({ config: { send_now: true, message: 'Olá {sequence_1}', interrupt_on_reply: true, bindings: [{ variable: 'sequence_1', fieldKey: 'contact.name' }] } });
    expect(stages[1]).toMatchObject({ config: { delay_value: 24, delay_unit: 'hours' } });
    expect(stages[2]).toMatchObject({ config: { delay_value: 48, delay_unit: 'hours' } });
    expect((stages[0].config as Record<string, unknown>).bindings).not.toEqual(expect.arrayContaining([expect.objectContaining({ value: 'Ana' })]));
    expect(payload.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: 'trigger-1', target: 'sequence-1-sequence-1' }),
      expect.objectContaining({ source: 'sequence-1-sequence-3', target: 'end-1' }),
      expect.objectContaining({ source: 'sequence-1-sequence-1', target: 'sequence-1-sequence-2' }),
    ]));
  });

  it('keeps executable scheduled stages stable across normalize and save', () => {
    const inputNodes = normalizeFlowNodes([{
      id: 'scheduled-1', type: 'scheduled_message', label: 'Follow-up', position: { x: 10, y: 20 }, config: {
        sequence_stage: true, message: 'Retorno {{1}}', template_name: 'followup', template_id: 'tpl-2',
        template_status: 'approved', template_language: 'pt_BR', bindings: [{ variable: 'sequence_1', fieldKey: 'contact.name' }],
        delay_value: 48, delay_unit: 'hours', interrupt_on_reply: true,
      },
    }]);
    const first = serializeExecutableFlowGraph(inputNodes, [], new Date('2026-08-19T12:00:00.000Z'));
    const second = serializeExecutableFlowGraph(normalizeFlowNodes(first.nodes), normalizeFlowEdges(first.edges), new Date('2026-08-19T12:00:00.000Z'));
    expect(second).toEqual(first);
    expect(second.nodes[0]).toMatchObject({ id: 'scheduled-1', label: 'Follow-up', config: { delay_value: 48, delay_unit: 'hours' } });
  });
});
