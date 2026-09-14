import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import {
  Activity,
  Check,
  CircleAlert,
  CircleCheck,
  Clipboard,
  Cable,
  ExternalLink,
  KeyRound,
  LoaderCircle,
  Pause,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Webhook,
} from 'lucide-react';
import { PersonalizedFilterBuilder } from '@/components/common/PersonalizedFilterBuilder';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useToast } from '@/hooks/use-toast';
import {
  klmApi,
  type KlmChannelConnection,
  type KlmGoogleDataManagerTrackingStatus,
  type KlmWebhookCenterReceipt,
} from '@/klm/api';
import { KlmMetricCard, KlmPanel, KlmPill, KlmProgress, KlmSectionTitle } from '@/klm/components/Primitives';
import {
  EMPTY_PERSONALIZED_FILTER,
  matchesPersonalizedFilter,
  type PersonalizedFilterDefinition,
  type PersonalizedFilterField,
} from '@/lib/personalized-filters';
import { resolveApiHttpUrl } from '@/services/api-origin';
import { useOptionalAppStore } from '@/store/app-context';

const providerCatalog = [
  { value: 'meta_ads', label: 'Meta Lead Ads', channel: 'facebook' },
  { value: 'tiktok_ads', label: 'TikTok Lead Ads', channel: 'tiktok' },
  { value: 'site', label: 'Site e formulários', channel: 'webchat' },
  { value: 'lovable', label: 'Site Lovable', channel: 'webchat' },
  { value: 'meta_instagram', label: 'Instagram', channel: 'instagram' },
  { value: 'meta_messenger', label: 'Messenger', channel: 'messenger' },
  { value: 'tiktok_business', label: 'TikTok Business', channel: 'tiktok' },
  { value: 'google_ads', label: 'Google Ads (Lead Form)', channel: 'webchat' },
  { value: 'google_tag_manager', label: 'Google Tag Manager', channel: 'webchat' },
  { value: 'ga4', label: 'Google Analytics 4', channel: 'webchat' },
  { value: 'whatsapp_cloud', label: 'WhatsApp Cloud API', channel: 'whatsapp' },
  { value: 'pluga', label: 'Pluga (automação)', channel: 'webchat' },
  { value: 'zapier', label: 'Zapier (automação)', channel: 'webchat' },
] as const;

type ProviderValue = (typeof providerCatalog)[number]['value'];

type ProviderGuide = {
  summary: string;
  accountHint: string;
  requiredEvents: string[];
  externalSteps: string[];
  officialDocs?: string;
};

const providerGuides: Record<ProviderValue, ProviderGuide> = {
  meta_ads: {
    summary: 'Recebe o lead do formulário instantâneo, preserva campanha, conjunto, anúncio, criativo e formulário e inicia a hidratação idempotente.',
    accountHint: 'ID da conta de anúncios ou da página vinculada ao formulário',
    requiredEvents: ['leadgen', 'alteração de formulário', 'evento de teste'],
    externalSteps: [
      'Vincule o aplicativo à empresa, conta de anúncios e página corretas.',
      'Cadastre o endpoint exclusivo e o token de verificação no produto Webhooks.',
      'Assine o evento de lead e confirme o formulário usado por cada campanha.',
      'Envie um lead de teste e confira campanha, conjunto, anúncio, criativo e form_id no recibo.',
    ],
    officialDocs: 'https://developers.facebook.com/docs/marketing-api/guides/lead-ads/retrieving',
  },
  tiktok_ads: {
    summary: 'Recebe notificações de Lead Generation, busca os dados autorizados e mantém advertiser, campanha, grupo, anúncio, criativo e formulário.',
    accountHint: 'Advertiser ID da conta que publica os formulários',
    requiredEvents: ['LEAD', 'evento de teste', 'postback de CRM'],
    externalSteps: [
      'Associe o aplicativo à conta TikTok for Business e ao advertiser correto.',
      'Cadastre a callback HTTPS e assine o objeto LEAD para os formulários escolhidos.',
      'Valide o evento de teste e a busca posterior dos campos autorizados do lead.',
      'Confirme os IDs de campanha, ad group, anúncio, criativo e formulário no recibo.',
    ],
    officialDocs: 'https://business-api.tiktok.com/portal',
  },
  site: {
    summary: 'Recebe formulários próprios com identificador único, UTMs, click IDs, página, sessão e consentimentos sem depender de um conector terceiro.',
    accountHint: 'Nome estável do site ou domínio de origem',
    requiredEvents: ['lead.submitted', 'form.updated', 'consent.updated'],
    externalSteps: [
      'Inclua o event_id único e os parâmetros UTM, gclid, gbraid, wbraid, fbclid e ttclid disponíveis.',
      'Assine o corpo bruto da requisição com o segredo desta conexão.',
      'Envie campanha, conjunto, anúncio, criativo, landing page e sessão sem alterar os IDs originais.',
      'Repita o mesmo event_id para provar que a deduplicação não cria outro lead.',
    ],
  },
  lovable: {
    summary: 'Conecta a landing page criada no Lovable ao ingresso nativo, mantendo formulário, sessão e atribuição de mídia até o caso e a venda.',
    accountHint: 'Projeto ou domínio publicado no Lovable',
    requiredEvents: ['lead.submitted', 'booking.requested', 'consent.updated'],
    externalSteps: [
      'Configure a ação do formulário para o endpoint exclusivo desta conexão.',
      'Envie event_id, telefone, e-mail, UTMs, click IDs, página e timestamp no mesmo payload.',
      'Use o segredo somente no backend ou função segura do projeto, nunca no navegador.',
      'Valide um envio local e confira o vínculo com identidade, conversa, card e origem.',
    ],
  },
  meta_instagram: {
    summary: 'Centraliza mensagens, comentários, menções e respostas de uma conta profissional na identidade e conversa canônicas.',
    accountHint: 'ID da conta profissional do Instagram',
    requiredEvents: ['messages', 'comments', 'mentions'],
    externalSteps: [
      'Vincule a conta profissional e a página ou empresa exigida pelo fluxo escolhido.',
      'Cadastre o endpoint, valide o challenge e selecione apenas os campos usados pela plataforma.',
      'Conclua as permissões e a revisão do aplicativo antes de atender contas externas.',
      'Teste mensagem, comentário e menção e confira a mesma identidade na caixa de entrada.',
    ],
    officialDocs: 'https://developers.facebook.com/docs/instagram-platform/webhooks',
  },
  meta_messenger: {
    summary: 'Recebe mensagens e postbacks de páginas Meta e os consolida na mesma conversa usada pelos demais canais do lead.',
    accountHint: 'Page ID da página atendida',
    requiredEvents: ['messages', 'messaging_postbacks', 'message_deliveries'],
    externalSteps: [
      'Vincule a página ao aplicativo e confirme o administrador responsável.',
      'Cadastre callback e token de verificação e assine os eventos necessários.',
      'Conclua permissões, revisão e modo de produção antes de receber usuários externos.',
      'Teste entrada, resposta e recibo sem criar uma segunda identidade para o mesmo lead.',
    ],
    officialDocs: 'https://developers.facebook.com/docs/messenger-platform/webhooks',
  },
  tiktok_business: {
    summary: 'Prepara mensagens comerciais e eventos de conta TikTok para a conversa canônica, preservando limites e capacidades do provedor.',
    accountHint: 'Business Account ID ou Advertiser ID',
    requiredEvents: ['business messaging', 'account webhook', 'evento de teste'],
    externalSteps: [
      'Associe o aplicativo à organização e à conta comercial correta.',
      'Cadastre a configuração de webhook para Business Messaging e eventos autorizados.',
      'Mapeie o identificador externo para a identidade canônica antes de responder.',
      'Teste mensagem recebida, resposta e falha de janela na trilha de eventos.',
    ],
    officialDocs: 'https://business-api.tiktok.com/portal',
  },
  google_ads: {
    summary: 'Recebe leads dos formulários do Google Ads via Lead Form Webhook, com validação por google_key e deduplicação por lead_id, e guarda o gcl_id para fechar o ciclo de conversão.',
    accountHint: 'Customer ID (CID) da conta Google Ads, formato 000-000-0000',
    requiredEvents: ['WebhookLead', 'lead de teste (is_test)', 'gcl_id preservado'],
    externalSteps: [
      'No Google Ads, edite o recurso de formulário de lead da campanha e abra a seção de entrega de leads por webhook.',
      'Cole a URL do webhook desta conexão e defina a chave (google_key); cadastre a mesma chave aqui.',
      'Use o botão "Enviar dados de teste" do Google Ads e confira o lead de teste no recibo desta página.',
      'Vincule a conta à MCC do Meu Executivo para relatórios e conversões; conversões offline sobem via Data Manager API (a partir de 15/06/2026 a Google Ads API não aceita mais esses uploads).',
    ],
    officialDocs: 'https://developers.google.com/google-ads/webhook/docs/implementation',
  },
  google_tag_manager: {
    summary: 'Estrutura o contêiner de tags do site do escritório (GA4 + conversões Google Ads) com Consent Mode v2, base do tracking liso de ponta a ponta.',
    accountHint: 'ID do contêiner GTM, formato GTM-XXXXXXX',
    requiredEvents: ['consent default/update', 'form_submit', 'conversão Google Ads'],
    externalSteps: [
      'Crie o contêiner web em tagmanager.google.com e instale o snippet nas páginas do site.',
      'Configure o banner de consentimento (LGPD) gravando os sinais do Consent Mode v2: ad_storage, analytics_storage, ad_user_data e ad_personalization.',
      'Adicione a tag Google (GA4) e a tag de conversão do Google Ads condicionadas ao consentimento.',
      'Publique o contêiner e valide no modo Preview; para tracking first-party avançado, adicione um contêiner server-side em subdomínio próprio.',
    ],
    officialDocs: 'https://developers.google.com/tag-platform/tag-manager',
  },
  ga4: {
    summary: 'Envia eventos de funil server-side (lead recebido, proposta, contrato, cobrança paga) direto ao GA4 via Measurement Protocol, sem depender de JavaScript do site.',
    accountHint: 'Measurement ID do data stream, formato G-XXXXXXXXXX',
    requiredEvents: ['lead_recebido', 'proposta_enviada', 'cobranca_paga'],
    externalSteps: [
      'No GA4, abra Administrador → Fluxos de dados → seu stream web e copie o Measurement ID.',
      'Ainda no stream, crie um segredo em "Chaves secretas da API do Measurement Protocol" (api_secret).',
      'Cadastre Measurement ID e api_secret nesta conexão (o segredo fica no cofre, nunca no navegador).',
      'Dispare um evento de teste e confira no relatório em tempo real do GA4; eventos retroativos valem até 3 dias.',
    ],
    officialDocs: 'https://support.google.com/analytics/answer/9900444',
  },
  whatsapp_cloud: {
    summary: 'Conecta o número oficial do escritório via WhatsApp Cloud API (Meta) para atendimento, disparos com template e a esteira de leads.',
    accountHint: 'ID do número de telefone e WABA ID da conta WhatsApp Business',
    requiredEvents: ['messages', 'message_status', 'template aprovado'],
    externalSteps: [
      'No Meta for Developers, crie/abra o app e adicione o produto WhatsApp, vinculando a conta WhatsApp Business (WABA).',
      'Cadastre o número, gere o token permanente de sistema e registre-o nesta conexão.',
      'Configure o webhook com a URL e o verify token exibidos aqui e assine os campos messages e message_status.',
      'Aprove ao menos um template de primeira mensagem e envie um teste; o resultado aparece na trilha desta página.',
    ],
    officialDocs: 'https://developers.facebook.com/docs/whatsapp/cloud-api',
  },
  pluga: {
    summary: 'Traz leads e eventos de mais de 100 ferramentas brasileiras via automação Pluga apontando para o webhook do workspace.',
    accountHint: 'Nome da automação criada na Pluga',
    requiredEvents: ['lead.submitted', 'evento de teste', 'dedupe por event_id'],
    externalSteps: [
      'Na Pluga, crie uma automação com a ferramenta de origem desejada como gatilho.',
      'Escolha "Webhook" como ação de destino e cole a URL do webhook de leads deste workspace.',
      'Mapeie os campos nome, e-mail e telefone (e UTMs, se houver) para o payload.',
      'Dispare o teste da automação e confirme o lead chegando no CRM com a origem correta.',
    ],
    officialDocs: 'https://github.com/PlugaDotCo/pluga-docs-developers',
  },
  zapier: {
    summary: 'Rota alternativa por webhook para fluxos que já vivem no ecossistema Zapier internacional.',
    accountHint: 'Nome do Zap criado no Zapier',
    requiredEvents: ['lead.submitted', 'evento de teste', 'dedupe por event_id'],
    externalSteps: [
      'No Zapier, crie um Zap com a ferramenta de origem como Trigger.',
      'Adicione a action "Webhooks by Zapier" (POST) e cole a URL do webhook de leads deste workspace.',
      'Monte o corpo JSON com nome, e-mail, telefone e um event_id estável para deduplicação.',
      'Rode o teste do Zap e confirme o lead no CRM antes de publicar o Zap.',
    ],
    officialDocs: 'https://zapier.com/apps/webhook/integrations',
  },
};

const stateLabels: Record<string, string> = {
  received: 'Recebido',
  processing: 'Processando',
  queued: 'Aguardando hidratação',
  fetching: 'Buscando dados',
  hydrated: 'Hidratado',
  retry_wait: 'Nova tentativa',
  retrying: 'Nova tentativa agendada',
  blocked_external: 'Bloqueado pelo gate externo',
  processed: 'Processado',
  completed: 'Processado',
  failed: 'Falhou',
  dead_letter: 'Intervenção necessária',
  cancelled: 'Cancelado',
};

function providerLabel(provider: string) {
  return providerCatalog.find((item) => item.value === provider)?.label
    ?? provider.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function stateTone(state: string): 'green' | 'red' | 'gold' | 'blue' | 'muted' {
  if (['completed', 'processed', 'delivered', 'hydrated'].includes(state)) return 'green';
  if (['failed', 'dead_letter', 'cancelled', 'blocked_external'].includes(state)) return 'red';
  if (['received'].includes(state)) return 'blue';
  return 'gold';
}

function safeKey(value: string) {
  return value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 70);
}

function generateStrongSecret(byteLength = 32) {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
}

export default function IntegrationsPage() {
  const appStore = useOptionalAppStore();
  const canManage = appStore?.can('integrations', 'manage') ?? false;
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get('tab') ?? 'visao-geral';
  const provider = searchParams.get('provider') ?? 'all';
  const state = searchParams.get('state') ?? 'all';
  const connectionId = searchParams.get('connection');
  const [query, setQuery] = useState('');
  const [personalizedFilter, setPersonalizedFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);
  const [connectionDialogOpen, setConnectionDialogOpen] = useState(false);
  const [credentialConnection, setCredentialConnection] = useState<KlmChannelConnection | null>(null);
  const [connectionDraft, setConnectionDraft] = useState({
    provider: 'meta_ads',
    displayName: '',
    connectionKey: '',
    externalAccountId: '',
  });
  const [credentialDraft, setCredentialDraft] = useState({ signingSecret: '', verifyToken: '', tolerance: '300' });

  const centerQuery = useQuery({
    queryKey: ['klm', 'webhook-center', provider, state],
    queryFn: () => klmApi.webhookCenter({
      provider: provider === 'all' ? undefined : provider,
      state: state === 'all' ? undefined : state,
      limit: 300,
    }),
  });
  const center = centerQuery.data;
  const assistantProvider = (providerCatalog.some((item) => item.value === provider) ? provider : 'meta_ads') as ProviderValue;
  const assistantGuide = providerGuides[assistantProvider];
  const assistantConnections = (center?.connections ?? []).filter((item) => item.connection.provider === assistantProvider);
  const assistantConnection = assistantConnections.find((item) => item.connection.id === connectionId)
    ?? (assistantConnections.length === 1 ? assistantConnections[0] : null);
  const assistantMilestones = [
    { label: 'Conexão cadastrada', completed: Boolean(assistantConnection) },
    { label: 'Identificador da conta informado', completed: Boolean(assistantConnection?.connection.externalAccountId) },
    { label: 'Assinatura protegida', completed: assistantConnection?.credential?.state === 'active' },
    { label: 'Endpoint exclusivo disponível', completed: Boolean(assistantConnection?.credential?.webhookPath) },
    { label: 'Primeiro evento recebido', completed: Boolean(assistantConnection?.receiptCount) },
    { label: 'Ativação externa aprovada', completed: Boolean(center?.externalGateOpen) },
  ];
  const assistantJourneyProgress = Math.round((assistantMilestones.filter((item) => item.completed).length / assistantMilestones.length) * 100);
  const providerOptions = useMemo(() => {
    const values = new Set(providerCatalog.map((item) => item.value as string));
    center?.connections.forEach((item) => values.add(item.connection.provider));
    return [...values];
  }, [center?.connections]);
  const receiptFields = useMemo<PersonalizedFilterField<KlmWebhookCenterReceipt>[]>(() => [
    { key: 'provider', label: 'Provedor', type: 'select', options: providerOptions.map((value) => ({ value, label: providerLabel(value) })) },
    { key: 'connectionKey', label: 'Conexão', type: 'text' },
    { key: 'externalEventId', label: 'ID do evento', type: 'text' },
    { key: 'externalLeadId', label: 'ID do lead', type: 'text' },
    { key: 'processingState', label: 'Estado', type: 'select', options: Object.entries(stateLabels).map(([value, label]) => ({ value, label })) },
    { key: 'signatureScheme', label: 'Assinatura', type: 'text' },
    { key: 'receivedAt', label: 'Recebido em', type: 'date' },
    { key: 'errorCode', label: 'Código de erro', type: 'text' },
  ], [providerOptions]);
  const receipts = useMemo(() => (center?.receipts ?? []).filter((receipt) => {
    const haystack = `${receipt.externalEventId} ${receipt.externalLeadId ?? ''} ${receipt.connectionKey} ${receipt.provider}`.toLocaleLowerCase('pt-BR');
    return haystack.includes(query.toLocaleLowerCase('pt-BR'))
      && matchesPersonalizedFilter(receipt, personalizedFilter, receiptFields);
  }), [center?.receipts, personalizedFilter, query, receiptFields]);

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ['klm', 'webhook-center'] });
    await queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-connections'] });
  };
  const createConnectionMutation = useMutation({
    mutationFn: () => {
      const definition = providerCatalog.find((item) => item.value === connectionDraft.provider)!;
      return klmApi.createOmnichannelConnection({
        channel: definition.channel,
        provider: connectionDraft.provider,
        connectionKey: connectionDraft.connectionKey || `${connectionDraft.provider}:${safeKey(connectionDraft.displayName)}`,
        displayName: connectionDraft.displayName,
        externalAccountId: connectionDraft.externalAccountId || null,
        transportMode: 'webhook',
        configuration: {},
      });
    },
    onSuccess: async (connection) => {
      await invalidate();
      setConnectionDialogOpen(false);
      const next = new URLSearchParams(searchParams);
      next.set('provider', connection.provider);
      next.set('connection', connection.id);
      setSearchParams(next, { replace: true });
      setCredentialConnection(connection);
      toast({ title: 'Conexão criada', description: 'Agora configure a assinatura do webhook.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível criar a conexão', description: error.message }),
  });
  const credentialMutation = useMutation({
    mutationFn: ({ connection, action }: { connection: KlmChannelConnection; action: 'save' | 'rotate' | 'disable' | 'enable' }) => {
      const current = center?.connections.find((item) => item.connection.id === connection.id)?.credential;
      return klmApi.configureWebhookCredential(connection.id, {
        expectedVersion: current?.version ?? 0,
        action,
        ...(action === 'save' || action === 'rotate' ? { signingSecret: credentialDraft.signingSecret } : {}),
        ...((action === 'save' || action === 'rotate') && connection.provider.startsWith('meta_') && credentialDraft.verifyToken
          ? { verifyToken: credentialDraft.verifyToken }
          : {}),
        ...(action === 'save' || action === 'rotate' ? { timestampToleranceSeconds: Number(credentialDraft.tolerance) } : {}),
      });
    },
    onSuccess: async (_, variables) => {
      await invalidate();
      setCredentialConnection(null);
      setCredentialDraft({ signingSecret: '', verifyToken: '', tolerance: '300' });
      toast({ title: variables.action === 'disable' ? 'Webhook pausado' : variables.action === 'enable' ? 'Webhook reativado' : 'Credencial protegida atualizada' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Alteração recusada', description: error.message }),
  });
  const validationMutation = useMutation({
    mutationFn: klmApi.validateOmnichannelConnection,
    onSuccess: (result) => toast({
      title: result.readyForExternalSetup ? 'Configuração local pronta' : 'Há dados de configuração pendentes',
      description: result.readyForExternalSetup ? 'Nenhuma API externa foi chamada.' : result.missingConfiguration.join(', '),
    }),
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Validação indisponível', description: error.message }),
  });

  const [measurementDraft, setMeasurementDraft] = useState({ datasetId: '', pixelId: '', accessToken: '' });
  const measurementConfigQuery = useQuery({
    queryKey: ['klm', 'ads-measurement-config'],
    queryFn: () => klmApi.getAdsMeasurementConfig(),
    enabled: tab === 'mensuracao',
  });
  useEffect(() => {
    if (measurementConfigQuery.data) {
      setMeasurementDraft({
        datasetId: measurementConfigQuery.data.datasetId ?? '',
        pixelId: measurementConfigQuery.data.pixelId ?? '',
        accessToken: '',
      });
    }
  }, [measurementConfigQuery.data]);
  const saveMeasurementMutation = useMutation({
    mutationFn: () => klmApi.saveAdsMeasurementConfig({
      datasetId: measurementDraft.datasetId.trim() || null,
      pixelId: measurementDraft.pixelId.trim() || null,
      accessToken: measurementDraft.accessToken.trim() || undefined,
    }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['klm', 'ads-measurement-config'] });
      setMeasurementDraft((current) => ({ ...current, accessToken: '' }));
      toast({ title: 'Mensuração Meta salva', description: 'O token, se informado, foi cifrado antes de gravar.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível salvar', description: error.message }),
  });

  const [googleDmDraft, setGoogleDmDraft] = useState({ customerId: '', developerToken: '', accessToken: '' });
  // Evita que a leitura assíncrona da config sobrescreva o que o usuário já
  // começou a digitar (mesma corrida que existiria sem essa guarda: digitar
  // customerId antes da query resolver zeraria o campo de volta).
  const googleDmTouchedRef = useRef(false);
  const googleDmConfigQuery = useQuery({
    queryKey: ['klm', 'tracking', 'google-data-manager', 'status'],
    queryFn: () => klmApi.googleDataManagerTrackingStatus(),
    enabled: tab === 'mensuracao',
  });
  useEffect(() => {
    if (googleDmConfigQuery.data && !googleDmTouchedRef.current) {
      setGoogleDmDraft({
        customerId: googleDmConfigQuery.data.customerId ?? '',
        developerToken: '',
        accessToken: '',
      });
    }
  }, [googleDmConfigQuery.data]);
  const saveGoogleDmMutation = useMutation({
    mutationFn: () => klmApi.saveGoogleDataManagerTracking({
      customerId: googleDmDraft.customerId.trim(),
      developerToken: googleDmDraft.developerToken.trim() || undefined,
      accessToken: googleDmDraft.accessToken.trim() || undefined,
    }),
    onSuccess: async (savedStatus: KlmGoogleDataManagerTrackingStatus) => {
      queryClient.setQueryData(['klm', 'tracking', 'google-data-manager', 'status'], savedStatus);
      setGoogleDmDraft((current) => ({ ...current, developerToken: '', accessToken: '' }));
      toast({ title: 'Mensuração Google salva', description: 'Os tokens, se informados, foram cifrados antes de gravar.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível salvar', description: error.message }),
  });

  const updateFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value === 'all') next.delete(key);
    else next.set(key, value);
    if (key === 'provider') next.delete('connection');
    setSearchParams(next, { replace: true });
  };
  const openConnectionView = (connection: KlmChannelConnection) => {
    const next = new URLSearchParams(searchParams);
    next.set('tab', 'conexoes');
    next.set('provider', connection.provider);
    next.set('connection', connection.id);
    setSearchParams(next, { replace: true });
  };
  const closeCredential = () => {
    setCredentialConnection(null);
    setCredentialDraft({ signingSecret: '', verifyToken: '', tolerance: '300' });
  };
  const openCredential = (connection: KlmChannelConnection) => {
    setCredentialDraft({ signingSecret: '', verifyToken: '', tolerance: '300' });
    setCredentialConnection(connection);
  };
  const openNewConnection = (selectedProvider: ProviderValue = assistantProvider) => {
    const definition = providerCatalog.find((item) => item.value === selectedProvider)!;
    setConnectionDraft({
      provider: selectedProvider,
      displayName: `${definition.label} principal`,
      connectionKey: `${selectedProvider}-principal`,
      externalAccountId: '',
    });
    setConnectionDialogOpen(true);
  };
  const copyEndpoint = async (path: string) => {
    await navigator.clipboard.writeText(resolveApiHttpUrl(path, window.location.origin));
    toast({ title: 'Endpoint copiado.' });
  };

  return (
    <div className="mx-auto w-full min-w-0 max-w-[1440px] space-y-5">
      <header className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Infraestrutura de canais</p>
          <h2 className="mt-2 text-2xl font-semibold">Integrações e webhooks</h2>
          <p className="mt-1 max-w-3xl text-xs leading-4 text-muted-foreground">Conexões, assinaturas, recibos, hidratação e vínculo com a conversa em uma única lâmina nativa.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <KlmPill tone={center?.externalGateOpen ? 'green' : 'muted'}>{center?.externalGateOpen ? 'gate externo aberto' : 'somente local'}</KlmPill>
          <Button asChild variant="outline" className="h-9 text-xs">
            <Link to="/app/conexoes"><Cable className="mr-2 h-3.5 w-3.5" />Central de Conexão</Link>
          </Button>
          <Button variant="outline" className="h-9 text-xs" onClick={() => void centerQuery.refetch()} disabled={centerQuery.isFetching}>
            {centerQuery.isFetching ? <LoaderCircle className="mr-2 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-2 h-3.5 w-3.5" />}Atualizar
          </Button>
          <Button className="h-9 bg-[#031121] text-xs text-white hover:bg-[#031121]" onClick={() => openNewConnection()} disabled={!canManage}>
            <Plus className="mr-2 h-3.5 w-3.5" />Nova conexão
          </Button>
        </div>
      </header>

      {centerQuery.isError && (
        <KlmPanel role="alert" className="border-red-300 bg-red-50 p-4 text-red-950 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex min-w-0 items-start gap-3">
              <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
              <div><p className="text-xs font-semibold">Não foi possível confirmar o registro nativo</p><p className="mt-1 text-2xs leading-4 opacity-80">{centerQuery.error instanceof Error ? `${centerQuery.error.message}. ` : 'A central não respondeu. '}Nenhum indicador abaixo deve ser interpretado como zero.</p></div>
            </div>
            <Button variant="outline" className="h-8 shrink-0 text-2xs" onClick={() => void centerQuery.refetch()}><RefreshCw className="mr-1.5 h-3 w-3" />Tentar novamente</Button>
          </div>
        </KlmPanel>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-8">
        <KlmMetricCard label="Conexões" value={center ? String(center.connectionCount) : '—'} delta={center ? `${center.configuredConnectionCount} configuradas` : 'aguardando leitura'} />
        <KlmMetricCard label="Credenciais ativas" value={center ? String(center.activeCredentialCount) : '—'} delta="criptografadas" trend={center ? 'up' : 'neutral'} />
        <KlmMetricCard label="Eventos encontrados" value={center ? String(center.receiptMatchCount) : '—'} delta={center ? `${center.receiptWindowCount} exibidos · ${center.receiptTotalCount} no total` : 'aguardando leitura'} />
        <KlmMetricCard label="Processados" value={center ? String(center.processedCount) : '—'} delta="vínculo concluído" trend={center ? 'up' : 'neutral'} />
        <KlmMetricCard label="Em hidratação" value={center ? String(center.awaitingHydrationCount) : '—'} delta="fila durável" />
        <KlmMetricCard label="Bloqueados pelo gate" value={center ? String(center.blockedCount) : '—'} delta="aguardam liberação externa" />
        <KlmMetricCard label="Falhas" value={center ? String(center.failedCount) : '—'} delta="exigem revisão" trend={center?.failedCount ? 'down' : 'neutral'} />
        <KlmMetricCard label="Idempotência" value={center ? (center.idempotencyEnforced ? 'Ativa' : 'Inativa') : '—'} delta="evento único por provedor" trend={center?.idempotencyEnforced ? 'up' : 'neutral'} />
      </div>

      <Tabs value={tab} onValueChange={(value) => updateFilter('tab', value)} className="space-y-4">
        <TabsList className="h-9 max-w-full justify-start overflow-x-auto bg-secondary p-1">
          <TabsTrigger value="visao-geral" className="text-xs">Visão geral</TabsTrigger>
          <TabsTrigger value="conexoes" className="text-xs">Conexões</TabsTrigger>
          <TabsTrigger value="eventos" className="text-xs">Eventos recebidos</TabsTrigger>
          <TabsTrigger value="assistente" className="text-xs">Assistente de integração</TabsTrigger>
          <TabsTrigger value="mensuracao" className="text-xs">Mensuração de conversões</TabsTrigger>
        </TabsList>

        <TabsContent value="visao-geral" className="space-y-4">
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
            <KlmPanel className="p-4">
              <KlmSectionTitle title="Prontidão por conexão" description="Cada percentual deriva de evidências persistidas, não de uma marcação visual." action={<KlmPill tone="green">antirreplay ativo</KlmPill>} />
              <div className="mt-4 divide-y rounded-lg border">
                {(center?.connections ?? []).map((item) => (
                  <button key={item.connection.id} type="button" onClick={() => openConnectionView(item.connection)} className="grid w-full gap-3 p-4 text-left transition-colors hover:bg-secondary sm:grid-cols-[minmax(180px,1fr)_170px_120px_120px] sm:items-center">
                    <span className="min-w-0"><strong className="block truncate text-xs">{item.connection.displayName}</strong><small className="mt-1 block truncate text-2xs text-muted-foreground">{providerLabel(item.connection.provider)} · {item.connection.connectionKey}</small></span>
                    <span><span className="mb-1 flex justify-between text-2xs"><span>Configuração</span><strong>{item.setupProgress}%</strong></span><KlmProgress value={item.setupProgress} tone={item.setupProgress === 100 ? 'green' : 'gold'} /></span>
                    <span className="text-2xs"><strong className="block text-sm">{item.receiptCount}</strong><small className="text-muted-foreground">eventos</small></span>
                    <KlmPill tone={item.failedCount ? 'red' : item.blockedCount ? 'gold' : item.setupProgress === 100 ? 'green' : 'gold'}>{item.failedCount ? `${item.failedCount} falha(s)` : item.blockedCount ? `${item.blockedCount} bloqueado(s)` : item.setupProgress === 100 ? 'Pronta' : 'Configurar'}</KlmPill>
                  </button>
                ))}
                {!centerQuery.isPending && !center?.connections.length && <div className="p-10 text-center text-2xs text-muted-foreground">Nenhuma conexão pública cadastrada. Use “Nova conexão” para começar.</div>}
              </div>
            </KlmPanel>
            <KlmPanel tone="black" className="p-5">
              <ShieldCheck className="h-5 w-5 text-primary" />
              <h2 className="mt-4 text-lg font-semibold">O que a plataforma comprova</h2>
              <div className="mt-4 space-y-3 text-2xs leading-5 text-white/65">
                <p>Assinatura validada antes de qualquer processamento.</p>
                <p>Hash do payload e recibo imutável para auditoria.</p>
                <p>Chave única impede repetir o mesmo evento.</p>
                <p>Lead hidratado só aparece como processado quando há vínculo persistido.</p>
              </div>
              <Button variant="outline" onClick={() => updateFilter('tab', 'eventos')} className="mt-5 w-full border-white/20 bg-transparent text-xs text-white hover:bg-white/10 hover:text-white">Ver trilha de eventos</Button>
            </KlmPanel>
          </div>
        </TabsContent>

        <TabsContent value="conexoes">
          <KlmPanel className="p-4">
            <KlmSectionTitle title="Conexões públicas" description="Gerencie endpoint, segredo, token de verificação, pausa e validação local." />
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              {(center?.connections ?? []).map((item) => (
                <article key={item.connection.id} className="rounded-lg border p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0"><h3 className="truncate text-sm font-semibold">{item.connection.displayName}</h3><p className="mt-1 truncate text-2xs text-muted-foreground">{providerLabel(item.connection.provider)} · {item.connection.connectionKey}</p></div>
                    <KlmPill tone={item.credential?.state === 'active' ? 'green' : item.credential ? 'red' : 'gold'}>{item.credential?.state === 'active' ? 'Assinatura ativa' : item.credential ? 'Pausada' : 'Sem credencial'}</KlmPill>
                  </div>
                  <div className="mt-4"><div className="mb-1 flex justify-between text-2xs"><span>Prontidão</span><strong>{item.setupProgress}%</strong></div><KlmProgress value={item.setupProgress} tone={item.setupProgress === 100 ? 'green' : 'gold'} /></div>
                  <div className="mt-4 space-y-2">
                    {item.setupSteps.map((step) => <div key={step.key} className="flex items-start gap-2 text-2xs"><span className={`mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full ${step.completed ? 'bg-emerald-600 text-white' : 'bg-secondary text-muted-foreground'}`}>{step.completed ? <Check className="h-2.5 w-2.5" /> : <span className="h-1 w-1 rounded-full bg-current" />}</span><span><strong className="block">{step.label}</strong><small className="text-muted-foreground">{step.detail}</small></span></div>)}
                  </div>
                  {item.credential && <div className="mt-4 flex min-w-0 items-center gap-2 rounded-md bg-secondary p-2.5"><Webhook className="h-3.5 w-3.5 shrink-0 text-[#0057d8]" /><code className="min-w-0 flex-1 truncate text-2xs">{item.credential.webhookPath}</code><Button size="icon" variant="ghost" className="h-7 w-7" onClick={() => void copyEndpoint(item.credential!.webhookPath)} aria-label="Copiar endpoint"><Clipboard className="h-3.5 w-3.5" /></Button></div>}
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Button size="sm" variant="outline" className="h-8 text-2xs" onClick={() => validationMutation.mutate(item.connection.id)} disabled={!canManage || validationMutation.isPending}><CircleCheck className="mr-1.5 h-3 w-3" />Validar local</Button>
                    <Button size="sm" variant="outline" className="h-8 text-2xs" onClick={() => openCredential(item.connection)} disabled={!canManage}><KeyRound className="mr-1.5 h-3 w-3" />{item.credential ? 'Rotacionar' : 'Configurar'}</Button>
                    {item.credential && <Button size="sm" variant="ghost" className="h-8 text-2xs" onClick={() => credentialMutation.mutate({ connection: item.connection, action: item.credential?.state === 'active' ? 'disable' : 'enable' })} disabled={!canManage || credentialMutation.isPending}>{item.credential.state === 'active' ? <Pause className="mr-1.5 h-3 w-3" /> : <Play className="mr-1.5 h-3 w-3" />}{item.credential.state === 'active' ? 'Pausar' : 'Reativar'}</Button>}
                  </div>
                </article>
              ))}
            </div>
          </KlmPanel>
        </TabsContent>

        <TabsContent value="eventos">
          <KlmPanel className="p-4">
            <KlmSectionTitle title="Trilha de recebimento" description="Da assinatura até o lead, caso e conversa, sem expor o segredo recebido." action={<KlmPill tone="blue">{center ? `${center.receiptMatchCount} encontrados · até 300 exibidos` : 'aguardando'}</KlmPill>} />
            <div className="mt-4 flex flex-col gap-2 lg:flex-row">
              <div className="relative min-w-0 flex-1"><Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" /><Input value={query} onChange={(event) => setQuery(event.target.value)} className="h-9 pl-9 text-xs" placeholder="Buscar ID do evento, lead ou conexão" /></div>
              <Select value={provider} onValueChange={(value) => updateFilter('provider', value)}><SelectTrigger className="h-9 w-full text-xs lg:w-48"><SelectValue placeholder="Provedor" /></SelectTrigger><SelectContent><SelectItem value="all">Todos os provedores</SelectItem>{providerOptions.map((value) => <SelectItem key={value} value={value}>{providerLabel(value)}</SelectItem>)}</SelectContent></Select>
              <Select value={state} onValueChange={(value) => updateFilter('state', value)}><SelectTrigger className="h-9 w-full text-xs lg:w-48"><SelectValue placeholder="Estado" /></SelectTrigger><SelectContent><SelectItem value="all">Todos os estados</SelectItem>{Object.entries(stateLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
              <PersonalizedFilterBuilder scope="webhook-events" fields={receiptFields} value={personalizedFilter} onChange={setPersonalizedFilter} className="h-9" />
            </div>
            <div className="mt-4 overflow-x-auto rounded-lg border">
              <table className="w-full min-w-[1120px] text-left text-2xs">
                <thead><tr className="border-b bg-secondary text-muted-foreground"><th className="p-3">Recebido</th><th className="p-3">Origem</th><th className="p-3">Evento externo</th><th className="p-3">Assinatura</th><th className="p-3">Processamento</th><th className="p-3">Vínculos</th><th className="p-3">Hash do payload</th></tr></thead>
                <tbody>{receipts.map((receipt) => <tr key={receipt.id} className="border-b last:border-0 hover:bg-secondary"><td className="p-3 whitespace-nowrap">{new Date(receipt.receivedAt).toLocaleString('pt-BR')}</td><td className="p-3"><strong className="block">{providerLabel(receipt.provider)}</strong><small className="text-muted-foreground">{receipt.connectionKey}</small></td><td className="p-3"><code className="block max-w-[210px] truncate">{receipt.externalEventId}</code><small className="text-muted-foreground">lead {receipt.externalLeadId ?? 'não informado'}</small></td><td className="p-3"><KlmPill tone="green">validada</KlmPill><small className="mt-1 block text-muted-foreground">{receipt.signatureScheme}</small></td><td className="p-3"><KlmPill tone={stateTone(receipt.processingState)}>{stateLabels[receipt.processingState] ?? receipt.processingState}</KlmPill>{receipt.errorCode && <small className="mt-1 block text-red-700">{receipt.errorCode}</small>}</td><td className="p-3"><div className="flex flex-wrap gap-1.5">{receipt.threadId && <Button asChild variant="outline" size="sm" className="h-7 text-2xs"><Link to={`/app/conversas?thread=${receipt.threadId}${receipt.matterId ? `&matter=${receipt.matterId}` : ''}`}>Conversa<ExternalLink className="ml-1 h-2.5 w-2.5" /></Link></Button>}{receipt.matterId && <Button asChild variant="outline" size="sm" className="h-7 text-2xs"><Link to={`/app/leads?matter=${receipt.matterId}`}>Caso<ExternalLink className="ml-1 h-2.5 w-2.5" /></Link></Button>}{!receipt.threadId && !receipt.matterId && <span className="text-muted-foreground">aguardando vínculo</span>}</div></td><td className="p-3"><code className="block max-w-[170px] truncate" title={receipt.payloadSha256}>{receipt.payloadSha256}</code></td></tr>)}</tbody>
              </table>
              {!centerQuery.isPending && !receipts.length && <div className="p-10 text-center text-2xs text-muted-foreground">Nenhum recibo corresponde aos filtros.</div>}
            </div>
          </KlmPanel>
        </TabsContent>

        <TabsContent value="assistente">
          <div className="space-y-4">
            <KlmPanel className="p-4">
              <KlmSectionTitle title="Escolha o provedor" description="Cada guia usa o mesmo contrato nativo de conexão, assinatura, recibo, deduplicação e vínculo com o lead." action={<KlmPill tone="gold">configuração assistida</KlmPill>} />
              <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                {providerCatalog.map((item) => (
                  <button
                    key={item.value}
                    type="button"
                    onClick={() => updateFilter('provider', item.value)}
                    className={`min-h-16 rounded-md border p-3 text-left transition-colors ${assistantProvider === item.value ? 'border-primary bg-primary/10' : 'hover:bg-secondary'}`}
                  >
                    <strong className="block text-2xs">{item.label}</strong>
                    <small className="mt-1 block text-2xs text-muted-foreground">{item.channel.replaceAll('_', ' ')}</small>
                  </button>
                ))}
              </div>
            </KlmPanel>

            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
              <KlmPanel className="p-4">
                <KlmSectionTitle title={`Integração guiada · ${providerLabel(assistantProvider)}`} description={assistantGuide.summary} />
                {assistantConnections.length > 1 && (
                  <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950/20">
                    <Label htmlFor="assistant-connection" className="text-2xs">Conexão que será configurada</Label>
                    <Select value={assistantConnection?.connection.id ?? '__select__'} onValueChange={(value) => {
                      if (value === '__select__') return;
                      const next = new URLSearchParams(searchParams);
                      next.set('connection', value);
                      setSearchParams(next, { replace: true });
                    }}>
                      <SelectTrigger id="assistant-connection" className="mt-2 h-9 bg-background text-2xs"><SelectValue /></SelectTrigger>
                      <SelectContent><SelectItem value="__select__" disabled>Selecione uma conexão</SelectItem>{assistantConnections.map((item) => <SelectItem key={item.connection.id} value={item.connection.id}>{item.connection.displayName} · {item.connection.connectionKey}</SelectItem>)}</SelectContent>
                    </Select>
                    {!assistantConnection && <p className="mt-2 text-2xs leading-4 text-amber-950 dark:text-amber-100">Há mais de uma conexão deste provedor. Escolha explicitamente uma delas antes de validar, copiar o endpoint ou alterar credenciais.</p>}
                  </div>
                )}
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <div className="rounded-lg border p-4">
                    <div className="flex items-center justify-between text-2xs"><span>Prontidão local</span><strong>{assistantConnection?.setupProgress ?? 0}%</strong></div>
                    <div className="mt-2"><KlmProgress value={assistantConnection?.setupProgress ?? 0} tone={assistantConnection?.setupProgress === 100 ? 'green' : 'gold'} /></div>
                  </div>
                  <div className="rounded-lg border p-4">
                    <div className="flex items-center justify-between text-2xs"><span>Jornada completa</span><strong>{assistantJourneyProgress}%</strong></div>
                    <div className="mt-2"><KlmProgress value={assistantJourneyProgress} tone={assistantJourneyProgress === 100 ? 'green' : 'gold'} /></div>
                  </div>
                </div>

                <div className="mt-4 grid gap-4 lg:grid-cols-2">
                  <section className="rounded-lg border p-4">
                    <h3 className="text-sm font-semibold">Checklist verificável</h3>
                    <div className="mt-3 space-y-2">
                      {assistantMilestones.map((step) => (
                        <div key={step.label} className="flex items-center gap-2 text-2xs">
                          <span className={`grid h-4 w-4 shrink-0 place-items-center rounded-full ${step.completed ? 'bg-emerald-600 text-white' : 'bg-secondary text-muted-foreground'}`}>{step.completed ? <Check className="h-2.5 w-2.5" /> : <span className="h-1 w-1 rounded-full bg-current" />}</span>
                          <span>{step.label}</span>
                        </div>
                      ))}
                    </div>
                  </section>
                  <section className="rounded-lg border p-4">
                    <h3 className="text-sm font-semibold">Eventos esperados</h3>
                    <div className="mt-3 flex flex-wrap gap-1.5">{assistantGuide.requiredEvents.map((eventName) => <KlmPill key={eventName} tone="blue">{eventName}</KlmPill>)}</div>
                    <p className="mt-4 text-2xs leading-4 text-muted-foreground"><strong className="text-foreground">Identificador:</strong> {assistantGuide.accountHint}.</p>
                  </section>
                </div>

                <ol className="mt-5 divide-y rounded-lg border">
                  {[
                    ['1', 'Cadastre a conta', 'Escolha o provedor, nome interno, chave estável e ID da conta de anúncios ou página.'],
                    ['2', 'Proteja a assinatura', 'Informe segredo e token de verificação. O banco guarda somente a versão criptografada.'],
                    ['3', 'Copie o endpoint', 'Cadastre a URL exclusiva no painel oficial do provedor e selecione os eventos permitidos.'],
                    ['4', 'Valide o desafio', 'A resposta de challenge e toda assinatura recebida são conferidas antes de ler o payload.'],
                    ['5', 'Acompanhe o primeiro evento', 'O recibo aparece nesta central com hidratação, erro e vínculos para conversa e caso.'],
                    ['6', 'Libere externamente depois', 'Credenciais oficiais e gate externo só serão ativados após sua aprovação específica.'],
                  ].map(([number, title, description]) => <li key={number} className="grid gap-3 p-4 sm:grid-cols-[32px_1fr]"><span className="grid h-8 w-8 place-items-center rounded-full bg-[#031121] text-2xs font-semibold text-white">{number}</span><span><strong className="block text-xs">{title}</strong><small className="mt-1 block text-2xs leading-4 text-muted-foreground">{description}</small></span></li>)}
                </ol>
              </KlmPanel>
              <div className="space-y-4">
                <KlmPanel tone="black" className="p-5">
                  <Activity className="h-5 w-5 text-primary" />
                  <h2 className="mt-4 text-lg font-semibold">Ações desta conexão</h2>
                  <div className="mt-4 space-y-2 text-2xs">
                    {center?.failedCount ? <p className="flex gap-2 text-red-300"><CircleAlert className="h-4 w-4 shrink-0" />{center.failedCount} evento(s) exigem revisão.</p> : <p className="flex gap-2 text-emerald-300"><CircleCheck className="h-4 w-4 shrink-0" />Nenhuma falha na janela atual.</p>}
                    {center?.blockedCount ? <p className="flex gap-2 text-amber-300"><ShieldCheck className="h-4 w-4 shrink-0" />{center.blockedCount} evento(s) preservados aguardam abertura do gate.</p> : null}
                    <p className="flex gap-2 text-white/65"><ShieldCheck className="h-4 w-4 shrink-0 text-primary" />Deduplicação transacional ativa.</p>
                    <p className="flex gap-2 text-white/65"><KeyRound className="h-4 w-4 shrink-0 text-primary" />Segredos nunca retornam pela API.</p>
                  </div>
                  <div className="mt-5 grid gap-2">
                    {!assistantConnection && assistantConnections.length === 0 && <Button onClick={() => openNewConnection(assistantProvider)} disabled={!canManage} className="w-full text-xs"><Plus className="mr-2 h-3.5 w-3.5" />Criar conexão</Button>}
                    {!assistantConnection && assistantConnections.length > 1 && <p className="rounded-md border border-white/15 p-3 text-2xs leading-4 text-white/70">Selecione a conexão acima para liberar ações sem risco de operar a conta errada.</p>}
                    {assistantConnection && !assistantConnection.credential && <Button onClick={() => openCredential(assistantConnection.connection)} disabled={!canManage} className="w-full text-xs"><KeyRound className="mr-2 h-3.5 w-3.5" />Proteger assinatura</Button>}
                    {assistantConnection?.credential && <Button onClick={() => validationMutation.mutate(assistantConnection.connection.id)} disabled={!canManage || validationMutation.isPending} className="w-full text-xs"><CircleCheck className="mr-2 h-3.5 w-3.5" />Validar configuração local</Button>}
                    {assistantConnection?.credential?.webhookPath && <Button variant="outline" onClick={() => void copyEndpoint(assistantConnection.credential!.webhookPath)} className="w-full border-white/20 bg-transparent text-xs text-white hover:bg-white/10 hover:text-white"><Clipboard className="mr-2 h-3.5 w-3.5" />Copiar endpoint</Button>}
                    <Button variant="outline" onClick={() => updateFilter('tab', center?.failedCount ? 'eventos' : 'conexoes')} className="w-full border-white/20 bg-transparent text-xs text-white hover:bg-white/10 hover:text-white">{center?.failedCount ? 'Revisar falhas' : 'Revisar conexões'}</Button>
                  </div>
                </KlmPanel>
                <KlmPanel className="p-4">
                  <h3 className="text-sm font-semibold">Passo a passo no provedor</h3>
                  <ol className="mt-3 space-y-3">{assistantGuide.externalSteps.map((step, index) => <li key={step} className="grid grid-cols-[20px_1fr] gap-2 text-2xs leading-4"><span className="grid h-5 w-5 place-items-center rounded-full bg-secondary text-2xs font-semibold">{index + 1}</span><span>{step}</span></li>)}</ol>
                  {assistantGuide.officialDocs && <Button asChild variant="outline" className="mt-4 w-full text-2xs"><a href={assistantGuide.officialDocs} target="_blank" rel="noreferrer">Documentação oficial<ExternalLink className="ml-2 h-3 w-3" /></a></Button>}
                  <div className="mt-4 rounded-md border border-amber-300 bg-amber-50 p-3 text-2xs leading-4 text-amber-950">Nenhuma ativação externa ocorre nesta fase. OAuth, tokens oficiais e assinatura do provedor continuam bloqueados até aprovação.</div>
                </KlmPanel>
              </div>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="mensuracao">
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
            <div className="flex flex-col gap-4">
              <KlmPanel className="p-4">
                <KlmSectionTitle
                  title="Mensuração Meta (CAPI)"
                  description="Dataset/pixel e token da Conversions API usados pelo despachante de feedback de conversões (Rastreamento → Feedback aos Ads). O token nunca volta pela API."
                  action={<KlmPill tone={measurementConfigQuery.data?.configured ? 'green' : 'gold'}>{measurementConfigQuery.data?.configured ? 'Configurado' : 'Pendente'}</KlmPill>}
                />
                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor="capi-dataset-id">Dataset ID (ou Pixel ID legado)</Label>
                    <Input id="capi-dataset-id" value={measurementDraft.datasetId} onChange={(event) => setMeasurementDraft((current) => ({ ...current, datasetId: event.target.value }))} placeholder="1234567890123456" disabled={!canManage} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="capi-pixel-id">Pixel ID (opcional)</Label>
                    <Input id="capi-pixel-id" value={measurementDraft.pixelId} onChange={(event) => setMeasurementDraft((current) => ({ ...current, pixelId: event.target.value }))} placeholder="Opcional" disabled={!canManage} />
                  </div>
                  <div className="space-y-1.5 sm:col-span-2">
                    <Label htmlFor="capi-token">Token de acesso CAPI</Label>
                    <Input id="capi-token" type="password" autoComplete="new-password" value={measurementDraft.accessToken} onChange={(event) => setMeasurementDraft((current) => ({ ...current, accessToken: event.target.value }))} placeholder={measurementConfigQuery.data?.accessTokenConfigured ? `Configurado (•••• ${measurementConfigQuery.data.accessTokenLast4 ?? '----'}). Deixe vazio para manter.` : 'Cole o token de acesso do dataset'} disabled={!canManage} />
                    <p className="text-2xs text-muted-foreground">Guardado cifrado (Fernet); esta tela nunca exibe o valor salvo, só os últimos 4 caracteres.</p>
                  </div>
                </div>
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <Button onClick={() => saveMeasurementMutation.mutate()} disabled={!canManage || saveMeasurementMutation.isPending || (!measurementDraft.datasetId.trim() && !measurementConfigQuery.data?.configured)} className="h-9 text-xs">
                    {saveMeasurementMutation.isPending ? <LoaderCircle className="mr-2 h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="mr-2 h-3.5 w-3.5" />}Salvar mensuração
                  </Button>
                  {measurementConfigQuery.data?.updatedAt && <span className="text-2xs text-muted-foreground">Atualizado em {new Date(measurementConfigQuery.data.updatedAt).toLocaleString('pt-BR')}</span>}
                </div>
                {!canManage && <p className="mt-3 text-2xs text-muted-foreground">Permissão integrations.manage necessária para editar.</p>}
              </KlmPanel>
              <KlmPanel className="p-4">
                <KlmSectionTitle
                  title="Mensuração Google (Data Manager)"
                  description="Customer ID, developer token e access token da Data Manager API — único caminho suportado para conversão offline ao Google desde o bloqueio do ClickConversionService (15/06/2026). Os tokens nunca voltam pela API."
                  action={<KlmPill tone={googleDmConfigQuery.data?.configured ? 'green' : 'gold'}>{googleDmConfigQuery.data?.configured ? 'Configurado' : 'Pendente'}</KlmPill>}
                />
                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                  <div className="space-y-1.5 sm:col-span-2">
                    <Label htmlFor="gdm-customer-id">Customer ID (Google Ads)</Label>
                    <Input id="gdm-customer-id" value={googleDmDraft.customerId} onChange={(event) => { googleDmTouchedRef.current = true; setGoogleDmDraft((current) => ({ ...current, customerId: event.target.value })); }} placeholder="123-456-7890" disabled={!canManage} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="gdm-developer-token">Developer token</Label>
                    <Input id="gdm-developer-token" type="password" autoComplete="new-password" value={googleDmDraft.developerToken} onChange={(event) => { googleDmTouchedRef.current = true; setGoogleDmDraft((current) => ({ ...current, developerToken: event.target.value })); }} placeholder={googleDmConfigQuery.data?.hasDeveloperToken ? 'Configurado. Deixe vazio para manter.' : 'Developer token da conta MCC'} disabled={!canManage} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="gdm-access-token">Access token</Label>
                    <Input id="gdm-access-token" type="password" autoComplete="new-password" value={googleDmDraft.accessToken} onChange={(event) => { googleDmTouchedRef.current = true; setGoogleDmDraft((current) => ({ ...current, accessToken: event.target.value })); }} placeholder={googleDmConfigQuery.data?.hasAccessToken ? 'Configurado. Deixe vazio para manter.' : 'Access token OAuth'} disabled={!canManage} />
                  </div>
                  <p className="text-2xs text-muted-foreground sm:col-span-2">Guardados cifrados (Fernet); esta tela nunca exibe os valores salvos, só se já estão configurados.</p>
                </div>
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <Button onClick={() => saveGoogleDmMutation.mutate()} disabled={!canManage || saveGoogleDmMutation.isPending || !googleDmDraft.customerId.trim()} className="h-9 text-xs">
                    {saveGoogleDmMutation.isPending ? <LoaderCircle className="mr-2 h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="mr-2 h-3.5 w-3.5" />}Salvar mensuração
                  </Button>
                </div>
                {!canManage && <p className="mt-3 text-2xs text-muted-foreground">Permissão integrations.manage necessária para editar.</p>}
              </KlmPanel>
            </div>
            <KlmPanel tone="black" className="p-5">
              <KeyRound className="h-5 w-5 text-primary" />
              <h2 className="mt-4 text-lg font-semibold">Como isso é usado</h2>
              <p className="mt-2 text-2xs leading-5 text-white/60">O worker de despacho (Rastreamento → aba Feedback aos Ads) consome as conversões já preparadas e, somente quando o gate de ações externas estiver aberto e a conversão aprovada localmente, envia este dataset/token à Conversions API ou à Data Manager API, conforme o provider da conversão. Enquanto o gate estiver fechado, cada tentativa gera um recibo marcado como simulado, nunca um envio real.</p>
              <ul className="mt-4 space-y-2 text-2xs text-white/70">
                <li className="flex gap-2"><Check className="h-3.5 w-3.5 shrink-0 text-emerald-400" />Dataset/pixel/customer ID e tokens por workspace</li>
                <li className="flex gap-2"><Check className="h-3.5 w-3.5 shrink-0 text-emerald-400" />Tokens cifrados (mesma proteção de outras credenciais)</li>
                <li className="flex gap-2"><Check className="h-3.5 w-3.5 shrink-0 text-emerald-400" />WhatsApp usa o formato business_messaging + ctwa_clid automaticamente</li>
              </ul>
            </KlmPanel>
          </div>
        </TabsContent>
      </Tabs>

      <Dialog open={connectionDialogOpen} onOpenChange={setConnectionDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader><DialogTitle>Nova conexão pública</DialogTitle><DialogDescription>Cadastre a identidade da conexão sem ativar nenhuma chamada externa.</DialogDescription></DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="space-y-1.5"><Label>Provedor</Label><Select value={connectionDraft.provider} onValueChange={(value) => {
              const definition = providerCatalog.find((item) => item.value === value)!;
              setConnectionDraft({ provider: value, displayName: `${definition.label} principal`, connectionKey: `${value}-principal`, externalAccountId: '' });
            }}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{providerCatalog.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent></Select></div>
            <div className="space-y-1.5"><Label>Nome interno</Label><Input value={connectionDraft.displayName} onChange={(event) => setConnectionDraft((current) => ({ ...current, displayName: event.target.value }))} placeholder="Meta Ads · Saúde" /></div>
            <div className="space-y-1.5"><Label>Chave estável</Label><Input value={connectionDraft.connectionKey} onChange={(event) => setConnectionDraft((current) => ({ ...current, connectionKey: event.target.value }))} placeholder="meta_ads:saude" /></div>
            <div className="space-y-1.5"><Label>ID da conta, página ou anunciante</Label><Input value={connectionDraft.externalAccountId} onChange={(event) => setConnectionDraft((current) => ({ ...current, externalAccountId: event.target.value }))} placeholder="Opcional nesta fase" /></div>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setConnectionDialogOpen(false)}>Cancelar</Button><Button onClick={() => createConnectionMutation.mutate()} disabled={!canManage || connectionDraft.displayName.trim().length < 2 || connectionDraft.connectionKey.trim().length < 2 || createConnectionMutation.isPending}>{createConnectionMutation.isPending ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}Criar conexão</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(credentialConnection)} onOpenChange={(open) => { if (!open) closeCredential(); }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader><DialogTitle>{center?.connections.some((item) => item.connection.id === credentialConnection?.id && item.credential) ? 'Rotacionar credencial' : 'Configurar credencial'}</DialogTitle><DialogDescription>{credentialConnection?.displayName}. Os valores não serão devolvidos pela API depois de salvos.</DialogDescription></DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-2"><Label>Segredo de assinatura</Label><Button type="button" variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setCredentialDraft((current) => ({ ...current, signingSecret: generateStrongSecret() }))}><KeyRound className="mr-1.5 h-3 w-3" />Gerar forte</Button></div>
              <Input aria-label="Segredo de assinatura" type="password" autoComplete="new-password" minLength={32} value={credentialDraft.signingSecret} onChange={(event) => setCredentialDraft((current) => ({ ...current, signingSecret: event.target.value }))} />
              <p className="text-2xs text-muted-foreground">Mínimo de 32 caracteres. Use o gerador para obter 256 bits aleatórios.</p>
            </div>
            {credentialConnection?.provider.startsWith('meta_') && <div className="space-y-1.5"><div className="flex items-center justify-between gap-2"><Label>Token de verificação Meta</Label><Button type="button" variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setCredentialDraft((current) => ({ ...current, verifyToken: generateStrongSecret(24) }))}><KeyRound className="mr-1.5 h-3 w-3" />Gerar token</Button></div><Input aria-label="Token de verificação Meta" type="password" autoComplete="new-password" minLength={16} value={credentialDraft.verifyToken} onChange={(event) => setCredentialDraft((current) => ({ ...current, verifyToken: event.target.value }))} placeholder={center?.connections.some((item) => item.connection.id === credentialConnection.id && item.credential) ? 'Deixe vazio para manter o atual' : ''} /><p className="text-2xs text-muted-foreground">Mínimo de 16 caracteres; numa rotação, vazio mantém o token atual.</p></div>}
            <div className="space-y-1.5"><Label>Janela antirreplay</Label><Select value={credentialDraft.tolerance} onValueChange={(value) => setCredentialDraft((current) => ({ ...current, tolerance: value }))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="120">2 minutos</SelectItem><SelectItem value="300">5 minutos</SelectItem><SelectItem value="600">10 minutos</SelectItem></SelectContent></Select></div>
          </div>
          <DialogFooter><Button variant="outline" onClick={closeCredential}>Cancelar</Button><Button onClick={() => credentialConnection && credentialMutation.mutate({ connection: credentialConnection, action: center?.connections.some((item) => item.connection.id === credentialConnection.id && item.credential) ? 'rotate' : 'save' })} disabled={!credentialConnection || credentialDraft.signingSecret.length < 32 || (credentialConnection.provider.startsWith('meta_') && Boolean(credentialDraft.verifyToken) && credentialDraft.verifyToken.length < 16) || (credentialConnection.provider.startsWith('meta_') && !center?.connections.some((item) => item.connection.id === credentialConnection.id && item.credential) && credentialDraft.verifyToken.length < 16) || credentialMutation.isPending}>{credentialMutation.isPending ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <RotateCcw className="mr-2 h-4 w-4" />}Salvar protegida</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
