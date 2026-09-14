import { useCallback, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Ban,
  Building2,
  CalendarClock,
  Eye,
  History,
  Loader2,
  LockKeyhole,
  Mail,
  Megaphone,
  Monitor,
  Plus,
  ReceiptText,
  RefreshCw,
  Save,
  Send,
  Users,
} from 'lucide-react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { FieldHelp, FieldHelpLabel } from '@/components/ui/field-help';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { PlatformEntityMultiSelect } from '@/components/platform/PlatformEntityMultiSelect';
import {
  cancelPlatformNotice,
  createPlatformNotice,
  dispatchDuePlatformNotices,
  listPlatformNoticeAudit,
  listPlatformNoticeReceipts,
  listPlatformNotices,
  previewPlatformNoticeRecipients,
  publishPlatformNotice,
  schedulePlatformNotice,
  updatePlatformNotice,
  type PlatformNotice,
  type PlatformNoticeAudience,
  type PlatformNoticeAudienceKind,
  type PlatformNoticeAuditAction,
  type PlatformNoticeChannel,
  type PlatformNoticeCreateInput,
  type PlatformNoticeReceiptState,
  type PlatformNoticeRecipientPreview,
  type PlatformNoticeRuleField,
  type PlatformNoticeRuleValue,
  type PlatformNoticeStatus,
  type PlatformNoticeUpdateInput,
  type PlatformNoticeUserRole,
  type PlatformNoticeWorkspaceRole,
} from '@/services/platform-notices';

const STATUS_LABELS: Record<PlatformNoticeStatus, string> = {
  draft: 'Rascunho',
  scheduled: 'Agendado',
  published: 'Publicado',
  cancelled: 'Cancelado',
};

const AUDIENCE_LABELS: Record<PlatformNoticeAudienceKind, string> = {
  global: 'Público geral',
  workspaces: 'Contas específicas',
  users: 'Usuários específicos',
  rule: 'Regra por papel',
};

const RECEIPT_STATE_LABELS: Record<PlatformNoticeReceiptState, string> = {
  in_app_available: 'Disponível in-app',
  email_queued_local: 'Na fila local de e-mail',
  email_blocked_missing_address: 'Sem endereço de e-mail',
  cancelled: 'Cancelado',
};

const AUDIT_ACTION_LABELS: Record<PlatformNoticeAuditAction, string> = {
  created: 'Rascunho criado',
  updated: 'Conteúdo atualizado',
  recipient_previewed: 'Público pré-visualizado',
  scheduled: 'Publicação agendada',
  published: 'Comunicado publicado',
  cancelled: 'Comunicado cancelado',
  read: 'Comunicado lido',
  acknowledged: 'Leitura confirmada',
};

const USER_ROLE_OPTIONS: ReadonlyArray<{ value: PlatformNoticeUserRole; label: string }> = [
  { value: 'senior_lawyer', label: 'Advogado(a) sênior' },
  { value: 'lawyer', label: 'Advogado(a)' },
  { value: 'intern', label: 'Estagiário(a)' },
  { value: 'admin', label: 'Administrador(a)' },
];

const WORKSPACE_ROLE_OPTIONS: ReadonlyArray<{ value: PlatformNoticeWorkspaceRole; label: string }> = [
  { value: 'workspace_owner', label: 'Titular da conta' },
  { value: 'workspace_admin', label: 'Administrador(a) da conta' },
  { value: 'legal_manager', label: 'Gestor(a) jurídico(a)' },
  { value: 'finance_manager', label: 'Gestor(a) financeiro(a)' },
  { value: 'operator', label: 'Operador(a)' },
  { value: 'viewer', label: 'Somente leitura' },
];

const EMPTY_NOTICES: PlatformNotice[] = [];
interface NoticeFormState {
  title: string;
  body: string;
  audienceKind: PlatformNoticeAudienceKind;
  workspaceIds: string[];
  userIds: string[];
  ruleField: PlatformNoticeRuleField;
  ruleValues: PlatformNoticeRuleValue[];
  channels: PlatformNoticeChannel[];
  requiresAcknowledgement: boolean;
}

type SaveVariables =
  | { mode: 'create'; payload: PlatformNoticeCreateInput }
  | { mode: 'update'; noticeId: string; payload: PlatformNoticeUpdateInput };

export interface PlatformAnnouncementsPanelProps {
  canManage?: boolean;
}

function emptyForm(): NoticeFormState {
  return {
    title: '',
    body: '',
    audienceKind: 'global',
    workspaceIds: [],
    userIds: [],
    ruleField: 'user_role',
    ruleValues: [],
    channels: ['in_app'],
    requiresAcknowledgement: false,
  };
}

function formFromNotice(notice: PlatformNotice): NoticeFormState {
  return {
    title: notice.title,
    body: notice.body,
    audienceKind: notice.audience.kind,
    workspaceIds: [...notice.audience.workspaceIds],
    userIds: [...notice.audience.userIds],
    ruleField: notice.audience.rule?.field ?? 'user_role',
    ruleValues: notice.audience.rule?.values ?? [],
    channels: [...notice.channels],
    requiresAcknowledgement: notice.requiresAcknowledgement,
  };
}

function buildAudience(form: NoticeFormState): PlatformNoticeAudience {
  if (form.audienceKind === 'workspaces') {
    return { kind: 'workspaces', workspaceIds: form.workspaceIds, userIds: [], rule: null };
  }
  if (form.audienceKind === 'users') {
    return { kind: 'users', workspaceIds: [], userIds: form.userIds, rule: null };
  }
  if (form.audienceKind === 'rule') {
    return {
      kind: 'rule',
      workspaceIds: [],
      userIds: [],
      rule: form.ruleField === 'user_role'
        ? { field: 'user_role', operator: 'in', values: form.ruleValues as PlatformNoticeUserRole[] }
        : { field: 'workspace_role', operator: 'in', values: form.ruleValues as PlatformNoticeWorkspaceRole[] },
    };
  }
  return { kind: 'global', workspaceIds: [], userIds: [], rule: null };
}

function sameStringSet(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value) => right.includes(value));
}

function audiencesEqual(left: PlatformNoticeAudience, right: PlatformNoticeAudience): boolean {
  if (left.kind !== right.kind) return false;
  if (!sameStringSet(left.workspaceIds, right.workspaceIds)) return false;
  if (!sameStringSet(left.userIds, right.userIds)) return false;
  if (left.rule === null || right.rule === null) return left.rule === right.rule;
  return left.rule.field === right.rule.field
    && left.rule.operator === right.rule.operator
    && sameStringSet(left.rule.values, right.rule.values);
}

function validateForm(form: NoticeFormState): string | null {
  if (!form.title.trim()) return 'Informe o título do comunicado.';
  if (!form.body.trim()) return 'Escreva a mensagem do comunicado.';
  if (form.channels.length === 0) return 'Selecione ao menos um canal de entrega.';
  if (form.requiresAcknowledgement && !form.channels.includes('in_app')) {
    return 'A confirmação de leitura exige o canal in-app.';
  }

  const audience = buildAudience(form);
  if (audience.kind === 'workspaces') {
    if (audience.workspaceIds.length === 0) return 'Informe ao menos uma conta destinatária.';
  }
  if (audience.kind === 'users') {
    if (audience.userIds.length === 0) return 'Informe ao menos um usuário destinatário.';
  }
  if (audience.kind === 'rule' && audience.rule?.values.length === 0) {
    return 'Selecione ao menos um papel para a regra de público.';
  }
  return null;
}

function formatDate(value?: string | null): string {
  if (!value) return 'Não informado';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Data inválida';
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(date);
}

function toLocalDateTimeInput(value?: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function minimumLocalDateTime(): string {
  return toLocalDateTimeInput(new Date(Date.now() + 60_000).toISOString());
}

function statusVariant(status: PlatformNoticeStatus): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'published') return 'default';
  if (status === 'scheduled') return 'secondary';
  if (status === 'cancelled') return 'destructive';
  return 'outline';
}

function receiptVariant(state: PlatformNoticeReceiptState): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (state === 'in_app_available') return 'default';
  if (state === 'email_queued_local') return 'secondary';
  if (state === 'email_blocked_missing_address' || state === 'cancelled') return 'destructive';
  return 'outline';
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Não foi possível concluir a operação.';
}

function matchedByLabel(value: string): string {
  if (value === 'global') return 'Público geral';
  if (value === 'workspace') return 'Conta';
  if (value === 'user') return 'Usuário';
  if (value === 'user_role' || value === 'rule:user_role') return 'Papel do usuário';
  if (value === 'workspace_role' || value === 'rule:workspace_role') return 'Papel na conta';
  return value.replaceAll('_', ' ');
}

function editorTitle(isCreating: boolean, notice: PlatformNotice | null): string {
  if (isCreating) return 'Novo rascunho';
  if (!notice) return 'Comunicado';
  if (notice.status === 'draft') return 'Editar rascunho';
  if (notice.status === 'scheduled') return 'Editar comunicado agendado';
  return 'Consultar comunicado';
}

export function PlatformAnnouncementsPanel({ canManage = true }: PlatformAnnouncementsPanelProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [statusFilter, setStatusFilter] = useState<'all' | PlatformNoticeStatus>('all');
  const [noticePage, setNoticePage] = useState(0);
  const noticePageSize = 25;
  const [receiptPage, setReceiptPage] = useState(0);
  const receiptPageSize = 25;
  const [auditPage, setAuditPage] = useState(0);
  const auditPageSize = 25;
  const [selectedNoticeId, setSelectedNoticeId] = useState<string | null>(null);
  const [selectedSnapshot, setSelectedSnapshot] = useState<PlatformNotice | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [form, setForm] = useState<NoticeFormState>(() => emptyForm());
  const [isDirty, setIsDirty] = useState(false);
  const [scheduledAt, setScheduledAt] = useState('');
  const [preview, setPreview] = useState<PlatformNoticeRecipientPreview | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);

  const noticesQuery = useQuery({
    queryKey: ['platform-notices', 'list', statusFilter, noticePage],
    queryFn: () => listPlatformNotices({
      status: statusFilter === 'all' ? undefined : statusFilter,
      limit: noticePageSize,
      offset: noticePage * noticePageSize,
    }),
    enabled: canManage,
    retry: false,
    refetchOnWindowFocus: false,
  });

  const notices = noticesQuery.data?.items ?? EMPTY_NOTICES;
  useEffect(() => setNoticePage(0), [statusFilter]);
  const listedNotice = useMemo(
    () => notices.find((notice) => notice.id === selectedNoticeId) ?? null,
    [notices, selectedNoticeId],
  );
  const currentSnapshot = selectedSnapshot?.id === selectedNoticeId ? selectedSnapshot : null;
  const selectedNotice = listedNotice && (!currentSnapshot || listedNotice.version > currentSnapshot.version)
    ? listedNotice
    : currentSnapshot ?? listedNotice;
  const activeNoticeId = isCreating ? null : selectedNoticeId;

  useEffect(() => {
    setReceiptPage(0);
    setAuditPage(0);
  }, [activeNoticeId]);

  const receiptsQuery = useQuery({
    queryKey: ['platform-notices', activeNoticeId, 'receipts', receiptPage],
    queryFn: () => listPlatformNoticeReceipts(
      activeNoticeId!,
      receiptPage * receiptPageSize,
      receiptPageSize,
    ),
    enabled: canManage && Boolean(activeNoticeId),
    retry: false,
    refetchOnWindowFocus: false,
  });

  const auditQuery = useQuery({
    queryKey: ['platform-notices', activeNoticeId, 'audit', auditPage],
    queryFn: () => listPlatformNoticeAudit(
      activeNoticeId!,
      auditPage * auditPageSize,
      auditPageSize,
    ),
    enabled: canManage && Boolean(activeNoticeId),
    retry: false,
    refetchOnWindowFocus: false,
  });

  const resetFromNotice = useCallback((notice: PlatformNotice) => {
    setForm(formFromNotice(notice));
    setScheduledAt(toLocalDateTimeInput(notice.scheduledAt));
    setIsDirty(false);
    setPreview(null);
  }, []);

  useEffect(() => {
    if (!noticesQuery.isSuccess || isCreating) return;
    if (selectedNoticeId && (listedNotice || selectedSnapshot?.id === selectedNoticeId)) return;
    if (notices.length === 0 && statusFilter === 'all') {
      setSelectedNoticeId(null);
      setSelectedSnapshot(null);
      setIsCreating(true);
      return;
    }
    const nextNotice = notices[0] ?? null;
    setSelectedNoticeId(nextNotice?.id ?? null);
    setSelectedSnapshot(nextNotice);
    if (nextNotice) resetFromNotice(nextNotice);
  }, [
    isCreating,
    listedNotice,
    notices,
    noticesQuery.isSuccess,
    resetFromNotice,
    selectedNoticeId,
    selectedSnapshot,
    statusFilter,
  ]);

  useEffect(() => {
    if (isCreating || !selectedNotice) return;
    resetFromNotice(selectedNotice);
  }, [isCreating, resetFromNotice, selectedNotice]);

  const invalidateNoticeQueries = () => {
    void queryClient.invalidateQueries({ queryKey: ['platform-notices'] });
  };

  const saveMutation = useMutation({
    mutationFn: (variables: SaveVariables) => (
      variables.mode === 'create'
        ? createPlatformNotice(variables.payload)
        : updatePlatformNotice(variables.noticeId, variables.payload)
    ),
    onSuccess: (notice) => {
      setStatusFilter('all');
      setSelectedNoticeId(notice.id);
      setSelectedSnapshot(notice);
      setIsCreating(false);
      resetFromNotice(notice);
      setActionNotice('Rascunho salvo. A prévia real já pode ser gerada.');
      invalidateNoticeQueries();
      toast({ title: 'Rascunho salvo' });
    },
    onError: (error) => setActionError(errorMessage(error)),
  });

  const previewMutation = useMutation({
    mutationFn: (noticeId: string) => previewPlatformNoticeRecipients(noticeId, 50),
    onSuccess: (result) => {
      setPreview(result);
      setActionNotice(`Prévia atualizada com ${result.totalRecipients} destinatário(s).`);
      setActionError(null);
      void queryClient.invalidateQueries({ queryKey: ['platform-notices', result.noticeId, 'audit'] });
    },
    onError: (error) => setActionError(errorMessage(error)),
  });

  const scheduleMutation = useMutation({
    mutationFn: ({ noticeId, value }: { noticeId: string; value: string }) => (
      schedulePlatformNotice(noticeId, value)
    ),
    onSuccess: (notice) => {
      setStatusFilter('all');
      setSelectedSnapshot(notice);
      resetFromNotice(notice);
      setActionNotice(`Comunicado agendado para ${formatDate(notice.scheduledAt)}.`);
      invalidateNoticeQueries();
      toast({ title: 'Publicação agendada' });
    },
    onError: (error) => setActionError(errorMessage(error)),
  });

  const publishMutation = useMutation({
    mutationFn: (noticeId: string) => publishPlatformNotice(noticeId),
    onSuccess: (publication) => {
      setStatusFilter('all');
      setSelectedSnapshot(publication.notice);
      resetFromNotice(publication.notice);
      setActionNotice(
        `Publicado para ${publication.recipientCount} destinatário(s), com ${publication.receiptCount} recibo(s) local(is).`,
      );
      invalidateNoticeQueries();
      toast({ title: 'Comunicado publicado' });
    },
    onError: (error) => setActionError(errorMessage(error)),
  });

  const cancelMutation = useMutation({
    mutationFn: (noticeId: string) => cancelPlatformNotice(noticeId),
    onSuccess: (notice) => {
      setStatusFilter('all');
      setSelectedSnapshot(notice);
      resetFromNotice(notice);
      setActionNotice('Comunicado cancelado e recibos locais marcados como cancelados.');
      invalidateNoticeQueries();
      toast({ title: 'Comunicado cancelado' });
    },
    onError: (error) => setActionError(errorMessage(error)),
  });

  const dispatchMutation = useMutation({
    mutationFn: () => dispatchDuePlatformNotices(100),
    onSuccess: (result) => {
      setActionNotice(`${result.processedCount} comunicado(s) agendado(s) vencido(s) processado(s).`);
      invalidateNoticeQueries();
      toast({ title: 'Fila de agendados processada' });
    },
    onError: (error) => setActionError(errorMessage(error)),
  });

  const changeForm = <Key extends keyof NoticeFormState,>(
    key: Key,
    value: NoticeFormState[Key],
  ) => {
    setForm((current) => ({ ...current, [key]: value }));
    setIsDirty(true);
    setPreview(null);
    setActionError(null);
    setActionNotice(null);
  };

  const canLeaveEditor = () => (
    !isDirty || window.confirm('Descartar as alterações ainda não salvas?')
  );

  const startNewDraft = () => {
    if (!canLeaveEditor()) return;
    setSelectedNoticeId(null);
    setSelectedSnapshot(null);
    setIsCreating(true);
    setForm(emptyForm());
    setScheduledAt('');
    setIsDirty(false);
    setPreview(null);
    setActionError(null);
    setActionNotice(null);
  };

  const selectNotice = (notice: PlatformNotice) => {
    if (notice.id === selectedNoticeId || !canLeaveEditor()) return;
    setIsCreating(false);
    setSelectedNoticeId(notice.id);
    setSelectedSnapshot(notice);
    setActionError(null);
    setActionNotice(null);
  };

  const handleStatusFilter = (value: 'all' | PlatformNoticeStatus) => {
    if (!canLeaveEditor()) return;
    setStatusFilter(value);
    setIsCreating(false);
    setSelectedNoticeId(null);
    setSelectedSnapshot(null);
    setPreview(null);
    setActionError(null);
    setActionNotice(null);
  };

  const handleSave = () => {
    setActionError(null);
    setActionNotice(null);
    const validationError = validateForm(form);
    if (validationError) {
      setActionError(validationError);
      return;
    }
    const payload: PlatformNoticeCreateInput = {
      title: form.title.trim(),
      body: form.body.trim(),
      audience: buildAudience(form),
      channels: form.channels,
      requiresAcknowledgement: form.requiresAcknowledgement,
    };
    if (isCreating || !selectedNotice) {
      saveMutation.mutate({ mode: 'create', payload });
      return;
    }
    const updatePayload: PlatformNoticeUpdateInput = { expectedVersion: selectedNotice.version };
    if (payload.title !== selectedNotice.title) updatePayload.title = payload.title;
    if (payload.body !== selectedNotice.body) updatePayload.body = payload.body;
    if (!audiencesEqual(payload.audience, selectedNotice.audience)) updatePayload.audience = payload.audience;
    if (!sameStringSet(payload.channels, selectedNotice.channels)) updatePayload.channels = payload.channels;
    if (payload.requiresAcknowledgement !== selectedNotice.requiresAcknowledgement) {
      updatePayload.requiresAcknowledgement = payload.requiresAcknowledgement;
    }
    if (Object.keys(updatePayload).length === 1) {
      setIsDirty(false);
      setActionNotice('Nenhuma alteração efetiva para salvar.');
      return;
    }
    saveMutation.mutate({
      mode: 'update',
      noticeId: selectedNotice.id,
      payload: updatePayload,
    });
  };

  const handleSchedule = () => {
    setActionError(null);
    if (!selectedNotice || !scheduledAt) {
      setActionError('Informe a data e a hora do agendamento.');
      return;
    }
    const date = new Date(scheduledAt);
    if (Number.isNaN(date.getTime()) || date.getTime() <= Date.now()) {
      setActionError('Escolha uma data e hora futuras para o agendamento.');
      return;
    }
    scheduleMutation.mutate({ noticeId: selectedNotice.id, value: date.toISOString() });
  };

  const toggleChannel = (channel: PlatformNoticeChannel, checked: boolean) => {
    const channels = checked
      ? [...new Set([...form.channels, channel])]
      : form.channels.filter((item) => item !== channel);
    changeForm('channels', channels);
    if (channel === 'in_app' && !checked && form.requiresAcknowledgement) {
      setForm((current) => ({ ...current, channels, requiresAcknowledgement: false }));
    }
  };

  const toggleRuleValue = (value: PlatformNoticeRuleValue, checked: boolean) => {
    changeForm(
      'ruleValues',
      checked ? [...new Set([...form.ruleValues, value])] : form.ruleValues.filter((item) => item !== value),
    );
  };

  const canEdit = canManage && (isCreating || selectedNotice?.status === 'draft' || selectedNotice?.status === 'scheduled');
  const canRunLifecycle = canManage
    && Boolean(selectedNotice)
    && (selectedNotice?.status === 'draft' || selectedNotice?.status === 'scheduled');
  const hasCurrentPreview = Boolean(
    selectedNotice && preview?.noticeId === selectedNotice.id && !isDirty,
  );
  const hasRecipients = hasCurrentPreview && (preview?.totalRecipients ?? 0) > 0;
  const isBusy = saveMutation.isPending
    || previewMutation.isPending
    || scheduleMutation.isPending
    || publishMutation.isPending
    || cancelMutation.isPending;
  const ruleOptions = form.ruleField === 'user_role' ? USER_ROLE_OPTIONS : WORKSPACE_ROLE_OPTIONS;

  if (!canManage) {
    return (
      <Alert variant="destructive">
        <LockKeyhole className="h-4 w-4" aria-hidden="true" />
        <AlertTitle>Acesso restrito</AlertTitle>
        <AlertDescription>
          A Central de Comunicados exige perfil Super Admin ou a permissão individual de gerenciamento.
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <section className="space-y-5" aria-labelledby="platform-announcements-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="platform-announcements-title" className="flex items-center gap-2 text-lg font-semibold">
            <Megaphone className="h-5 w-5 text-primary" aria-hidden="true" />
            Central de Comunicados
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Prepare, confira e publique avisos da plataforma com rastreabilidade por destinatário.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1">
            <FieldHelp
              label="Fila dos agendados"
              description="Processa comunicados cuja data agendada já passou e registra os recibos locais."
              whereToFind="Use esta ação somente quando o processamento automático não tiver ocorrido."
            />
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button type="button" variant="outline" disabled={dispatchMutation.isPending}>
                  {dispatchMutation.isPending
                    ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
                    : <CalendarClock className="mr-2 h-4 w-4" aria-hidden="true" />}
                  Processar agendados vencidos
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Processar publicações vencidas?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Até 100 comunicados agendados com horário vencido serão publicados. O canal in-app fica
                    disponível imediatamente; e-mails permanecem apenas na fila local.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Voltar</AlertDialogCancel>
                  <AlertDialogAction onClick={() => dispatchMutation.mutate()}>
                    Confirmar processamento
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
          <Button type="button" onClick={startNewDraft}>
            <Plus className="mr-2 h-4 w-4" aria-hidden="true" />Novo comunicado
          </Button>
        </div>
      </div>

      <Alert className="border-amber-300 bg-amber-50/70 dark:border-amber-900 dark:bg-amber-950/20">
        <LockKeyhole className="h-4 w-4" aria-hidden="true" />
        <AlertTitle>E-mail em fila local — APIs externas fechadas</AlertTitle>
        <AlertDescription>
          O canal e-mail registra destinatários e estados para auditoria, mas não tenta enviar mensagens para
          fora da plataforma nesta versão. O in-app é o único canal disponibilizado ao usuário agora.
        </AlertDescription>
      </Alert>

      {(actionError || actionNotice) && (
        <div
          className={actionError
            ? 'rounded-md border border-destructive/50 bg-destructive/5 px-4 py-3 text-sm text-destructive'
            : 'rounded-md border bg-muted/30 px-4 py-3 text-sm'}
          role={actionError ? 'alert' : 'status'}
          aria-live="polite"
        >
          {actionError ?? actionNotice}
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(18rem,0.8fr)_minmax(0,2fr)]">
        <Card className="h-fit">
          <CardHeader className="space-y-3">
            <div>
              <CardTitle className="text-base">Comunicados</CardTitle>
              <CardDescription>{noticesQuery.data?.total ?? 0} registro(s) no filtro atual</CardDescription>
            </div>
            <Select value={statusFilter} onValueChange={(value) => handleStatusFilter(value as 'all' | PlatformNoticeStatus)}>
              <SelectTrigger aria-label="Filtrar comunicados por status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todos os status</SelectItem>
                <SelectItem value="draft">Rascunhos</SelectItem>
                <SelectItem value="scheduled">Agendados</SelectItem>
                <SelectItem value="published">Publicados</SelectItem>
                <SelectItem value="cancelled">Cancelados</SelectItem>
              </SelectContent>
            </Select>
          </CardHeader>
          <CardContent>
            {noticesQuery.isLoading && (
              <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground" role="status">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />Carregando comunicados...
              </div>
            )}
            {noticesQuery.isError && (
              <div className="space-y-3 py-4" role="alert">
                <p className="text-sm text-destructive">{errorMessage(noticesQuery.error)}</p>
                <Button type="button" size="sm" variant="outline" onClick={() => noticesQuery.refetch()}>
                  <RefreshCw className="mr-2 h-4 w-4" aria-hidden="true" />Tentar novamente
                </Button>
              </div>
            )}
            {!noticesQuery.isLoading && !noticesQuery.isError && notices.length === 0 && (
              <p className="py-8 text-center text-sm text-muted-foreground">
                Nenhum comunicado neste status.
              </p>
            )}
            <div className="space-y-2" role="list" aria-label="Lista de comunicados">
              {notices.map((notice) => (
                <div key={notice.id} role="listitem">
                  <button
                    type="button"
                    aria-current={!isCreating && notice.id === selectedNoticeId ? 'true' : undefined}
                    onClick={() => selectNotice(notice)}
                    className="w-full rounded-lg border p-3 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-[current=true]:border-primary aria-[current=true]:bg-primary/5"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="min-w-0 line-clamp-2 text-sm font-semibold">{notice.title}</span>
                      <Badge className="shrink-0" variant={statusVariant(notice.status)}>{STATUS_LABELS[notice.status]}</Badge>
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {AUDIENCE_LABELS[notice.audience.kind]} · atualizado em {formatDate(notice.updatedAt)}
                    </p>
                    <div className="mt-2 flex gap-2 text-xs text-muted-foreground">
                      {notice.channels.includes('in_app') && <span>In-app</span>}
                      {notice.channels.includes('email') && <span>E-mail (fila local)</span>}
                    </div>
                  </button>
                </div>
              ))}
            </div>
            {noticesQuery.data && noticesQuery.data.total > 0 && (
              <div className="mt-4 flex items-center justify-between gap-3 border-t pt-3">
                <span className="text-xs text-muted-foreground">{noticePage * noticePageSize + 1}–{Math.min(noticePage * noticePageSize + notices.length, noticesQuery.data.total)} de {noticesQuery.data.total}</span>
                <span className="flex gap-2"><Button type="button" size="sm" variant="outline" disabled={noticePage === 0 || noticesQuery.isFetching} onClick={() => setNoticePage((value) => Math.max(0, value - 1))}>Anterior</Button><Button type="button" size="sm" variant="outline" disabled={(noticePage + 1) * noticePageSize >= noticesQuery.data.total || noticesQuery.isFetching} onClick={() => setNoticePage((value) => value + 1)}>Próxima</Button></span>
              </div>
            )}
          </CardContent>
        </Card>

        {!isCreating && !selectedNotice ? (
          <Card>
            <CardContent className="flex min-h-72 flex-col items-center justify-center gap-3 text-center">
              <Megaphone className="h-10 w-10 text-muted-foreground" aria-hidden="true" />
              <p className="font-medium">Selecione um comunicado ou crie um novo rascunho.</p>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <CardTitle className="text-base">{editorTitle(isCreating, selectedNotice)}</CardTitle>
                  <CardDescription>
                    {selectedNotice
                      ? `${STATUS_LABELS[selectedNotice.status]} · versão ${selectedNotice.version} · atualizado em ${formatDate(selectedNotice.updatedAt)}`
                      : 'O comunicado será criado como rascunho.'}
                  </CardDescription>
                </div>
                {selectedNotice && <Badge variant={statusVariant(selectedNotice.status)}>{STATUS_LABELS[selectedNotice.status]}</Badge>}
              </div>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="grid gap-5 lg:grid-cols-2">
                <div className="space-y-2 lg:col-span-2">
                  <Label htmlFor="announcement-title">Título</Label>
                  <Input
                    id="announcement-title"
                    value={form.title}
                    maxLength={240}
                    disabled={!canEdit || isBusy}
                    onChange={(event) => changeForm('title', event.target.value)}
                    placeholder="Ex.: Manutenção programada"
                  />
                  <p className="text-xs text-muted-foreground">{form.title.length}/240 caracteres</p>
                </div>

                <div className="space-y-2 lg:col-span-2">
                  <Label htmlFor="announcement-body">Mensagem</Label>
                  <Textarea
                    id="announcement-body"
                    value={form.body}
                    maxLength={50_000}
                    rows={7}
                    disabled={!canEdit || isBusy}
                    onChange={(event) => changeForm('body', event.target.value)}
                    placeholder="Explique o que aconteceu, quem é afetado e o que a pessoa precisa fazer."
                  />
                  <p className="text-xs text-muted-foreground">{form.body.length}/50.000 caracteres</p>
                </div>

                <fieldset className="space-y-3 rounded-lg border p-4 lg:col-span-2">
                  <legend className="px-1">
                    <span className="inline-flex items-center gap-1 text-sm font-medium">
                      Público
                      <FieldHelp
                        label="Público"
                        description="Define quem receberá o comunicado quando ele for publicado."
                        whereToFind="Escolha público geral, contas, usuários ou uma regra por papel. A prévia mostrará o resultado real antes da publicação."
                      />
                    </span>
                  </legend>
                  <Select
                    value={form.audienceKind}
                    disabled={!canEdit || isBusy}
                    onValueChange={(value) => changeForm('audienceKind', value as PlatformNoticeAudienceKind)}
                  >
                    <SelectTrigger aria-label="Tipo de público">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="global">Público geral</SelectItem>
                      <SelectItem value="workspaces">Contas específicas</SelectItem>
                      <SelectItem value="users">Usuários específicos</SelectItem>
                      <SelectItem value="rule">Regra por papel</SelectItem>
                    </SelectContent>
                  </Select>

                  {form.audienceKind === 'global' && (
                    <div className="flex items-start gap-2 rounded-md bg-muted/40 p-3 text-sm">
                      <Users className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                      <p>Inclui todos os usuários humanos ativos da plataforma no momento da publicação.</p>
                    </div>
                  )}

                  {form.audienceKind === 'workspaces' && (
                    <div className="space-y-2">
                      <Label>Contas destinatárias</Label>
                      <PlatformEntityMultiSelect entityType="accounts" purpose="announcement_audience" value={form.workspaceIds} onChange={(value) => changeForm('workspaceIds', value)} label="Selecionar contas destinatárias" placeholder="Pesquisar contas" disabled={!canEdit || isBusy} />
                    </div>
                  )}

                  {form.audienceKind === 'users' && (
                    <div className="space-y-2">
                      <Label>Usuários destinatários</Label>
                      <PlatformEntityMultiSelect entityType="users" purpose="announcement_audience" value={form.userIds} onChange={(value) => changeForm('userIds', value)} label="Selecionar usuários destinatários" placeholder="Pesquisar usuários" disabled={!canEdit || isBusy} />
                    </div>
                  )}

                  {form.audienceKind === 'rule' && (
                    <div className="grid gap-4 md:grid-cols-[minmax(12rem,0.7fr)_minmax(0,1.3fr)]">
                      <div className="space-y-2">
                        <div className="flex min-h-7 items-center gap-1">
                          <Label>Campo da regra</Label>
                          <FieldHelp
                            label="Campo da regra"
                            description="Escolhe se a regra compara o papel geral da pessoa ou o papel que ela exerce dentro de uma conta."
                            whereToFind="O papel consta no cadastro do usuário; o papel na conta consta na lista de membros de cada conta."
                          />
                        </div>
                        <Select
                          value={form.ruleField}
                          disabled={!canEdit || isBusy}
                          onValueChange={(value) => {
                            changeForm('ruleField', value as PlatformNoticeRuleField);
                            setForm((current) => ({ ...current, ruleField: value as PlatformNoticeRuleField, ruleValues: [] }));
                          }}
                        >
                          <SelectTrigger aria-label="Campo da regra de público">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="user_role">Papel geral do usuário</SelectItem>
                            <SelectItem value="workspace_role">Papel dentro da conta</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <fieldset className="space-y-2">
                        <legend className="flex min-h-7 items-center gap-1 text-sm font-medium">
                          Papéis incluídos
                          <FieldHelp
                            label="Papéis incluídos"
                            description="O comunicado alcançará pessoas ativas com pelo menos um dos papéis marcados. Agentes de IA não recebem comunicados."
                            whereToFind="Use os mesmos nomes de papel exibidos nos cadastros de usuários ou membros das contas."
                          />
                        </legend>
                        <div className="grid gap-2 sm:grid-cols-2">
                          {ruleOptions.map((option) => (
                            <div key={option.value} className="flex items-center gap-2">
                              <Checkbox
                                id={`announcement-rule-${option.value}`}
                                checked={form.ruleValues.includes(option.value)}
                                disabled={!canEdit || isBusy}
                                onCheckedChange={(checked) => toggleRuleValue(option.value, checked === true)}
                              />
                              <Label htmlFor={`announcement-rule-${option.value}`} className="font-normal">
                                {option.label}
                              </Label>
                            </div>
                          ))}
                        </div>
                      </fieldset>
                    </div>
                  )}
                </fieldset>

                <fieldset className="space-y-3 rounded-lg border p-4">
                  <legend className="px-1">
                    <span className="inline-flex items-center gap-1 text-sm font-medium">
                      Canais de entrega
                      <FieldHelp
                        label="Canais de entrega"
                        description="In-app cria um aviso dentro da plataforma. E-mail cria somente um item rastreável na fila local."
                        whereToFind="Marque um ou os dois canais. Nesta versão, nenhuma integração externa de e-mail é chamada."
                      />
                    </span>
                  </legend>
                  <div className="flex items-center gap-2">
                    <Checkbox
                      id="announcement-channel-in-app"
                      checked={form.channels.includes('in_app')}
                      disabled={!canEdit || isBusy}
                      onCheckedChange={(checked) => toggleChannel('in_app', checked === true)}
                    />
                    <Label htmlFor="announcement-channel-in-app" className="flex items-center gap-2 font-normal">
                      <Monitor className="h-4 w-4" aria-hidden="true" />In-app
                    </Label>
                  </div>
                  <div className="flex items-center gap-2">
                    <Checkbox
                      id="announcement-channel-email"
                      checked={form.channels.includes('email')}
                      disabled={!canEdit || isBusy}
                      onCheckedChange={(checked) => toggleChannel('email', checked === true)}
                    />
                    <Label htmlFor="announcement-channel-email" className="flex items-center gap-2 font-normal">
                      <Mail className="h-4 w-4" aria-hidden="true" />E-mail — somente fila local
                    </Label>
                  </div>
                </fieldset>

                <fieldset className="space-y-3 rounded-lg border p-4">
                  <legend className="px-1">
                    <span className="inline-flex items-center gap-1 text-sm font-medium">
                      Confirmação de leitura
                      <FieldHelp
                        label="Confirmação de leitura"
                        description="Pede que a pessoa confirme conscientemente que leu o aviso in-app; isso é diferente de apenas abrir."
                        whereToFind="A confirmação aparecerá no recibo do canal in-app. Ela não funciona quando somente e-mail está marcado."
                      />
                    </span>
                  </legend>
                  <div className="flex items-start gap-2">
                    <Checkbox
                      id="announcement-acknowledgement"
                      checked={form.requiresAcknowledgement}
                      disabled={!canEdit || isBusy || !form.channels.includes('in_app')}
                      onCheckedChange={(checked) => changeForm('requiresAcknowledgement', checked === true)}
                    />
                    <Label htmlFor="announcement-acknowledgement" className="font-normal leading-5">
                      Exigir confirmação explícita no aviso in-app
                    </Label>
                  </div>
                </fieldset>
              </div>

              {canEdit && (
                <div className="flex flex-wrap items-center gap-3 border-t pt-5">
                  <Button
                    type="button"
                    onClick={handleSave}
                    disabled={saveMutation.isPending || (!isCreating && !isDirty)}
                  >
                    {saveMutation.isPending
                      ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
                      : <Save className="mr-2 h-4 w-4" aria-hidden="true" />}
                    {isCreating ? 'Criar rascunho' : 'Salvar alterações'}
                  </Button>
                  {isDirty && <span className="text-xs text-amber-700 dark:text-amber-300">Alterações ainda não salvas</span>}
                </div>
              )}

              {selectedNotice && (
                <div className="space-y-4 border-t pt-5">
                  <div className="grid gap-3 lg:grid-cols-[minmax(14rem,1fr)_auto]">
                    <div className="space-y-2">
                      <FieldHelpLabel
                        htmlFor="announcement-scheduled-at"
                        label="Data e hora do envio"
                        description="Momento futuro em que o comunicado fica apto a ser publicado pelo processador de agendamentos."
                        whereToFind="Use o horário local exibido no seu dispositivo. Após salvar e gerar a prévia, escolha a data e confirme o agendamento."
                      />
                      <Input
                        id="announcement-scheduled-at"
                        type="datetime-local"
                        min={minimumLocalDateTime()}
                        value={scheduledAt}
                        disabled={!canRunLifecycle || isBusy}
                        onChange={(event) => {
                          setScheduledAt(event.target.value);
                          setActionError(null);
                        }}
                      />
                    </div>
                    <div className="flex items-end">
                      <Button
                        type="button"
                        variant="outline"
                        disabled={!canRunLifecycle || !hasRecipients || isBusy || !scheduledAt}
                        onClick={handleSchedule}
                      >
                        {scheduleMutation.isPending
                          ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
                          : <CalendarClock className="mr-2 h-4 w-4" aria-hidden="true" />}
                        {selectedNotice.status === 'scheduled' ? 'Reagendar' : 'Agendar'}
                      </Button>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button type="button" disabled={!canRunLifecycle || !hasRecipients || isBusy}>
                          <Send className="mr-2 h-4 w-4" aria-hidden="true" />Publicar agora
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>Publicar para {preview?.totalRecipients ?? 0} destinatário(s)?</AlertDialogTitle>
                          <AlertDialogDescription>
                            O aviso in-app ficará disponível imediatamente. Para e-mail, serão criados somente
                            registros na fila local, sem envio externo. Esta ação ficará registrada na auditoria.
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Voltar e revisar</AlertDialogCancel>
                          <AlertDialogAction onClick={() => publishMutation.mutate(selectedNotice.id)}>
                            Confirmar publicação
                          </AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>

                    {selectedNotice.status !== 'cancelled' && (
                      <AlertDialog>
                        <AlertDialogTrigger asChild>
                          <Button type="button" variant="destructive" disabled={isBusy}>
                            <Ban className="mr-2 h-4 w-4" aria-hidden="true" />
                            {selectedNotice.status === 'published' ? 'Retirar comunicado' : 'Cancelar'}
                          </Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>Cancelar este comunicado?</AlertDialogTitle>
                            <AlertDialogDescription>
                              O status mudará para cancelado e os recibos existentes serão marcados como
                              cancelados. A trilha de auditoria será preservada.
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>Manter comunicado</AlertDialogCancel>
                            <AlertDialogAction
                              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                              onClick={() => cancelMutation.mutate(selectedNotice.id)}
                            >
                              Confirmar cancelamento
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    )}
                  </div>

                  {canRunLifecycle && !hasRecipients && (
                    <p className="text-xs text-muted-foreground">
                      Salve as alterações e gere uma prévia real com ao menos um destinatário para liberar
                      publicação e agendamento.
                    </p>
                  )}
                </div>
              )}

              <Tabs defaultValue="preview" className="border-t pt-5">
                <TabsList className="grid h-auto w-full grid-cols-3">
                  <TabsTrigger value="preview"><Eye className="mr-2 h-4 w-4" aria-hidden="true" />Prévia</TabsTrigger>
                  <TabsTrigger value="receipts"><ReceiptText className="mr-2 h-4 w-4" aria-hidden="true" />Recibos</TabsTrigger>
                  <TabsTrigger value="audit"><History className="mr-2 h-4 w-4" aria-hidden="true" />Auditoria</TabsTrigger>
                </TabsList>

                <TabsContent value="preview" className="space-y-4 pt-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-1">
                        <h3 className="font-medium">Prévia real dos destinatários</h3>
                        <FieldHelp
                          label="Prévia real"
                          description="Consulta agora os usuários ativos que atendem ao público salvo; não é uma estimativa feita no navegador."
                          whereToFind="Salve o rascunho e clique em Gerar prévia real. Em públicos dinâmicos, o resultado pode mudar até a publicação."
                        />
                      </div>
                      <p className="text-sm text-muted-foreground">
                        A amostra exibe até 50 pessoas e registra a consulta na auditoria.
                      </p>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      disabled={!selectedNotice || isDirty || previewMutation.isPending}
                      onClick={() => selectedNotice && previewMutation.mutate(selectedNotice.id)}
                    >
                      {previewMutation.isPending
                        ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
                        : <Eye className="mr-2 h-4 w-4" aria-hidden="true" />}
                      Gerar prévia real
                    </Button>
                  </div>

                  {!selectedNotice && (
                    <p className="rounded-md bg-muted/40 p-4 text-sm text-muted-foreground">
                      Crie o rascunho para consultar os destinatários reais.
                    </p>
                  )}

                  {preview && hasCurrentPreview && (
                    <div className="space-y-4">
                      <div className="grid gap-3 sm:grid-cols-3">
                        <div className="rounded-lg border p-3">
                          <p className="text-xs text-muted-foreground">Destinatários</p>
                          <p className="text-2xl font-bold tabular-nums">{preview.totalRecipients}</p>
                        </div>
                        <div className="rounded-lg border p-3">
                          <p className="text-xs text-muted-foreground">Com e-mail cadastrado</p>
                          <p className="text-2xl font-bold tabular-nums">{preview.emailEligible}</p>
                        </div>
                        <div className="rounded-lg border p-3">
                          <p className="text-xs text-muted-foreground">Sem e-mail cadastrado</p>
                          <p className="text-2xl font-bold tabular-nums">{preview.emailBlockedMissingAddress}</p>
                        </div>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        Avaliado em {formatDate(preview.evaluatedAt)}. O público dinâmico será resolvido novamente na publicação.
                      </p>
                      {preview.sample.length === 0 ? (
                        <Alert variant="destructive">
                          <Users className="h-4 w-4" aria-hidden="true" />
                          <AlertTitle>Nenhum destinatário encontrado</AlertTitle>
                          <AlertDescription>Revise o público antes de publicar ou agendar.</AlertDescription>
                        </Alert>
                      ) : (
                        <Table aria-label="Amostra real de destinatários">
                          <TableHeader>
                            <TableRow>
                              <TableHead>Pessoa</TableHead>
                              <TableHead>Correspondência</TableHead>
                              <TableHead>E-mail</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {preview.sample.map((recipient) => (
                              <TableRow key={recipient.userId}>
                                <TableCell>
                                  <p className="font-medium">{recipient.name}</p>
                                  <p className="font-mono text-xs text-muted-foreground">{recipient.userId}</p>
                                </TableCell>
                                <TableCell>{matchedByLabel(recipient.matchedBy)}</TableCell>
                                <TableCell>{recipient.emailHint ?? 'Não cadastrado'}</TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      )}
                    </div>
                  )}
                </TabsContent>

                <TabsContent value="receipts" className="space-y-4 pt-3">
                  <div>
                    <h3 className="font-medium">Recibos por destinatário e canal</h3>
                    <p className="text-sm text-muted-foreground">
                      Estados locais de disponibilidade, fila, leitura e confirmação.
                    </p>
                  </div>
                  {!selectedNotice && <p className="text-sm text-muted-foreground">Salve o rascunho para consultar recibos.</p>}
                  {selectedNotice && receiptsQuery.isLoading && (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />Carregando recibos...
                    </div>
                  )}
                  {selectedNotice && receiptsQuery.isError && (
                    <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-destructive" role="alert">
                      <span>{errorMessage(receiptsQuery.error)}</span>
                      <Button type="button" size="sm" variant="outline" onClick={() => receiptsQuery.refetch()}>
                        Tentar novamente
                      </Button>
                    </div>
                  )}
                  {selectedNotice && !receiptsQuery.isLoading && !receiptsQuery.isError && (
                    <>
                      <p className="text-xs text-muted-foreground">
                        {receiptsQuery.data?.items.length ?? 0} de {receiptsQuery.data?.total ?? 0} recibo(s) exibido(s).
                      </p>
                      {(receiptsQuery.data?.items.length ?? 0) === 0 ? (
                        <p className="rounded-md bg-muted/40 p-4 text-sm text-muted-foreground">
                          Os recibos serão criados quando o comunicado for publicado.
                        </p>
                      ) : (
                        <Table aria-label="Recibos do comunicado">
                          <TableHeader>
                            <TableRow>
                              <TableHead>Destinatário</TableHead>
                              <TableHead>Canal</TableHead>
                              <TableHead>Estado</TableHead>
                              <TableHead>Leitura</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {receiptsQuery.data?.items.map((receipt) => (
                              <TableRow key={receipt.id}>
                                <TableCell>
                                  <p>{receipt.recipientName}</p>
                                  <p className="text-xs text-muted-foreground">{receipt.recipientAddressHint ?? 'Endereço não cadastrado'}</p>
                                </TableCell>
                                <TableCell>{receipt.channel === 'in_app' ? 'In-app' : 'E-mail (fila local)'}</TableCell>
                                <TableCell>
                                  <Badge variant={receiptVariant(receipt.state)}>{RECEIPT_STATE_LABELS[receipt.state]}</Badge>
                                </TableCell>
                                <TableCell className="text-xs">
                                  <p>Lido: {formatDate(receipt.readAt)}</p>
                                  <p>Confirmado: {formatDate(receipt.acknowledgedAt)}</p>
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      )}
                      {(receiptsQuery.data?.total ?? 0) > 0 && (
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-xs text-muted-foreground">
                            Página {receiptPage + 1} · total real de {receiptsQuery.data?.total ?? 0}
                          </span>
                          <span className="flex gap-2">
                            <Button type="button" size="sm" variant="outline" disabled={receiptPage === 0 || receiptsQuery.isFetching} onClick={() => setReceiptPage((value) => Math.max(0, value - 1))}>Anterior</Button>
                            <Button type="button" size="sm" variant="outline" disabled={!receiptsQuery.data?.hasMore || receiptsQuery.isFetching} onClick={() => setReceiptPage((value) => value + 1)}>Próxima</Button>
                          </span>
                        </div>
                      )}
                    </>
                  )}
                </TabsContent>

                <TabsContent value="audit" className="space-y-4 pt-3">
                  <div>
                    <h3 className="font-medium">Trilha de auditoria</h3>
                    <p className="text-sm text-muted-foreground">Histórico cronológico preservado pelo backend.</p>
                  </div>
                  {!selectedNotice && <p className="text-sm text-muted-foreground">Salve o rascunho para iniciar a auditoria.</p>}
                  {selectedNotice && auditQuery.isLoading && (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />Carregando auditoria...
                    </div>
                  )}
                  {selectedNotice && auditQuery.isError && (
                    <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-destructive" role="alert">
                      <span>{errorMessage(auditQuery.error)}</span>
                      <Button type="button" size="sm" variant="outline" onClick={() => auditQuery.refetch()}>
                        Tentar novamente
                      </Button>
                    </div>
                  )}
                  {selectedNotice && !auditQuery.isLoading && !auditQuery.isError && (
                    (auditQuery.data?.items.length ?? 0) === 0 ? (
                      <p className="rounded-md bg-muted/40 p-4 text-sm text-muted-foreground">Nenhum evento registrado.</p>
                    ) : (
                      <>
                        <ol className="space-y-3" aria-label="Eventos de auditoria do comunicado">
                          {auditQuery.data?.items.map((event) => (
                            <li key={event.id} className="rounded-lg border p-3 text-sm">
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <span className="font-medium">{AUDIT_ACTION_LABELS[event.action]}</span>
                                <time className="text-xs text-muted-foreground" dateTime={event.occurredAt}>
                                  {formatDate(event.occurredAt)}
                                </time>
                              </div>
                              <p className="mt-1 text-xs text-muted-foreground">
                                Responsável: {event.actorUserName ?? 'Sistema'}
                              </p>
                              {Object.keys(event.details).length > 0 && (
                                <details className="mt-2">
                                  <summary className="cursor-pointer text-xs font-medium">Ver dados técnicos do evento</summary>
                                  <pre className="mt-2 max-h-48 overflow-auto rounded bg-muted p-2 text-xs">
                                    {JSON.stringify(event.details, null, 2)}
                                  </pre>
                                </details>
                              )}
                            </li>
                          ))}
                        </ol>
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-xs text-muted-foreground">Página {auditPage + 1} · total real de {auditQuery.data?.total ?? 0}</span>
                          <span className="flex gap-2">
                            <Button type="button" size="sm" variant="outline" disabled={auditPage === 0 || auditQuery.isFetching} onClick={() => setAuditPage((value) => Math.max(0, value - 1))}>Anterior</Button>
                            <Button type="button" size="sm" variant="outline" disabled={!auditQuery.data?.hasMore || auditQuery.isFetching} onClick={() => setAuditPage((value) => value + 1)}>Próxima</Button>
                          </span>
                        </div>
                      </>
                    )
                  )}
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>
        )}
      </div>
    </section>
  );
}
