import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  History,
  Loader2,
  RefreshCcw,
  ShieldCheck,
  Tag,
  UserRoundCog,
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
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { FieldHelpLabel } from '@/components/ui/field-help';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import type { PlatformSearchResult } from '@/services/platform-search';
import {
  createStaffReassignment,
  listStaffReassignmentsPage,
  previewStaffReassignment,
  restoreStaffReassignment,
  type StaffReassignmentMode,
  type StaffReassignmentPlan,
  type StaffReassignmentPreview,
  type StaffReassignmentScope,
  type StaffReassignmentSurface,
} from '@/services/staff-reassignment';

export interface StaffContinuityUser {
  id: string;
  name: string;
  email?: string | null;
  roleLabel?: string | null;
  isActive: boolean;
}

interface StaffContinuityPanelProps {
  surface: StaffReassignmentSurface;
  users: StaffContinuityUser[];
  loadingUsers?: boolean;
  usePlatformLookup?: boolean;
  canManage: boolean;
  title?: string;
  description?: string;
}

type ReasonKind = 'illness' | 'vacation' | 'leave' | 'departure' | 'other';

const REASON_OPTIONS: Array<{ value: ReasonKind; label: string }> = [
  { value: 'illness', label: 'Doença ou afastamento de saúde' },
  { value: 'vacation', label: 'Férias' },
  { value: 'leave', label: 'Licença ou ausência programada' },
  { value: 'departure', label: 'Saída da equipe' },
  { value: 'other', label: 'Outro motivo' },
];
const HISTORY_PAGE_SIZE = 10;

const SCOPE_OPTIONS: Record<StaffReassignmentSurface, Array<{
  value: StaffReassignmentScope;
  label: string;
  description: string;
}>> = {
  workspace: [
    { value: 'matters', label: 'Leads e casos', description: 'Responsabilidade atual pelos casos.' },
    { value: 'tasks', label: 'Tarefas e follow-ups', description: 'Pendências atribuídas à pessoa.' },
    { value: 'meetings', label: 'Reuniões', description: 'Responsabilidade pelas reuniões já registradas.' },
    { value: 'booking_links', label: 'Links de agendamento', description: 'Agendas e links sob responsabilidade da pessoa.' },
    { value: 'omnichannel_threads', label: 'Conversas omnichannel', description: 'Atendimentos atribuídos na caixa unificada.' },
    { value: 'whatsapp_contacts', label: 'Contatos do WhatsApp', description: 'Carteira operacional vinculada ao responsável.' },
    { value: 'social_conversations', label: 'Conversas sociais', description: 'Atendimentos de Instagram, Facebook e canais sociais.' },
  ],
  platform: [
    { value: 'support_tickets', label: 'Tickets de suporte e TI', description: 'Chamados sob responsabilidade do funcionário.' },
    { value: 'growth_prospects', label: 'Oportunidades comerciais', description: 'Prospects e oportunidades atribuídos no Blackbox.' },
  ],
};

const STATUS_LABEL: Record<StaffReassignmentPlan['status'], string> = {
  applied: 'Em vigor',
  restored: 'Restaurada',
  partially_restored: 'Restaurada com conflitos',
  cancelled: 'Cancelada',
};

const MODE_LABEL: Record<StaffReassignmentMode, string> = {
  temporary: 'Temporária',
  permanent: 'Definitiva',
};

function localDateTimeInput(date: Date): string {
  const offset = date.getTimezoneOffset();
  return new Date(date.getTime() - offset * 60_000).toISOString().slice(0, 16);
}

function formatDateTime(value?: string | null): string {
  if (!value) return 'Sem data';
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(new Date(value));
}

function buildReason(kind: ReasonKind, detail: string): string {
  const label = REASON_OPTIONS.find((option) => option.value === kind)?.label ?? 'Outro motivo';
  const normalizedDetail = detail.trim();
  return normalizedDetail ? `${label}: ${normalizedDetail}` : label;
}

function countLabel(count: number, singular: string): string {
  return `${count.toLocaleString('pt-BR')} ${count === 1 ? singular : 'itens'}`;
}

export function StaffContinuityPanel({
  surface,
  users,
  loadingUsers = false,
  usePlatformLookup = false,
  canManage,
  title = 'Continuidade da equipe',
  description = 'Redistribua responsabilidades com prévia, confirmação e histórico auditável.',
}: StaffContinuityPanelProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const scopes = SCOPE_OPTIONS[surface];
  const [sourceUserId, setSourceUserId] = useState('');
  const [targetUserId, setTargetUserId] = useState('');
  const [sourceLookupItem, setSourceLookupItem] = useState<PlatformSearchResult | null>(null);
  const [targetLookupItem, setTargetLookupItem] = useState<PlatformSearchResult | null>(null);
  const [mode, setMode] = useState<StaffReassignmentMode>('temporary');
  const [reasonKind, setReasonKind] = useState<ReasonKind>('vacation');
  const [reasonDetail, setReasonDetail] = useState('');
  const [startsAt, setStartsAt] = useState(() => localDateTimeInput(new Date()));
  const [endsAt, setEndsAt] = useState(() => {
    const end = new Date();
    end.setDate(end.getDate() + 7);
    return localDateTimeInput(end);
  });
  const [selectedScopes, setSelectedScopes] = useState<StaffReassignmentScope[]>(
    () => scopes.map((scope) => scope.value),
  );
  const [preview, setPreview] = useState<StaffReassignmentPreview | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [restorePlan, setRestorePlan] = useState<StaffReassignmentPlan | null>(null);
  const [restoreReason, setRestoreReason] = useState('');
  const [historyPage, setHistoryPage] = useState(0);

  const eligibleUsers = useMemo(
    () => users.filter((user) => user.isActive),
    [users],
  );
  const sourceUser = users.find((user) => user.id === sourceUserId)
    ?? (sourceLookupItem ? { id: sourceLookupItem.id, name: sourceLookupItem.label, isActive: sourceLookupItem.status === 'active' } : undefined);
  const targetUser = users.find((user) => user.id === targetUserId)
    ?? (targetLookupItem ? { id: targetLookupItem.id, name: targetLookupItem.label, isActive: targetLookupItem.status === 'active' } : undefined);
  const selectionValid = Boolean(
    sourceUserId
    && targetUserId
    && sourceUserId !== targetUserId
    && selectedScopes.length
    && startsAt
    && (mode === 'permanent' || endsAt),
  );

  const history = useQuery({
    queryKey: ['staff-reassignments', surface, historyPage],
    queryFn: ({ signal }) => listStaffReassignmentsPage(
      surface,
      HISTORY_PAGE_SIZE,
      historyPage * HISTORY_PAGE_SIZE,
      signal,
    ),
  });

  const previewMutation = useMutation({
    mutationFn: () => previewStaffReassignment(surface, {
      sourceUserId,
      targetUserId,
      scopes: selectedScopes,
    }),
    onSuccess: (value) => setPreview(value),
    onError: (error: Error) => {
      setPreview(null);
      toast({
        title: 'Não foi possível preparar a prévia',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const applyMutation = useMutation({
    mutationFn: () => createStaffReassignment(surface, {
      sourceUserId,
      targetUserId,
      scopes: selectedScopes,
      mode,
      reason: buildReason(reasonKind, reasonDetail),
      startsAt: new Date(startsAt).toISOString(),
      endsAt: mode === 'temporary' ? new Date(endsAt).toISOString() : null,
    }),
    onSuccess: async (plan) => {
      setConfirmOpen(false);
      setPreview(null);
      setHistoryPage(0);
      await queryClient.invalidateQueries({ queryKey: ['staff-reassignments', surface] });
      toast({
        title: 'Continuidade aplicada',
        description: `${plan.assignmentSummary
          ? Object.values(plan.assignmentSummary).reduce((sum, value) => sum + value, 0)
          : 0} itens receberam a etiqueta ${plan.tagLabel}.`,
      });
    },
    onError: (error: Error) => {
      setConfirmOpen(false);
      toast({
        title: 'A transferência não foi aplicada',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const restoreMutation = useMutation({
    mutationFn: () => {
      if (!restorePlan) throw new Error('Selecione uma transferência temporária.');
      return restoreStaffReassignment(surface, restorePlan.id, {
        expectedVersion: restorePlan.version,
        reason: restoreReason.trim(),
      });
    },
    onSuccess: async (plan) => {
      setRestorePlan(null);
      setRestoreReason('');
      setHistoryPage(0);
      await queryClient.invalidateQueries({ queryKey: ['staff-reassignments', surface] });
      toast({
        title: plan.conflictCount ? 'Restauração concluída com conflitos' : 'Responsabilidades restauradas',
        description: plan.conflictCount
          ? `${plan.conflictCount} itens foram preservados com o responsável atual para evitar sobrescrita.`
          : 'Os itens elegíveis voltaram ao responsável de origem.',
      });
    },
    onError: (error: Error) => {
      toast({
        title: 'Não foi possível restaurar',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const toggleScope = (scope: StaffReassignmentScope, checked: boolean) => {
    setPreview(null);
    setSelectedScopes((current) => (
      checked
        ? Array.from(new Set([...current, scope]))
        : current.filter((value) => value !== scope)
    ));
  };

  const resetPreview = () => setPreview(null);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <UserRoundCog className="h-5 w-5 text-primary" aria-hidden="true" />
            <h1 className="text-xl font-semibold">{title}</h1>
          </div>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{description}</p>
        </div>
        <Badge variant="outline" className="gap-1">
          <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
          Autoria e histórico preservados
        </Badge>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(300px,0.72fr)]">
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle className="text-base">Preparar transferência</CardTitle>
            <CardDescription>
              A prévia apenas conta os itens. Nenhuma responsabilidade muda antes da confirmação.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {!canManage && (
              <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                Você pode consultar o histórico, mas não possui permissão para transferir responsabilidades.
              </div>
            )}

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <FieldHelpLabel
                  htmlFor={`continuity-source-${surface}`}
                  label="Pessoa que ficará ausente"
                  description="É o responsável atual pelos itens que serão redistribuídos."
                  whereToFind="Selecione um membro da equipe ativa."
                />
                {usePlatformLookup ? <PlatformEntityCombobox
                  entityType="staff"
                  purpose="staff_reassignment_source"
                  value={sourceUserId || null}
                  onChange={(value, item) => {
                    setSourceUserId(value ?? '');
                    setSourceLookupItem(item);
                    resetPreview();
                  }}
                  label="Selecionar funcionário de origem"
                  placeholder="Pesquisar funcionário"
                  disabled={!canManage}
                /> : <Select
                  value={sourceUserId}
                  onValueChange={(value) => {
                    setSourceUserId(value);
                    resetPreview();
                  }}
                  disabled={!canManage || loadingUsers}
                >
                  <SelectTrigger id={`continuity-source-${surface}`}>
                    <SelectValue placeholder={loadingUsers ? 'Carregando equipe...' : 'Selecione a origem'} />
                  </SelectTrigger>
                  <SelectContent>
                    {users.map((user) => (
                      <SelectItem key={user.id} value={user.id}>
                        {user.name}{user.roleLabel ? ` · ${user.roleLabel}` : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>}
              </div>

              <div className="space-y-2">
                <FieldHelpLabel
                  htmlFor={`continuity-target-${surface}`}
                  label="Pessoa que assumirá"
                  description="Receberá somente os conjuntos selecionados e continuará sujeita às próprias permissões."
                  whereToFind="Escolha um membro ativo diferente da origem."
                />
                {usePlatformLookup ? <PlatformEntityCombobox
                  entityType="staff"
                  purpose="staff_reassignment_target"
                  value={targetUserId || null}
                  onChange={(value, item) => {
                    setTargetUserId(value ?? '');
                    setTargetLookupItem(item);
                    resetPreview();
                  }}
                  label="Selecionar funcionário substituto"
                  placeholder="Pesquisar funcionário ativo"
                  disabled={!canManage}
                /> : <Select
                  value={targetUserId}
                  onValueChange={(value) => {
                    setTargetUserId(value);
                    resetPreview();
                  }}
                  disabled={!canManage || loadingUsers}
                >
                  <SelectTrigger id={`continuity-target-${surface}`}>
                    <SelectValue placeholder="Selecione o substituto" />
                  </SelectTrigger>
                  <SelectContent>
                    {eligibleUsers
                      .filter((user) => user.id !== sourceUserId)
                      .map((user) => (
                        <SelectItem key={user.id} value={user.id}>
                          {user.name}{user.roleLabel ? ` · ${user.roleLabel}` : ''}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>}
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label>Tipo de substituição</Label>
                <div className="grid grid-cols-2 gap-2" role="group" aria-label="Tipo de substituição">
                  <Button
                    type="button"
                    variant={mode === 'temporary' ? 'default' : 'outline'}
                    onClick={() => {
                      setMode('temporary');
                      resetPreview();
                    }}
                    disabled={!canManage}
                  >
                    Temporária
                  </Button>
                  <Button
                    type="button"
                    variant={mode === 'permanent' ? 'default' : 'outline'}
                    onClick={() => {
                      setMode('permanent');
                      resetPreview();
                    }}
                    disabled={!canManage}
                  >
                    Definitiva
                  </Button>
                </div>
              </div>

              <div className="space-y-2">
                <FieldHelpLabel
                  htmlFor={`continuity-reason-${surface}`}
                  label="Motivo"
                  description="Fica registrado na auditoria e ajuda a equipe a entender a redistribuição."
                  whereToFind="Escolha a situação mais próxima do caso real."
                />
                <Select
                  value={reasonKind}
                  onValueChange={(value) => {
                    setReasonKind(value as ReasonKind);
                    resetPreview();
                  }}
                  disabled={!canManage}
                >
                  <SelectTrigger id={`continuity-reason-${surface}`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {REASON_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor={`continuity-start-${surface}`}>Início</Label>
                <Input
                  id={`continuity-start-${surface}`}
                  type="datetime-local"
                  value={startsAt}
                  onChange={(event) => {
                    setStartsAt(event.target.value);
                    resetPreview();
                  }}
                  disabled={!canManage}
                />
              </div>
              {mode === 'temporary' ? (
                <div className="space-y-2">
                  <Label htmlFor={`continuity-end-${surface}`}>Retorno previsto</Label>
                  <Input
                    id={`continuity-end-${surface}`}
                    type="datetime-local"
                    value={endsAt}
                    min={startsAt}
                    onChange={(event) => {
                      setEndsAt(event.target.value);
                      resetPreview();
                    }}
                    disabled={!canManage}
                  />
                </div>
              ) : (
                <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                  A transferência definitiva não agenda retorno automático e exige confirmação explícita.
                </div>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor={`continuity-detail-${surface}`}>Detalhes para auditoria</Label>
              <Textarea
                id={`continuity-detail-${surface}`}
                value={reasonDetail}
                onChange={(event) => {
                  setReasonDetail(event.target.value);
                  resetPreview();
                }}
                placeholder="Ex.: férias aprovadas de 5 a 19 de agosto; manter a equipe informada."
                disabled={!canManage}
              />
            </div>

            <Separator />

            <div className="space-y-3">
              <div>
                <Label>O que será redistribuído</Label>
                <p className="mt-1 text-xs text-muted-foreground">
                  Autoria, documentos, evidências, assinaturas e credenciais nunca são transferidos.
                </p>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {scopes.map((scope) => {
                  const id = `continuity-${surface}-${scope.value}`;
                  return (
                    <label
                      key={scope.value}
                      htmlFor={id}
                      className="flex cursor-pointer items-start gap-3 rounded-md border p-3"
                    >
                      <Checkbox
                        id={id}
                        checked={selectedScopes.includes(scope.value)}
                        onCheckedChange={(checked) => toggleScope(scope.value, checked === true)}
                        disabled={!canManage}
                      />
                      <span className="min-w-0">
                        <span className="block text-sm font-medium">{scope.label}</span>
                        <span className="mt-0.5 block text-xs text-muted-foreground">{scope.description}</span>
                      </span>
                    </label>
                  );
                })}
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
              <p className="text-xs text-muted-foreground">
                A etiqueta de origem é gerada no momento da aplicação e aparecerá no histórico.
              </p>
              <Button
                type="button"
                onClick={() => previewMutation.mutate()}
                disabled={!canManage || !selectionValid || previewMutation.isPending}
              >
                {previewMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Revisar antes de aplicar
              </Button>
            </div>
          </CardContent>
        </Card>

        <div className="min-w-0 space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Prévia</CardTitle>
              <CardDescription>Confira as pessoas, o período e a quantidade antes de confirmar.</CardDescription>
            </CardHeader>
            <CardContent>
              {!preview ? (
                <div className="rounded-md border border-dashed p-5 text-center text-sm text-muted-foreground">
                  Preencha os dados e clique em <strong>Revisar antes de aplicar</strong>.
                </div>
              ) : (
                <div className="space-y-4" aria-live="polite">
                  <div className="flex items-center gap-2 rounded-md bg-muted p-3">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{preview.sourceName}</p>
                      <p className="text-xs text-muted-foreground">Responsável de origem</p>
                    </div>
                    <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                    <div className="min-w-0 flex-1 text-right">
                      <p className="truncate text-sm font-medium">{preview.targetName}</p>
                      <p className="text-xs text-muted-foreground">Novo responsável</p>
                    </div>
                  </div>

                  <div>
                    <div className="flex items-center justify-between text-sm">
                      <span>Itens elegíveis</span>
                      <strong>{preview.total.toLocaleString('pt-BR')}</strong>
                    </div>
                    <Progress value={preview.total ? 100 : 0} className="mt-2" />
                  </div>

                  <div className="space-y-2">
                    {selectedScopes.map((scope) => {
                      const option = scopes.find((candidate) => candidate.value === scope);
                      const count = preview.counts[scope] ?? 0;
                      return (
                        <div key={scope} className="flex items-center justify-between gap-3 text-sm">
                          <span className="min-w-0 text-muted-foreground">{option?.label ?? scope}</span>
                          <Badge variant="secondary">{countLabel(count, 'item')}</Badge>
                        </div>
                      );
                    })}
                  </div>

                  <div className="rounded-md border p-3 text-sm">
                    <p className="font-medium">{MODE_LABEL[mode]}</p>
                    <p className="mt-1 text-muted-foreground">
                      {formatDateTime(new Date(startsAt).toISOString())}
                      {mode === 'temporary' ? ` até ${formatDateTime(new Date(endsAt).toISOString())}` : ' · sem retorno automático'}
                    </p>
                  </div>

                  {preview.total === 0 && (
                    <div className="flex gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                      Nenhum item elegível foi encontrado para os escopos selecionados.
                    </div>
                  )}

                  <Button
                    type="button"
                    className="w-full"
                    onClick={() => setConfirmOpen(true)}
                    disabled={!preview.total || applyMutation.isPending}
                  >
                    Confirmar transferência
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <History className="h-4 w-4" aria-hidden="true" />
                Histórico
              </CardTitle>
              <CardDescription>Transferências, etiquetas e eventuais conflitos de restauração.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {history.isLoading && (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Carregando histórico...
                </div>
              )}
              {history.isError && (
                <div className="flex items-center justify-between gap-3 rounded-md border border-red-200 bg-red-50 p-3">
                  <p className="text-sm text-red-800">Não foi possível carregar o histórico.</p>
                  <Button type="button" size="sm" variant="outline" onClick={() => history.refetch()}>
                    Tentar novamente
                  </Button>
                </div>
              )}
              {!history.isLoading && !history.isError && !history.data?.items.length && (
                <p className="text-sm text-muted-foreground">Nenhuma transferência registrada.</p>
              )}
              {(history.data?.items ?? []).map((plan) => (
                <div key={plan.id} className="space-y-3 rounded-md border p-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">
                        {plan.sourceName} <ArrowRight className="mx-1 inline h-3.5 w-3.5" /> {plan.targetName}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        Aplicada em {formatDateTime(plan.appliedAt)}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-1">
                      <Badge variant="outline">{MODE_LABEL[plan.mode]}</Badge>
                      <Badge variant={plan.conflictCount ? 'destructive' : 'secondary'}>
                        {STATUS_LABEL[plan.status]}
                      </Badge>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Tag className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    <span className="break-all">{plan.tagLabel}</span>
                  </div>
                  <p className="text-xs text-muted-foreground">{plan.reason}</p>
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(plan.assignmentSummary).map(([scope, count]) => (
                      <Badge key={scope} variant="outline">
                        {scopes.find((option) => option.value === scope)?.label ?? scope}: {count}
                      </Badge>
                    ))}
                  </div>
                  {plan.conflictCount > 0 && (
                    <div className="flex items-start gap-2 text-xs text-amber-800">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                      {plan.conflictCount} itens não foram sobrescritos porque mudaram depois da transferência.
                    </div>
                  )}
                  {plan.mode === 'temporary' && plan.status === 'applied' && canManage && (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setRestorePlan(plan);
                        setRestoreReason('');
                      }}
                    >
                      <RefreshCcw className="mr-2 h-3.5 w-3.5" />
                      Restaurar agora
                    </Button>
                  )}
                </div>
              ))}
              {history.data && (
                <div className="flex flex-wrap items-center justify-between gap-2 pt-1 text-xs text-muted-foreground">
                  <span>
                    {history.data.total === 0
                      ? '0 transferências'
                      : `${history.data.offset + 1}–${history.data.offset + history.data.items.length} de ${history.data.total}`}
                  </span>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={historyPage === 0 || history.isFetching}
                      onClick={() => setHistoryPage((page) => Math.max(0, page - 1))}
                    >
                      Anterior
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={!history.data.hasMore || history.isFetching}
                      onClick={() => setHistoryPage((page) => page + 1)}
                    >
                      Próxima
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirmar redistribuição?</AlertDialogTitle>
            <AlertDialogDescription>
              {preview?.total.toLocaleString('pt-BR') ?? 0} itens serão atribuídos de{' '}
              <strong>{sourceUser?.name}</strong> para <strong>{targetUser?.name}</strong>.
              A operação ficará registrada e receberá uma etiqueta de origem.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2 rounded-md border p-3 text-sm">
            <p className="flex items-center gap-2">
              <CalendarClock className="h-4 w-4 text-muted-foreground" />
              {MODE_LABEL[mode]}
              {mode === 'temporary' ? ` até ${formatDateTime(new Date(endsAt).toISOString())}` : ''}
            </p>
            <p className="flex items-start gap-2">
              <CheckCircle2 className="mt-0.5 h-4 w-4 text-emerald-600" />
              Autoria, documentos, evidências e histórico permanecem inalterados.
            </p>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={applyMutation.isPending}>Voltar e revisar</AlertDialogCancel>
            <AlertDialogAction
              onClick={(event) => {
                event.preventDefault();
                applyMutation.mutate();
              }}
              disabled={applyMutation.isPending}
            >
              {applyMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Aplicar transferência
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={Boolean(restorePlan)}
        onOpenChange={(open) => {
          if (!open) {
            setRestorePlan(null);
            setRestoreReason('');
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Restaurar responsabilidades?</AlertDialogTitle>
            <AlertDialogDescription>
              A restauração só volta itens que ainda permanecem com o substituto. Mudanças posteriores serão preservadas e registradas como conflito.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2">
            <Label htmlFor={`continuity-restore-reason-${surface}`}>Motivo da restauração</Label>
            <Textarea
              id={`continuity-restore-reason-${surface}`}
              value={restoreReason}
              onChange={(event) => setRestoreReason(event.target.value)}
              placeholder="Ex.: retorno antecipado confirmado pelo gestor."
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={restoreMutation.isPending}>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={(event) => {
                event.preventDefault();
                restoreMutation.mutate();
              }}
              disabled={restoreReason.trim().length < 3 || restoreMutation.isPending}
            >
              {restoreMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Restaurar itens elegíveis
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
