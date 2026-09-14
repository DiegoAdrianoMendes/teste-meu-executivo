import { useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  BadgeCheck,
  CirclePause,
  CirclePlay,
  Clock3,
  DatabaseZap,
  ExternalLink,
  Eye,
  FileInput,
  FlaskConical,
  GitBranch,
  Globe2,
  Layers3,
  Loader2,
  MessageCircleMore,
  PenLine,
  RefreshCw,
  RotateCcw,
  Settings2,
  ShieldCheck,
  Smartphone,
  Target,
  Upload,
  XCircle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useContextualNavigate } from '@/klm/hooks/useContextualRouting';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  KlmMetricCard,
  KlmPanel,
  KlmPill,
  KlmSectionTitle,
} from '@/klm/components/Primitives';
import {
  klmApi,
  type KlmAdsManagerEntity,
  type KlmChannelConnection,
  type KlmLeadHydrationJob,
  type KlmOmnichannelChannel,
} from '@/klm/api';
import { formatBRL, formatDecimal } from '@/lib/formatters';
import { cn } from '@/lib/utils';

type AcquisitionProvider = 'meta_ads' | 'tiktok_ads' | 'site' | 'lovable';
type AdsProviderFilter = 'all' | 'meta_ads' | 'tiktok_ads';

const providerProfiles: Record<AcquisitionProvider, {
  label: string;
  channel: KlmOmnichannelChannel;
  source: string;
  campaign: string;
  icon: typeof Target;
}> = {
  meta_ads: {
    label: 'Meta Ads',
    channel: 'facebook',
    source: 'Meta Lead Ads',
    campaign: 'Negativas de plano de saúde',
    icon: Target,
  },
  tiktok_ads: {
    label: 'TikTok Ads',
    channel: 'tiktok',
    source: 'TikTok Instant Form',
    campaign: 'Direito à saúde',
    icon: MessageCircleMore,
  },
  site: {
    label: 'Formulários do site',
    channel: 'webchat',
    source: 'Site próprio',
    campaign: 'Formulário orgânico',
    icon: Globe2,
  },
  lovable: {
    label: 'Site Lovable',
    channel: 'webchat',
    source: 'Lovable',
    campaign: 'Landing page saúde',
    icon: FileInput,
  },
};

const acquisitionProviders = new Set(Object.keys(providerProfiles));
const entityLabels: Record<KlmAdsManagerEntity['entityType'], string> = {
  account: 'Conta',
  campaign: 'Campanha',
  ad_set: 'Conjunto',
  ad: 'Anúncio',
  creative: 'Criativo',
  form: 'Formulário',
};
const entityDepth: Record<KlmAdsManagerEntity['entityType'], number> = {
  account: 0,
  campaign: 1,
  ad_set: 2,
  ad: 3,
  creative: 4,
  form: 4,
};

function providerLabel(value: string) {
  return providerProfiles[value as AcquisitionProvider]?.label ?? value.replaceAll('_', ' ');
}

function connectionTone(connection: KlmChannelConnection) {
  if (connection.status === 'paused' || connection.status === 'disconnected_local') return 'muted' as const;
  if (connection.health.state === 'local_configuration_complete' || connection.transportMode === 'local_fixture') return 'green' as const;
  return 'gold' as const;
}

function stateTone(state: string) {
  if (['completed', 'hydrated', 'configured_local', 'active', 'scheduled'].includes(state)) return 'green' as const;
  if (['failed', 'cancelled'].includes(state)) return 'red' as const;
  if (['queued', 'processing', 'retrying', 'blocked_external'].includes(state)) return 'gold' as const;
  return 'muted' as const;
}

function stateLabel(state: string) {
  const labels: Record<string, string> = {
    blocked_external: 'aguarda conexão oficial',
    awaiting_connection: 'aguarda canal de saída',
    queued: 'na fila',
    processing: 'processando',
    retrying: 'nova tentativa',
    hydrated: 'dados recebidos',
    completed: 'concluído',
    failed: 'falhou',
    cancelled: 'cancelado',
    active: 'ativo',
    paused: 'pausado',
    configured_local: 'disponível',
  };
  return labels[state] ?? state.replaceAll('_', ' ');
}

function connectionDescription(provider: AcquisitionProvider, connected: boolean) {
  if (!connected) return 'Conexão ainda não configurada.';
  const descriptions: Record<AcquisitionProvider, string> = {
    meta_ads: 'Conta principal de anúncios da Meta.',
    tiktok_ads: 'Conta principal de anúncios do TikTok.',
    site: 'Formulários recebidos automaticamente pelo site.',
    lovable: 'Formulários recebidos pela página publicada.',
  };
  return descriptions[provider];
}

function numberValue(value: string | number | null | undefined) {
  if (value == null || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function isoDate(daysAgo = 0) {
  const value = new Date();
  value.setHours(12, 0, 0, 0);
  value.setDate(value.getDate() - daysAgo);
  return value.toISOString().slice(0, 10);
}

function metricValue(value: string | number | null | undefined, suffix = '') {
  const parsed = numberValue(value);
  return parsed == null ? '—' : `${formatDecimal(parsed)}${suffix}`;
}

function slugifyCampaignId(value: string) {
  return value
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-|-$)/g, '') || 'campanha';
}

function sourceBadge(source: string): { label: string; tone: 'gold' | 'blue' | 'green' } {
  if (source === 'local_fixture') return { label: 'demonstração', tone: 'gold' };
  if (source === 'manual') return { label: 'manual', tone: 'blue' };
  return { label: 'oficial', tone: 'green' };
}

type ManualAdsProvider = 'meta_ads' | 'tiktok_ads';

type ManualSpendRow = {
  provider: ManualAdsProvider;
  campaignId: string;
  campaignName: string;
  periodStart: string;
  periodEnd: string;
  spend: string;
  revenue: string;
};

function parseAdsSpendCsv(text: string): { rows: ManualSpendRow[]; errors: string[] } {
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const rows: ManualSpendRow[] = [];
  const errors: string[] = [];
  if (!lines.length) return { rows, errors: ['Arquivo vazio.'] };
  const header = lines[0].split(',').map((cell) => cell.trim().toLowerCase());
  const expected = ['provider', 'campaign_id', 'campaign_name', 'period_start', 'period_end', 'spend', 'revenue'];
  const columnIndex = (name: string) => header.indexOf(name);
  const missing = expected.filter((name) => columnIndex(name) === -1 && name !== 'revenue' && name !== 'campaign_id');
  if (missing.length) {
    return { rows, errors: [`Cabeçalho ausente: ${missing.join(', ')}. Use provider,campaign_id,campaign_name,period_start,period_end,spend,revenue.`] };
  }
  lines.slice(1).forEach((line, index) => {
    const cells = line.split(',').map((cell) => cell.trim());
    const provider = cells[columnIndex('provider')]?.toLowerCase();
    const campaignName = cells[columnIndex('campaign_name')];
    const periodStart = cells[columnIndex('period_start')];
    const periodEnd = cells[columnIndex('period_end')];
    const spend = cells[columnIndex('spend')];
    if (provider !== 'meta_ads' && provider !== 'tiktok_ads') {
      errors.push(`Linha ${index + 2}: provider deve ser meta_ads ou tiktok_ads.`);
      return;
    }
    if (!campaignName || !periodStart || !periodEnd || !spend) {
      errors.push(`Linha ${index + 2}: campos obrigatórios ausentes.`);
      return;
    }
    rows.push({
      provider: provider as ManualSpendRow['provider'],
      campaignId: (columnIndex('campaign_id') > -1 ? cells[columnIndex('campaign_id')] : '') || slugifyCampaignId(campaignName),
      campaignName,
      periodStart,
      periodEnd,
      spend,
      revenue: (columnIndex('revenue') > -1 ? cells[columnIndex('revenue')] : '') || '0',
    });
  });
  return { rows, errors };
}

function HydrationActions({
  job,
  disabled,
  onAction,
  onInspect,
}: {
  job: KlmLeadHydrationJob;
  disabled: boolean;
  onAction: (job: KlmLeadHydrationJob, action: 'retry' | 'cancel') => void;
  onInspect: (jobId: string) => void;
}) {
  const isFinal = job.state === 'hydrated';
  return (
    <div className="flex justify-end gap-1.5">
      <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => onInspect(job.id)} title="Ver histórico da busca">
        <Eye className="h-3.5 w-3.5" />
      </Button>
      <Button
        variant="outline"
        size="icon"
        className="h-8 w-8"
        disabled={disabled || isFinal}
        onClick={() => onAction(job, 'retry')}
        title="Tentar buscar os dados novamente"
      >
        <RotateCcw className="h-3.5 w-3.5" />
      </Button>
      <Button
        variant="outline"
        size="icon"
        className="h-8 w-8 text-destructive"
        disabled={disabled || isFinal || job.state === 'cancelled'}
        onClick={() => onAction(job, 'cancel')}
        title="Cancelar busca"
      >
        <XCircle className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}

export function AcquisitionIntegrationsPanel({
  canManageIntegrations,
  onNavigate,
}: {
  canManageIntegrations: boolean;
  onNavigate?: (path: string) => void;
}) {
  const navigate = useContextualNavigate({ embedded: Boolean(onNavigate), onNavigate });
  const queryClient = useQueryClient();
  const [provider, setProvider] = useState<AcquisitionProvider>('meta_ads');
  const [adsProvider, setAdsProvider] = useState<AdsProviderFilter>('all');
  const [periodDays, setPeriodDays] = useState('30');
  const [selectedHydrationId, setSelectedHydrationId] = useState<string | null>(null);
  const [leadName, setLeadName] = useState('Lead de validação local');
  const [phone, setPhone] = useState('+5511900000001');
  const [email, setEmail] = useState('lead.local@exemplo.test');
  const [campaign, setCampaign] = useState(providerProfiles.meta_ads.campaign);
  const [healthObject, setHealthObject] = useState('Medicamento de alto custo');
  const [proactive, setProactive] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [createdThread, setCreatedThread] = useState<{ threadId: string; matterId: string } | null>(null);
  const [manualProvider, setManualProvider] = useState<ManualAdsProvider>('meta_ads');
  const [manualCampaignName, setManualCampaignName] = useState('');
  const [manualCampaignId, setManualCampaignId] = useState('');
  const [manualPeriodStart, setManualPeriodStart] = useState(isoDate(29));
  const [manualPeriodEnd, setManualPeriodEnd] = useState(isoDate());
  const [manualSpend, setManualSpend] = useState('');
  const [manualRevenue, setManualRevenue] = useState('');
  const csvInputRef = useRef<HTMLInputElement | null>(null);

  const dateTo = isoDate();
  const dateFrom = isoDate(Number(periodDays) - 1);
  const providerFilter = adsProvider === 'all' ? undefined : adsProvider;

  const connectionsQuery = useQuery({
    queryKey: ['klm', 'omnichannel-connections'],
    queryFn: () => klmApi.omnichannelConnections(),
  });
  const capturesQuery = useQuery({
    queryKey: ['klm', 'lead-captures'],
    queryFn: () => klmApi.leadCaptures({ limit: 100 }),
  });
  const hydrationQuery = useQuery({
    queryKey: ['klm', 'lead-hydrations', providerFilter],
    queryFn: () => klmApi.leadHydrations({ provider: providerFilter, limit: 100 }),
  });
  const adsSummaryQuery = useQuery({
    queryKey: ['klm', 'ads-manager', 'summary', dateFrom, dateTo, providerFilter],
    queryFn: () => klmApi.adsManagerSummary({ dateFrom, dateTo, provider: providerFilter }),
  });
  const adsEntitiesQuery = useQuery({
    queryKey: ['klm', 'ads-manager', 'entities', providerFilter],
    queryFn: () => klmApi.adsManagerEntities({ provider: providerFilter, limit: 1000 }),
  });
  const adsImportsQuery = useQuery({
    queryKey: ['klm', 'ads-manager', 'imports', providerFilter],
    queryFn: () => klmApi.adsManagerImports({ provider: providerFilter, limit: 20 }),
  });
  const hydrationEventsQuery = useQuery({
    queryKey: ['klm', 'lead-hydrations', selectedHydrationId, 'events'],
    queryFn: () => klmApi.leadHydrationEvents(selectedHydrationId ?? ''),
    enabled: Boolean(selectedHydrationId),
  });

  const connections = useMemo(
    () => (connectionsQuery.data ?? []).filter((item) => acquisitionProviders.has(item.provider)),
    [connectionsQuery.data],
  );
  const outboundConnections = useMemo(
    () => (connectionsQuery.data ?? []).filter((item) => item.channel === 'whatsapp' || item.channel === 'email'),
    [connectionsQuery.data],
  );
  const selectedConnection = connections.find((item) => item.provider === provider);
  const activeAdsProvider = adsProvider === 'tiktok_ads' ? 'tiktok_ads' : 'meta_ads';
  const selectedAdsConnection = connections.find((item) => item.provider === activeAdsProvider);
  const readyCount = connections.filter((item) => connectionTone(item) === 'green').length;
  const waitingRoutes = (capturesQuery.data?.items ?? []).filter(
    (item) => item.receipt.result.proactiveRoute?.state === 'awaiting_connection',
  ).length;
  const summary = adsSummaryQuery.data;
  const pendingHydrations = (hydrationQuery.data ?? []).filter(
    (item) => !['hydrated', 'cancelled'].includes(item.state),
  ).length;

  const invalidateAds = () => {
    void queryClient.invalidateQueries({ queryKey: ['klm', 'ads-manager'] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'lead-hydrations'] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'lead-captures'] });
  };

  const prepareMutation = useMutation({
    mutationFn: (sourceProvider: AcquisitionProvider) => klmApi.createOmnichannelConnection({
      channel: providerProfiles[sourceProvider].channel,
      provider: sourceProvider,
      connectionKey: `${sourceProvider}:local`,
      displayName: `${providerProfiles[sourceProvider].label} local`,
      transportMode: 'local_fixture',
      isDefault: true,
      routingPolicy: { canonicalIdentity: true, createMatter: true, proactiveRoute: 'default' },
      configuration: { mappingVersion: 'v7', sourceType: sourceProvider },
    }),
    onSuccess: (connection) => {
      setNotice(`${providerLabel(connection.provider)} configurada. Nenhuma sincronização foi realizada.`);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-connections'] });
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Não foi possível preparar a fonte.'),
  });

  const validateMutation = useMutation({
    mutationFn: (connectionId: string) => klmApi.validateOmnichannelConnection(connectionId),
    onSuccess: (result) => {
      setNotice(result.readyForExternalSetup
        ? 'A configuração está pronta para conectar a conta quando você aprovar.'
        : `Configuração incompleta: ${result.missingConfiguration.join(', ')}.`);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-connections'] });
    },
    onError: () => setNotice('Não foi possível verificar esta configuração.'),
  });

  const stateMutation = useMutation({
    mutationFn: (connection: KlmChannelConnection) => klmApi.updateOmnichannelConnection(connection.id, {
      expectedVersion: connection.version,
      action: connection.status === 'paused' ? 'resume' : 'pause',
    }),
    onSuccess: (connection) => {
      setNotice(`${connection.displayName}: ${stateLabel(connection.status)}.`);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-connections'] });
    },
    onError: () => setNotice('A conexão mudou em outra sessão. Atualize a leitura e tente novamente.'),
  });

  const ingestMutation = useMutation({
    mutationFn: () => {
      if (!selectedConnection) throw new Error('Prepare a fonte selecionada antes do teste.');
      const profile = providerProfiles[provider];
      const leadId = `local:${provider}:${crypto.randomUUID()}`;
      const commonFields = [
        { name: 'full_name', values: [leadName] },
        { name: 'phone_number', values: [phone] },
        { name: 'email', values: [email] },
        { name: 'health_subtheme', values: ['medicamento_alto_custo'] },
        { name: 'medicamento', values: [healthObject] },
      ];
      const rawPayload = provider === 'meta_ads'
        ? {
            id: leadId,
            account_id: 'local:meta:account',
            campaign_id: 'local:meta:campaign',
            adset_id: 'local:meta:adset',
            ad_id: 'local:meta:ad',
            form_id: 'local:meta:form',
            campaign_name: campaign,
            source: profile.source,
            field_data: commonFields,
            fbclid: crypto.randomUUID(),
          }
        : provider === 'tiktok_ads'
          ? {
              data: {
                lead_id: leadId,
                advertiser_id: 'local:tiktok:account',
                campaign_id: 'local:tiktok:campaign',
                adgroup_id: 'local:tiktok:adgroup',
                ad_id: 'local:tiktok:ad',
                form_id: 'local:tiktok:form',
                campaign_name: campaign,
                platform: profile.source,
                field_data: commonFields,
                ttclid: crypto.randomUUID(),
              },
            }
          : {
              submission_id: leadId,
              form_id: `form:${provider}:saude`,
              campaign,
              source: profile.source,
              full_name: leadName,
              phone_number: phone,
              email,
              health_subtheme: 'medicamento_alto_custo',
              medicamento: healthObject,
            };
      return klmApi.ingestRawLeadCapture(selectedConnection.id, {
        rawPayload,
        proactiveMessageTemplate: proactive ? 'triagem_saude_v1' : null,
      });
    },
    onSuccess: (result) => {
      setCreatedThread({ threadId: result.thread.id, matterId: result.matterId });
      setNotice(result.receipt.result.proactiveRoute?.state === 'scheduled'
        ? 'Lead criado na conversa canônica e primeira abordagem agendada no canal padrão.'
        : 'Lead criado na conversa canônica; a abordagem aguarda um WhatsApp ou e-mail configurado.');
      invalidateAds();
      void queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'tracking'] });
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'O teste local falhou.'),
  });

  const intentMutation = useMutation({
    mutationFn: async (actionType: 'ads_campaign_sync' | 'ads_conversion_sync') => {
      const intent = await klmApi.createExternalIntent({
        actionType,
        provider: activeAdsProvider,
        idempotencyKey: `acquisition:${actionType}:${activeAdsProvider}:${crypto.randomUUID()}`,
        payload: {
          connectionId: selectedAdsConnection?.id ?? null,
          localOnly: true,
          externalActionsExecuted: 0,
        },
      });
      return klmApi.approveExternalIntentLocally(intent.id);
    },
    onSuccess: (intent) => setNotice(`Intenção ${intent.status} no ambiente local; nenhuma API de anúncios foi chamada.`),
    onError: () => setNotice('Não foi possível registrar a intenção local.'),
  });

  const snapshotMutation = useMutation({
    mutationFn: () => {
      if (!selectedAdsConnection) throw new Error(`Prepare ${providerLabel(activeAdsProvider)} antes de importar um cenário.`);
      const prefix = activeAdsProvider === 'meta_ads' ? 'meta' : 'tiktok';
      const accountId = `local:${prefix}:account`;
      const campaignId = `local:${prefix}:campaign`;
      const adSetId = `local:${prefix}:adset`;
      const adId = `local:${prefix}:ad`;
      return klmApi.ingestAdsManagerSnapshot(selectedAdsConnection.id, {
        externalSyncId: `spa-local:${prefix}:${crypto.randomUUID()}`,
        source: 'local_fixture',
        observedAt: new Date().toISOString(),
        metadata: { origin: 'klm_spa_local_fixture', externalActionsExecuted: 0 },
        entities: [
          {
            entityType: 'account',
            externalId: accountId,
            displayName: `Conta ${providerLabel(activeAdsProvider)} local`,
            effectiveStatus: 'active',
            currency: 'BRL',
            timezone: 'America/Sao_Paulo',
          },
          {
            entityType: 'campaign',
            externalId: campaignId,
            parentExternalId: accountId,
            displayName: 'Negativas de plano de saúde',
            effectiveStatus: 'active',
            objective: 'lead_generation',
            currency: 'BRL',
            hierarchyPath: [accountId, campaignId],
            metrics: [{
              metricDate: dateTo,
              currency: 'BRL',
              spend: '1280.00',
              impressions: 18400,
              reach: 13920,
              clicks: 426,
              conversations: 61,
              comments: 28,
              leads: 34,
              qualifiedLeads: 19,
              opportunities: 11,
              contracts: 5,
              revenue: '26000.00',
            }],
          },
          {
            entityType: 'ad_set',
            externalId: adSetId,
            parentExternalId: campaignId,
            displayName: 'Oncologia e medicamentos de alto custo',
            effectiveStatus: 'active',
            hierarchyPath: [accountId, campaignId, adSetId],
          },
          {
            entityType: 'ad',
            externalId: adId,
            parentExternalId: adSetId,
            displayName: 'Medicamento negado pelo plano',
            effectiveStatus: 'active',
            hierarchyPath: [accountId, campaignId, adSetId, adId],
          },
          {
            entityType: 'creative',
            externalId: `local:${prefix}:creative`,
            parentExternalId: adId,
            displayName: 'Atuação rápida em saúde',
            effectiveStatus: 'active',
          },
          {
            entityType: 'form',
            externalId: `local:${prefix}:form`,
            parentExternalId: adId,
            displayName: 'Triagem jurídica de saúde',
            effectiveStatus: 'active',
          },
        ],
      });
    },
    onSuccess: () => {
      setNotice('Cenário de anúncios importado na base local com recibo imutável. Nenhuma API externa foi chamada.');
      invalidateAds();
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Não foi possível importar o cenário local.'),
  });

  const buildManualEntity = (row: {
    campaignId: string;
    campaignName: string;
    periodEnd: string;
    spend: string;
    revenue?: string;
  }) => ({
    entityType: 'campaign' as const,
    externalId: row.campaignId,
    displayName: row.campaignName,
    effectiveStatus: 'active',
    currency: 'BRL',
    metrics: [{
      metricDate: row.periodEnd,
      currency: 'BRL',
      spend: row.spend,
      revenue: row.revenue || '0',
    }],
  });

  const manualSpendMutation = useMutation({
    mutationFn: () => {
      const connection = connections.find((item) => item.provider === manualProvider);
      if (!connection) throw new Error(`Prepare ${providerLabel(manualProvider)} na aba Fontes antes de lançar investimento.`);
      const campaignId = manualCampaignId.trim() || slugifyCampaignId(manualCampaignName);
      return klmApi.ingestAdsManagerSnapshot(connection.id, {
        externalSyncId: `manual:${manualProvider}:${campaignId}:${manualPeriodStart}:${manualPeriodEnd}`,
        source: 'manual',
        observedAt: new Date().toISOString(),
        metadata: {
          origin: 'manual_entry',
          periodStart: manualPeriodStart,
          periodEnd: manualPeriodEnd,
          externalActionsExecuted: 0,
        },
        entities: [buildManualEntity({
          campaignId,
          campaignName: manualCampaignName,
          periodEnd: manualPeriodEnd,
          spend: manualSpend,
          revenue: manualRevenue,
        })],
      });
    },
    onSuccess: () => {
      setNotice('Investimento manual registrado com recibo imutável. Nenhuma API externa foi chamada.');
      setManualCampaignName('');
      setManualCampaignId('');
      setManualSpend('');
      setManualRevenue('');
      invalidateAds();
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Não foi possível registrar o investimento manual.'),
  });

  const csvSpendMutation = useMutation({
    mutationFn: async (file: File) => {
      const text = await file.text();
      const { rows, errors } = parseAdsSpendCsv(text);
      if (errors.length) throw new Error(errors[0]);
      if (!rows.length) throw new Error('Nenhuma linha válida encontrada no CSV.');
      const grouped = new Map<ManualAdsProvider, ManualSpendRow[]>();
      rows.forEach((row) => grouped.set(row.provider, [...(grouped.get(row.provider) ?? []), row]));
      let imported = 0;
      for (const [rowProvider, providerRows] of grouped) {
        const connection = connections.find((item) => item.provider === rowProvider);
        if (!connection) throw new Error(`Prepare ${providerLabel(rowProvider)} na aba Fontes antes de importar o CSV.`);
        await klmApi.ingestAdsManagerSnapshot(connection.id, {
          externalSyncId: `manual-csv:${rowProvider}:${crypto.randomUUID()}`,
          source: 'manual',
          observedAt: new Date().toISOString(),
          metadata: { origin: 'manual_csv_upload', rows: providerRows.length, externalActionsExecuted: 0 },
          entities: providerRows.map((row) => buildManualEntity(row)),
        });
        imported += providerRows.length;
      }
      return imported;
    },
    onSuccess: (imported) => {
      setNotice(`${imported} linha(s) de investimento importada(s) do CSV com recibo imutável.`);
      invalidateAds();
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Não foi possível importar o CSV de investimento.'),
  });

  const hydrationMutation = useMutation({
    mutationFn: ({ job, action }: { job: KlmLeadHydrationJob; action: 'retry' | 'cancel' }) =>
      klmApi.updateLeadHydration(job.id, action),
    onSuccess: (job) => {
      setNotice(`Busca do lead ${job.externalLeadId}: ${stateLabel(job.state)}.`);
      invalidateAds();
      if (selectedHydrationId === job.id) {
        void queryClient.invalidateQueries({ queryKey: ['klm', 'lead-hydrations', job.id, 'events'] });
      }
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'A fila mudou antes da ação.'),
  });

  const metricLoading = adsSummaryQuery.isPending ? '—' : null;
  const hasNonFixtureImports = (adsImportsQuery.data ?? []).some((item) => item.source !== 'local_fixture');

  const handleCsvFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (file) csvSpendMutation.mutate(file);
  };

  const handleLoadFixtureScenario = () => {
    if (hasNonFixtureImports && !window.confirm(
      'Já existem importações manuais ou oficiais registradas. Carregar o cenário de demonstração vai misturar dados fictícios com dados reais. Continuar?',
    )) return;
    snapshotMutation.mutate();
  };

  return (
    <div className="space-y-4">
      <KlmSectionTitle
        title="Operação de anúncios"
        description="Conecte aquisição, formulários, campanhas, atribuição e busca dos dados do lead em uma operação nativa."
        action={<KlmPill tone="green">identidade e conversa únicas</KlmPill>}
      />
      {notice && (
        <KlmPanel className="flex flex-col gap-3 border-primary/30 bg-primary/10 p-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-center gap-2 text-2xs">
            <BadgeCheck className="h-4 w-4 shrink-0 text-[#1a9948]" />
            <span>{notice}</span>
          </div>
          <div className="flex shrink-0 gap-2">
            {createdThread && (
              <Button size="sm" className="h-8 text-2xs" onClick={() => navigate(`/app/conversas?thread=${createdThread.threadId}&matter=${createdThread.matterId}`)}>
                Abrir conversa<ExternalLink className="ml-1.5 h-3 w-3" />
              </Button>
            )}
            <Button variant="ghost" size="sm" className="h-8 text-2xs" onClick={() => setNotice(null)}>Fechar</Button>
          </div>
        </KlmPanel>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        <KlmMetricCard
          label="Investimento"
          value={metricLoading ?? formatBRL(numberValue(summary?.spend))}
          delta={`${periodDays} dias · ${summary?.currency ?? 'BRL'}`}
        />
        <KlmMetricCard
          label="Leads captados"
          value={metricLoading ?? String(summary?.capturedLeads ?? 0)}
          delta={`${summary?.providerLeads ?? 0} informados pela mídia`}
        />
        <KlmMetricCard
          label="Custo por lead"
          value={metricLoading ?? formatBRL(numberValue(summary?.cpl))}
          delta="captação própria quando disponível"
        />
        <KlmMetricCard
          label="Custo por cliente"
          value={metricLoading ?? formatBRL(numberValue(summary?.cac))}
          delta={`${summary?.contracts ?? 0} contrato(s)`}
        />
        <KlmMetricCard
          label="Retorno sobre anúncios"
          value={metricLoading ?? metricValue(summary?.roas, 'x')}
          delta={formatBRL(numberValue(summary?.revenue))}
          trend={numberValue(summary?.roas) != null && Number(summary?.roas) >= 1 ? 'up' : 'neutral'}
        />
        <KlmMetricCard
          label="Aguardando dados"
          value={hydrationQuery.isPending ? '—' : String(pendingHydrations)}
          delta={`${summary?.hydrationFailed ?? 0} falha(s) no período`}
          trend={pendingHydrations > 0 ? 'down' : 'neutral'}
        />
      </div>

      {adsSummaryQuery.isError && (
        <div className="flex items-center gap-2 border-l-2 border-destructive bg-destructive/5 px-3 py-2 text-2xs text-destructive">
          <XCircle className="h-4 w-4" />O desempenho de anúncios não pôde ser carregado. As demais funções continuam disponíveis.
        </div>
      )}

      <Tabs defaultValue="fontes" className="space-y-4">
        <TabsList className="h-auto min-h-10 w-full justify-start gap-1 overflow-x-auto bg-secondary p-1">
          <TabsTrigger value="fontes" className="text-2xs">Fontes</TabsTrigger>
          <TabsTrigger value="desempenho" className="text-2xs">Gestor de anúncios</TabsTrigger>
          <TabsTrigger value="captacao" className="text-2xs">Captação e buscas</TabsTrigger>
          <TabsTrigger value="teste" className="text-2xs">Testar captação</TabsTrigger>
        </TabsList>

        <TabsContent value="fontes" className="space-y-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <KlmSectionTitle title="Fontes de aquisição" description="Contas de anúncios, formulários e sites convergem na mesma identidade do lead." />
            <div className="flex gap-2"><KlmPill tone="green">{readyCount} pronta(s)</KlmPill><KlmPill tone={waitingRoutes ? 'gold' : 'muted'}>{waitingRoutes} sem rota</KlmPill></div>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {(Object.entries(providerProfiles) as Array<[AcquisitionProvider, typeof providerProfiles[AcquisitionProvider]]>).map(([key, profile]) => {
              const connection = connections.find((item) => item.provider === key);
              const Icon = profile.icon;
              return (
                <KlmPanel key={key} className="p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex h-9 w-9 items-center justify-center rounded-md bg-secondary"><Icon className="h-4 w-4" /></div>
                    <KlmPill tone={connection ? connectionTone(connection) : 'muted'}>
                      {connection ? stateLabel(connection.status) : 'não preparada'}
                    </KlmPill>
                  </div>
                  <h3 className="mt-4 text-sm font-semibold">{profile.label}</h3>
                  <p className="mt-1 min-h-8 text-2xs leading-4 text-muted-foreground">
                    {connectionDescription(key, Boolean(connection))}
                  </p>
                  <div className="mt-4 flex gap-2">
                    {connection ? (
                      <>
                        <Button variant="outline" size="sm" className="h-8 flex-1 text-2xs" disabled={!canManageIntegrations || validateMutation.isPending} onClick={() => validateMutation.mutate(connection.id)}>
                          <RefreshCw className="mr-1.5 h-3 w-3" />Verificar
                        </Button>
                        <Button variant="outline" size="icon" className="h-8 w-8" disabled={!canManageIntegrations || stateMutation.isPending} onClick={() => stateMutation.mutate(connection)} title={connection.status === 'paused' ? 'Ativar fonte' : 'Pausar fonte'}>
                          {connection.status === 'paused' ? <CirclePlay className="h-3.5 w-3.5" /> : <CirclePause className="h-3.5 w-3.5" />}
                        </Button>
                      </>
                    ) : (
                      <Button size="sm" className="h-8 w-full text-2xs" disabled={!canManageIntegrations || prepareMutation.isPending} onClick={() => prepareMutation.mutate(key)}>
                        <Settings2 className="mr-1.5 h-3 w-3" />Configurar
                      </Button>
                    )}
                  </div>
                </KlmPanel>
              );
            })}
          </div>

          <KlmPanel className="p-4">
            <KlmSectionTitle
              title="Conectar contas de anúncios"
              description="Escolha a conta que deseja consultar ou usar para registrar conversões. Qualquer envio continua dependendo da sua aprovação."
              action={<KlmPill tone="gold">Nenhuma sincronização realizada</KlmPill>}
            />
            <div className="mt-4 grid gap-3 sm:grid-cols-[180px_1fr_1fr]">
              <Select value={adsProvider === 'all' ? 'meta_ads' : adsProvider} onValueChange={(value) => setAdsProvider(value as AdsProviderFilter)}>
                <SelectTrigger aria-label="Canal de anúncios para preparar" className="h-10 text-2xs"><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="meta_ads">Meta Ads</SelectItem><SelectItem value="tiktok_ads">TikTok Ads</SelectItem></SelectContent>
              </Select>
              <Button variant="outline" disabled={!canManageIntegrations || !selectedAdsConnection || intentMutation.isPending} onClick={() => intentMutation.mutate('ads_campaign_sync')} className="h-10 justify-start text-2xs">
                <Activity className="mr-2 h-4 w-4" />Preparar leitura de campanhas
              </Button>
              <Button variant="outline" disabled={!canManageIntegrations || !selectedAdsConnection || intentMutation.isPending} onClick={() => intentMutation.mutate('ads_conversion_sync')} className="h-10 justify-start text-2xs">
                <Target className="mr-2 h-4 w-4" />Preparar envio de conversões
              </Button>
            </div>
          </KlmPanel>
        </TabsContent>

        <TabsContent value="desempenho" className="space-y-4">
          <KlmPanel className="p-4">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <KlmSectionTitle
                title="Gestor de anúncios"
                description="Investimento, captação, qualidade e receita na mesma leitura, sem abreviações monetárias."
                action={<KlmPill tone="green">dados persistidos</KlmPill>}
              />
              <div className="flex flex-wrap gap-2">
                <Select value={adsProvider} onValueChange={(value) => setAdsProvider(value as AdsProviderFilter)}>
                  <SelectTrigger aria-label="Filtrar gestor por canal" className="h-9 w-40 text-2xs"><SelectValue /></SelectTrigger>
                  <SelectContent><SelectItem value="all">Todos os canais</SelectItem><SelectItem value="meta_ads">Meta Ads</SelectItem><SelectItem value="tiktok_ads">TikTok Ads</SelectItem></SelectContent>
                </Select>
                <Select value={periodDays} onValueChange={setPeriodDays}>
                  <SelectTrigger aria-label="Período do gestor de anúncios" className="h-9 w-32 text-2xs"><SelectValue /></SelectTrigger>
                  <SelectContent><SelectItem value="7">7 dias</SelectItem><SelectItem value="30">30 dias</SelectItem><SelectItem value="90">90 dias</SelectItem></SelectContent>
                </Select>
                <Button variant="outline" size="sm" className="h-9 text-2xs" onClick={invalidateAds} disabled={adsSummaryQuery.isFetching}>
                  <RefreshCw className={cn('mr-1.5 h-3.5 w-3.5', adsSummaryQuery.isFetching && 'animate-spin')} />Atualizar
                </Button>
              </div>
            </div>

            <div className="mt-5 overflow-x-auto border">
              <table className="w-full min-w-[1040px] text-left text-2xs">
                <thead className="border-b bg-secondary text-muted-foreground">
                  <tr><th className="p-3">Campanha</th><th className="p-3">Canal</th><th className="p-3">Investimento</th><th className="p-3">Leads mídia / CRM</th><th className="p-3">Qualificados</th><th className="p-3">Oportunidades</th><th className="p-3">Contratos</th><th className="p-3">CPL</th><th className="p-3">CAC</th><th className="p-3">ROAS</th></tr>
                </thead>
                <tbody>
                  {(summary?.campaigns ?? []).map((item) => (
                    <tr key={item.entityId} className="border-b last:border-0 hover:bg-secondary/70">
                      <td className="p-3"><strong className="block max-w-[260px] truncate text-2xs">{item.displayName}</strong><span className="mt-1 block text-2xs text-muted-foreground">{item.objective?.replaceAll('_', ' ') ?? item.externalId}</span></td>
                      <td className="p-3"><KlmPill tone="blue">{providerLabel(item.provider)}</KlmPill></td>
                      <td className="p-3 font-semibold tabular-nums">{formatBRL(numberValue(item.spend))}</td>
                      <td className="p-3"><strong>{item.providerLeads.toLocaleString('pt-BR')}</strong><span className="ml-1 text-muted-foreground">/ {item.capturedLeads.toLocaleString('pt-BR')}</span></td>
                      <td className="p-3">{item.qualifiedLeads.toLocaleString('pt-BR')}</td>
                      <td className="p-3">{item.opportunities.toLocaleString('pt-BR')}</td>
                      <td className="p-3">{item.contracts.toLocaleString('pt-BR')}</td>
                      <td className="p-3">{formatBRL(numberValue(item.cpl))}</td>
                      <td className="p-3">{formatBRL(numberValue(item.cac))}</td>
                      <td className="p-3 font-semibold text-[#0b7435] dark:text-emerald-400">{metricValue(item.roas, 'x')}</td>
                    </tr>
                  ))}
                  {adsSummaryQuery.isPending && <tr><td colSpan={10} className="p-10 text-center text-muted-foreground"><Loader2 className="mx-auto mb-2 h-4 w-4 animate-spin" />Carregando campanhas</td></tr>}
                  {adsSummaryQuery.isSuccess && !summary?.campaigns.length && <tr><td colSpan={10} className="p-10 text-center text-muted-foreground">Nenhuma campanha importada neste período.</td></tr>}
                </tbody>
              </table>
            </div>
          </KlmPanel>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(300px,0.65fr)]">
            <KlmPanel className="overflow-hidden">
              <div className="flex items-center justify-between border-b p-4">
                <KlmSectionTitle title="Estrutura das campanhas" description="Conta, campanha, conjunto, anúncio, criativo e formulário." />
                <Layers3 className="h-4 w-4 text-muted-foreground" />
              </div>
              <div className="max-h-[430px] divide-y overflow-y-auto">
                {(adsEntitiesQuery.data ?? []).map((entity) => (
                  <div key={entity.id} className="grid grid-cols-[1fr_auto] items-center gap-3 px-4 py-3 hover:bg-secondary/60">
                    <div className="min-w-0" style={{ paddingLeft: `${entityDepth[entity.entityType] * 16}px` }}>
                      <div className="flex min-w-0 items-center gap-2">
                        <GitBranch className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <strong className="truncate text-2xs">{entity.displayName}</strong>
                      </div>
                      <p className="mt-1 truncate pl-5 text-2xs text-muted-foreground">{entityLabels[entity.entityType]} · {entity.externalId}</p>
                    </div>
                    <div className="flex items-center gap-2"><KlmPill tone="blue">{providerLabel(entity.provider)}</KlmPill><KlmPill tone={stateTone(entity.effectiveStatus)}>{stateLabel(entity.effectiveStatus)}</KlmPill></div>
                  </div>
                ))}
                {adsEntitiesQuery.isPending && <p className="p-10 text-center text-2xs text-muted-foreground"><Loader2 className="mx-auto mb-2 h-4 w-4 animate-spin" />Carregando estrutura</p>}
                {adsEntitiesQuery.isSuccess && !adsEntitiesQuery.data.length && <p className="p-10 text-center text-2xs text-muted-foreground">A estrutura aparecerá após a primeira importação.</p>}
              </div>
            </KlmPanel>

            <KlmPanel className="overflow-hidden">
              <div className="border-b p-4"><KlmSectionTitle title="Sincronizações" description="Recibos imutáveis de cada leitura." action={<DatabaseZap className="h-4 w-4 text-muted-foreground" />} /></div>
              <div className="max-h-[310px] divide-y overflow-y-auto">
                {(adsImportsQuery.data ?? []).map((item) => {
                  const badge = sourceBadge(item.source);
                  return (
                    <div key={item.id} className="p-3">
                      <div className="flex items-center justify-between gap-2"><strong className="truncate text-2xs">{providerLabel(item.provider)}</strong><KlmPill tone={badge.tone}>{badge.label}</KlmPill></div>
                      <p className="mt-1 text-2xs text-muted-foreground">{item.entityCount} itens · {item.observationCount} métricas</p>
                      <p className="mt-1 text-2xs text-muted-foreground">{new Date(item.observedAt).toLocaleString('pt-BR')}</p>
                    </div>
                  );
                })}
                {adsImportsQuery.isSuccess && !adsImportsQuery.data.length && <p className="p-8 text-center text-2xs text-muted-foreground">Nenhuma sincronização registrada.</p>}
              </div>
              <div className="border-t bg-secondary/50 p-4">
                <KlmSectionTitle title="Demonstração" description="Cenário fictício para explorar a tela sem investimento real." />
                <Button variant="outline" className="mt-3 w-full text-2xs" disabled={!canManageIntegrations || !selectedAdsConnection || snapshotMutation.isPending} onClick={handleLoadFixtureScenario}>
                  {snapshotMutation.isPending ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : <FlaskConical className="mr-2 h-3.5 w-3.5" />}
                  Carregar cenário local
                </Button>
                <p className="mt-2 text-center text-2xs leading-4 text-muted-foreground">Cria dados auditáveis de demonstração, sem acessar Meta ou TikTok.</p>
              </div>
            </KlmPanel>
          </div>

          <KlmPanel className="p-4">
            <KlmSectionTitle
              title="Investimento (spend)"
              description="Lance o gasto real de mídia manualmente ou por planilha, com recibo imutável e origem marcada como manual."
              action={<KlmPill tone="blue">separado da demonstração</KlmPill>}
            />
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <div className="space-y-3 rounded-md border p-4">
                <div className="flex items-center gap-2"><PenLine className="h-3.5 w-3.5 text-muted-foreground" /><strong className="text-2xs">Lançamento manual</strong></div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5 sm:col-span-2">
                    <Label className="text-2xs">Provedor</Label>
                    <Select value={manualProvider} onValueChange={(value) => setManualProvider(value as ManualAdsProvider)}>
                      <SelectTrigger className="h-9 text-2xs"><SelectValue /></SelectTrigger>
                      <SelectContent><SelectItem value="meta_ads">Meta Ads</SelectItem><SelectItem value="tiktok_ads">TikTok Ads</SelectItem></SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5 sm:col-span-2">
                    <Label className="text-2xs">Campanha</Label>
                    <Input className="h-9 text-2xs" placeholder="Nome da campanha" value={manualCampaignName} onChange={(event) => setManualCampaignName(event.target.value)} />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-2xs">Período: início</Label>
                    <Input type="date" className="h-9 text-2xs" value={manualPeriodStart} onChange={(event) => setManualPeriodStart(event.target.value)} />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-2xs">Período: fim</Label>
                    <Input type="date" className="h-9 text-2xs" value={manualPeriodEnd} onChange={(event) => setManualPeriodEnd(event.target.value)} />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-2xs">Valor gasto (R$)</Label>
                    <Input type="number" min="0" step="0.01" className="h-9 text-2xs" placeholder="0,00" value={manualSpend} onChange={(event) => setManualSpend(event.target.value)} />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-2xs">Receita (opcional)</Label>
                    <Input type="number" min="0" step="0.01" className="h-9 text-2xs" placeholder="0,00" value={manualRevenue} onChange={(event) => setManualRevenue(event.target.value)} />
                  </div>
                </div>
                <Button
                  className="h-9 w-full text-2xs"
                  disabled={!canManageIntegrations || manualSpendMutation.isPending || !manualCampaignName.trim() || !manualPeriodStart || !manualPeriodEnd || !manualSpend}
                  onClick={() => manualSpendMutation.mutate()}
                >
                  {manualSpendMutation.isPending ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : <PenLine className="mr-2 h-3.5 w-3.5" />}
                  Registrar investimento
                </Button>
              </div>

              <div className="space-y-3 rounded-md border p-4">
                <div className="flex items-center gap-2"><Upload className="h-3.5 w-3.5 text-muted-foreground" /><strong className="text-2xs">Upload em CSV</strong></div>
                <p className="text-2xs leading-4 text-muted-foreground">Colunas: <code>provider,campaign_id,campaign_name,period_start,period_end,spend,revenue</code>. <code>campaign_id</code> e <code>revenue</code> são opcionais.</p>
                <input ref={csvInputRef} type="file" accept=".csv,text/csv" className="hidden" onChange={handleCsvFileChange} />
                <Button
                  variant="outline"
                  className="h-9 w-full text-2xs"
                  disabled={!canManageIntegrations || csvSpendMutation.isPending}
                  onClick={() => csvInputRef.current?.click()}
                >
                  {csvSpendMutation.isPending ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : <Upload className="mr-2 h-3.5 w-3.5" />}
                  Selecionar arquivo CSV
                </Button>
                <p className="text-center text-2xs leading-4 text-muted-foreground">Cada linha vira um recibo imutável com origem "manual"; nenhuma API de anúncios é chamada.</p>
              </div>
            </div>
          </KlmPanel>
        </TabsContent>

        <TabsContent value="captacao" className="space-y-4">
          <KlmPanel className="overflow-hidden">
            <div className="flex flex-col gap-3 border-b p-4 sm:flex-row sm:items-center sm:justify-between">
              <KlmSectionTitle title="Leads recebidos" description="Um recibo idempotente por lead, ligado à identidade, conversa e caso." />
              <Button variant="outline" size="sm" className="h-8 text-2xs" onClick={() => void capturesQuery.refetch()} disabled={capturesQuery.isFetching}>
                <RefreshCw className={cn('mr-1.5 h-3 w-3', capturesQuery.isFetching && 'animate-spin')} />Atualizar
              </Button>
            </div>
            <div className="divide-y">
              {capturesQuery.isPending && <p className="p-8 text-center text-2xs text-muted-foreground"><Loader2 className="mx-auto mb-2 h-4 w-4 animate-spin" />Carregando captações</p>}
              {(capturesQuery.data?.items ?? []).map((item) => {
                const route = item.receipt.result.proactiveRoute;
                const references = item.acquisition.providerReferences ?? {};
                return (
                  <div key={item.receipt.id} className="grid gap-3 p-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_170px_auto] lg:items-center">
                    <div>
                      <div className="flex flex-wrap items-center gap-2"><strong className="text-2xs">{item.identity?.displayName ?? 'Identidade em revisão'}</strong><KlmPill tone="blue">{providerLabel(item.receipt.provider)}</KlmPill><KlmPill tone={stateTone(item.receipt.state)}>{stateLabel(item.receipt.state)}</KlmPill></div>
                      <p className="mt-1 text-2xs text-muted-foreground">{item.receipt.externalFormId ?? 'sem formulário'} · {new Date(item.receipt.createdAt).toLocaleString('pt-BR')}</p>
                    </div>
                    <div>
                      <strong className="block truncate text-2xs">{item.acquisition.campaign ?? item.acquisition.source ?? 'Sem campanha informada'}</strong>
                      <span className="mt-1 block truncate text-2xs text-muted-foreground">{item.acquisition.adSet ?? 'conjunto não informado'} · {item.acquisition.ad ?? 'anúncio não informado'}</span>
                      {Object.keys(references).length > 0 && <span className="mt-1 block truncate text-2xs text-muted-foreground">IDs: {Object.values(references).join(' · ')}</span>}
                    </div>
                    <div><KlmPill tone={stateTone(route?.state ?? '')}>{stateLabel(route?.state ?? 'sem abordagem')}</KlmPill><span className="mt-1 block text-2xs text-muted-foreground">{route?.connectionName ?? route?.channel ?? '—'}</span></div>
                    <div className="flex justify-end gap-2"><Button variant="outline" size="icon" className="h-8 w-8" disabled={!item.thread} onClick={() => item.thread && navigate(`/app/conversas?thread=${item.thread.id}${item.receipt.matterId ? `&matter=${item.receipt.matterId}` : ''}`)} title="Abrir conversa"><MessageCircleMore className="h-3.5 w-3.5" /></Button><Button variant="outline" size="icon" className="h-8 w-8" disabled={!item.receipt.matterId} onClick={() => item.receipt.matterId && navigate(`/app/funil?matter=${item.receipt.matterId}`)} title="Abrir caso"><ExternalLink className="h-3.5 w-3.5" /></Button></div>
                  </div>
                );
              })}
              {capturesQuery.isSuccess && !capturesQuery.data.items.length && <p className="p-10 text-center text-2xs text-muted-foreground">Nenhuma captação registrada.</p>}
            </div>
          </KlmPanel>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(300px,0.65fr)]">
            <KlmPanel className="overflow-hidden">
              <div className="border-b p-4"><KlmSectionTitle title="Busca de dados dos formulários" description="Meta e TikTok notificam o ID primeiro; a plataforma busca os dados completos depois da conexão oficial." action={<KlmPill tone={pendingHydrations ? 'gold' : 'green'}>{pendingHydrations} pendente(s)</KlmPill>} /></div>
              <div className="divide-y">
                {(hydrationQuery.data ?? []).map((job) => (
                  <div key={job.id} className={cn('grid gap-3 p-4 sm:grid-cols-[1fr_150px_auto] sm:items-center', selectedHydrationId === job.id && 'bg-primary/5')}>
                    <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><strong className="truncate text-2xs">Lead {job.externalLeadId}</strong><KlmPill tone="blue">{providerLabel(job.provider)}</KlmPill></div><p className="mt-1 truncate text-2xs text-muted-foreground">{job.externalFormId ?? 'formulário não informado'} · tentativa {job.attemptCount}/{job.maxAttempts}</p>{job.lastErrorMessage && <p className="mt-1 line-clamp-2 text-2xs text-destructive">{job.lastErrorMessage}</p>}</div>
                    <div><KlmPill tone={stateTone(job.state)}>{stateLabel(job.state)}</KlmPill><span className="mt-1 block text-2xs text-muted-foreground">{job.nextAttemptAt ? `próxima ${new Date(job.nextAttemptAt).toLocaleString('pt-BR')}` : new Date(job.updatedAt).toLocaleString('pt-BR')}</span></div>
                    <HydrationActions job={job} disabled={!canManageIntegrations || hydrationMutation.isPending} onInspect={setSelectedHydrationId} onAction={(selectedJob, action) => hydrationMutation.mutate({ job: selectedJob, action })} />
                  </div>
                ))}
                {hydrationQuery.isPending && <p className="p-10 text-center text-2xs text-muted-foreground"><Loader2 className="mx-auto mb-2 h-4 w-4 animate-spin" />Carregando fila</p>}
                {hydrationQuery.isSuccess && !hydrationQuery.data.length && <p className="p-10 text-center text-2xs text-muted-foreground">Nenhum lead aguarda busca de dados.</p>}
              </div>
            </KlmPanel>

            <KlmPanel className="overflow-hidden">
              <div className="border-b p-4"><KlmSectionTitle title="Histórico da busca" description="Evidência imutável de cada transição." action={<Clock3 className="h-4 w-4 text-muted-foreground" />} /></div>
              <div className="max-h-[360px] divide-y overflow-y-auto">
                {!selectedHydrationId && <p className="p-10 text-center text-2xs text-muted-foreground">Selecione o ícone de histórico em uma busca.</p>}
                {hydrationEventsQuery.isPending && <p className="p-8 text-center text-2xs text-muted-foreground"><Loader2 className="mx-auto mb-2 h-4 w-4 animate-spin" />Carregando histórico</p>}
                {(hydrationEventsQuery.data ?? []).map((event) => (
                  <div key={event.id} className="p-3"><div className="flex items-center justify-between gap-2"><strong className="text-2xs">{stateLabel(event.eventType)}</strong><KlmPill tone={stateTone(event.nextState)}>{stateLabel(event.nextState)}</KlmPill></div><p className="mt-1 text-2xs text-muted-foreground">{new Date(event.occurredAt).toLocaleString('pt-BR')} · {stateLabel(event.previousState)} → {stateLabel(event.nextState)}</p>{event.errorMessage && <p className="mt-1 text-2xs text-destructive">{event.errorMessage}</p>}</div>
                ))}
              </div>
            </KlmPanel>
          </div>
        </TabsContent>

        <TabsContent value="teste">
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
            <KlmPanel className="p-4">
              <KlmSectionTitle title="Simular formulário" description="Cria identidade, caso, atribuição e conversa na base local." action={<KlmPill tone="green">idempotente</KlmPill>} />
              <div className="mt-5 grid gap-4 sm:grid-cols-2">
                <div className="space-y-2"><Label className="text-2xs">Fonte</Label><Select value={provider} onValueChange={(value) => { const next = value as AcquisitionProvider; setProvider(next); setCampaign(providerProfiles[next].campaign); }}><SelectTrigger className="h-9 text-2xs"><SelectValue /></SelectTrigger><SelectContent>{(Object.keys(providerProfiles) as AcquisitionProvider[]).map((key) => <SelectItem key={key} value={key}>{providerProfiles[key].label}</SelectItem>)}</SelectContent></Select></div>
                <div className="space-y-2"><Label className="text-2xs">Nome do lead</Label><Input className="h-9 text-2xs" value={leadName} onChange={(event) => setLeadName(event.target.value)} /></div>
                <div className="space-y-2"><Label className="text-2xs">WhatsApp</Label><Input className="h-9 text-2xs" value={phone} onChange={(event) => setPhone(event.target.value)} /></div>
                <div className="space-y-2"><Label className="text-2xs">E-mail</Label><Input className="h-9 text-2xs" value={email} onChange={(event) => setEmail(event.target.value)} /></div>
                <div className="space-y-2"><Label className="text-2xs">Campanha</Label><Input className="h-9 text-2xs" value={campaign} onChange={(event) => setCampaign(event.target.value)} /></div>
                <div className="space-y-2"><Label className="text-2xs">Medicamento ou tratamento</Label><Input className="h-9 text-2xs" value={healthObject} onChange={(event) => setHealthObject(event.target.value)} /></div>
              </div>
              <div className="mt-4 flex items-center justify-between border bg-secondary p-3"><div><strong className="block text-2xs">Agendar primeira abordagem</strong><span className="text-2xs text-muted-foreground">Usa o WhatsApp ou e-mail padrão configurado.</span></div><Switch checked={proactive} onCheckedChange={setProactive} /></div>
              <Button className="mt-4 h-9 text-2xs" disabled={!canManageIntegrations || ingestMutation.isPending || !leadName || (!phone && !email)} onClick={() => ingestMutation.mutate()}>{ingestMutation.isPending ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : <FlaskConical className="mr-2 h-3.5 w-3.5" />}Executar teste local</Button>
            </KlmPanel>

            <KlmPanel tone="black" className="p-5">
              <Smartphone className="h-5 w-5 text-primary" />
              <h3 className="mt-4 text-sm font-medium">Rota prevista</h3>
              <div className="mt-4 space-y-3 text-2xs">
                <div className="flex justify-between border-b border-white/10 pb-2"><span className="text-white/55">Entrada</span><strong>{providerProfiles[provider].label}</strong></div>
                <div className="flex justify-between border-b border-white/10 pb-2"><span className="text-white/55">Identidade</span><strong>telefone + e-mail</strong></div>
                <div className="flex justify-between border-b border-white/10 pb-2"><span className="text-white/55">Destino</span><strong>conversa canônica</strong></div>
                <div className="flex justify-between border-b border-white/10 pb-2"><span className="text-white/55">Abordagem</span><strong>{proactive ? (outboundConnections[0]?.displayName ?? 'aguarda conexão') : 'desativada'}</strong></div>
                <div className="flex justify-between"><span className="text-white/55">Execução externa</span><KlmPill tone="gold">bloqueada</KlmPill></div>
              </div>
              <div className="mt-5 flex items-start gap-2 border-l-2 border-primary bg-white/5 px-3 py-2 text-2xs leading-4 text-white/65"><ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />O teste usa os mesmos contratos, IDs e deduplicação da futura integração oficial.</div>
              <Button variant="outline" className="mt-4 w-full border-white/20 bg-transparent text-2xs text-white hover:bg-white/10 hover:text-white" onClick={() => navigate(`/app/integracoes?tab=assistente&provider=${provider}`)}><Settings2 className="mr-2 h-3.5 w-3.5" />Configurar canais</Button>
            </KlmPanel>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
