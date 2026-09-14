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
  LockKeyhole,
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
  type KlmOperationalCopilotAction,
  type KlmOperationalCopilotAttachment,
  type KlmOperationalCopilotDomain,
  type KlmOperationalCopilotPlan,
} from '@/klm/api';
import { cn } from '@/lib/utils';

type LocalAttachment = KlmOperationalCopilotAttachment & {
  id: string;
  kind: 'image' | 'document';
};

type QueueItem = {
  plan: KlmOperationalCopilotPlan;
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

type OperationalCopilotProps = {
  domain: KlmOperationalCopilotDomain;
  title: string;
  subtitle: string;
  welcome: string;
  placeholder: string;
  contextKey?: string;
  contextSnapshot: Record<string, unknown>;
  workspaceId?: string;
  userId?: string;
  disabled?: boolean;
  canApply?: boolean;
  onApply: (
    actions: KlmOperationalCopilotAction[],
    plan: KlmOperationalCopilotPlan,
  ) => Promise<string | void> | string | void;
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
const SECRET_KEY_PATTERN = /(access.?token|api.?key|app.?secret|authorization|client.?secret|credential|password|private.?key|registration.?pin|secret|token)/i;
const MAX_QUEUE_ITEMS = 20;
const QUEUE_TTL_MS = 24 * 60 * 60 * 1000;

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([key]) => !SECRET_KEY_PATTERN.test(key))
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, canonicalize(item)]),
    );
  }
  return value;
}

function safeSnapshot(value: Record<string, unknown>) {
  const canonical = canonicalize(value) as Record<string, unknown>;
  const serialized = JSON.stringify(canonical);
  if (serialized.length <= 50_000) return canonical;
  return { context_truncated: true };
}

function contextSignature(value: Record<string, unknown>) {
  return JSON.stringify(safeSnapshot(value));
}

function isQueueItem(value: unknown): value is QueueItem {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<QueueItem>;
  const plan = item.plan as Partial<KlmOperationalCopilotPlan> | undefined;
  return Boolean(
    plan
    && typeof plan.proposal_id === 'string'
    && Array.isArray(plan.actions)
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
  if (state === 'applied') return 'Aplicada à interface';
  if (state === 'dismissed') return 'Descartada';
  return 'Aguardando aprovação';
}

export function OperationalCopilot({
  domain,
  title,
  subtitle,
  welcome,
  placeholder,
  contextKey = 'page',
  contextSnapshot,
  workspaceId,
  userId,
  disabled = false,
  canApply = true,
  onApply,
}: OperationalCopilotProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [attachments, setAttachments] = useState<LocalAttachment[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [chat, setChat] = useState<ChatEntry[]>([
    { id: 'welcome', role: 'assistant', body: welcome },
  ]);
  const [activeView, setActiveView] = useState<'chat' | 'queue'>('chat');
  const [loadedQueueKey, setLoadedQueueKey] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const sanitizedSnapshot = useMemo(
    () => safeSnapshot(contextSnapshot),
    [contextSnapshot],
  );
  const currentContextSignature = useMemo(
    () => contextSignature(sanitizedSnapshot),
    [sanitizedSnapshot],
  );
  const queueStorageKey = useMemo(
    () => [
      'meu-executivo:operational-copilot',
      workspaceId ?? 'workspace-unavailable',
      userId ?? 'anonymous',
      domain,
      contextKey,
    ].join(':'),
    [contextKey, domain, userId, workspaceId],
  );

  const planMutation = useMutation({
    mutationFn: (request: { instruction: string; baseSignature: string }) => (
      klmApi.operationalCopilotPlan({
        domain,
        instruction: request.instruction,
        context_key: contextKey,
        context_snapshot: sanitizedSnapshot,
        attachments: attachments.map(({ name, media_type, size_bytes, text_excerpt }) => ({
          name,
          media_type,
          size_bytes,
          text_excerpt,
        })),
      })
    ),
    onSuccess: (plan, request) => {
      const createdAt = new Date();
      const queueItem: QueueItem = {
        plan,
        state: 'pending',
        baseSignature: request.baseSignature,
        createdAt: createdAt.toISOString(),
        expiresAt: new Date(createdAt.getTime() + QUEUE_TTL_MS).toISOString(),
      };
      setQueue((current) => [queueItem, ...current].slice(0, MAX_QUEUE_ITEMS));
      setChat((current) => [
        ...current,
        { id: crypto.randomUUID(), role: 'assistant', body: plan.summary },
      ]);
      setAttachments([]);
    },
    onError: (error: Error) => {
      setChat((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'system',
          body: error.message || 'Não consegui preparar a proposta. A página não foi alterada.',
        },
      ]);
    },
  });

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ block: 'nearest' });
  }, [chat, planMutation.isPending]);

  useEffect(() => {
    try {
      setQueue(readQueue(window.localStorage.getItem(queueStorageKey)));
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
      // The queue remains available in memory for this browser session.
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
      accepted.push({
        id: crypto.randomUUID(),
        name: file.name,
        media_type: mediaType,
        size_bytes: file.size,
        text_excerpt: TEXT_FILE_TYPES.has(mediaType)
          ? (await file.text()).slice(0, 8000)
          : null,
        kind: isImage ? 'image' : 'document',
      });
    }
    const total = [...attachments, ...accepted].reduce(
      (sum, item) => sum + item.size_bytes,
      0,
    );
    if (total > 40_000_000) {
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
      baseSignature: currentContextSignature,
    });
  }

  function updateQueue(proposalId: string, state: QueueItem['state']) {
    setQueue((current) => current.map((item) => (
      item.plan.proposal_id === proposalId ? { ...item, state } : item
    )));
  }

  async function applyPlan(item: QueueItem) {
    if (item.baseSignature !== currentContextSignature) {
      updateQueue(item.plan.proposal_id, 'dismissed');
      setChat((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'system',
          body: 'A página mudou desde esta proposta. Ela foi descartada para preservar o estado atual; gere uma nova versão.',
        },
      ]);
      setActiveView('chat');
      return;
    }
    if (!canApply) {
      setChat((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'system',
          body: 'Sua permissão permite consultar o copiloto, mas não aplicar esta mudança.',
        },
      ]);
      setActiveView('chat');
      return;
    }
    try {
      const result = await onApply(item.plan.actions, item.plan);
      updateQueue(item.plan.proposal_id, 'applied');
      setChat((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          body: result || 'Proposta aplicada à interface. Revise e confirme na própria página para persistir.',
        },
      ]);
      setActiveView('chat');
    } catch (error) {
      setChat((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'system',
          body: error instanceof Error ? error.message : 'A proposta não pôde ser aplicada.',
        },
      ]);
      setActiveView('chat');
    }
  }

  const pendingCount = queue.filter((item) => item.state === 'pending').length;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="icon"
          disabled={disabled}
          aria-label={`Abrir ${title.toLocaleLowerCase('pt-BR')}`}
          title={title}
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
                <h3 className="text-sm font-semibold">{title}</h3>
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
              <p className="mt-0.5 truncate text-[11px] text-white/65">{subtitle}</p>
            </div>
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5 text-[10px]">
            <span className="rounded-full bg-white/10 px-2 py-1">playbook + conta</span>
            <span className="rounded-full bg-white/10 px-2 py-1">aprovação obrigatória</span>
            <span className="rounded-full bg-white/10 px-2 py-1">zero ação externa</span>
          </div>
        </header>

        <div
          className="grid shrink-0 grid-cols-2 border-b border-border bg-card p-1"
          role="tablist"
          aria-label="Visões do copiloto"
        >
          {(['chat', 'queue'] as const).map((view) => (
            <button
              key={view}
              type="button"
              role="tab"
              aria-selected={activeView === view}
              className={cn(
                'flex h-8 items-center justify-center gap-1.5 rounded-md text-xs font-medium transition-colors',
                activeView === view
                  ? 'bg-[#0066F7] text-white hover:bg-[#0057D8]'
                  : 'text-muted-foreground hover:bg-[#0066F7]/8 hover:text-[#0057D8]',
              )}
              onClick={() => setActiveView(view)}
            >
              {view === 'chat' ? 'Conversa' : 'Fila'}
              {view === 'queue' ? <span className="tabular-nums">{queue.length}</span> : null}
            </button>
          ))}
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
                    entry.role === 'user' && 'ml-auto border-[#0066F7]/30 bg-[#0066F7] text-white',
                    entry.role === 'assistant' && 'border-border bg-card',
                    entry.role === 'system' && 'mx-auto max-w-full border-[#D99A12]/40 bg-[#D99A12]/10 text-[#725000]',
                  )}
                >
                  {entry.body}
                </div>
              ))}
              {planMutation.isPending ? (
                <div className="flex w-fit items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-xs" role="status">
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin text-[#0066F7]" />
                  Validando contexto, permissões e ações...
                </div>
              ) : null}
              {queue[0]?.state === 'pending' ? (
                <ProposalCard
                  item={queue[0]}
                  canApply={canApply}
                  onApply={() => void applyPlan(queue[0])}
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
                    <span key={attachment.id} className="inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-md border border-border bg-muted/40 px-2 py-1 text-[10px]">
                      {attachment.kind === 'image'
                        ? <FileImage className="h-3 w-3 shrink-0 text-[#0066F7]" />
                        : <File className="h-3 w-3 shrink-0 text-[#0066F7]" />}
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
                  placeholder={placeholder}
                  aria-label={`Pedido para ${title.toLocaleLowerCase('pt-BR')}`}
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
                Textos compatíveis são lidos localmente. Imagens, PDF e Word exigem extração posterior; áudio e segredos são bloqueados.
              </p>
            </div>
          </>
        ) : (
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto bg-muted/20 p-3 scrollbar-thin" role="tabpanel" aria-label="Fila de propostas">
            {!queue.length ? (
              <div className="rounded-lg border border-dashed border-border bg-card p-5 text-center">
                <ShieldCheck className="mx-auto h-5 w-5 text-[#0066F7]" />
                <p className="mt-2 text-xs font-medium">A fila está vazia</p>
                <p className="mt-1 text-[11px] text-muted-foreground">As propostas aparecem aqui antes de alterar a página.</p>
              </div>
            ) : queue.map((item) => (
              <div key={item.plan.proposal_id} className="rounded-lg border border-border bg-card p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="line-clamp-2 text-xs font-medium">{item.plan.summary}</p>
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      {item.plan.actions.length} ações · {queueLabel(item.state)}
                    </p>
                  </div>
                  {item.state === 'pending' ? (
                    <Button
                      type="button"
                      size="sm"
                      className="h-7 shrink-0 bg-[#0066F7] text-[10px] text-white hover:bg-[#0057D8]"
                      disabled={!canApply}
                      onClick={() => void applyPlan(item)}
                    >
                      <Check className="mr-1 h-3 w-3" />
                      Aplicar
                    </Button>
                  ) : null}
                </div>
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
  canApply,
  onApply,
  onAdjust,
  onDismiss,
}: {
  item: QueueItem;
  canApply: boolean;
  onApply: () => void;
  onAdjust: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="rounded-lg border border-[#0066F7]/30 bg-card p-3 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-xs font-semibold">Proposta pronta para revisão</p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {item.plan.actions.length} ações locais · 0 ações externas
          </p>
        </div>
        <span className="rounded-full bg-[#D99A12]/15 px-2 py-1 text-[9px] font-semibold uppercase text-[#725000]">
          aprovação
        </span>
      </div>
      <div className="mt-3 space-y-2">
        {item.plan.actions.map((action) => (
          <div key={action.action_id} className="flex gap-1.5 text-[11px] leading-4">
            <ChevronRight className="mt-0.5 h-3 w-3 shrink-0 text-[#0066F7]" />
            <span><strong>{action.label}:</strong> {action.description}</span>
          </div>
        ))}
      </div>
      {!canApply ? (
        <p className="mt-3 flex items-center gap-1.5 rounded-md bg-muted/60 px-2.5 py-2 text-[10px]">
          <LockKeyhole className="h-3 w-3" />
          Consulte um usuário com permissão para aplicar.
        </p>
      ) : null}
      <div className="mt-3 grid grid-cols-[1fr_auto] gap-2">
        <Button
          type="button"
          size="sm"
          className="h-8 bg-[#0066F7] text-xs text-white hover:bg-[#0057D8]"
          disabled={!canApply || item.plan.actions.length === 0}
          onClick={onApply}
        >
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
