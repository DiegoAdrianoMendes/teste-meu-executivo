import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Background,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeTypes,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Box, CheckCircle2, CirclePause, Copy, Link2, LoaderCircle, PencilLine, Play, Plus, RotateCcw, Save, Search, Settings2, ShieldAlert, Trash2, Undo2, UploadCloud } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from '@/components/ui/context-menu';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { NodeConfigPanel } from '@/components/flow/NodeConfigPanel';
import { SchemaDrivenNodePanel } from '@/components/flow/SchemaDrivenNodePanel';
import type { JsonSchema, UISchemaElement } from '@jsonforms/core';
import { AgentNode } from '@/components/flow/nodes/AgentNode';
import { ConditionNode } from '@/components/flow/nodes/ConditionNode';
import { HumanPauseNode } from '@/components/flow/nodes/HumanPauseNode';
import { MessageNode } from '@/components/flow/nodes/MessageNode';
import { ScheduledMessageNode } from '@/components/flow/nodes/ScheduledMessageNode';
import { StudioNode } from '@/components/flow/nodes/StudioNode';
import { TriggerNode } from '@/components/flow/nodes/TriggerNode';
import { WaitNode } from '@/components/flow/nodes/WaitNode';
import { ZapSignNode } from '@/components/flow/nodes/ZapSignNode';
import { KlmPanel, KlmPill, KlmProgress, KlmSectionTitle } from '@/klm/components/Primitives';
import { NativeAgentOperationsPanel } from '@/klm/components/NativeAgentOperationsPanel';
import { NativeFlowRuntimePanel } from '@/klm/components/NativeFlowRuntimePanel';
import { NativeModuleBar } from '@/klm/components/NativeModuleBar';
import { FlowCopilot } from '@/klm/components/FlowCopilot';
import { EffectivePlaybookLink } from '@/klm/components/playbooks/EffectivePlaybookLink';
import {
  getDefaultNodeData,
  normalizeFlowEdges,
  serializeFlowEdges,
  type FlowEditorNodeData,
} from '@/lib/flow-editor';
import { cn } from '@/lib/utils';
import { klmApi, type KlmFlow, type KlmFlowCopilotPlan } from '@/klm/api';
import { ApiError } from '@/services/http';
import { useAppStore } from '@/store/app-context';

type FlowNodeData = {
  title: string;
  category: string;
  description: string;
  nodeType?: string;
  config?: Record<string, unknown>;
  status?: 'success' | 'running' | 'queued' | 'retry' | 'skipped' | 'idle';
};

type FlowBlockDefinition = {
  title: string;
  category: string;
  nodeType: string;
  description: string;
  config: Record<string, unknown>;
};

type FlowTemplateDefinition = {
  name: string;
  blocks: string[];
};

const richNodeTypes: NodeTypes = {
  trigger: TriggerNode,
  send_message: MessageNode,
  wait: WaitNode,
  condition: ConditionNode,
  action: StudioNode,
  end: StudioNode,
  form: StudioNode,
  llm: StudioNode,
  human_transfer: StudioNode,
  landing_page: StudioNode,
  router: StudioNode,
  merge: StudioNode,
  loop: StudioNode,
  error_handler: StudioNode,
  set_variable: StudioNode,
  transform: StudioNode,
  aggregate: StudioNode,
  link_action: StudioNode,
  human_pause: HumanPauseNode,
  agent: AgentNode,
  create_task: StudioNode,
  update_card: StudioNode,
  internal_note: StudioNode,
  audit_event: StudioNode,
  payment_request: StudioNode,
  social_action: StudioNode,
  connector_action: StudioNode,
  zapsign_request: ZapSignNode,
  scheduled_message: ScheduledMessageNode,
};

function socialTriggerConfig(
  triggerType: string,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    ...getDefaultNodeData('trigger'),
    trigger_type: triggerType,
    event: triggerType,
    data_availability_policy: 'fail_closed',
    ...overrides,
  };
}

const blockGroups: Array<{ group: string; blocks: FlowBlockDefinition[] }> = [
  {
    group: 'Gatilho',
    blocks: [
      { title: 'Novo lead', category: 'Gatilho', nodeType: 'trigger', description: 'Inicia ao criar um lead qualificado.', config: { event: 'new_lead' } },
      { title: 'Mensagem recebida', category: 'Gatilho', nodeType: 'trigger', description: 'Inicia com mensagem em um canal conectado.', config: { event: 'message_received' } },
      { title: 'Documento recebido', category: 'Gatilho', nodeType: 'trigger', description: 'Inicia quando um documento entra no caso.', config: { event: 'document_received' } },
    ],
  },
  {
    group: 'Gatilhos sociais',
    blocks: [
      { title: 'Comentário com palavra-chave', category: 'Gatilho social', nodeType: 'trigger', description: 'Inicia por comentário elegível e aplica palavras, exclusões, publicação e perfil.', config: socialTriggerConfig('keyword_comment') },
      { title: 'Comentário em post ou reel', category: 'Gatilho social', nodeType: 'trigger', description: 'Inicia por comentário em qualquer publicação ou em IDs selecionados.', config: socialTriggerConfig('post_comment') },
      { title: 'Comentário no Facebook', category: 'Gatilho social', nodeType: 'trigger', description: 'Inicia por comentário de Página e preserva a origem da publicação.', config: socialTriggerConfig('page_comment') },
      { title: 'Mensagem direta social', category: 'Gatilho social', nodeType: 'trigger', description: 'Inicia após uma DM oficial de Instagram, Messenger ou TikTok elegível.', config: socialTriggerConfig('direct_message') },
      { title: 'Resposta ao story', category: 'Gatilho social', nodeType: 'trigger', description: 'Inicia por resposta ou reação a um story.', config: socialTriggerConfig('story_reply') },
      { title: 'Menção no story', category: 'Gatilho social', nodeType: 'trigger', description: 'Inicia quando a conta profissional é mencionada em story.', config: socialTriggerConfig('story_mention') },
      { title: 'Lead de anúncio ou formulário', category: 'Aquisição', nodeType: 'trigger', description: 'Inicia por Lead Ads, formulário social ou formulário do site normalizado.', config: socialTriggerConfig('lead_form') },
      { title: 'Novo seguidor elegível', category: 'Gatilho social', nodeType: 'trigger', description: 'Só inicia se a conexão declarar um evento oficial de novo seguidor.', config: socialTriggerConfig('new_follower', { required_capability: 'new_follower_webhook' }) },
    ],
  },
  {
    group: 'Condição',
    blocks: [
      { title: 'Subtema', category: 'Condição', nodeType: 'condition', description: 'Compara o subtema jurídico classificado.', config: { field: 'health_subtheme', operator: 'eq', value: 'medicamento_alto_custo' } },
      { title: 'Nota', category: 'Condição', nodeType: 'condition', description: 'Compara a nota mínima de qualificação.', config: { field: 'lead_score', operator: 'gt', value: '60' } },
      { title: 'Docs OK', category: 'Condição', nodeType: 'condition', description: 'Verifica completude documental antes de avançar.', config: { field: 'doc_score', operator: 'gt', value: '87' } },
    ],
  },
  {
    group: 'Controle do fluxo',
    blocks: [
      { title: 'Roteador por regras', category: 'Controle', nodeType: 'router', description: 'Cria múltiplas rotas ordenadas com fallback obrigatório.', config: getDefaultNodeData('router') },
      { title: 'Unir rotas', category: 'Controle', nodeType: 'merge', description: 'Reconverge caminhos mutuamente exclusivos.', config: getDefaultNodeData('merge') },
      { title: 'Percorrer lista', category: 'Controle', nodeType: 'loop', description: 'Itera uma coleção com limite explícito.', config: getDefaultNodeData('loop') },
      { title: 'Tratar falha', category: 'Controle', nodeType: 'error_handler', description: 'Recebe uma rota de erro e registra a recuperação.', config: getDefaultNodeData('error_handler') },
      { title: 'Encerrar fluxo', category: 'Controle', nodeType: 'end', description: 'Finaliza o fluxo com motivo auditável.', config: getDefaultNodeData('end') },
    ],
  },
  {
    group: 'Dados',
    blocks: [
      { title: 'Definir variável', category: 'Dados', nodeType: 'set_variable', description: 'Grava um valor tipado no contexto do flow.', config: getDefaultNodeData('set_variable') },
      { title: 'Mapear dados', category: 'Dados', nodeType: 'transform', description: 'Transforma campos usando placeholders seguros.', config: getDefaultNodeData('transform') },
      { title: 'Agregar lista', category: 'Dados', nodeType: 'aggregate', description: 'Conta, soma ou resume coleções do contexto.', config: getDefaultNodeData('aggregate') },
      { title: 'Formulário', category: 'Dados', nodeType: 'form', description: 'Coleta dados com campos tipados e submissão rastreável.', config: getDefaultNodeData('form') },
    ],
  },
  {
    group: 'Agente',
    blocks: [
      { title: 'SDR', category: 'Agente', nodeType: 'agent', description: 'Triagem, qualificação e coleta documental.', config: { agent: 'klm_sdr', stage_key: 'triagem_sdr', use_rag: true, human_review_required: false } },
      { title: 'Jurídico', category: 'Agente', nodeType: 'agent', description: 'Analisa negativa, urgência, documentos e viabilidade.', config: { agent: 'klm_analyst', stage_key: 'analise_juridica', use_rag: true, human_review_required: true } },
      { title: 'Closer', category: 'Agente', nodeType: 'agent', description: 'Prepara proposta e encaminha decisão ao humano.', config: { agent: 'assistente_juridico', stage_key: 'fechamento', use_rag: true, human_review_required: true } },
      { title: 'Modelo de IA', category: 'Agente', nodeType: 'llm', description: 'Executa prompt com contexto/RAG e governança do workspace.', config: getDefaultNodeData('llm') },
    ],
  },
  {
    group: 'Ação',
    blocks: [
      { title: 'Enviar mensagem', category: 'Ação', nodeType: 'send_message', description: 'Envia mensagem pelo canal atual após os gates.', config: { message: 'Olá {nome}, seguimos acompanhando seu caso.' } },
      { title: 'Atualizar CRM', category: 'Ação', nodeType: 'update_card', description: 'Qualifica, etiqueta e atualiza campos permitidos do Caso 360.', config: { updates: { next_action: 'Revisar próximo passo' } } },
      { title: 'Agendar reunião', category: 'Ação', nodeType: 'link_action', description: 'Libera a agenda vinculada ao caso para escolha de horário.', config: { link_kind: 'booking', url: '/app/agenda', link_title: 'Escolher horário', target: 'customer', open_in_new_tab: true } },
      { title: 'Mensagem agendada', category: 'Ação', nodeType: 'scheduled_message', description: 'Agenda mensagem com horário e entrega auditável.', config: { message: 'Olá {nome}, este é um lembrete do seu atendimento.', scheduled_at: '09:00' } },
      { title: 'Ação externa', category: 'Ação', nodeType: 'action', description: 'Prepara conector externo com gate de credencial e aprovação.', config: getDefaultNodeData('action') },
    ],
  },
  {
    group: 'Ferramentas internas',
    blocks: [
      { title: 'Criar tarefa', category: 'Ferramenta interna', nodeType: 'create_task', description: 'Cria tarefa idempotente vinculada ao Caso 360.', config: getDefaultNodeData('create_task') },
      { title: 'Nota invisível', category: 'Ferramenta interna', nodeType: 'internal_note', description: 'Registra nota interna que nunca é enviada ao cliente.', config: getDefaultNodeData('internal_note') },
      { title: 'Registrar auditoria', category: 'Observabilidade', nodeType: 'audit_event', description: 'Grava checkpoint pesquisável no histórico.', config: getDefaultNodeData('audit_event') },
      { title: 'Transferir para humano', category: 'Governança', nodeType: 'human_transfer', description: 'Entrega a uma equipe ou fila e pausa o fluxo.', config: getDefaultNodeData('human_transfer') },
    ],
  },
  {
    group: 'Conversão',
    blocks: [
      { title: 'Abrir proposta', category: 'Conversão', nodeType: 'link_action', description: 'Libera link rastreável para a proposta do caso.', config: { link_kind: 'proposal', url: '/app/propostas', link_title: 'Ver proposta', target: 'customer', open_in_new_tab: true } },
      { title: 'Abrir checkout', category: 'Conversão', nodeType: 'link_action', description: 'Libera checkout ou página de compra aprovada.', config: { link_kind: 'checkout', url: '/checkout', link_title: 'Contratar agora', target: 'customer', open_in_new_tab: true } },
      { title: 'Página de destino', category: 'Conversão', nodeType: 'landing_page', description: 'Libera uma página/CTA rastreável sem mudar de canal.', config: getDefaultNodeData('landing_page') },
      { title: 'Solicitar pagamento', category: 'Conversão', nodeType: 'payment_request', description: 'Prepara Pix, boleto ou cartão com gate externo.', config: getDefaultNodeData('payment_request') },
      { title: 'Solicitar assinatura', category: 'Jurídico', nodeType: 'zapsign_request', description: 'Prepara contrato ou procuração para assinatura.', config: getDefaultNodeData('zapsign_request') },
    ],
  },
  {
    group: 'Social',
    blocks: [
      { title: 'Responder comentário', category: 'Social', nodeType: 'social_action', description: 'Prepara resposta pública em comentário elegível.', config: { ...getDefaultNodeData('social_action'), social_action: 'public_reply' } },
      { title: 'Enviar resposta privada', category: 'Social', nodeType: 'social_action', description: 'Prepara DM privada a partir da interação.', config: { ...getDefaultNodeData('social_action'), social_action: 'private_reply' } },
      { title: 'Curtir comentário', category: 'Social', nodeType: 'social_action', description: 'Prepara curtida oficial no comentário.', config: { social_action: 'comment_like', target_external_id: '{{social.eventId}}' } },
      { title: 'Ocultar comentário', category: 'Social', nodeType: 'social_action', description: 'Oculta comentário quando a capacidade oficial permitir.', config: { social_action: 'comment_hide', target_external_id: '{{social.eventId}}' } },
    ],
  },
  {
    group: 'Conectores',
    blocks: [
      { title: 'Webhook seguro', category: 'Integração', nodeType: 'connector_action', description: 'Prepara chamada HTTP tipada com gate e auditoria.', config: { connector: 'webhook', operation: 'post', input_mapping: {}, output_mapping: {}, timeout_seconds: 30 } },
      { title: 'Ferramenta MCP', category: 'Integração', nodeType: 'connector_action', description: 'Aciona uma ferramenta MCP permitida pelo workspace.', config: { connector: 'mcp', operation: 'invoke', input_mapping: {}, output_mapping: {}, timeout_seconds: 60 } },
      { title: 'API interna', category: 'Integração', nodeType: 'connector_action', description: 'Aciona operação interna registrada, sem expor segredo no grafo.', config: { connector: 'klm_internal', operation: 'invoke', input_mapping: {}, output_mapping: {}, timeout_seconds: 30 } },
    ],
  },
  {
    group: 'Espera',
    blocks: [
      { title: 'Aguardar prazo de resposta', category: 'Espera', nodeType: 'wait', description: 'Retoma depois do intervalo configurado.', config: { delay_hours: 18, condition: 'no_response' } },
      { title: 'Horário programado', category: 'Espera', nodeType: 'scheduled_message', description: 'Agenda a continuação para um horário local.', config: { message: 'Olá {nome}, seguimos com seu atendimento.', scheduled_at: '09:00' } },
      { title: 'Pausa humana (legado)', category: 'Aprovação humana', nodeType: 'human_pause', description: 'Compatibilidade com fluxos antigos; novos fluxos devem usar a aprovação humana governada.', config: { reason: 'Revisar o próximo passo antes de prosseguir.' } },
    ],
  },
];

const blocksByTitle = new Map(
  blockGroups.flatMap((group) => group.blocks).map((block) => [block.title, block]),
);

const localFlowTemplates: FlowTemplateDefinition[] = [
  { name: 'Triagem inicial', blocks: ['Novo lead', 'SDR', 'Subtema', 'Jurídico', 'Pausa humana (legado)'] },
  { name: 'Docs pendentes', blocks: ['Documento recebido', 'Docs OK', 'Atualizar CRM', 'Aguardar prazo de resposta', 'Enviar mensagem'] },
  { name: 'No-show', blocks: ['Horário programado', 'Agendar reunião', 'Aguardar prazo de resposta', 'Enviar mensagem', 'Pausa humana (legado)'] },
  { name: 'Infoproduto R$ 19,00', blocks: ['Novo lead', 'Nota', 'Pausa humana (legado)', 'Enviar mensagem'] },
];

const categoryByType: Record<string, string> = {
  trigger: 'Gatilho',
  agent: 'Agente',
  condition: 'Condição',
  router: 'Controle',
  merge: 'Controle',
  loop: 'Controle',
  error_handler: 'Controle',
  set_variable: 'Dados',
  transform: 'Dados',
  aggregate: 'Dados',
  scheduled_message: 'Espera',
  wait: 'Espera',
  human_pause: 'Aprovação humana',
  zapsign_request: 'Ação',
  send_message: 'Ação',
  link_action: 'Conversão',
  create_task: 'Ferramenta interna',
  update_card: 'Ferramenta interna',
  internal_note: 'Ferramenta interna',
  audit_event: 'Observabilidade',
  payment_request: 'Conversão',
  social_action: 'Social',
  connector_action: 'Integração',
};

function nodesFromFlow(flow: KlmFlow): Node<FlowNodeData>[] {
  const normalizeLegacyRow = flow.nodes.some((node) => {
    const position = typeof node.position === 'object' && node.position ? node.position as Record<string, unknown> : {};
    return Number(position.x ?? 0) > 900;
  });
  return flow.nodes.map((rawNode, index) => {
    const data = typeof rawNode.data === 'object' && rawNode.data ? rawNode.data as Record<string, unknown> : {};
    const config = typeof rawNode.config === 'object' && rawNode.config ? rawNode.config as Record<string, unknown> : {};
    const position = typeof rawNode.position === 'object' && rawNode.position ? rawNode.position as Record<string, unknown> : {};
    const nodeType = String(rawNode.type ?? 'action');
    return {
      id: String(rawNode.id ?? `node-${index + 1}`),
      position: normalizeLegacyRow
        ? { x: 40 + (index % 3) * 240, y: 104 + Math.floor(index / 3) * 168 }
        : { x: Number(position.x ?? 80 + index * 220), y: Number(position.y ?? 180) },
      data: {
        title: String(data.label ?? rawNode.label ?? `Etapa ${index + 1}`),
        category: String(data.category ?? categoryByType[nodeType] ?? 'Ação'),
        description: String(data.description ?? (nodeType === 'human_pause' ? 'aguarda decisão humana registrada' : nodeType === 'agent' ? 'rota API-first com simulação local disponível' : 'contrato tipado e versionado')),
        nodeType,
        config,
        status: 'idle',
      },
      style: { width: normalizeLegacyRow ? 145 : 160 },
    };
  });
}

function edgesFromFlow(flow: KlmFlow): Edge[] {
  return normalizeFlowEdges(flow.edges).map((edge) => ({
    ...edge,
    label: edge.label ? String(edge.label).toUpperCase() : undefined,
    type: 'smoothstep',
    style: { stroke: '#0066F7', strokeWidth: 1.5 },
    labelStyle: { fontSize: 10, fontWeight: 600 },
    animated: false,
  }));
}

function serializeCanvasNodes(nodes: Node<FlowNodeData>[]): Array<Record<string, unknown>> {
  return nodes.map((node) => ({
    id: node.id,
    type: node.data.nodeType ?? (
      node.data.category === 'Agente'
        ? 'agent'
        : node.data.category === 'Condição'
          ? 'condition'
          : node.data.category === 'Gatilho'
            ? 'trigger'
            : 'human_pause'
    ),
    position: node.position,
    label: node.data.title,
    config: node.data.config ?? {},
    data: {
      label: node.data.title,
      category: node.data.category,
      description: node.data.description,
    },
  }));
}

function nextOpenGridPosition(nodes: Node<FlowNodeData>[]): { x: number; y: number } {
  const columns = 3;
  for (let index = 0; index < Math.max(18, nodes.length + 6); index += 1) {
    const candidate = {
      x: 60 + (index % columns) * 240,
      y: 112 + Math.floor(index / columns) * 168,
    };
    const occupied = nodes.some((node) => (
      Math.abs(node.position.x - candidate.x) < 120
      && Math.abs(node.position.y - candidate.y) < 84
    ));
    if (!occupied) return candidate;
  }
  return { x: 60, y: 112 + Math.ceil(nodes.length / columns) * 168 };
}

function FlowCanvas({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onConnect,
  onNodeClick,
  onNodeDoubleClick,
  onDuplicateNode,
  onDeleteNode,
  onDropNode,
  runningNodeId,
  flowReady,
}: {
  nodes: Node<FlowNodeData>[];
  edges: Edge[];
  onNodesChange: (changes: NodeChange<Node<FlowNodeData>>[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  onConnect: (connection: Connection) => void;
  onNodeClick: (node: Node<FlowNodeData>) => void;
  onNodeDoubleClick: (node: Node<FlowNodeData>) => void;
  onDuplicateNode: (nodeId: string) => void;
  onDeleteNode: (nodeId: string) => void;
  onDropNode: (block: FlowBlockDefinition, position: { x: number; y: number }) => void;
  runningNodeId: string | null;
  flowReady: boolean;
}) {
  const { fitView, screenToFlowPosition } = useReactFlow();
  const compactCanvas = typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches;
  const [contextNodeId, setContextNodeId] = useState<string | null>(null);
  const visibleNodes = useMemo(() => nodes.map((node) => ({
    ...node,
    type: node.data.nodeType ?? 'human_pause',
    className: cn('klm-flow-node', node.id === runningNodeId && 'klm-flow-node-running'),
    style: {
      ...node.style,
      width: undefined,
      borderRadius: 8,
      boxShadow: node.id === runningNodeId ? '0 0 0 6px rgba(26,153,72,.16)' : undefined,
    },
    data: {
      ...node.data,
      ...getDefaultNodeData(node.data.nodeType ?? 'human_pause'),
      ...(node.data.config ?? {}),
      label: node.data.title,
    },
  })), [nodes, runningNodeId]);

  const visibleEdges = useMemo(() => edges.map((edge) => ({
    ...edge,
    animated: runningNodeId != null && edge.target === runningNodeId,
    style: { ...edge.style, stroke: runningNodeId != null && edge.target === runningNodeId ? '#1A9948' : '#0066F7' },
  })), [edges, runningNodeId]);

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const serializedBlock = event.dataTransfer.getData('application/klm-flow-block');
    if (!serializedBlock) return;
    try {
      onDropNode(JSON.parse(serializedBlock) as FlowBlockDefinition, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
    } catch {
      return;
    }
  };

  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <div
          className="h-full w-full"
          onDrop={handleDrop}
          onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }}
        >
          <ReactFlow
            nodes={visibleNodes}
            edges={visibleEdges}
            nodeTypes={richNodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_, node) => onNodeClick(node)}
            onNodeDoubleClick={(_, node) => onNodeDoubleClick(node)}
            onNodeContextMenu={(_, node) => setContextNodeId(node.id)}
            onPaneContextMenu={() => setContextNodeId(null)}
            fitView={!compactCanvas}
            defaultViewport={compactCanvas ? { x: 24, y: 24, zoom: 0.82 } : undefined}
            fitViewOptions={{ padding: 0.22 }}
            minZoom={compactCanvas ? 0.7 : 0.45}
            maxZoom={1.6}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#DCE7F2" gap={24} size={1} />
            <Controls position="bottom-left" showInteractive={false} />
          </ReactFlow>
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent className="w-52">
        {contextNodeId ? (
          <>
            <ContextMenuLabel className="truncate text-2xs">
              {nodes.find((node) => node.id === contextNodeId)?.data.title ?? 'Etapa'}
            </ContextMenuLabel>
            <ContextMenuItem className="text-xs" onSelect={() => {
              const node = nodes.find((item) => item.id === contextNodeId);
              if (node) onNodeDoubleClick(node);
            }}>
              <PencilLine className="mr-2 h-3.5 w-3.5" />
              Abrir configuração
            </ContextMenuItem>
            <ContextMenuItem className="text-xs" disabled={!flowReady} onSelect={() => onDuplicateNode(contextNodeId)}>
              <Copy className="mr-2 h-3.5 w-3.5" />
              Duplicar etapa
            </ContextMenuItem>
            <ContextMenuSeparator />
            <ContextMenuItem className="text-xs text-destructive focus:text-destructive" disabled={!flowReady || nodes.length <= 1} onSelect={() => onDeleteNode(contextNodeId)}>
              <Trash2 className="mr-2 h-3.5 w-3.5" />
              Excluir etapa
            </ContextMenuItem>
          </>
        ) : (
          <>
            <ContextMenuLabel className="text-2xs">Canvas do fluxo</ContextMenuLabel>
            <ContextMenuItem className="text-xs" onSelect={() => void fitView({ padding: 0.22, duration: 220 })}>
              <Box className="mr-2 h-3.5 w-3.5" />
              Ajustar fluxo à tela
            </ContextMenuItem>
          </>
        )}
      </ContextMenuContent>
    </ContextMenu>
  );
}

export default function FlowsPage() {
  const { can, state } = useAppStore();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const flowsQuery = useQuery({ queryKey: ['klm', 'flows'], queryFn: klmApi.flows });
  const flowTemplatesQuery = useQuery({ queryKey: ['klm', 'flows', 'templates'], queryFn: klmApi.flowTemplates });
  const flowCatalogQuery = useQuery({ queryKey: ['klm', 'flows', 'catalog'], queryFn: klmApi.flowNodeCatalog });
  const funnelQuery = useQuery({ queryKey: ['klm', 'funnel'], queryFn: klmApi.funnel });
  const requestedMatterId = searchParams.get('matter');
  const requestedThreadId = searchParams.get('thread');
  const threadsQuery = useQuery({ queryKey: ['klm', 'threads'], queryFn: klmApi.threads, enabled: Boolean(requestedThreadId) });
  const [nodes, setNodes] = useState<Node<FlowNodeData>[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [running, setRunning] = useState(false);
  const [runIndex, setRunIndex] = useState(-1);
  const [dryRunSequence, setDryRunSequence] = useState<string[]>([]);
  const [dryRunStatus, setDryRunStatus] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isDirty, setIsDirty] = useState(false);
  const [blockSearch, setBlockSearch] = useState('');
  const suppressPaletteClickUntil = useRef(0);
  const [loadedFlowId, setLoadedFlowId] = useState<string | null>(null);
  const [activeFlowId, setActiveFlowId] = useState<string | null>(() => searchParams.get('flow'));
  const requestedThread = threadsQuery.data?.find((thread) => thread.id === requestedThreadId);
  const contextMatterId = requestedMatterId ?? requestedThread?.matterId ?? null;
  const contextIsConsistent = !requestedThreadId
    || Boolean(requestedThread && (!requestedMatterId || requestedThread.matterId === requestedMatterId));
  const selectedMatter = contextIsConsistent
    ? funnelQuery.data?.cards.find((card) => card.id === contextMatterId)
    : undefined;
  const activeFlow = flowsQuery.data?.find((flow) => flow.id === activeFlowId) ?? flowsQuery.data?.[0];
  const flowReady = Boolean(activeFlow && loadedFlowId === activeFlow.id);
  const runSequence = useMemo(
    () => dryRunSequence.length ? dryRunSequence : nodes.map((node) => node.id),
    [dryRunSequence, nodes],
  );
  const runningNodeId = running && runIndex >= 0 ? runSequence[runIndex] : null;
  const selected = nodes.find((node) => node.id === selectedId) ?? nodes[0];
  const selectedEditorNode = useMemo<Node<FlowEditorNodeData> | null>(() => {
    if (!selected) return null;
    const nodeType = selected.data.nodeType ?? 'human_pause';
    const defaults = getDefaultNodeData(nodeType);
    const config = selected.data.config ?? {};
    return {
      id: selected.id,
      type: nodeType,
      position: selected.position,
      data: {
        ...defaults,
        ...config,
        label: selected.data.title,
        ...(nodeType === 'trigger'
          ? { trigger_type: String(config.trigger_type ?? config.event ?? defaults.trigger_type ?? 'new_lead') }
          : {}),
        ...(nodeType === 'scheduled_message'
          ? { scheduled_at: String(config.scheduled_at ?? config.scheduled_time ?? defaults.scheduled_at ?? '09:00') }
          : {}),
      },
    };
  }, [selected]);
  const paletteGroups = useMemo(() => {
    const catalogByType = new Map((flowCatalogQuery.data?.nodes ?? []).map((item) => [item.type, item]));
    const represented = new Set(blockGroups.flatMap((group) => group.blocks.map((block) => block.nodeType)));
    const groups = blockGroups.map((group) => ({
      ...group,
      blocks: group.blocks.map((block) => ({
        ...block,
        description: catalogByType.get(block.nodeType)?.description ?? block.description,
      })),
    }));
    for (const item of flowCatalogQuery.data?.nodes ?? []) {
      if (represented.has(item.type)) continue;
      const target = groups.find((group) => group.group === item.category);
      const block = {
        title: item.label,
        category: item.category,
        nodeType: item.type,
        description: item.description,
        config: getDefaultNodeData(item.type),
      };
      if (target) target.blocks.push(block);
      else groups.push({ group: item.category, blocks: [block] });
    }
    const query = blockSearch.trim().toLocaleLowerCase('pt-BR');
    if (!query) return groups;
    return groups
      .map((group) => ({
        ...group,
        blocks: group.blocks.filter((block) => (
          `${block.title} ${block.description} ${block.category}`
            .toLocaleLowerCase('pt-BR')
            .includes(query)
        )),
      }))
      .filter((group) => group.blocks.length > 0);
  }, [blockSearch, flowCatalogQuery.data]);
  const selectedStage = typeof selected?.data.config?.stage_key === 'string' ? selected.data.config.stage_key : undefined;
  const selectedAgent = typeof selected?.data.config?.agent === 'string' ? selected.data.config.agent : undefined;
  const serializedNodes = useMemo(() => serializeCanvasNodes(nodes), [nodes]);
  const serializedEdges = useMemo(() => serializeFlowEdges(edges), [edges]);

  useEffect(() => {
    const flows = flowsQuery.data ?? [];
    if (!flows.length) return;
    const requested = searchParams.get('flow');
    const nextId = flows.some((flow) => flow.id === requested)
      ? requested
      : flows.some((flow) => flow.id === activeFlowId)
        ? activeFlowId
        : flows[0].id;
    if (!nextId || nextId === activeFlowId) return;
    setActiveFlowId(nextId);
  }, [activeFlowId, flowsQuery.data, searchParams]);

  useEffect(() => {
    if (!activeFlow || loadedFlowId === activeFlow.id) return;
    const persistedNodes = nodesFromFlow(activeFlow);
    setNodes(persistedNodes);
    setEdges(edgesFromFlow(activeFlow));
    setSelectedId(persistedNodes[0]?.id ?? '');
    setLoadedFlowId(activeFlow.id);
    setDryRunSequence([]);
    setDryRunStatus(null);
    setIsDirty(false);
  }, [activeFlow, loadedFlowId]);

  useEffect(() => {
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!isDirty) return;
      event.preventDefault();
    };
    window.addEventListener('beforeunload', warnBeforeUnload);
    return () => window.removeEventListener('beforeunload', warnBeforeUnload);
  }, [isDirty]);

  useEffect(() => {
    const protectDraftNavigation = (event: MouseEvent) => {
      if (!isDirty || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const anchor = (event.target as Element | null)?.closest('a[href]') as HTMLAnchorElement | null;
      if (!anchor || anchor.target === '_blank') return;
      const destination = new URL(anchor.href, window.location.href);
      if (destination.origin !== window.location.origin) return;
      if (window.confirm('Há alterações não salvas neste fluxo. Deseja sair e manter o rascunho somente nesta tela?')) return;
      event.preventDefault();
      event.stopPropagation();
    };
    document.addEventListener('click', protectDraftNavigation, true);
    return () => document.removeEventListener('click', protectDraftNavigation, true);
  }, [isDirty]);

  const dryRunMutation = useMutation({
    mutationFn: ({ flowId, matterId, threadId }: { flowId: string; matterId: string; threadId?: string }) => (
      klmApi.dryRunFlow(flowId, matterId, threadId ? { thread_id: threadId } : {})
    ),
    onSuccess: (result) => {
      setDryRunSequence(result.trace.map((step) => step.nodeId));
      setDryRunStatus(result.status);
      setRunIndex(0);
      setRunning(result.trace.length > 0);
      if (!result.trace.length) setNotice('O dry-run terminou sem etapas executáveis. Revise o gatilho e as conexões.');
    },
    onError: () => setNotice('O runtime local recusou o dry-run. Nenhuma execução ou ação externa foi criada.'),
  });

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!activeFlow) throw new Error('Flow local indisponível');
      return klmApi.updateFlow(activeFlow.id, {
        expected_revision: activeFlow.current_revision,
        nodes: serializedNodes,
        edges: serializedEdges,
      });
    },
    onSuccess: (flow) => {
      queryClient.setQueryData<KlmFlow[]>(['klm', 'flows'], (current = []) => current.map((item) => item.id === flow.id ? flow : item));
      setIsDirty(false);
      setDryRunSequence([]);
      setDryRunStatus(null);
      setNotice('Rascunho salvo no registry local. Nenhum worker, agente ou conector foi acionado.');
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.status === 409) {
        await queryClient.invalidateQueries({ queryKey: ['klm', 'flows'] });
        setNotice('Este fluxo foi alterado por outro editor. O rascunho foi mantido; revise a versão mais recente antes de salvar novamente.');
        return;
      }
      setNotice('Não foi possível salvar o rascunho no registry local.');
    },
  });

  useEffect(() => {
    if (!running) return;
    if (runIndex >= runSequence.length - 1) {
      const timer = window.setTimeout(() => {
        setRunning(false);
        const result = dryRunStatus === 'waiting_human'
          ? `Dry-run pausou no gate humano após ${runSequence.length} etapas.`
          : dryRunStatus === 'max_steps_reached'
            ? `Dry-run interrompido pelo limite seguro após ${runSequence.length} etapas.`
            : `Dry-run concluído. ${runSequence.length} etapas percorridas, 0 efeitos externos.`;
        setNotice(result);
      }, 900);
      return () => window.clearTimeout(timer);
    }
    const timer = window.setTimeout(() => setRunIndex((current) => current + 1), 900);
    return () => window.clearTimeout(timer);
  }, [dryRunStatus, runIndex, runSequence.length, running]);

  const onNodesChange = useCallback((changes: NodeChange<Node<FlowNodeData>>[]) => {
    setNodes((current) => applyNodeChanges(changes, current));
    const changedGraph = changes.some((change) => (
      change.type === 'add'
      || change.type === 'remove'
      || change.type === 'replace'
      || change.type === 'position'
    ));
    if (changedGraph) {
      setIsDirty(true);
      setDryRunSequence([]);
      setDryRunStatus(null);
    }
  }, []);
  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((current) => applyEdgeChanges(changes, current));
    if (changes.some((change) => ['add', 'remove', 'replace'].includes(change.type))) {
      setIsDirty(true);
      setDryRunSequence([]);
      setDryRunStatus(null);
    }
  }, []);
  const onConnect = useCallback((connection: Connection) => {
    const routeKey = connection.sourceHandle || undefined;
    const conditionValue = connection.sourceHandle === 'true' || connection.sourceHandle === 'false'
      ? connection.sourceHandle
      : undefined;
    setEdges((current) => addEdge({
      ...connection,
      type: 'smoothstep',
      style: { stroke: '#0066F7', strokeWidth: 1.5 },
      label: conditionValue === 'true'
        ? 'SIM'
        : conditionValue === 'false'
          ? 'NÃO'
          : routeKey === 'fallback'
            ? 'DEMAIS CASOS'
            : routeKey?.toLocaleUpperCase('pt-BR'),
      ...(conditionValue ? { condition_value: conditionValue } : {}),
      ...(routeKey ? { route_key: routeKey } : {}),
      ...(routeKey === 'fallback' ? { is_fallback: true } : {}),
    }, current));
    setIsDirty(true);
    setDryRunSequence([]);
    setDryRunStatus(null);
  }, []);
  const onDropNode = useCallback((block: FlowBlockDefinition, position: { x: number; y: number }) => {
    if (!flowReady) return;
    const id = `local-${crypto.randomUUID()}`;
    setNodes((current) => [...current, {
      id,
      position,
      data: {
        title: block.title,
        category: block.category,
        description: block.description,
        nodeType: block.nodeType,
        config: { ...block.config },
        status: 'idle',
      },
      style: { width: 160 },
    }]);
    setSelectedId(id);
    setIsDirty(true);
    setDryRunSequence([]);
    setDryRunStatus(null);
    setNotice(`${block.title} adicionado. Arraste para posicionar e conecte pelas alças do node.`);
  }, [flowReady]);

  const updateSelectedNode = useCallback((nodeId: string, newData: FlowEditorNodeData) => {
    const { label, ...config } = newData;
    setNodes((current) => current.map((node) => node.id === nodeId
      ? {
          ...node,
          data: {
            ...node.data,
            title: label || node.data.title,
            config,
          },
        }
      : node));
    setIsDirty(true);
    setDryRunSequence([]);
    setDryRunStatus(null);
  }, []);

  // --- Painel schema-driven (spike Tarefa C, aditivo e isolado) ---------------
  // Ativado por ?schemaPanel=1 e só para o nó `condition`; o NodeConfigPanel
  // atual segue sendo o padrão sem a flag. Consome {schema, uischema} que a
  // Tarefa A expôs no catálogo do runtime (chaves opcionais, ausentes nos tipos
  // ainda no contrato v1 — daí o cast localizado, sem alterar o tipo público).
  const schemaPanelEnabled = searchParams.get('schemaPanel') === '1';
  const conditionCatalogEntry = useMemo(() => {
    const entry = (flowCatalogQuery.data?.nodes ?? []).find((node) => node.type === 'condition');
    return entry as
      | (NonNullable<typeof entry> & { schema?: JsonSchema; uischema?: UISchemaElement })
      | undefined;
  }, [flowCatalogQuery.data]);
  const conditionSchema = conditionCatalogEntry?.schema;
  const conditionUischema = conditionCatalogEntry?.uischema;
  const selectedCatalogNode = useMemo(
    () => (flowCatalogQuery.data?.nodes ?? []).find(
      (entry) => entry.type === selectedEditorNode?.type,
    ),
    [flowCatalogQuery.data, selectedEditorNode?.type],
  );

  // Dados iniciais do painel schema-driven, memoizados por nó (id) para manter a
  // referência estável durante a edição do mesmo nó: o JsonForms gerencia o
  // estado interno e empurra as mudanças por onChange, sem realimentação em loop.
  const conditionSchemaData = useMemo<Record<string, unknown>>(() => {
    if (!selectedEditorNode || selectedEditorNode.type !== 'condition') return {};
    const nodeData = selectedEditorNode.data;
    const out: Record<string, unknown> = {};
    if (typeof nodeData.field === 'string') out.field = nodeData.field;
    if (typeof nodeData.operator === 'string') out.operator = nodeData.operator;
    if (typeof nodeData.value === 'string') out.value = nodeData.value;
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedEditorNode?.id]);

  const applyConditionSchemaChange = useCallback(
    (node: Node<FlowEditorNodeData>, next: Record<string, unknown>) => {
      const merged: FlowEditorNodeData = {
        ...node.data,
        field: typeof next.field === 'string' ? next.field : node.data.field,
        operator: typeof next.operator === 'string' ? next.operator : node.data.operator,
        value: typeof next.value === 'string' ? next.value : node.data.value,
      };
      // Ignora o onChange inicial (defaults do JsonForms) e no-ops, para não
      // sujar o rascunho sem edição real.
      if (
        merged.field === node.data.field &&
        merged.operator === node.data.operator &&
        merged.value === node.data.value
      ) {
        return;
      }
      updateSelectedNode(node.id, merged);
    },
    [updateSelectedNode],
  );

  const openNodeEditor = useCallback((node: Node<FlowNodeData>) => {
    setSelectedId(node.id);
    setNotice(`Editando “${node.data.title}”. As alterações ficam no rascunho até salvar.`);
    window.setTimeout(() => {
      document.querySelector('[data-testid="flow-inspector"]')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, 0);
  }, []);

  const duplicateNode = useCallback((nodeId: string) => {
    if (!flowReady) return;
    const source = nodes.find((node) => node.id === nodeId);
    if (!source) return;
    const id = `local-${crypto.randomUUID()}`;
    const position = nextOpenGridPosition(nodes);
    setNodes((current) => [...current, {
      ...source,
      id,
      position,
      selected: false,
      data: {
        ...source.data,
        title: `${source.data.title} — cópia`,
        config: { ...(source.data.config ?? {}) },
      },
    }]);
    setSelectedId(id);
    setIsDirty(true);
    setDryRunSequence([]);
    setDryRunStatus(null);
    setNotice('Etapa duplicada no próximo espaço livre do canvas.');
  }, [flowReady, nodes]);

  const applyTemplate = (template: FlowTemplateDefinition) => {
    if (!flowReady) return;
    if (isDirty) {
      setNotice('Salve ou restaure as alterações atuais antes de aplicar outro template.');
      return;
    }
    const stamp = Date.now();
    const templateNodes = template.blocks.flatMap((title, index) => {
      const block = blocksByTitle.get(title);
      if (!block) return [];
      return [{
        id: `template-${stamp}-${index}`,
        position: { x: 60 + (index % 3) * 240, y: 112 + Math.floor(index / 3) * 168 },
        data: {
          title: block.title,
          category: block.category,
          description: block.description,
          nodeType: block.nodeType,
          config: { ...block.config },
          status: 'idle' as const,
        },
        style: { width: 160 },
      }];
    });
    const templateEdges = templateNodes.slice(1).map((node, index) => ({
      id: `template-edge-${stamp}-${index}`,
      source: templateNodes[index].id,
      target: node.id,
      type: 'smoothstep',
      style: { stroke: '#0066F7', strokeWidth: 1.5 },
    }));
    setNodes(templateNodes);
    setEdges(templateEdges);
    setSelectedId(templateNodes[0]?.id ?? '');
    setIsDirty(true);
    setDryRunSequence([]);
    setDryRunStatus(null);
    setNotice(`Template ${template.name} aplicado ao rascunho. Revise as conexões e salve para criar uma nova revisão.`);
  };

  const applyRegistryTemplate = (template: KlmFlow) => {
    if (!flowReady) return;
    if (isDirty) {
      setNotice('Salve ou restaure as alterações atuais antes de aplicar outro template.');
      return;
    }
    const templateNodes = nodesFromFlow(template);
    setNodes(templateNodes);
    setEdges(edgesFromFlow(template));
    setSelectedId(templateNodes[0]?.id ?? '');
    setIsDirty(true);
    setDryRunSequence([]);
    setDryRunStatus(null);
    setNotice(`Template versionado ${template.name} aplicado ao rascunho. Revise e salve para criar uma nova revisão.`);
  };

  const applyCopilotPlan = useCallback((plan: KlmFlowCopilotPlan) => {
    if (!activeFlow || !flowReady) return;
    const proposedFlow: KlmFlow = {
      ...activeFlow,
      nodes: plan.nodes,
      edges: plan.edges,
    };
    const nextNodes = nodesFromFlow(proposedFlow);
    setNodes(nextNodes);
    setEdges(edgesFromFlow(proposedFlow));
    setSelectedId(plan.added_node_ids[0] ?? nextNodes[0]?.id ?? '');
    setIsDirty(true);
    setRunning(false);
    setRunIndex(-1);
    setDryRunSequence([]);
    setDryRunStatus(null);
    setNotice(
      `Copiloto aplicou ${plan.added_node_ids.length} etapas ao rascunho. Revise o canvas, salve e execute o dry-run antes de publicar.`,
    );
  }, [activeFlow, flowReady]);

  const navigateFromDraft = (destination: string) => {
    if (isDirty && !window.confirm('Há alterações não salvas neste fluxo. Deseja sair sem salvá-las?')) return;
    navigate(destination);
  };

  const nudgeSelected = (dx: number, dy: number) => {
    if (!selected || !flowReady) return;
    setNodes((current) => current.map((node) => node.id === selected.id
      ? { ...node, position: { x: node.position.x + dx, y: node.position.y + dy } }
      : node));
    setIsDirty(true);
  };

  const connectSelected = (otherId: string, direction: 'before' | 'after') => {
    if (!selected || !flowReady || !otherId || otherId === selected.id) return;
    const source = direction === 'before' ? otherId : selected.id;
    const target = direction === 'before' ? selected.id : otherId;
    if (edges.some((edge) => edge.source === source && edge.target === target)) {
      setNotice('Esta conexão já existe no rascunho.');
      return;
    }
    setEdges((current) => [...current, {
      id: `edge-${source}-${target}-${Date.now()}`,
      source,
      target,
      type: 'smoothstep',
      style: { stroke: '#0066F7', strokeWidth: 1.5 },
    }]);
    setIsDirty(true);
    setNotice(`Conexão ${source} → ${target} adicionada ao rascunho.`);
  };

  const deleteNode = useCallback((nodeId: string) => {
    if (!flowReady) return;
    if (nodes.length <= 1) {
      setNotice('O fluxo precisa manter ao menos uma etapa.');
      return;
    }
    setNodes((current) => current.filter((node) => node.id !== nodeId));
    setEdges((current) => current.filter((edge) => edge.source !== nodeId && edge.target !== nodeId));
    setSelectedId(nodes.find((node) => node.id !== nodeId)?.id ?? '');
    setIsDirty(true);
    setNotice('Bloco e conexões associadas removidos do rascunho.');
  }, [flowReady, nodes]);

  const deleteSelected = () => {
    if (selected) deleteNode(selected.id);
  };

  const startSimulation = () => {
    if (isDirty) {
      setNotice('Salve o rascunho antes de simular. A execução nunca usa uma versão diferente da exibida no canvas.');
      return;
    }
    setNotice(null);
    if (requestedThreadId && threadsQuery.isPending) {
      setNotice('Aguarde a conversa contextual ser validada antes de iniciar o dry-run.');
      return;
    }
    if (activeFlow && selectedMatter) dryRunMutation.mutate({ flowId: activeFlow.id, matterId: selectedMatter.id, threadId: requestedThread?.id });
    else {
      setNotice('Abra Flows a partir de um caso ou conversa válida antes de iniciar o dry-run. Nenhum caso foi escolhido automaticamente.');
    }
  };

  const reset = () => {
    const persistedNodes = activeFlow ? nodesFromFlow(activeFlow) : [];
    setNodes(persistedNodes);
    setEdges(activeFlow ? edgesFromFlow(activeFlow) : []);
    setSelectedId(persistedNodes[0]?.id ?? '');
    setRunning(false);
    setRunIndex(-1);
    setDryRunSequence([]);
    setDryRunStatus(null);
    setIsDirty(false);
    setNotice('Rascunho restaurado para a versão local inicial.');
  };

  const selectFlow = (flowId: string) => {
    if (isDirty) {
      setNotice('Existem alterações não salvas. Salve ou restaure o rascunho antes de trocar de fluxo.');
      return;
    }
    setActiveFlowId(flowId);
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('flow', flowId);
    setSearchParams(nextParams, { replace: true });
    setRunning(false);
    setRunIndex(-1);
    setDryRunSequence([]);
    setDryRunStatus(null);
    setNotice(null);
  };

  const flowsReturnPath = (flowId?: string) => {
    const params = new URLSearchParams(searchParams);
    if (flowId) params.set('flow', flowId);
    const query = params.toString();
    return `/app/flows${query ? `?${query}` : ''}`;
  };
  const editorPath = (flowId?: string) => {
    const params = new URLSearchParams({ return: flowsReturnPath(flowId) });
    if (contextMatterId) params.set('matter', contextMatterId);
    if (requestedThreadId && contextIsConsistent) params.set('thread', requestedThreadId);
    return `/automacoes/editor${flowId ? `/${flowId}` : ''}?${params.toString()}`;
  };

  if (flowsQuery.isError) {
    return (
      <KlmPanel className="mx-auto max-w-[760px] p-8 text-center">
        <ShieldAlert className="mx-auto h-6 w-6 text-[#a53e3c]" />
        <h2 className="mt-4 text-lg font-semibold">Não foi possível carregar os fluxos</h2>
        <p className="mt-2 text-xs text-muted-foreground">O canvas não exibirá dados de demonstração como se fossem o registry real.</p>
        <Button className="mt-5" variant="outline" onClick={() => void flowsQuery.refetch()}><RotateCcw className="mr-2 h-4 w-4" />Tentar novamente</Button>
      </KlmPanel>
    );
  }

  if (flowsQuery.isLoading) {
    return (
      <KlmPanel className="mx-auto flex min-h-[420px] max-w-[760px] items-center justify-center p-8 text-center">
        <div>
          <LoaderCircle className="mx-auto h-6 w-6 animate-spin text-[#0057d8]" />
          <h2 className="mt-4 text-lg font-semibold">Carregando registry de fluxos</h2>
          <p className="mt-2 text-xs text-muted-foreground">O canvas será exibido somente após a revisão persistida estar disponível.</p>
        </div>
      </KlmPanel>
    );
  }

  if (!flowsQuery.isLoading && !(flowsQuery.data ?? []).length) {
    return (
      <KlmPanel className="mx-auto max-w-[760px] p-8 text-center">
        <Box className="mx-auto h-6 w-6 text-[#0057d8]" />
        <h2 className="mt-4 text-lg font-semibold">Nenhum fluxo no registry</h2>
        <p className="mt-2 text-xs text-muted-foreground">Crie o primeiro fluxo versionado no editor completo.</p>
        <Button className="mt-5" onClick={() => navigate(editorPath())}><Plus className="mr-2 h-4 w-4" />Criar fluxo</Button>
      </KlmPanel>
    );
  }

  return (
    <div data-testid="flows-page" className="mx-auto flex h-full min-h-0 max-w-[1440px] flex-col gap-2.5 overflow-y-auto xl:overflow-hidden">
      <div className="flex shrink-0 flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <p className="text-2xs font-semibold uppercase tracking-wide text-[#0057d8]">Meu Executivo / Flows</p>
          <h2 className="mt-1 text-2xl font-semibold">Automação visual</h2>
          <p className="mt-0.5 text-2xs text-muted-foreground">Canvas operacional tipado, versionado, testável, idempotente e observável.</p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Select value={activeFlow?.id ?? ''} onValueChange={selectFlow}>
            <SelectTrigger className="h-9 w-[min(78vw,280px)] text-xs sm:w-[280px]" aria-label="Fluxo em edição">
              <Box className="mr-2 h-3.5 w-3.5 shrink-0 text-[#0057d8]" />
              <SelectValue placeholder="Selecionar fluxo" />
            </SelectTrigger>
            <SelectContent>
              {(flowsQuery.data ?? []).map((flow) => (
                <SelectItem key={flow.id} value={flow.id} data-flow-id={flow.id}>
                  {flow.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <KlmPill className="tabular-nums">{activeFlow ? `${activeFlow.run_count} runs` : 'carregando'}</KlmPill><KlmPill tone={selectedMatter ? 'green' : 'muted'}>{selectedMatter ? `Caso: ${selectedMatter.name}` : 'Sem caso no contexto'}</KlmPill><KlmPill tone={!flowReady || isDirty ? 'gold' : 'green'} className="tabular-nums">{!flowReady ? <LoaderCircle className="mr-1 h-3 w-3 animate-spin" /> : <Save className="mr-1 h-3 w-3" />}{!flowReady ? 'Carregando revisão' : isDirty ? 'Não salvo' : `Revisão ${activeFlow?.current_revision ?? 0}`}</KlmPill>
          <Button variant="outline" size="sm" className="h-8 text-2xs" onClick={() => navigateFromDraft(editorPath())}><Plus className="mr-1.5 h-3.5 w-3.5" />Novo fluxo</Button>
          <Button variant="outline" size="sm" className="h-8 text-2xs" disabled={!activeFlow} onClick={() => activeFlow && navigateFromDraft(editorPath(activeFlow.id))}><UploadCloud className="mr-1.5 h-3.5 w-3.5" />Tela ampla</Button>
          <Button variant="outline" size="sm" className="h-8 text-2xs" disabled={!flowReady} onClick={reset}><RotateCcw className="mr-1.5 h-3.5 w-3.5" />Restaurar</Button>
          <Button size="sm" className="h-8 bg-primary text-2xs text-primary-foreground hover:bg-primary/90" title={selectedMatter ? 'Executar dry-run no caso contextual' : 'Abra Flows a partir de um caso para executar o dry-run'} disabled={!flowReady || !selectedMatter || dryRunMutation.isPending || Boolean(requestedThreadId && threadsQuery.isPending)} onClick={running ? () => setRunning(false) : startSimulation}>{running ? <CirclePause className="mr-1.5 h-3.5 w-3.5" /> : <Play className="mr-1.5 h-3.5 w-3.5" />}{dryRunMutation.isPending ? 'Preparando' : running ? 'Pausar replay' : 'Dry-run'}</Button>
          <Button size="sm" className="h-8 bg-[#031121] text-2xs text-white hover:bg-[#031121]" disabled={!flowReady || !isDirty || saveMutation.isPending} onClick={() => saveMutation.mutate()}><Save className="mr-1.5 h-3.5 w-3.5" />{saveMutation.isPending ? 'Salvando' : 'Salvar local'}</Button>
        </div>
      </div>

      <EffectivePlaybookLink
        selectors={{
          area: activeFlow?.practice_area ?? 'negativas_plano_saude',
          stage: selectedStage,
          agent: selectedAgent,
        }}
        className="w-fit max-w-full"
      />

      <details data-testid="flow-native-toolbox" className="group shrink-0 rounded-lg border border-border bg-card">
        <summary className="flex h-10 cursor-pointer list-none items-center gap-2 px-3 text-sm font-medium marker:hidden">
          <Settings2 className="h-3.5 w-3.5 text-[#0057d8]" />
          Runtime, agentes e capacidades nativas
          <KlmPill tone="green" className="ml-auto">prontos</KlmPill>
        </summary>
        <div className="max-h-[52dvh] space-y-3 overflow-y-auto border-t border-border p-3 scrollbar-thin">
          <NativeModuleBar
            domain="automation"
            flowId={activeFlow?.id}
            surface="Editor de flows"
            returnTo={activeFlow ? `/app/flows?flow=${activeFlow.id}` : '/app/flows'}
            hideSync
            capabilityIds={[
              'visual-editor',
              'nodes-forms-conditions',
              'nodes-form-action-end-transfer-landing-llm',
              'preview-execution',
              'draft-publish-version',
              'mcp-tools-agent-association',
              'handoff-inbox-transfer',
            ]}
          />
          {activeFlow && (
            <NativeFlowRuntimePanel
              flowId={activeFlow.id}
              revision={activeFlow.current_revision}
              isDirty={isDirty}
              flowReady={flowReady}
              canManage={can('automations', 'manage')}
              canOpenEditor={can('automations', 'view')}
            />
          )}
          <NativeAgentOperationsPanel view="flows" canManage={can('agents', 'manage')} />
        </div>
      </details>

      <div className="flex min-h-8 shrink-0 items-center gap-2 rounded-lg border border-[#031121]/25 bg-[#031121] px-3 py-2 text-xs text-white">
        <span className={cn('h-2 w-2 shrink-0 rounded-full', running ? 'animate-pulse bg-primary' : 'bg-white/35')} />
        <span className="min-w-0 truncate font-semibold tabular-nums">{running ? `DRY-RUN · etapa ${runIndex + 1}/${runSequence.length} · node ${runningNodeId}` : 'IDLE · dry-run sem persistência · efeitos externos bloqueados'}</span>
      </div>

      {notice && <div className="flex shrink-0 items-center justify-between gap-2 rounded-lg border border-primary/35 bg-primary/10 px-3 py-2 text-xs"><span className="min-w-0">{notice}</span><Button variant="ghost" size="sm" className="h-7 shrink-0 text-xs" onClick={() => setNotice(null)}>Fechar</Button></div>}

      <div data-testid="flow-workspace" className="grid min-h-[760px] gap-3 xl:min-h-0 xl:flex-1 xl:grid-cols-[236px_minmax(440px,1fr)_280px]">
        <KlmPanel data-testid="flow-block-palette" className="order-2 h-full min-h-0 max-h-[760px] overflow-y-auto p-3 scrollbar-thin xl:order-1 xl:max-h-none">
          <div className="flex items-start justify-between gap-2">
            <KlmSectionTitle title="Blocos" description="Arraste ou adicione com um clique" />
            <KlmPill tone={flowCatalogQuery.isError ? 'red' : 'green'} className="shrink-0 tabular-nums">
              {flowCatalogQuery.data?.node_count ?? new Set(blockGroups.flatMap((group) => group.blocks.map((block) => block.nodeType))).size} tipos
            </KlmPill>
          </div>
          <div className="relative mt-3">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              value={blockSearch}
              onChange={(event) => setBlockSearch(event.target.value)}
              className="h-8 pl-8 text-2xs"
              placeholder="Buscar gatilho, ação ou ferramenta"
              aria-label="Buscar blocos do flow"
            />
          </div>
          <div className="mt-4 space-y-4">
            {paletteGroups.map(({ group, blocks }) => (
              <div key={group}>
                <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{group}</p>
                <div className="space-y-1.5">
                  {blocks.map((block) => (
                    <button
                      key={block.title}
                      draggable
                      disabled={!flowReady}
                      aria-label={`Adicionar bloco ${block.title}`}
                      title={`${block.description} Arraste ou toque para adicionar.`}
                      onDragStart={(event) => {
                        suppressPaletteClickUntil.current = Date.now() + 2_000;
                        event.dataTransfer.effectAllowed = 'copy';
                        event.dataTransfer.setData('application/klm-flow-block', JSON.stringify(block));
                      }}
                      onDragEnd={() => { suppressPaletteClickUntil.current = Date.now() + 400; }}
                      onClick={(event) => {
                        if (Date.now() < suppressPaletteClickUntil.current) {
                          event.preventDefault();
                          return;
                        }
                        onDropNode(block, nextOpenGridPosition(nodes));
                      }}
                      className="flex min-h-8 w-full items-center gap-2 rounded-md border border-border bg-card px-2 py-1 text-left hover:border-primary disabled:cursor-wait disabled:opacity-50"
                    >
                      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" /><span title={block.title} className="min-w-0 flex-1 line-clamp-1 text-xs font-medium leading-4">{block.title}</span><Plus className="ml-auto h-3 w-3 shrink-0 text-muted-foreground" />
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </KlmPanel>

        <div className="order-1 grid min-h-[720px] gap-3 xl:order-2 xl:h-full xl:min-h-0 xl:grid-rows-[minmax(0,1fr)_130px]">
          <KlmPanel className="relative flex min-h-[540px] flex-col overflow-hidden p-0 xl:min-h-0">
            <div className="z-10 flex shrink-0 items-center justify-between gap-2 border-b border-border bg-card px-3 py-2">
              <div className="min-w-0"><p className="truncate text-sm font-medium">Fluxo: {activeFlow?.name ?? 'sem revisão carregada'}</p><p className="mt-0.5 truncate text-xs text-muted-foreground">Trace tipado · branches reais · limite de passos · gates humanos · zero envios</p></div>
              <KlmPill tone={running ? 'green' : 'gold'} className="shrink-0">{running ? 'live' : 'dry-run'}</KlmPill>
            </div>
            <div className="min-h-0 flex-1">
              <ReactFlowProvider>
                <FlowCanvas
                  nodes={nodes}
                  edges={edges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  onConnect={onConnect}
                  onNodeClick={(node) => setSelectedId(node.id)}
                  onNodeDoubleClick={openNodeEditor}
                  onDuplicateNode={duplicateNode}
                  onDeleteNode={deleteNode}
                  onDropNode={onDropNode}
                  runningNodeId={runningNodeId}
                  flowReady={flowReady}
                />
              </ReactFlowProvider>
            </div>
            {!flowReady && <div className="absolute inset-0 z-20 flex items-center justify-center bg-card/80 backdrop-blur-sm"><span className="flex items-center gap-2 text-sm font-medium"><LoaderCircle className="h-4 w-4 animate-spin" />Carregando snapshot da revisão...</span></div>}
          </KlmPanel>
          <KlmPanel className="min-h-0 overflow-y-auto p-3 scrollbar-thin">
            <KlmSectionTitle
              title="Templates prontos"
              description={flowTemplatesQuery.isSuccess ? 'Biblioteca versionada do backend e modelos locais' : 'Modelos locais; biblioteca versionada indisponível'}
            />
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {(flowTemplatesQuery.data ?? []).map((template) => (
                <button key={template.id} disabled={!flowReady} className="min-w-0 rounded-lg border border-primary/30 bg-primary/5 p-2 text-left hover:border-primary disabled:cursor-wait disabled:opacity-50" onClick={() => applyRegistryTemplate(template)}>
                  <span title={template.name} className="line-clamp-1 block text-sm font-medium">{template.name}</span>
                  <span className="mt-1 block truncate text-xs tabular-nums text-muted-foreground">{template.nodes.length} etapas · versionado</span>
                </button>
              ))}
              {localFlowTemplates.map((template) => <button key={template.name} disabled={!flowReady} className="min-w-0 rounded-lg border border-border p-2 text-left hover:border-primary disabled:cursor-wait disabled:opacity-50" onClick={() => applyTemplate(template)}><span title={template.name} className="line-clamp-1 block text-sm font-medium">{template.name}</span><span className="mt-1 block truncate text-xs tabular-nums text-muted-foreground">{template.blocks.length} etapas · modelo local</span></button>)}
            </div>
          </KlmPanel>
        </div>

        <div className="order-3 grid gap-3 md:grid-cols-2 xl:h-full xl:min-h-0 xl:grid-cols-1 xl:grid-rows-[minmax(0,1fr)_auto]">
          <KlmPanel data-testid="flow-inspector" className="h-full min-h-0 overflow-hidden p-0">
            <div className="border-b border-border p-3">
              <div className="flex items-center justify-between gap-2">
                <KlmSectionTitle title="Inspector" description="Configuração tipada da etapa" />
                <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-destructive" disabled={!selected || !flowReady || nodes.length <= 1} title="Excluir etapa" onClick={deleteSelected}><Trash2 className="h-3.5 w-3.5" /></Button>
              </div>
              {selected ? (
                <div className="mt-3 space-y-2">
                  <div className="grid grid-cols-4 gap-1.5">
                    <Button variant="outline" size="icon" className="h-8 w-full" title="Mover etapa para a esquerda" disabled={!flowReady} onClick={() => nudgeSelected(-24, 0)}><ArrowLeft className="h-3.5 w-3.5" /></Button>
                    <Button variant="outline" size="icon" className="h-8 w-full" title="Mover etapa para cima" disabled={!flowReady} onClick={() => nudgeSelected(0, -24)}><ArrowUp className="h-3.5 w-3.5" /></Button>
                    <Button variant="outline" size="icon" className="h-8 w-full" title="Mover etapa para baixo" disabled={!flowReady} onClick={() => nudgeSelected(0, 24)}><ArrowDown className="h-3.5 w-3.5" /></Button>
                    <Button variant="outline" size="icon" className="h-8 w-full" title="Mover etapa para a direita" disabled={!flowReady} onClick={() => nudgeSelected(24, 0)}><ArrowRight className="h-3.5 w-3.5" /></Button>
                  </div>
                  <Select onValueChange={(nodeId) => connectSelected(nodeId, 'before')} disabled={!flowReady || nodes.length < 2}>
                    <SelectTrigger className="h-8 text-sm" aria-label="Conectar depois de outra etapa"><Link2 className="mr-2 h-3 w-3" /><SelectValue placeholder="Conectar depois de..." /></SelectTrigger>
                    <SelectContent>{nodes.filter((node) => node.id !== selected.id).map((node) => <SelectItem key={node.id} value={node.id}>{node.data.title}</SelectItem>)}</SelectContent>
                  </Select>
                  <Select onValueChange={(nodeId) => connectSelected(nodeId, 'after')} disabled={!flowReady || nodes.length < 2}>
                    <SelectTrigger className="h-8 text-sm" aria-label="Conectar antes de outra etapa"><Link2 className="mr-2 h-3 w-3" /><SelectValue placeholder="Conectar antes de..." /></SelectTrigger>
                    <SelectContent>{nodes.filter((node) => node.id !== selected.id).map((node) => <SelectItem key={node.id} value={node.id}>{node.data.title}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
              ) : null}
            </div>
            {selectedEditorNode ? (
              <div className="h-[620px] min-h-0 xl:h-[calc(100%_-_168px)]">
                {schemaPanelEnabled && selectedEditorNode.type === 'condition' && conditionSchema ? (
                  <SchemaDrivenNodePanel
                    key={selectedEditorNode.id}
                    schema={conditionSchema}
                    uischema={conditionUischema}
                    title="Condição (schema-driven)"
                    description="Painel experimental derivado do catálogo do runtime (registro v2). Ativado por ?schemaPanel=1; sem a flag, o editor padrão segue disponível."
                    data={conditionSchemaData}
                    onChange={(next) => applyConditionSchemaChange(selectedEditorNode, next)}
                    onClose={() => setSelectedId('')}
                  />
                ) : (
                  <NodeConfigPanel
                    node={selectedEditorNode}
                    catalogNode={selectedCatalogNode}
                    onUpdate={updateSelectedNode}
                    onClose={() => setSelectedId('')}
                  />
                )}
              </div>
            ) : (
              <div className="m-3 rounded-lg border border-dashed border-border p-4 text-xs text-muted-foreground">Selecione uma etapa no canvas para editar todos os campos, ferramentas e estratégias de erro.</div>
            )}
          </KlmPanel>
          <KlmPanel className="p-3">
            <KlmSectionTitle title="Execução" description={running ? 'Replay do dry-run atual' : 'Dados do registry'} />
            <div className="mt-3 grid grid-cols-2 gap-2">
              {[[String(activeFlow?.run_count ?? 0), 'runs'], [String(nodes.length), 'etapas'], [String(edges.length), 'conexões'], [`r${activeFlow?.current_revision ?? 0}`, 'revisão']].map(([value, label]) => <div key={label} className="min-w-0 border-l-2 border-primary pl-2"><p className="truncate text-base font-semibold tabular-nums">{value}</p><p className="truncate text-xs uppercase tracking-wide text-muted-foreground">{label}</p></div>)}
            </div>
            <div className="mt-3"><KlmProgress value={running ? Math.max(8, ((runIndex + 1) / runSequence.length) * 100) : 0} tone="green" /></div>
            <div className="mt-3 text-xs tabular-nums text-muted-foreground">{running ? `Etapa ${runIndex + 1} de ${runSequence.length}: ${runningNodeId}` : dryRunStatus ? `Último dry-run: ${dryRunStatus}` : 'Nenhum dry-run em execução. O canvas mostra a revisão persistida.'}</div>
          </KlmPanel>
        </div>
      </div>
      <FlowCopilot
        flowId={activeFlow?.id}
        flowName={activeFlow?.name}
        practiceArea={activeFlow?.practice_area}
        workspaceId={state.activeWorkspace?.id}
        userId={state.currentUser?.id}
        currentNodes={serializedNodes}
        currentEdges={serializedEdges}
        disabled={!flowReady || !can('automations', 'manage')}
        onApply={applyCopilotPlan}
      />
    </div>
  );
}
