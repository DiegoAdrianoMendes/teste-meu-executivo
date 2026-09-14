import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CalendarClock,
  CheckCircle2,
  Eye,
  History,
  Layers3,
  Loader2,
  RefreshCw,
  Rocket,
  Save,
  UsersRound,
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
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { FieldHelp } from '@/components/ui/field-help';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { PlatformEntityMultiSelect } from '@/components/platform/PlatformEntityMultiSelect';
import {
  createPlanPolicyIdempotencyKey,
  getPlanPolicyEditor,
  getPlanPolicyEntitlementCatalog,
  listPlanPoliciesPage,
  listPlanPolicyVersionsPage,
  PLAN_POLICY_ENTITLEMENT_KEYS,
  previewPlanPolicyRollout,
  publishPlanPolicy,
  savePlanPolicyDraft,
  type PlanPolicyEditor,
  type PlanPolicyEntitlementCatalogItem,
  type PlanPolicyEntitlementKey,
  type PlanPolicyPlanSummary,
  type PlanPolicyPreview,
  type PlanPolicyPreviewRequest,
  type PlanPolicyPublication,
  type PlanPolicyPublishRequest,
  type PlanPolicyRolloutMode,
  type PlanPolicyRolloutRequest,
  type PlanPolicyRolloutRule,
  type PlanPolicySnapshot,
  type PlanPolicySubscriptionStatus,
  type PlanPolicyVersion,
  type PlanPolicyWorkspaceSetupMode,
  type PlanPolicyWorkspaceStatus,
} from '@/services/plan-policies';

const policyQueryKey = ['platform', 'plan-policies'] as const;
const EMPTY_PLAN_SUMMARIES: PlanPolicyPlanSummary[] = [];
const PLAN_PAGE_SIZE = 10;
const VERSION_PAGE_SIZE = 10;

const entitlementCopy: Record<PlanPolicyEntitlementKey, {
  label: string;
  unit?: string;
  help?: { description: string; whereToFind: string };
}> = {
  tokens: {
    label: 'Tokens de IA',
    unit: 'tokens por mês',
    help: {
      description: 'Unidades de texto processadas pelos modelos de inteligência artificial.',
      whereToFind: 'Nos relatórios de uso de IA de cada conta.',
    },
  },
  ai_executions: { label: 'Execuções de IA', unit: 'execuções por mês' },
  ai_conversations: { label: 'Conversas com IA', unit: 'conversas por mês' },
  audio_minutes: { label: 'Transcrição de áudio', unit: 'minutos por mês' },
  storage_bytes: {
    label: 'Armazenamento',
    unit: 'bytes',
    help: {
      description: 'Espaço total permitido para os arquivos da conta, medido em bytes.',
      whereToFind: 'No painel de armazenamento da conta.',
    },
  },
  files: { label: 'Arquivos', unit: 'arquivos' },
  users: { label: 'Usuários', unit: 'usuários' },
  whatsapp_numbers: { label: 'Números de WhatsApp', unit: 'números' },
  agents: { label: 'Agentes de IA', unit: 'agentes' },
  legal_areas: { label: 'Áreas jurídicas', unit: 'áreas' },
  automations: { label: 'Automações', unit: 'automações' },
  digital_signature: { label: 'Assinatura digital' },
  calendar: { label: 'Sincronização externa de calendário' },
  email: { label: 'E-mail' },
  webhooks: {
    label: 'Webhooks',
    help: {
      description: 'Avisos automáticos enviados para outro sistema quando um evento acontece.',
      whereToFind: 'Nas configurações de integrações da conta.',
    },
  },
  advanced_audit: { label: 'Auditoria avançada' },
  success_manager: { label: 'Gerente de sucesso' },
  monitored_processes: { label: 'Processos monitorados', unit: 'processos ativos' },
};

const rolloutCopy: Record<PlanPolicyRolloutMode, { label: string; description: string }> = {
  existing_and_new: {
    label: 'Atuais e novas',
    description: 'Inclui as contas atuais do plano e as criadas depois.',
  },
  new_only: {
    label: 'Somente novas',
    description: 'Vale para contas criadas após a publicação.',
  },
  scheduled: {
    label: 'Agendado',
    description: 'Entra em vigor na data e hora escolhidas.',
  },
  rule: {
    label: 'Por regra',
    description: 'Aplica critérios combinados para selecionar contas.',
  },
};

const workspaceStatusOptions: Array<{ value: PlanPolicyWorkspaceStatus; label: string }> = [
  { value: 'active', label: 'Ativa' },
  { value: 'provisioning', label: 'Em preparação' },
  { value: 'suspended', label: 'Suspensa' },
];

const setupModeOptions: Array<{ value: PlanPolicyWorkspaceSetupMode; label: string }> = [
  { value: 'self_service', label: 'Autoconfiguração' },
  { value: 'assisted', label: 'Configuração assistida' },
];

const subscriptionStatusOptions: Array<{ value: PlanPolicySubscriptionStatus; label: string }> = [
  { value: 'trialing', label: 'Em avaliação' },
  { value: 'active', label: 'Ativa' },
  { value: 'past_due', label: 'Pagamento pendente' },
  { value: 'cancelled', label: 'Cancelada' },
  { value: 'unpaid', label: 'Não paga' },
  { value: 'paused', label: 'Pausada' },
];

interface RuleForm {
  workspaceIds: string[];
  excludeWorkspaceIds: string[];
  workspaceStatuses: PlanPolicyWorkspaceStatus[];
  setupModes: PlanPolicyWorkspaceSetupMode[];
  subscriptionStatuses: PlanPolicySubscriptionStatus[];
  workspaceCreatedFrom: string;
  workspaceCreatedBefore: string;
  workspaceIsActive: 'any' | 'active' | 'inactive';
}

interface PreviewState {
  signature: string;
  data: PlanPolicyPreview;
}

interface PublicationIntent {
  fingerprint: string;
  key: string;
}

interface RolloutBuild {
  request: PlanPolicyRolloutRequest | null;
  error: string | null;
}

export interface PlatformPlanPoliciesPanelProps {
  canManage?: boolean;
  /** Publicação e restauração são reservadas ao platform_owner. */
  canPublish?: boolean;
}

function emptyRuleForm(): RuleForm {
  return {
    workspaceIds: [],
    excludeWorkspaceIds: [],
    workspaceStatuses: [],
    setupModes: [],
    subscriptionStatuses: [],
    workspaceCreatedFrom: '',
    workspaceCreatedBefore: '',
    workspaceIsActive: 'any',
  };
}

function cloneSnapshot(snapshot: PlanPolicySnapshot): PlanPolicySnapshot {
  return {
    ...snapshot,
    integrations: [...snapshot.integrations],
    highlights: [...snapshot.highlights],
    parameters: { ...snapshot.parameters },
    entitlements: Object.fromEntries(
      PLAN_POLICY_ENTITLEMENT_KEYS.map((key) => [key, { ...snapshot.entitlements[key] }]),
    ) as PlanPolicySnapshot['entitlements'],
  };
}

function snapshotSignature(snapshot: PlanPolicySnapshot | null): string {
  return snapshot ? JSON.stringify(snapshot) : '';
}

function formatDate(value?: string | null): string {
  if (!value) return 'Não informado';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Data inválida';
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(date);
}

function localDateTimeToIso(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function buildRollout(
  rolloutMode: PlanPolicyRolloutMode,
  scheduledAt: string,
  ruleForm: RuleForm,
): RolloutBuild {
  if (rolloutMode === 'scheduled') {
    const effectiveAt = localDateTimeToIso(scheduledAt);
    return effectiveAt
      ? { request: { rolloutMode, effectiveAt }, error: null }
      : { request: null, error: 'Informe uma data e hora válidas para o agendamento.' };
  }
  if (rolloutMode !== 'rule') return { request: { rolloutMode }, error: null };

  const workspaceIds = ruleForm.workspaceIds;
  const excludeWorkspaceIds = ruleForm.excludeWorkspaceIds;
  if (workspaceIds.some((item) => excludeWorkspaceIds.includes(item))) {
    return { request: null, error: 'A mesma conta não pode ser incluída e excluída.' };
  }

  const workspaceCreatedFrom = localDateTimeToIso(ruleForm.workspaceCreatedFrom);
  const workspaceCreatedBefore = localDateTimeToIso(ruleForm.workspaceCreatedBefore);
  if (ruleForm.workspaceCreatedFrom && !workspaceCreatedFrom) {
    return { request: null, error: 'A data inicial da regra é inválida.' };
  }
  if (ruleForm.workspaceCreatedBefore && !workspaceCreatedBefore) {
    return { request: null, error: 'A data final da regra é inválida.' };
  }
  if (
    workspaceCreatedFrom
    && workspaceCreatedBefore
    && new Date(workspaceCreatedFrom) >= new Date(workspaceCreatedBefore)
  ) {
    return { request: null, error: 'A data final precisa ser posterior à data inicial.' };
  }

  const rule: PlanPolicyRolloutRule = {
    workspaceIds,
    excludeWorkspaceIds,
    workspaceStatuses: ruleForm.workspaceStatuses,
    setupModes: ruleForm.setupModes,
    subscriptionStatuses: ruleForm.subscriptionStatuses,
    ...(workspaceCreatedFrom ? { workspaceCreatedFrom } : {}),
    ...(workspaceCreatedBefore ? { workspaceCreatedBefore } : {}),
    ...(ruleForm.workspaceIsActive === 'any'
      ? {}
      : { workspaceIsActive: ruleForm.workspaceIsActive === 'active' }),
  };
  const hasCriterion = Boolean(
    rule.workspaceIds.length
    || rule.excludeWorkspaceIds.length
    || rule.workspaceStatuses.length
    || rule.setupModes.length
    || rule.subscriptionStatuses.length
    || rule.workspaceCreatedFrom
    || rule.workspaceCreatedBefore
    || rule.workspaceIsActive !== undefined,
  );
  return hasCriterion
    ? { request: { rolloutMode, rule }, error: null }
    : { request: null, error: 'Selecione ao menos um critério para a regra.' };
}

function validateSnapshot(
  snapshot: PlanPolicySnapshot | null,
  catalog: PlanPolicyEntitlementCatalogItem[],
): string | null {
  if (!snapshot) return 'A política do plano ainda não foi carregada.';
  if (catalog.length !== PLAN_POLICY_ENTITLEMENT_KEYS.length) {
    return 'O catálogo de capacidades está incompleto.';
  }
  for (const item of catalog) {
    const setting = snapshot.entitlements[item.key];
    if (!setting) return `A capacidade ${entitlementCopy[item.key].label} não foi carregada.`;
    if (!setting.enabled && setting.limitQuantity !== null) {
      return `${entitlementCopy[item.key].label} está desativada e não pode manter quantidade.`;
    }
    if (
      setting.limitQuantity !== null
      && (!Number.isSafeInteger(setting.limitQuantity) || setting.limitQuantity < 0)
    ) {
      return `Informe uma quantidade inteira válida para ${entitlementCopy[item.key].label}.`;
    }
    if (setting.enabled && item.requiresFiniteLimit && setting.limitQuantity === null) {
      return `${entitlementCopy[item.key].label} exige uma quantidade finita.`;
    }
    if (!item.supportsLimit && setting.limitQuantity !== null) {
      return `${entitlementCopy[item.key].label} não aceita quantidade.`;
    }
  }
  return null;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function toggleArrayValue<T extends string>(values: T[], value: T, checked: boolean): T[] {
  return checked ? Array.from(new Set([...values, value])) : values.filter((item) => item !== value);
}

function parsePackageList(value: string): string[] {
  return Array.from(new Set(value
    .split(/[\n,;]+/)
    .map((item) => item.trim())
    .filter(Boolean)));
}

function formatPackageList(value: string[]): string {
  return value.join('\n');
}

function planStatus(summary: PlanPolicyPlanSummary | null): string {
  if (!summary) return '';
  if (summary.draftRevision) return `Rascunho ${summary.draftRevision}`;
  if (summary.latestVersion) return `Versão ${summary.latestVersion}`;
  return 'Catálogo inicial';
}

function workspaceStatusLabel(status: PlanPolicyWorkspaceStatus): string {
  return workspaceStatusOptions.find((item) => item.value === status)?.label ?? status;
}

function subscriptionStatusLabel(status: PlanPolicySubscriptionStatus): string {
  return subscriptionStatusOptions.find((item) => item.value === status)?.label ?? status;
}

function editorSourceLabel(source: PlanPolicyEditor['source']): string {
  if (source === 'draft') return 'Rascunho';
  if (source === 'published') return 'Última publicação';
  return 'Catálogo atual';
}

export function PlatformPlanPoliciesPanel({ canManage = true, canPublish = false }: PlatformPlanPoliciesPanelProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [selectedPlanId, setSelectedPlanId] = useState('');
  const [planPage, setPlanPage] = useState(0);
  const [versionPage, setVersionPage] = useState(0);
  const [snapshot, setSnapshot] = useState<PlanPolicySnapshot | null>(null);
  const [savedSignature, setSavedSignature] = useState('');
  const [draftEtag, setDraftEtag] = useState<string | null>(null);
  const [draftRevision, setDraftRevision] = useState<number | null>(null);
  const [editorSource, setEditorSource] = useState<PlanPolicyEditor['source'] | null>(null);
  const [rolloutMode, setRolloutMode] = useState<PlanPolicyRolloutMode>('existing_and_new');
  const [scheduledAt, setScheduledAt] = useState('');
  const [ruleForm, setRuleForm] = useState<RuleForm>(emptyRuleForm);
  const [sampleLimit, setSampleLimit] = useState(10);
  const [reason, setReason] = useState('');
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const loadedEditorKeyRef = useRef('');
  const publicationIntentRef = useRef<PublicationIntent | null>(null);

  const plansQuery = useQuery({
    queryKey: [...policyQueryKey, 'page', planPage],
    queryFn: ({ signal }) => listPlanPoliciesPage(PLAN_PAGE_SIZE, planPage * PLAN_PAGE_SIZE, signal),
    staleTime: 30_000,
  });
  const catalogQuery = useQuery({
    queryKey: [...policyQueryKey, 'entitlement-catalog'],
    queryFn: ({ signal }) => getPlanPolicyEntitlementCatalog(signal),
    staleTime: 5 * 60_000,
  });
  const editorQuery = useQuery({
    queryKey: [...policyQueryKey, selectedPlanId, 'editor'],
    queryFn: ({ signal }) => getPlanPolicyEditor(selectedPlanId, signal),
    enabled: Boolean(selectedPlanId),
    retry: false,
  });
  const versionsQuery = useQuery({
    queryKey: [...policyQueryKey, selectedPlanId, 'versions', versionPage],
    queryFn: ({ signal }) => listPlanPolicyVersionsPage(
      selectedPlanId,
      VERSION_PAGE_SIZE,
      versionPage * VERSION_PAGE_SIZE,
      signal,
    ),
    enabled: Boolean(selectedPlanId),
    retry: false,
  });

  const plans = plansQuery.data?.items ?? EMPTY_PLAN_SUMMARIES;
  const catalogByKey = useMemo(
    () => new Map((catalogQuery.data ?? []).map((item) => [item.key, item])),
    [catalogQuery.data],
  );
  const orderedCatalog = useMemo(
    () => PLAN_POLICY_ENTITLEMENT_KEYS
      .map((key) => catalogByKey.get(key))
      .filter((item): item is PlanPolicyEntitlementCatalogItem => Boolean(item)),
    [catalogByKey],
  );
  const selectedPlan = plans.find((plan) => plan.planId === selectedPlanId) ?? null;

  useEffect(() => {
    if (!selectedPlanId && plans.length) setSelectedPlanId(plans[0].planId);
    if (selectedPlanId && !plans.some((plan) => plan.planId === selectedPlanId)) {
      setSelectedPlanId(plans[0]?.planId ?? '');
    }
  }, [plans, selectedPlanId]);

  useEffect(() => {
    setVersionPage(0);
    setSnapshot(null);
    setSavedSignature('');
    setDraftEtag(null);
    setDraftRevision(null);
    setEditorSource(null);
    setRolloutMode('existing_and_new');
    setScheduledAt('');
    setRuleForm(emptyRuleForm());
    setSampleLimit(10);
    setReason('');
    setPreview(null);
    loadedEditorKeyRef.current = '';
    publicationIntentRef.current = null;
  }, [selectedPlanId]);

  useEffect(() => {
    const editor = editorQuery.data;
    if (!editor || editor.planId !== selectedPlanId) return;
    const editorKey = `${editor.planId}:${editor.source}:${editor.etag}`;
    if (loadedEditorKeyRef.current === editorKey) return;
    loadedEditorKeyRef.current = editorKey;
    const nextSnapshot = cloneSnapshot(editor.snapshot);
    setSnapshot(nextSnapshot);
    setSavedSignature(snapshotSignature(nextSnapshot));
    setDraftEtag(editor.source === 'draft' ? editor.etag : null);
    setDraftRevision(editor.draftRevision);
    setEditorSource(editor.source);
  }, [editorQuery.data, selectedPlanId]);

  const currentSignature = snapshotSignature(snapshot);
  const isDirty = Boolean(snapshot && currentSignature !== savedSignature);
  const snapshotError = validateSnapshot(snapshot, orderedCatalog);
  const rolloutBuild = useMemo(
    () => buildRollout(rolloutMode, scheduledAt, ruleForm),
    [rolloutMode, ruleForm, scheduledAt],
  );
  const rolloutSignature = rolloutBuild.request && selectedPlanId
    ? JSON.stringify({ planId: selectedPlanId, ...rolloutBuild.request, sampleLimit })
    : '';
  const previewIsCurrent = Boolean(preview && preview.signature === rolloutSignature);
  const normalizedReason = reason.trim();

  const saveMutation = useMutation({
    mutationFn: ({ planId, value, ifMatch }: {
      planId: string;
      value: PlanPolicySnapshot;
      ifMatch?: string;
    }) => savePlanPolicyDraft(planId, value, ifMatch),
    onSuccess: (draft) => {
      const nextSnapshot = cloneSnapshot(draft.snapshot);
      setSnapshot(nextSnapshot);
      setSavedSignature(snapshotSignature(nextSnapshot));
      setDraftEtag(draft.etag);
      setDraftRevision(draft.revision);
      setEditorSource('draft');
      queryClient.setQueryData<PlanPolicyEditor>(
        [...policyQueryKey, draft.planId, 'editor'],
        (current) => current ? {
          ...current,
          source: 'draft',
          etag: draft.etag,
          draftRevision: draft.revision,
          snapshot: draft.snapshot,
        } : current,
      );
      void queryClient.invalidateQueries({ queryKey: policyQueryKey });
      toast({
        title: 'Rascunho salvo.',
        description: `Revisão ${draft.revision} pronta para prévia e publicação.`,
      });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Não foi possível salvar o rascunho',
      description: errorMessage(error, 'Revise os dados e tente novamente.'),
    }),
  });

  const previewMutation = useMutation({
    mutationFn: ({ planId, payload, signature }: {
      planId: string;
      payload: PlanPolicyPreviewRequest;
      signature: string;
    }) => previewPlanPolicyRollout(planId, payload).then((data) => ({ data, signature })),
    onSuccess: (result) => setPreview(result),
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Não foi possível gerar a prévia',
      description: errorMessage(error, 'Revise o alcance e tente novamente.'),
    }),
  });

  const publishMutation = useMutation({
    mutationFn: ({ planId, payload, idempotencyKey }: {
      planId: string;
      payload: PlanPolicyPublishRequest;
      idempotencyKey: string;
    }) => publishPlanPolicy(planId, payload, idempotencyKey),
    onSuccess: (publication: PlanPolicyPublication) => {
      publicationIntentRef.current = null;
      setReason('');
      setPreview(null);
      void queryClient.invalidateQueries({ queryKey: policyQueryKey });
      toast({
        title: publication.idempotentReplay
          ? `Versão ${publication.policy.version} confirmada.`
          : `Versão ${publication.policy.version} publicada.`,
        description: `${publication.affectedCount} conta(s) selecionada(s) pelo alcance.`,
      });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Publicação não concluída',
      description: errorMessage(error, 'A mesma tentativa poderá ser repetida com segurança.'),
    }),
  });

  const updateEntitlement = (
    key: PlanPolicyEntitlementKey,
    update: Partial<PlanPolicySnapshot['entitlements'][PlanPolicyEntitlementKey]>,
  ) => {
    setSnapshot((current) => {
      if (!current) return current;
      return {
        ...current,
        entitlements: {
          ...current.entitlements,
          [key]: { ...current.entitlements[key], ...update },
        },
      };
    });
  };

  const saveDraft = () => {
    if (!snapshot || snapshotError || !selectedPlanId || !canManage) return;
    saveMutation.mutate({
      planId: selectedPlanId,
      value: cloneSnapshot(snapshot),
      ifMatch: editorSource === 'draft' ? draftEtag ?? undefined : undefined,
    });
  };

  const generatePreview = () => {
    if (!rolloutBuild.request || !selectedPlanId) return;
    previewMutation.mutate({
      planId: selectedPlanId,
      payload: { ...rolloutBuild.request, sampleLimit },
      signature: rolloutSignature,
    });
  };

  const publishCurrentDraft = () => {
    if (
      !selectedPlanId
      || !draftEtag
      || !rolloutBuild.request
      || !previewIsCurrent
      || normalizedReason.length < 3
      || isDirty
      || !canPublish
    ) return;
    const payload: PlanPolicyPublishRequest = {
      ...rolloutBuild.request,
      draftEtag,
      reason: normalizedReason,
    };
    const fingerprint = JSON.stringify({ planId: selectedPlanId, payload });
    if (publicationIntentRef.current?.fingerprint !== fingerprint) {
      publicationIntentRef.current = {
        fingerprint,
        key: createPlanPolicyIdempotencyKey(),
      };
    }
    publishMutation.mutate({
      planId: selectedPlanId,
      payload,
      idempotencyKey: publicationIntentRef.current.key,
    });
  };

  const refreshSelectedPlan = () => {
    void queryClient.invalidateQueries({ queryKey: policyQueryKey });
  };

  const prepareVersionRestoration = (version: PlanPolicyVersion) => {
    if (!canPublish) return;
    const restoredSnapshot = cloneSnapshot(version.snapshot);
    setSnapshot(restoredSnapshot);
    setPreview(null);
    setReason(`Restaurar a política da versão ${version.version} como nova versão.`);
    publicationIntentRef.current = null;
    requestAnimationFrame(() => {
      document.getElementById('plan-policy-package-heading')?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      });
    });
  };

  const publicationReady = Boolean(
    canPublish
    && draftEtag
    && !isDirty
    && !snapshotError
    && rolloutBuild.request
    && previewIsCurrent
    && normalizedReason.length >= 3
    && !publishMutation.isPending,
  );

  const publicationBlocker = !canPublish
    ? 'Somente o Super Admin publica ou restaura versões.'
    : !draftEtag
      ? 'Salve um rascunho antes de publicar.'
      : isDirty
        ? 'Salve as alterações do rascunho antes de publicar.'
        : rolloutBuild.error
          ? null
          : !previewIsCurrent
            ? 'Gere uma prévia atualizada antes de publicar.'
            : normalizedReason.length < 3
              ? 'Informe um motivo com pelo menos 3 caracteres.'
              : null;

  if (plansQuery.isLoading || catalogQuery.isLoading) {
    return (
      <div className="space-y-4" aria-label="Carregando políticas dos planos" role="status">
        <Skeleton className="h-10 w-full max-w-md" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (plansQuery.isError || catalogQuery.isError) {
    return (
      <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4" role="alert">
        <p className="text-sm font-medium text-destructive">Não foi possível carregar as políticas dos planos.</p>
        <Button className="mt-3" size="sm" variant="outline" onClick={refreshSelectedPlan}>
          <RefreshCw className="h-4 w-4" />Tentar novamente
        </Button>
      </div>
    );
  }

  if (!plans.length) {
    return (
      <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
        Nenhum plano disponível para edição.
      </div>
    );
  }

  return (
    <div className="space-y-7" aria-busy={saveMutation.isPending || publishMutation.isPending}>
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            <Layers3 className="h-4 w-4 text-primary" aria-hidden="true" />
            Políticas dos planos
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Capacidades, alcance e versões publicadas.
          </p>
        </div>
        <Button
          type="button"
          size="icon"
          variant="outline"
          aria-label="Atualizar políticas"
          title="Atualizar políticas"
          onClick={refreshSelectedPlan}
        >
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
        </Button>
      </header>

      <section aria-labelledby="plan-policy-plan-heading" className="border-b pb-6">
        <div className="grid gap-3 sm:grid-cols-[minmax(0,24rem)_1fr] sm:items-end">
          <div className="space-y-1.5">
            <Label id="plan-policy-plan-heading" htmlFor="plan-policy-plan">Plano</Label>
            <Select
              value={selectedPlanId}
              disabled={saveMutation.isPending || previewMutation.isPending || publishMutation.isPending}
              onValueChange={setSelectedPlanId}
            >
              <SelectTrigger id="plan-policy-plan" aria-label="Plano da política">
                <SelectValue placeholder="Selecione um plano" />
              </SelectTrigger>
              <SelectContent>
                {plans.map((plan) => (
                  <SelectItem key={plan.planId} value={plan.planId}>{plan.planName}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {selectedPlan && (
            <div className="flex flex-wrap items-center gap-2 pb-1 text-sm text-muted-foreground">
              <Badge variant="outline">{selectedPlan.planTier}</Badge>
              <span>{planStatus(selectedPlan)}</span>
              {selectedPlan.latestEffectiveAt && <span>vigente desde {formatDate(selectedPlan.latestEffectiveAt)}</span>}
            </div>
          )}
        </div>
        {plansQuery.data && plansQuery.data.total > 0 && (
          <div className="mt-3 flex items-center justify-between gap-3">
            <span className="text-xs text-muted-foreground">
              {plansQuery.data.offset + 1}–{Math.min(plansQuery.data.offset + plans.length, plansQuery.data.total)} de {plansQuery.data.total} planos
            </span>
            <span className="flex gap-2">
              <Button size="sm" variant="outline" disabled={planPage === 0 || plansQuery.isFetching} onClick={() => setPlanPage((value) => Math.max(0, value - 1))}>Anterior</Button>
              <Button size="sm" variant="outline" disabled={!plansQuery.data.hasMore || plansQuery.isFetching} onClick={() => setPlanPage((value) => value + 1)}>Próxima</Button>
            </span>
          </div>
        )}
      </section>

      {editorQuery.isLoading && (
        <div className="space-y-3" role="status" aria-label="Carregando editor do plano">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-80 w-full" />
        </div>
      )}

      {editorQuery.isError && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4" role="alert">
          <p className="text-sm text-destructive">
            {errorMessage(editorQuery.error, 'Não foi possível carregar o editor deste plano.')}
          </p>
          <Button className="mt-3" size="sm" variant="outline" onClick={() => editorQuery.refetch()}>
            <RefreshCw className="h-4 w-4" />Tentar novamente
          </Button>
        </div>
      )}

      {snapshot && editorSource && (
        <>
          <section aria-labelledby="plan-policy-package-heading" className="space-y-4 border-b pb-7">
            <div>
              <h3 id="plan-policy-package-heading" className="text-sm font-semibold">Identidade do pacote</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                Defina como este plano aparece para a conta e quais integrações e benefícios ficam incluídos.
              </p>
            </div>
            <fieldset disabled={!canManage || saveMutation.isPending} className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="plan-policy-package-name">Nome do pacote</Label>
                <Input
                  id="plan-policy-package-name"
                  value={snapshot.name}
                  onChange={(event) => setSnapshot((current) => current ? ({ ...current, name: event.target.value }) : current)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="plan-policy-support-tier">Nível de suporte</Label>
                <Input
                  id="plan-policy-support-tier"
                  value={snapshot.supportTier}
                  onChange={(event) => setSnapshot((current) => current ? ({ ...current, supportTier: event.target.value }) : current)}
                />
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="plan-policy-package-description">Descrição do pacote</Label>
                <Textarea
                  id="plan-policy-package-description"
                  rows={3}
                  value={snapshot.description ?? ''}
                  onChange={(event) => setSnapshot((current) => current ? ({ ...current, description: event.target.value || null }) : current)}
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-3 sm:col-span-2">
                <label htmlFor="plan-policy-is-active" className="flex items-center gap-2 text-sm">
                  <Checkbox id="plan-policy-is-active" checked={snapshot.isActive} onCheckedChange={(checked) => setSnapshot((current) => current ? ({ ...current, isActive: checked === true }) : current)} />
                  Pacote ativo
                </label>
                <label htmlFor="plan-policy-is-public" className="flex items-center gap-2 text-sm">
                  <Checkbox id="plan-policy-is-public" checked={snapshot.isPublic} onCheckedChange={(checked) => setSnapshot((current) => current ? ({ ...current, isPublic: checked === true }) : current)} />
                  Visível no catálogo
                </label>
                <label htmlFor="plan-policy-recommended" className="flex items-center gap-2 text-sm">
                  <Checkbox id="plan-policy-recommended" checked={snapshot.recommended} onCheckedChange={(checked) => setSnapshot((current) => current ? ({ ...current, recommended: checked === true }) : current)} />
                  Recomendado
                </label>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="plan-policy-monthly-price">Preço mensal (centavos)</Label>
                <Input id="plan-policy-monthly-price" type="number" min={0} step={1} inputMode="numeric" value={snapshot.priceMonthlyCents} onChange={(event) => setSnapshot((current) => current ? ({ ...current, priceMonthlyCents: Number(event.target.value) }) : current)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="plan-policy-annual-price">Preço anual (centavos)</Label>
                <Input id="plan-policy-annual-price" type="number" min={0} step={1} inputMode="numeric" value={snapshot.priceAnnuallyCents} onChange={(event) => setSnapshot((current) => current ? ({ ...current, priceAnnuallyCents: Number(event.target.value) }) : current)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="plan-policy-sort-order">Ordem no catálogo</Label>
                <Input id="plan-policy-sort-order" type="number" min={0} step={1} inputMode="numeric" value={snapshot.sortOrder} onChange={(event) => setSnapshot((current) => current ? ({ ...current, sortOrder: Number(event.target.value) }) : current)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="plan-policy-max-matters">Casos ativos</Label>
                <Input id="plan-policy-max-matters" type="number" min={0} step={1} inputMode="numeric" placeholder="Sem limite" value={snapshot.parameters.maxMatters ?? ''} onChange={(event) => setSnapshot((current) => current ? ({ ...current, parameters: { ...current.parameters, maxMatters: event.target.value === '' ? null : Number(event.target.value) } }) : current)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="plan-policy-max-workspaces">Espaços de trabalho</Label>
                <Input id="plan-policy-max-workspaces" type="number" min={0} step={1} inputMode="numeric" placeholder="Sem limite" value={snapshot.parameters.maxWorkspaces ?? ''} onChange={(event) => setSnapshot((current) => current ? ({ ...current, parameters: { ...current.parameters, maxWorkspaces: event.target.value === '' ? null : Number(event.target.value) } }) : current)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="plan-policy-max-files-conversation">Arquivos por conversa</Label>
                <Input id="plan-policy-max-files-conversation" type="number" min={0} step={1} inputMode="numeric" placeholder="Sem limite" value={snapshot.parameters.maxFilesPerConversation ?? ''} onChange={(event) => setSnapshot((current) => current ? ({ ...current, parameters: { ...current.parameters, maxFilesPerConversation: event.target.value === '' ? null : Number(event.target.value) } }) : current)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="plan-policy-max-recipients">Destinatários operacionais</Label>
                <Input id="plan-policy-max-recipients" type="number" min={0} step={1} inputMode="numeric" placeholder="Sem limite" value={snapshot.parameters.maxOperationalRecipients ?? ''} onChange={(event) => setSnapshot((current) => current ? ({ ...current, parameters: { ...current.parameters, maxOperationalRecipients: event.target.value === '' ? null : Number(event.target.value) } }) : current)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="plan-policy-max-campaign-recipients">Destinatários de campanhas por mês</Label>
                <Input id="plan-policy-max-campaign-recipients" type="number" min={0} step={1} inputMode="numeric" placeholder="Sem limite" value={snapshot.parameters.maxCampaignRecipientsPerMonth ?? ''} onChange={(event) => setSnapshot((current) => current ? ({ ...current, parameters: { ...current.parameters, maxCampaignRecipientsPerMonth: event.target.value === '' ? null : Number(event.target.value) } }) : current)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="plan-policy-integrations">Integrações incluídas</Label>
                <Textarea id="plan-policy-integrations" rows={3} placeholder="Uma por linha" value={formatPackageList(snapshot.integrations)} onChange={(event) => setSnapshot((current) => current ? ({ ...current, integrations: parsePackageList(event.target.value) }) : current)} />
                <p className="text-xs text-muted-foreground">Uma integração por linha, vírgula ou ponto e vírgula.</p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="plan-policy-highlights">Benefícios exibidos</Label>
                <Textarea id="plan-policy-highlights" rows={3} placeholder="Um benefício por linha" value={formatPackageList(snapshot.highlights)} onChange={(event) => setSnapshot((current) => current ? ({ ...current, highlights: parsePackageList(event.target.value) }) : current)} />
                <p className="text-xs text-muted-foreground">Use frases curtas para o catálogo e onboarding.</p>
              </div>
            </fieldset>
          </section>

          <section aria-labelledby="plan-policy-capabilities-heading" className="space-y-4 border-b pb-7">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h3 id="plan-policy-capabilities-heading" className="text-sm font-semibold">Capacidades</h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  Base carregada: {editorSourceLabel(editorSource)}{draftRevision ? ` ${draftRevision}` : ''}.
                </p>
              </div>
              <Badge variant={isDirty ? 'secondary' : 'outline'}>
                {isDirty ? 'Alterações não salvas' : 'Sem alterações pendentes'}
              </Badge>
            </div>

            <fieldset disabled={!canManage || saveMutation.isPending} className="overflow-hidden rounded-md border">
              <legend className="sr-only">Capacidades incluídas no plano</legend>
              <div className="hidden grid-cols-[minmax(0,1fr)_minmax(9rem,0.45fr)_minmax(10rem,0.45fr)] gap-4 border-b bg-muted/30 px-3 py-2 text-xs font-medium text-muted-foreground sm:grid">
                <span>Capacidade</span>
                <span>Quantidade</span>
                <span className="flex items-center gap-1">
                  Bloquear excedentes
                  <FieldHelp
                    label="Bloqueio no limite"
                    description="Impede novo consumo quando a quantidade configurada é atingida."
                    whereToFind="Ao lado das capacidades que aceitam quantidade."
                  />
                </span>
              </div>
              <div className="divide-y">
                {orderedCatalog.map((item) => {
                  const setting = snapshot.entitlements[item.key];
                  const copy = entitlementCopy[item.key];
                  const enabledId = `plan-policy-${item.key}-enabled`;
                  const quantityId = `plan-policy-${item.key}-quantity`;
                  const hardLimitId = `plan-policy-${item.key}-hard-limit`;
                  return (
                    <div
                      key={item.key}
                      className="grid min-h-16 gap-3 px-3 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(9rem,0.45fr)_minmax(10rem,0.45fr)] sm:items-center sm:gap-4"
                    >
                      <div className="flex min-w-0 items-center gap-2.5">
                        <Checkbox
                          id={enabledId}
                          checked={setting.enabled}
                          onCheckedChange={(checked) => updateEntitlement(item.key, {
                            enabled: checked === true,
                            limitQuantity: checked === true
                              ? (item.requiresFiniteLimit ? setting.limitQuantity ?? 0 : setting.limitQuantity)
                              : null,
                          })}
                        />
                        <Label htmlFor={enabledId} className="min-w-0 font-medium leading-5">{copy.label}</Label>
                        {copy.help && (
                          <FieldHelp
                            label={copy.label}
                            description={copy.help.description}
                            whereToFind={copy.help.whereToFind}
                          />
                        )}
                      </div>

                      {item.supportsLimit ? (
                        <div className="space-y-1">
                          <Label htmlFor={quantityId} className="text-xs text-muted-foreground sm:sr-only">
                            Quantidade de {copy.label}
                          </Label>
                          <Input
                            id={quantityId}
                            type="number"
                            inputMode="numeric"
                            min={0}
                            step={1}
                            disabled={!setting.enabled}
                            value={setting.limitQuantity ?? ''}
                            placeholder={item.requiresFiniteLimit ? 'Obrigatória' : 'Sem limite'}
                            aria-describedby={copy.unit ? `${quantityId}-unit` : undefined}
                            onChange={(event) => {
                              const value = event.target.value;
                              updateEntitlement(item.key, {
                                limitQuantity: value === '' ? null : Number(value),
                              });
                            }}
                          />
                          {copy.unit && <p id={`${quantityId}-unit`} className="text-xs text-muted-foreground">{copy.unit}</p>}
                        </div>
                      ) : (
                        <span className="hidden text-xs text-muted-foreground sm:block">Não se aplica</span>
                      )}

                      {item.supportsLimit ? (
                        <label htmlFor={hardLimitId} className="flex items-center gap-2 text-sm">
                          <Checkbox
                            id={hardLimitId}
                            checked={setting.hardLimit}
                            disabled={!setting.enabled}
                            onCheckedChange={(checked) => updateEntitlement(item.key, { hardLimit: checked === true })}
                          />
                          <span className="sm:sr-only">Bloquear excedentes de {copy.label}</span>
                          <span className="sm:hidden">Bloquear excedentes</span>
                        </label>
                      ) : (
                        <span className="hidden text-xs text-muted-foreground sm:block">Não se aplica</span>
                      )}
                    </div>
                  );
                })}
              </div>
            </fieldset>

            {snapshotError && <p className="text-sm text-destructive" role="alert">{snapshotError}</p>}
            {saveMutation.isError && (
              <p className="text-sm text-destructive" role="alert">
                {errorMessage(saveMutation.error, 'Não foi possível salvar o rascunho.')}
              </p>
            )}
            <div className="flex justify-end">
              <Button
                type="button"
                onClick={saveDraft}
                disabled={
                  !canManage
                  || Boolean(snapshotError)
                  || saveMutation.isPending
                  || (!isDirty && editorSource === 'draft')
                }
              >
                {saveMutation.isPending
                  ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  : <Save className="h-4 w-4" aria-hidden="true" />}
                Salvar rascunho
              </Button>
            </div>
          </section>

          <section aria-labelledby="plan-policy-rollout-heading" className="space-y-5 border-b pb-7">
            <div className="flex items-center gap-1">
              <h3 id="plan-policy-rollout-heading" className="text-sm font-semibold">Alcance da publicação</h3>
              <FieldHelp
                label="Rollout"
                description="Forma de distribuir uma nova versão da política entre as contas de um plano."
                whereToFind="Nas opções de alcance desta publicação."
              />
            </div>

            <RadioGroup
              value={rolloutMode}
              onValueChange={(value) => setRolloutMode(value as PlanPolicyRolloutMode)}
              className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4"
              aria-label="Alcance da publicação"
            >
              {(Object.entries(rolloutCopy) as Array<[PlanPolicyRolloutMode, typeof rolloutCopy[PlanPolicyRolloutMode]]>)
                .map(([value, copy]) => {
                  const id = `plan-policy-rollout-${value}`;
                  return (
                    <label
                      key={value}
                      htmlFor={id}
                      className="flex min-h-20 cursor-pointer items-start gap-2.5 rounded-md border p-3 has-[[data-state=checked]]:border-primary has-[[data-state=checked]]:bg-primary/5"
                    >
                      <RadioGroupItem id={id} value={value} className="mt-0.5" />
                      <span>
                        <span className="block text-sm font-medium">{copy.label}</span>
                        <span className="mt-1 block text-xs leading-5 text-muted-foreground">{copy.description}</span>
                      </span>
                    </label>
                  );
                })}
            </RadioGroup>

            {rolloutMode === 'scheduled' && (
              <div className="max-w-sm space-y-1.5">
                <Label htmlFor="plan-policy-scheduled-at" className="flex items-center gap-2">
                  <CalendarClock className="h-4 w-4" aria-hidden="true" />Data e hora de vigência
                </Label>
                <Input
                  id="plan-policy-scheduled-at"
                  type="datetime-local"
                  value={scheduledAt}
                  onChange={(event) => setScheduledAt(event.target.value)}
                />
              </div>
            )}

            {rolloutMode === 'rule' && (
              <div className="space-y-5 rounded-md border p-4">
                <div className="flex items-center gap-1">
                  <h4 className="text-sm font-medium">Regra segmentada</h4>
                  <FieldHelp
                    label="Regra segmentada"
                    description="Conjunto de critérios combinados; a conta precisa atender a todos os grupos preenchidos."
                    whereToFind="Nos status, datas e identificadores abaixo."
                  />
                </div>

                <div className="grid gap-5 lg:grid-cols-3">
                  <fieldset>
                    <legend className="mb-2 text-xs font-medium text-muted-foreground">Status da conta</legend>
                    <div className="space-y-2">
                      {workspaceStatusOptions.map((option) => {
                        const id = `rule-workspace-status-${option.value}`;
                        return (
                          <label key={option.value} htmlFor={id} className="flex items-center gap-2 text-sm">
                            <Checkbox
                              id={id}
                              checked={ruleForm.workspaceStatuses.includes(option.value)}
                              onCheckedChange={(checked) => setRuleForm((current) => ({
                                ...current,
                                workspaceStatuses: toggleArrayValue(
                                  current.workspaceStatuses,
                                  option.value,
                                  checked === true,
                                ),
                              }))}
                            />
                            {option.label}
                          </label>
                        );
                      })}
                    </div>
                  </fieldset>

                  <fieldset>
                    <legend className="mb-2 text-xs font-medium text-muted-foreground">Configuração inicial</legend>
                    <div className="space-y-2">
                      {setupModeOptions.map((option) => {
                        const id = `rule-setup-mode-${option.value}`;
                        return (
                          <label key={option.value} htmlFor={id} className="flex items-center gap-2 text-sm">
                            <Checkbox
                              id={id}
                              checked={ruleForm.setupModes.includes(option.value)}
                              onCheckedChange={(checked) => setRuleForm((current) => ({
                                ...current,
                                setupModes: toggleArrayValue(current.setupModes, option.value, checked === true),
                              }))}
                            />
                            {option.label}
                          </label>
                        );
                      })}
                    </div>
                  </fieldset>

                  <fieldset>
                    <legend className="mb-2 text-xs font-medium text-muted-foreground">Situação da assinatura</legend>
                    <div className="grid grid-cols-2 gap-2 lg:grid-cols-1">
                      {subscriptionStatusOptions.map((option) => {
                        const id = `rule-subscription-status-${option.value}`;
                        return (
                          <label key={option.value} htmlFor={id} className="flex items-center gap-2 text-sm">
                            <Checkbox
                              id={id}
                              checked={ruleForm.subscriptionStatuses.includes(option.value)}
                              onCheckedChange={(checked) => setRuleForm((current) => ({
                                ...current,
                                subscriptionStatuses: toggleArrayValue(
                                  current.subscriptionStatuses,
                                  option.value,
                                  checked === true,
                                ),
                              }))}
                            />
                            {option.label}
                          </label>
                        );
                      })}
                    </div>
                  </fieldset>
                </div>

                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="rule-workspace-active">Conta habilitada</Label>
                    <Select
                      value={ruleForm.workspaceIsActive}
                      onValueChange={(value) => setRuleForm((current) => ({
                        ...current,
                        workspaceIsActive: value as RuleForm['workspaceIsActive'],
                      }))}
                    >
                      <SelectTrigger id="rule-workspace-active"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="any">Qualquer situação</SelectItem>
                        <SelectItem value="active">Sim</SelectItem>
                        <SelectItem value="inactive">Não</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="rule-created-from">Criada a partir de</Label>
                    <Input
                      id="rule-created-from"
                      type="datetime-local"
                      value={ruleForm.workspaceCreatedFrom}
                      onChange={(event) => setRuleForm((current) => ({
                        ...current,
                        workspaceCreatedFrom: event.target.value,
                      }))}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="rule-created-before">Criada antes de</Label>
                    <Input
                      id="rule-created-before"
                      type="datetime-local"
                      value={ruleForm.workspaceCreatedBefore}
                      onChange={(event) => setRuleForm((current) => ({
                        ...current,
                        workspaceCreatedBefore: event.target.value,
                      }))}
                    />
                  </div>
                </div>

                <details className="rounded-md border px-3 py-2">
                  <summary className="cursor-pointer text-sm font-medium">Contas específicas</summary>
                  <div className="mt-3 grid gap-4 sm:grid-cols-2">
                    <div className="space-y-1.5">
                      <Label>Incluir contas</Label>
                      <PlatformEntityMultiSelect entityType="accounts" purpose="plan_policy_include" value={ruleForm.workspaceIds} excludedIds={ruleForm.excludeWorkspaceIds} onChange={(value) => setRuleForm((current) => ({ ...current, workspaceIds: value }))} label="Selecionar contas incluídas" placeholder="Pesquisar contas" />
                    </div>
                    <div className="space-y-1.5">
                      <Label>Excluir contas</Label>
                      <PlatformEntityMultiSelect entityType="accounts" purpose="plan_policy_exclude" value={ruleForm.excludeWorkspaceIds} excludedIds={ruleForm.workspaceIds} onChange={(value) => setRuleForm((current) => ({ ...current, excludeWorkspaceIds: value }))} label="Selecionar contas excluídas" placeholder="Pesquisar contas" />
                    </div>
                  </div>
                </details>
              </div>
            )}

            {rolloutBuild.error && <p className="text-sm text-destructive" role="alert">{rolloutBuild.error}</p>}

            <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
              <div className="w-full max-w-48 space-y-1.5">
                <Label htmlFor="plan-policy-sample-limit">Tamanho da amostra</Label>
                <Input
                  id="plan-policy-sample-limit"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={100}
                  step={1}
                  value={sampleLimit}
                  onChange={(event) => setSampleLimit(
                    Math.min(100, Math.max(1, Number(event.target.value) || 1)),
                  )}
                />
              </div>
              <Button
                type="button"
                variant="outline"
                onClick={generatePreview}
                disabled={!rolloutBuild.request || previewMutation.isPending}
              >
                {previewMutation.isPending
                  ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  : <Eye className="h-4 w-4" aria-hidden="true" />}
                Gerar prévia
              </Button>
            </div>

            {previewMutation.isError && (
              <p className="text-sm text-destructive" role="alert">
                {errorMessage(previewMutation.error, 'Não foi possível gerar a prévia.')}
              </p>
            )}

            {preview && (
              <div className={`rounded-md border ${previewIsCurrent ? '' : 'border-amber-500/50 bg-amber-500/5'}`}>
                <div className="flex flex-col gap-2 border-b px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex items-center gap-2">
                    <UsersRound className="h-4 w-4 text-primary" aria-hidden="true" />
                    <p className="text-sm font-medium">
                      {preview.data.affectedCount} conta(s) no alcance
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="outline">Amostra: {preview.data.accounts.length}</Badge>
                    <Badge variant={preview.data.appliesImmediately ? 'secondary' : 'outline'}>
                      {preview.data.appliesImmediately ? 'Aplicação imediata' : `Vigência em ${formatDate(preview.data.effectiveAt)}`}
                    </Badge>
                  </div>
                </div>
                {!previewIsCurrent && (
                  <p className="border-b px-4 py-2 text-xs text-amber-700" role="status">
                    O alcance mudou. Gere outra prévia antes de publicar.
                  </p>
                )}
                {preview.data.accounts.length ? (
                  <ul className="divide-y" aria-label="Amostra de contas afetadas">
                    {preview.data.accounts.map((account) => (
                      <li key={account.workspaceId} className="grid gap-2 px-4 py-3 text-sm sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:items-center">
                        <div className="min-w-0">
                          <p className="truncate font-medium">{account.name}</p>
                          <p className="truncate text-xs text-muted-foreground">{account.slug}</p>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          <Badge variant="outline">{workspaceStatusLabel(account.workspaceStatus)}</Badge>
                          <Badge variant="outline">Assinatura {subscriptionStatusLabel(account.subscriptionStatus).toLowerCase()}</Badge>
                        </div>
                        <span className="text-xs text-muted-foreground">
                          {account.currentPolicyVersion ? `Versão atual ${account.currentPolicyVersion}` : 'Sem versão atribuída'}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="px-4 py-5 text-sm text-muted-foreground">Nenhuma conta para exibir na amostra.</p>
                )}
              </div>
            )}
          </section>

          <section aria-labelledby="plan-policy-publication-heading" className="space-y-4 border-b pb-7">
            <div className="flex items-center gap-1">
              <h3 id="plan-policy-publication-heading" className="text-sm font-semibold">Publicação</h3>
              <FieldHelp
                label="Publicação idempotente"
                description="Uma repetição da mesma tentativa confirma a publicação já criada, sem duplicar versões."
                whereToFind="Na confirmação final de publicação."
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="plan-policy-publication-reason">Motivo da publicação</Label>
              <Textarea
                id="plan-policy-publication-reason"
                value={reason}
                minLength={3}
                maxLength={500}
                required
                disabled={!canPublish || publishMutation.isPending}
                aria-describedby="plan-policy-publication-reason-count"
                placeholder="Ex.: ampliar a franquia de automações do plano"
                onChange={(event) => setReason(event.target.value)}
              />
              <p id="plan-policy-publication-reason-count" className="text-right text-xs text-muted-foreground">
                {reason.length}/500
              </p>
            </div>

            {publishMutation.isError && (
              <p className="text-sm text-destructive" role="alert">
                {errorMessage(publishMutation.error, 'Não foi possível publicar a versão.')}
              </p>
            )}
            {publicationBlocker && <p className="text-sm text-muted-foreground" role="status">{publicationBlocker}</p>}

            <div className="flex justify-end">
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button type="button" disabled={!publicationReady}>
                    {publishMutation.isPending
                      ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                      : <Rocket className="h-4 w-4" aria-hidden="true" />}
                    Publicar versão
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Confirmar publicação</AlertDialogTitle>
                    <AlertDialogDescription>
                      Esta ação cria uma versão imutável da política. O rascunho continuará disponível para edições futuras.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 rounded-md border p-3 text-sm">
                    <dt className="text-muted-foreground">Plano</dt>
                    <dd className="font-medium">{selectedPlan?.planName}</dd>
                    <dt className="text-muted-foreground">Alcance</dt>
                    <dd className="font-medium">{rolloutCopy[rolloutMode].label}</dd>
                    <dt className="text-muted-foreground">Contas</dt>
                    <dd className="font-medium">{preview?.data.affectedCount ?? 0}</dd>
                    <dt className="text-muted-foreground">Motivo</dt>
                    <dd className="break-words font-medium">{normalizedReason}</dd>
                  </dl>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancelar</AlertDialogCancel>
                    <AlertDialogAction onClick={publishCurrentDraft}>Confirmar publicação</AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          </section>

          <section aria-labelledby="plan-policy-history-heading" className="space-y-4">
            <div className="flex items-center gap-1">
              <History className="h-4 w-4 text-primary" aria-hidden="true" />
              <h3 id="plan-policy-history-heading" className="text-sm font-semibold">Histórico</h3>
              <FieldHelp
                label="Versão imutável"
                description="Registro publicado que não pode ser alterado; uma mudança posterior cria outra versão."
                whereToFind="Na lista cronológica abaixo."
              />
            </div>

            {versionsQuery.isLoading && (
              <div className="space-y-2" role="status" aria-label="Carregando histórico">
                <Skeleton className="h-20 w-full" />
                <Skeleton className="h-20 w-full" />
              </div>
            )}
            {versionsQuery.isError && (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3" role="alert">
                <p className="text-sm text-destructive">
                  {errorMessage(versionsQuery.error, 'Não foi possível carregar o histórico.')}
                </p>
                <Button className="mt-2" size="sm" variant="outline" onClick={() => versionsQuery.refetch()}>
                  <RefreshCw className="h-4 w-4" />Tentar novamente
                </Button>
              </div>
            )}
            {!versionsQuery.isLoading && !versionsQuery.isError && !(versionsQuery.data?.items ?? []).length && (
              <p className="rounded-md border border-dashed p-5 text-sm text-muted-foreground">
                Nenhuma versão publicada para este plano.
              </p>
            )}
            {(versionsQuery.data?.items ?? []).length > 0 && (
              <ol className="divide-y overflow-hidden rounded-md border" aria-label="Versões publicadas">
                {(versionsQuery.data?.items as PlanPolicyVersion[]).map((version) => (
                  <li key={version.id} className="grid gap-3 p-4 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-start">
                    <span className="flex h-8 min-w-8 items-center justify-center rounded-md bg-primary/10 px-2 text-sm font-semibold text-primary">
                      {version.version}
                    </span>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="font-medium">Versão {version.version}</p>
                        <Badge variant="outline">{rolloutCopy[version.rolloutMode].label}</Badge>
                      </div>
                      <p className="mt-1 break-words text-sm text-muted-foreground">{version.publicationReason}</p>
                    </div>
                    <div className="space-y-2 text-xs text-muted-foreground sm:text-right">
                      <p className="flex items-center gap-1 sm:justify-end">
                        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                        Publicada em {formatDate(version.publishedAt)}
                      </p>
                      <p className="mt-1">Vigência: {formatDate(version.effectiveAt)}</p>
                      {canPublish && version.version !== selectedPlan?.latestVersion && (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          onClick={() => prepareVersionRestoration(version)}
                        >
                          Restaurar como rascunho
                        </Button>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            )}
            {versionsQuery.data && versionsQuery.data.total > 0 && (
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">
                  {versionsQuery.data.offset + 1}–{Math.min(versionsQuery.data.offset + versionsQuery.data.items.length, versionsQuery.data.total)} de {versionsQuery.data.total} versões
                </span>
                <span className="flex gap-2">
                  <Button size="sm" variant="outline" disabled={versionPage === 0 || versionsQuery.isFetching} onClick={() => setVersionPage((value) => Math.max(0, value - 1))}>Anterior</Button>
                  <Button size="sm" variant="outline" disabled={!versionsQuery.data.hasMore || versionsQuery.isFetching} onClick={() => setVersionPage((value) => value + 1)}>Próxima</Button>
                </span>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
