import type { Edge, Node } from '@xyflow/react';
import { describe, expect, it } from 'vitest';

import {
  appendFlowHistory,
  autoArrangeFlowGraph,
  cloneFlowGraph,
  validateFlowGraph,
} from '@/lib/flow-builder-graph';
import type { FlowEditorNodeData } from '@/lib/flow-editor';

function node(
  id: string,
  type: string,
  data: Partial<FlowEditorNodeData> = {},
): Node<FlowEditorNodeData> {
  return {
    id,
    type,
    position: { x: 0, y: 0 },
    data: { label: id, ...data },
  };
}

function edge(id: string, source: string, target: string, branch?: 'true' | 'false'): Edge {
  return {
    id,
    source,
    target,
    sourceHandle: branch,
    label: branch === 'true' ? 'Sim' : branch === 'false' ? 'Não' : undefined,
  };
}

describe('flow builder graph validation', () => {
  it('does not allow activation with only a trigger', () => {
    const result = validateFlowGraph([node('trigger', 'trigger')], []);

    expect(result.canActivate).toBe(false);
    expect(result.errors.some((issue) => issue.code === 'trigger_only')).toBe(true);
  });

  it('accepts a complete conditional flow', () => {
    const nodes = [
      node('trigger', 'trigger'),
      node('condition', 'condition', { field: 'lead_score', operator: 'gte', value: '70' }),
      node('yes', 'send_message', { message: 'Vamos avançar.' }),
      node('no', 'human_pause', { reason: 'Revisar caso manualmente.' }),
    ];
    const edges = [
      edge('e1', 'trigger', 'condition'),
      edge('e2', 'condition', 'yes', 'true'),
      edge('e3', 'condition', 'no', 'false'),
    ];

    const result = validateFlowGraph(nodes, edges);

    expect(result.canActivate).toBe(true);
    expect(result.errors).toEqual([]);
    expect(result.reachableNodeIds).toHaveLength(4);
  });

  it('blocks activation when a branch and a message body are missing', () => {
    const nodes = [
      node('trigger', 'trigger'),
      node('condition', 'condition', { field: 'doc_score', operator: 'gte', value: '80' }),
      node('message', 'send_message'),
    ];
    const edges = [edge('e1', 'trigger', 'condition'), edge('e2', 'condition', 'message', 'true')];

    const result = validateFlowGraph(nodes, edges);

    expect(result.canActivate).toBe(false);
    expect(result.errors.map((issue) => issue.code)).toEqual(expect.arrayContaining([
      'condition_missing_branch',
      'message_missing_body',
    ]));
  });

  it('finds disconnected nodes and orphan edges', () => {
    const nodes = [node('trigger', 'trigger'), node('connected', 'wait', { delay_hours: 2 }), node('loose', 'agent', { agent: 'klm_sdr' })];
    const edges = [edge('e1', 'trigger', 'connected'), edge('e2', 'missing', 'connected')];

    const result = validateFlowGraph(nodes, edges);

    expect(result.errors.map((issue) => issue.code)).toEqual(expect.arrayContaining(['orphan_edge', 'unreachable_node']));
  });

  it('warns about loops while preserving a valid graph', () => {
    const nodes = [node('trigger', 'trigger'), node('wait', 'wait', { delay_hours: 1 })];
    const edges = [edge('e1', 'trigger', 'wait'), edge('e2', 'wait', 'trigger')];

    const result = validateFlowGraph(nodes, edges);

    expect(result.warnings.some((issue) => issue.code === 'reachable_cycle')).toBe(true);
    expect(result.errors.some((issue) => issue.code === 'trigger_has_incoming')).toBe(true);
  });
});

describe('flow builder graph history and layout', () => {
  it('stores detached snapshots and deduplicates identical checkpoints', () => {
    const nodes = [node('trigger', 'trigger')];
    const snapshot = cloneFlowGraph(nodes, []);
    const once = appendFlowHistory([], snapshot);
    const twice = appendFlowHistory(once, snapshot);

    nodes[0].position.x = 999;

    expect(twice).toHaveLength(1);
    expect(twice[0].nodes[0].position.x).toBe(0);
  });

  it('arranges connected nodes from top to bottom and branches side by side', () => {
    const nodes = [
      node('trigger', 'trigger'),
      node('condition', 'condition', { field: 'lead_score' }),
      node('yes', 'send_message', { message: 'Sim' }),
      node('no', 'human_pause', { reason: 'Não' }),
    ];
    const edges = [
      edge('e1', 'trigger', 'condition'),
      edge('e2', 'condition', 'yes', 'true'),
      edge('e3', 'condition', 'no', 'false'),
    ];

    const arranged = autoArrangeFlowGraph(nodes, edges);
    const positions = Object.fromEntries(arranged.map((item) => [item.id, item.position]));

    expect(positions.condition.y).toBeGreaterThan(positions.trigger.y);
    expect(positions.yes.y).toBeGreaterThan(positions.condition.y);
    expect(positions.no.y).toBe(positions.yes.y);
    expect(positions.no.x).not.toBe(positions.yes.x);
  });
});
