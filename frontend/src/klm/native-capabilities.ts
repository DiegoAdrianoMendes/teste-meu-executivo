import { expireClientSession, getToken, recoverAccessToken } from '@/services/auth';
import { API_BASE } from '@/services/api-origin';
import {
  isKlmNativeDomain,
  type KlmNativeDomain,
} from '@/klm/native-domains';
import { nativeDisplayText, nativeDomainLabels } from '@/klm/native-display';

export type NativePrimitive = string | number | boolean | null;
export type NativeValue = NativePrimitive | NativeValue[] | { [key: string]: NativeValue };
export type NativeRecord = Record<string, NativeValue>;

export type NativeHealthState = 'healthy' | 'degraded' | 'syncing' | 'offline' | 'gated' | 'unknown';
export type NativeAccessState = 'allowed' | 'forbidden' | 'gated';
export type NativeValueFormat =
  | 'text'
  | 'number'
  | 'integer'
  | 'currency'
  | 'percent'
  | 'date'
  | 'datetime'
  | 'boolean'
  | 'status'
  | 'link'
  | 'json';

export interface NativeCapabilityHealth {
  state: NativeHealthState;
  label?: string;
  message?: string;
  lastSyncAt?: string | null;
  latencyMs?: number | null;
}

export interface NativeCapabilityAccess {
  state: NativeAccessState;
  reason?: string;
}

export interface NativeCapabilityKpi {
  id: string;
  label: string;
  value: NativePrimitive;
  displayValue?: string;
  format?: NativeValueFormat;
  delta?: string | number | null;
  trend?: 'up' | 'down' | 'neutral';
  description?: string;
}

export interface NativeCapabilityColumn {
  key: string;
  label: string;
  format?: NativeValueFormat;
  width?: string;
  hiddenOnMobile?: boolean;
}

export interface NativeSectionBase {
  id: string;
  type: string;
  title: string;
  description?: string;
  actionIds?: string[];
}

export interface NativeTableSection extends NativeSectionBase {
  type: 'table';
  columns: NativeCapabilityColumn[];
  rows: NativeRecord[];
  idField?: string;
  searchable?: boolean;
  searchKeys?: string[];
  pageSize?: number;
  rowActionIds?: string[];
}

export interface NativeDetailsSection extends NativeSectionBase {
  type: 'details';
  data: NativeRecord;
  fields?: NativeCapabilityColumn[];
}

export interface NativeListSection extends NativeSectionBase {
  type: 'list';
  items: NativeRecord[];
  idField?: string;
  titleField?: string;
  descriptionField?: string;
  statusField?: string;
  rowActionIds?: string[];
}

export interface NativeTimelineSection extends NativeSectionBase {
  type: 'timeline';
  items: NativeAuditEntry[];
}

export interface NativeChartPoint {
  id: string;
  label: string;
  value: number;
  displayValue?: string;
  format?: NativeValueFormat;
  detail?: string;
}

export interface NativeChartSection extends NativeSectionBase {
  type: 'chart';
  points: NativeChartPoint[];
}

export interface NativeNoticeSection extends NativeSectionBase {
  type: 'notice';
  tone?: 'info' | 'success' | 'warning' | 'danger' | 'neutral';
  body?: string;
}

export interface NativeStructuredSection extends NativeSectionBase {
  type: 'structured';
  data: NativeValue;
}

export type NativeCapabilitySection =
  | NativeTableSection
  | NativeDetailsSection
  | NativeListSection
  | NativeTimelineSection
  | NativeChartSection
  | NativeNoticeSection
  | NativeStructuredSection;

export interface NativeCapabilityTab {
  id: string;
  label: string;
  description?: string;
  count?: number;
  sections: NativeCapabilitySection[];
}

export interface NativeAuditEntry {
  id: string;
  title: string;
  description?: string;
  timestamp?: string | null;
  actor?: string;
  status?: string;
  metadata?: NativeRecord;
}

export interface NativeActionOption {
  value: NativePrimitive;
  label: string;
  description?: string;
}

export interface NativeActionCondition {
  field: string;
  equals?: NativePrimitive;
  notEquals?: NativePrimitive;
}

export interface NativeActionField {
  name: string;
  label: string;
  type:
    | 'text'
    | 'email'
    | 'url'
    | 'password'
    | 'textarea'
    | 'number'
    | 'integer'
    | 'date'
    | 'datetime'
    | 'select'
    | 'multiselect'
    | 'checkbox'
    | 'switch'
    | 'json'
    | 'hidden';
  description?: string;
  placeholder?: string;
  required?: boolean;
  readOnly?: boolean;
  defaultValue?: NativeValue;
  options?: NativeActionOption[];
  min?: number;
  max?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  sourceKey?: string;
  oneOfGroup?: string;
  visibleWhen?: NativeActionCondition;
}

export interface NativeActionGate {
  state: 'open' | 'gated' | 'offline';
  reason?: string;
  allowDryRun?: boolean;
  allowConfigure?: boolean;
}

export interface NativeOptimisticPatch {
  op: 'add' | 'replace' | 'remove';
  path: string;
  value?: NativeValue;
  valueFromField?: string;
}

export interface NativeActionSchema {
  id: string;
  label: string;
  description?: string;
  intent?: 'primary' | 'secondary' | 'danger';
  method?: 'POST' | 'PATCH' | 'DELETE';
  execution?: 'local' | 'external';
  fields: NativeActionField[];
  permission?: {
    allowed: boolean;
    reason?: string;
  };
  gate?: NativeActionGate;
  supportsDryRun?: boolean;
  confirmation?: {
    title?: string;
    body?: string;
    confirmLabel?: string;
  };
  optimisticPatch?: NativeOptimisticPatch[];
}

export interface NativeCapabilityDocument {
  schemaVersion: string;
  domain: {
    id: KlmNativeDomain;
    label: string;
  };
  capability: {
    id: string;
    label: string;
    description?: string;
    category?: string;
  };
  health: NativeCapabilityHealth;
  access: NativeCapabilityAccess;
  version: string | number;
  updatedAt?: string | null;
  kpis: NativeCapabilityKpi[];
  tabs: NativeCapabilityTab[];
  actions: NativeActionSchema[];
  audit: NativeAuditEntry[];
}

export type NativeActionMode = 'execute' | 'dry-run' | 'configure';

export interface NativeCapabilityActionRequest {
  actionId: string;
  values: NativeRecord;
  transientSecrets?: Record<string, string>;
  mode: NativeActionMode;
  version: string | number;
  idempotencyKey: string;
}

export interface NativeCapabilityActionResult {
  status?: string;
  message?: string;
  version?: string | number;
  document?: NativeCapabilityDocument;
  auditEntry?: NativeAuditEntry;
  result?: NativeValue;
  idempotentReplay?: boolean;
  refreshPending?: boolean;
}

export interface NativeCapabilityTransport {
  load(domain: KlmNativeDomain, capability: string, signal?: AbortSignal): Promise<NativeCapabilityDocument>;
  execute(
    domain: KlmNativeDomain,
    capability: string,
    action: NativeActionSchema,
    request: NativeCapabilityActionRequest,
    signal?: AbortSignal,
  ): Promise<NativeCapabilityActionResult>;
}

export class NativeCapabilityError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly detail?: unknown;

  constructor(status: number, message: string, code?: string, detail?: unknown) {
    super(message);
    this.name = 'NativeCapabilityError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

const NATIVE_LABEL_OVERRIDES: Record<string, string> = {
  account_id: 'ID da conta',
  actions: 'Ações disponíveis',
  api_settings: 'Configurações da API',
  attribute_display_name: 'Nome de exibição do atributo',
  attribute_display_type: 'Tipo de exibição do atributo',
  assignment_policy: 'Política de atribuição',
  avatar_url: 'Foto',
  available_name: 'Nome disponível',
  browser_name: 'Navegador',
  browser_version: 'Versão do navegador',
  cache: 'Cache local',
  cache_keys: 'Chaves de cache',
  canned_response: 'Resposta pronta',
  canonical: 'Dados consolidados na plataforma',
  canonical_records: 'Registros na plataforma',
  channel_id: 'ID do canal',
  channel_type: 'Tipo de canal',
  collection: 'Coleção técnica',
  confirmed: 'Confirmado',
  conflicts: 'Conflitos de sincronização',
  card_qualify: 'Qualificar card',
  contact_note: 'Nota do contato',
  created_at: 'Criado em',
  custom_attribute: 'Atributo personalizado',
  date_of_joining: 'Data de entrada',
  device_name: 'Dispositivo',
  direct_message: 'Mensagem direta',
  display_name: 'Nome de exibição',
  external_gate: 'Controle de execução externa',
  greeting_enabled: 'Saudação ativa',
  greeting_message: 'Mensagem de saudação',
  has_marketing_access: 'Acesso ao marketing',
  has_sales_access: 'Acesso comercial',
  help_center: 'Central de ajuda',
  integration_connections: 'Integrações configuradas',
  integration_event: 'Evento de integração',
  inviter_id: 'ID de quem convidou',
  ip_address: 'Endereço IP',
  is_active: 'Ativo',
  last_login: 'Último acesso',
  last_remote_digest: 'Assinatura da última sincronização',
  lead_capture: 'Captura de lead',
  local_message: 'Mensagem local',
  mobile_preferences: 'Preferências móveis',
  message_signature: 'Assinatura da mensagem',
  native_state: 'Estado nativo',
  nativestate: 'Estado nativo',
  notification_rule: 'Regra de notificação',
  native_records: 'Registros locais',
  oauth_app: 'Aplicativo OAuth',
  organization_settings: 'Configurações da organização',
  platform_name: 'Sistema operacional',
  platform_version: 'Versão do sistema operacional',
  personal_access_token: 'Token de acesso pessoal',
  private_note: 'Nota interna',
  refresh: 'Atualizar dados',
  record_count: 'Quantidade de registros',
  recordcount: 'Quantidade de registros',
  records: 'Registros',
  ratings_count: 'Avaliações por nota',
  remote_resources: 'Recursos integrados',
  room_id: 'ID da sala',
  roomid: 'ID da sala',
  runtime_risk: 'Compatibilidade do módulo',
  sections: 'Seções',
  source_of_truth: 'Fonte oficial dos dados',
  tenant_scoped: 'Isolado por ambiente',
  token_name: 'Nome do token',
  total_count: 'Total',
  total_sent_messages_count: 'Pesquisas enviadas',
  thread_id: 'ID da conversa',
  threadid: 'ID da conversa',
  workspace_id: 'ID do ambiente',
  workspaceid: 'ID do ambiente',
  working_hours_enabled: 'Horário de atendimento ativo',
  uid: 'Identificador do usuário',
  ui_settings: 'Preferências da interface',
  username: 'Nome de usuário',
};

const NATIVE_WORD_LABELS: Record<string, string> = {
  account: 'conta', accounts: 'contas', action: 'ação', actions: 'ações', active: 'ativo', address: 'endereço',
  agent: 'agente', agents: 'agentes', ai: 'IA', alert: 'alerta', allowed: 'permitido', analytics: 'análises',
  api: 'API', app: 'aplicativo', approval: 'aprovação', approvals: 'aprovações', assignment: 'atribuição',
  attachments: 'anexos', attribute: 'atributo', audit: 'auditoria', automation: 'automação', available: 'disponível',
  blocked: 'bloqueado', board: 'quadro', body: 'conteúdo', calendar: 'calendário', campaigns: 'campanhas',
  cancelled: 'cancelado', canonical: 'consolidado na plataforma', capacity: 'capacidade', case: 'caso', cases: 'casos',
  category: 'categoria', channel: 'canal', channels: 'canais', chat: 'conversa', chats: 'conversas',
  client: 'cliente', clients: 'clientes', closed: 'fechado', code: 'código', collection: 'coleção',
  columns: 'colunas', command: 'comando', commands: 'comandos', company: 'empresa', config: 'configuração',
  configuration: 'configuração', configured: 'configurado', conflict: 'conflito', conflicts: 'conflitos',
  connections: 'conexões', contact: 'contato', contacts: 'contatos', content: 'conteúdo', context: 'contexto',
  contract: 'contrato', controls: 'controles', conversation: 'conversa', count: 'quantidade', counts: 'quantidades',
  created: 'criado', customer: 'cliente', customers: 'clientes', dashboard: 'painel', data: 'dados', date: 'data',
  deleted: 'excluído', description: 'descrição', details: 'detalhes', digest: 'assinatura', document: 'documento',
  documents: 'documentos', draft: 'rascunho', effect: 'efeito', email: 'e-mail', enabled: 'ativo',
  entries: 'registros', error: 'erro', errors: 'erros', event: 'evento', events: 'eventos', execution: 'execução',
  external: 'externa', field: 'campo', fields: 'campos', file: 'arquivo', files: 'arquivos', filter: 'filtro',
  finance: 'financeiro', format: 'formato', gate: 'controle', gated: 'bloqueado', general: 'geral',
  greeting: 'saudação', group: 'grupo', handoff: 'transferência humana', health: 'saúde do módulo',
  history: 'histórico', hours: 'horário', id: 'ID', inbox: 'caixa de entrada', inboxes: 'caixas de entrada',
  integration: 'integração', integrations: 'integrações', invoice: 'fatura', item: 'item', items: 'itens',
  key: 'chave', kind: 'tipo', label: 'etiqueta', labels: 'etiquetas', last: 'último', lead: 'lead',
  links: 'links', login: 'acesso', matter: 'caso', matters: 'casos', member: 'membro', members: 'membros',
  message: 'mensagem', messages: 'mensagens', metadata: 'metadados', method: 'método', metric: 'métrica',
  mobile: 'móvel', mode: 'modo', model: 'modelo', modules: 'módulos', name: 'nome',
  native: 'nativo', network: 'rede', node: 'etapa', nodes: 'etapas', note: 'nota', notes: 'notas',
  notification: 'notificação', notifications: 'notificações', number: 'número', operations: 'operações',
  opportunity: 'oportunidade', opportunities: 'oportunidades', order: 'pedido', organization: 'organização', output: 'resultado', overview: 'visão geral',
  owner: 'responsável', payments: 'pagamentos', payload: 'dados da operação', people: 'pessoas', permission: 'permissão',
  permissions: 'permissões', person: 'pessoa', phone: 'telefone', pipeline: 'funil', policy: 'política',
  preferences: 'preferências', preview: 'prévia', product: 'produto', profile: 'perfil', properties: 'propriedades',
  proposal: 'proposta', quality: 'qualidade', queue: 'fila', raw: 'dados técnicos',
  rating: 'avaliação', reason: 'motivo', record: 'registro', records: 'registros', remote: 'remoto', report: 'relatório',
  reports: 'relatórios', requirement: 'requisito', requirements: 'requisitos', resources: 'recursos', result: 'resultado',
  revision: 'revisão', risk: 'risco', role: 'perfil de acesso', roles: 'perfis de acesso', room: 'sala', rooms: 'salas',
  routing: 'roteamento', rows: 'linhas', rule: 'regra', runtime: 'execução', schema: 'estrutura', scope: 'escopo',
  section: 'seção', sections: 'seções', security: 'segurança', service: 'serviço', settings: 'configurações',
  size: 'tamanho', snapshot: 'retrato atual', solution: 'solução', state: 'estado',
  status: 'status', steps: 'etapas', summary: 'resumo', system: 'sistema', tag: 'etiqueta', tags: 'etiquetas',
  task: 'tarefa', tasks: 'tarefas', team: 'equipe', teams: 'equipes', tenant: 'workspace', template: 'modelo',
  text: 'texto', thread: 'conversa', threads: 'conversas', ticket: 'chamado', time: 'tempo', timer: 'cronômetro',
  title: 'título', token: 'token', tokens: 'tokens', tool: 'ferramenta', tools: 'ferramentas', total: 'total',
  type: 'tipo', unread: 'não lido', updated: 'atualizado', uri: 'URI', url: 'URL',
  user: 'usuário', username: 'usuário', users: 'usuários', value: 'valor', values: 'valores', version: 'versão',
  webhook: 'webhook', webhooks: 'webhooks', whatsapp: 'WhatsApp', widgets: 'indicadores', workflow: 'fluxo',
  working: 'atendimento', workspace: 'ambiente',
};

const SAFE_SEGMENT = /^[a-z0-9][a-z0-9._-]{0,159}$/i;
const BLOCKED_OBJECT_KEYS = new Set(['__proto__', 'prototype', 'constructor']);

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function string(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function finiteNumber(value: unknown): number | undefined {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function boolean(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function primitive(value: unknown): NativePrimitive {
  return value === null || ['string', 'number', 'boolean'].includes(typeof value)
    ? value as NativePrimitive
    : String(value ?? '');
}

function nativeValue(value: unknown, depth = 0): NativeValue {
  if (depth > 8) return '[profundidade limitada]';
  if (value === null || ['string', 'number', 'boolean'].includes(typeof value)) return value as NativePrimitive;
  if (Array.isArray(value)) return value.slice(0, 1_000).map((item) => nativeValue(item, depth + 1));
  const record = object(value);
  if (!record) return String(value ?? '');
  return Object.fromEntries(
    Object.entries(record)
      .filter(([key]) => !BLOCKED_OBJECT_KEYS.has(key))
      .slice(0, 1_000)
      .map(([key, item]) => [key, nativeValue(item, depth + 1)]),
  );
}

function nativeRecord(value: unknown): NativeRecord {
  const record = object(nativeValue(value));
  return record ? record as NativeRecord : { value: nativeValue(value) };
}

export function humanizeNativeKey(value: string): string {
  const normalized = value
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[._-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  const lookup = normalized.toLowerCase().replaceAll(' ', '_');
  if (NATIVE_LABEL_OVERRIDES[lookup]) return NATIVE_LABEL_OVERRIDES[lookup];
  const translated = normalized
    .split(' ')
    .map((word) => NATIVE_WORD_LABELS[word.toLowerCase()] ?? word)
    .join(' ');
  return translated.replace(/^./, (letter) => letter.toUpperCase());
}

function normalizeHealthState(value: unknown): NativeHealthState {
  const state = string(value).toLowerCase();
  if (['healthy', 'ready', 'ok', 'online', 'synced'].includes(state)) return 'healthy';
  if (['degraded', 'warning', 'stale', 'partial'].includes(state)) return 'degraded';
  if (['syncing', 'loading', 'pending'].includes(state)) return 'syncing';
  if (['offline', 'down', 'unavailable', 'error'].includes(state)) return 'offline';
  if (['gated', 'blocked', 'disabled'].includes(state)) return 'gated';
  return 'unknown';
}

function normalizeAccessState(value: unknown): NativeAccessState {
  const state = string(value).toLowerCase();
  if (['forbidden', 'denied', 'unauthorized'].includes(state)) return 'forbidden';
  if (['gated', 'blocked'].includes(state)) return 'gated';
  return 'allowed';
}

function normalizeFormat(value: unknown): NativeValueFormat | undefined {
  const format = string(value).toLowerCase();
  return ['text', 'number', 'integer', 'currency', 'percent', 'date', 'datetime', 'boolean', 'status', 'link', 'json'].includes(format)
    ? format as NativeValueFormat
    : undefined;
}

function normalizeColumn(value: unknown, index: number): NativeCapabilityColumn | null {
  if (typeof value === 'string') return { key: value, label: humanizeNativeKey(value) };
  const row = object(value);
  if (!row) return null;
  const key = string(row.key ?? row.field ?? row.id);
  if (!key || BLOCKED_OBJECT_KEYS.has(key)) return null;
  return {
    key,
    label: nativeDisplayText(string(row.label ?? row.title, humanizeNativeKey(key))),
    format: normalizeFormat(row.format ?? row.valueType),
    width: string(row.width) || undefined,
    hiddenOnMobile: boolean(row.hiddenOnMobile ?? row.hidden_on_mobile),
  };
}

function inferColumns(rows: NativeRecord[]): NativeCapabilityColumn[] {
  const keys = new Set<string>();
  rows.slice(0, 25).forEach((row) => Object.keys(row).forEach((key) => {
    if (!BLOCKED_OBJECT_KEYS.has(key)) keys.add(key);
  }));
  return [...keys].slice(0, 24).map((key) => ({ key, label: humanizeNativeKey(key) }));
}

function derivedTableSection(id: string, title: string, value: unknown): NativeTableSection {
  const rows = array(value).map(nativeRecord);
  return {
    id,
    type: 'table',
    title,
    columns: inferColumns(rows),
    rows,
    idField: 'id',
    searchable: true,
  };
}

function deriveSectionsFromObject(
  value: unknown,
  prefix: string,
  title: string,
): NativeCapabilitySection[] {
  if (Array.isArray(value)) return [derivedTableSection(prefix, title, value)];
  const data = object(value);
  if (!data) {
    return [{ id: prefix, type: 'details', title, data: { value: nativeValue(value) } }];
  }

  const sections: NativeCapabilitySection[] = [];
  const summary: NativeRecord = {};
  Object.entries(data).slice(0, 40).forEach(([key, item]) => {
    if (BLOCKED_OBJECT_KEYS.has(key)) return;
    const sectionId = `${prefix}-${key}`;
    if (Array.isArray(item)) {
      sections.push(derivedTableSection(sectionId, humanizeNativeKey(key), item));
      return;
    }
    const nested = object(item);
    if (nested) {
      const nestedArrays = Object.entries(nested).filter(([, nestedValue]) => Array.isArray(nestedValue));
      if (nestedArrays.length) {
        nestedArrays.slice(0, 20).forEach(([nestedKey, nestedValue]) => {
          sections.push(derivedTableSection(`${sectionId}-${nestedKey}`, humanizeNativeKey(nestedKey), nestedValue));
        });
        const remaining = Object.fromEntries(Object.entries(nested).filter(([, nestedValue]) => !Array.isArray(nestedValue)));
        if (Object.keys(remaining).length) {
          sections.push({ id: `${sectionId}-summary`, type: 'structured', title: humanizeNativeKey(key), data: nativeValue(remaining) });
        }
      } else {
        sections.push({ id: sectionId, type: 'structured', title: humanizeNativeKey(key), data: nativeValue(nested) });
      }
      return;
    }
    summary[key] = nativeValue(item);
  });
  if (Object.keys(summary).length) sections.unshift({ id: `${prefix}-summary`, type: 'details', title, data: summary });
  return sections.length ? sections : [{ id: prefix, type: 'structured', title, data: nativeValue(data) }];
}

function deriveSnapshotSections(root: Record<string, unknown>, capabilityId: string, title: string): NativeCapabilitySection[] {
  const candidates: Array<[string, unknown]> = [
    ['records', root.records],
    ['content', root.content],
    ['data', root.data],
    ['native-state', root.nativeState ?? root.native_state],
    ['canonical', root.canonical],
    ['configuration', root.configuration],
    ['commands', root.commands],
  ];
  const sections: NativeCapabilitySection[] = [];
  candidates.forEach(([key, value]) => {
    if (value === undefined || value === null) return;
    const sectionTitle = key === 'records' || key === 'content' || key === 'data'
      ? title
      : humanizeNativeKey(key);
    sections.push(...deriveSectionsFromObject(value, `${capabilityId}-${key}`, sectionTitle));
  });
  return sections;
}

function normalizeAuditEntry(value: unknown, index: number): NativeAuditEntry {
  const row = object(value) ?? {};
  const metadata = row.metadata ?? row.meta;
  return {
    id: string(row.id, `audit-${index}`),
    title: nativeDisplayText(string(row.title ?? row.action ?? row.event, `Evento ${index + 1}`)),
    description: nativeDisplayText(string(row.description ?? row.detail ?? row.message)) || undefined,
    timestamp: string(row.timestamp ?? row.createdAt ?? row.created_at) || null,
    actor: string(row.actor ?? row.user ?? row.author) || undefined,
    status: string(row.status) || undefined,
    metadata: object(metadata) ? nativeRecord(metadata) : undefined,
  };
}

function normalizeSection(value: unknown, index: number, tabId: string): NativeCapabilitySection {
  const row = object(value) ?? {};
  const id = string(row.id, `${tabId}-section-${index}`);
  const title = nativeDisplayText(string(row.title ?? row.label, `Dados ${index + 1}`));
  const description = nativeDisplayText(string(row.description)) || undefined;
  const actionIds = array(row.actionIds ?? row.action_ids).map((item) => string(item)).filter(Boolean);
  const requestedType = string(row.type ?? row.kind).toLowerCase();
  const base = { id, title, description, actionIds: actionIds.length ? actionIds : undefined };

  if (['table', 'grid', 'records', 'record_table'].includes(requestedType) || Array.isArray(row.rows ?? row.records)) {
    const rows = array(row.rows ?? row.records ?? row.data).map(nativeRecord);
    const columns = array(row.columns).map(normalizeColumn).filter(Boolean) as NativeCapabilityColumn[];
    return {
      ...base,
      type: 'table',
      columns: columns.length ? columns : inferColumns(rows),
      rows,
      idField: string(row.idField ?? row.id_field) || undefined,
      searchable: row.searchable !== false,
      searchKeys: array(row.searchKeys ?? row.search_keys).map((item) => string(item)).filter(Boolean),
      pageSize: finiteNumber(row.pageSize ?? row.page_size),
      rowActionIds: array(row.rowActionIds ?? row.row_action_ids).map((item) => string(item)).filter(Boolean),
    };
  }

  if (['details', 'detail', 'key_value', 'summary'].includes(requestedType)) {
    const rawFields = array(row.fields);
    const sourceData = nativeRecord(row.data ?? row.value ?? {});
    const fieldData = rawFields.length
      ? Object.fromEntries(rawFields.map((field, fieldIndex) => {
          const entry = object(field) ?? {};
          const key = string(entry.key ?? entry.field, `field-${fieldIndex}`);
          return [
            key,
            entry.value === undefined ? sourceData[key] : nativeValue(entry.value),
          ];
        }))
      : sourceData;
    return {
      ...base,
      type: 'details',
      data: fieldData,
      fields: rawFields.map(normalizeColumn).filter(Boolean) as NativeCapabilityColumn[],
    };
  }

  if (['list', 'queue', 'collection'].includes(requestedType)) {
    return {
      ...base,
      type: 'list',
      items: array(row.items ?? row.data).map(nativeRecord),
      idField: string(row.idField ?? row.id_field, 'id'),
      titleField: string(row.titleField ?? row.title_field, 'title'),
      descriptionField: string(row.descriptionField ?? row.description_field, 'description'),
      statusField: string(row.statusField ?? row.status_field, 'status'),
      rowActionIds: array(row.rowActionIds ?? row.row_action_ids).map((item) => string(item)).filter(Boolean),
    };
  }

  if (['timeline', 'audit', 'history', 'feed'].includes(requestedType)) {
    return { ...base, type: 'timeline', items: array(row.items ?? row.events ?? row.data).map(normalizeAuditEntry) };
  }

  if (['chart', 'series', 'distribution'].includes(requestedType)) {
    const rawPoints = row.points ?? row.series ?? row.data;
    const points = Array.isArray(rawPoints)
      ? rawPoints.map((point, pointIndex) => {
          const item = object(point) ?? {};
          return {
            id: string(item.id, `point-${pointIndex}`),
            label: nativeDisplayText(string(item.label ?? item.name, `Série ${pointIndex + 1}`)),
            value: finiteNumber(item.value ?? item.total) ?? 0,
            displayValue: string(item.displayValue ?? item.display_value) || undefined,
            format: normalizeFormat(item.format),
            detail: nativeDisplayText(string(item.detail ?? item.description)) || undefined,
          };
        })
      : Object.entries(object(rawPoints) ?? {}).map(([key, point]) => ({
          id: key,
          label: humanizeNativeKey(key),
          value: finiteNumber(point) ?? 0,
        }));
    return { ...base, type: 'chart', points };
  }

  if (['notice', 'alert', 'message'].includes(requestedType)) {
    const tone = string(row.tone).toLowerCase();
    return {
      ...base,
      type: 'notice',
      tone: ['info', 'success', 'warning', 'danger', 'neutral'].includes(tone)
        ? tone as NativeNoticeSection['tone']
        : 'info',
      body: nativeDisplayText(string(row.body ?? row.message ?? row.data)) || undefined,
    };
  }

  return { ...base, type: 'structured', data: nativeValue(row.data ?? row.value ?? row) };
}

function normalizeActionField(value: unknown, index: number): NativeActionField | null {
  const row = object(value);
  if (!row) return null;
  const name = string(row.name ?? row.key ?? row.id);
  if (!name || BLOCKED_OBJECT_KEYS.has(name)) return null;
  const requestedType = string(row.type, 'text').toLowerCase();
  const type = ['text', 'email', 'url', 'password', 'textarea', 'number', 'integer', 'date', 'datetime', 'select', 'multiselect', 'checkbox', 'switch', 'json', 'hidden'].includes(requestedType)
    ? requestedType as NativeActionField['type']
    : 'text';
  const condition = object(row.visibleWhen ?? row.visible_when);
  return {
    name,
    label: nativeDisplayText(string(row.label ?? row.title, humanizeNativeKey(name))),
    type,
    description: nativeDisplayText(string(row.description ?? row.helpText ?? row.help_text)) || undefined,
    placeholder: nativeDisplayText(string(row.placeholder)) || undefined,
    required: boolean(row.required),
    readOnly: boolean(row.readOnly ?? row.read_only),
    defaultValue: row.defaultValue !== undefined || row.default_value !== undefined
      ? nativeValue(row.defaultValue ?? row.default_value)
      : undefined,
    options: array(row.options).map((option, optionIndex) => {
      const item = object(option);
      return item
        ? {
            value: primitive(item.value ?? item.id),
            label: nativeDisplayText(string(item.label ?? item.name, `Opção ${optionIndex + 1}`)),
            description: nativeDisplayText(string(item.description)) || undefined,
          }
        : { value: primitive(option), label: String(option) };
    }),
    min: finiteNumber(row.min),
    max: finiteNumber(row.max),
    minLength: finiteNumber(row.minLength ?? row.min_length),
    maxLength: finiteNumber(row.maxLength ?? row.max_length),
    pattern: string(row.pattern) || undefined,
    sourceKey: string(row.sourceKey ?? row.source_key) || undefined,
    oneOfGroup: string(row.oneOfGroup ?? row.one_of_group) || undefined,
    visibleWhen: condition ? {
      field: string(condition.field),
      equals: condition.equals === undefined ? undefined : primitive(condition.equals),
      notEquals: condition.notEquals === undefined && condition.not_equals === undefined
        ? undefined
        : primitive(condition.notEquals ?? condition.not_equals),
    } : undefined,
  };
}

function fieldsFromRequired(required: string[]): NativeActionField[] {
  return required.flatMap<NativeActionField>((rawName): NativeActionField[] => {
    const alternatives = rawName.split('|').map((item) => item.trim()).filter(Boolean);
    if (alternatives.length <= 1) {
      return [{ name: rawName, label: humanizeNativeKey(rawName), type: nativeFieldTypeForName(rawName), required: true }];
    }
    const description = `Preencha ao menos um: ${alternatives.map(humanizeNativeKey).join(' ou ')}.`;
    return alternatives.map((name) => ({
      name,
      label: humanizeNativeKey(name),
      type: nativeFieldTypeForName(name),
      required: false,
      oneOfGroup: rawName,
      description,
    }));
  });
}

function nativeFieldTypeForName(name: string): NativeActionField['type'] {
  const normalized = name.replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase();
  const jsonNames = new Set(['actions', 'aliases', 'attribute_keys', 'changes', 'conditions', 'custom_attributes', 'data', 'graph', 'manifest', 'metadata', 'payload', 'permissions', 'policy', 'user_ids']);
  if (jsonNames.has(normalized) || /_(payload|config|metadata|graph)$/.test(normalized)) return 'json';
  if (['body', 'content', 'description', 'message', 'notes', 'text'].includes(normalized)) return 'textarea';
  if (normalized === 'email' || normalized.endsWith('_email')) return 'email';
  if (normalized === 'password' || normalized.endsWith('_password')) return 'password';
  if (/(url|uri|endpoint)/.test(normalized)) return 'url';
  if (['byte_size', 'capacity', 'size'].includes(normalized) || /(count|days|limit|position|priority)/.test(normalized)) return 'integer';
  if (/^(is|has)_/.test(normalized) || ['active', 'confirm', 'confirm_local_only', 'confirmed', 'enabled', 'private'].includes(normalized)) return 'switch';
  if (normalized.endsWith('_at') || normalized.includes('datetime')) return 'datetime';
  if (normalized.endsWith('date')) return 'date';
  return 'text';
}

function normalizeAction(value: unknown, index: number): NativeActionSchema | null {
  const row = typeof value === 'string' ? { command: value } : object(value);
  if (!row) return null;
  const id = string(row.id ?? row.key ?? row.command);
  if (!id || BLOCKED_OBJECT_KEYS.has(id)) return null;
  const permission = object(row.permission);
  const gate = object(row.gate);
  const confirmation = object(row.confirmation);
  const intent = string(row.intent).toLowerCase();
  const method = string(row.method, 'POST').toUpperCase();
  const execution = string(row.execution ?? row.executionType ?? row.execution_type).toLowerCase();
  const gateState = string(gate?.state).toLowerCase();
  const command = string(row.command, id);
  const rawFields = array(row.fields ?? row.schema);
  const requiredFields = rawFields.length ? [] : array(row.required).map((item) => string(item)).filter(Boolean);
  const external = execution === 'external' || row.externalDispatch === true || string(row.effect).toLowerCase() === 'remote';
  const inferredGated = row.networkAllowed === false || (external && row.enabled === false);
  const inferredDryRun = /dry[-_.]?run|preview/.test(command);
  return {
    id,
    label: nativeDisplayText(string(row.label ?? row.title, humanizeNativeKey(id))),
    description: nativeDisplayText(string(row.description)) || undefined,
    intent: ['primary', 'secondary', 'danger'].includes(intent) ? intent as NativeActionSchema['intent'] : 'secondary',
    method: ['POST', 'PATCH', 'DELETE'].includes(method) ? method as NativeActionSchema['method'] : 'POST',
    execution: external ? 'external' : 'local',
    fields: rawFields.length
      ? rawFields.map(normalizeActionField).filter(Boolean) as NativeActionField[]
      : fieldsFromRequired(requiredFields),
    permission: permission ? {
      allowed: permission.allowed !== false,
      reason: string(permission.reason) || undefined,
    } : {
      allowed: external || row.enabled !== false,
      reason: string(row.disabledReason ?? row.disabled_reason ?? row.reasonCode ?? row.reason_code) || undefined,
    },
    gate: gate || inferredGated ? {
      state: gate && ['open', 'gated', 'offline'].includes(gateState) ? gateState as NativeActionGate['state'] : inferredGated ? 'gated' : 'open',
      reason: string(gate?.reason ?? row.disabledReason ?? row.disabled_reason ?? row.reasonCode ?? row.reason_code) || undefined,
      allowDryRun: boolean(gate?.allowDryRun ?? gate?.allow_dry_run) || inferredDryRun,
      allowConfigure: boolean(gate?.allowConfigure ?? gate?.allow_configure) || /config|setting/.test(command),
    } : undefined,
    supportsDryRun: boolean(row.supportsDryRun ?? row.supports_dry_run) || inferredDryRun,
    confirmation: confirmation ? {
      title: nativeDisplayText(string(confirmation.title)) || undefined,
      body: nativeDisplayText(string(confirmation.body ?? confirmation.description)) || undefined,
      confirmLabel: nativeDisplayText(string(confirmation.confirmLabel ?? confirmation.confirm_label)) || undefined,
    } : boolean(row.requiresConfirmation ?? row.requires_confirmation ?? row.destructive) ? {} : undefined,
    optimisticPatch: array(row.optimisticPatch ?? row.optimistic_patch).map((patch) => {
      const item = object(patch) ?? {};
      const op = string(item.op).toLowerCase();
      return {
        op: ['add', 'replace', 'remove'].includes(op) ? op as NativeOptimisticPatch['op'] : 'replace',
        path: string(item.path),
        value: item.value === undefined ? undefined : nativeValue(item.value),
        valueFromField: string(item.valueFromField ?? item.value_from_field) || undefined,
      };
    }).filter((patch) => patch.path.startsWith('/')),
  };
}

export function normalizeNativeCapabilityDocument(
  input: unknown,
  domainFallback: KlmNativeDomain,
  capabilityFallback: string,
): NativeCapabilityDocument {
  const root = object(input) ?? {};
  const domainData = object(root.domain);
  const capabilityData = object(root.capability);
  const healthData = object(root.health ?? root.status) ?? {};
  const accessData = object(root.access ?? root.permission) ?? {};
  const rawDomainId = string(domainData?.id ?? root.domain);
  const domain = isKlmNativeDomain(rawDomainId) ? rawDomainId : domainFallback;
  const capabilityId = string(capabilityData?.id ?? root.capabilityId ?? root.capability_id ?? root.capabilityKey ?? root.capability_key, capabilityFallback);
  const rawSections = array(root.sections);
  const rawTabs = array(root.tabs);
  const hasRenderableSections = rawSections.some((section) => {
    const item = object(section);
    return Boolean(item && (item.type || item.rows || item.records || item.items || item.data || item.body || item.points || item.series));
  });
  const derivedSections = deriveSnapshotSections(
    root,
    capabilityId,
    string(capabilityData?.label ?? capabilityData?.name ?? root.title, humanizeNativeKey(capabilityId)),
  );
  const tabs = (rawTabs.length ? rawTabs : [{
    id: 'overview',
    label: 'Visão geral',
    sections: hasRenderableSections ? rawSections : derivedSections,
  }])
    .map((tab, tabIndex): NativeCapabilityTab => {
      const item = object(tab) ?? {};
      const id = string(item.id, `tab-${tabIndex}`);
      return {
        id,
        label: nativeDisplayText(string(item.label ?? item.title, humanizeNativeKey(id))),
        description: nativeDisplayText(string(item.description)) || undefined,
        count: finiteNumber(item.count),
        sections: array(item.sections).map((section, sectionIndex) => normalizeSection(section, sectionIndex, id)),
      };
    });
  const viewData = object(root.view) ?? {};
  const viewSummary = object(viewData.summary) ?? {};
  const rawKpis = array(root.kpis ?? root.metrics ?? root.widgets);
  const kpis = [
    ...rawKpis,
    ...(!rawKpis.length ? Object.entries(viewSummary).map(([key, value]) => ({ key, label: humanizeNativeKey(key), value })) : []),
  ];
  const stateData = object(root.state) ?? {};
  const nativeState = object(root.nativeState ?? root.native_state) ?? {};
  const rawActions = array(root.actions).length ? array(root.actions) : array(root.availableCommands ?? root.available_commands);

  return {
    schemaVersion: string(root.schemaVersion ?? root.schema_version, '1.0'),
    domain: {
      id: domain,
      label: nativeDisplayText(string(domainData?.label, nativeDomainLabels[domain])),
    },
    capability: {
      id: capabilityId,
      label: nativeDisplayText(string(capabilityData?.label ?? capabilityData?.name ?? root.title, humanizeNativeKey(capabilityId))),
      description: nativeDisplayText(string(capabilityData?.description ?? root.description)) || undefined,
      category: nativeDisplayText(string(capabilityData?.category ?? root.category)) || undefined,
    },
    health: {
      state: normalizeHealthState(healthData.state ?? healthData.status ?? root.healthState ?? root.health_state ?? root.status ?? object(root.content)?.status),
      label: nativeDisplayText(string(healthData.label)) || undefined,
      message: nativeDisplayText(string(healthData.message ?? healthData.detail)) || undefined,
      lastSyncAt: string(healthData.lastSyncAt ?? healthData.last_sync_at ?? root.lastSyncAt ?? root.last_sync_at) || null,
      latencyMs: finiteNumber(healthData.latencyMs ?? healthData.latency_ms) ?? null,
    },
    access: {
      state: normalizeAccessState(accessData.state ?? (accessData.allowed === false ? 'forbidden' : undefined)),
      reason: string(accessData.reason) || undefined,
    },
    version: primitive(root.version ?? root.revision ?? stateData.configVersion ?? stateData.config_version ?? nativeState.version ?? '0') as string | number,
    updatedAt: string(root.updatedAt ?? root.updated_at) || null,
    kpis: kpis.map((kpi, index) => {
      const item = object(kpi) ?? {};
      const id = string(item.id ?? item.key, `kpi-${index}`);
      const trend = string(item.trend).toLowerCase();
      return {
        id,
        label: nativeDisplayText(string(item.label ?? item.title, humanizeNativeKey(id))),
        value: primitive(item.value),
        displayValue: string(item.displayValue ?? item.display_value) || undefined,
        format: normalizeFormat(item.format),
        delta: item.delta === null || ['string', 'number'].includes(typeof item.delta) ? item.delta as string | number | null : undefined,
        trend: ['up', 'down', 'neutral'].includes(trend) ? trend as NativeCapabilityKpi['trend'] : 'neutral',
        description: nativeDisplayText(string(item.description)) || undefined,
      };
    }),
    tabs,
    actions: rawActions.map(normalizeAction).filter(Boolean) as NativeActionSchema[],
    audit: array(root.audit ?? root.auditTrail ?? root.audit_trail).map(normalizeAuditEntry),
  };
}

export function buildNativeCapabilityPath(domain: KlmNativeDomain, capability: string): string {
  if (!isKlmNativeDomain(domain) || !SAFE_SEGMENT.test(capability)) {
    throw new NativeCapabilityError(400, 'Domínio ou capacidade inválidos.', 'invalid_route');
  }
  return `/api/v1/capabilities/domains/${encodeURIComponent(domain)}/${encodeURIComponent(capability)}/document`;
}

function browserNativeContext(): NativeRecord {
  if (typeof window === 'undefined') return {};
  const source = new URLSearchParams(window.location.search);
  const aliases: Record<string, string> = {
    matter: 'matter_id',
    matterId: 'matter_id',
    thread: 'thread_id',
    threadId: 'thread_id',
    flow: 'flow_id',
    flowId: 'flow_id',
    agent: 'agent_id',
    agentId: 'agent_id',
    workflow: 'workflow_id',
    workflowId: 'workflow_id',
    timeRange: 'time_range',
  };
  const allowed = new Set(['matter_id', 'thread_id', 'flow_id', 'agent_id', 'workflow_id', 'time_range', 'search', 'limit', 'page', 'page_size']);
  const context: NativeRecord = {};
  source.forEach((value, key) => {
    const target = aliases[key] ?? key;
    if (allowed.has(target) && !(target in context)) context[target] = value;
  });
  return context;
}

async function parseResponse(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  return response.json().catch(() => null);
}

function responseError(response: Response, payload: unknown): NativeCapabilityError {
  const data = object(payload) ?? {};
  const detail = object(data.detail);
  const message = string(detail?.message ?? data.message ?? data.detail, response.status === 403
    ? 'Você não possui permissão para acessar esta capacidade.'
    : 'Não foi possível processar a capacidade nativa.');
  return new NativeCapabilityError(
    response.status,
    message,
    string(detail?.code ?? data.code) || undefined,
    payload,
  );
}

async function authenticatedFetch(path: string, init: RequestInit): Promise<Response> {
  const request = (token?: string | null) => fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: init.credentials ?? 'include',
    headers: {
      Accept: 'application/json',
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });

  try {
    let response = await request(getToken());
    if (response.status === 401) {
      const recovered = await recoverAccessToken({ forceRefresh: true });
      if (recovered) response = await request(recovered);
    }
    if (response.status === 401) expireClientSession();
    return response;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new NativeCapabilityError(0, 'O backend local está offline ou inacessível.', 'offline', error);
  }
}

export async function fetchNativeCapability(
  domain: KlmNativeDomain,
  capability: string,
  signal?: AbortSignal,
): Promise<NativeCapabilityDocument> {
  const context = browserNativeContext();
  const query = new URLSearchParams(Object.entries(context).map(([key, value]) => [key, String(value)]));
  const path = `${buildNativeCapabilityPath(domain, capability)}${query.size ? `?${query.toString()}` : ''}`;
  const response = await authenticatedFetch(path, { method: 'GET', signal });
  const payload = await parseResponse(response);
  if (!response.ok) throw responseError(response, payload);
  return normalizeNativeCapabilityDocument(payload, domain, capability);
}

export async function executeNativeCapabilityAction(
  domain: KlmNativeDomain,
  capability: string,
  action: NativeActionSchema,
  request: NativeCapabilityActionRequest,
  signal?: AbortSignal,
): Promise<NativeCapabilityActionResult> {
  const path = buildNativeCapabilityPath(domain, capability).replace(/\/document$/, '/actions');
  const context = browserNativeContext();
  const response = await authenticatedFetch(path, {
    method: action.method ?? 'POST',
    signal,
    headers: {
      'If-Match': String(request.version),
      'Idempotency-Key': request.idempotencyKey,
      'X-KLM-Action-Mode': request.mode,
    },
    body: JSON.stringify({
      actionId: request.actionId,
      values: request.values,
      transientSecrets: request.transientSecrets ?? {},
      mode: request.mode,
      version: request.version,
      idempotencyKey: request.idempotencyKey,
      context,
    }),
  });
  const payload = await parseResponse(response);
  if (!response.ok) throw responseError(response, payload);
  const data = object(payload) ?? {};
  const audit = object(data.auditEntry ?? data.audit_entry);
  return {
    status: string(data.status) || undefined,
    message: string(data.message) || undefined,
    version: data.version === undefined ? undefined : primitive(data.version) as string | number,
    document: data.document == null ? undefined : normalizeNativeCapabilityDocument(data.document, domain, capability),
    auditEntry: audit ? normalizeAuditEntry(audit, 0) : undefined,
    result: data.result === undefined ? undefined : nativeValue(data.result),
    idempotentReplay: boolean(data.idempotentReplay ?? data.idempotent_replay),
    refreshPending: boolean(data.refreshPending ?? data.refresh_pending),
  };
}

export const nativeCapabilityTransport: NativeCapabilityTransport = {
  load: fetchNativeCapability,
  execute: executeNativeCapabilityAction,
};

function pointerSegments(path: string): string[] {
  if (!path.startsWith('/')) return [];
  return path
    .slice(1)
    .split('/')
    .map((segment) => segment.replace(/~1/g, '/').replace(/~0/g, '~'))
    .filter((segment) => segment && !BLOCKED_OBJECT_KEYS.has(segment));
}

export function applyNativeOptimisticPatch(
  document: NativeCapabilityDocument,
  patches: NativeOptimisticPatch[] | undefined,
  values: NativeRecord,
): NativeCapabilityDocument {
  if (!patches?.length) return document;
  const clone = structuredClone(document) as unknown as Record<string, unknown>;
  patches.forEach((patch) => {
    const segments = pointerSegments(patch.path);
    if (!segments.length) return;
    let target: unknown = clone;
    for (const segment of segments.slice(0, -1)) {
      if (Array.isArray(target)) {
        const index = Number(segment);
        if (!Number.isInteger(index) || index < 0 || index >= target.length) return;
        target = target[index];
      } else {
        const record = object(target);
        if (!record || !(segment in record)) return;
        target = record[segment];
      }
    }
    const key = segments[segments.length - 1];
    const nextValue = patch.valueFromField ? values[patch.valueFromField] : patch.value;
    if (Array.isArray(target)) {
      const index = key === '-' ? target.length : Number(key);
      if (!Number.isInteger(index) || index < 0 || index > target.length) return;
      if (patch.op === 'remove') target.splice(index, 1);
      else if (patch.op === 'add') target.splice(index, 0, structuredClone(nextValue));
      else if (index < target.length) target[index] = structuredClone(nextValue);
      return;
    }
    const record = object(target);
    if (!record || BLOCKED_OBJECT_KEYS.has(key)) return;
    if (patch.op === 'remove') delete record[key];
    else record[key] = structuredClone(nextValue);
  });
  return clone as unknown as NativeCapabilityDocument;
}

export function createNativeIdempotencyKey(domain: KlmNativeDomain, capability: string, actionId: string): string {
  const nonce = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `klm-native:${domain}:${capability}:${actionId}:${nonce}`;
}
