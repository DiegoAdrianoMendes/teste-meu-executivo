import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BadgeCheck,
  Ban,
  CheckCircle2,
  FileCheck2,
  LoaderCircle,
  LockKeyhole,
  MessageSquareText,
  RefreshCw,
  Save,
  ShieldCheck,
  TriangleAlert,
  UsersRound,
  Webhook,
  XCircle,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { KlmPill } from '@/klm/components/Primitives';
import {
  klmApi,
  type KlmMetaGroup,
  type KlmMetaGroupCapability,
  type KlmMetaGroupCapabilityGate,
  type KlmMetaGroupWebhookField,
} from '@/klm/api';
import { cn } from '@/lib/utils';

export type OfficialMetaGroupConnection = {
  id: string;
  name: string;
  provider: string;
  productMode: string | null;
  phoneNumberId?: string | null;
};

export type OfficialMetaGroupSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connection: OfficialMetaGroupConnection | null;
  canConfigure: boolean;
  canPrepareOperations: boolean;
};

type EvidenceDraft = {
  phoneNumberId: string;
  wabaId: string;
  developerAppConfigured: boolean;
  webhookCallbackVerified: boolean;
  subscribedWebhookFields: KlmMetaGroupWebhookField[];
  obaEligibilityState: KlmMetaGroupCapabilityGate['obaEligibilityState'];
  obaEvidenceSource: KlmMetaGroupCapabilityGate['obaEvidenceSource'];
  obaEvidenceReference: string;
  obaEvidenceRecordedAt: string;
};

const WEBHOOK_FIELDS: ReadonlyArray<{ value: KlmMetaGroupWebhookField; label: string }> = [
  { value: 'group_lifecycle_update', label: 'Ciclo de vida do grupo' },
  { value: 'group_participants_update', label: 'Participantes do grupo' },
  { value: 'group_settings_update', label: 'Configurações do grupo' },
  { value: 'group_status_update', label: 'Status do grupo' },
];

const PRODUCT_MODE_LABELS: Record<KlmMetaGroupCapabilityGate['productMode'], string> = {
  official_api: 'API oficial',
  cloud_api: 'Cloud API',
  business_app: 'WhatsApp Business App',
  coexistence: 'Coexistence',
  multi_solution: 'Multi-solution',
  unsupported: 'Modo não suportado',
};

const DENIAL_REASON_LABELS: Record<string, string> = {
  capability_evidence_not_configured: 'A evidência da capability ainda não foi configurada.',
  developer_app_not_configured: 'O app em Meta for Developers não está marcado como configurado.',
  webhook_callback_not_verified: 'O callback de webhook ainda não foi verificado.',
  oba_eligibility_unknown: 'A elegibilidade OBA ainda não foi comprovada.',
  oba_ineligible: 'O número foi registrado como inelegível para OBA.',
  oba_external_evidence_missing: 'Falta uma referência externa verificável para a elegibilidade OBA.',
  provider_not_meta_cloud: 'A conexão selecionada não usa o provedor Meta Cloud.',
  coexistence_unsupported: 'Coexistence não é suportado para grupos oficiais neste contrato.',
  business_app_unsupported: 'O WhatsApp Business App não oferece este contrato de grupos oficiais.',
  multi_solution_unsupported: 'Multi-solution não é suportado para grupos oficiais neste contrato.',
  transport_mode_unsupported: 'O modo de transporte da conexão não é suportado.',
  runtime_external_actions_disabled: 'As ações externas do runtime estão desabilitadas.',
  external_dispatch_not_implemented: 'O despacho externo de grupos não está implementado no runtime local.',
  local_internal_group_has_no_meta_group_id: 'O grupo interno não possui um identificador oficial da Meta.',
};

function emptyEvidenceDraft(phoneNumberId = ''): EvidenceDraft {
  return {
    phoneNumberId,
    wabaId: '',
    developerAppConfigured: false,
    webhookCallbackVerified: false,
    subscribedWebhookFields: [],
    obaEligibilityState: 'unknown',
    obaEvidenceSource: 'unverified',
    obaEvidenceReference: '',
    obaEvidenceRecordedAt: '',
  };
}

function toLocalDateTime(value: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '';
  return new Date(date.getTime() - (date.getTimezoneOffset() * 60_000)).toISOString().slice(0, 16);
}

function evidenceDraftFromCapability(
  capability: KlmMetaGroupCapability,
  fallbackPhoneNumberId: string,
): EvidenceDraft {
  return {
    phoneNumberId: capability.phoneNumberId ?? fallbackPhoneNumberId,
    wabaId: capability.wabaId ?? '',
    developerAppConfigured: capability.developerAppConfigured,
    webhookCallbackVerified: capability.webhookCallbackVerified,
    subscribedWebhookFields: capability.gate.subscribedWebhookFields,
    obaEligibilityState: capability.gate.obaEligibilityState,
    obaEvidenceSource: capability.gate.obaEvidenceSource,
    obaEvidenceReference: capability.obaEvidenceReference ?? '',
    obaEvidenceRecordedAt: toLocalDateTime(capability.obaEvidenceRecordedAt),
  };
}

function denialReasonLabel(reason: string): string {
  if (reason.startsWith('webhook_subscription_missing:')) {
    const field = reason.split(':')[1] as KlmMetaGroupWebhookField;
    const label = WEBHOOK_FIELDS.find((item) => item.value === field)?.label ?? field;
    return `A assinatura obrigatória de webhook “${label}” está ausente.`;
  }
  return DENIAL_REASON_LABELS[reason] ?? reason.replaceAll('_', ' ');
}

function actionErrorMessage(error: unknown): string {
  if (error && typeof error === 'object' && 'detail' in error) {
    const detail = error.detail;
    if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') {
      return detail.message;
    }
  }
  return error instanceof Error && error.message.trim()
    ? error.message
    : 'Não foi possível concluir a operação local.';
}

function GateRow({
  label,
  ready,
  status,
  detail,
}: {
  label: string;
  ready: boolean;
  status: string;
  detail: string;
}) {
  return (
    <div className="grid min-h-[62px] grid-cols-[minmax(86px,0.7fr)_minmax(0,1.3fr)] items-center gap-3 border-b border-border/70 py-2.5 last:border-b-0">
      <div className="flex min-w-0 items-center gap-2">
        {ready
          ? <CheckCircle2 className="h-4 w-4 shrink-0 text-[#0b7435] dark:text-emerald-400" />
          : <XCircle className="h-4 w-4 shrink-0 text-[#a53e3c] dark:text-red-400" />}
        <span className="text-xs font-semibold">{label}</span>
      </div>
      <div className="min-w-0">
        <p className={cn('text-xs font-semibold', ready ? 'text-[#0b7435] dark:text-emerald-400' : 'text-[#a53e3c] dark:text-red-400')}>{status}</p>
        <p className="mt-0.5 text-xs leading-4 text-muted-foreground">{detail}</p>
      </div>
    </div>
  );
}

function GroupKind({ group }: { group: KlmMetaGroup }) {
  return group.isOfficialMetaGroup ? (
    <KlmPill tone="green"><BadgeCheck className="mr-1 h-3 w-3" />Grupo oficial Meta</KlmPill>
  ) : (
    <KlmPill tone="blue"><LockKeyhole className="mr-1 h-3 w-3" />Grupo interno Meu Executivo</KlmPill>
  );
}

export function OfficialMetaGroupSheet({
  open,
  onOpenChange,
  connection,
  canConfigure,
  canPrepareOperations,
}: OfficialMetaGroupSheetProps) {
  const queryClient = useQueryClient();
  const [evidenceDraft, setEvidenceDraft] = useState<EvidenceDraft>(() => emptyEvidenceDraft(connection?.phoneNumberId ?? ''));
  const [hydratedEvidenceVersion, setHydratedEvidenceVersion] = useState<number | null>(null);
  const [evidenceErrors, setEvidenceErrors] = useState<string[]>([]);
  const [internalGroupSubject, setInternalGroupSubject] = useState('');
  const [internalGroupDescription, setInternalGroupDescription] = useState('');
  const [internalJoinApprovalMode, setInternalJoinApprovalMode] = useState<'auto_approve' | 'approval_required'>('auto_approve');
  const [officialGroupSubject, setOfficialGroupSubject] = useState('');
  const [officialGroupDescription, setOfficialGroupDescription] = useState('');
  const [officialJoinApprovalMode, setOfficialJoinApprovalMode] = useState<'auto_approve' | 'approval_required'>('auto_approve');
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [messageBody, setMessageBody] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const connectionId = connection?.id ?? null;
  const capabilityQuery = useQuery({
    queryKey: ['klm', 'meta-group-capability', connectionId],
    queryFn: () => klmApi.metaGroupCapability(connectionId!),
    enabled: open && Boolean(connectionId),
    retry: false,
  });
  const groupsQuery = useQuery({
    queryKey: ['klm', 'meta-groups', connectionId],
    queryFn: () => klmApi.metaGroups(connectionId!),
    enabled: open && Boolean(connectionId),
    retry: false,
  });

  const capability = capabilityQuery.data;
  const gate = capability?.gate;
  const capabilityReady = Boolean(gate?.externalCapabilityGateOpen);
  const groups = groupsQuery.data?.items ?? [];
  const selectedGroup = groups.find((group) => group.id === selectedGroupId) ?? null;

  useEffect(() => {
    if (!open) return;
    setActionError(null);
    setNotice(null);
    setEvidenceErrors([]);
    setSelectedGroupId(null);
    setMessageBody('');
    setInternalGroupSubject('');
    setInternalGroupDescription('');
    setInternalJoinApprovalMode('auto_approve');
    setOfficialGroupSubject('');
    setOfficialGroupDescription('');
    setOfficialJoinApprovalMode('auto_approve');
    setEvidenceDraft(emptyEvidenceDraft(connection?.phoneNumberId ?? ''));
    setHydratedEvidenceVersion(null);
  }, [connection?.id, connection?.phoneNumberId, open]);

  useEffect(() => {
    if (!open || !capability) return;
    setEvidenceDraft(evidenceDraftFromCapability(capability, connection?.phoneNumberId ?? ''));
    setHydratedEvidenceVersion(capability.version);
  }, [capability, connection?.phoneNumberId, open]);

  const requiredWebhookFields = useMemo(
    () => new Set(gate?.requiredWebhookFields ?? WEBHOOK_FIELDS.map((item) => item.value)),
    [gate?.requiredWebhookFields],
  );
  const subscribedWebhookFields = useMemo(
    () => new Set(gate?.subscribedWebhookFields ?? []),
    [gate?.subscribedWebhookFields],
  );

  const configureMutation = useMutation({
    mutationFn: async () => {
      if (!connection || !capability) throw new Error('A capability da conexão ainda não foi carregada.');
      const errors: string[] = [];
      const phoneNumberId = evidenceDraft.phoneNumberId.trim();
      const evidenceReference = evidenceDraft.obaEvidenceReference.trim();
      if (!phoneNumberId) errors.push('Informe o Phone Number ID da conexão.');
      if (evidenceDraft.obaEligibilityState === 'eligible' && evidenceDraft.obaEvidenceSource === 'unverified') {
        errors.push('Elegibilidade OBA exige evidência da Meta API ou do Meta Business Manager.');
      }
      if (evidenceDraft.obaEvidenceSource === 'unverified' && (evidenceReference || evidenceDraft.obaEvidenceRecordedAt)) {
        errors.push('Evidência não verificada não pode carregar referência ou data externa.');
      }
      if (evidenceDraft.obaEvidenceSource !== 'unverified' && (!evidenceReference || !evidenceDraft.obaEvidenceRecordedAt)) {
        errors.push('Informe a referência e o momento observado da evidência OBA externa.');
      }
      if (errors.length) {
        setEvidenceErrors(errors);
        throw new Error('Revise os campos de evidência destacados.');
      }
      return klmApi.configureMetaGroupCapability(connection.id, {
        expectedVersion: capability.version,
        phoneNumberId,
        wabaId: evidenceDraft.wabaId.trim() || null,
        developerAppConfigured: evidenceDraft.developerAppConfigured,
        webhookCallbackVerified: evidenceDraft.webhookCallbackVerified,
        subscribedWebhookFields: evidenceDraft.subscribedWebhookFields,
        obaEligibilityState: evidenceDraft.obaEligibilityState,
        obaEvidenceSource: evidenceDraft.obaEvidenceSource,
        obaEvidenceReference: evidenceDraft.obaEvidenceSource === 'unverified' ? null : evidenceReference,
        obaEvidenceRecordedAt: evidenceDraft.obaEvidenceSource === 'unverified'
          ? null
          : new Date(evidenceDraft.obaEvidenceRecordedAt).toISOString(),
        evidenceMetadata: { source: 'conversations_official_meta_group_sheet' },
      });
    },
    onMutate: () => {
      setActionError(null);
      setNotice(null);
      setEvidenceErrors([]);
    },
    onSuccess: (result) => {
      queryClient.setQueryData(['klm', 'meta-group-capability', result.connectionId], result);
      setNotice('Evidência salva no Meu Executivo com versão e auditoria. Nenhuma API da Meta foi acionada.');
    },
    onError: (error) => {
      if (!(error instanceof Error && error.message === 'Revise os campos de evidência destacados.')) {
        setActionError(actionErrorMessage(error));
      }
    },
  });

  const createInternalGroupMutation = useMutation({
    mutationFn: async () => {
      if (!connection) throw new Error('A conexão WhatsApp ainda não foi carregada.');
      const subject = internalGroupSubject.trim();
      if (!subject) throw new Error('Informe o nome do grupo.');
      return klmApi.createInternalMetaGroup(connection.id, {
        subject,
        description: internalGroupDescription.trim() || null,
        joinApprovalMode: internalJoinApprovalMode,
      });
    },
    onMutate: () => {
      setActionError(null);
      setNotice(null);
    },
    onSuccess: async (result) => {
      setInternalGroupSubject('');
      setInternalGroupDescription('');
      setNotice(`“${result.group.subject}” foi criado somente como grupo interno. Nenhum grupo foi criado no WhatsApp.`);
      await queryClient.invalidateQueries({ queryKey: ['klm', 'meta-groups', connectionId] });
    },
    onError: (error) => setActionError(actionErrorMessage(error)),
  });

  const prepareOfficialCreationMutation = useMutation({
    mutationFn: async () => {
      if (!connection || !capabilityReady) {
        throw new Error('O capability gate oficial precisa estar ready antes da preparação.');
      }
      const subject = officialGroupSubject.trim();
      if (!subject) throw new Error('Informe o nome do grupo oficial.');
      return klmApi.prepareOfficialMetaGroupCreation(connection.id, {
        subject,
        description: officialGroupDescription.trim() || null,
        joinApprovalMode: officialJoinApprovalMode,
      });
    },
    onMutate: () => {
      setActionError(null);
      setNotice(null);
    },
    onSuccess: (result) => {
      setOfficialGroupSubject('');
      setOfficialGroupDescription('');
      setNotice(result.providerRequestPayloadReady
        ? 'Payload de criação oficial preparado e auditado. Nenhum grupo foi criado ou entregue na Meta; a projeção depende de lifecycle verificado.'
        : 'A preparação oficial foi registrada como bloqueada. Nenhum grupo foi criado ou entregue na Meta.');
    },
    onError: (error) => setActionError(actionErrorMessage(error)),
  });

  const prepareMessageMutation = useMutation({
    mutationFn: async () => {
      if (!selectedGroup?.isOfficialMetaGroup || !capabilityReady) {
        throw new Error('Selecione um grupo oficial com capability gate ready.');
      }
      const body = messageBody.trim();
      if (!body) throw new Error('Escreva a mensagem que será preparada.');
      return klmApi.prepareMetaGroupMessage(selectedGroup.id, body);
    },
    onMutate: () => {
      setActionError(null);
      setNotice(null);
    },
    onSuccess: (result) => {
      setMessageBody('');
      setNotice(result.providerPayloadReady
        ? 'Payload oficial preparado e auditado localmente. Nenhuma mensagem foi enviada à Meta.'
        : 'A operação local foi registrada como bloqueada. Nenhuma mensagem foi enviada à Meta.');
    },
    onError: (error) => setActionError(actionErrorMessage(error)),
  });

  const setWebhookSubscription = (field: KlmMetaGroupWebhookField, checked: boolean) => {
    setEvidenceDraft((current) => ({
      ...current,
      subscribedWebhookFields: checked
        ? Array.from(new Set([...current.subscribedWebhookFields, field]))
        : current.subscribedWebhookFields.filter((item) => item !== field),
    }));
    setEvidenceErrors([]);
  };

  const refresh = () => {
    setActionError(null);
    setNotice(null);
    void capabilityQuery.refetch();
    void groupsQuery.refetch();
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-[98vw] max-w-full flex-col overflow-hidden p-0 sm:max-w-[760px]">
        <SheetHeader className="border-b border-border px-5 py-5 pr-12 text-left">
          <div className="mb-1 flex h-9 w-9 items-center justify-center rounded-md bg-[#031121] text-white dark:bg-white dark:text-[#031121]">
            <UsersRound className="h-4 w-4" />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SheetTitle className="text-lg font-semibold">Grupos oficiais WhatsApp</SheetTitle>
            {gate ? <KlmPill tone={capabilityReady ? 'green' : 'red'}>{capabilityReady ? 'Capability ready' : 'Capability bloqueada'}</KlmPill> : null}
            {gate ? <KlmPill tone="muted">Execução externa fechada</KlmPill> : null}
          </div>
          <SheetDescription className="break-words text-xs leading-5">
            {connection
              ? `${connection.name} · leitura de readiness, evidência e preparação auditada sem sair de Conversas.`
              : 'A conversa selecionada não possui uma conexão WhatsApp identificada para este contrato.'}
          </SheetDescription>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto scrollbar-thin">
          {!connection ? (
            <div role="status" className="m-5 border border-dashed border-border p-5 text-center">
              <Ban className="mx-auto h-5 w-5 text-muted-foreground" />
              <p className="mt-3 text-xs font-semibold">Sem conexão WhatsApp selecionada</p>
              <p className="mt-1 text-xs leading-4 text-muted-foreground">Selecione uma rota WhatsApp vinculada à conversa para consultar os grupos e gates locais.</p>
            </div>
          ) : capabilityQuery.isPending || groupsQuery.isPending ? (
            <div role="status" className="flex min-h-48 items-center justify-center gap-2 text-xs text-muted-foreground">
              <LoaderCircle className="h-4 w-4 animate-spin" />Carregando contrato de grupos
            </div>
          ) : capabilityQuery.isError || groupsQuery.isError || !gate ? (
            <div role="alert" className="m-5 border border-red-200 bg-red-50 p-5 text-center text-red-950 dark:border-red-900/70 dark:bg-red-950/25 dark:text-red-100">
              <TriangleAlert className="mx-auto h-5 w-5" />
              <p className="mt-3 text-xs font-semibold">Não foi possível ler os grupos oficiais</p>
              <p className="mt-1 text-xs leading-4 opacity-75">Nenhum estado alternativo será presumido. Recarregue os contratos locais.</p>
              <Button variant="outline" size="sm" className="mt-4 h-8 text-xs" onClick={refresh}><RefreshCw className="mr-1.5 h-3.5 w-3.5" />Tentar novamente</Button>
            </div>
          ) : (
            <>
              <section className="border-b border-border px-5 py-4" aria-label="Natureza dos grupos">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="min-w-0 border-l-2 border-[#031121] pl-3">
                    <div className="flex items-center gap-2"><LockKeyhole className="h-3.5 w-3.5 text-[#031121] dark:text-blue-300" /><p className="text-xs font-semibold">Grupo interno Meu Executivo</p></div>
                    <p className="mt-1 text-xs leading-4 text-muted-foreground">Preparação local auditada, sem `providerGroupId` e sem criação no WhatsApp.</p>
                  </div>
                  <div className="min-w-0 border-l-2 border-[#1a9948] pl-3">
                    <div className="flex items-center gap-2"><BadgeCheck className="h-3.5 w-3.5 text-[#0b7435] dark:text-emerald-400" /><p className="text-xs font-semibold">Grupo oficial Meta</p></div>
                    <p className="mt-1 text-xs leading-4 text-muted-foreground">Projeção com identificador oficial, derivada de evidência de lifecycle do provedor.</p>
                  </div>
                </div>
              </section>

              <section className="border-b border-border px-5 py-4" aria-labelledby="meta-groups-readiness-title">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 id="meta-groups-readiness-title" className="text-sm font-semibold">Readiness e gates</h3>
                    <p className="mt-1 text-xs leading-4 text-muted-foreground">Decisão observada em {new Date(gate.observedAt).toLocaleString('pt-BR')}.</p>
                  </div>
                  <KlmPill tone={gate.productModeSupported ? 'green' : 'red'}>{PRODUCT_MODE_LABELS[gate.productMode]}</KlmPill>
                </div>
                <div className="mt-3" aria-label="Readiness de grupos oficiais">
                  <GateRow
                    label="Developers"
                    ready={gate.developerAppReady}
                    status={gate.developerAppReady ? 'Ready' : 'Not ready'}
                    detail={gate.developerAppReady ? 'App, callback e quatro assinaturas obrigatórias registrados.' : 'A configuração do app ou dos webhooks está incompleta.'}
                  />
                  <GateRow
                    label="OBA"
                    ready={gate.obaGateOpen}
                    status={gate.obaGateOpen ? 'Elegível com evidência externa' : gate.obaEligibilityState === 'ineligible' ? 'Inelegível' : 'Não comprovado'}
                    detail={`Fonte: ${gate.obaEvidenceSource === 'meta_api' ? 'Meta API' : gate.obaEvidenceSource === 'meta_business_manager' ? 'Meta Business Manager' : 'não verificada'}.`}
                  />
                  <GateRow
                    label="Coexistence"
                    ready={false}
                    status={gate.coexistenceDenied ? 'Bloqueado' : 'Não aplicável'}
                    detail={gate.coexistenceDenied ? 'Não suportado para grupos oficiais; a plataforma não promete ativação nesse modo.' : 'Nenhum suporte a Coexistence é presumido por esta conexão.'}
                  />
                  <GateRow
                    label="Multi-solution"
                    ready={false}
                    status={gate.multiSolutionDenied ? 'Bloqueado' : 'Não aplicável'}
                    detail={gate.multiSolutionDenied ? 'Não suportado para grupos oficiais neste contrato.' : 'Nenhum suporte a Multi-solution é presumido por esta conexão.'}
                  />
                  <GateRow
                    label="Capability"
                    ready={capabilityReady}
                    status={capabilityReady ? 'Ready para preparar payload' : 'Preparação oficial bloqueada'}
                    detail="Este gate comprova requisitos para construir o payload; não autoriza entrega externa."
                  />
                  <GateRow
                    label="Execução"
                    ready={gate.externalExecutionGateOpen}
                    status="Fechada no runtime local"
                    detail="externalExecutionGateOpen e externalDispatchImplemented permanecem false."
                  />
                </div>

                <div className="mt-4 border border-amber-300/70 bg-amber-50 px-3 py-2.5 text-amber-950 dark:border-amber-800/70 dark:bg-amber-950/25 dark:text-amber-100">
                  <div className="flex items-start gap-2">
                    <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
                    <div><p className="text-xs font-semibold">Despacho externo não implementado</p><p className="mt-0.5 text-xs leading-4 opacity-80">Mesmo com gate ready, esta tela apenas registra evidência e prepara operações. `externalDispatchExecuted` permanece `false`.</p></div>
                  </div>
                </div>

                {gate.capabilityDenialReasons.length ? (
                  <div className="mt-4" aria-label="Razões de bloqueio">
                    <p className="text-xs font-semibold uppercase text-muted-foreground">Bloqueios da capability</p>
                    <ul className="mt-2 space-y-1.5">
                      {gate.capabilityDenialReasons.map((reason) => <li key={reason} className="flex items-start gap-2 text-xs leading-4 text-muted-foreground"><XCircle className="mt-0.5 h-3 w-3 shrink-0 text-[#a53e3c]" />{denialReasonLabel(reason)}</li>)}
                    </ul>
                  </div>
                ) : null}
                <div className="mt-4" aria-label="Limites da execução externa">
                  <p className="text-xs font-semibold uppercase text-muted-foreground">Execução externa</p>
                  <ul className="mt-2 space-y-1.5">
                    {gate.executionDenialReasons.filter((reason) => !gate.capabilityDenialReasons.includes(reason)).map((reason) => <li key={reason} className="flex items-start gap-2 text-xs leading-4 text-muted-foreground"><LockKeyhole className="mt-0.5 h-3 w-3 shrink-0" />{denialReasonLabel(reason)}</li>)}
                  </ul>
                </div>
              </section>

              <section className="border-b border-border px-5 py-4" aria-labelledby="meta-groups-limits-title">
                <h3 id="meta-groups-limits-title" className="text-sm font-semibold">Limites oficiais</h3>
                <div className="mt-3 grid grid-cols-1 divide-y divide-border border-y border-border text-center sm:grid-cols-3 sm:divide-x sm:divide-y-0">
                  <div className="px-2 py-3"><p className="text-sm font-semibold">{gate.limits.maxParticipants}</p><p className="mt-1 text-xs leading-4 text-muted-foreground">participantes por grupo</p></div>
                  <div className="px-2 py-3"><p className="text-sm font-semibold">{gate.limits.maxGroupsPerBusinessNumber.toLocaleString('pt-BR')}</p><p className="mt-1 text-xs leading-4 text-muted-foreground">grupos por número</p></div>
                  <div className="px-2 py-3"><p className="text-sm font-semibold">{gate.limits.inviteOnly ? 'Sim' : 'Não'}</p><p className="mt-1 text-xs leading-4 text-muted-foreground">entrada por convite</p></div>
                </div>
              </section>

              <section className="border-b border-border px-5 py-4" aria-labelledby="meta-groups-webhooks-title">
                <div className="flex items-center gap-2"><Webhook className="h-4 w-4 text-[#031121] dark:text-blue-300" /><h3 id="meta-groups-webhooks-title" className="text-sm font-semibold">Webhooks exigidos</h3></div>
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {WEBHOOK_FIELDS.filter((item) => requiredWebhookFields.has(item.value)).map((item) => {
                    const subscribed = subscribedWebhookFields.has(item.value);
                    return <div key={item.value} className="flex min-h-10 items-center justify-between gap-2 border-b border-border/70 py-2"><span className="text-xs">{item.label}</span><KlmPill tone={subscribed ? 'green' : 'red'}>{subscribed ? 'Assinado' : 'Ausente'}</KlmPill></div>;
                  })}
                </div>
              </section>

              <section className="border-b border-border px-5 py-4" aria-labelledby="meta-groups-evidence-title">
                <div className="flex items-start justify-between gap-3">
                  <div><h3 id="meta-groups-evidence-title" className="text-sm font-semibold">Evidência da capability</h3><p className="mt-1 text-xs leading-4 text-muted-foreground">Versão {capability.version}. OBA elegível requer fonte, referência e momento externos verificáveis.</p></div>
                  <KlmPill tone={capability.configured ? 'green' : 'muted'}>{capability.configured ? 'Configurada' : 'Não configurada'}</KlmPill>
                </div>

                {canConfigure ? (
                  <div className="mt-4 space-y-4" aria-label="Configurar evidência oficial">
                    <div className="grid gap-3 sm:grid-cols-2">
                      <div className="space-y-1.5"><Label htmlFor="meta-group-phone-number-id" className="text-xs">Phone Number ID</Label><Input id="meta-group-phone-number-id" value={evidenceDraft.phoneNumberId} onChange={(event) => setEvidenceDraft((current) => ({ ...current, phoneNumberId: event.target.value }))} className="h-9 text-xs" /></div>
                      <div className="space-y-1.5"><Label htmlFor="meta-group-waba-id" className="text-xs">WABA ID</Label><Input id="meta-group-waba-id" value={evidenceDraft.wabaId} onChange={(event) => setEvidenceDraft((current) => ({ ...current, wabaId: event.target.value }))} className="h-9 text-xs" /></div>
                    </div>

                    <div className="grid gap-2 sm:grid-cols-2">
                      <div className="flex min-h-11 items-center justify-between gap-3 border border-border px-3 py-2"><Label htmlFor="meta-group-developer-app" className="text-xs leading-4">App Developers configurado</Label><Switch id="meta-group-developer-app" checked={evidenceDraft.developerAppConfigured} onCheckedChange={(checked) => setEvidenceDraft((current) => ({ ...current, developerAppConfigured: checked }))} /></div>
                      <div className="flex min-h-11 items-center justify-between gap-3 border border-border px-3 py-2"><Label htmlFor="meta-group-webhook-callback" className="text-xs leading-4">Callback verificado</Label><Switch id="meta-group-webhook-callback" checked={evidenceDraft.webhookCallbackVerified} onCheckedChange={(checked) => setEvidenceDraft((current) => ({ ...current, webhookCallbackVerified: checked }))} /></div>
                    </div>

                    <fieldset>
                      <legend className="text-xs font-semibold">Assinaturas observadas</legend>
                      <div className="mt-2 grid gap-2 sm:grid-cols-2">
                        {WEBHOOK_FIELDS.map((item) => (
                          <label key={item.value} className="flex min-h-10 cursor-pointer items-center gap-2 border border-border px-3 py-2 text-xs">
                            <Checkbox checked={evidenceDraft.subscribedWebhookFields.includes(item.value)} onCheckedChange={(checked) => setWebhookSubscription(item.value, checked === true)} />
                            <span>{item.label}</span>
                          </label>
                        ))}
                      </div>
                    </fieldset>

                    <div className="grid gap-3 sm:grid-cols-2">
                      <div className="space-y-1.5"><Label className="text-xs">Estado OBA</Label><Select value={evidenceDraft.obaEligibilityState} onValueChange={(value: EvidenceDraft['obaEligibilityState']) => setEvidenceDraft((current) => ({ ...current, obaEligibilityState: value }))}><SelectTrigger className="h-9 text-xs" aria-label="Estado OBA"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="unknown">Não comprovado</SelectItem><SelectItem value="eligible">Elegível</SelectItem><SelectItem value="ineligible">Inelegível</SelectItem></SelectContent></Select></div>
                      <div className="space-y-1.5"><Label className="text-xs">Fonte da evidência OBA</Label><Select value={evidenceDraft.obaEvidenceSource} onValueChange={(value: EvidenceDraft['obaEvidenceSource']) => setEvidenceDraft((current) => ({ ...current, obaEvidenceSource: value, obaEvidenceReference: value === 'unverified' ? '' : current.obaEvidenceReference, obaEvidenceRecordedAt: value === 'unverified' ? '' : current.obaEvidenceRecordedAt }))}><SelectTrigger className="h-9 text-xs" aria-label="Fonte da evidência OBA"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="unverified">Não verificada</SelectItem><SelectItem value="meta_api">Meta API</SelectItem><SelectItem value="meta_business_manager">Meta Business Manager</SelectItem></SelectContent></Select></div>
                    </div>

                    {evidenceDraft.obaEvidenceSource !== 'unverified' ? (
                      <div className="grid gap-3 sm:grid-cols-2">
                        <div className="space-y-1.5"><Label htmlFor="meta-group-evidence-reference" className="text-xs">Referência externa</Label><Input id="meta-group-evidence-reference" value={evidenceDraft.obaEvidenceReference} onChange={(event) => setEvidenceDraft((current) => ({ ...current, obaEvidenceReference: event.target.value }))} className="h-9 text-xs" placeholder="ID ou URL verificável" /></div>
                        <div className="space-y-1.5"><Label htmlFor="meta-group-evidence-observed-at" className="text-xs">Momento observado</Label><Input id="meta-group-evidence-observed-at" type="datetime-local" value={evidenceDraft.obaEvidenceRecordedAt} onChange={(event) => setEvidenceDraft((current) => ({ ...current, obaEvidenceRecordedAt: event.target.value }))} className="h-9 text-xs" /></div>
                      </div>
                    ) : null}

                    {evidenceErrors.length ? <div role="alert" className="border border-red-200 bg-red-50 p-3 text-red-950 dark:border-red-900/70 dark:bg-red-950/25 dark:text-red-100"><ul className="space-y-1 text-xs leading-4">{evidenceErrors.map((error) => <li key={error}>• {error}</li>)}</ul></div> : null}
                    <Button className="h-9 bg-[#031121] text-xs text-white hover:bg-[#031121]" onClick={() => configureMutation.mutate()} disabled={configureMutation.isPending || hydratedEvidenceVersion !== capability.version}><Save className="mr-1.5 h-3.5 w-3.5" />{configureMutation.isPending ? 'Salvando evidência' : 'Salvar evidência local'}</Button>
                  </div>
                ) : (
                  <div className="mt-3 flex items-start gap-2 border border-dashed border-border p-3 text-xs leading-4 text-muted-foreground"><LockKeyhole className="mt-0.5 h-3.5 w-3.5 shrink-0" />Seu perfil pode consultar o gate, mas ações mutáveis de evidência não são exibidas.</div>
                )}
              </section>

              <section className="border-b border-border px-5 py-4" aria-labelledby="meta-groups-list-title">
                <div className="flex items-start justify-between gap-3">
                  <div><h3 id="meta-groups-list-title" className="text-sm font-semibold">Grupos desta conexão</h3><p className="mt-1 text-xs text-muted-foreground">{groupsQuery.data?.total ?? 0} projeção{(groupsQuery.data?.total ?? 0) === 1 ? '' : 'ões'} persistida{(groupsQuery.data?.total ?? 0) === 1 ? '' : 's'}.</p></div>
                  <Button variant="outline" size="icon" className="h-8 w-8" aria-label="Atualizar grupos oficiais" title="Atualizar grupos" onClick={refresh} disabled={capabilityQuery.isFetching || groupsQuery.isFetching}><RefreshCw className={cn('h-3.5 w-3.5', (capabilityQuery.isFetching || groupsQuery.isFetching) && 'animate-spin')} /></Button>
                </div>

                {groups.length === 0 ? (
                  <div className="mt-3 border border-dashed border-border p-4 text-center text-xs text-muted-foreground">Nenhum grupo interno ou oficial foi projetado para este número.</div>
                ) : (
                  <div className="mt-3 space-y-2" role="list" aria-label="Grupos internos e oficiais">
                    {groups.map((group) => (
                      <article key={group.id} role="listitem" aria-label={`${group.isOfficialMetaGroup ? 'Grupo oficial Meta' : 'Grupo interno Meu Executivo'}: ${group.subject}`} className="rounded-lg border border-border bg-card p-3">
                        <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                          <div className="min-w-0 flex-1"><p className="truncate text-xs font-semibold">{group.subject}</p><p className="mt-1 line-clamp-2 break-words text-xs leading-4 text-muted-foreground">{group.description || (group.isOfficialMetaGroup ? `Meta ID: ${group.providerGroupId}` : 'Somente preparação interna; não existe na Meta.')}</p></div>
                          <GroupKind group={group} />
                        </div>
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><span>{group.participantCount}/{gate.limits.maxParticipants} participantes</span><span>•</span><span>{group.joinApprovalMode === 'approval_required' ? 'Entrada com aprovação' : 'Aprovação automática'}</span><span>•</span><span>{group.state.replaceAll('_', ' ')}</span></div>
                        {canPrepareOperations && group.isOfficialMetaGroup ? (
                          <Button variant="outline" size="sm" className="mt-3 h-8 text-xs" disabled={!capabilityReady} title={capabilityReady ? 'Preparar payload local sem envio' : 'O capability gate oficial precisa estar ready'} onClick={() => { setSelectedGroupId(group.id); setMessageBody(''); setActionError(null); setNotice(null); }}><MessageSquareText className="mr-1.5 h-3.5 w-3.5" />Preparar mensagem</Button>
                        ) : null}
                      </article>
                    ))}
                  </div>
                )}
              </section>

              {canPrepareOperations ? (
                <section className="px-5 py-4" aria-labelledby="meta-groups-operations-title">
                  <div className="flex items-center gap-2"><FileCheck2 className="h-4 w-4 text-[#0057d8] dark:text-primary" /><h3 id="meta-groups-operations-title" className="text-sm font-semibold">Operações locais</h3></div>
                  <p className="mt-1 text-xs leading-4 text-muted-foreground">Grupo interno e preparação oficial usam contratos distintos. Nenhuma ação desta tela executa entrega externa.</p>

                  <div className="mt-4 space-y-3 border-t border-border pt-4">
                    <div><p className="text-xs font-semibold">Criar grupo interno Meu Executivo</p><p className="mt-1 text-xs leading-4 text-muted-foreground">Cria somente a projeção interna; não depende da capability Meta e nunca vira grupo oficial por esta ação.</p></div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <div className="space-y-1.5"><Label htmlFor="meta-internal-group-subject" className="text-xs">Nome do grupo interno</Label><Input id="meta-internal-group-subject" value={internalGroupSubject} onChange={(event) => setInternalGroupSubject(event.target.value)} maxLength={128} className="h-9 text-xs" /></div>
                      <div className="space-y-1.5"><Label className="text-xs">Entrada interna</Label><Select value={internalJoinApprovalMode} onValueChange={(value: typeof internalJoinApprovalMode) => setInternalJoinApprovalMode(value)}><SelectTrigger className="h-9 text-xs" aria-label="Entrada no grupo interno"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="auto_approve">Aprovação automática</SelectItem><SelectItem value="approval_required">Aprovação obrigatória</SelectItem></SelectContent></Select></div>
                    </div>
                    <div className="space-y-1.5"><Label htmlFor="meta-internal-group-description" className="text-xs">Descrição interna</Label><Textarea id="meta-internal-group-description" value={internalGroupDescription} onChange={(event) => setInternalGroupDescription(event.target.value)} maxLength={2048} className="min-h-[70px] resize-none text-xs" /></div>
                    <Button variant="outline" className="h-auto min-h-9 max-w-full whitespace-normal px-3 py-2 text-xs" disabled={createInternalGroupMutation.isPending || !internalGroupSubject.trim()} onClick={() => createInternalGroupMutation.mutate()}><LockKeyhole className="mr-1.5 h-3.5 w-3.5 shrink-0" />{createInternalGroupMutation.isPending ? 'Criando grupo interno Meu Executivo' : 'Criar grupo interno Meu Executivo'}</Button>
                  </div>

                  <div className="mt-5 space-y-3 border-t border-border pt-4" aria-label="Preparar criação de grupo oficial">
                    <div className="flex flex-wrap items-start justify-between gap-2"><div><p className="text-xs font-semibold">Preparar criação oficial Meta</p><p className="mt-1 text-xs leading-4 text-muted-foreground">Monta e audita o payload oficial. Não cria projeção, não chama a Meta e só é liberado pela capability.</p></div><KlmPill tone={capabilityReady ? 'green' : 'red'}>{capabilityReady ? 'Capability ready' : 'Bloqueada'}</KlmPill></div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <div className="space-y-1.5"><Label htmlFor="meta-official-group-subject" className="text-xs">Nome do grupo oficial</Label><Input id="meta-official-group-subject" value={officialGroupSubject} onChange={(event) => setOfficialGroupSubject(event.target.value)} maxLength={128} className="h-9 text-xs" /></div>
                      <div className="space-y-1.5"><Label className="text-xs">Entrada oficial</Label><Select value={officialJoinApprovalMode} onValueChange={(value: typeof officialJoinApprovalMode) => setOfficialJoinApprovalMode(value)}><SelectTrigger className="h-9 text-xs" aria-label="Entrada no grupo oficial"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="auto_approve">Aprovação automática</SelectItem><SelectItem value="approval_required">Aprovação obrigatória</SelectItem></SelectContent></Select></div>
                    </div>
                    <div className="space-y-1.5"><Label htmlFor="meta-official-group-description" className="text-xs">Descrição oficial</Label><Textarea id="meta-official-group-description" value={officialGroupDescription} onChange={(event) => setOfficialGroupDescription(event.target.value)} maxLength={2048} className="min-h-[70px] resize-none text-xs" /></div>
                    <Button className="h-auto min-h-9 max-w-full whitespace-normal bg-[#031121] px-3 py-2 text-xs text-white hover:bg-[#031121]" disabled={!capabilityReady || prepareOfficialCreationMutation.isPending || !officialGroupSubject.trim()} title={capabilityReady ? 'Preparar payload oficial sem criar ou entregar' : 'O capability gate oficial precisa estar ready'} onClick={() => prepareOfficialCreationMutation.mutate()}><FileCheck2 className="mr-1.5 h-3.5 w-3.5 shrink-0" />{prepareOfficialCreationMutation.isPending ? 'Preparando criação oficial' : 'Preparar criação oficial sem envio'}</Button>
                  </div>

                  {selectedGroup ? (
                    <div className="mt-5 space-y-3 border-t border-border pt-4" aria-label={`Preparar mensagem para ${selectedGroup.subject}`}>
                      <div><p className="text-xs font-semibold">Mensagem para {selectedGroup.subject}</p><p className="mt-1 text-xs text-muted-foreground">O endpoint validará e auditará o payload oficial, mantendo o despacho externo em `false`.</p></div>
                      <Textarea aria-label="Mensagem oficial a preparar" value={messageBody} onChange={(event) => setMessageBody(event.target.value)} className="min-h-[86px] resize-none text-xs" placeholder="Texto para validação local" />
                      <div className="flex flex-wrap gap-2"><Button className="h-9 bg-[#031121] text-xs text-white hover:bg-[#031121]" disabled={!capabilityReady || !messageBody.trim() || prepareMessageMutation.isPending} onClick={() => prepareMessageMutation.mutate()}><FileCheck2 className="mr-1.5 h-3.5 w-3.5" />{prepareMessageMutation.isPending ? 'Preparando payload' : 'Preparar payload sem envio'}</Button><Button variant="ghost" size="sm" className="h-9 text-xs" onClick={() => { setSelectedGroupId(null); setMessageBody(''); }}>Cancelar</Button></div>
                    </div>
                  ) : null}
                </section>
              ) : (
                <section className="px-5 py-4"><div className="flex items-start gap-2 border border-dashed border-border p-3 text-xs leading-4 text-muted-foreground"><LockKeyhole className="mt-0.5 h-3.5 w-3.5 shrink-0" />Seu perfil pode consultar grupos e readiness, mas ações mutáveis de preparação não são exibidas.</div></section>
              )}
            </>
          )}
        </div>

        {(notice || actionError) ? (
          <div role={actionError ? 'alert' : 'status'} className={cn('border-t px-5 py-3 text-xs leading-4', actionError ? 'border-red-300 bg-red-50 text-red-950 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100' : 'border-emerald-300 bg-emerald-50 text-emerald-950 dark:border-emerald-900 dark:bg-emerald-950/25 dark:text-emerald-100')}>
            <div className="flex items-start gap-2">{actionError ? <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />}<span>{actionError ?? notice}</span></div>
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
