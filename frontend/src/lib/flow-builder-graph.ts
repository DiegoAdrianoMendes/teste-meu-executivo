import type { Edge, Node } from '@xyflow/react';

import type { FlowEditorNodeData } from '@/lib/flow-editor';

export type FlowGraphIssueSeverity = 'error' | 'warning';

export type FlowGraphIssue = {
  code: string;
  severity: FlowGraphIssueSeverity;
  title: string;
  description: string;
  nodeId?: string;
  edgeId?: string;
};

export type FlowGraphValidation = {
  issues: FlowGraphIssue[];
  errors: FlowGraphIssue[];
  warnings: FlowGraphIssue[];
  canActivate: boolean;
  reachableNodeIds: string[];
};

export type FlowGraphSnapshot = {
  nodes: Array<Node<FlowEditorNodeData>>;
  edges: Edge[];
};

const SCHEDULE_TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

function cloneValue<T>(value: T): T {
  if (typeof structuredClone === 'function') return structuredClone(value);
  return JSON.parse(JSON.stringify(value)) as T;
}

export function cloneFlowGraph(
  nodes: Array<Node<FlowEditorNodeData>>,
  edges: Edge[],
): FlowGraphSnapshot {
  return { nodes: cloneValue(nodes), edges: cloneValue(edges) };
}

export function flowGraphFingerprint(snapshot: FlowGraphSnapshot): string {
  return JSON.stringify({
    nodes: snapshot.nodes.map((node) => ({
      id: node.id,
      type: node.type,
      position: node.position,
      data: node.data,
    })),
    edges: snapshot.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle,
      targetHandle: edge.targetHandle,
      label: edge.label,
      conditionValue: (edge as Edge & { condition_value?: string }).condition_value,
    })),
  });
}

export function appendFlowHistory(
  history: FlowGraphSnapshot[],
  snapshot: FlowGraphSnapshot,
  limit = 50,
): FlowGraphSnapshot[] {
  const previous = history.at(-1);
  if (previous && flowGraphFingerprint(previous) === flowGraphFingerprint(snapshot)) return history;
  return [...history, cloneFlowGraph(snapshot.nodes, snapshot.edges)].slice(-limit);
}

function nodeLabel(node: Node<FlowEditorNodeData>): string {
  return String(node.data?.label || node.id);
}

function conditionBranch(edge: Edge): string | null {
  const typedEdge = edge as Edge & { condition_value?: string; route_key?: string };
  const raw = typedEdge.route_key || typedEdge.condition_value || edge.sourceHandle || edge.label;
  const normalized = String(raw || '').trim().toLowerCase();
  if (['true', 'sim', 'yes', '1'].includes(normalized)) return 'true';
  if (['false', 'não', 'nao', 'no', '0'].includes(normalized)) return 'false';
  return normalized || null;
}

function graphHasReachableCycle(startId: string, outgoing: Map<string, Edge[]>): boolean {
  const visited = new Set<string>();
  const active = new Set<string>();

  const visit = (nodeId: string): boolean => {
    if (active.has(nodeId)) return true;
    if (visited.has(nodeId)) return false;
    visited.add(nodeId);
    active.add(nodeId);
    for (const edge of outgoing.get(nodeId) ?? []) {
      if (visit(edge.target)) return true;
    }
    active.delete(nodeId);
    return false;
  };

  return visit(startId);
}

export function validateFlowGraph(
  nodes: Array<Node<FlowEditorNodeData>>,
  edges: Edge[],
): FlowGraphValidation {
  const issues: FlowGraphIssue[] = [];
  const nodeById = new Map<string, Node<FlowEditorNodeData>>();
  const nodeIds = new Set<string>();

  if (!nodes.length) {
    issues.push({
      code: 'empty_graph',
      severity: 'error',
      title: 'Fluxo sem etapas',
      description: 'Adicione um gatilho e ao menos uma etapa operacional.',
    });
  }

  for (const node of nodes) {
    if (nodeIds.has(node.id)) {
      issues.push({
        code: 'duplicate_node_id',
        severity: 'error',
        title: 'Identificador de etapa duplicado',
        description: `A etapa ${node.id} aparece mais de uma vez.`,
        nodeId: node.id,
      });
    }
    nodeIds.add(node.id);
    nodeById.set(node.id, node);
  }

  const outgoing = new Map<string, Edge[]>();
  const incoming = new Map<string, Edge[]>();
  const edgeIds = new Set<string>();
  const routes = new Set<string>();

  for (const edge of edges) {
    if (edgeIds.has(edge.id)) {
      issues.push({
        code: 'duplicate_edge_id',
        severity: 'error',
        title: 'Conexão duplicada',
        description: `A conexão ${edge.id} possui um identificador repetido.`,
        edgeId: edge.id,
      });
    }
    edgeIds.add(edge.id);

    if (!nodeById.has(edge.source) || !nodeById.has(edge.target)) {
      issues.push({
        code: 'orphan_edge',
        severity: 'error',
        title: 'Conexão órfã',
        description: 'A conexão aponta para uma etapa que não existe mais.',
        edgeId: edge.id,
      });
      continue;
    }
    if (edge.source === edge.target) {
      issues.push({
        code: 'self_loop',
        severity: 'warning',
        title: 'Etapa conectada a ela mesma',
        description: `“${nodeLabel(nodeById.get(edge.source)!)}” depende do limite seguro de passos para interromper o loop.`,
        nodeId: edge.source,
        edgeId: edge.id,
      });
    }

    const routeKey = `${edge.source}:${edge.target}:${conditionBranch(edge) ?? ''}`;
    if (routes.has(routeKey)) {
      issues.push({
        code: 'duplicate_route',
        severity: 'error',
        title: 'Rota repetida',
        description: 'Duas conexões levam à mesma etapa usando a mesma saída.',
        edgeId: edge.id,
        nodeId: edge.source,
      });
    }
    routes.add(routeKey);
    outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge]);
    incoming.set(edge.target, [...(incoming.get(edge.target) ?? []), edge]);
  }

  const triggers = nodes.filter((node) => node.type === 'trigger');
  if (triggers.length === 0 && nodes.length) {
    issues.push({
      code: 'missing_trigger',
      severity: 'error',
      title: 'Gatilho ausente',
      description: 'Todo fluxo ativado precisa começar por um gatilho explícito.',
    });
  } else if (triggers.length > 1) {
    issues.push({
      code: 'multiple_triggers',
      severity: 'error',
      title: 'Mais de um gatilho',
      description: 'Mantenha um único ponto de entrada por fluxo para uma execução determinística.',
    });
  }
  if (nodes.length === 1 && triggers.length === 1) {
    issues.push({
      code: 'trigger_only',
      severity: 'error',
      title: 'Fluxo sem ação',
      description: 'Conecte ao gatilho ao menos uma mensagem, condição, agente ou etapa operacional.',
      nodeId: triggers[0].id,
    });
  }

  const root = triggers[0] ?? nodes[0];
  if (root && (incoming.get(root.id)?.length ?? 0) > 0) {
    issues.push({
      code: 'trigger_has_incoming',
      severity: 'error',
      title: 'Gatilho recebe uma conexão',
      description: 'O ponto de entrada não pode depender de uma etapa anterior.',
      nodeId: root.id,
    });
  }
  if (root && nodes.length > 1 && (outgoing.get(root.id)?.length ?? 0) === 0) {
    issues.push({
      code: 'trigger_without_route',
      severity: 'error',
      title: 'Gatilho sem próximo passo',
      description: 'Conecte o gatilho à primeira etapa do atendimento.',
      nodeId: root.id,
    });
  }

  for (const node of nodes) {
    const data = node.data ?? { label: node.id };
    const routesFromNode = outgoing.get(node.id) ?? [];
    const label = nodeLabel(node);

    if (node.type === 'condition') {
      if (!String(data.field ?? '').trim()) {
        issues.push({
          code: 'condition_missing_field',
          severity: 'error',
          title: 'Condição sem campo',
          description: `Defina qual dado será avaliado em “${label}”.`,
          nodeId: node.id,
        });
      }
      const branches = new Set(routesFromNode.map(conditionBranch));
      if (!branches.has('true') || !branches.has('false')) {
        issues.push({
          code: 'condition_missing_branch',
          severity: 'error',
          title: 'Condição incompleta',
          description: `“${label}” precisa das saídas Sim e Não.`,
          nodeId: node.id,
        });
      }
    } else if (node.type === 'router') {
      const configuredRoutes = Array.isArray(data.routes)
        ? data.routes.filter((route) => Boolean(route && typeof route === 'object'))
        : [];
      const routeKeys = new Set(configuredRoutes.map((route) => String(route.key ?? '')).filter(Boolean));
      const connectedKeys = new Set(routesFromNode.map(conditionBranch).filter(Boolean));
      for (const routeKey of routeKeys) {
        if (!connectedKeys.has(routeKey)) {
          issues.push({ code: 'router_route_unconnected', severity: 'error', title: 'Rota sem conexão', description: `A saída “${routeKey}” de “${label}” ainda não leva a nenhuma etapa.`, nodeId: node.id });
        }
      }
      if (!routeKeys.has(String(data.fallback_route_key ?? 'fallback'))) {
        issues.push({ code: 'router_missing_fallback', severity: 'error', title: 'Roteador sem fallback', description: `“${label}” precisa de uma saída para casos não previstos.`, nodeId: node.id });
      }
    } else if (node.type === 'loop') {
      const branches = new Set(routesFromNode.map(conditionBranch));
      if (!branches.has('loop') || !branches.has('done')) {
        issues.push({ code: 'loop_missing_branch', severity: 'error', title: 'Iterador incompleto', description: `“${label}” precisa das saídas Iterar e Concluído.`, nodeId: node.id });
      }
    } else if (routesFromNode.length > 1) {
      issues.push({
        code: 'ambiguous_route',
        severity: 'warning',
        title: 'Saída ambígua',
        description: `“${label}” possui mais de uma saída; o runtime seguirá a primeira rota persistida.`,
        nodeId: node.id,
      });
    }

    if ((node.type === 'send_message' || node.type === 'scheduled_message') && !String(data.message ?? '').trim()) {
      issues.push({
        code: 'message_missing_body',
        severity: 'error',
        title: 'Mensagem sem conteúdo',
        description: `Escreva o texto que será enviado em “${label}”.`,
        nodeId: node.id,
      });
    }
    if (node.type === 'scheduled_message' && !SCHEDULE_TIME.test(String(data.scheduled_at ?? ''))) {
      issues.push({
        code: 'invalid_schedule_time',
        severity: 'error',
        title: 'Horário inválido',
        description: `Use HH:MM entre 00:00 e 23:59 em “${label}”.`,
        nodeId: node.id,
      });
    }
    if (node.type === 'wait' && (!(Number(data.delay_hours) > 0) || !Number.isFinite(Number(data.delay_hours)))) {
      issues.push({
        code: 'invalid_wait',
        severity: 'error',
        title: 'Espera inválida',
        description: `Informe um intervalo maior que zero em “${label}”.`,
        nodeId: node.id,
      });
    }
    if (node.type === 'human_pause' && !String(data.reason ?? '').trim()) {
      issues.push({
        code: 'human_pause_missing_reason',
        severity: 'error',
        title: 'Pausa sem instrução',
        description: `Explique o que o humano precisa decidir em “${label}”.`,
        nodeId: node.id,
      });
    }
    if (node.type === 'agent' && !String(data.agent ?? '').trim()) {
      issues.push({
        code: 'agent_missing',
        severity: 'error',
        title: 'Agente não definido',
        description: `Escolha o agente responsável por “${label}”.`,
        nodeId: node.id,
      });
    }
    if (node.type === 'zapsign_request' && !String(data.doc_type ?? '').trim()) {
      issues.push({
        code: 'signature_document_missing',
        severity: 'error',
        title: 'Documento de assinatura ausente',
        description: `Escolha o documento solicitado em “${label}”.`,
        nodeId: node.id,
      });
    }
    if (node.type === 'link_action' && !String(data.url ?? '').trim()) {
      issues.push({ code: 'link_missing_url', severity: 'error', title: 'Link sem destino', description: `Informe o endereço em “${label}”.`, nodeId: node.id });
    }
    if (node.type === 'create_task' && !String(data.task_title ?? '').trim()) {
      issues.push({ code: 'task_missing_title', severity: 'error', title: 'Tarefa sem título', description: `Informe o título da tarefa em “${label}”.`, nodeId: node.id });
    }
    if (node.type === 'connector_action' && (!String(data.connector ?? '').trim() || !String(data.operation ?? '').trim())) {
      issues.push({ code: 'connector_incomplete', severity: 'error', title: 'Conector incompleto', description: `Escolha o conector e a operação em “${label}”.`, nodeId: node.id });
    }
  }

  const reachable = new Set<string>();
  if (root) {
    const queue = [root.id];
    while (queue.length) {
      const nodeId = queue.shift()!;
      if (reachable.has(nodeId)) continue;
      reachable.add(nodeId);
      for (const edge of outgoing.get(nodeId) ?? []) queue.push(edge.target);
    }
  }

  for (const node of nodes) {
    if (!reachable.has(node.id)) {
      issues.push({
        code: 'unreachable_node',
        severity: 'error',
        title: 'Etapa fora do fluxo',
        description: `“${nodeLabel(node)}” não pode ser alcançada a partir do gatilho.`,
        nodeId: node.id,
      });
    }
  }

  if (root && graphHasReachableCycle(root.id, outgoing)) {
    issues.push({
      code: 'reachable_cycle',
      severity: 'warning',
      title: 'Loop detectado',
      description: 'O fluxo contém um ciclo. Confirme a condição de saída; o runtime mantém um limite seguro de etapas.',
      nodeId: root.id,
    });
  }

  const errors = issues.filter((issue) => issue.severity === 'error');
  const warnings = issues.filter((issue) => issue.severity === 'warning');
  return {
    issues,
    errors,
    warnings,
    canActivate: errors.length === 0,
    reachableNodeIds: [...reachable],
  };
}

export function autoArrangeFlowGraph(
  nodes: Array<Node<FlowEditorNodeData>>,
  edges: Edge[],
): Array<Node<FlowEditorNodeData>> {
  if (nodes.length < 2) return cloneValue(nodes);

  const nodeIds = new Set(nodes.map((node) => node.id));
  const incomingCount = new Map(nodes.map((node) => [node.id, 0]));
  const outgoing = new Map<string, string[]>();
  for (const edge of edges) {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) continue;
    incomingCount.set(edge.target, (incomingCount.get(edge.target) ?? 0) + 1);
    outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge.target]);
  }

  const triggerIds = nodes.filter((node) => node.type === 'trigger').map((node) => node.id);
  const rootIds = [
    ...triggerIds,
    ...nodes.filter((node) => !triggerIds.includes(node.id) && (incomingCount.get(node.id) ?? 0) === 0).map((node) => node.id),
  ];
  if (!rootIds.length) rootIds.push(nodes[0].id);

  const levelById = new Map<string, number>();
  const queue = rootIds.map((id) => ({ id, level: 0 }));
  while (queue.length) {
    const { id, level } = queue.shift()!;
    const known = levelById.get(id);
    if (known !== undefined && known >= level) continue;
    levelById.set(id, level);
    for (const target of outgoing.get(id) ?? []) {
      if (target !== id) queue.push({ id: target, level: Math.min(level + 1, nodes.length) });
    }
  }

  let overflowLevel = Math.max(0, ...levelById.values()) + 1;
  for (const node of nodes) {
    if (!levelById.has(node.id)) levelById.set(node.id, overflowLevel++);
  }

  const levels = new Map<number, string[]>();
  for (const node of nodes) {
    const level = levelById.get(node.id) ?? 0;
    levels.set(level, [...(levels.get(level) ?? []), node.id]);
  }

  const positionById = new Map<string, { x: number; y: number }>();
  const horizontalGap = 250;
  const verticalGap = 175;
  const centerX = 360;
  for (const [level, ids] of [...levels.entries()].sort(([left], [right]) => left - right)) {
    const rowWidth = Math.max(0, ids.length - 1) * horizontalGap;
    ids.forEach((id, index) => {
      positionById.set(id, {
        x: centerX - rowWidth / 2 + index * horizontalGap,
        y: 70 + level * verticalGap,
      });
    });
  }

  return nodes.map((node) => ({ ...cloneValue(node), position: positionById.get(node.id) ?? node.position }));
}
