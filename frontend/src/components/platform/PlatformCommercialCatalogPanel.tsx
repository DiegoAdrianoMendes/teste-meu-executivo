import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  BadgeDollarSign,
  CheckCircle2,
  Layers3,
  Loader2,
  RefreshCw,
  Scale,
  Sparkles,
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
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  bootstrapCommercialOffers,
  commercialIdempotencyKey,
  commercialMoney,
  COMMERCIAL_ENTITLEMENT_KEYS,
  COMMERCIAL_ENTITLEMENT_LABELS,
  discardCommercialOfferDraft,
  getCommercialOfferDraft,
  getCommercialOfferHistory,
  listCommercialContracts,
  listCommercialOffers,
  previewCommercialOfferDraft,
  previewCommercialOfferRestore,
  publishCommercialOfferDraft,
  restoreCommercialOffer,
  saveCommercialOfferDraft,
  type CommercialEntitlementKey,
  type CommercialEntitlementKeyV2,
  type CommercialEntitlementSetting,
  type CommercialOfferDraftPreview,
  type CommercialOfferSnapshot,
  type CommercialOfferSnapshotWrite,
  type CommercialOfferVersion,
} from '@/services/platform-commercial-catalog';
import { ApiError } from '@/services/http';

const catalogQueryKey = ['platform', 'commercial-catalog'] as const;
const CONTRACT_PAGE_SIZE = 10;
const MIN_COMMERCIAL_REASON_LENGTH = 8;

const COLLABORATION_KEYS = new Set<CommercialEntitlementKey>([
  'active_tasks',
  'task_participants',
  'task_reviews',
  'review_rounds',
  'task_reminder_rules',
  'meeting_participants',
]);

const LEGAL_KEYS = new Set<CommercialEntitlementKey>([
  'legal_suite',
  'legal_areas',
  'legal_cases',
  'djen_terms',
  'legal_ai_generations',
  'legal_documents',
  'legal_automations',
  'legal_templates',
  'legal_signatures',
  'protocol_folders',
  'legal_bulk_operations',
  'legal_audit',
  'legal_integrations',
  'monitored_processes',
]);

// These keys have a commercial vocabulary, but no proven runtime consumer yet.
// They remain visible for catalog completeness and fail closed until the
// corresponding domain enforces the entitlement at its operational boundary.
const UNAVAILABLE_LEGAL_KEYS = new Set<CommercialEntitlementKey>([
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

interface CatalogImpact {
  changedFields: string[];
  warnings: string[];
}

const LEGACY_V2_CONTACTS_SETTING: CommercialEntitlementSetting = {
  enabled: false,
  unit: 'contacts',
  period: 'stock',
  limitQuantity: null,
  enforcement: 'hard',
  graceQuantity: null,
  composition: 'maximum',
};

export interface PlatformCommercialCatalogPanelProps {
  canViewContracts?: boolean;
  canDraft?: boolean;
  canPreview?: boolean;
  canRestore?: boolean;
  isPlatformRoot?: boolean;
}

function cloneSnapshot(snapshot: CommercialOfferSnapshot): CommercialOfferSnapshotWrite {
  return {
    ...snapshot,
    // Editing a frozen V2 snapshot creates an explicit V3 draft. The published
    // V2 revision remains unchanged and contacts stay disabled until configured.
    schemaVersion: '3.0',
    availableIntervals: [...snapshot.availableIntervals],
    entitlements: Object.fromEntries(
      COMMERCIAL_ENTITLEMENT_KEYS.map((key) => {
        const setting = snapshot.entitlements[key]
          ?? (snapshot.schemaVersion === '2.0' && key === 'contacts'
            ? LEGACY_V2_CONTACTS_SETTING
            : null);
        if (!setting) {
          throw new Error(`Snapshot comercial sem definição para ${key}.`);
        }
        return [key, { ...setting }];
      }),
    ) as CommercialOfferSnapshotWrite['entitlements'],
  };
}

function snapshotFingerprint(snapshot: CommercialOfferSnapshot | null): string {
  return snapshot ? JSON.stringify(snapshot) : '';
}

function entitlementGroup(key: CommercialEntitlementKey): 'Core' | 'Colaboração' | 'Jurídico' {
  if (LEGAL_KEYS.has(key)) return 'Jurídico';
  if (COLLABORATION_KEYS.has(key)) return 'Colaboração';
  return 'Core';
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Data indisponível';
  return parsed.toLocaleString('pt-BR');
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function entitlementSetting(
  snapshot: CommercialOfferSnapshot,
  key: CommercialEntitlementKey,
): CommercialEntitlementSetting | undefined {
  if (snapshot.schemaVersion === '2.0') {
    return key === 'contacts'
      ? LEGACY_V2_CONTACTS_SETTING
      : snapshot.entitlements[key as CommercialEntitlementKeyV2];
  }
  return snapshot.entitlements[key];
}

function buildImpact(before: CommercialOfferSnapshot, after: CommercialOfferSnapshot): CatalogImpact {
  const changedFields: string[] = [];
  const warnings: string[] = [];
  const identityFields: Array<keyof CommercialOfferSnapshot> = [
    'name',
    'description',
    'isPublic',
    'isActive',
    'isRecommended',
    'isQuoteOnly',
    'priceMonthlyCents',
    'priceAnnualCents',
    'cta',
    'sortOrder',
  ];
  identityFields.forEach((field) => {
    if (JSON.stringify(before[field]) !== JSON.stringify(after[field])) changedFields.push(String(field));
  });
  COMMERCIAL_ENTITLEMENT_KEYS.forEach((key) => {
    const previous = entitlementSetting(before, key);
    const next = entitlementSetting(after, key);
    if (JSON.stringify(previous) === JSON.stringify(next)) return;
    changedFields.push(COMMERCIAL_ENTITLEMENT_LABELS[key]);
    if (previous?.enabled && !next?.enabled) {
      warnings.push(`${COMMERCIAL_ENTITLEMENT_LABELS[key]} será desativado para novas contratações desta revisão.`);
    }
    if (
      previous?.limitQuantity != null
      && next?.limitQuantity != null
      && next.limitQuantity < previous.limitQuantity
    ) {
      warnings.push(`O limite de ${COMMERCIAL_ENTITLEMENT_LABELS[key]} foi reduzido; contas existentes não são reprificadas automaticamente.`);
    }
  });
  return { changedFields, warnings };
}

function validateSnapshot(
  snapshot: CommercialOfferSnapshotWrite | null,
  family: CommercialOfferVersion['family'] | null,
): string | null {
  if (!snapshot) return 'Selecione uma oferta.';
  if (!snapshot.name.trim()) return 'Informe o nome da oferta.';
  if (!snapshot.cta.trim()) return 'Informe o texto da ação comercial.';
  if (!snapshot.isQuoteOnly && snapshot.priceMonthlyCents == null) return 'Informe o preço mensal.';
  for (const key of COMMERCIAL_ENTITLEMENT_KEYS) {
    const setting = snapshot.entitlements[key];
    if (!setting) return `A política não contém ${COMMERCIAL_ENTITLEMENT_LABELS[key]}.`;
    if (family === 'legal' && UNAVAILABLE_LEGAL_KEYS.has(key) && setting.enabled) {
      return `${COMMERCIAL_ENTITLEMENT_LABELS[key]} ainda não possui enforcement operacional e não pode ser ofertado.`;
    }
    if (!setting.enabled && (setting.limitQuantity != null || setting.graceQuantity != null)) {
      return `${COMMERCIAL_ENTITLEMENT_LABELS[key]} está desativado, mas ainda possui limite.`;
    }
    if (setting.period === 'feature' && (setting.limitQuantity != null || setting.graceQuantity != null)) {
      return `${COMMERCIAL_ENTITLEMENT_LABELS[key]} é uma função booleana e não aceita quantidade.`;
    }
    if (setting.enabled && setting.period !== 'feature' && setting.limitQuantity == null) {
      return `${COMMERCIAL_ENTITLEMENT_LABELS[key]} exige limite finito.`;
    }
    if (setting.enforcement === 'grace' && (setting.limitQuantity == null || setting.graceQuantity == null)) {
      return `${COMMERCIAL_ENTITLEMENT_LABELS[key]} exige limite e tolerância finitos.`;
    }
  }
  return null;
}

function EntitlementEditor({
  entitlementKey,
  setting,
  disabled,
  unavailableReason,
  onChange,
}: {
  entitlementKey: CommercialEntitlementKey;
  setting: CommercialEntitlementSetting;
  disabled: boolean;
  unavailableReason?: string;
  onChange: (value: CommercialEntitlementSetting) => void;
}) {
  const label = COMMERCIAL_ENTITLEMENT_LABELS[entitlementKey];
  const quantitative = setting.period !== 'feature';
  return (
    <div className="space-y-3 rounded-lg border p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-medium">{label}</p>
            <Badge variant="outline" className="text-2xs">{entitlementGroup(entitlementKey)}</Badge>
            {unavailableReason && <Badge variant="secondary" className="text-2xs">Sem enforcement</Badge>}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {unavailableReason
              ? unavailableReason
              : setting.enabled
              ? quantitative
                ? `${setting.limitQuantity ?? 'Sem teto'} ${setting.unit} · ${setting.period}`
                : 'Função habilitada'
              : 'Não incluído'}
          </p>
        </div>
        <Switch
          aria-label={`Incluir ${label}`}
          checked={setting.enabled}
          disabled={disabled || (Boolean(unavailableReason) && !setting.enabled)}
          onCheckedChange={(enabled) => onChange({
            ...setting,
            enabled,
            limitQuantity: enabled ? setting.limitQuantity : null,
            graceQuantity: enabled ? setting.graceQuantity : null,
          })}
        />
      </div>

      {setting.enabled && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-md bg-muted/40 p-2 text-xs">
            <p className="text-muted-foreground">Unidade canônica</p>
            <p className="mt-1 font-medium">{setting.unit}</p>
          </div>
          <div className="rounded-md bg-muted/40 p-2 text-xs">
            <p className="text-muted-foreground">Período e composição</p>
            <p className="mt-1 font-medium">{setting.period} · {setting.composition}</p>
          </div>
          {quantitative && (
            <div className="space-y-1">
              <Label htmlFor={`commercial-limit-${entitlementKey}`}>Limite</Label>
              <Input
                id={`commercial-limit-${entitlementKey}`}
                type="number"
                min={0}
                value={setting.limitQuantity ?? ''}
                disabled={disabled || Boolean(unavailableReason)}
                onChange={(event) => onChange({
                  ...setting,
                  limitQuantity: event.target.value === '' ? null : Math.max(0, Math.trunc(Number(event.target.value))),
                })}
              />
            </div>
          )}
          {quantitative && (
            <div className="space-y-1">
              <Label htmlFor={`commercial-enforcement-${entitlementKey}`}>Aplicação</Label>
              <Select
                value={setting.enforcement}
                disabled={disabled || Boolean(unavailableReason)}
                onValueChange={(enforcement: CommercialEntitlementSetting['enforcement']) => onChange({
                  ...setting,
                  enforcement,
                  graceQuantity: enforcement === 'hard' ? null : setting.graceQuantity ?? 0,
                })}
              >
                <SelectTrigger id={`commercial-enforcement-${entitlementKey}`}><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="hard">Bloqueio no limite</SelectItem>
                  <SelectItem value="grace">Tolerância finita</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
          {quantitative && setting.enforcement === 'grace' && (
            <div className="space-y-1">
              <Label htmlFor={`commercial-grace-${entitlementKey}`}>Tolerância adicional</Label>
              <Input
                id={`commercial-grace-${entitlementKey}`}
                type="number"
                min={0}
                value={setting.graceQuantity ?? ''}
                disabled={disabled || Boolean(unavailableReason)}
                onChange={(event) => onChange({
                  ...setting,
                  graceQuantity: event.target.value === '' ? null : Math.max(0, Math.trunc(Number(event.target.value))),
                })}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function PlatformCommercialCatalogPanel({
  canViewContracts = false,
  canDraft = false,
  canPreview = false,
  canRestore = false,
  isPlatformRoot = false,
}: PlatformCommercialCatalogPanelProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [selectedSku, setSelectedSku] = useState('');
  const [draft, setDraft] = useState<CommercialOfferSnapshotWrite | null>(null);
  const [savedFingerprint, setSavedFingerprint] = useState('');
  const [reason, setReason] = useState('');
  const [serverPreview, setServerPreview] = useState<CommercialOfferDraftPreview | null>(null);
  const [restoreReason, setRestoreReason] = useState('');
  const [restoreTargetVersion, setRestoreTargetVersion] = useState<number | null>(null);
  const [contractPage, setContractPage] = useState(0);

  const offersQuery = useQuery({
    queryKey: [...catalogQueryKey, 'offers'],
    queryFn: ({ signal }) => listCommercialOffers({ signal }),
    staleTime: 30_000,
  });
  const contractsQuery = useQuery({
    queryKey: [...catalogQueryKey, 'contracts', contractPage],
    queryFn: ({ signal }) => listCommercialContracts({
      limit: CONTRACT_PAGE_SIZE,
      offset: contractPage * CONTRACT_PAGE_SIZE,
      signal,
    }),
    enabled: canViewContracts,
    staleTime: 15_000,
  });
  const persistedDraftQuery = useQuery({
    queryKey: [...catalogQueryKey, 'draft', selectedSku],
    queryFn: async ({ signal }) => {
      try {
        return await getCommercialOfferDraft(selectedSku, signal);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(selectedSku && canDraft),
    retry: false,
  });
  const historyQuery = useQuery({
    queryKey: [...catalogQueryKey, 'history', selectedSku],
    queryFn: ({ signal }) => getCommercialOfferHistory(selectedSku, signal),
    enabled: Boolean(selectedSku),
    staleTime: 15_000,
  });

  const offers = useMemo(
    () => [...(offersQuery.data?.items ?? [])].sort((left, right) => left.snapshot.sortOrder - right.snapshot.sortOrder),
    [offersQuery.data?.items],
  );
  const selectedOffer = offers.find((offer) => offer.sku === selectedSku) ?? null;
  const draftFingerprint = snapshotFingerprint(draft);
  const isDirty = Boolean(draft && draftFingerprint !== savedFingerprint);
  const impact = useMemo(
    () => selectedOffer && draft ? buildImpact(selectedOffer.snapshot, draft) : null,
    [draft, selectedOffer],
  );
  const previewIsCurrent = Boolean(
    serverPreview
    && persistedDraftQuery.data
    && serverPreview.draft.draftVersion === persistedDraftQuery.data.draftVersion,
  );
  const validationError = validateSnapshot(draft, selectedOffer?.family ?? null);

  useEffect(() => {
    if (!selectedSku && offers.length) setSelectedSku(offers[0].sku);
    if (selectedSku && !offers.some((offer) => offer.sku === selectedSku)) {
      setSelectedSku(offers[0]?.sku ?? '');
    }
  }, [offers, selectedSku]);

  useEffect(() => {
    if (!selectedOffer || (canDraft && persistedDraftQuery.isLoading)) {
      if (!selectedOffer) {
        setDraft(null);
        setSavedFingerprint('');
      }
      return;
    }
    const next = cloneSnapshot(persistedDraftQuery.data?.snapshot ?? selectedOffer.snapshot);
    setDraft(next);
    setSavedFingerprint(snapshotFingerprint(next));
    setReason(persistedDraftQuery.data?.reason ?? '');
    setServerPreview(null);
    setRestoreReason('');
    setRestoreTargetVersion(null);
  }, [canDraft, persistedDraftQuery.data, persistedDraftQuery.isLoading, selectedOffer]);

  const bootstrapMutation = useMutation({
    mutationFn: () => bootstrapCommercialOffers(commercialIdempotencyKey('catalog-bootstrap')),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: catalogQueryKey });
      toast({
        title: result.idempotentReplay ? 'Catálogo já existia.' : 'Catálogo inicial criado.',
        description: `${result.items.length} ofertas canônicas disponíveis.`,
      });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Não foi possível criar o catálogo',
      description: errorMessage(error, 'Tente novamente.'),
    }),
  });

  const saveDraftMutation = useMutation({
    mutationFn: ({ offer, snapshot, draftReason }: {
      offer: CommercialOfferVersion;
      snapshot: CommercialOfferSnapshotWrite;
      draftReason: string;
    }) => saveCommercialOfferDraft(
      offer.sku,
      persistedDraftQuery.data?.draftVersion ?? null,
      {
        expectedPublishedVersion: offer.version,
        snapshot,
        reason: draftReason,
      },
      commercialIdempotencyKey('offer-draft-save'),
    ),
    onSuccess: (result) => {
      setSavedFingerprint(snapshotFingerprint(result.draft.snapshot));
      setServerPreview(null);
      // The command response is the canonical persisted draft. FastAPI 0.115
      // can finish a yield-dependency commit after the response body becomes
      // readable, so an immediate refetch may still observe the previous 404
      // and erase the successful result from the editor.
      queryClient.setQueryData(
        [...catalogQueryKey, 'draft', selectedSku],
        result.draft,
      );
      toast({
        title: result.idempotentReplay ? 'Rascunho já estava salvo.' : `Rascunho v${result.draft.draftVersion} salvo.`,
        description: 'O catálogo publicado e o enforcement ainda não mudaram.',
      });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Rascunho não salvo',
      description: errorMessage(error, 'Recarregue a oferta e tente novamente.'),
    }),
  });

  const previewMutation = useMutation({
    mutationFn: (sku: string) => previewCommercialOfferDraft(sku),
    onSuccess: setServerPreview,
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Prévia não gerada',
      description: errorMessage(error, 'Salve o rascunho antes de gerar a prévia.'),
    }),
  });

  const discardMutation = useMutation({
    mutationFn: ({ sku, draftVersion, discardReason }: {
      sku: string;
      draftVersion: number;
      discardReason: string;
    }) => discardCommercialOfferDraft(
      sku,
      draftVersion,
      discardReason,
      commercialIdempotencyKey('offer-draft-discard'),
    ),
    onSuccess: () => {
      setServerPreview(null);
      setReason('');
      void queryClient.invalidateQueries({ queryKey: catalogQueryKey });
      toast({ title: 'Rascunho descartado', description: 'A evidência histórica foi preservada.' });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Rascunho não descartado',
      description: errorMessage(error, 'Recarregue e tente novamente.'),
    }),
  });

  const publishMutation = useMutation({
    mutationFn: ({ sku, draftVersion, publicationReason }: {
      sku: string;
      draftVersion: number;
      publicationReason: string;
    }) => publishCommercialOfferDraft(
      sku,
      draftVersion,
      publicationReason,
      commercialIdempotencyKey('offer-draft-publish'),
    ),
    onSuccess: (result) => {
      setReason('');
      setServerPreview(null);
      void queryClient.invalidateQueries({ queryKey: catalogQueryKey });
      toast({
        title: result.idempotentReplay
          ? `Revisão ${result.offer.version} confirmada.`
          : `Revisão ${result.offer.version} publicada.`,
        description: 'Contratos vigentes mantêm a revisão e o preço já contratados.',
      });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Publicação não concluída',
      description: errorMessage(error, 'A tentativa pode ser repetida com a mesma chave.'),
    }),
  });

  const restorePreviewMutation = useMutation({
    mutationFn: ({ sku, targetVersion, currentVersion }: {
      sku: string;
      targetVersion: number;
      currentVersion: number;
    }) => previewCommercialOfferRestore(sku, targetVersion, currentVersion),
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Prévia de restauração não gerada',
      description: errorMessage(error, 'A oferta pode ter mudado.'),
    }),
  });

  const restoreMutation = useMutation({
    mutationFn: ({ sku, targetVersion, currentVersion, restorationReason }: {
      sku: string;
      targetVersion: number;
      currentVersion: number;
      restorationReason: string;
    }) => restoreCommercialOffer(
      sku,
      targetVersion,
      currentVersion,
      restorationReason,
      commercialIdempotencyKey('offer-restore'),
    ),
    onSuccess: (result) => {
      setRestoreReason('');
      setRestoreTargetVersion(null);
      restorePreviewMutation.reset();
      void queryClient.invalidateQueries({ queryKey: catalogQueryKey });
      toast({
        title: `Revisão ${result.offer.version} publicada por restauração.`,
        description: 'Contratos vigentes não foram reprificados.',
      });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Restauração não concluída',
      description: errorMessage(error, 'Gere uma nova prévia e tente novamente.'),
    }),
  });

  const publish = () => {
    const persisted = persistedDraftQuery.data;
    if (
      !isPlatformRoot
      || !selectedOffer
      || !persisted
      || !previewIsCurrent
      || !serverPreview?.eligible
      || validationError
      || reason.trim().length < MIN_COMMERCIAL_REASON_LENGTH
    ) return;
    publishMutation.mutate({
      sku: selectedOffer.sku,
      draftVersion: persisted.draftVersion,
      publicationReason: reason.trim(),
    });
  };

  if (offersQuery.isLoading) {
    return <div className="space-y-4" role="status" aria-label="Carregando catálogo comercial">
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-72 w-full" />
    </div>;
  }

  if (offersQuery.isError) {
    return <Alert variant="destructive">
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>Catálogo comercial indisponível</AlertTitle>
      <AlertDescription className="mt-2 space-y-3">
        <p>{errorMessage(offersQuery.error, 'Não foi possível consultar as ofertas.')}</p>
        <Button size="sm" variant="outline" onClick={() => offersQuery.refetch()}>
          <RefreshCw className="h-4 w-4" /> Tentar novamente
        </Button>
      </AlertDescription>
    </Alert>;
  }

  if (!offers.length) {
    return <Card className="border-dashed">
      <CardHeader>
        <CardTitle className="text-lg">Catálogo ainda não inicializado</CardTitle>
        <CardDescription>
          Não há oferta persistida. Nenhum preço ou limite fictício será exibido.
        </CardDescription>
      </CardHeader>
      {isPlatformRoot && <CardContent>
        <Button disabled={bootstrapMutation.isPending} onClick={() => bootstrapMutation.mutate()}>
          {bootstrapMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Criar catálogo inicial
        </Button>
      </CardContent>}
    </Card>;
  }

  return <div className="space-y-7">
    <header className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <BadgeDollarSign className="h-5 w-5 text-primary" aria-hidden="true" />
        <h2 className="text-xl font-semibold">Catálogo comercial canônico</h2>
        <Badge variant={isPlatformRoot ? 'default' : 'outline'}>
          {isPlatformRoot ? 'Publicação root' : 'Consulta por permissão'}
        </Badge>
      </div>
      <p className="max-w-4xl text-sm text-muted-foreground">
        Uma única fonte para oferta, preço, módulos e limites. Publicar cria uma revisão imutável e não reprifica contratos vigentes.
      </p>
    </header>

    <section aria-label="Ofertas comerciais" className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      {offers.map((offer) => {
        const selected = selectedSku === offer.sku;
        return <button
          key={offer.sku}
          type="button"
          aria-pressed={selected}
          onClick={() => setSelectedSku(offer.sku)}
          className={`rounded-xl border p-4 text-left transition ${selected ? 'border-primary bg-primary/5 ring-1 ring-primary/30' : 'hover:border-primary/40'}`}
        >
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="font-semibold">{offer.snapshot.name}</p>
              <p className="mt-1 text-xs text-muted-foreground">{offer.snapshot.description}</p>
            </div>
            {offer.snapshot.isRecommended && <Badge>Recomendado</Badge>}
          </div>
          <p className="mt-4 text-2xl font-semibold tabular-nums">
            {offer.snapshot.isQuoteOnly ? 'Sob consulta' : commercialMoney(offer.snapshot.priceMonthlyCents)}
          </p>
          {!offer.snapshot.isQuoteOnly && <p className="text-xs text-muted-foreground">por mês</p>}
          <div className="mt-3 flex flex-wrap gap-1.5">
            <Badge variant="outline">{offer.family === 'legal' ? 'Jurídico' : 'Core'}</Badge>
            <Badge variant={offer.snapshot.isPublic ? 'secondary' : 'outline'}>
              {offer.snapshot.isPublic ? 'Pública' : 'Interna'}
            </Badge>
            <Badge variant="secondary">revisão {offer.version}</Badge>
          </div>
        </button>;
      })}
    </section>

    {selectedOffer && draft && <Card>
      <CardHeader className="border-b">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-lg">
              {selectedOffer.family === 'legal' ? <Scale className="h-4 w-4 text-primary" /> : <Layers3 className="h-4 w-4 text-primary" />}
              {draft.name}
            </CardTitle>
            <CardDescription>
              Revisão {selectedOffer.version}, publicada em {formatDate(selectedOffer.publishedAt)}.
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            {persistedDraftQuery.data && <Badge variant="secondary">
              Rascunho v{persistedDraftQuery.data.draftVersion}
            </Badge>}
            {isDirty && <Badge variant="outline">Alterações ainda não salvas</Badge>}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-7 pt-6">
        <fieldset disabled={!canDraft || saveDraftMutation.isPending} className="space-y-6">
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <div className="space-y-1 xl:col-span-2">
              <Label htmlFor="commercial-offer-name">Nome</Label>
              <Input id="commercial-offer-name" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="commercial-offer-price">Preço mensal (R$)</Label>
              <Input
                id="commercial-offer-price"
                inputMode="decimal"
                disabled={draft.isQuoteOnly || !canDraft}
                value={draft.priceMonthlyCents == null ? '' : (draft.priceMonthlyCents / 100).toFixed(2)}
                onChange={(event) => {
                  const parsed = Number(event.target.value.replace(',', '.'));
                  setDraft({ ...draft, priceMonthlyCents: Number.isFinite(parsed) ? Math.round(parsed * 100) : null });
                }}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="commercial-offer-cta">Ação comercial</Label>
              <Input id="commercial-offer-cta" value={draft.cta} onChange={(event) => setDraft({ ...draft, cta: event.target.value })} />
            </div>
            <div className="space-y-1 md:col-span-2 xl:col-span-4">
              <Label htmlFor="commercial-offer-description">Descrição</Label>
              <Textarea id="commercial-offer-description" rows={2} value={draft.description ?? ''} onChange={(event) => setDraft({ ...draft, description: event.target.value || null })} />
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {([
              ['isActive', 'Oferta ativa'],
              ['isPublic', 'Visível comercialmente'],
              ['isRecommended', 'Marcar como recomendada'],
              ['isQuoteOnly', 'Preço sob consulta'],
            ] as const).map(([field, label]) => <label key={field} className="flex items-center justify-between gap-3 rounded-lg border p-3 text-sm">
              {label}
              <Switch
                aria-label={label}
                checked={draft[field]}
                onCheckedChange={(checked) => setDraft({
                  ...draft,
                  [field]: checked,
                  ...(field === 'isQuoteOnly' && checked ? { priceMonthlyCents: null, priceAnnualCents: null } : {}),
                })}
              />
            </label>)}
          </div>

          <section className="space-y-3" aria-labelledby="commercial-entitlements-heading">
            <div>
              <h3 id="commercial-entitlements-heading" className="font-semibold">Módulos, colaboração e limites</h3>
              <p className="text-sm text-muted-foreground">
                O catálogo técnico fixa unidade, período e composição. Aqui se editam inclusão, limite e comportamento no teto. Zero é um limite real, não ausência de dado.
              </p>
            </div>
            <div className="space-y-3">
              {COMMERCIAL_ENTITLEMENT_KEYS.map((key) => <EntitlementEditor
                key={key}
                entitlementKey={key}
                setting={draft.entitlements[key]}
                disabled={!canDraft || saveDraftMutation.isPending}
                unavailableReason={selectedOffer.family === 'legal' && UNAVAILABLE_LEGAL_KEYS.has(key)
                  ? 'Indisponível para venda até existir consumo e bloqueio operacional comprovados.'
                  : undefined}
                onChange={(setting) => setDraft({
                  ...draft,
                  entitlements: { ...draft.entitlements, [key]: setting },
                })}
              />)}
            </div>
          </section>
        </fieldset>

        {validationError && <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Revisão inválida</AlertTitle>
          <AlertDescription>{validationError}</AlertDescription>
        </Alert>}

        {isDirty && <section className="space-y-3 rounded-lg border bg-muted/20 p-4" aria-labelledby="commercial-local-impact-heading">
          <div>
            <h3 id="commercial-local-impact-heading" className="font-semibold">Alterações locais</h3>
            <p className="text-sm text-muted-foreground">
              Salvar cria um rascunho persistido, mas não muda a oferta publicada nem o enforcement.
            </p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {impact?.changedFields.map((field) => <Badge key={field} variant="outline">{field}</Badge>)}
          </div>
          {impact?.warnings.map((warning) => <p key={warning} className="text-xs text-amber-700 dark:text-amber-300">{warning}</p>)}
        </section>}

        {canDraft ? <div className="space-y-4 border-t pt-5">
          <div className="space-y-1">
            <Label htmlFor="commercial-publication-reason">Motivo da alteração</Label>
            <Textarea
              id="commercial-publication-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Explique a mudança comercial e de capacidade"
              rows={2}
            />
            <p className="text-xs text-muted-foreground">
              Use ao menos {MIN_COMMERCIAL_REASON_LENGTH} caracteres para manter a justificativa auditável.
            </p>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={discardMutation.isPending || (!isDirty && !persistedDraftQuery.data)}
              onClick={() => {
                if (persistedDraftQuery.data) {
                  discardMutation.mutate({
                    sku: selectedOffer.sku,
                    draftVersion: persistedDraftQuery.data.draftVersion,
                    discardReason: reason.trim().length >= MIN_COMMERCIAL_REASON_LENGTH
                      ? reason.trim()
                      : 'Descartar rascunho comercial.',
                  });
                } else {
                  const reset = cloneSnapshot(selectedOffer.snapshot);
                  setDraft(reset);
                  setSavedFingerprint(snapshotFingerprint(reset));
                  setServerPreview(null);
                  setReason('');
                }
              }}
            >
              Descartar rascunho
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={
                !isDirty
                || Boolean(validationError)
                || reason.trim().length < MIN_COMMERCIAL_REASON_LENGTH
                || saveDraftMutation.isPending
              }
              onClick={() => saveDraftMutation.mutate({
                offer: selectedOffer,
                snapshot: cloneSnapshot(draft),
                draftReason: reason.trim(),
              })}
            >
              {saveDraftMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Salvar rascunho
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={!canPreview || isDirty || !persistedDraftQuery.data || previewMutation.isPending}
              onClick={() => previewMutation.mutate(selectedOffer.sku)}
            >
              {previewMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Gerar prévia da revisão
            </Button>
            {isPlatformRoot && <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  disabled={
                    !previewIsCurrent
                    || !serverPreview?.eligible
                    || Boolean(validationError)
                    || reason.trim().length < MIN_COMMERCIAL_REASON_LENGTH
                    || publishMutation.isPending
                  }
                >
                  {publishMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                  Publicar nova revisão
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Publicar a revisão de {draft.name}?</AlertDialogTitle>
                  <AlertDialogDescription>
                    A revisão será imutável. Novas contratações usarão estes limites; contratos vigentes não serão reprificados.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancelar</AlertDialogCancel>
                  <AlertDialogAction onClick={publish}>Confirmar publicação</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>}
          </div>
          {serverPreview && <section className="space-y-2 rounded-lg border bg-muted/20 p-4" aria-labelledby="commercial-impact-heading">
            <h3 id="commercial-impact-heading" className="font-semibold">Prévia persistida</h3>
            <p className="flex items-center gap-2 text-sm font-medium text-emerald-700 dark:text-emerald-300">
              <CheckCircle2 className="h-4 w-4" /> {serverPreview.diff.length} campo(s) alterado(s)
            </p>
            <div className="flex flex-wrap gap-1.5">
              {serverPreview.diff.map((field) => <Badge key={field.path} variant="outline">{field.label}</Badge>)}
            </div>
            {serverPreview.warnings.map((warning) => <p key={warning} className="text-xs text-amber-700 dark:text-amber-300">{warning}</p>)}
            <div className="mt-3 space-y-2 border-t pt-3" aria-label="Gate de unit economics">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-semibold">Unit economics da IA</p>
                <Badge variant={serverPreview.unitEconomics.eligible ? 'secondary' : 'destructive'}>
                  {serverPreview.unitEconomics.status === 'ready' ? 'Evidência suficiente'
                    : serverPreview.unitEconomics.status === 'not_required' ? 'Não exigido'
                      : serverPreview.unitEconomics.status === 'unprofitable' ? 'Custo acima do teto'
                        : 'Evidência insuficiente'}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground">
                Método versionado: <code>{serverPreview.unitEconomics.assessmentVersion}</code>
              </p>
              {serverPreview.unitEconomics.requiresEvidence && <p className="text-xs text-muted-foreground">
                {serverPreview.unitEconomics.observedCalls ?? 0} de {serverPreview.unitEconomics.minimumConfirmedCalls} chamadas confirmadas · {' '}
                {serverPreview.unitEconomics.observedDays ?? 0} de {serverPreview.unitEconomics.minimumDistinctDays} dias · {' '}
                {(serverPreview.unitEconomics.observedTokens ?? 0).toLocaleString('pt-BR')} de {serverPreview.unitEconomics.minimumObservedTokens.toLocaleString('pt-BR')} tokens
              </p>}
              {serverPreview.unitEconomics.blockers.map((blocker) => <p key={blocker.code} className="text-xs text-destructive">
                {blocker.message}
              </p>)}
              {serverPreview.unitEconomics.diagnostics.map((diagnostic) => <p key={diagnostic} className="text-xs text-muted-foreground">
                {diagnostic}
              </p>)}
            </div>
          </section>}
          {!isPlatformRoot && <Alert>
            <Sparkles className="h-4 w-4" />
            <AlertTitle>Fluxo com separação de funções</AlertTitle>
            <AlertDescription>Você pode salvar e validar o rascunho. Somente o Super Admin publica a revisão.</AlertDescription>
          </Alert>}
        </div> : <Alert>
          <Sparkles className="h-4 w-4" />
          <AlertTitle>Consulta segura</AlertTitle>
          <AlertDescription>Seu perfil pode consultar as revisões, mas não editar rascunhos.</AlertDescription>
        </Alert>}

        <section className="space-y-3 border-t pt-5" aria-labelledby="commercial-history-heading">
          <div>
            <h3 id="commercial-history-heading" className="font-semibold">Histórico e restauração</h3>
            <p className="text-sm text-muted-foreground">Restaurar sempre cria uma nova revisão e não reprifica contratos vigentes.</p>
          </div>
          {historyQuery.isLoading ? <Skeleton className="h-24 w-full" /> : historyQuery.isError ? <p className="text-sm text-destructive">Histórico indisponível.</p> : <div className="space-y-2">
            {historyQuery.data?.items.map((item) => <div key={item.offer.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3 text-sm">
              <div>
                <p className="font-medium">Revisão {item.offer.version} · {item.offer.snapshot.name}</p>
                <p className="text-xs text-muted-foreground">{formatDate(item.offer.publishedAt)} · {item.offer.publicationReason}</p>
                <p className="mt-1 text-xs text-muted-foreground">{item.diff.length} campo(s) alterado(s)</p>
              </div>
              {canRestore && item.offer.version !== selectedOffer.version && <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setRestoreTargetVersion(item.offer.version);
                  restorePreviewMutation.mutate({
                    sku: selectedOffer.sku,
                    targetVersion: item.offer.version,
                    currentVersion: selectedOffer.version,
                  });
                }}
              >
                Preparar restauração
              </Button>}
            </div>)}
          </div>}
          {restoreTargetVersion != null && restorePreviewMutation.data && <div className="space-y-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-4">
            <p className="font-medium">Restaurar a revisão {restoreTargetVersion} como nova revisão</p>
            <p className="text-sm text-muted-foreground">{restorePreviewMutation.data.diff.length} campo(s) serão revertidos. Contratos permanecem presos às versões já contratadas.</p>
            <Label htmlFor="commercial-restore-reason">Motivo da restauração</Label>
            <Textarea id="commercial-restore-reason" value={restoreReason} onChange={(event) => setRestoreReason(event.target.value)} />
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  variant="destructive"
                  disabled={
                    !restorePreviewMutation.data.eligible
                    || restoreReason.trim().length < MIN_COMMERCIAL_REASON_LENGTH
                    || restoreMutation.isPending
                  }
                >
                  Restaurar como nova revisão
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Confirmar restauração da revisão {restoreTargetVersion}?</AlertDialogTitle>
                  <AlertDialogDescription>A ação é auditada e cria uma revisão imutável adicional.</AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancelar</AlertDialogCancel>
                  <AlertDialogAction onClick={() => restoreMutation.mutate({
                    sku: selectedOffer.sku,
                    targetVersion: restoreTargetVersion,
                    currentVersion: selectedOffer.version,
                    restorationReason: restoreReason.trim(),
                  })}>Confirmar restauração</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>}
        </section>
      </CardContent>
    </Card>}

    {canViewContracts && <section className="space-y-3" aria-labelledby="commercial-contracts-heading">
      <div>
        <h2 id="commercial-contracts-heading" className="text-lg font-semibold">Contratos canônicos</h2>
        <p className="text-sm text-muted-foreground">Linhas Core e Jurídico, preço contratado e ajustes ficam separados das ofertas públicas.</p>
      </div>
      {contractsQuery.isLoading ? <Skeleton className="h-40 w-full" /> : contractsQuery.isError ? <Alert variant="destructive">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Contratos indisponíveis</AlertTitle>
        <AlertDescription>{errorMessage(contractsQuery.error, 'Não foi possível carregar os contratos.')}</AlertDescription>
      </Alert> : !contractsQuery.data?.items.length ? <p className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground">Nenhum contrato canônico foi criado.</p> : <>
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full min-w-[780px] text-left text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th className="p-3">Conta</th><th className="p-3">Produtos</th><th className="p-3">Status</th><th className="p-3">Recorrência</th><th className="p-3">Atualização</th></tr></thead>
            <tbody>{contractsQuery.data.items.map((contract) => <tr key={contract.id} className="border-t">
              <td className="p-3 font-medium">{contract.workspaceLabel}</td>
              <td className="p-3"><div className="flex flex-wrap gap-1">{contract.lines.map((line) => <Badge key={`${line.sku}-${line.version}`} variant="outline">{offers.find((offer) => offer.sku === line.sku)?.snapshot.name ?? line.sku}</Badge>)}</div></td>
              <td className="p-3"><Badge variant={contract.status === 'active' ? 'secondary' : 'outline'}>{contract.status}</Badge></td>
              <td className="p-3 tabular-nums">{commercialMoney(contract.price.recurringPriceCents)}</td>
              <td className="p-3 text-xs text-muted-foreground">{formatDate(contract.updatedAt)}</td>
            </tr>)}</tbody>
          </table>
        </div>
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">{contractsQuery.data.total} contrato(s)</p>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" disabled={contractPage === 0 || contractsQuery.isFetching} onClick={() => setContractPage((page) => Math.max(0, page - 1))}>Anterior</Button>
            <Button size="sm" variant="outline" disabled={!contractsQuery.data.hasMore || contractsQuery.isFetching} onClick={() => setContractPage((page) => page + 1)}>Próxima</Button>
          </div>
        </div>
      </>}
    </section>}
  </div>;
}
