import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
} from 'react';
import { useMutation } from '@tanstack/react-query';
import {
  Bot,
  Check,
  ChevronRight,
  File,
  FileImage,
  LoaderCircle,
  Paperclip,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Textarea } from '@/components/ui/textarea';
import {
  klmApi,
  type KlmFlowCopilotAttachment,
  type KlmFlowCopilotPlan,
} from '@/klm/api';
import { cn } from '@/lib/utils';

type LocalAttachment = KlmFlowCopilotAttachment & {
  id: string;
  kind: 'image' | 'document';
};

type QueueItem = {
  plan: KlmFlowCopilotPlan;
  state: 'pending' | 'applied' | 'dismissed';
  baseSignature: string;
  createdAt: string;
  expiresAt: string;
};

type ChatEntry = {
  id: string;
  role: 'user' | 'assistant' | 'system';
  body: string;
};

type FlowCopilotProps = {
  flowId?: string;
  flowName?: string;
  practiceArea?: string;
  workspaceId?: string;
  userId?: string;
  currentNodes: Array<Record<string, unknown>>;
  currentEdges: Array<Record<string, unknown>>;
  disabled?: boolean;
  onApply: (plan: KlmFlowCopilotPlan) => void;
};

const ACCEPTED_FILE_TYPES = [
  'image/png',
  'image/jpeg',
  'image/webp',
  'application/pdf',
  'text/plain',
  'text/csv',
  'application/json',
  'text/markdown',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
].join(',');

const TEXT_FILE_TYPES = new Set([
  'text/plain',
  'text/csv',
  'application/json',
  'text/markdown',
]);

const MAX_QUEUE_ITEMS = 20;
const QUEUE_TTL_MS = 24 * 60 * 60 * 1000;

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, canonicalize(item)]),
    );
  }
  return value;
}

function graphSignature(
  nodes: Array<Record<string, unknown>>,
  edges: Array<Record<string, unknown>>,
) {
  return JSON.stringify(canonicalize({ nodes, edges }));
}

function isQueueItem(value: unknown): value is QueueItem {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<QueueItem>;
  const plan = item.plan as Partial<KlmFlowCopilotPlan> | undefined;
  return Boolean(
    plan
    && typeof plan.proposal_id === 'string'
    && ['pending', 'applied', 'dismissed'].includes(String(item.state))
    && typeof item.baseSignature === 'string'
    && typeof item.createdAt === 'string'
    && typeof item.expiresAt === 'string',
  );
}

function readQueue(value: string | null): QueueItem[] {
  if (!value) return [];
  const parsed: unknown = JSON.parse(value);
  if (!Array.isArray(parsed)) return [];
  const now = Date.now();
  return parsed
    .filter(isQueueItem)
    .filter((item) => Date.parse(item.expiresAt) > now)
    .slice(0, MAX_QUEUE_ITEMS);
}

function queueLabel(state: QueueItem['state']) {
  if (state === 'applied') return 'Aplicada ao rascunho';
  if (state === 'dismissed') return 'Descartada';
  return 'Aguardando aprovação';
}

export function FlowCopilot({
  flowId,
  flowName,
  practiceArea = 'universal',
  workspaceId,
  userId,
  currentNodes,
  currentEdges,
  disabled = false,
  onApply,
}: FlowCopilotProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [attachments, setAttachments] = useState<LocalAttachment[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [chat, setChat] = useState<ChatEntry[]>([
    {
      id: 'welcome',
      role: 'assistant',
      body: 'Descreva o fluxo, as regras e os gates. Eu preparo uma proposta no canvas, sugiro melhorias e aguardo sua aprovação.',
    },
  ]);
  const [activeView, setActiveView] = useState<'chat' | 'queue'>('chat');
  const [loadedQueueKey, setLoadedQueueKey] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const currentGraphSignature = useMemo(
    () => graphSignature(currentNodes, currentEdges),
    [currentEdges, currentNodes],
  );
  const queueStorageKey = useMemo(
    () => [
      'meu-executivo:flow-copilot',
      workspaceId ?? 'workspace-unavailable',
      userId ?? 'anonymous',
      flowId ?? 'unselected',
    ].join(':'),
    [flowId, userId, workspaceId],
  );

  const planMutation = useMutation({
    mutationFn: (request: { instruction: string; baseSignature: string }) => klmApi.flowCopilotPlan({
      instruction: request.instruction,
      flow_id: flowId,
      practice_area: practiceArea,
      current_nodes: currentNodes,
      current_edges: currentEdges,
      attachments: attachments.map(({ name, media_type, size_bytes, text_excerpt }) => ({
        name,
        media_type,
        size_bytes,
        text_excerpt,
      })),
    }),
    onSuccess: (plan, request) => {
      const createdAt = new Date();
      const nextItem: QueueItem = {
        plan,
        state: 'pending',
        baseSignature: request.baseSignature,
        createdAt: createdAt.toISOString(),
        expiresAt: new Date(createdAt.getTime() + QUEUE_TTL_MS).toISOString(),
      };
      setQueue((current) => [nextItem, ...current].slice(0, MAX_QUEUE_ITEMS));
      setChat((current) => [
        ...current,
        { id: crypto.randomUUID(), role: 'assistant', body: plan.summary },
      ]);
      setAttachments([]);
    },
    onError: () => {
      setChat((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'system',
          body: 'Não consegui validar a proposta no runtime local. O flow atual não foi alterado.',
        },
      ]);
    },
  });

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ block: 'nearest' });
  }, [chat, planMutation.isPending]);

  useEffect(() => {
    try {
      const persisted = window.localStorage.getItem(queueStorageKey);
      setQueue(readQueue(persisted));
    } catch {
      setQueue([]);
    }
    setLoadedQueueKey(queueStorageKey);
  }, [queueStorageKey]);

  useEffect(() => {
    if (loadedQueueKey !== queueStorageKey) return;
    try {
      window.localStorage.setItem(
        queueStorageKey,
        JSON.stringify(queue.slice(0, MAX_QUEUE_ITEMS)),
      );
    } catch {
      setChat((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'system',
          body: 'A fila continua disponível nesta sessão, mas não pôde ser salva no navegador.',
        },
      ]);
    }
  }, [loadedQueueKey, queue, queueStorageKey]);

  async function ingestFiles(files: File[]) {
    const accepted: LocalAttachment[] = [];
    const rejected: string[] = [];
    for (const file of files) {
      const mediaType = file.type || 'application/octet-stream';
      if (mediaType.startsWith('audio/')) {
        rejected.push(`${file.name}: áudio não é aceito`);
        continue;
      }
      if (file.size > 20_000_000) {
        rejected.push(`${file.name}: limite individual de 20 MB`);
        continue;
      }
      const isImage = mediaType.startsWith('image/');
      const isAcceptedDocument = TEXT_FILE_TYPES.has(mediaType)
        || mediaType === 'application/pdf'
        || mediaType.includes('word');
      if (!isImage && !isAcceptedDocument) {
        rejected.push(`${file.name}: formato não suportado`);
        continue;
      }
      const textExcerpt = TEXT_FILE_TYPES.has(mediaType)
        ? (await file.text()).slice(0, 8000)
        : null;
      accepted.push({
        id: crypto.randomUUID(),
        name: file.name,
        media_type: mediaType,
        size_bytes: file.size,
        text_excerpt: textExcerpt,
        kind: isImage ? 'image' : 'document',
      });
    }
    const nextTotal = [...attachments, ...accepted].reduce((sum, item) => sum + item.size_bytes, 0);
    if (nextTotal > 40_000_000) {
      rejected.push('O conjunto ultrapassa o limite de 40 MB por solicitação');
    } else {
      setAttachments((current) => [...current, ...accepted].slice(0, 12));
    }
    if (rejected.length) {
      setChat((current) => [
        ...current,
        { id: crypto.randomUUID(), role: 'system', body: rejected.join(' · ') },
      ]);
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = '';
    void ingestFiles(files);
  }

  function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(event.clipboardData.files);
    if (!files.length) return;
    event.preventDefault();
    void ingestFiles(files);
  }

  function submitRequest() {
    const instruction = draft.trim();
    if (instruction.length < 3 || planMutation.isPending) return;
    setChat((current) => [
      ...current,
      { id: crypto.randomUUID(), role: 'user', body: instruction },
    ]);
    setDraft('');
    planMutation.mutate({
      instruction,
      baseSignature: currentGraphSignature,
    });
  }

  function updateQueue(proposalId: string, state: QueueItem['state']) {
    setQueue((current) => current.map((item) => (
      item.plan.proposal_id === proposalId ? { ...item, state } : item
    )));
  }

  function applyPlan(item: QueueItem) {
    if (item.baseSignature !== currentGraphSignature) {
      updateQueue(item.plan.proposal_id, 'dismissed');
      setChat((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'system',
          body: 'O canvas mudou desde esta proposta. Ela foi descartada para preservar suas edições; gere uma nova versão.',
        },
      ]);
      setActiveView('chat');
      return;
    }
    onApply(item.plan);
    updateQueue(item.plan.proposal_id, 'applied');
    setChat((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        role: 'assistant',
        body: 'Proposta aplicada somente ao rascunho visual. Salve e execute o dry-run para criar uma revisão persistida.',
      },
    ]);
    setActiveView('chat');
  }

  const pendingCount = queue.filter((item) => item.state === 'pending').length;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="icon"
          disabled={disabled}
          aria-label="Abrir copiloto de flows"
          title="Copiloto de flows"
          className="fixed bottom-20 right-4 z-40 h-12 w-12 rounded-full border border-white/20 bg-[#0066F7] text-white shadow-[0_14px_36px_rgba(0,70,190,.32)] hover:bg-[#0057d8] md:bottom-6 md:right-6"
        >
          <Sparkles className="h-5 w-5" />
          {pendingCount > 0 ? (
            <span className="absolute -right-1 -top-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-[#D99A12] px-1 text-[10px] font-bold text-[#031121]">
              {pendingCount}
            </span>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="end"
        sideOffset={10}
        className="mr-2 flex h-[min(720px,calc(100dvh-108px))] w-[min(430px,calc(100vw-24px))] flex-col overflow-hidden rounded-lg border-border p-0 shadow-2xl"
      >
        <header className="shrink-0 border-b border-border bg-[#031121] px-4 py-3 text-white">
          <div className="flex items-start gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#0066F7]">
              <Bot className="h-4 w-4" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-semibold">Copiloto de Flows</h3>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-7 w-7 text-white hover:bg-white/10 hover:text-white"
                  onClick={() => setOpen(false)}
                  aria-label="Fechar copiloto"
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
              <p className="mt-0.5 truncate text-[11px] text-white/65">
                {flowName ? `Editando ${flowName}` : 'Selecione um flow para começar'}
              </p>
            </div>
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5 text-[10px]">
            <span className="rounded-full bg-white/10 px-2 py-1">playbook + conta</span>
            <span className="rounded-full bg-white/10 px-2 py-1">aprovação obrigatória</span>
            <span className="rounded-full bg-white/10 px-2 py-1">sem áudio</span>
          </div>
        </header>

        <div
          className="grid shrink-0 grid-cols-2 border-b border-border bg-card p-1"
          role="tablist"
          aria-label="Visões do copiloto"
        >
          <button
            type="button"
            role="tab"
            aria-selected={activeView === 'chat'}
            className={cn(
              'h-8 rounded-md text-xs font-medium transition-colors',
              activeView === 'chat'
                ? 'bg-[#0066F7] text-white hover:bg-[#0057D8]'
                : 'text-muted-foreground hover:bg-[#0066F7]/8 hover:text-[#0057D8]',
            )}
            onClick={() => setActiveView('chat')}
          >
            Conversa
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeView === 'queue'}
            className={cn(
              'flex h-8 items-center justify-center gap-1.5 rounded-md text-xs font-medium transition-colors',
              activeView === 'queue'
                ? 'bg-[#0066F7] text-white hover:bg-[#0057D8]'
                : 'text-muted-foreground hover:bg-[#0066F7]/8 hover:text-[#0057D8]',
            )}
            onClick={() => setActiveView('queue')}
          >
            Fila
            <span className="tabular-nums">{queue.length}</span>
          </button>
        </div>

        {activeView === 'chat' ? (
          <>
            <div
              className="min-h-0 flex-1 space-y-3 overflow-y-auto bg-muted/20 px-3 py-3 scrollbar-thin"
              role="log"
              aria-live="polite"
              aria-relevant="additions text"
            >
              {chat.map((entry) => (
                <div
                  key={entry.id}
                  className={cn(
                    'max-w-[88%] rounded-lg border px-3 py-2 text-xs leading-5',
                    entry.role === 'user' && 'ml-auto border-primary/30 bg-primary text-primary-foreground',
                    entry.role === 'assistant' && 'border-border bg-card',
                    entry.role === 'system' && 'mx-auto max-w-full border-[#D99A12]/40 bg-[#D99A12]/10 text-[#725000]',
                  )}
                >
                  {entry.body}
                </div>
              ))}
              {planMutation.isPending ? (
                <div
                  className="flex w-fit items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-xs"
                  role="status"
                >
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin text-primary" />
                  Validando nós, conexões e gates...
                </div>
              ) : null}
              {queue[0]?.state === 'pending' ? (
                <ProposalCard
                  item={queue[0]}
                  onApply={() => applyPlan(queue[0])}
                  onAdjust={() => {
                    setDraft(`Ajuste a proposta ${queue[0].plan.proposal_id.slice(0, 8)} para `);
                    updateQueue(queue[0].plan.proposal_id, 'dismissed');
                  }}
                  onDismiss={() => updateQueue(queue[0].plan.proposal_id, 'dismissed')}
                />
              ) : null}
              <div ref={chatEndRef} />
            </div>

            <div className="shrink-0 border-t border-border bg-card p-3">
              {attachments.length ? (
                <div className="mb-2 flex max-h-20 flex-wrap gap-1.5 overflow-y-auto">
                  {attachments.map((attachment) => (
                    <span
                      key={attachment.id}
                      className="inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-md border border-border bg-muted/40 px-2 py-1 text-[10px]"
                    >
                      {attachment.kind === 'image'
                        ? <FileImage className="h-3 w-3 shrink-0 text-primary" />
                        : <File className="h-3 w-3 shrink-0 text-primary" />}
                      <span className="max-w-48 truncate">{attachment.name}</span>
                      <button
                        type="button"
                        aria-label={`Remover ${attachment.name}`}
                        onClick={() => setAttachments((current) => current.filter((item) => item.id !== attachment.id))}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </span>
                  ))}
                </div>
              ) : null}
              <div className="relative">
                <Textarea
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onPaste={handlePaste}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault();
                      submitRequest();
                    }
                  }}
                  className="min-h-[92px] resize-none pr-20 text-xs leading-5"
                  placeholder="Ex.: crie um fluxo para comentários, qualifique o lead, peça documentos e passe ao humano antes da proposta."
                  aria-label="Pedido para o copiloto de flows"
                />
                <div className="absolute bottom-2 right-2 flex gap-1">
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    className="h-8 w-8"
                    onClick={() => fileInputRef.current?.click()}
                    aria-label="Anexar imagem ou documento"
                    title="Anexar imagem ou documento"
                  >
                    <Paperclip className="h-4 w-4" />
                  </Button>
                  <Button
                    type="button"
                    size="icon"
                    className="h-8 w-8 bg-[#0066F7] text-white hover:bg-[#0057D8] disabled:bg-[#0066F7]/35 disabled:text-white"
                    disabled={draft.trim().length < 3 || planMutation.isPending}
                    onClick={submitRequest}
                    aria-label="Enviar pedido ao copiloto"
                  >
                    {planMutation.isPending
                      ? <LoaderCircle className="h-4 w-4 animate-spin" />
                      : <Send className="h-4 w-4" />}
                  </Button>
                </div>
              </div>
              <input
                ref={fileInputRef}
                className="hidden"
                type="file"
                multiple
                accept={ACCEPTED_FILE_TYPES}
                onChange={handleFileChange}
              />
              <p className="mt-1.5 text-[10px] text-muted-foreground">
                Textos compatíveis são lidos localmente. PDF, Word e imagens entram como referência até extração ou OCR; áudio é bloqueado.
              </p>
            </div>
          </>
        ) : (
          <div
            className="min-h-0 flex-1 space-y-2 overflow-y-auto bg-muted/20 p-3 scrollbar-thin"
            role="tabpanel"
            aria-label="Fila de propostas"
          >
            {!queue.length ? (
              <div className="rounded-lg border border-dashed border-border bg-card p-5 text-center">
                <ShieldCheck className="mx-auto h-5 w-5 text-primary" />
                <p className="mt-2 text-xs font-medium">A fila está vazia</p>
                <p className="mt-1 text-[11px] text-muted-foreground">As propostas aparecem aqui antes de alterar o canvas.</p>
              </div>
            ) : queue.map((item) => (
              <div key={item.plan.proposal_id} className="rounded-lg border border-border bg-card p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="line-clamp-2 text-xs font-medium">{item.plan.summary}</p>
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      {item.plan.added_node_ids.length} etapas · {queueLabel(item.state)}
                    </p>
                  </div>
                  {item.state === 'pending' ? (
                    <Button type="button" size="sm" className="h-7 shrink-0 text-[10px]" onClick={() => applyPlan(item)}>
                      <Check className="mr-1 h-3 w-3" />
                      Aplicar
                    </Button>
                  ) : null}
                </div>
                {item.plan.playbook_name ? (
                  <p className="mt-2 text-[10px] text-muted-foreground">
                    {item.plan.playbook_name} · v{item.plan.playbook_version}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}

function ProposalCard({
  item,
  onApply,
  onAdjust,
  onDismiss,
}: {
  item: QueueItem;
  onApply: () => void;
  onAdjust: () => void;
  onDismiss: () => void;
}) {
  const { plan } = item;
  return (
    <div className="rounded-lg border border-primary/30 bg-card p-3 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-xs font-semibold">Proposta pronta para revisão</p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {plan.added_node_ids.length} novas etapas · 0 ações executadas
          </p>
        </div>
        <span className="rounded-full bg-[#D99A12]/15 px-2 py-1 text-[9px] font-semibold uppercase text-[#725000]">
          aprovação
        </span>
      </div>
      <div className="mt-3 space-y-1.5">
        {plan.suggestions.slice(0, 3).map((suggestion) => (
          <p key={suggestion} className="flex gap-1.5 text-[11px] leading-4">
            <ChevronRight className="mt-0.5 h-3 w-3 shrink-0 text-primary" />
            <span>{suggestion}</span>
          </p>
        ))}
      </div>
      {plan.questions.length ? (
        <div className="mt-3 rounded-md bg-muted/60 px-2.5 py-2 text-[10px] leading-4">
          <span className="font-semibold">Antes de publicar:</span> {plan.questions.join(' ')}
        </div>
      ) : null}
      <div className="mt-3 grid grid-cols-[1fr_auto] gap-2">
        <Button type="button" size="sm" className="h-8 text-xs" onClick={onApply}>
          <Check className="mr-1.5 h-3.5 w-3.5" />
          Aprovar e aplicar
        </Button>
        <Button type="button" size="icon" variant="outline" className="h-8 w-8" onClick={onDismiss} aria-label="Descartar proposta">
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>
      <Button type="button" variant="ghost" size="sm" className="mt-1 h-7 w-full text-[10px]" onClick={onAdjust}>
        Pedir ajustes antes de aplicar
      </Button>
    </div>
  );
}
