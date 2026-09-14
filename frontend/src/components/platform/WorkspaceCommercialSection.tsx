import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, BadgeDollarSign, CheckCircle2, ExternalLink, Loader2, Plus, Scale } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { ApiError } from '@/services/http';
import {
  addCommercialAdjustment,
  addEntitlementBonus,
  commercialIdempotencyKey,
  commercialMoney,
  COMMERCIAL_ENTITLEMENT_KEYS,
  COMMERCIAL_ENTITLEMENT_LABELS,
  createWorkspaceCommercialBillingPortal,
  createWorkspaceCommercialContract,
  getWorkspaceCapabilityManifest,
  getWorkspaceCommercialContract,
  getWorkspaceCommercialContractHistory,
  getWorkspaceCommercialProviderState,
  listCommercialAdjustments,
  listCommercialOffers,
  listEntitlementBonuses,
  previewWorkspaceCommercialContract,
  previewWorkspaceCommercialContractRevision,
  previewWorkspaceCommercialContractRestore,
  restoreWorkspaceCommercialContract,
  reviseWorkspaceCommercialContract,
  revokeCommercialAdjustment,
  revokeEntitlementBonus,
  updateCommercialAdjustment,
  updateEntitlementBonus,
  type CommercialAdjustment,
  type CommercialAdjustmentCreateRequest,
  type CommercialAdjustmentSource,
  type CommercialAdjustmentType,
  type CommercialEntitlementKey,
  type CommercialOfferVersion,
  type CommercialPortability,
  type EntitlementBonusCreateRequest,
  type EntitlementBonus,
  type EntitlementBonusSource,
  type WorkspaceContractLineWrite,
  type WorkspaceContractPreview,
  type WorkspaceContractRevisionPreview,
  type WorkspaceContractRestorePreview,
} from '@/services/platform-commercial-catalog';
import {
  fetchPlatformCommercialRequests,
  resolvePlatformCommercialRequest,
  executePlatformCommercialProviderChange,
  type CommercialAccountRequest,
} from '@/services/commercial-self-service';

const FEATURE_ENTITLEMENTS = new Set<CommercialEntitlementKey>([
  'digital_signature',
  'calendar',
  'email',
  'webhooks',
  'advanced_audit',
  'success_manager',
  'legal_suite',
  'legal_signatures',
  'legal_bulk_operations',
  'legal_audit',
  'legal_integrations',
]);

const UNAVAILABLE_BONUS_ENTITLEMENTS = new Set<CommercialEntitlementKey>([
  'legal_cases',
  'djen_terms',
  'legal_ai_generations',
  'legal_automations',
  'legal_templates',
  'legal_signatures',
  'legal_bulk_operations',
  'legal_audit',
  'legal_integrations',
]);

const NO_CORE_SKU = 'legal-only';
const MIN_COMMERCIAL_REASON_LENGTH = 8;
const MAX_COMMERCIAL_REASON_LENGTH = 500;

function isCommercialReasonValid(value: string): boolean {
  const length = Array.from(value.trim()).length;
  return length >= MIN_COMMERCIAL_REASON_LENGTH && length <= MAX_COMMERCIAL_REASON_LENGTH;
}

interface WorkspaceCommercialSectionProps {
  workspaceId: string;
  canView: boolean;
  canManageContracts: boolean;
  canViewAdjustments: boolean;
  canManageAdjustments: boolean;
  canViewBonuses: boolean;
  canManageBonuses: boolean;
  canRestoreContract?: boolean;
  isPlatformRoot?: boolean;
}

interface AdjustmentForm {
  editingId?: string;
  editingVersion?: number;
  type: CommercialAdjustmentType;
  value: string;
  lineKey: string;
  startsAt: string;
  endsAt: string;
  remainingCycles: string;
  stackGroup: string;
  stackable: boolean;
  portability: CommercialPortability;
  source: CommercialAdjustmentSource;
  reason: string;
}

interface BonusForm {
  editingId?: string;
  editingVersion?: number;
  entitlement: CommercialEntitlementKey;
  quantity: string;
  startsAt: string;
  endsAt: string;
  renewsEachPeriod: boolean;
  portability: CommercialPortability;
  source: EntitlementBonusSource;
  reason: string;
}

function localDateTime(value: Date = new Date()): string {
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function toIso(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function adjustmentLabel(type: CommercialAdjustmentType): string {
  return {
    percentage_discount: 'Desconto percentual',
    fixed_discount: 'Desconto fixo',
    negotiated_price: 'Preço negociado',
    courtesy_period: 'Cortesia por período',
    invoice_credit: 'Crédito na fatura',
  }[type];
}

function adjustmentValue(type: CommercialAdjustmentType, raw: string): number | null {
  if (type === 'courtesy_period') return raw === '1' ? 1 : null;
  const scaled = scaledDecimalInteger(raw, 2);
  if (scaled == null) return null;
  if (type === 'percentage_discount') {
    return scaled <= 10_000 ? scaled : null;
  }
  return scaled;
}

function scaledDecimalInteger(raw: string, decimalPlaces: number): number | null {
  const normalized = raw.trim().replace(',', '.');
  const match = normalized.match(new RegExp(`^(\\d+)(?:\\.(\\d{1,${decimalPlaces}}))?$`));
  if (!match) return null;
  const factor = 10 ** decimalPlaces;
  const whole = Number(match[1]);
  const fraction = Number((match[2] ?? '').padEnd(decimalPlaces, '0'));
  const scaled = whole * factor + fraction;
  return Number.isSafeInteger(scaled) ? scaled : null;
}

function positiveMoneyCents(raw: string): number | null {
  const cents = scaledDecimalInteger(raw, 2);
  return cents != null && cents > 0 ? cents : null;
}

function integerQuantity(raw: string, minimum: number): number | null {
  const normalized = raw.trim();
  if (!/^\d+$/.test(normalized)) return null;
  const value = Number(normalized);
  return Number.isSafeInteger(value) && value >= minimum ? value : null;
}

function buildAdjustmentPayload(
  form: AdjustmentForm,
  now: Date = new Date(),
): CommercialAdjustmentCreateRequest | null {
  const value = adjustmentValue(form.type, form.value);
  const startsAt = toIso(form.startsAt);
  const endsAt = toIso(form.endsAt);
  const stackGroup = form.stackGroup.trim();
  if (
    value == null
    || !startsAt
    || !isCommercialReasonValid(form.reason)
    || !stackGroup
    || stackGroup.length > 80
    || (form.endsAt.trim() !== '' && !endsAt)
  ) return null;
  if (form.type === 'courtesy_period' && !endsAt) return null;
  if (endsAt) {
    const endTimestamp = Date.parse(endsAt);
    if (endTimestamp <= Date.parse(startsAt) || endTimestamp <= now.getTime()) return null;
  }
  return {
    type: form.type,
    value,
    startsAt,
    stackGroup,
    stackable: form.stackable,
    portability: form.portability,
    reason: form.reason.trim(),
    source: form.source,
    ...(form.lineKey !== 'contract' ? { contractLineId: form.lineKey } : {}),
    ...(endsAt ? { endsAt } : {}),
    // remainingCycles is deliberately not written until cycle consumption
    // has an idempotent ledger. endsAt is the supported finite boundary.
  };
}

function buildBonusPayload(
  form: BonusForm,
  now: Date = new Date(),
): EntitlementBonusCreateRequest | null {
  const startsAt = toIso(form.startsAt);
  const endsAt = toIso(form.endsAt);
  const feature = FEATURE_ENTITLEMENTS.has(form.entitlement);
  const quantity = feature ? null : integerQuantity(form.quantity, 0);
  if (
    !startsAt
    || !isCommercialReasonValid(form.reason)
    || (form.endsAt.trim() !== '' && !endsAt)
    || (!form.renewsEachPeriod && !endsAt)
    || (!feature && quantity == null)
  ) return null;
  if (endsAt) {
    const endTimestamp = Date.parse(endsAt);
    if (endTimestamp <= Date.parse(startsAt) || endTimestamp <= now.getTime()) return null;
  }
  return {
    entitlement: form.entitlement,
    mode: feature ? 'feature_grant' : 'quantity_delta',
    ...(feature ? {} : { quantity: quantity! }),
    startsAt,
    ...(endsAt ? { endsAt } : {}),
    renewsEachPeriod: form.renewsEachPeriod,
    portability: form.portability,
    reason: form.reason.trim(),
    source: form.source,
  };
}

function adjustmentDisplay(type: CommercialAdjustmentType, value: number): string {
  if (type === 'percentage_discount') return `${(value / 100).toLocaleString('pt-BR')}%`;
  if (type === 'courtesy_period') return 'Cortesia integral no intervalo definido';
  return commercialMoney(value);
}

function offerLabel(offer: CommercialOfferVersion): string {
  return offer.snapshot.name;
}

export function WorkspaceCommercialSection({
  workspaceId,
  canView,
  canManageContracts,
  canViewAdjustments,
  canManageAdjustments,
  canViewBonuses,
  canManageBonuses,
  canRestoreContract = false,
  isPlatformRoot = false,
}: WorkspaceCommercialSectionProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [baseSku, setBaseSku] = useState(NO_CORE_SKU);
  const [includeLegal, setIncludeLegal] = useState(false);
  const [legalPrice, setLegalPrice] = useState('');
  const [legalUsers, setLegalUsers] = useState('1');
  const [legalAreas, setLegalAreas] = useState('1');
  const [legalStorageGb, setLegalStorageGb] = useState('5');
  const [legalMonitoredProcesses, setLegalMonitoredProcesses] = useState('0');
  const [legalDocuments, setLegalDocuments] = useState('');
  const [legalProtocolFolders, setLegalProtocolFolders] = useState('');
  const [contractReason, setContractReason] = useState('');
  const [preview, setPreview] = useState<{
    fingerprint: string;
    data: WorkspaceContractPreview | WorkspaceContractRevisionPreview;
    mode: 'create' | 'revision';
  } | null>(null);
  const [adjustmentForm, setAdjustmentForm] = useState<AdjustmentForm | null>(null);
  const [bonusForm, setBonusForm] = useState<BonusForm | null>(null);
  const [adjustmentRevoke, setAdjustmentRevoke] = useState<{ item: CommercialAdjustment; reason: string } | null>(null);
  const [bonusRevoke, setBonusRevoke] = useState<{ item: EntitlementBonus; reason: string } | null>(null);
  const [restorePreview, setRestorePreview] = useState<WorkspaceContractRestorePreview | null>(null);
  const [restoreReason, setRestoreReason] = useState('');
  const contractIntent = useRef<{ fingerprint: string; key: string } | null>(null);
  const initializedContractVersion = useRef<string | null>(null);

  const contractQuery = useQuery({
    queryKey: ['platform', 'commercial', 'workspace', workspaceId, 'contract'],
    queryFn: async ({ signal }) => {
      try {
        return await getWorkspaceCommercialContract(workspaceId, signal);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: canView,
    retry: false,
  });
  const offersQuery = useQuery({
    queryKey: ['platform', 'commercial', 'offers'],
    queryFn: ({ signal }) => listCommercialOffers({ signal }),
    enabled: canView,
    staleTime: 30_000,
  });
  const manifestQuery = useQuery({
    queryKey: ['platform', 'commercial', 'workspace', workspaceId, 'manifest'],
    queryFn: ({ signal }) => getWorkspaceCapabilityManifest(workspaceId, signal),
    enabled: canView && Boolean(contractQuery.data),
    retry: false,
  });
  const providerQuery = useQuery({
    queryKey: ['platform', 'commercial', 'workspace', workspaceId, 'provider-state'],
    queryFn: ({ signal }) => getWorkspaceCommercialProviderState(workspaceId, signal),
    enabled: canView && Boolean(contractQuery.data),
    retry: false,
  });
  const providerStateUnresolved = Boolean(contractQuery.data)
    && (providerQuery.isLoading || providerQuery.isError || !providerQuery.data);
  const providerManagesCore = Boolean(providerQuery.data?.subscriptionLinked);
  const adjustmentsQuery = useQuery({
    queryKey: ['platform', 'commercial', 'contract', contractQuery.data?.id, 'adjustments'],
    queryFn: ({ signal }) => listCommercialAdjustments(contractQuery.data!.id, signal),
    enabled: canViewAdjustments && Boolean(contractQuery.data?.id),
    retry: false,
  });
  const bonusesQuery = useQuery({
    queryKey: ['platform', 'commercial', 'workspace', workspaceId, 'bonuses'],
    queryFn: ({ signal }) => listEntitlementBonuses(workspaceId, signal),
    enabled: canViewBonuses,
    retry: false,
  });
  const historyQuery = useQuery({
    queryKey: ['platform', 'commercial', 'contract', contractQuery.data?.id, 'history'],
    queryFn: ({ signal }) => getWorkspaceCommercialContractHistory(
      contractQuery.data!.id,
      signal,
    ),
    enabled: canView && Boolean(contractQuery.data?.id),
    retry: false,
  });
  const requestsQuery = useQuery({
    queryKey: ['platform', 'commercial', 'workspace', workspaceId, 'requests'],
    queryFn: () => fetchPlatformCommercialRequests(workspaceId),
    enabled: canView,
    retry: false,
  });
  const resolveRequestMutation = useMutation({
    mutationFn: ({ item, status, note }: { item: CommercialAccountRequest; status: 'resolved' | 'rejected'; note: string }) =>
      resolvePlatformCommercialRequest(item.id, { expectedVersion: item.version, status, note }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'commercial', 'workspace', workspaceId] });
      toast({ title: 'Solicitação comercial atualizada.' });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Solicitação não atualizada',
      description: errorMessage(error, 'Aplique primeiro a mudança no contrato canônico.'),
    }),
  });
  const executeProviderRequestMutation = useMutation({
    mutationFn: (item: CommercialAccountRequest) => executePlatformCommercialProviderChange(
      item.id,
      {
        expectedVersion: item.version,
        note: `Alteração ${item.requestType.replaceAll('_', ' ')} executada no provedor e reconciliada.`,
      },
    ),
    onSuccess: () => {
      initializedContractVersion.current = null;
      void queryClient.invalidateQueries({ queryKey: ['platform', 'commercial', 'workspace', workspaceId] });
      toast({
        title: 'Alteração executada no provedor.',
        description: 'O contrato canônico e a solicitação foram reconciliados.',
      });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Alteração não executada',
      description: errorMessage(error, 'Confirme o vínculo, o gate externo e a versão do contrato.'),
    }),
  });
  const billingPortalMutation = useMutation({
    mutationFn: () => createWorkspaceCommercialBillingPortal(workspaceId),
    onSuccess: ({ portalUrl }) => {
      window.open(portalUrl, '_blank', 'noopener,noreferrer');
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Portal do provedor indisponível',
      description: errorMessage(error, 'Confirme o vínculo e o gate de ações externas.'),
    }),
  });

  const offers = useMemo(() => offersQuery.data?.items ?? [], [offersQuery.data?.items]);
  const baseOffers = offers.filter((offer) => offer.family === 'crm' && offer.snapshot.isActive);
  const legalOffer = offers.find((offer) => offer.family === 'legal' && offer.snapshot.isActive) ?? null;
  const offerBySku = useMemo(() => new Map(offers.map((offer) => [offer.sku, offer])), [offers]);

  useEffect(() => {
    const contract = contractQuery.data;
    if (!contract) return;
    const marker = `${contract.id}:${contract.version}`;
    if (initializedContractVersion.current === marker) return;
    const activeLines = contract.lines.filter((line) => line.status === 'active');
    const core = activeLines.find((line) => line.offerFamily === 'crm');
    const legal = activeLines.find((line) => line.offerFamily === 'legal');
    setBaseSku(core?.sku ?? NO_CORE_SKU);
    setIncludeLegal(Boolean(legal));
    if (legal) {
      setLegalPrice(String(legal.contractedPriceCents / 100));
      setLegalUsers(String(legal.entitlementOverrides.users?.limitQuantity ?? 1));
      setLegalAreas(String(legal.entitlementOverrides.legal_areas?.limitQuantity ?? 1));
      setLegalStorageGb(String((legal.entitlementOverrides.storage_bytes?.limitQuantity ?? 5 * 1024 ** 3) / 1024 ** 3));
      setLegalMonitoredProcesses(String(legal.entitlementOverrides.monitored_processes?.limitQuantity ?? 0));
      setLegalDocuments(String(legal.entitlementOverrides.legal_documents?.limitQuantity ?? ''));
      setLegalProtocolFolders(String(legal.entitlementOverrides.protocol_folders?.limitQuantity ?? ''));
    }
    setPreview(null);
    initializedContractVersion.current = marker;
  }, [contractQuery.data]);

  const contractLines = useMemo<WorkspaceContractLineWrite[]>(() => {
    const lines: WorkspaceContractLineWrite[] = [];
    const activeLines = contractQuery.data?.lines.filter((line) => line.status === 'active') ?? [];
    const currentCore = activeLines.find((line) => line.offerFamily === 'crm' && line.sku === baseSku);
    if (baseSku !== NO_CORE_SKU) lines.push({
      sku: baseSku,
      ...(currentCore ? {
        policyVersionId: currentCore.policyVersionId,
        contractedPriceCents: currentCore.contractedPriceCents,
      } : {}),
      quantity: 1,
      // Existing annual contracts are read-only legacy terms: adding Legal
      // or revising another family must preserve their actual interval.
      billingInterval: currentCore?.billingInterval ?? 'monthly',
      entitlementOverrides: currentCore?.entitlementOverrides ?? {},
    });
    if (includeLegal && legalOffer) {
      const currentLegal = activeLines.find((line) => line.offerFamily === 'legal');
      const priceCents = positiveMoneyCents(legalPrice);
      const users = integerQuantity(legalUsers, 1);
      const areas = integerQuantity(legalAreas, 1);
      const storageBytes = Math.round(Number(legalStorageGb.replace(',', '.')) * 1024 ** 3);
      const monitored = integerQuantity(legalMonitoredProcesses, 0);
      const documents = integerQuantity(legalDocuments, 1);
      const protocolFolders = integerQuantity(legalProtocolFolders, 1);
      const setting = legalOffer.snapshot.entitlements;
      lines.push({
        sku: legalOffer.sku,
        policyVersionId: currentLegal?.policyVersionId ?? legalOffer.id,
        quantity: 1,
        ...(priceCents == null ? {} : { contractedPriceCents: priceCents }),
        billingInterval: currentLegal?.billingInterval ?? 'monthly',
        entitlementOverrides: {
          users: { ...setting.users, enabled: true, limitQuantity: users ?? 0 },
          legal_areas: { ...setting.legal_areas, enabled: true, limitQuantity: areas ?? 0 },
          storage_bytes: { ...setting.storage_bytes, enabled: true, limitQuantity: Number.isFinite(storageBytes) ? storageBytes : 0 },
          monitored_processes: { ...setting.monitored_processes, enabled: true, limitQuantity: monitored ?? 0 },
          legal_documents: { ...setting.legal_documents, enabled: true, limitQuantity: documents ?? 0 },
          protocol_folders: { ...setting.protocol_folders, enabled: true, limitQuantity: protocolFolders ?? 0 },
        },
      });
    }
    return lines;
  }, [baseSku, contractQuery.data?.lines, includeLegal, legalAreas, legalDocuments, legalMonitoredProcesses, legalOffer, legalPrice, legalProtocolFolders, legalStorageGb, legalUsers]);
  const contractFingerprint = JSON.stringify({ workspaceId, version: contractQuery.data?.version ?? null, lines: contractLines });
  const previewIsCurrent = Boolean(preview && preview.fingerprint === contractFingerprint);

  const previewMutation = useMutation({
    mutationFn: () => contractQuery.data
      ? previewWorkspaceCommercialContractRevision(contractQuery.data.id, {
        expectedVersion: contractQuery.data.version,
        lines: contractLines,
      })
      : previewWorkspaceCommercialContract({ workspaceId, lines: contractLines }),
    onSuccess: (data) => setPreview({
      fingerprint: contractFingerprint,
      data,
      mode: contractQuery.data ? 'revision' : 'create',
    }),
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Não foi possível gerar a prévia',
      description: errorMessage(error, 'Revise os produtos selecionados.'),
    }),
  });

  const createContractMutation = useMutation({
    mutationFn: ({ key }: { key: string }) => createWorkspaceCommercialContract({
      workspaceId,
      lines: contractLines,
      status: 'active',
      reason: contractReason.trim(),
    }, key),
    onSuccess: () => {
      contractIntent.current = null;
      setPreview(null);
      setContractReason('');
      void queryClient.invalidateQueries({ queryKey: ['platform', 'commercial', 'workspace', workspaceId] });
      toast({ title: 'Contrato comercial criado.', description: 'As capacidades efetivas foram recalculadas.' });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Contrato não criado',
      description: errorMessage(error, 'A mesma tentativa pode ser repetida com segurança.'),
    }),
  });

  const reviseContractMutation = useMutation({
    mutationFn: ({ key }: { key: string }) => reviseWorkspaceCommercialContract(
      contractQuery.data!.id,
      contractQuery.data!.version,
      { lines: contractLines, reason: contractReason.trim() },
      key,
    ),
    onSuccess: () => {
      contractIntent.current = null;
      setPreview(null);
      setContractReason('');
      void queryClient.invalidateQueries({ queryKey: ['platform', 'commercial', 'workspace', workspaceId] });
      toast({ title: 'Contrato atualizado.', description: 'A nova versão já governa as capacidades da conta.' });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Contrato não atualizado',
      description: errorMessage(error, 'Gere uma nova prévia e tente novamente.'),
    }),
  });

  const restorePreviewMutation = useMutation({
    mutationFn: (targetVersion: number) => previewWorkspaceCommercialContractRestore(
      contractQuery.data!.id,
      targetVersion,
      contractQuery.data!.version,
    ),
    onSuccess: (data) => {
      setRestorePreview(data);
      setRestoreReason('');
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Prévia de restauração não gerada',
      description: errorMessage(error, 'O contrato pode ter mudado.'),
    }),
  });

  const restoreContractMutation = useMutation({
    mutationFn: () => restoreWorkspaceCommercialContract(
      contractQuery.data!.id,
      restorePreview!.targetVersion,
      contractQuery.data!.version,
      restoreReason.trim(),
      commercialIdempotencyKey('workspace-contract-restore'),
    ),
    onSuccess: () => {
      setRestorePreview(null);
      setRestoreReason('');
      initializedContractVersion.current = null;
      void queryClient.invalidateQueries({ queryKey: ['platform', 'commercial', 'workspace', workspaceId] });
      void queryClient.invalidateQueries({ queryKey: ['platform', 'commercial', 'contract'] });
      toast({
        title: 'Contrato restaurado como nova versão.',
        description: 'Descontos, créditos e bônus individuais foram preservados.',
      });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Contrato não restaurado',
      description: errorMessage(error, 'Gere uma nova prévia e tente novamente.'),
    }),
  });

  const adjustmentMutation = useMutation({
    mutationFn: ({ payload, key }: { payload: CommercialAdjustmentCreateRequest; key: string }) =>
      adjustmentForm?.editingId && adjustmentForm.editingVersion
        ? updateCommercialAdjustment(adjustmentForm.editingId, adjustmentForm.editingVersion, payload, key)
        : addCommercialAdjustment(contractQuery.data!.id, payload, key),
    onSuccess: () => {
      setAdjustmentForm(null);
      void queryClient.invalidateQueries({ queryKey: ['platform', 'commercial', 'contract', contractQuery.data?.id, 'adjustments'] });
      void queryClient.invalidateQueries({ queryKey: ['platform', 'commercial', 'workspace', workspaceId] });
      toast({ title: 'Ajuste comercial registrado.' });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Ajuste não registrado', description: errorMessage(error, 'Revise os dados.') }),
  });

  const revokeAdjustmentMutation = useMutation({
    mutationFn: ({ item, reason }: { item: CommercialAdjustment; reason: string }) =>
      revokeCommercialAdjustment(item.id, item.version, reason, commercialIdempotencyKey('revoke-adjustment')),
    onSuccess: () => {
      setAdjustmentRevoke(null);
      void queryClient.invalidateQueries({ queryKey: ['platform', 'commercial', 'contract', contractQuery.data?.id, 'adjustments'] });
      void queryClient.invalidateQueries({ queryKey: ['platform', 'commercial', 'workspace', workspaceId] });
      toast({ title: 'Ajuste revogado.' });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Ajuste não revogado', description: errorMessage(error, 'Recarregue os dados e tente novamente.') }),
  });

  const bonusMutation = useMutation({
    mutationFn: ({ payload, key }: { payload: EntitlementBonusCreateRequest; key: string }) =>
      bonusForm?.editingId && bonusForm.editingVersion
        ? updateEntitlementBonus(bonusForm.editingId, bonusForm.editingVersion, payload, key)
        : addEntitlementBonus(workspaceId, payload, key),
    onSuccess: () => {
      setBonusForm(null);
      void queryClient.invalidateQueries({ queryKey: ['platform', 'commercial', 'workspace', workspaceId] });
      toast({ title: 'Bônus de capacidade registrado.' });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Bônus não registrado', description: errorMessage(error, 'Revise os dados.') }),
  });

  const revokeBonusMutation = useMutation({
    mutationFn: ({ item, reason }: { item: EntitlementBonus; reason: string }) =>
      revokeEntitlementBonus(item.id, item.version, reason, commercialIdempotencyKey('revoke-bonus')),
    onSuccess: () => {
      setBonusRevoke(null);
      void queryClient.invalidateQueries({ queryKey: ['platform', 'commercial', 'workspace', workspaceId] });
      toast({ title: 'Bônus revogado.' });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Bônus não revogado', description: errorMessage(error, 'Recarregue os dados e tente novamente.') }),
  });

  const legalConfigurationValid = !includeLegal || (
    positiveMoneyCents(legalPrice) != null
    && integerQuantity(legalUsers, 1) != null
    && integerQuantity(legalAreas, 1) != null
    && Number(legalStorageGb.replace(',', '.')) > 0
    && integerQuantity(legalMonitoredProcesses, 0) != null
    && integerQuantity(legalDocuments, 1) != null
    && integerQuantity(legalProtocolFolders, 1) != null
  );
  const revisionEligible = preview?.mode !== 'revision'
    || ('eligible' in preview.data && preview.data.eligible);
  const validAdjustmentPayload = adjustmentForm
    ? buildAdjustmentPayload(adjustmentForm)
    : null;
  const validBonusPayload = bonusForm ? buildBonusPayload(bonusForm) : null;

  const confirmContract = () => {
    if (!previewIsCurrent || !revisionEligible || !isCommercialReasonValid(contractReason)) return;
    const fingerprint = JSON.stringify({ contractFingerprint, reason: contractReason.trim() });
    if (contractIntent.current?.fingerprint !== fingerprint) {
      contractIntent.current = { fingerprint, key: commercialIdempotencyKey('workspace-contract') };
    }
    if (contractQuery.data) reviseContractMutation.mutate({ key: contractIntent.current.key });
    else createContractMutation.mutate({ key: contractIntent.current.key });
  };

  const submitAdjustment = () => {
    if (!adjustmentForm || !contractQuery.data || !validAdjustmentPayload) return;
    adjustmentMutation.mutate({
      payload: validAdjustmentPayload,
      key: commercialIdempotencyKey('commercial-adjustment'),
    });
  };

  const editAdjustment = (item: CommercialAdjustment) => setAdjustmentForm({
    editingId: item.id,
    editingVersion: item.version,
    type: item.type,
    value: item.type === 'percentage_discount'
      ? String(item.value / 100)
      : item.type === 'courtesy_period'
        ? '1'
        : String(item.value / 100),
    lineKey: item.contractLineId ?? 'contract',
    startsAt: localDateTime(new Date(item.startsAt)),
    endsAt: item.endsAt ? localDateTime(new Date(item.endsAt)) : '',
    remainingCycles: item.remainingCycles == null ? '' : String(item.remainingCycles),
    stackGroup: item.stackGroup,
    stackable: item.stackable,
    portability: item.portability,
    source: item.source,
    reason: item.reason,
  });

  const submitBonus = () => {
    if (!bonusForm || !validBonusPayload) return;
    bonusMutation.mutate({
      payload: validBonusPayload,
      key: commercialIdempotencyKey('entitlement-bonus'),
    });
  };

  const editBonus = (item: EntitlementBonus) => setBonusForm({
    editingId: item.id,
    editingVersion: item.version,
    entitlement: item.entitlement,
    quantity: item.quantity == null ? '' : String(item.quantity),
    startsAt: localDateTime(new Date(item.startsAt)),
    endsAt: item.endsAt ? localDateTime(new Date(item.endsAt)) : '',
    renewsEachPeriod: item.renewsEachPeriod,
    portability: item.portability,
    source: item.source,
    reason: item.reason,
  });

  const contractEditor = canManageContracts ? <div className="space-y-4 rounded-lg border bg-muted/10 p-4">
    <div>
      <p className="text-sm font-semibold">{contractQuery.data ? 'Revisar contrato vigente' : 'Criar contrato canônico'}</p>
      <p className="text-xs text-muted-foreground">A prévia congela versão, preço, capacidades e impactos antes da confirmação.</p>
    </div>
    <div className="space-y-1">
      <Label htmlFor="workspace-commercial-core-plan">Plano Core</Label>
      <Select disabled={providerStateUnresolved || providerManagesCore} value={baseSku} onValueChange={(value) => { setBaseSku(value); setPreview(null); }}>
        <SelectTrigger id="workspace-commercial-core-plan"><SelectValue placeholder="Selecione o plano" /></SelectTrigger>
        <SelectContent>
          <SelectItem value={NO_CORE_SKU}>Somente Jurídico 360 (sem plano Core)</SelectItem>
          {baseOffers.map((offer) => <SelectItem key={offer.sku} value={offer.sku}>{offerLabel(offer)} · {commercialMoney(offer.snapshot.priceMonthlyCents)}/mês</SelectItem>)}
        </SelectContent>
      </Select>
      {baseSku === NO_CORE_SKU && <p className="text-xs text-muted-foreground">Nenhum módulo Core será incluído. Ative e configure o Jurídico 360 para continuar.</p>}
      {providerManagesCore && <p className="text-xs text-muted-foreground">O plano Core é administrado no provedor {providerQuery.data?.provider ?? 'externo'} e reconciliado pelo webhook assinado.</p>}
    </div>
    {legalOffer && <div className="space-y-3 rounded-lg border p-3">
      <label className="flex items-center justify-between gap-3 text-sm">
        <span><span className="flex items-center gap-1.5 font-medium"><Scale className="h-4 w-4 text-primary" /> Jurídico 360</span><span className="mt-1 block text-xs text-muted-foreground">Upgrade sob proposta, com limites apenas onde há enforcement operacional.</span></span>
        <Switch aria-label="Adicionar Jurídico 360" checked={includeLegal} onCheckedChange={(checked) => { setIncludeLegal(checked); setPreview(null); }} />
      </label>
      {includeLegal && <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <div className="space-y-1"><Label htmlFor="legal-price">Preço contratado (R$)</Label><Input id="legal-price" inputMode="decimal" required aria-invalid={positiveMoneyCents(legalPrice) == null} value={legalPrice} onChange={(event) => { setLegalPrice(event.target.value); setPreview(null); }} /><p className="text-xs text-muted-foreground">Obrigatório e maior que zero. Cortesias e descontos são registrados separadamente.</p></div>
        <div className="space-y-1"><Label htmlFor="legal-users">Usuários</Label><Input id="legal-users" type="number" min={1} step={1} value={legalUsers} onChange={(event) => { setLegalUsers(event.target.value); setPreview(null); }} /></div>
        <div className="space-y-1"><Label htmlFor="legal-areas">Áreas jurídicas</Label><Input id="legal-areas" type="number" min={1} step={1} value={legalAreas} onChange={(event) => { setLegalAreas(event.target.value); setPreview(null); }} /></div>
        <div className="space-y-1"><Label htmlFor="legal-storage">Armazenamento (GB)</Label><Input id="legal-storage" inputMode="decimal" value={legalStorageGb} onChange={(event) => { setLegalStorageGb(event.target.value); setPreview(null); }} /></div>
        <div className="space-y-1"><Label htmlFor="legal-monitored">Processos monitorados</Label><Input id="legal-monitored" type="number" min={0} step={1} value={legalMonitoredProcesses} onChange={(event) => { setLegalMonitoredProcesses(event.target.value); setPreview(null); }} /></div>
        <div className="space-y-1"><Label htmlFor="legal-documents">Documentos jurídicos</Label><Input id="legal-documents" type="number" min={1} step={1} value={legalDocuments} onChange={(event) => { setLegalDocuments(event.target.value); setPreview(null); }} placeholder="Defina na proposta" /></div>
        <div className="space-y-1"><Label htmlFor="legal-protocol-folders">Pastas de protocolo</Label><Input id="legal-protocol-folders" type="number" min={1} step={1} value={legalProtocolFolders} onChange={(event) => { setLegalProtocolFolders(event.target.value); setPreview(null); }} placeholder="Defina na proposta" /></div>
      </div>}
    </div>}
    {!legalConfigurationValid && <p className="text-xs text-destructive">Informe preço, usuários, áreas, armazenamento, processos, documentos e pastas de protocolo válidos.</p>}
    <Button variant="outline" disabled={!contractLines.length || !legalConfigurationValid || previewMutation.isPending} onClick={() => previewMutation.mutate()}>
      {previewMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Gerar prévia e impacto
    </Button>
    {previewIsCurrent && preview && <div className="space-y-2 rounded-lg bg-muted/40 p-3 text-xs">
      <p className="font-medium">{preview.data.lines.map((line) => offerBySku.get(line.sku)?.snapshot.name ?? line.sku).join(' + ')}</p>
      {'currentPrice' in preview.data && <p>Antes: <strong>{commercialMoney(preview.data.currentPrice.recurringPriceCents)}</strong></p>}
      <p>Depois: <strong>{commercialMoney(preview.data.price.recurringPriceCents)}</strong></p>
      <p>{Object.values(preview.data.manifest.capabilities).filter((item) => item.contracted).length} capacidades contratadas.</p>
      {preview.data.warnings.map((warning) => <p key={warning} className="text-amber-700 dark:text-amber-300">{warning}</p>)}
      {'blockers' in preview.data && preview.data.blockers.map((blocker) => <p key={blocker} className="text-destructive">{blocker}</p>)}
    </div>}
    <div className="space-y-1"><Label htmlFor="workspace-commercial-contract-reason">Motivo da alteração contratual</Label><Textarea id="workspace-commercial-contract-reason" rows={2} maxLength={MAX_COMMERCIAL_REASON_LENGTH} value={contractReason} onChange={(event) => setContractReason(event.target.value)} placeholder="Registre a proposta ou decisão comercial" /><p className="text-xs text-muted-foreground">Informe entre {MIN_COMMERCIAL_REASON_LENGTH} e {MAX_COMMERCIAL_REASON_LENGTH} caracteres.</p></div>
    <Button disabled={!previewIsCurrent || !revisionEligible || !isCommercialReasonValid(contractReason) || createContractMutation.isPending || reviseContractMutation.isPending} onClick={confirmContract}>
      {(createContractMutation.isPending || reviseContractMutation.isPending) && <Loader2 className="h-4 w-4 animate-spin" />} {contractQuery.data ? 'Aplicar nova versão' : 'Confirmar contrato'}
    </Button>
  </div> : null;

  if (!canView) return null;

  return <section className="space-y-4" aria-labelledby="workspace-commercial-heading">
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <BadgeDollarSign className="h-4 w-4 text-primary" aria-hidden="true" />
        <h3 id="workspace-commercial-heading" className="text-sm font-semibold">Contrato e capacidades efetivas</h3>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">Core, Jurídico 360, preço contratado, ajustes e bônus desta conta.</p>
    </div>

    {requestsQuery.isError ? <Alert variant="destructive">
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>Solicitações indisponíveis</AlertTitle>
      <AlertDescription>Não foi possível consultar as solicitações comerciais desta conta.</AlertDescription>
    </Alert> : Boolean(requestsQuery.data?.items.length) && <div className="space-y-2 rounded-lg border p-3">
      <p className="text-sm font-semibold">Solicitações da conta</p>
      {requestsQuery.data!.items.map((item) => <div key={item.id} className="rounded-md bg-muted/30 p-3 text-xs">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div><p className="font-medium">{item.requestType.replaceAll('_', ' ')}</p><p className="text-muted-foreground">{item.reason}</p></div>
          <Badge variant={item.status === 'pending' ? 'secondary' : 'outline'}>{item.status}</Badge>
        </div>
        {item.status === 'pending' && canManageContracts && <div className="mt-2 flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={resolveRequestMutation.isPending} onClick={() => resolveRequestMutation.mutate({ item, status: 'rejected', note: 'Solicitação rejeitada após revisão comercial.' })}>Rejeitar</Button>
          {providerManagesCore && ['change_base_plan', 'cancel_contract'].includes(item.requestType)
            ? isPlatformRoot && providerQuery.data?.provider !== 'stripe'
              ? <Button
                size="sm"
                disabled={executeProviderRequestMutation.isPending}
                onClick={() => {
                  if (
                    item.requestType !== 'cancel_contract'
                    || window.confirm('Cancelar a assinatura no provedor e encerrar o contrato?')
                  ) executeProviderRequestMutation.mutate(item);
                }}
              >
                {executeProviderRequestMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                Executar no {providerQuery.data?.provider === 'asaas' ? 'Asaas' : 'Mercado Pago'}
              </Button>
              : isPlatformRoot
                ? <Button size="sm" disabled={resolveRequestMutation.isPending} onClick={() => resolveRequestMutation.mutate({ item, status: 'resolved', note: 'Alteração aplicada pelo portal e conferida no contrato canônico.' })}>Conferir e concluir</Button>
                : <span className="text-muted-foreground">Aguardando execução pelo Super Admin.</span>
            : <Button size="sm" disabled={resolveRequestMutation.isPending} onClick={() => resolveRequestMutation.mutate({ item, status: 'resolved', note: 'Alteração aplicada e conferida no contrato canônico.' })}>Marcar concluída</Button>}
        </div>}
      </div>)}
    </div>}

    {contractQuery.isLoading || offersQuery.isLoading ? <div className="space-y-2" role="status" aria-label="Carregando contrato comercial">
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-36 w-full" />
    </div> : contractQuery.isError || offersQuery.isError ? <Alert variant="destructive">
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>Dados comerciais indisponíveis</AlertTitle>
      <AlertDescription>{errorMessage(contractQuery.error ?? offersQuery.error, 'Não foi possível consultar o contrato.')}</AlertDescription>
    </Alert> : contractQuery.data ? <div className="space-y-4">
      <div className="rounded-lg border p-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="text-sm font-medium">Contrato versão {contractQuery.data.version}</p>
            <p className="mt-1 text-xs text-muted-foreground">{contractQuery.data.lines.filter((line) => line.status === 'active').map((line) => offerBySku.get(line.sku)?.snapshot.name ?? line.sku).join(' + ')}</p>
          </div>
          <Badge variant={contractQuery.data.status === 'active' ? 'secondary' : 'outline'}>{contractQuery.data.status}</Badge>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
          <div className="rounded-md bg-muted/40 p-2"><p className="text-muted-foreground">Preço de lista</p><p className="font-semibold tabular-nums">{commercialMoney(contractQuery.data.price.listPriceCents)}</p></div>
          <div className="rounded-md bg-muted/40 p-2"><p className="text-muted-foreground">Preço recorrente</p><p className="font-semibold tabular-nums">{commercialMoney(contractQuery.data.price.recurringPriceCents)}</p></div>
        </div>
      </div>

      {manifestQuery.isLoading ? <Skeleton className="h-32 w-full" /> : manifestQuery.isError ? <Alert variant="destructive">
        <AlertTriangle className="h-4 w-4" /><AlertTitle>Manifesto indisponível</AlertTitle><AlertDescription>O contrato existe, mas as capacidades efetivas não puderam ser resolvidas.</AlertDescription>
      </Alert> : manifestQuery.data && <div className="space-y-2">
        <div className="flex flex-wrap gap-1.5">{manifestQuery.data.activeProducts.map((product) => <Badge key={product}>{offerBySku.get(product)?.snapshot.name ?? product}</Badge>)}</div>
        <div className="space-y-2">
          {Object.values(manifestQuery.data.capabilities).filter((capability) => capability.contracted || capability.used != null).map((capability) => {
            const denominator = capability.graceLimit ?? capability.limit;
            const progress = denominator && capability.used != null ? Math.min(100, (capability.used / denominator) * 100) : 0;
            return <div key={capability.key} className="rounded-lg border p-3 text-xs">
              <div className="flex items-start justify-between gap-2">
                <div><p className="font-medium">{COMMERCIAL_ENTITLEMENT_LABELS[capability.key]}</p><p className="text-muted-foreground">{capability.period} · {capability.unit} · {capability.enforcement === 'hard' ? 'bloqueio' : 'tolerância finita'}</p></div>
                <Badge variant={capability.permitted ? 'secondary' : 'outline'}>{capability.permitted ? 'permitido' : capability.denialReason ?? 'bloqueado'}</Badge>
              </div>
              {capability.limit != null && <><Progress className="mt-2 h-1.5" value={progress} /><p className="mt-1 tabular-nums text-muted-foreground">{capability.used ?? 'Sem medição'} / {capability.limit}{capability.graceLimit != null ? ` + tolerância até ${capability.graceLimit}` : ''}{capability.remaining != null ? ` · ${capability.remaining} restantes` : ''}</p></>}
            </div>;
          })}
        </div>
      </div>}

      <Alert>
        <CheckCircle2 className="h-4 w-4" />
        <AlertTitle>Contrato canônico e versionado</AlertTitle>
        <AlertDescription>Trocas de Core e inclusão ou remoção do Jurídico 360 geram prévia e uma nova versão no mesmo contrato.</AlertDescription>
      </Alert>
      {providerQuery.isError ? <Alert variant="destructive">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Estado da cobrança indisponível</AlertTitle>
        <AlertDescription>Não altere o plano Core até confirmar o vínculo com o provedor.</AlertDescription>
      </Alert> : providerManagesCore && <Alert>
        <BadgeDollarSign className="h-4 w-4" />
        <AlertTitle>Cobrança Core gerida pelo provedor</AlertTitle>
        <AlertDescription className="space-y-2">
          <p>{providerQuery.data.providerMessage}</p>
          <p>Plano e cancelamento seguem o fluxo do provedor vinculado. Jurídico 360, descontos e bônus continuam versionados aqui.</p>
          {canRestoreContract && <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!providerQuery.data.billingPortalAvailable || billingPortalMutation.isPending}
            onClick={() => billingPortalMutation.mutate()}
          >
            {billingPortalMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ExternalLink className="h-4 w-4" />}
            Abrir portal do provedor
          </Button>}
        </AlertDescription>
      </Alert>}
      <section className="space-y-3 rounded-lg border p-3" aria-labelledby="workspace-contract-history-heading">
        <div>
          <h4 id="workspace-contract-history-heading" className="text-sm font-semibold">Histórico contratual</h4>
          <p className="text-xs text-muted-foreground">Cada restauração cria nova versão e preserva descontos, créditos e bônus individuais.</p>
        </div>
        {historyQuery.isLoading ? <Skeleton className="h-16 w-full" /> : historyQuery.isError ? <p className="text-xs text-destructive">Histórico contratual indisponível.</p> : <div className="space-y-2">
          {historyQuery.data?.items.map((item) => <div key={item.eventId} className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-muted/30 p-2 text-xs">
            <div>
              <p className="font-medium">Versão {item.version} · {item.eventType}</p>
              <p className="text-muted-foreground">{item.reason} · {item.diff.length} campo(s) alterado(s)</p>
            </div>
            {canRestoreContract && item.version !== contractQuery.data.version && <Button
              size="sm"
              variant="outline"
              disabled={restorePreviewMutation.isPending}
              onClick={() => restorePreviewMutation.mutate(item.version)}
            >
              Prévia de restauração
            </Button>}
          </div>)}
        </div>}
        {restorePreview && <div className="space-y-3 rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-xs">
          <p className="font-medium">Restaurar versão {restorePreview.targetVersion} como versão {restorePreview.currentVersion + 1}</p>
          <div className="flex flex-wrap gap-1.5">
            <Badge variant={restorePreview.preservesAdjustments ? 'secondary' : 'destructive'}>
              {restorePreview.preservesAdjustments ? 'Descontos preservados' : 'Descontos requerem intervenção'}
            </Badge>
            <Badge variant={restorePreview.preservesBonuses ? 'secondary' : 'destructive'}>
              {restorePreview.preservesBonuses ? 'Bônus preservados' : 'Bônus requerem intervenção'}
            </Badge>
            <Badge variant="outline">{restorePreview.diff.length} alteração(ões)</Badge>
          </div>
          {providerManagesCore && (restorePreview.revision.changedFamilies.includes('crm') || restorePreview.targetSnapshot.status !== contractQuery.data.status) && <p className="text-destructive">Esta versão altera o plano ou estado Core vinculado ao provedor. Faça a alteração pelo fluxo de cobrança correspondente.</p>}
          {restorePreview.revision.warnings.map((warning) => <p key={warning} className="text-amber-700 dark:text-amber-300">{warning}</p>)}
          {restorePreview.revision.blockers.map((blocker) => <p key={blocker} className="text-destructive">{blocker}</p>)}
          <div className="space-y-1">
            <Label htmlFor="workspace-contract-restore-reason">Motivo da restauração</Label>
            <Textarea id="workspace-contract-restore-reason" rows={2} maxLength={MAX_COMMERCIAL_REASON_LENGTH} value={restoreReason} onChange={(event) => setRestoreReason(event.target.value)} />
            <p className="text-xs text-muted-foreground">Informe entre {MIN_COMMERCIAL_REASON_LENGTH} e {MAX_COMMERCIAL_REASON_LENGTH} caracteres.</p>
          </div>
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => { setRestorePreview(null); setRestoreReason(''); }}>Cancelar</Button>
            <Button
              size="sm"
              variant="destructive"
              disabled={!restorePreview.revision.eligible || !isCommercialReasonValid(restoreReason) || restoreContractMutation.isPending || (providerManagesCore && (restorePreview.revision.changedFamilies.includes('crm') || restorePreview.targetSnapshot.status !== contractQuery.data.status))}
              onClick={() => restoreContractMutation.mutate()}
            >
              {restoreContractMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Confirmar restauração
            </Button>
          </div>
        </div>}
      </section>
      {contractEditor}
    </div> : <div className="space-y-4 rounded-lg border border-dashed p-4">
      <div>
        <p className="text-sm font-medium">Nenhum contrato canônico</p>
        <p className="text-xs text-muted-foreground">A conta ainda usa apenas o estado comercial legado ou não foi contratada.</p>
      </div>
      {contractEditor}
    </div>}

    {contractQuery.data && canViewAdjustments && <div className="space-y-3 border-t pt-4">
      <div className="flex items-center justify-between gap-3"><div><h4 className="text-sm font-semibold">Ajustes comerciais</h4><p className="text-xs text-muted-foreground">Descontos e créditos não alteram as quotas.</p></div>{canManageAdjustments && <Button size="sm" variant="outline" onClick={() => setAdjustmentForm({ type: 'percentage_discount', value: '', lineKey: 'contract', startsAt: localDateTime(), endsAt: '', remainingCycles: '', stackGroup: 'negociacao', stackable: false, portability: 'preserve', source: 'manual', reason: '' })}><Plus className="h-4 w-4" />Adicionar</Button>}</div>
      {adjustmentsQuery.isLoading ? <Skeleton className="h-20 w-full" /> : adjustmentsQuery.isError ? <p className="text-xs text-destructive">Não foi possível carregar os ajustes.</p> : adjustmentsQuery.data?.length ? <div className="space-y-2">{adjustmentsQuery.data.map((adjustment) => {
        const requiresIntervention = adjustment.remainingCycles != null || adjustment.portability === 'recalculate' || (adjustment.type === 'courtesy_period' && adjustment.value !== 1);
        return <div key={adjustment.id} className="rounded-lg border p-3 text-xs"><div className="flex justify-between gap-2"><p className="font-medium">{adjustmentLabel(adjustment.type)}</p><Badge variant="outline">{adjustment.status}</Badge></div><p className="mt-1">{adjustmentDisplay(adjustment.type, adjustment.value)}</p><p className="text-muted-foreground">{adjustment.reason}</p>{requiresIntervention && <p className="mt-1 text-amber-700 dark:text-amber-300">Regra legada requer intervenção antes de edição ou troca de oferta.</p>}{canManageAdjustments && !['revoked', 'expired'].includes(adjustment.status) && <div className="mt-2 flex gap-2">{!requiresIntervention && <Button size="sm" variant="outline" onClick={() => editAdjustment(adjustment)}>Editar</Button>}<Button size="sm" variant="ghost" onClick={() => setAdjustmentRevoke({ item: adjustment, reason: '' })}>Revogar</Button></div>}</div>;
      })}</div> : <p className="text-xs text-muted-foreground">Nenhum ajuste comercial.</p>}
      {adjustmentRevoke && <div className="space-y-2 rounded-lg border border-destructive/30 p-3"><Label htmlFor="revoke-adjustment-reason">Motivo da revogação</Label><Textarea id="revoke-adjustment-reason" rows={2} maxLength={MAX_COMMERCIAL_REASON_LENGTH} value={adjustmentRevoke.reason} onChange={(event) => setAdjustmentRevoke({ ...adjustmentRevoke, reason: event.target.value })} /><p className="text-xs text-muted-foreground">Informe entre {MIN_COMMERCIAL_REASON_LENGTH} e {MAX_COMMERCIAL_REASON_LENGTH} caracteres.</p><div className="flex justify-end gap-2"><Button size="sm" variant="ghost" onClick={() => setAdjustmentRevoke(null)}>Cancelar</Button><Button size="sm" variant="destructive" disabled={!isCommercialReasonValid(adjustmentRevoke.reason) || revokeAdjustmentMutation.isPending} onClick={() => revokeAdjustmentMutation.mutate({ item: adjustmentRevoke.item, reason: adjustmentRevoke.reason.trim() })}>Confirmar revogação</Button></div></div>}
      {adjustmentForm && <div className="space-y-3 rounded-lg border bg-muted/20 p-3">
        <div className="grid gap-2 sm:grid-cols-2"><div className="space-y-1"><Label htmlFor="commercial-adjustment-type">Tipo</Label><Select value={adjustmentForm.type} onValueChange={(type: CommercialAdjustmentType) => setAdjustmentForm({ ...adjustmentForm, type, value: type === 'courtesy_period' ? '1' : '', endsAt: type === 'courtesy_period' ? adjustmentForm.endsAt : '', remainingCycles: '' })}><SelectTrigger id="commercial-adjustment-type"><SelectValue /></SelectTrigger><SelectContent>{(['percentage_discount', 'fixed_discount', 'negotiated_price', 'courtesy_period', 'invoice_credit'] as CommercialAdjustmentType[]).map((type) => <SelectItem key={type} value={type}>{adjustmentLabel(type)}</SelectItem>)}</SelectContent></Select></div>{adjustmentForm.type === 'courtesy_period' ? <div className="space-y-1"><Label>Cobertura</Label><p className="rounded-md border bg-background px-3 py-2 text-sm">Cortesia integral entre início e fim.</p></div> : <div className="space-y-1"><Label htmlFor="commercial-adjustment-value">{adjustmentForm.type === 'percentage_discount' ? 'Percentual' : 'Valor (R$)'}</Label><Input id="commercial-adjustment-value" inputMode="decimal" value={adjustmentForm.value} onChange={(event) => setAdjustmentForm({ ...adjustmentForm, value: event.target.value })} /></div>}</div>
        <div className="space-y-1"><Label htmlFor="commercial-adjustment-target">Aplicar em</Label><Select value={adjustmentForm.lineKey} onValueChange={(lineKey) => setAdjustmentForm({ ...adjustmentForm, lineKey })}><SelectTrigger id="commercial-adjustment-target"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="contract">Contrato inteiro</SelectItem>{contractQuery.data.lines.filter((line) => line.id && line.status === 'active').map((line) => <SelectItem key={line.id!} value={line.id!}>{offerBySku.get(line.sku)?.snapshot.name ?? line.sku}</SelectItem>)}</SelectContent></Select></div>
        <div className="grid gap-2 sm:grid-cols-2"><div className="space-y-1"><Label htmlFor="commercial-adjustment-start">Início</Label><Input id="commercial-adjustment-start" type="datetime-local" value={adjustmentForm.startsAt} onChange={(event) => setAdjustmentForm({ ...adjustmentForm, startsAt: event.target.value })} /></div>{adjustmentForm.type === 'courtesy_period' && <div className="space-y-1"><Label htmlFor="commercial-adjustment-end">Fim obrigatório</Label><Input id="commercial-adjustment-end" type="datetime-local" value={adjustmentForm.endsAt} onChange={(event) => setAdjustmentForm({ ...adjustmentForm, endsAt: event.target.value })} /></div>}</div>
        <div className="space-y-1"><Label htmlFor="commercial-adjustment-reason">Motivo do ajuste</Label><Textarea id="commercial-adjustment-reason" rows={2} maxLength={MAX_COMMERCIAL_REASON_LENGTH} value={adjustmentForm.reason} onChange={(event) => setAdjustmentForm({ ...adjustmentForm, reason: event.target.value })} /><p className="text-xs text-muted-foreground">Informe entre {MIN_COMMERCIAL_REASON_LENGTH} e {MAX_COMMERCIAL_REASON_LENGTH} caracteres.</p></div>
        <div className="flex justify-end gap-2"><Button size="sm" variant="ghost" onClick={() => setAdjustmentForm(null)}>Cancelar</Button><Button size="sm" disabled={adjustmentMutation.isPending || !validAdjustmentPayload} onClick={submitAdjustment}>{adjustmentForm.editingId ? 'Salvar ajuste' : 'Registrar ajuste'}</Button></div>
      </div>}
    </div>}

    {canViewBonuses && <div className="space-y-3 border-t pt-4">
      <div className="flex items-center justify-between gap-3"><div><h4 className="text-sm font-semibold">Bônus de capacidade</h4><p className="text-xs text-muted-foreground">Capacidade adicional sem alterar preço.</p></div>{canManageBonuses && <Button size="sm" variant="outline" onClick={() => setBonusForm({ entitlement: 'ai_executions', quantity: '', startsAt: localDateTime(), endsAt: '', renewsEachPeriod: false, portability: 'preserve', source: 'manual', reason: '' })}><Plus className="h-4 w-4" />Adicionar</Button>}</div>
      {bonusesQuery.isLoading ? <Skeleton className="h-20 w-full" /> : bonusesQuery.isError ? <p className="text-xs text-destructive">Não foi possível carregar os bônus.</p> : bonusesQuery.data?.length ? <div className="space-y-2">{bonusesQuery.data.map((bonus) => {
        const requiresIntervention = bonus.portability === 'recalculate';
        return <div key={bonus.id} className="rounded-lg border p-3 text-xs"><div className="flex justify-between gap-2"><p className="font-medium">{COMMERCIAL_ENTITLEMENT_LABELS[bonus.entitlement]}</p><Badge variant="outline">{bonus.status}</Badge></div><p className="mt-1">{bonus.mode === 'feature_grant' ? 'Função concedida' : `+ ${bonus.quantity ?? 0}`}</p><p className="text-muted-foreground">{bonus.reason}</p>{requiresIntervention && <p className="mt-1 text-amber-700 dark:text-amber-300">Regra legada de recálculo requer intervenção.</p>}{canManageBonuses && !['revoked', 'expired'].includes(bonus.status) && <div className="mt-2 flex gap-2">{!requiresIntervention && <Button size="sm" variant="outline" onClick={() => editBonus(bonus)}>Editar</Button>}<Button size="sm" variant="ghost" onClick={() => setBonusRevoke({ item: bonus, reason: '' })}>Revogar</Button></div>}</div>;
      })}</div> : <p className="text-xs text-muted-foreground">Nenhum bônus de capacidade.</p>}
      {bonusRevoke && <div className="space-y-2 rounded-lg border border-destructive/30 p-3"><Label htmlFor="revoke-bonus-reason">Motivo da revogação</Label><Textarea id="revoke-bonus-reason" rows={2} maxLength={MAX_COMMERCIAL_REASON_LENGTH} value={bonusRevoke.reason} onChange={(event) => setBonusRevoke({ ...bonusRevoke, reason: event.target.value })} /><p className="text-xs text-muted-foreground">Informe entre {MIN_COMMERCIAL_REASON_LENGTH} e {MAX_COMMERCIAL_REASON_LENGTH} caracteres.</p><div className="flex justify-end gap-2"><Button size="sm" variant="ghost" onClick={() => setBonusRevoke(null)}>Cancelar</Button><Button size="sm" variant="destructive" disabled={!isCommercialReasonValid(bonusRevoke.reason) || revokeBonusMutation.isPending} onClick={() => revokeBonusMutation.mutate({ item: bonusRevoke.item, reason: bonusRevoke.reason.trim() })}>Confirmar revogação</Button></div></div>}
      {bonusForm && <div className="space-y-3 rounded-lg border bg-muted/20 p-3">
        <div className="space-y-1"><Label htmlFor="commercial-bonus-entitlement">Capacidade</Label><Select value={bonusForm.entitlement} onValueChange={(entitlement: CommercialEntitlementKey) => setBonusForm({ ...bonusForm, entitlement, quantity: FEATURE_ENTITLEMENTS.has(entitlement) ? '' : bonusForm.quantity })}><SelectTrigger id="commercial-bonus-entitlement"><SelectValue /></SelectTrigger><SelectContent>{COMMERCIAL_ENTITLEMENT_KEYS.filter((key) => key !== 'legal_suite' && !UNAVAILABLE_BONUS_ENTITLEMENTS.has(key)).map((key) => <SelectItem key={key} value={key}>{COMMERCIAL_ENTITLEMENT_LABELS[key]}</SelectItem>)}</SelectContent></Select></div>
        {!FEATURE_ENTITLEMENTS.has(bonusForm.entitlement) && <div className="space-y-1"><Label htmlFor="commercial-bonus-quantity">Quantidade adicional</Label><Input id="commercial-bonus-quantity" type="number" min={0} value={bonusForm.quantity} onChange={(event) => setBonusForm({ ...bonusForm, quantity: event.target.value })} /></div>}
        <div className="grid gap-2 sm:grid-cols-2"><div className="space-y-1"><Label htmlFor="commercial-bonus-start">Início</Label><Input id="commercial-bonus-start" type="datetime-local" value={bonusForm.startsAt} onChange={(event) => setBonusForm({ ...bonusForm, startsAt: event.target.value })} /></div><div className="space-y-1"><Label htmlFor="commercial-bonus-end">{bonusForm.renewsEachPeriod ? 'Fim opcional' : 'Fim obrigatório'}</Label><Input id="commercial-bonus-end" type="datetime-local" required={!bonusForm.renewsEachPeriod} value={bonusForm.endsAt} onChange={(event) => setBonusForm({ ...bonusForm, endsAt: event.target.value })} /></div></div>
        <label className="flex items-center justify-between gap-3 rounded-lg border p-2 text-xs">Renovar a cada período<Switch aria-label="Renovar bônus a cada período" checked={bonusForm.renewsEachPeriod} onCheckedChange={(renewsEachPeriod) => setBonusForm({ ...bonusForm, renewsEachPeriod })} /></label>
        <div className="space-y-1"><Label htmlFor="commercial-bonus-reason">Motivo do bônus</Label><Textarea id="commercial-bonus-reason" rows={2} maxLength={MAX_COMMERCIAL_REASON_LENGTH} value={bonusForm.reason} onChange={(event) => setBonusForm({ ...bonusForm, reason: event.target.value })} /><p className="text-xs text-muted-foreground">Informe entre {MIN_COMMERCIAL_REASON_LENGTH} e {MAX_COMMERCIAL_REASON_LENGTH} caracteres.</p></div>
        <div className="flex justify-end gap-2"><Button size="sm" variant="ghost" onClick={() => setBonusForm(null)}>Cancelar</Button><Button size="sm" disabled={bonusMutation.isPending || !validBonusPayload} onClick={submitBonus}>{bonusForm.editingId ? 'Salvar bônus' : 'Registrar bônus'}</Button></div>
      </div>}
    </div>}
  </section>;
}
