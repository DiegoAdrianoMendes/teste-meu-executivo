import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { Node } from '@xyflow/react';
import { Bot, FileSignature, MessageSquare, Plus, Settings2, ShieldCheck, Sparkles, Trash2, X } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { SchemaDrivenNodePanel } from '@/components/flow/SchemaDrivenNodePanel';
import { SequenceBuilder, type SequenceMessage, type SequenceTemplateOption } from '@/components/flow/SequenceBuilder';
import type { KlmFlowNodeCatalogEntry } from '@/klm/api';
import { flowNodeLookups } from '@/services/flow-node-lookups';
import { humanScheduleIsoToLocal, humanScheduleLocalToIso } from '@/lib/flow-human-schedule-time';
import {
  AGENT_OPTIONS,
  CONTEXT_MODE_OPTIONS,
  MODEL_OPTIONS,
  SIGNATURE_PROVIDER_OPTIONS,
  STAGE_OPTIONS,
  type FlowEditorNodeData,
} from '@/lib/flow-editor';

interface NodeConfigPanelProps {
  node: Node<FlowEditorNodeData>;
  /** Runtime descriptor from GET /flows/catalog. */
  catalogNode?: KlmFlowNodeCatalogEntry;
  /** Existing human checkpoints in the same graph. Node IDs are selected,
   * never copied as opaque UUIDs. */
  humanCheckpointTargets?: Array<{ id: string; label: string }>;
  onUpdate: (nodeId: string, newData: FlowEditorNodeData) => void;
  onClose: () => void;
  onEditSequence?: () => void;
  editSequenceHref?: string;
  sequenceTemplates?: SequenceTemplateOption[];
  sequenceBindingFields?: Array<{ fieldKey: string; label?: string; resolvedValue?: string | null }>;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  const controlId = useId();
  const labelId = `${controlId}-label`;
  const labelledControl = isValidElement(children)
    ? cloneElement(children as ReactElement<{ id?: string; 'aria-label'?: string; 'aria-labelledby'?: string }>, {
        id: (children.props as { id?: string }).id ?? controlId,
        ...(!(children.props as { 'aria-label'?: string; 'aria-labelledby'?: string })['aria-label']
          && !(children.props as { 'aria-label'?: string; 'aria-labelledby'?: string })['aria-labelledby']
          ? { 'aria-labelledby': labelId }
          : {}),
      })
    : children;

  return (
    <div className="space-y-1.5">
      <Label id={labelId} htmlFor={controlId} className="text-xs text-muted-foreground">{label}</Label>
      {labelledControl}
      {hint ? <p className="text-xs text-muted-foreground leading-relaxed">{hint}</p> : null}
    </div>
  );
}

function Section({
  title,
  description,
  icon,
  children,
}: {
  title: string;
  description?: string;
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="space-y-3 rounded-xl border bg-background/60 p-4">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          {icon}
          <p className="text-sm font-semibold">{title}</p>
        </div>
        {description ? <p className="text-xs text-muted-foreground leading-relaxed">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

function displayValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === undefined || value === null) return '';
  return JSON.stringify(value);
}

function parseValue(value: string): unknown {
  const normalized = value.trim();
  if (!normalized) return '';
  if (normalized === 'true') return true;
  if (normalized === 'false') return false;
  if (/^-?\d+(\.\d+)?$/.test(normalized)) return Number(normalized);
  if ((normalized.startsWith('[') && normalized.endsWith(']')) || (normalized.startsWith('{') && normalized.endsWith('}'))) {
    try { return JSON.parse(normalized); } catch { return value; }
  }
  return value;
}

function KeyValueEditor({
  value,
  onChange,
  keyPlaceholder = 'campo.destino',
  valuePlaceholder = '{{campo.origem}}',
}: {
  value: Record<string, unknown> | undefined;
  onChange: (next: Record<string, unknown>) => void;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
}) {
  const entries = Object.entries(value ?? {});
  const replaceEntry = (index: number, key: string, nextValue: unknown) => {
    const nextEntries = [...entries];
    nextEntries[index] = [key, nextValue];
    onChange(Object.fromEntries(nextEntries.filter(([entryKey]) => entryKey.trim())));
  };
  return (
    <div className="space-y-2">
      {entries.map(([key, currentValue], index) => (
        <div key={`${key}-${index}`} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_32px] gap-2">
          <Input className="h-9 text-xs" value={key} placeholder={keyPlaceholder} onChange={(event) => replaceEntry(index, event.target.value, currentValue)} />
          <Input className="h-9 text-xs" value={displayValue(currentValue)} placeholder={valuePlaceholder} onChange={(event) => replaceEntry(index, key, parseValue(event.target.value))} />
          <Button type="button" variant="ghost" size="icon" className="h-9 w-8 text-destructive" onClick={() => onChange(Object.fromEntries(entries.filter((_, entryIndex) => entryIndex !== index)))} aria-label="Remover mapeamento"><Trash2 className="h-3.5 w-3.5" /></Button>
        </div>
      ))}
      <Button type="button" variant="outline" size="sm" className="h-8 w-full text-xs" onClick={() => onChange({ ...(value ?? {}), [`campo_${entries.length + 1}`]: '' })}><Plus className="mr-1.5 h-3.5 w-3.5" />Adicionar campo</Button>
    </div>
  );
}

const modeHints = {
  trigger: 'Escolha como esse fluxo começa. Para triggers operacionais, prefira eventos bem objetivos.',
  send_message: 'Escreva a mensagem pronta para o cliente. Use variáveis para personalizar sem duplicar fluxos.',
  wait: 'A pausa é útil para follow-up, janela de resposta e checkpoints antes do próximo passo.',
  condition: 'A condição divide o fluxo. Use campos simples e previsíveis para evitar comportamentos confusos.',
  router: 'O roteador testa regras em ordem e usa uma rota de fallback obrigatória.',
  merge: 'Reconverge caminhos mutuamente exclusivos em uma continuação única.',
  loop: 'Percorre uma coleção com limite explícito para impedir ciclos infinitos.',
  error_handler: 'Recebe uma falha roteada e registra a recuperação antes de continuar.',
  set_variable: 'Cria um valor tipado no contexto, sem executar código arbitrário.',
  transform: 'Mapeia campos com placeholders seguros, por exemplo {{cliente.nome}}.',
  aggregate: 'Resume uma coleção por contagem, soma, primeiro, último ou concatenação.',
  link_action: 'Prepara um link ou botão rastreável para site, agenda, documento, proposta ou compra.',
  human_pause: 'Essa etapa segura o fluxo e entrega uma instrução clara para alguém do time concluir.',
  agent: 'Aqui você decide quem atende, como a IA pensa e quanta autonomia esse nó terá.',
  create_task: 'Cria uma tarefa idempotente e vinculada ao Caso 360.',
  update_card: 'Altera somente campos permitidos do Caso 360 e respeita mudanças manuais mais recentes.',
  internal_note: 'Registra uma nota invisível ao cliente dentro da conversa.',
  audit_event: 'Cria evidência pesquisável para auditoria e observabilidade.',
  payment_request: 'Prepara uma cobrança; a emissão externa depende da conexão e do gate aprovado.',
  social_action: 'Executa uma capacidade oficial do canal social quando ela estiver disponível.',
  connector_action: 'Define uma chamada tipada de API, webhook ou MCP, com credencial e gate externos.',
  zapsign_request: 'Configure assinatura ou uma etapa de integração de forma clara para a operação.',
  scheduled_message: 'Agende uma mensagem com horário fixo quando o atendimento exigir uma cadência controlada.',
} as const;

const SOCIAL_TRIGGER_TYPES = new Set([
  'direct_message',
  'keyword_comment',
  'post_comment',
  'page_comment',
  'story_reply',
  'story_mention',
  'new_follower',
]);
const KEYWORD_TRIGGER_TYPES = new Set(['keyword_comment', 'post_comment', 'page_comment', 'story_reply']);
const COMMENT_TRIGGER_TYPES = new Set(['keyword_comment', 'post_comment', 'page_comment']);
const SOURCE_TRIGGER_TYPES = new Set(['keyword_comment', 'post_comment', 'page_comment', 'story_reply', 'story_mention', 'lead_form']);

const HUMAN_ROLE_OPTIONS = [
  ['workspace_owner', 'Dono do escritório'],
  ['workspace_admin', 'Administração'],
  ['legal_manager', 'Gestão jurídica'],
  ['finance_manager', 'Gestão financeira'],
  ['operator', 'Operação'],
  ['support', 'Suporte'],
] as const;

const HUMAN_SKILL_OPTIONS = [
  ['juridico', 'Jurídico'],
  ['tributario', 'Tributário'],
  ['contabilidade', 'Contabilidade'],
  ['financeiro', 'Financeiro'],
  ['comercial', 'Comercial'],
  ['suporte_tecnico', 'Suporte técnico'],
] as const;

function optionToggle(current: string[], value: string) {
  return current.includes(value) ? current.filter((item) => item !== value) : [...current, value];
}

function HumanNodeConfigPanel({
  node,
  data,
  onUpdate,
  onClose,
  humanCheckpointTargets = [],
}: Pick<NodeConfigPanelProps, 'node' | 'onUpdate' | 'onClose' | 'humanCheckpointTargets'> & { data: FlowEditorNodeData }) {
  const schedule = node.type === 'human_schedule';
  const takeover = node.type === 'human_takeover';
  const escalate = node.type === 'human_escalate';
  const resolve = node.type === 'human_resolve';
  const inputRequest = node.type === 'human_request_input';
  const review = node.type === 'human_review';
  const approval = node.type === 'human_approval';
  const wait = node.type === 'human_wait';
  const assignment = node.type === 'human_assign';
  const notify = node.type === 'human_notify';
  const selectedRoles = Array.isArray(data.required_roles) ? data.required_roles.filter((item): item is string => typeof item === 'string') : ['workspace_owner'];
  const selectedSkills = Array.isArray(data.required_skills) ? data.required_skills.filter((item): item is string => typeof item === 'string') : [];
  const inputFields = Array.isArray((data.options as Record<string, unknown> | undefined)?.inputFields) ? ((data.options as Record<string, unknown>).inputFields as unknown[]).filter((item): item is string => typeof item === 'string') : [];
  const matters = useQuery({ queryKey: ['klm', 'funnel', 'human-schedule'], queryFn: flowNodeLookups.funnel, enabled: schedule, retry: false });
  const threads = useQuery({ queryKey: ['klm', 'threads', 'human-takeover'], queryFn: flowNodeLookups.threads, enabled: takeover, retry: false });
  const update = (next: Record<string, unknown>) => onUpdate(node.id, { ...data, ...next });

  return <div className="flex h-full flex-col" data-testid="human-node-config-panel">
    <div className="flex items-start justify-between gap-3 border-b px-4 py-4">
      <div><p className="text-base font-medium">{schedule ? 'Agendar compromisso humano' : takeover ? 'Assumir atendimento humano' : 'Escalonar decisão humana'}</p><p className="mt-1 text-xs leading-relaxed text-muted-foreground">A etapa cria ou usa um checkpoint humano durável. O fluxo fica pausado até a decisão correta; não há espera invisível nem retomada automática.</p></div>
      <Button type="button" variant="ghost" size="icon" className="h-7 w-7 shrink-0" aria-label="Fechar configuração do nó" onClick={onClose}><X className="h-4 w-4" /></Button>
    </div>
    <div className="flex-1 space-y-4 overflow-y-auto p-4">
      {escalate || resolve ? <Section title={resolve ? 'Decisão a confirmar como resolvida' : 'Decisão a escalar'} description={resolve ? 'Escolha a decisão humana já consumida neste fluxo. O runtime não aceita ID técnico nem resolve uma chamada pendente.' : 'Escolha um checkpoint humano anterior deste mesmo fluxo. O runtime valida que a decisão está pendente e pertence à execução atual.'}>
        <Field label="Checkpoint humano"><Select value={typeof data.target_node_id === 'string' ? data.target_node_id : '__none__'} onValueChange={(value) => update({ target_node_id: value === '__none__' ? '' : value })}><SelectTrigger className="h-10"><SelectValue placeholder="Selecionar checkpoint" /></SelectTrigger><SelectContent><SelectItem value="__none__">Selecionar checkpoint</SelectItem>{humanCheckpointTargets.map((target) => <SelectItem key={target.id} value={target.id}>{target.label}</SelectItem>)}</SelectContent></Select></Field>
        {humanCheckpointTargets.length === 0 ? <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-xs text-muted-foreground">Adicione antes uma etapa de chamada humana neste fluxo. Nenhum ID técnico pode ser digitado aqui.</p> : null}
      </Section> : <>
        <Section title="Roteamento humano" description="A função e a competência são resolvidas pelo servidor; se não houver especialista, a política sobe pela hierarquia até o dono do escritório. A chamada só pausa depois de ser criada e notificada.">
          <Field label="Funções elegíveis" hint="Escolha ao menos uma função. Pessoas e IDs nunca são configurados no fluxo."><div className="flex flex-wrap gap-2">{HUMAN_ROLE_OPTIONS.map(([value, label]) => <Button key={value} type="button" size="sm" variant={selectedRoles.includes(value) ? 'default' : 'outline'} onClick={() => update({ required_roles: optionToggle(selectedRoles, value) })}>{label}</Button>)}</div></Field>
          <Field label="Competências necessárias" hint="Use somente competências reconhecidas para a primeira tentativa; a hierarquia continua até o dono se ninguém estiver disponível."><div className="flex flex-wrap gap-2">{HUMAN_SKILL_OPTIONS.map(([value, label]) => <Button key={value} type="button" size="sm" variant={selectedSkills.includes(value) ? 'default' : 'outline'} onClick={() => update({ required_skills: optionToggle(selectedSkills, value) })}>{label}</Button>)}</div></Field>
          <div className="grid gap-3 sm:grid-cols-3"><Field label="Prioridade"><Select value={typeof data.priority === 'string' ? data.priority : 'normal'} onValueChange={(value) => update({ priority: value })}><SelectTrigger className="h-10"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="low">Baixa</SelectItem><SelectItem value="normal">Normal</SelectItem><SelectItem value="high">Alta</SelectItem><SelectItem value="critical">Crítica</SelectItem></SelectContent></Select></Field><Field label="SLA (minutos)" hint="O runtime persiste em segundos."><Input aria-label="SLA humano em minutos" type="number" min="1" max="10080" className="h-10 text-sm" value={String(Math.max(1, Math.round(Number(data.sla_seconds ?? 3600) / 60)))} onChange={(event) => update({ sla_seconds: Math.max(60, Number(event.target.value || 0) * 60) })} /></Field><Field label="Máximo de escaladas"><Select value={String(data.max_hops ?? 4)} onValueChange={(value) => update({ max_hops: Number(value) })}><SelectTrigger className="h-10"><SelectValue /></SelectTrigger><SelectContent>{Array.from({ length: 8 }, (_, index) => <SelectItem key={index + 1} value={String(index + 1)}>{index + 1} nível{index === 0 ? '' : 'is'}</SelectItem>)}</SelectContent></Select></Field></div>
          <Field label="Motivo da chamada"><Textarea className="min-h-20 text-sm" value={typeof data.reason === 'string' ? data.reason : ''} onChange={(event) => update({ reason: event.target.value })} placeholder="Explique o que a pessoa precisa decidir ou executar" /></Field>
        </Section>
        {inputRequest ? <Section title="Informações solicitadas" description="Defina os campos que o humano deve preencher. A resposta é vinculada a este checkpoint e usada uma única vez."><Field label="Campos solicitados" hint="Digite um nome de campo e confirme. Não há payload JSON nem ID técnico."><div className="flex flex-wrap gap-2">{inputFields.map((field) => <Button key={field} type="button" size="sm" variant="secondary" onClick={() => update({ options: { ...(data.options as Record<string, unknown> ?? {}), inputFields: inputFields.filter((item) => item !== field) } })}>{field} ×</Button>)}<Input aria-label="Novo campo solicitado" className="h-9 max-w-xs text-sm" placeholder="Ex.: documento_comprovante" onKeyDown={(event) => { if (event.key === 'Enter' && event.currentTarget.value.trim()) { event.preventDefault(); const field = event.currentTarget.value.trim(); if (!inputFields.includes(field)) update({ options: { ...(data.options as Record<string, unknown> ?? {}), inputFields: [...inputFields, field] } }); event.currentTarget.value = ''; } }} /></div></Field></Section> : null}
        {review || approval ? <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-xs text-muted-foreground">{review ? 'A revisão humana devolve, aprova ou rejeita o artefato vinculado ao checkpoint.' : 'A aprovação é de uso único e fica vinculada ao intento exato do fluxo.'} Nenhuma ação externa é liberada apenas pela configuração deste nó.</p> : null}
        {wait ? <p className="rounded-md border bg-muted/40 p-3 text-xs text-muted-foreground">A espera é durável, tem SLA e escalonamento finito. O runtime encerra ou escala a chamada em vez de aguardar alguém que não foi chamado.</p> : null}
        {assignment || notify ? <p className="rounded-md border bg-muted/40 p-3 text-xs text-muted-foreground">{assignment ? 'O runtime atribui uma pessoa disponível pela política e cria checkpoint antes de pausar.' : 'O runtime registra a notificação e a evidência de entrega antes de iniciar qualquer espera.'}</p> : null}
        {schedule ? <Section title="Compromisso" description="O responsável roteado vira organizador; o dono recebe acompanhamento. A reunião é criada antes da pausa do fluxo.">
          <Field label="Título"><Input className="h-10 text-sm" value={typeof data.title === 'string' ? data.title : ''} onChange={(event) => update({ title: event.target.value })} placeholder="Ex.: Revisar estratégia do caso" /></Field>
          <div className="grid gap-3 sm:grid-cols-2"><Field label="Início" hint="Salvo com timezone explícito em UTC."><Input aria-label="Início do compromisso" type="datetime-local" className="h-10 text-sm" value={humanScheduleIsoToLocal(data.starts_at)} onChange={(event) => update({ starts_at: humanScheduleLocalToIso(event.target.value) ?? '' })} /></Field><Field label="Fim" hint="Salvo com timezone explícito em UTC."><Input aria-label="Fim do compromisso" type="datetime-local" className="h-10 text-sm" value={humanScheduleIsoToLocal(data.ends_at)} onChange={(event) => update({ ends_at: humanScheduleLocalToIso(event.target.value) ?? '' })} /></Field></div>
          <Field label="Caso vinculado" hint="O caso que disparou o fluxo é usado por padrão; selecione outro caso somente quando necessário."><Select value={typeof data.matter_id === 'string' && data.matter_id ? data.matter_id : '__flow_matter__'} disabled={matters.isPending || matters.isError} onValueChange={(value) => update({ matter_id: value === '__flow_matter__' ? '' : value })}><SelectTrigger className="h-10"><SelectValue placeholder={matters.isPending ? 'Carregando casos…' : 'Caso do fluxo'} /></SelectTrigger><SelectContent><SelectItem value="__flow_matter__">Caso que disparou o fluxo</SelectItem>{matters.data?.cards.map((matter) => <SelectItem key={matter.id} value={matter.id}>{matter.name} · {matter.subject}</SelectItem>)}</SelectContent></Select></Field>
          {matters.isError ? <p className="text-xs text-destructive">Os casos não puderam ser consultados; nenhum ID técnico é aceito como alternativa.</p> : null}
          <div className="grid gap-3 sm:grid-cols-2"><Field label="Local"><Input className="h-10 text-sm" value={typeof data.location === 'string' ? data.location : ''} onChange={(event) => update({ location: event.target.value })} /></Field><Field label="Link da reunião"><Input type="url" className="h-10 text-sm" value={typeof data.meeting_url === 'string' ? data.meeting_url : ''} onChange={(event) => update({ meeting_url: event.target.value })} placeholder="https://…" /></Field></div>
          <Field label="Notas"><Textarea className="min-h-20 text-sm" value={typeof data.notes === 'string' ? data.notes : ''} onChange={(event) => update({ notes: event.target.value })} /></Field>
        </Section> : null}
        {takeover ? <Section title="Conversa a assumir" description="Selecione uma conversa deste workspace. A transferência é auditada e o fluxo aguarda a resolução do responsável.">
          <Field label="Conversa"><Select value={typeof data.thread_id === 'string' && data.thread_id ? data.thread_id : '__none__'} disabled={threads.isPending || threads.isError} onValueChange={(value) => update({ thread_id: value === '__none__' ? '' : value })}><SelectTrigger className="h-10"><SelectValue placeholder={threads.isPending ? 'Carregando conversas…' : 'Selecionar conversa'} /></SelectTrigger><SelectContent><SelectItem value="__none__">Selecionar conversa</SelectItem>{threads.data?.map((thread) => <SelectItem key={thread.id} value={thread.id}>{thread.subject ?? thread.objectName ?? 'Conversa sem assunto'} · {thread.status}</SelectItem>)}</SelectContent></Select></Field>
          {threads.isError ? <p className="text-xs text-destructive">As conversas não puderam ser consultadas; nenhum UUID pode ser digitado como alternativa.</p> : null}
        </Section> : null}
      </>}
      <p className="rounded-md border bg-muted/40 p-3 text-xs text-muted-foreground">Depois de salvar, valide e publique o fluxo. O runtime revalida permissões, tenant, destinatário, disponibilidade e checkpoint antes de executar.</p>
    </div>
  </div>;
}

const FINANCE_READ_OPERATIONS = [
  ['analytics', 'KPIs executivos'],
  ['drilldown', 'Explorar lançamentos e alocações'],
  ['dre_cash', 'DRE de caixa'],
  ['dre_accrual', 'DRE por competência'],
  ['forecast', 'Projeções'],
  ['valuation', 'Valuation'],
  ['tax_reform', 'Reforma tributária'],
] as const;

const FINANCE_DRAFT_OPERATIONS = [
  ['allocation_draft', 'Rascunho de alocação'],
  ['collection_draft', 'Rascunho de cobrança'],
] as const;

function FinanceNodeConfigPanel({
  node,
  data,
  onUpdate,
  onClose,
}: Pick<NodeConfigPanelProps, 'node' | 'onUpdate' | 'onClose'> & { data: FlowEditorNodeData }) {
  const read = node.type === 'finance_read';
  const operation = typeof data.operation === 'string' ? data.operation : read ? 'analytics' : 'allocation_draft';
  const update = (next: Record<string, unknown>) => onUpdate(node.id, { ...data, ...next });
  const costCenters = useQuery({ queryKey: ['klm', 'finance-cost-centers', 'flow-node'], queryFn: flowNodeLookups.financeCostCenters, retry: false });
  const clients = useQuery({ queryKey: ['klm', 'finance-lookup', 'client', 'flow-node'], queryFn: () => flowNodeLookups.finance('client', { limit: 100 }), retry: false });
  const contracts = useQuery({ queryKey: ['klm', 'finance-lookup', 'contract', 'flow-node'], queryFn: () => flowNodeLookups.finance('contract', { limit: 100 }), retry: false });
  const matters = useQuery({ queryKey: ['klm', 'finance-lookup', 'matter', 'flow-node'], queryFn: () => flowNodeLookups.finance('matter', { limit: 100 }), retry: false });
  const parameters = (data.parameters && typeof data.parameters === 'object' && !Array.isArray(data.parameters)) ? data.parameters as Record<string, unknown> : {};
  const selectedRoles = Array.isArray(data.required_roles) ? data.required_roles.filter((item): item is string => typeof item === 'string') : ['finance_manager'];
  const selectValue = (value: unknown) => typeof value === 'string' && value ? value : '__none__';
  const setLookup = (key: 'cost_center_id' | 'client_id' | 'contract_id' | 'matter_id', value: string) => update({ [key]: value === '__none__' ? '' : value });
  const lookupError = costCenters.isError || clients.isError || contracts.isError || matters.isError;

  return <div className="flex h-full flex-col" data-testid="finance-node-config-panel">
    <div className="flex items-start justify-between gap-3 border-b px-4 py-4"><div><p className="text-base font-medium">{read ? 'Consulta financeira governada' : 'Rascunho financeiro governado'}</p><p className="mt-1 text-xs leading-relaxed text-muted-foreground">Use dados reais deste workspace. O fluxo só prepara consulta ou rascunho; pagamento, cobrança externa e orientação tributária continuam sob gate humano.</p></div><Button type="button" variant="ghost" size="icon" className="h-7 w-7 shrink-0" aria-label="Fechar configuração do nó" onClick={onClose}><X className="h-4 w-4" /></Button></div>
    <div className="flex-1 space-y-4 overflow-y-auto p-4">
      <Section title={read ? 'Consulta' : 'Rascunho'} description={read ? 'Escolha o relatório ou a análise. Consultas fiscais encaminham automaticamente para revisão humana.' : 'O lançamento fica pendente e sempre é revisado por humano antes de qualquer efeito.'}>
        <Field label="Operação financeira"><Select value={operation} onValueChange={(value) => update({ operation: value })}><SelectTrigger className="h-10"><SelectValue /></SelectTrigger><SelectContent>{(read ? FINANCE_READ_OPERATIONS : FINANCE_DRAFT_OPERATIONS).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></Field>
      </Section>
      {(operation === 'drilldown' || !read) ? <Section title="Contexto financeiro" description="Seletores pesquisáveis do workspace; não há UUID copiável. Cada vínculo é revalidado pelo backend.">
        <div className="grid gap-3 sm:grid-cols-2"><Field label="Centro de custo"><Select value={selectValue(data.cost_center_id)} disabled={costCenters.isPending || costCenters.isError} onValueChange={(value) => setLookup('cost_center_id', value)}><SelectTrigger className="h-10"><SelectValue placeholder="Todos os centros" /></SelectTrigger><SelectContent><SelectItem value="__none__">Todos os centros</SelectItem>{costCenters.data?.filter((item) => item.status === 'active').map((item) => <SelectItem key={item.id} value={item.id}>{item.code} · {item.name}</SelectItem>)}</SelectContent></Select></Field><Field label="Cliente"><Select value={selectValue(data.client_id)} disabled={clients.isPending || clients.isError} onValueChange={(value) => setLookup('client_id', value)}><SelectTrigger className="h-10"><SelectValue placeholder="Todos os clientes" /></SelectTrigger><SelectContent><SelectItem value="__none__">Todos os clientes</SelectItem>{clients.data?.items.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectContent></Select></Field><Field label="Contrato"><Select value={selectValue(data.contract_id)} disabled={contracts.isPending || contracts.isError} onValueChange={(value) => setLookup('contract_id', value)}><SelectTrigger className="h-10"><SelectValue placeholder="Todos os contratos" /></SelectTrigger><SelectContent><SelectItem value="__none__">Todos os contratos</SelectItem>{contracts.data?.items.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectContent></Select></Field><Field label="Caso"><Select value={selectValue(data.matter_id)} disabled={matters.isPending || matters.isError} onValueChange={(value) => setLookup('matter_id', value)}><SelectTrigger className="h-10"><SelectValue placeholder="Todos os casos" /></SelectTrigger><SelectContent><SelectItem value="__none__">Todos os casos</SelectItem>{matters.data?.items.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectContent></Select></Field></div>
        {lookupError ? <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">Os vínculos financeiros não puderam ser carregados. Nenhum campo técnico é exibido como alternativa.</p> : null}
      </Section> : null}
      {!read ? <><Section title="Dados do rascunho" description="O valor é obrigatório e permanece pendente. Não use este nó para confirmar baixa, pagamento ou envio externo."><div className="grid gap-3 sm:grid-cols-2"><Field label="Valor"><Input aria-label="Valor do rascunho financeiro" type="number" min="0.01" step="0.01" className="h-10 text-sm" value={typeof parameters.amount === 'string' || typeof parameters.amount === 'number' ? String(parameters.amount) : ''} onChange={(event) => update({ parameters: { ...parameters, amount: event.target.value } })} /></Field><Field label="Tipo"><Select value={typeof parameters.entry_type === 'string' ? parameters.entry_type : 'expense'} onValueChange={(value) => update({ parameters: { ...parameters, entry_type: value } })}><SelectTrigger className="h-10"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="expense">Despesa</SelectItem><SelectItem value="receivable">Recebível</SelectItem><SelectItem value="refund">Estorno</SelectItem></SelectContent></Select></Field></div><Field label="Título"><Input aria-label="Título do rascunho financeiro" className="h-10 text-sm" value={typeof parameters.title === 'string' ? parameters.title : ''} onChange={(event) => update({ parameters: { ...parameters, title: event.target.value } })} placeholder="Ex.: Honorários de agosto" /></Field><Field label="Descrição"><Textarea value={typeof parameters.description === 'string' ? parameters.description : ''} onChange={(event) => update({ parameters: { ...parameters, description: event.target.value } })} /></Field><Field label="Vencimento"><Input aria-label="Vencimento do rascunho financeiro" type="datetime-local" className="h-10 text-sm" value={typeof parameters.due_at === 'string' ? humanScheduleIsoToLocal(parameters.due_at) : ''} onChange={(event) => update({ parameters: { ...parameters, due_at: humanScheduleLocalToIso(event.target.value) ?? '' } })} /></Field></Section>
        <Section title="Revisão humana obrigatória" description="O responsável é escolhido por função disponível; se ninguém responder, a rota sobe de modo finito até o dono do escritório."><Field label="Funções elegíveis"><div className="flex flex-wrap gap-2">{HUMAN_ROLE_OPTIONS.map(([value, label]) => <Button key={value} type="button" size="sm" variant={selectedRoles.includes(value) ? 'default' : 'outline'} onClick={() => update({ required_roles: optionToggle(selectedRoles, value) })}>{label}</Button>)}</div></Field><Field label="Motivo da revisão"><Textarea value={typeof data.reason === 'string' ? data.reason : 'Revisar rascunho financeiro antes de qualquer efeito.'} onChange={(event) => update({ reason: event.target.value })} /></Field></Section></> : null}
      {operation === 'tax_reform' ? <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-xs text-muted-foreground">A simulação tributária sempre chama contador/gestão financeira e avisa o dono do escritório. O fluxo não entrega recomendação fiscal sem a decisão humana.</p> : null}
      <p className="rounded-md border bg-muted/40 p-3 text-xs text-muted-foreground">Após salvar, compile e simule o fluxo. O runtime verifica tenant, entitlement, permissões, vínculos financeiros e o gate humano antes da execução.</p>
    </div>
  </div>;
}

export function NodeConfigPanel({ node, catalogNode, onUpdate, onClose, humanCheckpointTargets, onEditSequence, editSequenceHref, sequenceTemplates, sequenceBindingFields }: NodeConfigPanelProps) {
  const data = (node.data ?? {}) as FlowEditorNodeData;
  const update = (key: keyof FlowEditorNodeData, value: unknown) => onUpdate(node.id, { ...data, [key]: value });
  const stageLabel = STAGE_OPTIONS.find((option) => option.value === data.stage_key)?.label;
  const providerLabel = SIGNATURE_PROVIDER_OPTIONS.find((option) => option.value === data.provider)?.label;

  // A partir do contrato v2 o runtime é dono do formulário. Não usamos um
  // fallback manual para estes tipos: schema e UI schema chegam junto com o
  // nó que o worker reconhece, e o save continua pelo mesmo onUpdate do
  // editor legado.
  if (catalogNode?.schema && ['human_assign', 'human_request_input', 'human_review', 'human_approval', 'human_takeover', 'human_notify', 'human_escalate', 'human_schedule', 'human_wait', 'human_resolve'].includes(node.type ?? '')) {
    return <HumanNodeConfigPanel node={node} data={data} onUpdate={onUpdate} onClose={onClose} humanCheckpointTargets={humanCheckpointTargets} />;
  }

  if (catalogNode?.schema && ['finance_read', 'finance_draft'].includes(node.type ?? '')) {
    return <FinanceNodeConfigPanel node={node} data={data} onUpdate={onUpdate} onClose={onClose} />;
  }

  if (catalogNode?.schema) {
    return (
      <SchemaDrivenNodePanel
        schema={catalogNode.schema}
        uischema={catalogNode.uischema as never}
        data={data}
        title={catalogNode.label}
        description={catalogNode.description}
        badges={(
          <>
            <Badge variant="outline" className="text-xs capitalize">{catalogNode.category}</Badge>
            <Badge variant="outline" className="text-xs">v{catalogNode.config_version}</Badge>
            {catalogNode.execution_mode === 'gated_external' ? <Badge variant="secondary" className="text-xs">requer conexão</Badge> : null}
          </>
        )}
        onClose={onClose}
        onChange={(nextConfig) => onUpdate(node.id, { ...data, ...nextConfig })}
      />
    );
  }

  const renderConfig = () => {
    switch (node.type) {
      case 'trigger':
        return (
          <Section
            title="Entrada do fluxo"
            description={modeHints.trigger}
            icon={<Sparkles className="h-4 w-4 text-yellow-500" />}
          >
            <Field label="Evento que inicia o fluxo">
              <Select value={data.trigger_type ?? 'new_lead'} onValueChange={(value) => {
                onUpdate(node.id, { ...data, trigger_type: value, event: value });
              }}>
                <SelectTrigger className="h-9 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="new_lead">Novo lead</SelectItem>
                  <SelectItem value="column_moved">Card movido de coluna</SelectItem>
                  <SelectItem value="message_received">Mensagem recebida</SelectItem>
                  <SelectItem value="keyword_comment">Comentário com palavra-chave</SelectItem>
                  <SelectItem value="post_comment">Comentário em post ou reel</SelectItem>
                  <SelectItem value="page_comment">Comentário no Facebook</SelectItem>
                  <SelectItem value="direct_message">Mensagem direta social</SelectItem>
                  <SelectItem value="story_reply">Resposta ou reação ao story</SelectItem>
                  <SelectItem value="story_mention">Menção no story</SelectItem>
                  <SelectItem value="new_follower">Novo seguidor, se a conexão oferecer evento</SelectItem>
                  <SelectItem value="lead_form">Lead de formulário ou anúncio</SelectItem>
                  <SelectItem value="website_form">Formulário do site</SelectItem>
                  <SelectItem value="meeting_booked">Reunião agendada</SelectItem>
                  <SelectItem value="payment_received">Pagamento confirmado</SelectItem>
                  <SelectItem value="signature_complete">Assinatura concluída</SelectItem>
                  <SelectItem value="scheduled">Agendado</SelectItem>
                  <SelectItem value="manual">Manual</SelectItem>
                </SelectContent>
              </Select>
            </Field>

            {(data.trigger_type === 'column_moved' || data.trigger_type === 'manual') && (
              <Field label="Coluna ou etapa relevante" hint="Opcional. Ajuda o time a entender o contexto que dispara esse fluxo.">
                <Select value={data.column_id ?? '__none__'} onValueChange={(value) => update('column_id', value === '__none__' ? '' : value)}>
                  <SelectTrigger className="h-9 text-sm">
                    <SelectValue placeholder="Selecionar etapa" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Sem etapa específica</SelectItem>
                    {STAGE_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            )}

            {KEYWORD_TRIGGER_TYPES.has(data.trigger_type ?? '') && (
              <div className="space-y-3 rounded-lg border p-3">
                <Field label="Palavras que ativam" hint="Separe por vírgula; vazio significa qualquer comentário ou resposta.">
                  <Input className="h-9 text-sm" value={(data.keywords ?? []).join(', ')} onChange={(event) => update('keywords', event.target.value.split(',').map((item) => item.trim()).filter(Boolean))} placeholder="medicamento, cirurgia, negativa" />
                </Field>
                <Field label="Palavras que bloqueiam">
                  <Input className="h-9 text-sm" value={(data.excluded_keywords ?? []).join(', ')} onChange={(event) => update('excluded_keywords', event.target.value.split(',').map((item) => item.trim()).filter(Boolean))} placeholder="emprego, currículo" />
                </Field>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Forma de comparação">
                    <Select value={data.keyword_mode ?? 'contains'} onValueChange={(value) => update('keyword_mode', value)}>
                      <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="contains">Contém</SelectItem>
                        <SelectItem value="exact">Texto exato</SelectItem>
                        <SelectItem value="starts_with">Começa com</SelectItem>
                        <SelectItem value="ends_with">Termina com</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                  <Field label="Lista de palavras">
                    <Select value={data.match_mode ?? 'any'} onValueChange={(value) => update('match_mode', value)}>
                      <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="any">Qualquer palavra</SelectItem>
                        <SelectItem value="all">Todas as palavras</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                </div>
                <div className="grid gap-2">
                  <label className="flex items-center justify-between rounded-md border p-2.5"><span className="text-xs">Diferenciar maiúsculas</span><Switch checked={data.case_sensitive ?? false} onCheckedChange={(checked) => update('case_sensitive', checked)} /></label>
                  {COMMENT_TRIGGER_TYPES.has(data.trigger_type ?? '') ? <label className="flex items-center justify-between rounded-md border p-2.5"><span className="text-xs">Somente comentário principal</span><Switch checked={data.first_level_only ?? true} onCheckedChange={(checked) => update('first_level_only', checked)} /></label> : null}
                </div>
              </div>
            )}

            {SOURCE_TRIGGER_TYPES.has(data.trigger_type ?? '') && (
              <Field label="Posts, reels, stories ou anúncios específicos" hint="IDs separados por vírgula. Vazio aceita todas as fontes elegíveis.">
                <Input className="h-9 text-sm" value={(data.source_ids ?? []).join(', ')} onChange={(event) => update('source_ids', event.target.value.split(',').map((item) => item.trim()).filter(Boolean))} placeholder="1789..., 1802..." />
              </Field>
            )}

            {SOCIAL_TRIGGER_TYPES.has(data.trigger_type ?? '') && (
              <div className="space-y-3 rounded-lg border p-3">
                <div>
                  <p className="text-xs font-medium">Sinais do perfil social</p>
                  <p className="mt-1 text-xs leading-relaxed text-muted-foreground">A consulta usa o perfil oficial após uma interação elegível. Se o provedor não entregar o dado, o filtro fica desconhecido e a rota não executa.</p>
                </div>
                <Field label="Relação com o perfil" hint="O fluxo só avalia esse filtro quando o canal oficial entrega o dado; status desconhecido não vira 'não segue'.">
                  <Select value={data.follower_status ?? 'any'} onValueChange={(value) => update('follower_status', value)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="any">Qualquer pessoa</SelectItem><SelectItem value="follows">Já segue o perfil</SelectItem><SelectItem value="not_follows">Ainda não segue</SelectItem></SelectContent></Select>
                </Field>
                <Field label="Verificação do perfil">
                  <Select value={data.verified_status ?? 'any'} onValueChange={(value) => update('verified_status', value)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="any">Qualquer perfil</SelectItem><SelectItem value="verified">Somente verificado</SelectItem><SelectItem value="not_verified">Somente não verificado</SelectItem></SelectContent></Select>
                </Field>
                <Field label="Janela oficial de mensagem">
                  <Select value={data.messaging_window ?? 'any'} onValueChange={(value) => update('messaging_window', value)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="any">Qualquer estado</SelectItem><SelectItem value="open">Janela aberta</SelectItem><SelectItem value="closed">Janela fechada</SelectItem></SelectContent></Select>
                </Field>
                {data.trigger_type === 'new_follower' ? <p className="rounded-md border border-amber-200 bg-amber-50 p-2.5 text-xs leading-relaxed text-amber-900">Este gatilho permanece bloqueado até a conexão declarar <strong>evento oficial de novo seguidor</strong>. A consulta de status após DM não é usada como falso webhook.</p> : null}
              </div>
            )}
          </Section>
        );

      case 'send_message':
      case 'scheduled_message':
        return (
          <>
            <Section
              title={node.type === 'scheduled_message' ? 'Mensagem agendada' : 'Mensagem ao cliente'}
              description={modeHints[node.type]}
              icon={<MessageSquare className="h-4 w-4 text-blue-500" />}
            >
              <Field label="Texto da mensagem" hint="Use variáveis como {nome}, {prazo}, {area} e {sign_url}.">
                <Textarea
                  className="text-sm min-h-[160px] resize-none"
                  placeholder="Olá {nome}, vou te ajudar com os próximos passos..."
                  value={data.message ?? ''}
                  onChange={(event) => update('message', event.target.value)}
                />
              </Field>

              {node.type === 'scheduled_message' && (
                <Field label="Horário de envio" hint="Formato HH:MM. Ex.: 09:00.">
                  <Input
                    className="h-9 text-sm"
                    placeholder="09:00"
                    value={data.scheduled_at ?? ''}
                    onChange={(event) => update('scheduled_at', event.target.value)}
                  />
                </Field>
              )}
            </Section>
            {(node.type === 'send_message' || node.type === 'scheduled_message') && data.is_sequence ? (
              <SequenceBuilder
                value={data.sequence as SequenceMessage[] | undefined}
                templates={sequenceTemplates}
                availableBindingFields={sequenceBindingFields}
                interruptOnReply={data.interrupt_on_reply ?? true}
                onChange={(messages, interruptOnReply) => onUpdate(node.id, { ...data, sequence: messages, is_sequence: true, sequence_stage: true, interrupt_on_reply: interruptOnReply })}
                onEditSequence={onEditSequence}
                editSequenceHref={editSequenceHref}
              />
            ) : node.type === 'send_message' || node.type === 'scheduled_message' ? (
              <Button type="button" variant="outline" className="w-full text-xs" onClick={() => onUpdate(node.id, { ...data, is_sequence: true, sequence_stage: true, interrupt_on_reply: true, sequence: [{ id: `${node.id}-message-1`, body: data.message ?? '', status: 'approved', windowStatus: 'unverified', bindings: [] }] })}>
                <Plus className="mr-1.5 h-3.5 w-3.5" />Configurar sequência
              </Button>
            ) : null}
          </>
        );

      case 'wait':
        return (
          <Section
            title="Espera e follow-up"
            description={modeHints.wait}
            icon={<Sparkles className="h-4 w-4 text-slate-500" />}
          >
            <Field label="Tempo de espera (horas)">
              <Input
                type="number"
                min={0}
                className="h-9 text-sm"
                value={String(data.delay_hours ?? 24)}
                onChange={(event) => update('delay_hours', Number(event.target.value))}
              />
            </Field>
            <Field label="Condição para seguir">
              <Select value={data.condition ?? 'always'} onValueChange={(value) => update('condition', value)}>
                <SelectTrigger className="h-9 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="always">Sempre</SelectItem>
                  <SelectItem value="no_response">Sem resposta</SelectItem>
                  <SelectItem value="docs_missing">Documentos faltando</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </Section>
        );

      case 'condition':
        return (
          <Section
            title="Regra de decisão"
            description={modeHints.condition}
            icon={<ShieldCheck className="h-4 w-4 text-amber-500" />}
          >
            <div className="grid gap-3">
              <Field label="Campo avaliado">
                <Input
                  className="h-9 text-sm"
                  placeholder="Ex.: sdr_score, document_readiness"
                  value={data.field ?? ''}
                  onChange={(event) => update('field', event.target.value)}
                />
              </Field>
              <Field label="Operador">
                <Select value={data.operator ?? 'gte'} onValueChange={(value) => update('operator', value)}>
                  <SelectTrigger className="h-9 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="gte">Maior ou igual</SelectItem>
                    <SelectItem value="lte">Menor ou igual</SelectItem>
                    <SelectItem value="eq">Igual</SelectItem>
                    <SelectItem value="exists">Existe</SelectItem>
                    <SelectItem value="not_empty">Não vazio</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field label="Valor esperado">
                <Input
                  className="h-9 text-sm"
                  placeholder="Ex.: 7, completo, true"
                  value={data.value ?? ''}
                  onChange={(event) => update('value', event.target.value)}
                />
              </Field>
            </div>
          </Section>
        );

      case 'router': {
        const routes = data.routes ?? [];
        return (
          <Section title="Rotas ordenadas" description={modeHints.router} icon={<Settings2 className="h-4 w-4 text-indigo-500" />}>
            <div className="space-y-3">
              {routes.map((route, index) => {
                const isFallback = route.key === (data.fallback_route_key ?? 'fallback');
                const replaceRoute = (patch: Partial<typeof route>) => update('routes', routes.map((item, routeIndex) => routeIndex === index ? { ...item, ...patch } : item));
                return (
                  <div key={`${route.key}-${index}`} className="space-y-2 rounded-lg border p-3">
                    <div className="grid grid-cols-[1fr_1fr_32px] gap-2">
                      <Input className="h-8 text-xs" value={route.key} onChange={(event) => replaceRoute({ key: event.target.value })} placeholder="chave_da_rota" />
                      <Input className="h-8 text-xs" value={route.label ?? ''} onChange={(event) => replaceRoute({ label: event.target.value })} placeholder="Nome visível" />
                      <Button type="button" variant="ghost" size="icon" className="h-8 w-8 text-destructive" disabled={routes.length <= 2} onClick={() => update('routes', routes.filter((_, routeIndex) => routeIndex !== index))}><Trash2 className="h-3.5 w-3.5" /></Button>
                    </div>
                    {!isFallback ? <div className="grid grid-cols-[1fr_120px_1fr] gap-2"><Input className="h-8 text-xs" value={route.field ?? ''} onChange={(event) => replaceRoute({ field: event.target.value })} placeholder="lead_score" /><Select value={route.operator ?? 'eq'} onValueChange={(value) => replaceRoute({ operator: value })}><SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="eq">Igual</SelectItem><SelectItem value="contains">Contém</SelectItem><SelectItem value="gte">Maior/igual</SelectItem><SelectItem value="lte">Menor/igual</SelectItem><SelectItem value="exists">Existe</SelectItem></SelectContent></Select><Input className="h-8 text-xs" value={displayValue(route.value)} onChange={(event) => replaceRoute({ value: parseValue(event.target.value) })} placeholder="valor" /></div> : <p className="text-xs text-muted-foreground">Fallback: recebe tudo que não correspondeu às regras anteriores.</p>}
                  </div>
                );
              })}
              <Button type="button" variant="outline" size="sm" className="h-8 w-full text-xs" onClick={() => update('routes', [...routes.slice(0, -1), { key: `rota_${routes.length}`, label: `Rota ${routes.length}`, field: '', operator: 'eq', value: '' }, ...routes.slice(-1)])}><Plus className="mr-1.5 h-3.5 w-3.5" />Adicionar rota</Button>
            </div>
          </Section>
        );
      }

      case 'merge':
        return <Section title="Reconvergência" description={modeHints.merge}><Field label="Modo"><Select value={data.merge_mode ?? 'exclusive'} onValueChange={(value) => update('merge_mode', value)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="exclusive">Primeira rota que chegar</SelectItem><SelectItem value="all">Aguardar todas as rotas</SelectItem></SelectContent></Select></Field></Section>;

      case 'loop':
        return <Section title="Iteração segura" description={modeHints.loop}><Field label="Coleção no contexto"><Input className="h-9 text-sm" value={data.collection_path ?? ''} onChange={(event) => update('collection_path', event.target.value)} placeholder="documentos" /></Field><div className="grid grid-cols-2 gap-3"><Field label="Variável do item"><Input className="h-9 text-sm" value={data.item_variable ?? 'item'} onChange={(event) => update('item_variable', event.target.value)} /></Field><Field label="Variável do índice"><Input className="h-9 text-sm" value={data.index_variable ?? 'index'} onChange={(event) => update('index_variable', event.target.value)} /></Field></div><Field label="Limite de iterações"><Input type="number" min={1} max={500} className="h-9 text-sm" value={data.max_iterations ?? 100} onChange={(event) => update('max_iterations', Number(event.target.value))} /></Field></Section>;

      case 'error_handler':
        return <Section title="Recuperação" description={modeHints.error_handler}><label className="flex items-center justify-between rounded-lg border p-3"><span><span className="block text-sm font-medium">Limpar erro após registrar</span><span className="block text-xs text-muted-foreground">Mantém uma cópia em lastFlowErrorHandled.</span></span><Switch checked={data.clear_error ?? true} onCheckedChange={(checked) => update('clear_error', checked)} /></label></Section>;

      case 'set_variable':
        return <Section title="Variável tipada" description={modeHints.set_variable}><Field label="Nome / caminho"><Input className="h-9 text-sm" value={data.variable_name ?? ''} onChange={(event) => update('variable_name', event.target.value)} placeholder="lead.qualificado" /></Field><Field label="Valor ou expressão"><Input className="h-9 text-sm" value={displayValue(data.value_expression)} onChange={(event) => update('value_expression', parseValue(event.target.value))} placeholder="{{lead_score}}" /></Field><Field label="Tipo"><Select value={data.value_type ?? 'auto'} onValueChange={(value) => update('value_type', value)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="auto">Automático</SelectItem><SelectItem value="string">Texto</SelectItem><SelectItem value="number">Número</SelectItem><SelectItem value="boolean">Sim/não</SelectItem><SelectItem value="json">JSON</SelectItem></SelectContent></Select></Field></Section>;

      case 'transform':
        return <Section title="Mapeamento declarativo" description={modeHints.transform}><KeyValueEditor value={data.mappings} onChange={(value) => update('mappings', value)} /></Section>;

      case 'aggregate':
        return <Section title="Agregação" description={modeHints.aggregate}><Field label="Coleção de origem"><Input className="h-9 text-sm" value={data.source_path ?? ''} onChange={(event) => update('source_path', event.target.value)} /></Field><Field label="Salvar resultado em"><Input className="h-9 text-sm" value={data.target_variable ?? ''} onChange={(event) => update('target_variable', event.target.value)} /></Field><Field label="Operação"><Select value={data.aggregate_operation ?? 'count'} onValueChange={(value) => update('aggregate_operation', value)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="count">Contar</SelectItem><SelectItem value="sum">Somar</SelectItem><SelectItem value="first">Primeiro</SelectItem><SelectItem value="last">Último</SelectItem><SelectItem value="join">Concatenar</SelectItem></SelectContent></Select></Field>{data.aggregate_operation === 'join' ? <Field label="Separador"><Input className="h-9 text-sm" value={data.separator ?? ', '} onChange={(event) => update('separator', event.target.value)} /></Field> : null}</Section>;

      case 'link_action':
        return <Section title="Link rastreável" description={modeHints.link_action}><Field label="Tipo de destino"><Select value={data.link_kind ?? 'website'} onValueChange={(value) => update('link_kind', value)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="website">Site</SelectItem><SelectItem value="booking">Agenda</SelectItem><SelectItem value="proposal">Proposta</SelectItem><SelectItem value="document">Documento</SelectItem><SelectItem value="checkout">Checkout</SelectItem><SelectItem value="purchase">Compra</SelectItem><SelectItem value="custom">Personalizado</SelectItem></SelectContent></Select></Field><Field label="Texto do botão"><Input className="h-9 text-sm" value={data.link_title ?? ''} onChange={(event) => update('link_title', event.target.value)} /></Field><Field label="URL ou rota interna"><Input className="h-9 text-sm" value={data.url ?? ''} onChange={(event) => update('url', event.target.value)} placeholder="https://... ou /agendar/..." /></Field><label className="flex items-center justify-between rounded-lg border p-3"><span className="text-sm">Abrir em nova guia</span><Switch checked={data.open_in_new_tab ?? true} onCheckedChange={(checked) => update('open_in_new_tab', checked)} /></label></Section>;

      case 'human_pause':
        return (
          <Section
            title="Instrução para o time"
            description={modeHints.human_pause}
            icon={<ShieldCheck className="h-4 w-4 text-orange-500" />}
          >
            <Field label="O que a pessoa precisa fazer aqui">
              <Textarea
                className="text-sm min-h-[120px] resize-none"
                placeholder="Ex.: revisar proposta comercial antes de enviar ao cliente."
                value={data.reason ?? ''}
                onChange={(event) => update('reason', event.target.value)}
              />
            </Field>
          </Section>
        );

      case 'agent':
        return (
          <>
            <Section
              title="Quem atende nesta etapa"
              description={modeHints.agent}
              icon={<Bot className="h-4 w-4 text-violet-500" />}
            >
              <div className="grid gap-3">
                <Field label="Agente responsável">
                  <Select value={data.agent ?? 'klm_sdr'} onValueChange={(value) => update('agent', value)}>
                    <SelectTrigger className="h-9 text-sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {AGENT_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>

                <Field label="Base de política desta etapa" hint="Define o tipo de contexto e o comportamento operacional que esse nó deve herdar.">
                  <Select value={data.stage_key ?? 'entrada'} onValueChange={(value) => update('stage_key', value)}>
                    <SelectTrigger className="h-9 text-sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {STAGE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
              </div>
            </Section>

            <Section
              title="Instruções da IA"
              description="Esse texto sobrescreve o comportamento padrão só neste nó. Use para ajustar tom, objetivo e checklist da etapa."
              icon={<Sparkles className="h-4 w-4 text-primary" />}
            >
              <Field label="Prompt / instruções adicionais">
                <Textarea
                  className="text-sm min-h-[180px] resize-none"
                  placeholder="Ex.: seja mais consultivo, peça um documento por vez, nunca avance sem confirmar CPF e telefone."
                  value={data.prompt ?? ''}
                  onChange={(event) => update('prompt', event.target.value)}
                />
              </Field>
            </Section>

            <Section
              title="Modelo, contexto e governança"
              description="Aqui você equilibra custo, profundidade de contexto e necessidade de revisão humana."
              icon={<ShieldCheck className="h-4 w-4 text-emerald-500" />}
            >
              <div className="grid gap-3">
                <Field label="Modelo preferido">
                  <Select value={data.model ? data.model : '__default__'} onValueChange={(value) => update('model', value === '__default__' ? '' : value)}>
                    <SelectTrigger className="h-9 text-sm">
                      <SelectValue placeholder="Usar padrão do workspace" />
                    </SelectTrigger>
                    <SelectContent>
                      {MODEL_OPTIONS.map((option) => (
                        <SelectItem key={option.value || '__default__'} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>

                <Field label="Quanto contexto a IA recebe">
                  <Select value={data.context_mode ?? 'balanced'} onValueChange={(value) => update('context_mode', value)}>
                    <SelectTrigger className="h-9 text-sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {CONTEXT_MODE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
              </div>

              <div className="grid gap-3">
                <label className="flex items-center justify-between rounded-lg border p-3">
                  <div className="space-y-0.5">
                    <span className="text-sm font-medium">Usar RAG do card</span>
                    <p className="text-xs text-muted-foreground">Consulta memória, conversa e documentos relevantes.</p>
                  </div>
                  <Switch checked={data.use_rag ?? true} onCheckedChange={(checked) => update('use_rag', checked)} />
                </label>

                <label className="flex items-center justify-between rounded-lg border p-3">
                  <div className="space-y-0.5">
                    <span className="text-sm font-medium">Ler mídia e documentos</span>
                    <p className="text-xs text-muted-foreground">Inclui imagem, áudio e arquivo quando houver material útil.</p>
                  </div>
                  <Switch checked={data.multimodal_enabled ?? true} onCheckedChange={(checked) => update('multimodal_enabled', checked)} />
                </label>

                <label className="flex items-center justify-between rounded-lg border p-3">
                  <div className="space-y-0.5">
                    <span className="text-sm font-medium">Exigir revisão humana</span>
                    <p className="text-xs text-muted-foreground">Bom para etapas sensíveis, comerciais ou com impacto alto.</p>
                  </div>
                  <Switch checked={data.human_review_required ?? false} onCheckedChange={(checked) => update('human_review_required', checked)} />
                </label>
              </div>
            </Section>
          </>
        );

      case 'create_task':
        return <Section title="Tarefa do Caso 360" description={modeHints.create_task}><Field label="Título"><Input className="h-9 text-sm" value={data.task_title ?? ''} onChange={(event) => update('task_title', event.target.value)} /></Field><Field label="Descrição"><Textarea className="min-h-[100px] resize-none text-sm" value={data.task_description ?? ''} onChange={(event) => update('task_description', event.target.value)} /></Field><Field label="Prioridade"><Select value={data.task_priority ?? 'media'} onValueChange={(value) => update('task_priority', value)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="baixa">Baixa</SelectItem><SelectItem value="media">Média</SelectItem><SelectItem value="alta">Alta</SelectItem><SelectItem value="urgente">Urgente</SelectItem></SelectContent></Select></Field></Section>;

      case 'update_card':
        return <Section title="Campos do Caso 360" description={modeHints.update_card}><KeyValueEditor value={data.updates} onChange={(value) => update('updates', value)} keyPlaceholder="next_action" valuePlaceholder="Próxima ação" /><p className="text-xs leading-4 text-muted-foreground">Permitidos: coluna, microstatus, prioridade, urgência, tags, origem, demanda, subtema, objeto de saúde e próxima ação. Alterações manuais têm precedência.</p></Section>;

      case 'internal_note':
        return <Section title="Nota invisível ao cliente" description={modeHints.internal_note}><Field label="Conteúdo"><Textarea className="min-h-[140px] resize-none text-sm" value={data.note ?? ''} onChange={(event) => update('note', event.target.value)} placeholder="Use {{variaveis}} para incluir contexto." /></Field></Section>;

      case 'audit_event':
        return <Section title="Evidência de execução" description={modeHints.audit_event}><Field label="Tipo do evento"><Input className="h-9 text-sm" value={data.event_type ?? ''} onChange={(event) => update('event_type', event.target.value)} placeholder="flow_checkpoint" /></Field><Field label="Resumo"><Textarea className="min-h-[100px] resize-none text-sm" value={data.summary ?? ''} onChange={(event) => update('summary', event.target.value)} /></Field></Section>;

      case 'payment_request':
        return <Section title="Cobrança preparada" description={modeHints.payment_request}><Field label="Provedor"><Select value={data.payment_provider ?? 'asaas'} onValueChange={(value) => update('payment_provider', value)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="asaas">Asaas</SelectItem><SelectItem value="stripe">Stripe</SelectItem><SelectItem value="manual">Manual / outro</SelectItem></SelectContent></Select></Field><Field label="Forma"><Select value={data.payment_method ?? 'pix'} onValueChange={(value) => update('payment_method', value)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="pix">PIX</SelectItem><SelectItem value="boleto">Boleto</SelectItem><SelectItem value="credit_card">Cartão</SelectItem><SelectItem value="checkout">Checkout</SelectItem><SelectItem value="any">Cliente escolhe</SelectItem></SelectContent></Select></Field><Field label="Valor ou expressão"><Input className="h-9 text-sm" value={displayValue(data.amount_expression)} onChange={(event) => update('amount_expression', parseValue(event.target.value))} placeholder="{{honorarios.iniciais}}" /></Field><Field label="Vencimento em dias"><Input type="number" min={0} max={3650} className="h-9 text-sm" value={data.due_days ?? 3} onChange={(event) => update('due_days', Number(event.target.value))} /></Field><Field label="Descrição"><Input className="h-9 text-sm" value={data.payment_description ?? ''} onChange={(event) => update('payment_description', event.target.value)} /></Field></Section>;

      case 'social_action':
        return <Section title="Capacidade social oficial" description={modeHints.social_action}><Field label="Ação"><Select value={data.social_action ?? 'private_reply'} onValueChange={(value) => update('social_action', value)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="public_reply">Responder publicamente</SelectItem><SelectItem value="private_reply">Enviar mensagem privada</SelectItem><SelectItem value="comment_like">Curtir comentário</SelectItem><SelectItem value="comment_hide">Ocultar comentário</SelectItem><SelectItem value="comment_delete">Excluir comentário</SelectItem></SelectContent></Select></Field>{['public_reply', 'private_reply'].includes(data.social_action ?? '') ? <Field label="Mensagem"><Textarea className="min-h-[120px] resize-none text-sm" value={data.message ?? ''} onChange={(event) => update('message', event.target.value)} /></Field> : null}<Field label="ID do comentário ou evento"><Input className="h-9 text-sm" value={data.target_external_id ?? ''} onChange={(event) => update('target_external_id', event.target.value)} placeholder="{{social.eventId}}" /></Field></Section>;

      case 'connector_action':
        return <><Section title="Conexão externa tipada" description={modeHints.connector_action}><Field label="Conector"><Select value={data.connector ?? 'webhook'} onValueChange={(value) => update('connector', value)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="webhook">Webhook</SelectItem><SelectItem value="http_api">API HTTP</SelectItem><SelectItem value="mcp">MCP</SelectItem><SelectItem value="google_calendar">Google Agenda</SelectItem><SelectItem value="meta">Meta</SelectItem><SelectItem value="tiktok">TikTok</SelectItem><SelectItem value="asaas">Asaas</SelectItem><SelectItem value="zapsign">ZapSign</SelectItem><SelectItem value="elevenlabs">ElevenLabs</SelectItem></SelectContent></Select></Field><Field label="Operação"><Input className="h-9 text-sm" value={data.operation ?? ''} onChange={(event) => update('operation', event.target.value)} placeholder="create_event, post, tool_name..." /></Field><Field label="Timeout em segundos"><Input type="number" min={1} max={900} className="h-9 text-sm" value={data.timeout_seconds ?? 30} onChange={(event) => update('timeout_seconds', Number(event.target.value))} /></Field></Section><Section title="Entrada"><KeyValueEditor value={data.input_mapping} onChange={(value) => update('input_mapping', value)} /></Section><Section title="Saída"><KeyValueEditor value={data.output_mapping} onChange={(value) => update('output_mapping', value)} /></Section></>;

      case 'zapsign_request':
        return (
          <>
            <Section
              title="Assinatura e integrações"
              description="Deixe claro qual provedor será usado e qual comportamento operacional essa etapa exige."
              icon={<FileSignature className="h-4 w-4 text-emerald-500" />}
            >
              <div className="grid gap-3">
                <Field label="Provedor desta etapa">
                  <Select value={data.provider ?? 'zapsign'} onValueChange={(value) => update('provider', value)}>
                    <SelectTrigger className="h-9 text-sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {SIGNATURE_PROVIDER_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>

                <Field label="Tipo de documento">
                  <Select value={data.doc_type ?? 'procuracao'} onValueChange={(value) => update('doc_type', value)}>
                    <SelectTrigger className="h-9 text-sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="procuracao">Procuração</SelectItem>
                      <SelectItem value="contrato_honorarios">Contrato de honorários</SelectItem>
                      <SelectItem value="termo_adesao">Termo de adesão</SelectItem>
                      <SelectItem value="contrato_completo">Pacote completo</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>

                <Field label="Autenticação da assinatura">
                  <Select value={data.auth_mode ?? 'assinaturaTela-tokenEmail'} onValueChange={(value) => update('auth_mode', value)}>
                    <SelectTrigger className="h-9 text-sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="assinaturaTela-tokenEmail">Assinatura + token por email</SelectItem>
                      <SelectItem value="tokenSms">Token por SMS</SelectItem>
                      <SelectItem value="assinaturaTela">Somente assinatura em tela</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
              </div>
            </Section>

            <Section
              title="Operação da integração"
              description="Ajude o time a entender se a assinatura é totalmente automática ou se precisa de condução manual."
              icon={<Settings2 className="h-4 w-4 text-primary" />}
            >
              <Field label="Nome interno do template ou operação">
                <Input
                  className="h-9 text-sm"
                  placeholder="Ex.: Contrato padrão de onboarding"
                  value={data.template_name ?? ''}
                  onChange={(event) => update('template_name', event.target.value)}
                />
              </Field>
              <Field label="Orientação operacional" hint={providerLabel === 'ZapSign' ? 'Para ZapSign, esse texto serve como contexto para o time e para auditoria.' : 'Para DocuSign/manual, essa orientação ajuda o time a finalizar o passo sem ambiguidade.'}>
                <Textarea
                  className="text-sm min-h-[120px] resize-none"
                  placeholder="Ex.: enviar contrato com assinatura por email e confirmar com o cliente no mesmo dia."
                  value={data.integration_notes ?? ''}
                  onChange={(event) => update('integration_notes', event.target.value)}
                />
              </Field>
            </Section>
          </>
        );

      default:
        return catalogNode ? (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs leading-relaxed text-amber-950">
            O runtime reconhece esta capacidade, mas ainda não publicou um contrato de formulário. A autoria fica bloqueada na paleta para não persistir configuração que o backend não consegue validar.
          </div>
        ) : <p className="text-xs text-muted-foreground">Nenhuma configuração disponível para este nó.</p>;
    }
  };

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-start justify-between gap-3 px-4 py-4 border-b">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Settings2 className="h-4 w-4 text-muted-foreground" />
            <span className="text-base font-medium">Configuração do Nó</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary" className="text-xs">{data.label || 'Sem nome'}</Badge>
            <Badge variant="outline" className="text-xs capitalize">{node.type?.replace('_', ' ')}</Badge>
            {node.type === 'agent' && stageLabel ? <Badge variant="outline" className="text-xs">{stageLabel}</Badge> : null}
            {node.type === 'zapsign_request' && providerLabel ? <Badge variant="outline" className="text-xs">{providerLabel}</Badge> : null}
          </div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-7 w-7 shrink-0"
          aria-label="Fechar configuração do nó"
          title="Fechar configuração do nó"
          onClick={onClose}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        <Section
          title="Nome do nó"
          description="Use um nome operacional que deixe claro o objetivo da etapa ao olhar o canvas."
        >
          <Field label="Título exibido">
            <Input
              className="h-9 text-sm"
              value={data.label ?? ''}
              onChange={(event) => update('label', event.target.value)}
            />
          </Field>
        </Section>

        <div className="rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground leading-relaxed">
          {modeHints[node.type as keyof typeof modeHints] ?? catalogNode?.description ?? 'Configuração governada pelo runtime.'}
        </div>

        <Separator />

        {renderConfig()}

        {!catalogNode && !['trigger', 'condition', 'router', 'merge', 'human_pause'].includes(node.type ?? '') ? (
          <Section title="Em caso de erro" description="A estratégia fica versionada junto com o nó e aparece no trace da execução." icon={<ShieldCheck className="h-4 w-4 text-red-500" />}>
            <Field label="Estratégia">
              <Select value={data.error_strategy ?? 'fail'} onValueChange={(value) => update('error_strategy', value)}><SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="fail">Interromper e alertar</SelectItem><SelectItem value="retry">Tentar novamente</SelectItem><SelectItem value="route">Seguir rota de erro</SelectItem><SelectItem value="wait_human">Pausar para humano</SelectItem></SelectContent></Select>
            </Field>
            {data.error_strategy === 'retry' ? <div className="grid grid-cols-2 gap-3"><Field label="Tentativas"><Input type="number" min={0} max={10} className="h-9 text-sm" value={data.max_retries ?? 3} onChange={(event) => update('max_retries', Number(event.target.value))} /></Field><Field label="Intervalo (segundos)"><Input type="number" min={0} max={86400} className="h-9 text-sm" value={data.retry_delay_seconds ?? 60} onChange={(event) => update('retry_delay_seconds', Number(event.target.value))} /></Field></div> : null}
          </Section>
        ) : null}
      </div>
    </div>
  );
}
