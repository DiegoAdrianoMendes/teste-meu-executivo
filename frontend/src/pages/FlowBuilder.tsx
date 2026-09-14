import { useState, useCallback, useRef, useEffect, useMemo, type ReactNode } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  type Connection,
  type Node,
  type Edge,
  type NodeTypes,
  BackgroundVariant,
  Panel,
  type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import {
  ArrowLeft, Save, Play, Pause, Zap, MessageSquare, Clock,
  GitBranch, UserCheck, Bot, FileSignature, GripVertical, Loader2, Sparkles, ShieldCheck,
  Undo2, Redo2, Copy, Trash2, LayoutTemplate, ListChecks, FlaskConical,
  CheckCircle2, AlertTriangle, Search, RefreshCw, Activity, CalendarClock,
  Route, Merge, RefreshCcw, ShieldAlert, Braces, GitFork, Calculator, Link2,
  ClipboardList, Tags, PenLine, BookOpenCheck, BadgeDollarSign, MessageSquareReply, Cable,
} from 'lucide-react';
import { TriggerNode } from '@/components/flow/nodes/TriggerNode';
import { MessageNode } from '@/components/flow/nodes/MessageNode';
import { WaitNode } from '@/components/flow/nodes/WaitNode';
import { ConditionNode } from '@/components/flow/nodes/ConditionNode';
import { HumanPauseNode } from '@/components/flow/nodes/HumanPauseNode';
import { AgentNode } from '@/components/flow/nodes/AgentNode';
import { ZapSignNode } from '@/components/flow/nodes/ZapSignNode';
import { ScheduledMessageNode } from '@/components/flow/nodes/ScheduledMessageNode';
import { StudioNode } from '@/components/flow/nodes/StudioNode';
import { NodeConfigPanel } from '@/components/flow/NodeConfigPanel';
import { toast } from '@/components/ui/use-toast';
import { apiFetch, ApiError } from '@/services/http';
import {
  klmApi,
  type KlmFlow,
  type KlmFlowDryRun,
  type KlmFlowGraphValidation,
  type KlmFlowNodeCatalog,
  type KlmFlowNodeCatalogEntry,
  type KlmFunnelCard,
} from '@/klm/api';
import { MatterFlowJourney } from '@/klm/components/MatterFlowJourney';
import { fetchTemplatePicker } from '@/services/campaigns';
import type { SequenceTemplateOption } from '@/components/flow/SequenceBuilder';
import {
  getDefaultNodeData,
  normalizeFlowEdges,
  normalizeFlowNodes,
  serializeExecutableFlowGraph,
  type FlowEditorNodeData,
} from '@/lib/flow-editor';
import {
  appendFlowHistory,
  autoArrangeFlowGraph,
  cloneFlowGraph,
  validateFlowGraph,
  type FlowGraphSnapshot,
} from '@/lib/flow-builder-graph';

/** Specialized renderers are presentation only. The runtime catalogue remains
 * the source of truth for which node types can be authored. */
const specializedNodeTypes: NodeTypes = {
  trigger: TriggerNode,
  send_message: MessageNode,
  wait: WaitNode,
  condition: ConditionNode,
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

/**
 * Only colours/icons live in the browser. Type, label, category and capability
 * availability always come from GET /api/v1/flows/catalog.
 */
const nodeVisuals: Record<string, { icon: typeof Zap; color: string; bg: string }> = {
  trigger: { icon: Zap, color: 'text-yellow-600', bg: 'bg-yellow-500/10' },
  condition: { icon: GitBranch, color: 'text-amber-600', bg: 'bg-amber-500/10' },
  router: { icon: Route, color: 'text-indigo-600', bg: 'bg-indigo-500/10' },
  merge: { icon: Merge, color: 'text-indigo-600', bg: 'bg-indigo-500/10' },
  loop: { icon: RefreshCcw, color: 'text-indigo-600', bg: 'bg-indigo-500/10' },
  wait: { icon: Clock, color: 'text-slate-600', bg: 'bg-slate-500/10' },
  error_handler: { icon: ShieldAlert, color: 'text-red-600', bg: 'bg-red-500/10' },
  set_variable: { icon: Braces, color: 'text-cyan-600', bg: 'bg-cyan-500/10' },
  transform: { icon: GitFork, color: 'text-cyan-600', bg: 'bg-cyan-500/10' },
  aggregate: { icon: Calculator, color: 'text-cyan-600', bg: 'bg-cyan-500/10' },
  send_message: { icon: MessageSquare, color: 'text-blue-600', bg: 'bg-blue-500/10' },
  scheduled_message: { icon: CalendarClock, color: 'text-cyan-600', bg: 'bg-cyan-500/10' },
  social_action: { icon: MessageSquareReply, color: 'text-pink-600', bg: 'bg-pink-500/10' },
  agent: { icon: Bot, color: 'text-violet-600', bg: 'bg-violet-500/10' },
  human_pause: { icon: UserCheck, color: 'text-orange-600', bg: 'bg-orange-500/10' },
  create_task: { icon: ClipboardList, color: 'text-emerald-600', bg: 'bg-emerald-500/10' },
  update_card: { icon: Tags, color: 'text-emerald-600', bg: 'bg-emerald-500/10' },
  internal_note: { icon: PenLine, color: 'text-emerald-600', bg: 'bg-emerald-500/10' },
  audit_event: { icon: BookOpenCheck, color: 'text-slate-600', bg: 'bg-slate-500/10' },
  link_action: { icon: Link2, color: 'text-emerald-600', bg: 'bg-emerald-500/10' },
  payment_request: { icon: BadgeDollarSign, color: 'text-amber-600', bg: 'bg-amber-500/10' },
  zapsign_request: { icon: FileSignature, color: 'text-emerald-600', bg: 'bg-emerald-500/10' },
  connector_action: { icon: Cable, color: 'text-violet-600', bg: 'bg-violet-500/10' },
};

const categoryVisuals: Record<string, { icon: typeof Zap; color: string; bg: string }> = {
  Entradas: nodeVisuals.trigger,
  Controle: nodeVisuals.router,
  Dados: nodeVisuals.set_variable,
  Atendimento: nodeVisuals.send_message,
  Inteligência: nodeVisuals.agent,
  Humanos: nodeVisuals.human_pause,
  Governança: nodeVisuals.human_pause,
  Financeiro: nodeVisuals.aggregate,
  Integrações: nodeVisuals.connector_action,
};

/** Bespoke legacy panels are deliberately kept until their runtime contracts are migrated. */
const legacyAuthorableNodeTypes = new Set([
  'trigger', 'send_message', 'wait', 'condition', 'router', 'merge', 'loop',
  'error_handler', 'set_variable', 'transform', 'aggregate', 'link_action',
  'human_pause', 'agent', 'create_task', 'update_card', 'internal_note',
  'audit_event', 'payment_request', 'social_action', 'connector_action',
  'zapsign_request', 'scheduled_message',
]);

/**
 * Converts the backend-owned node contract into a palette item.  Exported for
 * the catalog contract test: an unavailable backend capability must never be
 * made authorable merely because it still has a schema for display.
 */
export function paletteItemFromCatalog(node: KlmFlowNodeCatalogEntry) {
  const visual = nodeVisuals[node.type] ?? categoryVisuals[node.category] ?? nodeVisuals.audit_event;
  const availabilityReason = node.availabilityReason ?? node.availability_reason ?? null;
  const runtimeAvailable = node.availability !== 'unavailable';
  return {
    ...node,
    ...visual,
    defaultData: { ...getDefaultNodeData(node.type), label: node.label },
    availabilityReason,
    runtimeAvailable,
    authorable: runtimeAvailable && (Boolean(node.schema) || legacyAuthorableNodeTypes.has(node.type)),
  };
}

const INITIAL_NODES: Array<Node<FlowEditorNodeData>> = normalizeFlowNodes(null);
const INITIAL_EDGES: Edge[] = [];

let nodeIdCounter = 100;

type FlowTemplateOption = Pick<
  KlmFlow,
  'id' | 'name' | 'description' | 'practice_area' | 'trigger' | 'trigger_config' | 'nodes' | 'edges'
>;

function IconAction({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          aria-label={label}
          disabled={disabled}
          onClick={onClick}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent className="text-xs">{label}</TooltipContent>
    </Tooltip>
  );
}

function dryRunStatusLabel(status: KlmFlowDryRun['status']): string {
  if (status === 'completed') return 'Simulação concluída';
  if (status === 'waiting_human') return 'Aguardando decisão humana';
  if (status === 'max_steps_reached') return 'Limite seguro alcançado';
  return 'Grafo inválido';
}

export default function FlowBuilder() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const returnTo = searchParams.get('return') || '/automacoes';
  const [flowName, setFlowName] = useState('Meu Fluxo');
  const [flowDescription, setFlowDescription] = useState<string | null>(null);
  const [practiceArea, setPracticeArea] = useState('universal');
  const [trigger, setTrigger] = useState('manual');
  const [triggerConfig, setTriggerConfig] = useState<Record<string, unknown>>({ localOnly: true, category: 'custom' });
  const [isEditingName, setIsEditingName] = useState(false);
  const [isActive, setIsActive] = useState(false);
  const [selectedNode, setSelectedNode] = useState<Node<FlowEditorNodeData> | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState(INITIAL_NODES);
  const [edges, setEdges, onEdgesChange] = useEdgesState(INITIAL_EDGES);
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const [saving, setSaving] = useState(false);
  const [loadingFlow, setLoadingFlow] = useState(Boolean(id));
  const [isDirty, setIsDirty] = useState(!id);
  const [currentRevision, setCurrentRevision] = useState<number | null>(null);
  const [persistedFlowId, setPersistedFlowId] = useState<string | null>(id ?? null);
  const [leaveDialogOpen, setLeaveDialogOpen] = useState(false);
  const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance<Node<FlowEditorNodeData>, Edge> | null>(null);
  const [historyPast, setHistoryPast] = useState<FlowGraphSnapshot[]>([]);
  const [historyFuture, setHistoryFuture] = useState<FlowGraphSnapshot[]>([]);
  const dragCheckpointOpen = useRef(false);
  const [validationOpen, setValidationOpen] = useState(false);
  const [runtimeValidation, setRuntimeValidation] = useState<KlmFlowGraphValidation | null>(null);
  const [runtimeValidationSignature, setRuntimeValidationSignature] = useState<string | null>(null);
  const [runtimeValidationLoading, setRuntimeValidationLoading] = useState(false);
  const [runtimeSimulation, setRuntimeSimulation] = useState<KlmFlowGraphValidation | null>(null);
  const [runtimeSimulationLoading, setRuntimeSimulationLoading] = useState(false);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [templates, setTemplates] = useState<FlowTemplateOption[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [templateSearch, setTemplateSearch] = useState('');
  const [pendingTemplate, setPendingTemplate] = useState<FlowTemplateOption | null>(null);
  const [dryRunOpen, setDryRunOpen] = useState(false);
  const [dryRun, setDryRun] = useState<KlmFlowDryRun | null>(null);
  const [dryRunLoading, setDryRunLoading] = useState(false);
  const [dryRunStep, setDryRunStep] = useState(-1);
  const [matterOptions, setMatterOptions] = useState<KlmFunnelCard[]>([]);
  const [matterOptionsLoading, setMatterOptionsLoading] = useState(false);
  const [testMatterId, setTestMatterId] = useState(searchParams.get('matter') ?? '');
  const [nodeSearch, setNodeSearch] = useState('');
  const [nodeCatalog, setNodeCatalog] = useState<KlmFlowNodeCatalog | null>(null);
  const [nodeCatalogState, setNodeCatalogState] = useState<'loading' | 'ready' | 'unavailable'>('loading');
  const [sequenceTemplates, setSequenceTemplates] = useState<SequenceTemplateOption[]>([]);
  const validation = useMemo(() => validateFlowGraph(nodes, edges), [edges, nodes]);
  const runtimeGraphPayload = useMemo(() => serializeExecutableFlowGraph(nodes, edges), [edges, nodes]);
  const runtimeGraphSignature = useMemo(() => JSON.stringify(runtimeGraphPayload), [runtimeGraphPayload]);
  const runtimeValidationIsCurrent = runtimeValidationSignature === runtimeGraphSignature;
  const filteredTemplates = useMemo(() => {
    const query = templateSearch.trim().toLocaleLowerCase('pt-BR');
    if (!query) return templates;
    return templates.filter((template) => (
      `${template.name} ${template.description ?? ''} ${template.practice_area}`
        .toLocaleLowerCase('pt-BR')
        .includes(query)
    ));
  }, [templateSearch, templates]);
  const visiblePalette = useMemo(() => {
    const query = nodeSearch.trim().toLocaleLowerCase('pt-BR');
    return (nodeCatalog?.nodes ?? [])
      .map(paletteItemFromCatalog)
      .filter((item) => !query || `${item.label} ${item.category} ${item.description}`.toLocaleLowerCase('pt-BR').includes(query));
  }, [nodeCatalog, nodeSearch]);
  const groupedPalette = useMemo(() => {
    const groups = new Map<string, typeof visiblePalette>();
    visiblePalette.forEach((item) => groups.set(item.category, [...(groups.get(item.category) ?? []), item]));
    return [...groups.entries()];
  }, [visiblePalette]);
  const runtimeNodeTypes = useMemo<NodeTypes>(() => {
    const discoveredTypes = new Set([
      ...Object.keys(specializedNodeTypes),
      ...(nodeCatalog?.nodes ?? []).map((entry) => entry.type),
      ...nodes.map((node) => node.type ?? ''),
    ]);
    return Object.fromEntries(
      [...discoveredTypes].filter(Boolean).map((type) => [type, specializedNodeTypes[type] ?? StudioNode]),
    ) as NodeTypes;
  }, [nodeCatalog, nodes]);
  const dryRunProgress = dryRun?.trace.length
    ? Math.min(100, Math.max(0, ((dryRunStep + 1) / dryRun.trace.length) * 100))
    : 0;
  const readinessPercent = nodes.length
    ? Math.round((nodes.length / (nodes.length + validation.errors.length)) * 100)
    : 0;
  const dryRunStateByNode = useMemo(() => {
    const states = new Map<string, 'completed' | 'current' | 'upcoming'>();
    if (!dryRun) return states;
    dryRun.trace.forEach((step, index) => {
      states.set(step.nodeId, index < dryRunStep ? 'completed' : index === dryRunStep ? 'current' : 'upcoming');
    });
    return states;
  }, [dryRun, dryRunStep]);
  const visibleNodes = useMemo(() => nodes.map((node) => {
    const executionState = dryRunStateByNode.get(node.id);
    if (!executionState) return node;
    return {
      ...node,
      style: {
        ...node.style,
        borderRadius: 12,
        boxShadow: executionState === 'current'
          ? '0 0 0 6px rgba(220, 167, 76, .22), 0 16px 36px rgba(19, 23, 32, .14)'
          : executionState === 'completed'
            ? '0 0 0 4px rgba(26, 153, 72, .16)'
            : undefined,
        opacity: executionState === 'upcoming' ? 0.58 : 1,
      },
    };
  }), [dryRunStateByNode, nodes]);

  const checkpoint = useCallback(() => {
    setHistoryPast((history) => appendFlowHistory(history, cloneFlowGraph(nodes, edges)));
    setHistoryFuture([]);
  }, [edges, nodes]);

  // Carregar flow existente se há um ID na URL
  useEffect(() => {
    let cancelled = false;
    setNodeCatalogState('loading');
    void klmApi.flowNodeCatalog()
      .then((catalog) => {
        if (cancelled) return;
        setNodeCatalog(catalog);
        setNodeCatalogState('ready');
      })
      .catch(() => {
        if (cancelled) return;
        // Sem catálogo não há como provar que um tipo é reconhecido pelo
        // runtime. Mantemos a edição do grafo já carregado, mas não exibimos
        // uma paleta estática incompleta nem deixamos criar nós órfãos.
        setNodeCatalog(null);
        setNodeCatalogState('unavailable');
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!id) {
      setLoadingFlow(false);
      setPersistedFlowId(null);
      return;
    }
    setLoadingFlow(true);
    setPersistedFlowId(id);
    apiFetch<{
      name: string;
      description: string | null;
      practice_area: string;
      trigger: string;
      trigger_config: Record<string, unknown> | null;
      is_active: boolean;
      current_revision: number;
      nodes: Node[];
      edges: Edge[];
    }>(`/api/v1/flows/${id}`)
      .then(data => {
        setFlowName(data.name ?? 'Meu Fluxo');
        setFlowDescription(data.description ?? null);
        setPracticeArea(data.practice_area ?? 'universal');
        setTrigger(data.trigger ?? 'manual');
        setTriggerConfig(data.trigger_config ?? {});
        setIsActive(data.is_active ?? false);
        setCurrentRevision(data.current_revision);
        setNodes(normalizeFlowNodes(data.nodes));
        setEdges(normalizeFlowEdges(data.edges));
        setSelectedNode(null);
        setHistoryPast([]);
        setHistoryFuture([]);
        setDryRun(null);
        setDryRunStep(-1);
        setIsDirty(false);
      })
      .catch((error: Error) =>
        toast({
          variant: 'destructive',
          title: 'Erro ao carregar fluxo',
          description: error.message || 'Esse fluxo pode estar inválido ou indisponível para o seu workspace.',
        }),
      )
      .finally(() => setLoadingFlow(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // ConversationTemplatePicker carries the reviewed sequence draft in the
  // navigation URL. Hydrate it into the real graph so opening the editor does
  // not silently discard stages, delays, bindings, or interrupt behavior.
  useEffect(() => {
    const draftKey = searchParams.get('sequence_draft');
    if (id || (!draftKey && !searchParams.get('sequence')) || nodes.some((node) => node.id === 'sequence-draft-1')) return;
    try {
      const storedDraft = draftKey && typeof window !== 'undefined'
        ? window.sessionStorage.getItem(`klm-sequence-draft:${draftKey}`)
        : null;
      const parsed = JSON.parse(storedDraft ?? searchParams.get('sequence') ?? '') as unknown;
      const draft = Array.isArray(parsed) ? parsed : (parsed as { messages?: unknown[] }).messages;
      const interruptOnReply = Array.isArray(parsed) ? true : (parsed as { interruptOnReply?: unknown }).interruptOnReply !== false;
      if (!Array.isArray(draft) || !draft.length) return;
      const sequence = draft as FlowEditorNodeData['sequence'];
      const node: Node<FlowEditorNodeData> = {
        id: 'sequence-draft-1',
        type: 'send_message',
        position: { x: 250, y: 220 },
        data: {
          label: 'Sequência WhatsApp',
          message: sequence?.[0]?.body ?? '',
          is_sequence: true,
          sequence_stage: true,
          interrupt_on_reply: interruptOnReply,
          sequence,
        },
      };
      setNodes((current) => [...current, node]);
      setEdges((current) => [...current, { id: 'edge-trigger-sequence-draft', source: 'trigger-1', target: node.id, animated: true, style: { strokeWidth: 2 } }]);
      setSelectedNode(node);
      setIsDirty(true);
    } catch {
      // Invalid navigation drafts are ignored; the editor remains usable.
    }
  }, [id, nodes, searchParams, setEdges, setNodes]);

  useEffect(() => {
    const data = selectedNode?.data as FlowEditorNodeData | undefined;
    const sequenceNodeSelected = Boolean(selectedNode && (selectedNode.type === 'send_message' || selectedNode.type === 'scheduled_message') && data?.is_sequence);
    if (!sequenceNodeSelected || sequenceTemplates.length) return;
    let cancelled = false;
    void fetchTemplatePicker({ threadId: searchParams.get('thread'), matterId: searchParams.get('matter') })
      .then((result) => {
        if (cancelled) return;
        setSequenceTemplates(result.cards.filter((card) => String(card.status).toLowerCase() === 'approved').map((card) => ({
          id: card.id,
          name: card.name,
          status: card.status,
          language: card.language,
          bodyText: card.bodyText,
          windowStatus: 'unverified' as const,
          variableCount: card.variables.filter((variable) => variable.scope === 'body').length,
          variables: card.variables.filter((variable) => variable.scope === 'body').map((variable) => ({
            fieldKey: variable.binding?.kind === 'field' && 'fieldKey' in variable.binding && typeof variable.binding.fieldKey === 'string'
              ? variable.binding.fieldKey
              : '',
            resolvedValue: variable.example ?? '',
          })),
        })));
      })
      .catch(() => {
        if (!cancelled) setSequenceTemplates([]);
      });
    return () => { cancelled = true; };
  }, [searchParams, selectedNode, sequenceTemplates.length]);

  useEffect(() => {
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!isDirty) return;
      event.preventDefault();
    };
    window.addEventListener('beforeunload', warnBeforeUnload);
    return () => window.removeEventListener('beforeunload', warnBeforeUnload);
  }, [isDirty]);

  useEffect(() => {
    if (!selectedNode) return;
    const current = nodes.find((node) => node.id === selectedNode.id) ?? null;
    if (current !== selectedNode) setSelectedNode(current);
  }, [nodes, selectedNode]);

  useEffect(() => {
    if (!dryRun || dryRunStep < 0 || dryRunStep >= dryRun.trace.length - 1) return;
    const timer = window.setTimeout(() => setDryRunStep((step) => step + 1), 720);
    return () => window.clearTimeout(timer);
  }, [dryRun, dryRunStep]);

  const onConnect = useCallback(
    (params: Connection) => {
      checkpoint();
      const routeKey = params.sourceHandle || undefined;
      const conditionValue = params.sourceHandle === 'true' || params.sourceHandle === 'false'
        ? params.sourceHandle
        : undefined;
      setEdges((eds) => addEdge({
        ...params,
        animated: true,
        style: { strokeWidth: 2 },
        label: conditionValue === 'true'
          ? 'Sim'
          : conditionValue === 'false'
            ? 'Não'
            : routeKey === 'fallback'
              ? 'Demais casos'
              : routeKey,
        ...(conditionValue ? { condition_value: conditionValue } : {}),
        ...(routeKey ? { route_key: routeKey } : {}),
        ...(routeKey === 'fallback' ? { is_fallback: true } : {}),
      }, eds));
      setIsDirty(true);
    },
    [checkpoint, setEdges]
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const type = event.dataTransfer.getData('application/reactflow/type');
      const dataStr = event.dataTransfer.getData('application/reactflow/data');
      if (!type || !reactFlowWrapper.current || !reactFlowInstance) return;
      checkpoint();
      const position = reactFlowInstance.screenToFlowPosition({ x: event.clientX, y: event.clientY });
      const defaultData = dataStr ? JSON.parse(dataStr) : {};
      const newNode: Node<FlowEditorNodeData> = {
        id: `${type}-${++nodeIdCounter}`,
        type,
        position,
        data: defaultData as FlowEditorNodeData,
      };
      setNodes((nds) => [...nds, newNode]);
      setIsDirty(true);
    },
    [checkpoint, reactFlowInstance, setNodes]
  );

  const addNodeFromPalette = useCallback((type: string, defaultData: FlowEditorNodeData) => {
    checkpoint();
    const newNode: Node<FlowEditorNodeData> = {
      id: `${type}-${++nodeIdCounter}`,
      type,
      position: { x: 160 + (nodes.length % 3) * 210, y: 120 + Math.floor(nodes.length / 3) * 150 },
      data: { ...defaultData },
    };
    setNodes((current) => [...current, newNode]);
    setSelectedNode(newNode);
    setIsDirty(true);
  }, [checkpoint, nodes.length, setNodes]);

  const handleNodeUpdate = useCallback((nodeId: string, newData: FlowEditorNodeData) => {
    checkpoint();
    const nodeType = nodes.find((node) => node.id === nodeId)?.type;
    if (nodeType === 'trigger') {
      const nextTrigger = String(newData.trigger_type ?? newData.event ?? 'manual');
      setTrigger(nextTrigger);
      setTriggerConfig((current) => ({
        ...current,
        event: nextTrigger,
        column_id: newData.column_id || null,
        keywords: newData.keywords ?? [],
        excludedKeywords: newData.excluded_keywords ?? [],
        keywordMode: newData.keyword_mode ?? 'contains',
        match: newData.match_mode ?? 'any',
        sourceIds: newData.source_ids ?? [],
        caseSensitive: newData.case_sensitive ?? false,
        firstLevelOnly: newData.first_level_only ?? true,
        followerStatus: newData.follower_status ?? 'any',
        verifiedStatus: newData.verified_status ?? 'any',
        messagingWindow: newData.messaging_window ?? 'any',
      }));
    }
    setNodes((nds) => nds.map((n) => n.id === nodeId ? { ...n, data: newData } : n));
    setSelectedNode((prev) => prev?.id === nodeId ? { ...prev, data: newData } : prev);
    setIsDirty(true);
  }, [checkpoint, nodes, setNodes]);

  const handleNodesChange = useCallback((changes: Parameters<typeof onNodesChange>[0]) => {
    if (changes.some((change) => ['add', 'remove', 'replace'].includes(change.type))) checkpoint();
    onNodesChange(changes);
    if (changes.some((change) => change.type !== 'select')) setIsDirty(true);
  }, [checkpoint, onNodesChange]);

  const handleEdgesChange = useCallback((changes: Parameters<typeof onEdgesChange>[0]) => {
    if (changes.some((change) => ['add', 'remove', 'replace'].includes(change.type))) checkpoint();
    onEdgesChange(changes);
    if (changes.some((change) => change.type !== 'select')) setIsDirty(true);
  }, [checkpoint, onEdgesChange]);

  const leaveEditor = () => {
    if (isDirty) {
      setLeaveDialogOpen(true);
      return;
    }
    navigate(returnTo);
  };

  const restoreSnapshot = (snapshot: FlowGraphSnapshot) => {
    setNodes(snapshot.nodes);
    setEdges(snapshot.edges);
    setSelectedNode(null);
    setDryRun(null);
    setDryRunStep(-1);
    setIsDirty(true);
    window.setTimeout(() => void reactFlowInstance?.fitView({ padding: 0.2, duration: 240 }), 0);
  };

  const handleUndo = () => {
    const previous = historyPast.at(-1);
    if (!previous) return;
    setHistoryFuture((future) => [cloneFlowGraph(nodes, edges), ...future].slice(0, 50));
    setHistoryPast((history) => history.slice(0, -1));
    restoreSnapshot(previous);
  };

  const handleRedo = () => {
    const next = historyFuture[0];
    if (!next) return;
    setHistoryPast((history) => appendFlowHistory(history, cloneFlowGraph(nodes, edges)));
    setHistoryFuture((future) => future.slice(1));
    restoreSnapshot(next);
  };

  const deleteSelectedNode = () => {
    if (!selectedNode) return;
    checkpoint();
    setNodes((current) => current.filter((node) => node.id !== selectedNode.id));
    setEdges((current) => current.filter((edge) => edge.source !== selectedNode.id && edge.target !== selectedNode.id));
    setSelectedNode(null);
    setDryRun(null);
    setIsDirty(true);
  };

  const duplicateSelectedNode = () => {
    if (!selectedNode) return;
    if (selectedNode.type === 'trigger') {
      toast({ title: 'O fluxo usa um único gatilho', description: 'Adicione outro tipo de etapa e conecte-a ao ponto de entrada.' });
      return;
    }
    checkpoint();
    const duplicate: Node<FlowEditorNodeData> = {
      ...selectedNode,
      id: `${selectedNode.type ?? 'node'}-${++nodeIdCounter}`,
      position: { x: selectedNode.position.x + 42, y: selectedNode.position.y + 42 },
      data: { ...selectedNode.data, label: `${selectedNode.data.label} (cópia)` },
      selected: false,
    };
    setNodes((current) => [...current, duplicate]);
    setSelectedNode(duplicate);
    setDryRun(null);
    setIsDirty(true);
  };

  const arrangeGraph = () => {
    checkpoint();
    setNodes(autoArrangeFlowGraph(nodes, edges));
    setIsDirty(true);
    window.setTimeout(() => void reactFlowInstance?.fitView({ padding: 0.2, duration: 300 }), 0);
  };

  const focusNode = (nodeId?: string) => {
    if (!nodeId) return;
    const node = nodes.find((candidate) => candidate.id === nodeId);
    if (!node) return;
    setSelectedNode(node);
    void reactFlowInstance?.fitView({ nodes: [{ id: nodeId }], padding: 1.2, duration: 260 });
  };

  const validateRuntimeGraph = async (): Promise<KlmFlowGraphValidation | null> => {
    setRuntimeValidationLoading(true);
    try {
      const result = await klmApi.validateFlowGraphDraft(runtimeGraphPayload);
      setRuntimeValidation(result);
      setRuntimeValidationSignature(runtimeGraphSignature);
      return result;
    } catch (error) {
      setRuntimeValidation(null);
      setRuntimeValidationSignature(null);
      toast({
        variant: 'destructive',
        title: 'Compilação do runtime indisponível',
        description: error instanceof Error ? error.message : 'O fluxo não será publicado sem a validação do servidor.',
      });
      return null;
    } finally {
      setRuntimeValidationLoading(false);
    }
  };

  const simulateRuntimeGraph = async () => {
    setRuntimeSimulationLoading(true);
    try {
      setRuntimeSimulation(await klmApi.simulateFlowGraphDraft(runtimeGraphPayload));
    } catch (error) {
      toast({
        variant: 'destructive',
        title: 'Simulação estrutural indisponível',
        description: error instanceof Error ? error.message : 'Nenhuma ferramenta nem efeito externo foi executado.',
      });
    } finally {
      setRuntimeSimulationLoading(false);
    }
  };

  const persistFlow = async (nextActive: boolean, announce = true): Promise<string | null> => {
    if (!flowName.trim()) {
      toast({ variant: 'destructive', title: 'Dê um nome ao fluxo' });
      return null;
    }
    if (nextActive && !validation.canActivate) {
      setValidationOpen(true);
      toast({
        variant: 'destructive',
        title: 'Corrija o fluxo antes de ativar',
        description: `${validation.errors.length} pendência${validation.errors.length === 1 ? '' : 's'} bloqueia${validation.errors.length === 1 ? '' : 'm'} a publicação.`,
      });
      return null;
    }

    if (nextActive) {
      const compiled = await validateRuntimeGraph();
      if (!compiled?.valid) {
        setValidationOpen(true);
        toast({
          variant: 'destructive',
          title: 'Publicação bloqueada pelo runtime',
          description: compiled ? 'Corrija os achados estruturais e compile novamente.' : 'Não foi possível confirmar os gates no servidor.',
        });
        return null;
      }
    }

    setSaving(true);
    try {
      const basePayload = {
        name: flowName.trim(),
        description: flowDescription?.trim() || undefined,
        practice_area: practiceArea,
        nodes: runtimeGraphPayload.nodes,
        edges: runtimeGraphPayload.edges,
        is_active: nextActive,
        trigger,
        trigger_config: triggerConfig,
      };
      let saved: KlmFlow;
      if (persistedFlowId) {
        if (currentRevision === null) throw new Error('A revisão do fluxo ainda não foi carregada.');
        saved = await klmApi.updateFlow(persistedFlowId, {
          ...basePayload,
          expected_revision: currentRevision,
        });
      } else {
        saved = await klmApi.createFlow(basePayload);
      }

      setPersistedFlowId(saved.id);
      setCurrentRevision(saved.current_revision);
      setIsActive(saved.is_active);
      setIsDirty(false);
      if (!persistedFlowId) {
        const nextParams = new URLSearchParams(searchParams);
        navigate(`/automacoes/editor/${saved.id}?${nextParams.toString()}`, { replace: true });
      }
      if (announce) {
        toast({
          title: nextActive ? 'Fluxo ativado' : 'Rascunho salvo',
          description: nextActive
            ? `“${saved.name}” está ativo na revisão ${saved.current_revision}.`
            : `“${saved.name}” foi salvo e permanece pausado.`,
        });
      }
      return saved.id;
    } catch (error) {
      toast({
        variant: 'destructive',
        title: error instanceof ApiError && error.status === 409 ? 'Fluxo alterado em outra tela' : 'Erro ao salvar fluxo',
        description: error instanceof ApiError && error.status === 409
          ? 'Seu rascunho foi preservado. Reabra a versão mais recente e aplique as alterações antes de salvar.'
          : error instanceof Error ? error.message : 'Tente novamente.',
      });
      return null;
    } finally {
      setSaving(false);
    }
  };

  const openTemplateLibrary = async () => {
    setTemplatesOpen(true);
    if (templates.length || templatesLoading) return;
    setTemplatesLoading(true);
    try {
      setTemplates(await apiFetch<FlowTemplateOption[]>('/api/v1/flows/templates'));
    } catch (error) {
      toast({
        variant: 'destructive',
        title: 'Biblioteca indisponível',
        description: error instanceof Error ? error.message : 'Não foi possível carregar os modelos.',
      });
    } finally {
      setTemplatesLoading(false);
    }
  };

  const applyTemplate = (template: FlowTemplateOption) => {
    checkpoint();
    const templateNodes = normalizeFlowNodes(template.nodes).map((node, index) => ({
      ...node,
      id: `template-${Date.now()}-${index}-${node.id}`,
    }));
    const idMap = new Map(normalizeFlowNodes(template.nodes).map((node, index) => [node.id, templateNodes[index].id]));
    const templateEdges = normalizeFlowEdges(template.edges).map((edge, index) => ({
      ...edge,
      id: `template-edge-${Date.now()}-${index}`,
      source: idMap.get(edge.source) ?? edge.source,
      target: idMap.get(edge.target) ?? edge.target,
    }));
    setNodes(autoArrangeFlowGraph(templateNodes, templateEdges));
    setEdges(templateEdges);
    setFlowName(`${template.name} · cópia`);
    setFlowDescription(template.description ?? null);
    setPracticeArea(template.practice_area ?? 'universal');
    setTrigger(template.trigger ?? 'manual');
    setTriggerConfig(template.trigger_config ?? { localOnly: true, category: 'template' });
    setIsActive(false);
    setSelectedNode(null);
    setDryRun(null);
    setIsDirty(true);
    setTemplatesOpen(false);
    setPendingTemplate(null);
    window.setTimeout(() => void reactFlowInstance?.fitView({ padding: 0.2, duration: 300 }), 0);
    toast({ title: 'Modelo aplicado ao rascunho', description: 'Revise as etapas, valide e salve quando estiver pronto.' });
  };

  const loadMatterOptions = async () => {
    if (matterOptions.length || matterOptionsLoading) return;
    setMatterOptionsLoading(true);
    try {
      const funnel = await klmApi.funnel();
      setMatterOptions(funnel.cards);
      if (!testMatterId && funnel.cards[0]) setTestMatterId(funnel.cards[0].id);
    } catch (error) {
      toast({
        variant: 'destructive',
        title: 'Casos indisponíveis para teste',
        description: error instanceof Error ? error.message : 'Não foi possível carregar o funil.',
      });
    } finally {
      setMatterOptionsLoading(false);
    }
  };

  const openDryRun = () => {
    setDryRunOpen(true);
    void loadMatterOptions();
  };

  const runDryRun = async () => {
    if (!validation.canActivate) {
      setDryRunOpen(false);
      setValidationOpen(true);
      return;
    }
    if (!testMatterId) {
      toast({ variant: 'destructive', title: 'Selecione um caso para simular' });
      return;
    }
    setDryRunLoading(true);
    try {
      let flowId = persistedFlowId;
      if (!flowId || isDirty) flowId = await persistFlow(false, false);
      if (!flowId) return;
      const result = await klmApi.dryRunFlow(flowId, testMatterId, {
        source: 'flow_builder_preview',
        thread_id: searchParams.get('thread'),
      });
      setDryRun(result);
      setDryRunStep(result.trace.length ? 0 : -1);
      toast({
        title: dryRunStatusLabel(result.status),
        description: `${result.trace.length} etapa${result.trace.length === 1 ? '' : 's'} · 0 ações externas.`,
      });
    } catch (error) {
      toast({
        variant: 'destructive',
        title: 'Simulação recusada pelo runtime',
        description: error instanceof Error ? error.message : 'Revise o caso e o grafo antes de tentar novamente.',
      });
    } finally {
      setDryRunLoading(false);
    }
  };

  useEffect(() => {
    const handleKeyboard = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const editingText = Boolean(target?.closest('input, textarea, [contenteditable="true"]'));
      const modifier = event.metaKey || event.ctrlKey;
      if (modifier && event.key.toLowerCase() === 's') {
        event.preventDefault();
        void persistFlow(false);
        return;
      }
      if (modifier && event.key.toLowerCase() === 'z') {
        event.preventDefault();
        if (event.shiftKey) handleRedo();
        else handleUndo();
        return;
      }
      if (!editingText && (event.key === 'Delete' || event.key === 'Backspace')) {
        event.preventDefault();
        deleteSelectedNode();
      }
    };
    window.addEventListener('keydown', handleKeyboard);
    return () => window.removeEventListener('keydown', handleKeyboard);
  });

  return (
    <TooltipProvider delayDuration={250}>
    <div className="flex h-screen flex-col bg-background">
      {/* Header */}
      <div className="flex min-h-14 shrink-0 flex-wrap items-center justify-between gap-2 border-b bg-card px-3 py-2 lg:px-4">
        <div className="flex min-w-0 items-center gap-2">
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={leaveEditor} title="Voltar">
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <Separator orientation="vertical" className="h-5" />
          {isEditingName ? (
            <Input
              className="h-8 w-[min(48vw,18rem)] px-2 text-sm font-semibold"
              value={flowName}
              autoFocus
              onChange={(e) => { setFlowName(e.target.value); setIsDirty(true); }}
              onBlur={() => setIsEditingName(false)}
              onKeyDown={(e) => e.key === 'Enter' && setIsEditingName(false)}
            />
          ) : (
            <button
              className="max-w-[min(48vw,22rem)] truncate text-sm font-semibold transition-colors hover:text-primary"
              onClick={() => setIsEditingName(true)}
            >
              {flowName}
            </button>
          )}
          <Badge variant={isActive ? 'default' : 'secondary'} className="text-xs h-5">
            {isActive ? 'Ativo' : 'Pausado'}
          </Badge>
          <Badge variant="outline" className="hidden h-5 text-xs sm:inline-flex">
            {currentRevision ? `Revisão ${currentRevision}` : 'Novo'}
          </Badge>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          <div className="mr-1 flex items-center rounded-md border bg-background p-0.5">
            <IconAction label="Desfazer" disabled={!historyPast.length} onClick={handleUndo}><Undo2 className="h-3.5 w-3.5" /></IconAction>
            <IconAction label="Refazer" disabled={!historyFuture.length} onClick={handleRedo}><Redo2 className="h-3.5 w-3.5" /></IconAction>
            <IconAction label="Organizar automaticamente" disabled={nodes.length < 2} onClick={arrangeGraph}><RefreshCw className="h-3.5 w-3.5" /></IconAction>
            <IconAction label="Abrir biblioteca de modelos" onClick={() => void openTemplateLibrary()}><LayoutTemplate className="h-3.5 w-3.5" /></IconAction>
            <IconAction label="Duplicar etapa selecionada" disabled={!selectedNode} onClick={duplicateSelectedNode}><Copy className="h-3.5 w-3.5" /></IconAction>
            <IconAction label="Excluir etapa selecionada" disabled={!selectedNode} onClick={deleteSelectedNode}><Trash2 className="h-3.5 w-3.5" /></IconAction>
          </div>
          <Button
            size="sm"
            variant={validation.canActivate && (!runtimeValidationIsCurrent || runtimeValidation?.valid) ? 'outline' : 'destructive'}
            className="h-8 gap-1.5 text-xs"
            onClick={() => setValidationOpen(true)}
          >
            {validation.canActivate && (!runtimeValidationIsCurrent || runtimeValidation?.valid) ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
            {runtimeValidationIsCurrent ? runtimeValidation?.valid ? 'Runtime validado' : 'Runtime bloqueou' : validation.canActivate ? 'Validar runtime' : `${validation.errors.length} ajustes`}
          </Button>
          <Button size="sm" variant="outline" className="h-8 gap-1.5 text-xs" onClick={() => void validateRuntimeGraph()} disabled={runtimeValidationLoading || !validation.canActivate}>
            {runtimeValidationLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
            Compilar
          </Button>
          <Button size="sm" variant="outline" className="h-8 gap-1.5 text-xs" onClick={() => void simulateRuntimeGraph()} disabled={runtimeSimulationLoading || !validation.canActivate}>
            {runtimeSimulationLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Calculator className="h-3.5 w-3.5" />}
            Simular custo
          </Button>
          <Button size="sm" variant="outline" className="h-8 gap-1.5 text-xs" onClick={openDryRun}>
            <FlaskConical className="h-3.5 w-3.5" />
            Testar
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 text-xs"
            onClick={() => void persistFlow(false)}
            disabled={saving || loadingFlow || !isDirty}
          >
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            Salvar rascunho
          </Button>
          <Button
            size="sm"
            variant={isActive ? 'outline' : 'default'}
            className="h-8 gap-1.5 text-xs"
            onClick={() => void persistFlow(!isActive)}
            disabled={saving || loadingFlow}
          >
            {isActive ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            {isActive ? 'Pausar' : 'Ativar'}
          </Button>
        </div>
      </div>

      {/* Body: Left panel + Canvas + Right panel */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left Panel — Node Palette */}
        <div className="hidden w-72 shrink-0 overflow-y-auto border-r bg-card md:block">
          <div className="px-3 py-3">
            <div className="mb-3 flex items-center justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Etapas</p>
                <p className="mt-1 text-xs font-medium tabular-nums">
                  {nodeCatalogState === 'ready' ? `${nodeCatalog?.node_count ?? 0} capacidades do runtime` : 'Catálogo do runtime indisponível'}
                </p>
              </div>
              <IconAction label="Modelos prontos" onClick={() => void openTemplateLibrary()}><LayoutTemplate className="h-4 w-4" /></IconAction>
            </div>
            <div className="relative mb-3"><Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" /><Input value={nodeSearch} onChange={(event) => setNodeSearch(event.target.value)} className="h-8 pl-8 text-sm" placeholder="Buscar ação, gatilho ou ferramenta" /></div>
            <div className="space-y-4">
              {nodeCatalogState === 'loading' ? <p className="py-8 text-center text-xs text-muted-foreground"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />Carregando capacidades autorizadas…</p> : null}
              {nodeCatalogState === 'unavailable' ? <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-xs leading-relaxed text-amber-950">Não foi possível carregar o catálogo autoritativo. Você pode editar o grafo atual, mas não criar uma etapa nova até o runtime responder.</div> : null}
              {groupedPalette.map(([category, items]) => <section key={category}>
                <div className="mb-1.5 flex items-center justify-between"><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{category}</p><span className="text-xs tabular-nums text-muted-foreground">{items.length}</span></div>
                <div className="space-y-1.5">{items.map((item) => (
                <button
                  type="button"
                  key={item.type}
                  draggable={item.authorable}
                  aria-label={`Adicionar nó ${item.label}`}
                  aria-disabled={!item.authorable}
                  disabled={!item.authorable}
                  onDragStart={(e) => {
                    if (!item.authorable) return;
                    e.dataTransfer.setData('application/reactflow/type', item.type);
                    e.dataTransfer.setData('application/reactflow/data', JSON.stringify(item.defaultData));
                    e.dataTransfer.effectAllowed = 'move';
                  }}
                  onClick={() => item.authorable && addNodeFromPalette(item.type, item.defaultData)}
                  title={item.authorable ? (item.description || `Adicionar ${item.label}`) : item.runtimeAvailable ? 'O backend ainda não publicou um contrato de formulário para este nó; a autoria segura está bloqueada.' : (item.availabilityReason ?? 'Este nó não está disponível no ambiente atual.')}
                  className="flex min-h-10 w-full items-center gap-2.5 rounded-md border border-border bg-background px-2.5 py-2 text-left transition-colors hover:border-primary hover:bg-accent active:cursor-grabbing disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <div className={`w-6 h-6 rounded-md flex items-center justify-center ${item.bg}`}>
                    <item.icon className={`h-3.5 w-3.5 ${item.color}`} />
                  </div>
                  <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{item.label}</span>{item.execution_mode === 'gated_external' ? <span className="block text-2xs text-amber-700">requer conexão</span> : null}{!item.runtimeAvailable ? <span className="block text-2xs text-destructive">{item.availabilityReason ?? 'indisponível no ambiente atual'}</span> : !item.authorable ? <span className="block text-2xs text-muted-foreground">formulário pendente no runtime</span> : null}</span>
                  <GripVertical className="h-3 w-3 text-muted-foreground/40 ml-auto" />
                </button>
                ))}</div>
              </section>)}
              {nodeCatalogState === 'ready' && !visiblePalette.length ? <p className="py-8 text-center text-xs text-muted-foreground">Nenhuma capacidade encontrada.</p> : null}
            </div>
          </div>
        </div>

        {/* Canvas */}
        <div ref={reactFlowWrapper} className="flex-1 relative" onDrop={onDrop} onDragOver={onDragOver}>
          <ReactFlow
            nodes={visibleNodes}
            edges={edges}
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
            onConnect={onConnect}
            nodeTypes={runtimeNodeTypes}
            onInit={setReactFlowInstance}
            snapToGrid
            snapGrid={[16, 16]}
            fitView
            onNodeClick={(_, node) => setSelectedNode(node)}
            onPaneClick={() => setSelectedNode(null)}
            onNodeDragStart={() => {
              if (dragCheckpointOpen.current) return;
              checkpoint();
              dragCheckpointOpen.current = true;
            }}
            onNodeDragStop={() => {
              dragCheckpointOpen.current = false;
              setIsDirty(true);
            }}
            className="bg-background"
            minZoom={0.3}
            maxZoom={1.8}
            proOptions={{ hideAttribution: true }}
            defaultEdgeOptions={{ animated: true, style: { strokeWidth: 2, stroke: 'hsl(var(--border))' } }}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="hsl(var(--border))" />
            <Controls />
            <MiniMap
              nodeColor={(n) => {
                const map: Record<string, string> = {
                  trigger: '#facc15', send_message: '#60a5fa', wait: '#94a3b8',
                  condition: '#fbbf24', human_pause: '#fb923c', agent: '#a78bfa', zapsign_request: '#34d399', scheduled_message: '#0891b2'
                };
                return map[n.type ?? ''] ?? '#94a3b8';
              }}
              className="!bg-card !border !border-border"
            />
            <Panel position="bottom-center">
              <div className="flex min-w-[250px] items-center gap-3 rounded-md border bg-card/95 px-3 py-2 text-xs text-muted-foreground shadow-sm backdrop-blur">
                <span className="whitespace-nowrap tabular-nums">{dryRun ? `${Math.round(dryRunProgress)}% executado` : `${readinessPercent}% pronto`}</span>
                <Progress value={dryRun ? dryRunProgress : readinessPercent} className="h-1.5" />
                <span className="whitespace-nowrap tabular-nums">{nodes.length} etapas</span>
              </div>
            </Panel>
            <Panel position="top-center">
              <div className="hidden items-center gap-2 rounded-md border bg-card/95 px-3 py-2 text-xs shadow-sm backdrop-blur md:flex">
                {dryRun && dryRunStep >= 0 ? (
                  <><Activity className="h-3.5 w-3.5 animate-pulse text-emerald-600" /><span className="font-medium">Etapa {dryRunStep + 1}/{dryRun.trace.length}: {dryRun.trace[dryRunStep]?.label}</span></>
                ) : validation.canActivate ? (
                  <><ShieldCheck className="h-3.5 w-3.5 text-emerald-600" /><span className="font-medium">Grafo pronto para ativação</span></>
                ) : (
                  <><AlertTriangle className="h-3.5 w-3.5 text-destructive" /><button type="button" className="font-medium" onClick={() => setValidationOpen(true)}>{validation.errors.length} pendência{validation.errors.length === 1 ? '' : 's'} no grafo</button></>
                )}
              </div>
            </Panel>
          </ReactFlow>
          {loadingFlow ? (
            <div className="absolute inset-0 z-20 flex items-center justify-center bg-background/75 backdrop-blur-sm">
              <div className="flex items-center gap-2 text-sm font-medium"><Loader2 className="h-4 w-4 animate-spin" />Carregando revisão</div>
            </div>
          ) : null}
        </div>

        {/* Right Panel — Node Config */}
        <div className="hidden w-[24rem] shrink-0 overflow-hidden border-l bg-card xl:block">
          {selectedNode ? (
            <NodeConfigPanel
              node={selectedNode}
              catalogNode={nodeCatalog?.nodes.find((entry) => entry.type === selectedNode.type)}
              humanCheckpointTargets={nodes.filter((entry) => ['human_assign', 'human_request_input', 'human_review', 'human_approval', 'human_notify', 'human_wait', 'human_schedule', 'human_takeover'].includes(entry.type ?? '')).map((entry) => ({ id: entry.id, label: String((entry.data as FlowEditorNodeData | undefined)?.label ?? entry.type ?? 'Checkpoint humano') }))}
              onUpdate={handleNodeUpdate}
              onClose={() => setSelectedNode(null)}
              sequenceTemplates={sequenceTemplates}
              editSequenceHref={persistedFlowId ? `/automacoes/editor/${persistedFlowId}` : '/automacoes/editor'}
            />
          ) : (
            <ScrollArea className="h-full">
              <div className="space-y-5 p-5">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Configuração geral</p>
                  <h2 className="mt-1 truncate text-lg font-semibold">{flowName}</h2>
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-medium" htmlFor="flow-description">Descrição</label>
                  <Textarea id="flow-description" value={flowDescription ?? ''} onChange={(event) => { setFlowDescription(event.target.value); setIsDirty(true); }} className="min-h-24 resize-none text-sm" />
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-medium" htmlFor="flow-practice-area">Área</label>
                  <Select value={practiceArea} onValueChange={(value) => { setPracticeArea(value); setIsDirty(true); }}>
                    <SelectTrigger id="flow-practice-area" className="h-9 text-sm"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="universal">Universal</SelectItem>
                      <SelectItem value="plano_saude">Plano de saúde</SelectItem>
                      <SelectItem value="trabalhista">Trabalhista</SelectItem>
                      <SelectItem value="previdenciario">Previdenciário</SelectItem>
                      <SelectItem value="bancario">Bancário</SelectItem>
                      <SelectItem value="tributario">Tributário</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="border-t pt-4">
                  <button type="button" className="flex w-full items-center justify-between text-left" onClick={() => setValidationOpen(true)}>
                    <span><span className="block text-xs font-semibold">Integridade do fluxo</span><span className="mt-1 block text-xs tabular-nums text-muted-foreground">{validation.errors.length} erros · {validation.warnings.length} alertas</span></span>
                    <span className="text-sm font-semibold tabular-nums">{readinessPercent}%</span>
                  </button>
                  <Progress value={readinessPercent} className="mt-3 h-2" />
                </div>
                {testMatterId ? (
                  <div className="border-t pt-4">
                    <MatterFlowJourney matterId={testMatterId} />
                  </div>
                ) : null}
              </div>
            </ScrollArea>
          )}
        </div>
      </div>

      <Dialog open={validationOpen} onOpenChange={setValidationOpen}>
        <DialogContent className="max-h-[82vh] max-w-2xl overflow-hidden p-0">
          <DialogHeader className="border-b px-6 py-5">
            <DialogTitle className="flex items-center gap-2 text-lg">
              <ListChecks className="h-4 w-4 text-primary" />
              Validação do fluxo
            </DialogTitle>
            <DialogDescription>
              {validation.canActivate
                ? `Pronto para ativar com ${validation.warnings.length} alerta${validation.warnings.length === 1 ? '' : 's'} não bloqueante${validation.warnings.length === 1 ? '' : 's'}.`
                : `${validation.errors.length} pendência${validation.errors.length === 1 ? '' : 's'} precisa${validation.errors.length === 1 ? '' : 'm'} ser corrigida${validation.errors.length === 1 ? '' : 's'} antes da ativação.`}
            </DialogDescription>
          </DialogHeader>
          <div className="px-6 pt-4">
            <div className="flex items-center justify-between text-xs"><span>Prontidão operacional</span><strong className="tabular-nums">{readinessPercent}%</strong></div>
            <Progress value={readinessPercent} className="mt-2 h-2" />
          </div>
          <ScrollArea className="max-h-[52vh] px-6 py-4">
            <section aria-label="Gate do runtime" className="mb-4 rounded-md border bg-muted/20 p-3 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2"><strong>Gate estrutural do runtime</strong><Badge variant={runtimeValidationIsCurrent && runtimeValidation?.valid ? 'default' : runtimeValidationIsCurrent ? 'destructive' : 'secondary'}>{runtimeValidationIsCurrent ? runtimeValidation?.valid ? 'aprovado' : 'bloqueado' : 'ainda não compilado'}</Badge></div>
              {runtimeValidationIsCurrent && runtimeValidation ? <><div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4"><span>Nós: <strong>{runtimeValidation.node_count}</strong></span><span>Alcançáveis: <strong>{runtimeValidation.reachable_node_count}</strong></span><span>Ações externas: <strong>{runtimeValidation.external_action_count}</strong></span><span>Gates humanos: <strong>{runtimeValidation.human_gate_count}</strong></span></div><p className="mt-2 text-muted-foreground">Custo estrutural estimado: {runtimeValidation.estimated_cost_units} unidades. Esta análise não executa ferramentas nem conectores.</p></> : <p className="mt-2 text-muted-foreground">A publicação compila o grafo no servidor obrigatoriamente. Você também pode compilar ou simular antes para antecipar ciclos, nós inalcançáveis e gates humanos.</p>}
              <div className="mt-3 flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => void validateRuntimeGraph()} disabled={runtimeValidationLoading || !validation.canActivate}>{runtimeValidationLoading ? 'Compilando…' : 'Compilar no runtime'}</Button><Button size="sm" variant="outline" onClick={() => void simulateRuntimeGraph()} disabled={runtimeSimulationLoading || !validation.canActivate}>{runtimeSimulationLoading ? 'Simulando…' : 'Simular custo e gates'}</Button></div>
              {runtimeSimulation ? <p className="mt-2 text-muted-foreground">Simulação: {runtimeSimulation.reachable_node_count}/{runtimeSimulation.node_count} nós alcançáveis · {runtimeSimulation.external_action_count} ações externas governadas · {runtimeSimulation.human_gate_count} gates humanos · custo {runtimeSimulation.estimated_cost_units}.</p> : null}
              {runtimeValidationIsCurrent && runtimeValidation?.issues.length ? <div className="mt-3 space-y-2">{runtimeValidation.issues.map((issue, index) => <button type="button" key={`${issue.code}-${issue.node_ids.join('-')}-${index}`} className="block w-full rounded border p-2 text-left hover:border-primary" onClick={() => { focusNode(issue.node_ids[0]); }}><strong>{issue.severity === 'error' ? 'Bloqueio' : 'Atenção'} · {issue.code}</strong><span className="mt-1 block text-muted-foreground">{issue.message}</span></button>)}</div> : null}
            </section>
            {validation.issues.length ? (
              <div className="space-y-2">
                {validation.issues.map((issue, index) => (
                  <button
                    type="button"
                    key={`${issue.code}-${issue.nodeId ?? issue.edgeId ?? index}`}
                    className="flex w-full items-start gap-3 rounded-md border p-3 text-left transition-colors hover:border-primary"
                    onClick={() => { focusNode(issue.nodeId); setValidationOpen(false); }}
                  >
                    {issue.severity === 'error'
                      ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                      : <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />}
                    <span className="min-w-0"><strong className="block text-sm">{issue.title}</strong><span className="mt-1 block text-xs text-muted-foreground">{issue.description}</span></span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="py-10 text-center"><CheckCircle2 className="mx-auto h-7 w-7 text-emerald-600" /><p className="mt-3 text-sm font-semibold">Grafo íntegro e pronto</p><p className="mt-1 text-xs text-muted-foreground">A ativação criará uma revisão imutável e auditável.</p></div>
            )}
          </ScrollArea>
          <DialogFooter className="border-t px-6 py-4">
            <Button variant="outline" onClick={() => setValidationOpen(false)}>Fechar</Button>
            <Button disabled={!validation.canActivate || saving || runtimeValidationLoading} onClick={() => { setValidationOpen(false); void persistFlow(true); }}><Play className="mr-2 h-4 w-4" />Ativar revisão</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={templatesOpen} onOpenChange={setTemplatesOpen}>
        <DialogContent className="max-h-[86vh] max-w-4xl overflow-hidden p-0">
          <DialogHeader className="border-b px-6 py-5">
            <DialogTitle className="flex items-center gap-2 text-lg"><LayoutTemplate className="h-4 w-4 text-primary" />Modelos de automação</DialogTitle>
            <DialogDescription>Modelos nativos versionados. A aplicação cria um rascunho independente para o seu workspace.</DialogDescription>
          </DialogHeader>
          <div className="px-6 pt-4">
            <div className="relative"><Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" /><Input value={templateSearch} onChange={(event) => setTemplateSearch(event.target.value)} className="h-9 pl-9" placeholder="Buscar por objetivo ou área" /></div>
          </div>
          <ScrollArea className="max-h-[58vh] px-6 py-4">
            {templatesLoading ? (
              <div className="flex items-center justify-center py-14"><Loader2 className="h-5 w-5 animate-spin" /></div>
            ) : filteredTemplates.length ? (
              <div className="grid gap-3 md:grid-cols-2">
                {filteredTemplates.map((template) => (
                  <button type="button" key={template.id} className="rounded-md border p-4 text-left transition-colors hover:border-primary hover:bg-accent/40" onClick={() => setPendingTemplate(template)}>
                    <div className="flex items-start justify-between gap-3"><strong className="min-w-0 truncate text-sm">{template.name}</strong><Badge variant="outline" className="shrink-0 text-xs">{template.practice_area}</Badge></div>
                    <p className="mt-2 line-clamp-2 text-xs leading-5 text-muted-foreground">{template.description || 'Modelo operacional versionado.'}</p>
                    <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground"><span className="tabular-nums">{template.nodes.length} etapas</span><span className="font-semibold text-foreground">Aplicar</span></div>
                  </button>
                ))}
              </div>
            ) : (
              <div className="py-14 text-center text-sm text-muted-foreground">Nenhum modelo corresponde à busca.</div>
            )}
          </ScrollArea>
        </DialogContent>
      </Dialog>

      <AlertDialog open={Boolean(pendingTemplate)} onOpenChange={(open) => { if (!open) setPendingTemplate(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Aplicar “{pendingTemplate?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>O grafo atual será substituído no rascunho. Você ainda poderá desfazer esta ação antes de sair.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction onClick={() => pendingTemplate && applyTemplate(pendingTemplate)}>Aplicar modelo</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={dryRunOpen} onOpenChange={setDryRunOpen}>
        <DialogContent className="max-h-[88vh] max-w-3xl overflow-hidden p-0">
          <DialogHeader className="border-b px-6 py-5">
            <DialogTitle className="flex items-center gap-2 text-lg"><FlaskConical className="h-4 w-4 text-primary" />Teste do fluxo</DialogTitle>
            <DialogDescription>Executa a revisão no contexto real do caso, sem enviar mensagens, acionar agentes externos ou criar uma execução.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 border-b px-6 py-4 sm:grid-cols-[1fr_auto]">
            <Select value={testMatterId} onValueChange={(value) => { setTestMatterId(value); setDryRun(null); setDryRunStep(-1); }} disabled={matterOptionsLoading}>
              <SelectTrigger className="h-9"><SelectValue placeholder={matterOptionsLoading ? 'Carregando casos...' : 'Selecionar caso'} /></SelectTrigger>
              <SelectContent>
                {matterOptions.map((matter) => <SelectItem key={matter.id} value={matter.id}>{matter.name} · {matter.subject}</SelectItem>)}
              </SelectContent>
            </Select>
            <Button className="h-9" onClick={() => void runDryRun()} disabled={dryRunLoading || matterOptionsLoading || !testMatterId}>
              {dryRunLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
              {dryRunLoading ? 'Simulando' : 'Executar teste'}
            </Button>
          </div>
          <div className="px-6 pt-4">
            <div className="flex items-center justify-between text-xs"><span>{dryRun ? dryRunStatusLabel(dryRun.status) : 'Aguardando simulação'}</span><strong className="tabular-nums">{Math.round(dryRunProgress)}%</strong></div>
            <Progress value={dryRunProgress} className="mt-2 h-2" />
          </div>
          <ScrollArea className="max-h-[52vh] px-6 py-4">
            {dryRun ? (
              <div className="space-y-2">
                {dryRun.trace.map((step, index) => {
                  const state = index < dryRunStep ? 'completed' : index === dryRunStep ? 'current' : 'upcoming';
                  return (
                    <button type="button" key={`${step.step}-${step.nodeId}`} className="flex w-full items-center gap-3 rounded-md border p-3 text-left hover:border-primary" onClick={() => focusNode(step.nodeId)}>
                      <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold tabular-nums ${state === 'completed' ? 'bg-emerald-600 text-white' : state === 'current' ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground'}`}>{state === 'completed' ? <CheckCircle2 className="h-4 w-4" /> : step.step}</span>
                      <span className="min-w-0 flex-1"><strong className="block truncate text-sm">{step.label}</strong><span className="mt-1 block text-xs text-muted-foreground">{step.type}{step.decision?.branch ? ` · ramo ${step.decision.branch === 'true' ? 'Sim' : 'Não'}` : ''}</span></span>
                      <Badge variant="outline" className="shrink-0 text-xs">{state === 'completed' ? 'Concluída' : state === 'current' ? 'Agora' : 'Próxima'}</Badge>
                    </button>
                  );
                })}
                <div className="mt-4 flex items-center justify-between border-t pt-4 text-xs"><span className="text-muted-foreground">Efeitos externos executados</span><strong className="tabular-nums">{dryRun.external_actions_executed}</strong></div>
              </div>
            ) : (
              <div className="py-12 text-center"><Activity className="mx-auto h-6 w-6 text-muted-foreground" /><p className="mt-3 text-sm font-semibold">Selecione um caso e execute o teste</p><p className="mt-1 text-xs text-muted-foreground">O replay aparecerá no canvas e nesta linha do tempo.</p></div>
            )}
          </ScrollArea>
        </DialogContent>
      </Dialog>

      <AlertDialog open={leaveDialogOpen} onOpenChange={setLeaveDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Descartar alterações não salvas?</AlertDialogTitle>
            <AlertDialogDescription>
              O rascunho atual do fluxo ainda não foi persistido. Esta ação não pode ser desfeita.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Continuar editando</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => navigate(returnTo)}
            >
              Descartar e sair
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
    </TooltipProvider>
  );
}
