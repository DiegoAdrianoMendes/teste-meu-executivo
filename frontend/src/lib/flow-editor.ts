import type { Edge, Node } from '@xyflow/react';

export type FlowNodeType =
  | 'trigger'
  | 'send_message'
  | 'wait'
  | 'condition'
  | 'router'
  | 'merge'
  | 'loop'
  | 'error_handler'
  | 'set_variable'
  | 'transform'
  | 'aggregate'
  | 'link_action'
  | 'human_pause'
  | 'agent'
  | 'create_task'
  | 'update_card'
  | 'internal_note'
  | 'audit_event'
  | 'payment_request'
  | 'social_action'
  | 'connector_action'
  | 'zapsign_request'
  | 'scheduled_message'
  | 'form'
  | 'action'
  | 'end'
  | 'human_transfer'
  | 'human_assign'
  | 'human_request_input'
  | 'human_review'
  | 'human_approval'
  | 'human_takeover'
  | 'human_notify'
  | 'human_escalate'
  | 'human_schedule'
  | 'human_wait'
  | 'human_resolve'
  | 'finance_read'
  | 'finance_draft'
  | 'landing_page'
  | 'llm';

export type FlowStageKey =
  | 'entrada'
  | 'proposta_fechamento'
  | 'secretaria_agendamento'
  | 'coleta_dados'
  | 'assistente_juridico'
  | 'financeiro_cobranca'
  | 'pos_venda_relacionamento';

export type FlowContextMode = 'essential' | 'balanced' | 'full';
export type SignatureProvider = 'zapsign' | 'docusign' | 'manual';

export interface FlowSequenceBinding {
  fieldKey: string;
  value: string;
}

export interface FlowSequenceMessage {
  id: string;
  templateId?: string | null;
  templateName?: string | null;
  templateLanguage?: string | null;
  body?: string;
  status?: string | null;
  windowStatus?: 'new' | 'paid' | 'unverified';
  delayValue?: number;
  delayUnit?: 'hours' | 'days';
  /** Persisted scheduled stages use this to distinguish "now" from a
   * relative delay when an executable node is opened and saved again. */
  scheduleMode?: 'now' | 'delay';
  scheduledAt?: string | null;
  bindings?: FlowSequenceBinding[];
}

export interface FlowEditorNodeData extends Record<string, unknown> {
  label: string;
  trigger_type?: string;
  event?: string;
  column_id?: string;
  message?: string;
  delay_hours?: number;
  delay_value?: number;
  delay_unit?: 'hours' | 'days';
  condition?: string;
  field?: string;
  operator?: string;
  value?: string;
  reason?: string;
  agent?: string;
  stage_key?: FlowStageKey;
  prompt?: string;
  model?: string;
  context_mode?: FlowContextMode;
  use_rag?: boolean;
  multimodal_enabled?: boolean;
  human_review_required?: boolean;
  doc_type?: string;
  auth_mode?: string;
  provider?: SignatureProvider;
  template_name?: string;
  integration_notes?: string;
  scheduled_at?: string;
  routes?: Array<{ key: string; label?: string; field?: string; operator?: string; value?: unknown }>;
  fallback_route_key?: string;
  collection_path?: string;
  item_variable?: string;
  index_variable?: string;
  max_iterations?: number;
  merge_mode?: string;
  variable_name?: string;
  value_expression?: unknown;
  value_type?: string;
  mappings?: Record<string, unknown>;
  source_path?: string;
  target_variable?: string;
  aggregate_operation?: string;
  separator?: string;
  link_kind?: string;
  url?: string;
  link_title?: string;
  target?: string;
  open_in_new_tab?: boolean;
  task_title?: string;
  task_description?: string;
  task_priority?: string;
  updates?: Record<string, unknown>;
  note?: string;
  event_type?: string;
  summary?: string;
  connector?: string;
  operation?: string;
  input_mapping?: Record<string, unknown>;
  output_mapping?: Record<string, unknown>;
  timeout_seconds?: number;
  error_strategy?: string;
  max_retries?: number;
  retry_delay_seconds?: number;
  clear_error?: boolean;
  payment_provider?: string;
  payment_method?: string;
  amount_expression?: unknown;
  due_days?: number;
  payment_description?: string;
  social_action?: string;
  target_external_id?: string;
  case_sensitive?: boolean;
  first_level_only?: boolean;
  follower_status?: string;
  verified_status?: string;
  messaging_window?: string;
  data_availability_policy?: string;
  required_capability?: string;
  excluded_keywords?: string[];
  keywords?: string[];
  keyword_mode?: string;
  match_mode?: string;
  source_ids?: string[];
  fields?: Array<{ name: string; label?: string; type?: string; required?: boolean; options?: string[] }>;
  submit_label?: string;
  team?: string;
  queue?: string;
  end_status?: string;
  sequence?: FlowSequenceMessage[];
  is_sequence?: boolean;
  interrupt_on_reply?: boolean;
  sequence_stage?: boolean;
  template_status?: 'approved' | 'pending';
  template_id?: string;
  template_language?: string;
  send_now?: boolean;
}

export const AGENT_OPTIONS = [
  { value: 'klm_sdr', label: 'SDR Jurídico', description: 'Primeiro atendimento, qualificação e coleta inicial.' },
  { value: 'klm_analyst', label: 'Analista IA', description: 'Leitura do caso, riscos, viabilidade e próximos passos.' },
  { value: 'klm_drafter', label: 'Redator IA', description: 'Geração de conteúdo estruturado com revisão humana.' },
  { value: 'administrativo_ops', label: 'Onboarding Ops', description: 'Coleta de dados, documentos e checklist operacional.' },
  { value: 'financeiro_ops', label: 'Financeiro Ops', description: 'Cobrança, lembretes e pendências financeiras.' },
  { value: 'secretaria_hibrida', label: 'Secretaria Híbrida', description: 'Agenda, confirmações e preparação operacional.' },
  { value: 'assistente_juridico', label: 'Assistente Jurídico', description: 'Apoio consultivo e organização do caso.' },
];

export const STAGE_OPTIONS: Array<{ value: FlowStageKey; label: string }> = [
  { value: 'entrada', label: 'Entrada e qualificação' },
  { value: 'proposta_fechamento', label: 'Fechamento comercial' },
  { value: 'secretaria_agendamento', label: 'Agenda e confirmação' },
  { value: 'coleta_dados', label: 'Coleta de dados e documentos' },
  { value: 'assistente_juridico', label: 'Apoio jurídico' },
  { value: 'financeiro_cobranca', label: 'Financeiro e cobrança' },
  { value: 'pos_venda_relacionamento', label: 'Pós-venda e relacionamento' },
];

export const MODEL_OPTIONS = [
  { value: '__default__', label: 'Usar padrão do workspace' },
  { value: 'google/gemma3@gemma-3-4b-it', label: 'Gemma 3 4B' },
  { value: 'google/gemma3@gemma-3-27b-it', label: 'Gemma 3 27B' },
  { value: 'qwen3:4b', label: 'Qwen3 4B (Ollama)' },
  { value: 'qwen3:14b', label: 'Qwen3 14B (Ollama)' },
  { value: 'gemma4:12b', label: 'Gemma 4 12B (Ollama)' },
  { value: 'gemma4:e4b', label: 'Gemma 4 E4B (Ollama)' },
  { value: 'qwen3:30b', label: 'Qwen3 30B (Ollama)' },
  { value: 'gemma4:31b', label: 'Gemma 4 31B (Ollama)' },
  { value: 'gemini-2.5-flash-lite', label: 'Gemini 2.5 Flash-Lite' },
  { value: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
  { value: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
  { value: 'gpt-4o-mini', label: 'GPT-4o mini' },
  { value: 'gpt-4o', label: 'GPT-4o' },
];

export const CONTEXT_MODE_OPTIONS: Array<{ value: FlowContextMode; label: string; hint: string }> = [
  { value: 'essential', label: 'Essencial', hint: 'Só o mínimo para reduzir custo e latência.' },
  { value: 'balanced', label: 'Balanceado', hint: 'Mistura conversa, card e memória relevante.' },
  { value: 'full', label: 'Completo', hint: 'Entrega visão total do caso quando a etapa exige mais contexto.' },
];

export const SIGNATURE_PROVIDER_OPTIONS: Array<{ value: SignatureProvider; label: string; hint: string }> = [
  { value: 'zapsign', label: 'ZapSign', hint: 'Execução nativa automatizada já suportada.' },
  { value: 'docusign', label: 'DocuSign', hint: 'Configuração pronta para operação assistida.' },
  { value: 'manual', label: 'Manual / outro integrador', hint: 'Cria passo operacional claro para o time concluir.' },
];

const DEFAULT_NODE_DATA: Record<FlowNodeType, FlowEditorNodeData> = {
  trigger: {
    label: 'Novo lead',
    trigger_type: 'new_lead',
    event: 'new_lead',
    keywords: [],
    excluded_keywords: [],
    keyword_mode: 'contains',
    match_mode: 'any',
    source_ids: [],
    case_sensitive: false,
    first_level_only: true,
    follower_status: 'any',
    verified_status: 'any',
    messaging_window: 'any',
  },
  send_message: { label: 'Enviar mensagem', message: '' },
  wait: { label: 'Aguardar', delay_hours: 24, condition: 'always' },
  condition: { label: 'Verificar', condition: 'Condição', field: '', operator: 'gte', value: '' },
  router: {
    label: 'Rotear por regras',
    routes: [
      { key: 'qualificado', label: 'Qualificado', field: 'lead_score', operator: 'gte', value: 70 },
      { key: 'fallback', label: 'Demais casos' },
    ],
    fallback_route_key: 'fallback',
  },
  merge: { label: 'Unir rotas', merge_mode: 'exclusive' },
  loop: { label: 'Percorrer itens', collection_path: 'items', item_variable: 'item', index_variable: 'index', max_iterations: 100 },
  error_handler: { label: 'Tratar falha', clear_error: true },
  set_variable: { label: 'Definir variável', variable_name: 'flow.value', value_expression: '', value_type: 'auto' },
  transform: { label: 'Mapear dados', mappings: { 'flow.matterId': '{{matter_id}}' } },
  aggregate: { label: 'Agregar dados', source_path: 'items', target_variable: 'flow.total', aggregate_operation: 'count', separator: ', ' },
  link_action: { label: 'Liberar link', link_kind: 'website', url: '/app', link_title: 'Abrir', target: 'customer', target_variable: 'lastLink', open_in_new_tab: true },
  landing_page: { label: 'Página de destino', link_kind: 'website', url: '/app', link_title: 'Abrir página', target: 'customer', open_in_new_tab: true },
  form: { label: 'Coletar dados', fields: [{ name: 'nome', label: 'Nome', type: 'text', required: true }], submit_label: 'Enviar' },
  action: { label: 'Ação externa', connector: 'webhook', operation: 'post', input_mapping: {}, output_mapping: {}, timeout_seconds: 30 },
  end: { label: 'Encerrar fluxo', end_status: 'completed', reason: 'Fluxo concluído' },
  human_pause: { label: 'Revisão necessária', reason: 'Revisar antes de continuar' },
  human_transfer: { label: 'Transferir para humano', team: 'Jurídico', reason: 'Atendimento humano necessário' },
  human_assign: { label: 'Atribuir responsável', reason: 'Atribuição humana necessária', required_roles: ['workspace_owner'], required_skills: [], sla_seconds: 3600, max_hops: 4, priority: 'normal' },
  human_request_input: { label: 'Solicitar informação', reason: 'Informação necessária para continuar', required_roles: ['workspace_owner'], required_skills: [], sla_seconds: 3600, max_hops: 4, priority: 'normal' },
  human_review: { label: 'Revisão humana', reason: 'Revisar o resultado antes de continuar', required_roles: ['workspace_owner'], required_skills: [], sla_seconds: 3600, max_hops: 4, priority: 'high' },
  human_approval: { label: 'Aprovação humana', reason: 'Revisar o próximo passo antes de prosseguir.', required_roles: ['workspace_owner'], required_skills: [], sla_seconds: 3600, max_hops: 4, priority: 'high' },
  human_takeover: { label: 'Assumir atendimento', reason: 'Atendimento humano necessário', required_roles: ['workspace_owner'], required_skills: [], sla_seconds: 3600, max_hops: 4, priority: 'high' },
  human_notify: { label: 'Notificar humano', reason: 'Acompanhamento humano necessário', required_roles: ['workspace_owner'], required_skills: [], sla_seconds: 3600, max_hops: 4, priority: 'normal' },
  human_escalate: { label: 'Escalonar', reason: 'SLA humano atingido', required_roles: ['workspace_owner'], required_skills: [], sla_seconds: 3600, max_hops: 4, priority: 'high' },
  human_schedule: { label: 'Agendar', reason: 'Agendamento humano necessário', required_roles: ['workspace_owner'], required_skills: [], sla_seconds: 3600, max_hops: 4, priority: 'normal' },
  human_wait: { label: 'Aguardar', reason: 'Aguardar decisão humana persistida', required_roles: ['workspace_owner'], required_skills: [], sla_seconds: 3600, max_hops: 4, priority: 'normal' },
  human_resolve: { label: 'Resolver decisão', target_node_id: '' },
  finance_read: { label: 'Consultar financeiro', operation: 'analytics', parameters: {}, human_review_required: true },
  finance_draft: { label: 'Rascunho financeiro', operation: 'allocation_draft', parameters: {}, human_review_required: true },
  agent: {
    label: 'SDR Jurídico',
    agent: 'klm_sdr',
    stage_key: 'entrada',
    prompt: '',
    model: '',
    context_mode: 'balanced',
    use_rag: true,
    multimodal_enabled: true,
    human_review_required: false,
  },
  llm: { label: 'Modelo de IA', agent: 'klm_sdr', prompt: 'Analise o contexto do caso e proponha o próximo passo.', context_mode: 'balanced', use_rag: true, human_review_required: true },
  create_task: { label: 'Criar tarefa', task_title: 'Revisar caso', task_description: '', task_priority: 'media' },
  update_card: { label: 'Atualizar Caso 360', updates: { next_action: 'Revisar próxima ação' } },
  internal_note: { label: 'Registrar nota interna', note: 'Checkpoint criado pelo fluxo.' },
  audit_event: { label: 'Registrar auditoria', event_type: 'flow_checkpoint', summary: 'Checkpoint do fluxo' },
  payment_request: { label: 'Solicitar pagamento', payment_provider: 'asaas', payment_method: 'pix', amount_expression: '{{honorarios.iniciais}}', due_days: 3, payment_description: 'Honorários advocatícios' },
  social_action: { label: 'Responder interação', social_action: 'private_reply', message: 'Olá. Enviei uma mensagem para entender melhor seu caso.', target_external_id: '{{social.eventId}}' },
  connector_action: { label: 'Acionar conector', connector: 'webhook', operation: 'post', input_mapping: {}, output_mapping: {}, timeout_seconds: 30 },
  zapsign_request: {
    label: 'Assinatura do cliente',
    provider: 'zapsign',
    doc_type: 'procuracao',
    auth_mode: 'assinaturaTela-tokenEmail',
    template_name: '',
    integration_notes: '',
  },
  scheduled_message: {
    label: 'Mensagem agendada',
    message: '',
    scheduled_at: '09:00',
  },
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function toStringValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function toNumberValue(value: unknown, fallback: number): number {
  return typeof value === 'number' && !Number.isNaN(value) ? value : fallback;
}

function toBooleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

export function getDefaultNodeData(type: string): FlowEditorNodeData {
  const defaultData = DEFAULT_NODE_DATA[(type as FlowNodeType) ?? 'trigger'];
  return defaultData ? { ...defaultData } : { label: 'Novo nó' };
}

export function normalizeFlowNode(rawNode: Record<string, unknown>): Node<FlowEditorNodeData> {
  const type = toStringValue(rawNode.type, 'trigger') as FlowNodeType;
  const defaults = getDefaultNodeData(type);
  const config = isRecord(rawNode.config) ? rawNode.config : {};
  const data = isRecord(rawNode.data) ? rawNode.data : {};
  const merged = { ...defaults, ...config, ...data } as FlowEditorNodeData;
  const triggerType = toStringValue(merged.trigger_type ?? merged.event, 'new_lead');
  const label = toStringValue(rawNode.label ?? merged.label, defaults.label);
  const scheduledSequenceBindings = Array.isArray(merged.bindings)
    ? merged.bindings.filter(isRecord).map((binding) => ({
      fieldKey: toStringValue(binding.fieldKey ?? binding.field_key),
      value: '',
    })).filter((binding) => binding.fieldKey)
    : [];
  const scheduledSequence = type === 'scheduled_message' && (merged.sequence_stage || merged.template_name)
    ? [{
      id: toStringValue(rawNode.id, `${type}-${Date.now()}`),
      templateId: typeof merged.template_id === 'string' ? merged.template_id : null,
      templateName: typeof merged.template_name === 'string' ? merged.template_name : null,
      templateLanguage: typeof merged.template_language === 'string' ? merged.template_language : null,
      body: typeof merged.message === 'string' ? merged.message : '',
      status: typeof merged.template_status === 'string' ? merged.template_status : null,
      delayValue: typeof merged.delay_value === 'number' ? merged.delay_value : undefined,
      delayUnit: merged.delay_unit === 'days' ? 'days' as const : 'hours' as const,
      scheduleMode: merged.send_now === true || typeof merged.delay_value !== 'number' ? 'now' as const : 'delay' as const,
      scheduledAt: typeof merged.scheduled_at === 'string' ? merged.scheduled_at : null,
      bindings: scheduledSequenceBindings,
    }]
    : undefined;

  return {
    id: toStringValue(rawNode.id, `${type}-${Date.now()}`),
    type,
    position: isRecord(rawNode.position)
      ? {
          x: toNumberValue(rawNode.position.x, 0),
          y: toNumberValue(rawNode.position.y, 0),
        }
      : { x: 0, y: 0 },
    data: {
      ...merged,
      label,
      trigger_type: triggerType,
      event: triggerType,
      delay_hours: toNumberValue(merged.delay_hours, defaults.delay_hours ?? 24),
      max_iterations: toNumberValue(merged.max_iterations, defaults.max_iterations ?? 100),
      timeout_seconds: toNumberValue(merged.timeout_seconds, defaults.timeout_seconds ?? 30),
      due_days: toNumberValue(merged.due_days, defaults.due_days ?? 3),
      model: toStringValue(merged.model, toStringValue(defaults.model, '')),
      context_mode: toStringValue(merged.context_mode, toStringValue(defaults.context_mode, 'balanced')) as FlowContextMode,
      stage_key: toStringValue(merged.stage_key, toStringValue(defaults.stage_key, 'entrada')) as FlowStageKey,
      provider: toStringValue(merged.provider, toStringValue(defaults.provider, 'zapsign')) as SignatureProvider,
      use_rag: toBooleanValue(merged.use_rag, defaults.use_rag ?? true),
      multimodal_enabled: toBooleanValue(merged.multimodal_enabled, defaults.multimodal_enabled ?? false),
      human_review_required: toBooleanValue(merged.human_review_required, defaults.human_review_required ?? false),
      open_in_new_tab: toBooleanValue(merged.open_in_new_tab, defaults.open_in_new_tab ?? true),
      clear_error: toBooleanValue(merged.clear_error, defaults.clear_error ?? true),
      case_sensitive: toBooleanValue(merged.case_sensitive, defaults.case_sensitive ?? false),
      first_level_only: toBooleanValue(merged.first_level_only, defaults.first_level_only ?? true),
      ...(scheduledSequence ? { is_sequence: true, sequence_stage: true, sequence: scheduledSequence } : {}),
    },
  };
}

export function normalizeFlowNodes(nodes: unknown[] | undefined | null): Array<Node<FlowEditorNodeData>> {
  if (!Array.isArray(nodes) || nodes.length === 0) {
    return [
      {
        id: 'trigger-1',
        type: 'trigger',
        position: { x: 250, y: 50 },
        data: getDefaultNodeData('trigger'),
      },
    ];
  }

  return nodes
    .filter(isRecord)
    .map((node) => normalizeFlowNode(node));
}

function pruneConfig(config: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(config).filter(([, value]) => {
      if (value === undefined || value === null) return false;
      if (typeof value === 'string') return value.trim() !== '';
      if (Array.isArray(value)) return value.length > 0;
      return true;
    }),
  );
}

export function serializeFlowNode(node: Node<FlowEditorNodeData>): Record<string, unknown> {
  const data = isRecord(node.data) ? (node.data as FlowEditorNodeData) : getDefaultNodeData(node.type ?? 'trigger');
  const { label, ...config } = data;
  const serializedConfig = pruneConfig({
    ...config,
    event: toStringValue(data.trigger_type ?? data.event, ''),
    trigger_type: toStringValue(data.trigger_type ?? data.event, ''),
    model: toStringValue(data.model, ''),
  });

  if (serializedConfig.model === '') {
    delete serializedConfig.model;
  }

  return {
    id: node.id,
    type: node.type,
    position: node.position,
    label: label || getDefaultNodeData(node.type ?? 'trigger').label,
    config: serializedConfig,
  };
}

export function serializeFlowNodes(nodes: Array<Node<FlowEditorNodeData>>): Array<Record<string, unknown>> {
  return nodes.map((node) => serializeFlowNode(node));
}

function sequenceBindingPayload(message: FlowSequenceMessage): Array<{ variable: string; fieldKey: string }> {
  return (message.bindings ?? [])
    .map((binding, index) => binding.fieldKey.trim()
      ? { variable: `sequence_${index + 1}`, fieldKey: binding.fieldKey.trim() }
      : null)
    .filter((binding): binding is { variable: string; fieldKey: string } => Boolean(binding));
}

/** Meta templates arrive with positional `{{1}}` placeholders, while the
 * executable flow contract validates named `{sequence_1}` placeholders. Keep
 * the body and ordered binding names in lockstep before it reaches the API. */
function executableSequenceMessageBody(body: string): string {
  return body.replace(/\{\{\s*(\d+)\s*\}\}/g, (_match, position: string) => `{sequence_${position}}`);
}

/** Expand the editor-only sequence draft into the executable scheduled-message
 * contract. The runtime does not execute nested `data.sequence` arrays or
 * send_message sequence metadata; each stage must be a scheduled_message node
 * with ordered `{variable, fieldKey}` bindings. */
export function serializeExecutableFlowGraph(
  nodes: Array<Node<FlowEditorNodeData>>,
  edges: Edge[],
  _now = new Date(),
): { nodes: Array<Record<string, unknown>>; edges: Array<Record<string, unknown>> } {
  const expansions = new Map<string, string[]>();
  const executableNodes: Array<Record<string, unknown>> = [];

  nodes.forEach((node) => {
    const data = (node.data ?? {}) as FlowEditorNodeData;
    const sequence = data.sequence;
    const sequenceNode = (node.type === 'send_message' || node.type === 'scheduled_message')
      && Boolean(data.is_sequence || data.sequence_stage)
      && Array.isArray(sequence)
      && sequence.length > 0;
    if (!sequenceNode) {
      executableNodes.push(serializeFlowNode(node));
      return;
    }

    const stageIds = sequence!.length === 1
      ? [node.id]
      : sequence!.map((message, index) => `${node.id}-sequence-${index + 1}`);
    expansions.set(node.id, stageIds);
    let cumulativeHours = 0;
    sequence!.forEach((message, index) => {
      const bindings = sequenceBindingPayload(message);
      const relativeDelay = typeof message.delayValue === 'number' && message.delayValue > 0
        ? message.delayValue
        : undefined;
      const relativeUnit = message.delayUnit ?? 'hours';
      const isDelayedStage = message.scheduleMode === 'delay' || index > 0;
      if (isDelayedStage && relativeDelay !== undefined && sequence!.length > 1) {
        cumulativeHours += relativeDelay * (relativeUnit === 'days' ? 24 : 1);
      }
      const delayValue = isDelayedStage
        ? sequence!.length === 1 ? relativeDelay : cumulativeHours || relativeDelay
        : undefined;
      const delayUnit = isDelayedStage
        ? sequence!.length === 1 ? relativeUnit : 'hours'
        : undefined;
      const config = pruneConfig({
        message: executableSequenceMessageBody(message.body ?? ''),
        sequence_stage: true,
        template_name: message.templateName ?? '',
        template_id: message.templateId ?? undefined,
        template_status: message.status === 'pending' ? 'pending' : message.status === 'approved' ? 'approved' : undefined,
        template_language: message.templateLanguage ?? undefined,
        bindings,
        delay_value: delayValue,
        delay_unit: delayUnit,
        send_now: !isDelayedStage,
        interrupt_on_reply: data.interrupt_on_reply ?? true,
      });
      executableNodes.push({
        id: stageIds[index],
        type: 'scheduled_message',
        position: { x: node.position.x, y: node.position.y + index * 160 },
        label: sequence!.length === 1 ? String(data.label || 'Sequência') : `${String(data.label || 'Sequência')} · ${index + 1}`,
        config,
      });
    });
  });

  const executableEdges: Array<Record<string, unknown>> = serializeFlowEdges(edges).map((edge) => {
    const sourceExpansion = expansions.get(String(edge.source));
    const targetExpansion = expansions.get(String(edge.target));
    return {
      ...edge,
      source: sourceExpansion?.at(-1) ?? edge.source,
      target: targetExpansion?.[0] ?? edge.target,
    };
  });
  expansions.forEach((stageIds, originalId) => {
    stageIds.slice(0, -1).forEach((stageId, index) => {
      executableEdges.push({
        id: `${originalId}-sequence-edge-${index + 1}`,
        source: stageId,
        target: stageIds[index + 1],
        sourceHandle: '',
        targetHandle: '',
        label: '',
        animated: true,
        style: { strokeWidth: 2 },
      });
    });
  });
  return { nodes: executableNodes, edges: executableEdges };
}

export function normalizeFlowEdges(edges: unknown[] | undefined | null): Edge[] {
  if (!Array.isArray(edges)) return [];
  return edges
    .filter(isRecord)
    .map((edge, index) => ({
      id: toStringValue(edge.id, `edge-${index + 1}`),
      source: toStringValue(edge.source),
      target: toStringValue(edge.target),
      sourceHandle: toStringValue(
        edge.sourceHandle
        ?? edge.source_handle
        ?? edge.route_key
        ?? edge.routeKey
        ?? edge.condition_value,
      ),
      targetHandle: toStringValue(edge.targetHandle ?? edge.target_handle),
      label: toStringValue(edge.label),
      animated: true,
      style: { strokeWidth: 2 },
      ...(edge.condition_value ? { condition_value: edge.condition_value } : {}),
      ...(edge.route_key || edge.routeKey ? { route_key: edge.route_key ?? edge.routeKey } : {}),
      ...(typeof edge.priority === 'number' ? { priority: edge.priority } : {}),
      ...(typeof edge.is_fallback === 'boolean' ? { is_fallback: edge.is_fallback } : {}),
      ...(isRecord(edge.filter) ? { filter: edge.filter } : {}),
    })) as Edge[];
}

export function serializeFlowEdges(edges: Edge[]): Array<Record<string, unknown>> {
  return edges.map((edge) => {
    const typedEdge = edge as Edge & { condition_value?: string; route_key?: string; priority?: number; is_fallback?: boolean; filter?: Record<string, unknown> };
    const persistedCondition = typedEdge.condition_value;
    const conditionValue = persistedCondition || (edge.sourceHandle === 'true' || edge.sourceHandle === 'false'
      ? edge.sourceHandle
      : undefined);
    return pruneConfig({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle,
      targetHandle: edge.targetHandle,
      label: edge.label ?? (conditionValue === 'true' ? 'Sim' : conditionValue === 'false' ? 'Não' : undefined),
      condition_value: conditionValue,
      route_key: typedEdge.route_key || edge.sourceHandle || conditionValue,
      priority: typedEdge.priority,
      is_fallback: typedEdge.is_fallback || edge.sourceHandle === 'fallback',
      filter: typedEdge.filter,
    });
  });
}
