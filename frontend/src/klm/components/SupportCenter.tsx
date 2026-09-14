import {
  type ChangeEvent,
  type MouseEvent as ReactMouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  AlertCircle,
  ArrowLeft,
  Bot,
  Bug,
  Calendar,
  Camera,
  CheckCircle2,
  Clock3,
  FileText,
  EyeOff,
  Inbox,
  Lightbulb,
  LoaderCircle,
  MessageSquare,
  Paperclip,
  PhoneCall,
  Plus,
  RefreshCw,
  RotateCcw,
  Send,
  Star,
  Trash2,
  WifiOff,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  klmApi,
  type KlmSupportPriority,
  type KlmSupportSeverity,
  type KlmSupportStatus,
  type KlmSupportTicket,
  type KlmSupportTicketCreate,
  type KlmSupportTicketType,
} from '@/klm/api';
import { cn } from '@/lib/utils';
import { getSupportEscalationStatus } from '@/services/platform-support-settings';
import { collectSupportTechnicalContext, sanitizeSupportFailureIds } from '@/klm/support-context';
import { useAppStore } from '@/store/app-context';
import {
  countQueuedSupportOperations,
  flushQueuedSupportOperations,
  submitSupportMessage,
  submitSupportTicket,
} from '@/klm/support-offline-queue';
import { KlmPill } from './Primitives';

type SupportCenterProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currentRoute: string;
  initialTicketId?: string | null;
  supportLabel?: string;
  welcomeMessage?: string;
};

type Draft = {
  ticketType: KlmSupportTicketType;
  subject: string;
  description: string;
  priority: KlmSupportPriority;
  severity: KlmSupportSeverity;
};

type Notice = {
  tone: 'success' | 'warning' | 'error';
  text: string;
};

const EMPTY_DRAFT: Draft = {
  ticketType: 'problem',
  subject: '',
  description: '',
  priority: 'normal',
  severity: 'medium',
};
const MAX_ATTACHMENTS = 5;
const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

const typeOptions: Array<{
  value: KlmSupportTicketType;
  label: string;
  icon: typeof Bug;
}> = [
  { value: 'problem', label: 'Problema', icon: Bug },
  { value: 'instability', label: 'Instabilidade', icon: Activity },
  { value: 'suggestion', label: 'Sugestão', icon: Lightbulb },
];

const statusLabels: Record<KlmSupportStatus, string> = {
  new: 'Recebido',
  triage: 'Em triagem',
  in_progress: 'Em andamento',
  waiting_customer: 'Aguardando você',
  waiting_internal: 'Análise interna',
  validating: 'Em validação',
  resolved: 'Resolvido',
  closed: 'Encerrado',
  cancelled: 'Cancelado',
};

const priorityLabels: Record<KlmSupportPriority, string> = {
  low: 'Baixa',
  normal: 'Normal',
  high: 'Alta',
  urgent: 'Urgente',
};

const severityLabels: Record<KlmSupportSeverity, string> = {
  low: 'Baixa',
  medium: 'Média',
  high: 'Alta',
  critical: 'Crítica',
};

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Data indisponível';
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(date);
}

function formatBytes(bytes: number | null): string {
  if (bytes === null) return 'tamanho em análise';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function statusTone(status: KlmSupportStatus): 'gold' | 'green' | 'red' | 'blue' | 'muted' {
  if (status === 'resolved' || status === 'closed') return 'green';
  if (status === 'cancelled') return 'red';
  if (status === 'waiting_customer') return 'gold';
  if (status === 'new' || status === 'triage') return 'blue';
  return 'muted';
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'A operação não pôde ser concluída.';
}

function displayCaptureAllowed(): boolean {
  const policyDocument = document as Document & {
    featurePolicy?: { allowsFeature: (feature: string) => boolean };
    permissionsPolicy?: { allowsFeature: (feature: string) => boolean };
  };
  const policy = policyDocument.permissionsPolicy ?? policyDocument.featurePolicy;
  return policy?.allowsFeature('display-capture') ?? true;
}

function mergeFiles(current: File[], incoming: File[], reserved = 0): { files: File[]; error?: string } {
  const accepted: File[] = [];
  for (const file of incoming) {
    if (file.size > MAX_ATTACHMENT_BYTES) {
      return { files: current, error: `“${file.name}” excede o limite de 10 MB.` };
    }
    const duplicate = [...current, ...accepted].some(
      (item) => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified,
    );
    if (!duplicate) accepted.push(file);
  }
  if (current.length + accepted.length + reserved > MAX_ATTACHMENTS) {
    return { files: current, error: `Selecione no máximo ${MAX_ATTACHMENTS} anexos.` };
  }
  return { files: [...current, ...accepted] };
}

function TicketListItem({
  ticket,
  onSelect,
}: {
  ticket: KlmSupportTicket;
  onSelect: (ticketId: string) => void;
}) {
  return (
    <button
      type="button"
      className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-border px-4 py-3.5 text-left transition-colors last:border-b-0 hover:bg-secondary/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      onClick={() => onSelect(ticket.id)}
      aria-label={`Abrir chamado ${ticket.publicNumber}: ${ticket.subject}`}
    >
      <span className="min-w-0">
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[10px] font-semibold text-primary">{ticket.publicNumber}</span>
          <KlmPill tone={statusTone(ticket.status)}>{statusLabels[ticket.status]}</KlmPill>
        </span>
        <strong className="mt-1.5 block truncate text-xs">{ticket.subject}</strong>
        <span className="mt-1 block text-[10px] text-muted-foreground">
          {formatDate(ticket.updatedAt)} · {severityLabels[ticket.severity]}
        </span>
      </span>
      <span className="text-[10px] font-semibold text-muted-foreground">Ver</span>
    </button>
  );
}

export function SupportCenter({
  open,
  onOpenChange,
  currentRoute,
  initialTicketId = null,
  supportLabel = 'Central de SAC',
  welcomeMessage = '',
}: SupportCenterProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const { state } = useAppStore();
  const attachmentInputRef = useRef<HTMLInputElement>(null);
  const messageAttachmentInputRef = useRef<HTMLInputElement>(null);
  const [activeTab, setActiveTab] = useState('chat');
  const [assistantMessages, setAssistantMessages] = useState<Array<{ id: number; role: 'assistant' | 'user'; body: string }>>([
    { id: 1, role: 'assistant', body: 'Olá. Sou a IA de atendimento do Meu Executivo. Conte o que aconteceu e eu tento orientar primeiro. Você pode pedir atendimento humano a qualquer momento.' },
  ]);
  const [assistantInput, setAssistantInput] = useState('');
  const [selectedTicketId, setSelectedTicketId] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<'all' | 'open' | 'closed'>('all');
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [attachments, setAttachments] = useState<File[]>([]);
  const [captureFile, setCaptureFile] = useState<File | null>(null);
  const [capturePreview, setCapturePreview] = useState<string | null>(null);
  const [messageBody, setMessageBody] = useState('');
  const [messageAttachments, setMessageAttachments] = useState<File[]>([]);
  const [ratingScore, setRatingScore] = useState(0);
  const [ratingComment, setRatingComment] = useState('');
  const [queueCount, setQueueCount] = useState(0);
  const [realtimeStatus, setRealtimeStatus] = useState<string | null>(null);
  const [failureIds, setFailureIds] = useState<string[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [redactionEnabled, setRedactionEnabled] = useState(false);
  const [redacting, setRedacting] = useState(false);
  const [notResolvedClicked, setNotResolvedClicked] = useState(false);
  const queueScope = useMemo(() => {
    const workspaceId = state.session?.activeWorkspace?.id;
    const userId = state.currentUser?.id;
    return workspaceId && userId ? { workspaceId, userId } : null;
  }, [state.currentUser?.id, state.session?.activeWorkspace?.id]);

  const ticketsQuery = useQuery({
    queryKey: ['klm', 'support', 'tickets'],
    queryFn: klmApi.supportTickets,
    enabled: open,
    staleTime: 15_000,
  });
  const detailQuery = useQuery({
    queryKey: ['klm', 'support', 'tickets', selectedTicketId],
    queryFn: () => klmApi.supportTicket(selectedTicketId!),
    enabled: open && Boolean(selectedTicketId),
  });
  const escalationStatusQuery = useQuery({
    queryKey: ['support-escalation-status'],
    queryFn: getSupportEscalationStatus,
    enabled: open && Boolean(selectedTicketId),
    staleTime: 30_000,
    retry: false,
  });
  const selectedTicket = detailQuery.data;

  const refreshQueueCount = useCallback(async () => {
    if (!queueScope) {
      setQueueCount(0);
      return;
    }
    try {
      setQueueCount(await countQueuedSupportOperations(queueScope));
    } catch {
      setQueueCount(0);
    }
  }, [queueScope]);

  const syncOfflineQueue = useCallback(async () => {
    if (!queueScope) return;
    try {
      const result = await flushQueuedSupportOperations(queueScope);
      setQueueCount(result.remaining);
      if (result.confirmedTicketIds.length || result.confirmedMessageIds.length) {
        await queryClient.invalidateQueries({ queryKey: ['klm', 'support'] });
        setNotice({
          tone: 'success',
          text: 'Os envios pendentes foram confirmados pelo SAC.',
        });
      }
    } catch {
      await refreshQueueCount();
    }
  }, [queryClient, queueScope, refreshQueueCount]);

  useEffect(() => {
    if (open) void syncOfflineQueue();
  }, [open, syncOfflineQueue]);

  useEffect(() => {
    if (!open || !initialTicketId) return;
    setActiveTab('tickets');
    setSelectedTicketId(initialTicketId);
  }, [initialTicketId, open]);

  useEffect(() => {
    if (open && !initialTicketId) setActiveTab('chat');
  }, [initialTicketId, open]);

  const requestHumanSupport = useCallback(() => {
    const lastUserMessage = [...assistantMessages].reverse().find((message) => message.role === 'user')?.body ?? '';
    setDraft((current) => ({
      ...current,
      subject: current.subject || 'Atendimento solicitado pelo chat',
      description: current.description || lastUserMessage,
    }));
    setActiveTab('new');
  }, [assistantMessages]);

  const sendAssistantMessage = () => {
    const body = assistantInput.trim();
    if (!body) return;
    const wantsHuman = /\b(humano|humana|atendente|pessoa|especialista)\b/.test(body.toLocaleLowerCase('pt-BR'));
    const nextId = Date.now();
    setAssistantMessages((current) => [
      ...current,
      { id: nextId, role: 'user', body },
      {
        id: nextId + 1,
        role: 'assistant',
        body: wantsHuman
          ? 'Entendi. Vou preparar o encaminhamento para uma pessoa da equipe com a sua descrição já preenchida.'
          : 'Recebi o contexto. Verifique primeiro se a tela mantém o mesmo comportamento após atualizar os dados. Se continuar, envie a mensagem de erro ou uma captura. Posso transformar esta conversa em chamado ou encaminhar para uma pessoa da equipe.',
      },
    ]);
    setAssistantInput('');
    if (wantsHuman) {
      setDraft((current) => ({ ...current, subject: 'Atendimento humano solicitado', description: body }));
      setActiveTab('new');
    }
  };

  useEffect(() => {
    const handleOnline = () => void syncOfflineQueue();
    const handleRealtimeStatus = (event: Event) => {
      const value = (event as CustomEvent<{ status?: unknown }>).detail?.status;
      if (typeof value === 'string' && /^[a-z_-]{1,40}$/i.test(value)) {
        const normalized = value.toLowerCase();
        setRealtimeStatus(normalized);
        if (normalized === 'connected') {
          void queryClient.invalidateQueries({ queryKey: ['klm', 'support'] });
          void syncOfflineQueue();
        }
      }
    };
    const handleRealtime = (event: Event) => {
      const name = (event as CustomEvent<{ event?: unknown }>).detail?.event;
      if (typeof name === 'string' && name.startsWith('SUPPORT_')) {
        void queryClient.invalidateQueries({ queryKey: ['klm', 'support'] });
      }
    };
    const handleFailure = (event: Event) => {
      const detail = (event as CustomEvent<{ requestId?: unknown; errorId?: unknown }>).detail;
      setFailureIds((current) => sanitizeSupportFailureIds([
        ...current,
        detail?.requestId,
        detail?.errorId,
      ]));
    };
    window.addEventListener('online', handleOnline);
    window.addEventListener('klm:realtime-status', handleRealtimeStatus);
    window.addEventListener('klm:realtime', handleRealtime);
    window.addEventListener('klm:support-failure', handleFailure);
    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('klm:realtime-status', handleRealtimeStatus);
      window.removeEventListener('klm:realtime', handleRealtime);
      window.removeEventListener('klm:support-failure', handleFailure);
    };
  }, [queryClient, syncOfflineQueue]);

  useEffect(() => () => {
    if (capturePreview) URL.revokeObjectURL(capturePreview);
  }, [capturePreview]);

  useEffect(() => {
    if (!selectedTicket) return;
    setRatingScore(selectedTicket.satisfactionScore ?? 0);
    setRatingComment(selectedTicket.satisfactionComment ?? '');
  }, [selectedTicket]);

  useEffect(() => {
    setNotResolvedClicked(false);
  }, [selectedTicketId]);

  const filteredTickets = useMemo(() => {
    const tickets = ticketsQuery.data?.items ?? [];
    if (statusFilter === 'open') {
      return tickets.filter((ticket) => !['resolved', 'closed', 'cancelled'].includes(ticket.status));
    }
    if (statusFilter === 'closed') {
      return tickets.filter((ticket) => ['resolved', 'closed', 'cancelled'].includes(ticket.status));
    }
    return tickets;
  }, [statusFilter, ticketsQuery.data?.items]);

  const resetDraft = () => {
    setDraft(EMPTY_DRAFT);
    setAttachments([]);
    setCaptureFile(null);
    setCapturePreview(null);
    setRedactionEnabled(false);
  };

  const ticketMutation = useMutation({
    mutationFn: async () => {
      if (!queueScope) throw new Error('Conta ou usuário não identificado para o envio seguro.');
      if (draft.subject.trim().length < 3) throw new Error('Informe um assunto com pelo menos 3 caracteres.');
      if (draft.description.trim().length < 10) throw new Error('Descreva o pedido com pelo menos 10 caracteres.');
      const payload: KlmSupportTicketCreate = {
        ticketType: draft.ticketType,
        subject: draft.subject.trim(),
        description: draft.description.trim(),
        priority: draft.priority,
        severity: draft.severity,
        technicalContext: collectSupportTechnicalContext({
          route: currentRoute,
          realtimeStatus,
          failureIds,
        }),
      };
      return submitSupportTicket(queueScope, payload, [
        ...attachments,
        ...(captureFile ? [captureFile] : []),
      ]);
    },
    onSuccess: async (result) => {
      resetDraft();
      await refreshQueueCount();
      if (result.state === 'queued') {
        setNotice({
          tone: 'warning',
          text: 'Envio guardado neste dispositivo. O número do chamado aparecerá somente após a confirmação do SAC.',
        });
        toast({
          title: 'Envio pendente',
          description: 'A fila tentará novamente quando a conexão voltar.',
        });
        return;
      }
      await queryClient.invalidateQueries({ queryKey: ['klm', 'support'] });
      setSelectedTicketId(result.record?.id ?? null);
      setActiveTab('tickets');
      setNotice({
        tone: 'success',
        text: result.failedAttachments
          ? `Chamado confirmado. ${result.failedAttachments} anexo(s) não foram enviados.`
          : result.pendingAttachments
          ? `Chamado confirmado. ${result.pendingAttachments} anexo(s) aguardam sincronização.`
          : `Chamado ${result.record?.publicNumber ?? ''} confirmado pelo SAC.`,
      });
      toast({
        title: 'Chamado aberto',
        description: result.record?.publicNumber,
      });
    },
    onError: (error) => {
      setNotice({ tone: 'error', text: errorMessage(error) });
    },
  });

  const messageMutation = useMutation({
    mutationFn: async () => {
      if (!selectedTicketId) throw new Error('Chamado não selecionado.');
      if (!queueScope) throw new Error('Conta ou usuário não identificado para o envio seguro.');
      if (!messageBody.trim()) throw new Error('Escreva uma mensagem antes de enviar.');
      return submitSupportMessage(queueScope, selectedTicketId, messageBody.trim(), messageAttachments);
    },
    onSuccess: async (result) => {
      setMessageBody('');
      setMessageAttachments([]);
      await refreshQueueCount();
      if (result.state === 'queued') {
        setNotice({
          tone: 'warning',
          text: 'Mensagem guardada neste dispositivo. Ela aparecerá no histórico após a confirmação do SAC.',
        });
        return;
      }
      await queryClient.invalidateQueries({
        queryKey: ['klm', 'support', 'tickets', selectedTicketId],
      });
      setNotice({
        tone: 'success',
        text: result.failedAttachments
          ? `Mensagem confirmada. ${result.failedAttachments} anexo(s) não foram enviados.`
          : result.pendingAttachments
          ? `Mensagem confirmada. ${result.pendingAttachments} anexo(s) aguardam sincronização.`
          : 'Mensagem confirmada no chamado.',
      });
    },
    onError: (error) => setNotice({ tone: 'error', text: errorMessage(error) }),
  });

  const ticketActionMutation = useMutation({
    mutationFn: async (action: 'close' | 'reopen' | 'rate') => {
      if (!selectedTicketId) throw new Error('Chamado não selecionado.');
      if (action === 'close') return klmApi.closeSupportTicket(selectedTicketId);
      if (action === 'reopen') return klmApi.reopenSupportTicket(selectedTicketId);
      if (!ratingScore) throw new Error('Selecione uma nota de 1 a 5.');
      return klmApi.rateSupportTicket(selectedTicketId, ratingScore, ratingComment);
    },
    onSuccess: async (_, action) => {
      await queryClient.invalidateQueries({ queryKey: ['klm', 'support'] });
      setNotice({
        tone: 'success',
        text: action === 'close'
          ? 'Chamado encerrado.'
          : action === 'reopen'
            ? 'Chamado reaberto.'
            : 'Avaliação registrada.',
      });
    },
    onError: (error) => setNotice({ tone: 'error', text: errorMessage(error) }),
  });

  const selectAttachments = (
    event: ChangeEvent<HTMLInputElement>,
    target: 'ticket' | 'message',
  ) => {
    const incoming = Array.from(event.target.files ?? []);
    event.target.value = '';
    const current = target === 'ticket' ? attachments : messageAttachments;
    const result = mergeFiles(current, incoming, target === 'ticket' && captureFile ? 1 : 0);
    if (result.error) {
      setNotice({ tone: 'error', text: result.error });
      return;
    }
    if (target === 'ticket') setAttachments(result.files);
    else setMessageAttachments(result.files);
  };

  const captureScreen = async () => {
    if (!displayCaptureAllowed()) {
      setNotice({
        tone: 'warning',
        text: 'A política de segurança deste ambiente bloqueia a captura automática. Anexe uma imagem salva no dispositivo.',
      });
      return;
    }
    if (!navigator.mediaDevices?.getDisplayMedia) {
      setNotice({ tone: 'error', text: 'A captura de tela não é suportada neste navegador.' });
      return;
    }
    if (attachments.length >= MAX_ATTACHMENTS) {
      setNotice({ tone: 'error', text: `Selecione no máximo ${MAX_ATTACHMENTS} anexos.` });
      return;
    }
    setCapturing(true);
    let stream: MediaStream | null = null;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: false,
      });
      onOpenChange(false);
      await new Promise((resolve) => setTimeout(resolve, 250));
      const video = document.createElement('video');
      video.muted = true;
      video.srcObject = stream;
      await new Promise<void>((resolve, reject) => {
        video.onloadedmetadata = () => resolve();
        video.onerror = () => reject(new Error('Não foi possível preparar a captura.'));
      });
      await video.play();
      const canvas = document.createElement('canvas');
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Não foi possível processar a captura.');
      context.drawImage(video, 0, 0);
      const blob = await new Promise<Blob>((resolve, reject) => {
        canvas.toBlob(
          (value) => value ? resolve(value) : reject(new Error('Não foi possível gerar a captura.')),
          'image/png',
          0.92,
        );
      });
      if (blob.size > MAX_ATTACHMENT_BYTES) {
        throw new Error('A captura excedeu o limite de 10 MB.');
      }
      const file = new File([blob], `captura-${Date.now()}.png`, {
        type: 'image/png',
        lastModified: Date.now(),
      });
      if (capturePreview) URL.revokeObjectURL(capturePreview);
      setCaptureFile(file);
      setCapturePreview(URL.createObjectURL(file));
      setRedactionEnabled(false);
      setNotice(null);
    } catch (error) {
      setNotice({
        tone: error instanceof DOMException && error.name === 'NotAllowedError' ? 'warning' : 'error',
        text: error instanceof DOMException && error.name === 'NotAllowedError'
          ? 'Captura cancelada ou indisponível neste navegador.'
          : errorMessage(error),
      });
    } finally {
      stream?.getTracks().forEach((track) => track.stop());
      setCapturing(false);
      onOpenChange(true);
    }
  };

  const removeCapture = () => {
    if (capturePreview) URL.revokeObjectURL(capturePreview);
    setCapturePreview(null);
    setCaptureFile(null);
    setRedactionEnabled(false);
  };

  const redactCapture = async (event: ReactMouseEvent<HTMLDivElement>) => {
    if (!redactionEnabled || redacting || !captureFile || !capturePreview) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    const relativeX = Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width));
    const relativeY = Math.min(1, Math.max(0, (event.clientY - bounds.top) / bounds.height));
    setRedacting(true);
    try {
      const image = new Image();
      image.src = capturePreview;
      await new Promise<void>((resolve, reject) => {
        image.onload = () => resolve();
        image.onerror = () => reject(new Error('Não foi possível editar a captura.'));
      });
      const canvas = document.createElement('canvas');
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Não foi possível editar a captura.');
      context.drawImage(image, 0, 0);
      const redactionWidth = Math.min(canvas.width, Math.max(120, canvas.width * 0.22));
      const redactionHeight = Math.min(canvas.height, Math.max(60, canvas.height * 0.1));
      const left = Math.min(
        canvas.width - redactionWidth,
        Math.max(0, (relativeX * canvas.width) - (redactionWidth / 2)),
      );
      const top = Math.min(
        canvas.height - redactionHeight,
        Math.max(0, (relativeY * canvas.height) - (redactionHeight / 2)),
      );
      context.fillStyle = '#07182b';
      context.fillRect(left, top, redactionWidth, redactionHeight);
      const blob = await new Promise<Blob>((resolve, reject) => {
        canvas.toBlob(
          (value) => value ? resolve(value) : reject(new Error('Não foi possível salvar a ocultação.')),
          'image/png',
          0.92,
        );
      });
      if (blob.size > MAX_ATTACHMENT_BYTES) {
        throw new Error('A captura editada excedeu o limite de 10 MB.');
      }
      const file = new File([blob], captureFile.name, {
        type: 'image/png',
        lastModified: Date.now(),
      });
      URL.revokeObjectURL(capturePreview);
      setCaptureFile(file);
      setCapturePreview(URL.createObjectURL(file));
      setNotice({
        tone: 'success',
        text: 'Trecho ocultado no arquivo. Clique em outros pontos sensíveis ou conclua a ocultação.',
      });
    } catch (error) {
      setNotice({ tone: 'error', text: errorMessage(error) });
    } finally {
      setRedacting(false);
    }
  };

  const canClose = selectedTicket && ['resolved', 'validating'].includes(selectedTicket.status);
  const canReopen = selectedTicket && ['resolved', 'closed', 'cancelled'].includes(selectedTicket.status);
  const canRate = selectedTicket && ['resolved', 'closed'].includes(selectedTicket.status);
  const canMessage = selectedTicket && !['closed', 'cancelled'].includes(selectedTicket.status);
  const ticketMarkedUnresolved = Boolean(
    selectedTicket && !['resolved', 'closed', 'cancelled'].includes(selectedTicket.status),
  );
  const canFlagNotResolved = Boolean(
    selectedTicket && ['resolved', 'closed'].includes(selectedTicket.status) && !notResolvedClicked,
  );
  const escalation = escalationStatusQuery.data;
  const showEscalation = Boolean(
    selectedTicket
    && (ticketMarkedUnresolved || notResolvedClicked)
    && (escalation?.schedulingUrl || escalation?.whatsappLink || escalation?.nextWindow),
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex h-dvh w-screen max-w-none flex-col gap-0 overflow-hidden p-0 sm:w-[min(96vw,860px)] sm:max-w-[860px]"
        data-testid="support-center"
      >
        <SheetHeader className="shrink-0 border-b border-border px-4 py-4 pr-12 text-left sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <MessageSquare className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <SheetTitle className="text-base sm:text-lg">{supportLabel}</SheetTitle>
              <SheetDescription className="mt-0.5 text-[10px] sm:text-xs">
                {welcomeMessage || 'Atendimento, histórico e retorno sem sair da tela atual.'}
              </SheetDescription>
            </div>
          </div>
        </SheetHeader>

        {queueCount > 0 && (
          <div className="flex shrink-0 items-center justify-between gap-3 border-b border-amber-300/40 bg-amber-50 px-4 py-2.5 text-amber-950 dark:bg-amber-950/30 dark:text-amber-100 sm:px-6" role="status">
            <span className="flex min-w-0 items-center gap-2 text-[10px]">
              <WifiOff className="h-3.5 w-3.5 shrink-0" />
              <span>{queueCount} envio{queueCount === 1 ? '' : 's'} aguardando confirmação do SAC</span>
            </span>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-7 w-7 shrink-0"
              onClick={() => void syncOfflineQueue()}
              title="Tentar sincronizar agora"
              aria-label="Tentar sincronizar envios pendentes"
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </Button>
          </div>
        )}

        {notice && (
          <div
            className={cn(
              'flex shrink-0 items-start gap-2 border-b px-4 py-2.5 text-[10px] leading-4 sm:px-6',
              notice.tone === 'success' && 'border-emerald-300/40 bg-emerald-50 text-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100',
              notice.tone === 'warning' && 'border-amber-300/40 bg-amber-50 text-amber-950 dark:bg-amber-950/30 dark:text-amber-100',
              notice.tone === 'error' && 'border-red-300/40 bg-red-50 text-red-900 dark:bg-red-950/30 dark:text-red-100',
            )}
            role={notice.tone === 'error' ? 'alert' : 'status'}
          >
            {notice.tone === 'success' ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
            <span>{notice.text}</span>
          </div>
        )}

        <Tabs value={activeTab} onValueChange={(value) => { setActiveTab(value); setSelectedTicketId(null); }} className="flex min-h-0 flex-1 flex-col">
          <div className="shrink-0 border-b border-border px-4 py-2.5 sm:px-6">
            <TabsList className="grid h-9 w-full grid-cols-3 sm:w-[520px]">
              <TabsTrigger value="chat" className="gap-2 text-[10px]">
                <Bot className="h-3.5 w-3.5" />Conversa agora
              </TabsTrigger>
              <TabsTrigger value="tickets" className="gap-2 text-[10px]">
                <Inbox className="h-3.5 w-3.5" />Meus chamados
              </TabsTrigger>
              <TabsTrigger value="new" className="gap-2 text-[10px]">
                <Plus className="h-3.5 w-3.5" />Novo chamado
              </TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="chat" className="m-0 flex min-h-0 flex-1 flex-col">
            <div className="border-b border-border bg-primary/5 px-4 py-3 sm:px-6">
              <div className="flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground"><Bot className="h-4 w-4" /><span className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-background bg-emerald-500" /></span>
                  <span><span className="block text-xs font-semibold">IA de atendimento</span><span className="block text-[9px] text-muted-foreground">Primeiro atendimento em tempo real</span></span>
                </div>
                <Button type="button" variant="outline" size="sm" className="h-8 shrink-0 text-[10px]" onClick={requestHumanSupport}>
                  <PhoneCall className="mr-1.5 h-3.5 w-3.5" />Pedir humano
                </Button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
              <div className="mx-auto max-w-[720px] space-y-3" aria-live="polite">
                {assistantMessages.map((message) => <div key={message.id} className={cn('flex', message.role === 'user' ? 'justify-end' : 'justify-start')}><div className={cn('max-w-[85%] rounded-lg px-3 py-2.5 text-xs leading-5', message.role === 'user' ? 'bg-primary text-primary-foreground' : 'border bg-card')}>{message.body}</div></div>)}
              </div>
            </div>
            <form className="shrink-0 border-t border-border p-4 sm:px-6" onSubmit={(event) => { event.preventDefault(); sendAssistantMessage(); }}>
              <div className="mx-auto flex max-w-[720px] items-end gap-2">
                <Textarea value={assistantInput} onChange={(event) => setAssistantInput(event.target.value)} rows={2} className="min-h-[48px] resize-none text-xs" placeholder="Descreva sua dúvida ou problema" aria-label="Mensagem para a IA de atendimento" />
                <Button type="submit" size="icon" className="h-10 w-10 shrink-0" disabled={!assistantInput.trim()} aria-label="Enviar mensagem para a IA"><Send className="h-4 w-4" /></Button>
              </div>
              <p className="mx-auto mt-2 max-w-[720px] text-[9px] text-muted-foreground">A IA faz a orientação inicial. Atendimento humano somente quando você solicitar ou quando o caso exigir.</p>
            </form>
          </TabsContent>

          <TabsContent value="new" className="m-0 min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
            <form
              className="mx-auto max-w-[720px] space-y-5"
              onSubmit={(event) => {
                event.preventDefault();
                ticketMutation.mutate();
              }}
            >
              <fieldset className="space-y-2">
                <legend className="text-[10px] font-semibold">Tipo de solicitação</legend>
                <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="Tipo de solicitação">
                  {typeOptions.map((option) => {
                    const Icon = option.icon;
                    const selected = draft.ticketType === option.value;
                    return (
                      <button
                        key={option.value}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        className={cn(
                          'flex min-h-16 flex-col items-center justify-center gap-1.5 rounded-lg border px-2 py-2 text-[10px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                          selected
                            ? 'border-primary bg-primary/10 text-primary'
                            : 'border-border bg-card text-muted-foreground hover:bg-secondary',
                        )}
                        onClick={() => setDraft((current) => ({ ...current, ticketType: option.value }))}
                      >
                        <Icon className="h-4 w-4" />
                        <span className="break-words text-center">{option.label}</span>
                      </button>
                    );
                  })}
                </div>
              </fieldset>

              <div className="space-y-1.5">
                <Label htmlFor="support-subject" className="text-[10px]">Assunto</Label>
                <Input
                  id="support-subject"
                  value={draft.subject}
                  maxLength={240}
                  onChange={(event) => setDraft((current) => ({ ...current, subject: event.target.value }))}
                  placeholder="Resumo objetivo do pedido"
                  required
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="support-description" className="text-[10px]">Descrição</Label>
                <Textarea
                  id="support-description"
                  value={draft.description}
                  maxLength={20_000}
                  rows={7}
                  className="min-h-[150px] resize-y text-xs"
                  onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))}
                  placeholder="O que aconteceu, qual era o resultado esperado e como isso afeta seu trabalho?"
                  required
                />
                <p className="text-right text-[9px] text-muted-foreground">{draft.description.length}/20.000</p>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="support-priority" className="text-[10px]">Prioridade percebida</Label>
                  <Select
                    value={draft.priority}
                    onValueChange={(value: KlmSupportPriority) => setDraft((current) => ({ ...current, priority: value }))}
                  >
                    <SelectTrigger id="support-priority"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {Object.entries(priorityLabels).map(([value, label]) => (
                        <SelectItem key={value} value={value}>{label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="support-severity" className="text-[10px]">Severidade</Label>
                  <Select
                    value={draft.severity}
                    onValueChange={(value: KlmSupportSeverity) => setDraft((current) => ({ ...current, severity: value }))}
                  >
                    <SelectTrigger id="support-severity"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {Object.entries(severityLabels).map(([value, label]) => (
                        <SelectItem key={value} value={value}>{label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <section className="space-y-3 border-t border-border pt-5" aria-labelledby="support-attachments-heading">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h3 id="support-attachments-heading" className="text-xs font-semibold">Anexos</h3>
                    <p className="mt-0.5 text-[9px] text-muted-foreground">Até 5 arquivos de 10 MB cada.</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      ref={attachmentInputRef}
                      type="file"
                      multiple
                      className="sr-only"
                      accept="image/*,.pdf,.txt,.log,.json,.csv,.doc,.docx"
                      onChange={(event) => selectAttachments(event, 'ticket')}
                      aria-label="Selecionar anexos do chamado"
                    />
                    <Button type="button" variant="outline" size="sm" className="h-8 text-[10px]" onClick={() => attachmentInputRef.current?.click()}>
                      <Paperclip className="mr-1.5 h-3.5 w-3.5" />Arquivo
                    </Button>
                    <Button type="button" variant="outline" size="sm" className="h-8 text-[10px]" onClick={() => void captureScreen()} disabled={capturing}>
                      {capturing ? <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Camera className="mr-1.5 h-3.5 w-3.5" />}
                      Captura
                    </Button>
                  </div>
                </div>

                {attachments.length > 0 && (
                  <div className="divide-y divide-border rounded-lg border border-border">
                    {attachments.map((file, index) => (
                      <div key={`${file.name}-${file.lastModified}`} className="flex items-center gap-3 px-3 py-2">
                        <FileText className="h-4 w-4 shrink-0 text-primary" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[10px] font-medium">{file.name}</span>
                          <span className="block text-[9px] text-muted-foreground">{formatBytes(file.size)}</span>
                        </span>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                          aria-label={`Remover ${file.name}`}
                          title="Remover anexo"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}

                {capturePreview && captureFile && (
                  <div className="overflow-hidden rounded-lg border border-border">
                    <div className="relative bg-secondary p-2">
                      <div
                        className={cn(
                          'relative mx-auto w-fit max-w-full',
                          redactionEnabled && 'cursor-crosshair ring-2 ring-primary ring-offset-2 ring-offset-secondary',
                        )}
                        onClick={(event) => void redactCapture(event)}
                        aria-label={redactionEnabled ? 'Clique na imagem para ocultar um trecho sensível' : undefined}
                      >
                        <img
                          src={capturePreview}
                          alt="Prévia da captura de tela selecionada"
                          className="block max-h-[420px] max-w-full object-contain"
                        />
                        {redacting && (
                          <span className="absolute inset-0 flex items-center justify-center bg-background/55">
                            <LoaderCircle className="h-5 w-5 animate-spin text-primary" aria-label="Ocultando trecho" />
                          </span>
                        )}
                      </div>
                      <Button
                        type="button"
                        variant="secondary"
                        size="icon"
                        className="absolute right-2 top-2 h-8 w-8"
                        onClick={removeCapture}
                        aria-label="Remover captura de tela"
                        title="Remover captura"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-3 py-2">
                      <p className="text-[9px] text-muted-foreground">
                        {redactionEnabled
                          ? 'Clique sobre cada trecho sensível. A ocultação será gravada no arquivo.'
                          : 'Revise a imagem e oculte informações sensíveis antes do envio.'}
                      </p>
                      <Button
                        type="button"
                        variant={redactionEnabled ? 'default' : 'outline'}
                        size="sm"
                        className="h-7 text-[9px]"
                        onClick={() => setRedactionEnabled((current) => !current)}
                        disabled={redacting}
                      >
                        <EyeOff className="mr-1.5 h-3.5 w-3.5" />
                        {redactionEnabled ? 'Concluir ocultação' : 'Ocultar trecho'}
                      </Button>
                    </div>
                  </div>
                )}
              </section>

              <details className="border-t border-border pt-4 text-[10px]">
                <summary className="cursor-pointer font-semibold text-foreground">Contexto técnico seguro</summary>
                <p className="mt-2 leading-4 text-muted-foreground">
                  Rota sem parâmetros, versão, navegador, tipo de dispositivo, resolução, horário,
                  conexão e identificadores técnicos válidos. Sem tokens, corpos de requisição,
                  conversas ou dados pessoais.
                </p>
              </details>

              <div className="flex justify-end border-t border-border pt-4">
                <Button
                  type="submit"
                  className="min-w-[150px]"
                  disabled={ticketMutation.isPending || draft.subject.trim().length < 3 || draft.description.trim().length < 10}
                >
                  {ticketMutation.isPending ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Send className="mr-2 h-4 w-4" />}
                  Enviar ao SAC
                </Button>
              </div>
            </form>
          </TabsContent>

          <TabsContent value="tickets" className="m-0 min-h-0 flex-1 overflow-hidden">
            {selectedTicketId ? (
              <div className="flex h-full min-h-0 flex-col">
                <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-4 py-3 sm:px-6">
                  <Button type="button" variant="ghost" size="sm" className="h-8 px-2 text-[10px]" onClick={() => setSelectedTicketId(null)}>
                    <ArrowLeft className="mr-1.5 h-3.5 w-3.5" />Voltar
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    onClick={() => void detailQuery.refetch()}
                    aria-label="Atualizar chamado"
                    title="Atualizar chamado"
                    disabled={detailQuery.isFetching}
                  >
                    <RefreshCw className={cn('h-3.5 w-3.5', detailQuery.isFetching && 'animate-spin')} />
                  </Button>
                </div>

                <div className="min-h-0 flex-1 overflow-y-auto">
                  {detailQuery.isPending && (
                    <div className="flex h-48 items-center justify-center text-muted-foreground">
                      <LoaderCircle className="h-5 w-5 animate-spin" aria-label="Carregando chamado" />
                    </div>
                  )}
                  {detailQuery.isError && (
                    <div className="p-6 text-center">
                      <AlertCircle className="mx-auto h-5 w-5 text-destructive" />
                      <p className="mt-2 text-xs font-semibold">Não foi possível carregar o chamado.</p>
                      <p className="mt-1 text-[10px] text-muted-foreground">{errorMessage(detailQuery.error)}</p>
                      <Button type="button" variant="outline" size="sm" className="mt-4" onClick={() => void detailQuery.refetch()}>Tentar novamente</Button>
                    </div>
                  )}
                  {selectedTicket && (
                    <div className="divide-y divide-border">
                      <section className="space-y-4 px-4 py-5 sm:px-6">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-[10px] font-semibold text-primary">{selectedTicket.publicNumber}</span>
                          <KlmPill tone={statusTone(selectedTicket.status)}>{statusLabels[selectedTicket.status]}</KlmPill>
                          <KlmPill tone="muted">{severityLabels[selectedTicket.severity]}</KlmPill>
                        </div>
                        <div>
                          <h2 className="break-words text-base font-semibold">{selectedTicket.subject}</h2>
                          <p className="mt-2 whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground">{selectedTicket.description}</p>
                        </div>
                        <dl className="grid gap-3 text-[10px] sm:grid-cols-3">
                          <div><dt className="text-muted-foreground">Criado</dt><dd className="mt-0.5 font-medium">{formatDate(selectedTicket.createdAt)}</dd></div>
                          <div><dt className="text-muted-foreground">Prioridade</dt><dd className="mt-0.5 font-medium">{priorityLabels[selectedTicket.priority]}</dd></div>
                          <div><dt className="text-muted-foreground">Responsável</dt><dd className="mt-0.5 font-medium">{selectedTicket.assignee?.name ?? 'Equipe de SAC'}</dd></div>
                        </dl>
                        <div className="flex flex-wrap gap-2">
                          {canClose && (
                            <Button type="button" variant="outline" size="sm" className="h-8 text-[10px]" onClick={() => ticketActionMutation.mutate('close')} disabled={ticketActionMutation.isPending}>
                              <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />Encerrar
                            </Button>
                          )}
                          {canReopen && (
                            <Button type="button" variant="outline" size="sm" className="h-8 text-[10px]" onClick={() => ticketActionMutation.mutate('reopen')} disabled={ticketActionMutation.isPending}>
                              <RotateCcw className="mr-1.5 h-3.5 w-3.5" />Reabrir
                            </Button>
                          )}
                          {canFlagNotResolved && (
                            <Button type="button" variant="ghost" size="sm" className="h-8 text-[10px]" onClick={() => setNotResolvedClicked(true)}>
                              <PhoneCall className="mr-1.5 h-3.5 w-3.5" />Isso não resolveu
                            </Button>
                          )}
                        </div>
                      </section>

                      {showEscalation && (
                        <section className="px-4 py-5 sm:px-6" aria-labelledby="support-escalation-heading">
                          <h3 id="support-escalation-heading" className="text-xs font-semibold">Falar com um humano</h3>
                          <p className="mt-2 text-[10px] text-muted-foreground">
                            {escalation?.availableNow
                              ? 'Nosso time está disponível agora.'
                              : escalation?.nextWindow
                                ? `Fora do horário de atendimento. Próxima janela: ${formatDate(escalation.nextWindow)}.`
                                : 'Fora do horário de atendimento.'}
                          </p>
                          <div className="mt-3 flex flex-wrap gap-2">
                            {escalation?.schedulingUrl && (
                              <Button asChild type="button" variant="outline" size="sm" className="h-8 text-[10px]">
                                <a href={escalation.schedulingUrl} target="_blank" rel="noreferrer">
                                  <Calendar className="mr-1.5 h-3.5 w-3.5" />Agendar horário
                                </a>
                              </Button>
                            )}
                            {escalation?.whatsappLink && (
                              <Button asChild type="button" variant="outline" size="sm" className="h-8 text-[10px]">
                                <a href={escalation.whatsappLink} target="_blank" rel="noreferrer">
                                  <MessageSquare className="mr-1.5 h-3.5 w-3.5" />WhatsApp
                                </a>
                              </Button>
                            )}
                          </div>
                        </section>
                      )}

                      <section className="px-4 py-5 sm:px-6" aria-labelledby="support-messages-heading">
                        <div className="flex items-center justify-between gap-3">
                          <h3 id="support-messages-heading" className="text-xs font-semibold">Mensagens públicas</h3>
                          <KlmPill tone="muted">{selectedTicket.messages.length}</KlmPill>
                        </div>
                        <div className="mt-4 space-y-3">
                          {selectedTicket.messages.length ? selectedTicket.messages.map((message) => (
                            <article key={message.id} className="rounded-lg border border-border bg-secondary/50 px-3 py-3">
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <strong className="text-[10px]">{message.authorLabel}</strong>
                                <time className="text-[9px] text-muted-foreground" dateTime={message.createdAt}>{formatDate(message.createdAt)}</time>
                              </div>
                              <p className="mt-2 whitespace-pre-wrap break-words text-[11px] leading-5">{message.body}</p>
                            </article>
                          )) : (
                            <p className="rounded-lg border border-dashed border-border px-3 py-5 text-center text-[10px] text-muted-foreground">Nenhuma mensagem pública ainda.</p>
                          )}
                        </div>

                        {canMessage ? (
                          <form
                            className="mt-5 space-y-3 border-t border-border pt-4"
                            onSubmit={(event) => {
                              event.preventDefault();
                              messageMutation.mutate();
                            }}
                          >
                            <Label htmlFor="support-public-message" className="text-[10px]">Nova mensagem pública</Label>
                            <Textarea
                              id="support-public-message"
                              value={messageBody}
                              onChange={(event) => setMessageBody(event.target.value)}
                              rows={4}
                              maxLength={20_000}
                              className="resize-y text-xs"
                              placeholder="Escreva sua resposta"
                            />
                            <div className="flex flex-wrap items-center justify-between gap-3">
                              <div className="flex min-w-0 flex-wrap items-center gap-2">
                                <input
                                  ref={messageAttachmentInputRef}
                                  type="file"
                                  multiple
                                  className="sr-only"
                                  accept="image/*,.pdf,.txt,.log,.json,.csv,.doc,.docx"
                                  onChange={(event) => selectAttachments(event, 'message')}
                                  aria-label="Selecionar anexos da mensagem"
                                />
                                <Button type="button" variant="outline" size="sm" className="h-8 text-[10px]" onClick={() => messageAttachmentInputRef.current?.click()}>
                                  <Paperclip className="mr-1.5 h-3.5 w-3.5" />Anexar
                                </Button>
                                {messageAttachments.map((file, index) => (
                                  <button
                                    key={`${file.name}-${file.lastModified}`}
                                    type="button"
                                    className="max-w-[190px] truncate rounded-md border border-border bg-secondary px-2 py-1 text-[9px]"
                                    onClick={() => setMessageAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                                    title={`Remover ${file.name}`}
                                  >
                                    {file.name}
                                  </button>
                                ))}
                              </div>
                              <Button type="submit" size="sm" disabled={!messageBody.trim() || messageMutation.isPending}>
                                {messageMutation.isPending ? <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Send className="mr-1.5 h-3.5 w-3.5" />}
                                Enviar
                              </Button>
                            </div>
                          </form>
                        ) : (
                          <p className="mt-4 flex items-center gap-2 border-t border-border pt-4 text-[10px] text-muted-foreground">
                            <Clock3 className="h-3.5 w-3.5" />Reabra o chamado para enviar uma nova mensagem.
                          </p>
                        )}
                      </section>

                      <section className="px-4 py-5 sm:px-6" aria-labelledby="support-files-heading">
                        <div className="flex items-center justify-between gap-3">
                          <h3 id="support-files-heading" className="text-xs font-semibold">Anexos</h3>
                          <KlmPill tone="muted">{selectedTicket.attachments.length}</KlmPill>
                        </div>
                        <div className="mt-3 divide-y divide-border rounded-lg border border-border">
                          {selectedTicket.attachments.length ? selectedTicket.attachments.map((attachment) => (
                            <div key={attachment.id} className="flex items-center gap-3 px-3 py-2.5">
                              <FileText className="h-4 w-4 shrink-0 text-primary" />
                              <span className="min-w-0 flex-1">
                                <span className="block truncate text-[10px] font-medium">{attachment.originalFilename}</span>
                                <span className="mt-0.5 block text-[9px] text-muted-foreground">
                                  {formatBytes(attachment.byteSize)} · {attachment.state === 'available' ? 'Disponível' : attachment.state === 'rejected' ? 'Rejeitado' : 'Em verificação'}
                                </span>
                              </span>
                              <KlmPill tone={attachment.avStatus === 'clean' ? 'green' : attachment.avStatus === 'infected' ? 'red' : 'muted'}>
                                {attachment.avStatus === 'clean' ? 'Verificado' : attachment.avStatus === 'infected' ? 'Bloqueado' : 'Analisando'}
                              </KlmPill>
                            </div>
                          )) : (
                            <p className="px-3 py-5 text-center text-[10px] text-muted-foreground">Nenhum anexo neste chamado.</p>
                          )}
                        </div>
                      </section>

                      {canRate && (
                        <section className="px-4 py-5 sm:px-6" aria-labelledby="support-rating-heading">
                          <h3 id="support-rating-heading" className="text-xs font-semibold">Avaliar atendimento</h3>
                          <div className="mt-3 flex gap-1" role="radiogroup" aria-label="Nota do atendimento">
                            {[1, 2, 3, 4, 5].map((score) => (
                              <button
                                key={score}
                                type="button"
                                role="radio"
                                aria-checked={ratingScore === score}
                                className="flex h-9 w-9 items-center justify-center rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                onClick={() => setRatingScore(score)}
                                aria-label={`${score} de 5`}
                              >
                                <Star className={cn('h-5 w-5', score <= ratingScore ? 'fill-primary text-primary' : 'text-muted-foreground')} />
                              </button>
                            ))}
                          </div>
                          <Textarea
                            value={ratingComment}
                            onChange={(event) => setRatingComment(event.target.value)}
                            rows={3}
                            maxLength={2_000}
                            className="mt-3 resize-y text-xs"
                            aria-label="Comentário da avaliação"
                            placeholder="Comentário opcional"
                          />
                          <Button
                            type="button"
                            size="sm"
                            className="mt-3"
                            onClick={() => ticketActionMutation.mutate('rate')}
                            disabled={!ratingScore || ticketActionMutation.isPending}
                          >
                            Registrar avaliação
                          </Button>
                        </section>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="flex h-full min-h-0 flex-col">
                <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-4 py-3 sm:px-6">
                  <Select value={statusFilter} onValueChange={(value: 'all' | 'open' | 'closed') => setStatusFilter(value)}>
                    <SelectTrigger className="h-8 w-[150px] text-[10px]" aria-label="Filtrar chamados">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Todos</SelectItem>
                      <SelectItem value="open">Em aberto</SelectItem>
                      <SelectItem value="closed">Concluídos</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    onClick={() => void ticketsQuery.refetch()}
                    aria-label="Atualizar meus chamados"
                    title="Atualizar chamados"
                    disabled={ticketsQuery.isFetching}
                  >
                    <RefreshCw className={cn('h-3.5 w-3.5', ticketsQuery.isFetching && 'animate-spin')} />
                  </Button>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto">
                  {ticketsQuery.isPending && (
                    <div className="flex h-48 items-center justify-center text-muted-foreground">
                      <LoaderCircle className="h-5 w-5 animate-spin" aria-label="Carregando chamados" />
                    </div>
                  )}
                  {ticketsQuery.isError && (
                    <div className="p-6 text-center">
                      <AlertCircle className="mx-auto h-5 w-5 text-destructive" />
                      <p className="mt-2 text-xs font-semibold">Não foi possível carregar seus chamados.</p>
                      <p className="mt-1 text-[10px] text-muted-foreground">{errorMessage(ticketsQuery.error)}</p>
                      <Button type="button" variant="outline" size="sm" className="mt-4" onClick={() => void ticketsQuery.refetch()}>Tentar novamente</Button>
                    </div>
                  )}
                  {ticketsQuery.isSuccess && filteredTickets.length > 0 && (
                    <div>
                      {filteredTickets.map((ticket) => (
                        <TicketListItem key={ticket.id} ticket={ticket} onSelect={setSelectedTicketId} />
                      ))}
                    </div>
                  )}
                  {ticketsQuery.isSuccess && filteredTickets.length === 0 && (
                    <div className="flex min-h-[280px] flex-col items-center justify-center px-6 text-center">
                      <Inbox className="h-7 w-7 text-muted-foreground" />
                      <h2 className="mt-3 text-sm font-semibold">Nenhum chamado neste filtro</h2>
                      <p className="mt-1 text-[10px] text-muted-foreground">Abra um novo chamado para falar com o SAC.</p>
                      <Button type="button" size="sm" className="mt-4" onClick={() => setActiveTab('new')}>
                        <Plus className="mr-1.5 h-3.5 w-3.5" />Novo chamado
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            )}
          </TabsContent>
        </Tabs>
      </SheetContent>
    </Sheet>
  );
}
