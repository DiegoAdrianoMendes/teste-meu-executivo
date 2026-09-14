import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AtSign,
  BadgeCheck,
  CheckCircle2,
  CirclePause,
  CirclePlay,
  Cloud,
  Facebook,
  FileInput,
  Globe2,
  Instagram,
  Loader2,
  Mail,
  MessageCircleMore,
  Pencil,
  Plus,
  RotateCcw,
  Save,
  ShieldCheck,
  Smartphone,
  Target,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { klmApi, type KlmChannelConnection, type KlmOmnichannelChannel } from '@/klm/api';

type ProfileKey =
  | 'whatsapp_cloud'
  | 'whatsapp_coexistence'
  | 'instagram'
  | 'facebook_page'
  | 'messenger'
  | 'tiktok'
  | 'email_google'
  | 'email_microsoft'
  | 'meta_ads'
  | 'tiktok_ads'
  | 'site'
  | 'lovable';

type ConnectionProfile = {
  label: string;
  channel: KlmOmnichannelChannel;
  provider: string;
  transportMode: string;
  icon: typeof Smartphone;
  fields: Array<{ key: string; label: string; placeholder: string }>;
};

const profiles: Record<ProfileKey, ConnectionProfile> = {
  whatsapp_cloud: {
    label: 'WhatsApp Cloud API', channel: 'whatsapp', provider: 'meta_cloud', transportMode: 'cloud_api', icon: Smartphone,
    fields: [{ key: 'phoneNumberId', label: 'Phone Number ID', placeholder: 'ID do número' }, { key: 'wabaId', label: 'WABA ID', placeholder: 'ID da conta WhatsApp' }, { key: 'businessAccountId', label: 'Business Account ID', placeholder: 'ID do negócio' }],
  },
  whatsapp_coexistence: {
    label: 'WhatsApp Coexistence', channel: 'whatsapp', provider: 'meta_cloud', transportMode: 'coexistence', icon: Cloud,
    fields: [{ key: 'phoneNumberId', label: 'Phone Number ID', placeholder: 'ID do mesmo número usado no App' }, { key: 'wabaId', label: 'WABA ID', placeholder: 'ID da conta WhatsApp' }, { key: 'businessAccountId', label: 'Business Account ID', placeholder: 'ID do negócio' }],
  },
  instagram: {
    label: 'Instagram', channel: 'instagram', provider: 'meta_instagram', transportMode: 'official_api', icon: Instagram,
    fields: [{ key: 'pageId', label: 'Page ID', placeholder: 'Página vinculada' }, { key: 'instagramAccountId', label: 'Instagram Account ID', placeholder: 'Conta profissional' }],
  },
  facebook_page: {
    label: 'Página do Facebook', channel: 'facebook', provider: 'meta_messenger', transportMode: 'official_api', icon: Facebook,
    fields: [{ key: 'pageId', label: 'Page ID', placeholder: 'Página vinculada' }],
  },
  messenger: {
    label: 'Messenger', channel: 'messenger', provider: 'meta_messenger', transportMode: 'official_api', icon: Facebook,
    fields: [{ key: 'pageId', label: 'Page ID', placeholder: 'Página vinculada' }],
  },
  tiktok: {
    label: 'TikTok Business', channel: 'tiktok', provider: 'tiktok_business', transportMode: 'official_api', icon: MessageCircleMore,
    fields: [{ key: 'businessAccountId', label: 'Business Account ID', placeholder: 'Conta do negócio' }, { key: 'advertiserId', label: 'Advertiser ID', placeholder: 'Conta de anúncios' }],
  },
  email_google: {
    label: 'Google Workspace', channel: 'email', provider: 'google_mail', transportMode: 'oauth', icon: Mail,
    fields: [{ key: 'accountAddress', label: 'E-mail', placeholder: 'atendimento@escritorio.com.br' }],
  },
  email_microsoft: {
    label: 'Microsoft 365', channel: 'email', provider: 'microsoft_graph', transportMode: 'oauth', icon: AtSign,
    fields: [{ key: 'accountAddress', label: 'E-mail', placeholder: 'atendimento@escritorio.com.br' }],
  },
  meta_ads: {
    label: 'Meta Ads', channel: 'facebook', provider: 'meta_ads', transportMode: 'official_api', icon: Target,
    fields: [{ key: 'businessAccountId', label: 'Business Account ID', placeholder: 'ID do negócio' }, { key: 'adAccountId', label: 'Ad Account ID', placeholder: 'Conta de anúncios' }, { key: 'pixelId', label: 'Pixel ID', placeholder: 'Pixel para atribuição' }],
  },
  tiktok_ads: {
    label: 'TikTok Ads', channel: 'tiktok', provider: 'tiktok_ads', transportMode: 'official_api', icon: Target,
    fields: [{ key: 'businessAccountId', label: 'Business Account ID', placeholder: 'ID do negócio' }, { key: 'advertiserId', label: 'Advertiser ID', placeholder: 'Conta de anúncios' }, { key: 'pixelCode', label: 'Pixel Code', placeholder: 'Pixel para atribuição' }],
  },
  site: {
    label: 'Formulários do site', channel: 'webchat', provider: 'site', transportMode: 'webhook', icon: Globe2,
    fields: [{ key: 'formSource', label: 'Origem do formulário', placeholder: 'site-klm' }],
  },
  lovable: {
    label: 'Site Lovable', channel: 'webchat', provider: 'lovable', transportMode: 'webhook', icon: FileInput,
    fields: [{ key: 'formSource', label: 'Origem do formulário', placeholder: 'lovable-klm' }],
  },
};

const profileKeys = Object.keys(profiles) as ProfileKey[];

function slug(value: string) {
  return value.toLocaleLowerCase('pt-BR').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 64);
}

function transportLabel(value: string) {
  if (value === 'coexistence') return 'Cloud API + App';
  if (value === 'cloud_api') return 'Cloud API';
  if (value === 'official_api') return 'API oficial';
  if (value === 'oauth') return 'OAuth';
  if (value === 'webhook') return 'Webhook';
  return value.replaceAll('_', ' ');
}

function profileForConnection(connection: KlmChannelConnection): ProfileKey {
  const exact = profileKeys.find((key) => {
    const profile = profiles[key];
    return profile.channel === connection.channel
      && profile.provider === connection.provider
      && profile.transportMode === connection.transportMode;
  });
  if (exact) return exact;
  const providerMatch = profileKeys.find((key) => {
    const profile = profiles[key];
    return profile.channel === connection.channel && profile.provider === connection.provider;
  });
  if (providerMatch) return providerMatch;
  const channelMatch = profileKeys.find((key) => profiles[key].channel === connection.channel);
  return channelMatch ?? 'site';
}

function stateTone(connection: KlmChannelConnection) {
  if (connection.status === 'paused' || connection.status === 'disconnected_local') return 'muted' as const;
  if (connection.health.state === 'local_configuration_complete') return 'green' as const;
  return 'gold' as const;
}

const emptyConfiguration = (): Record<string, string> => ({});

export function OmnichannelConnectionsPanel({ readOnly = false }: { readOnly?: boolean }) {
  const queryClient = useQueryClient();
  const [profileKey, setProfileKey] = useState<ProfileKey>('whatsapp_cloud');
  const [displayName, setDisplayName] = useState('WhatsApp comercial');
  const [connectionKey, setConnectionKey] = useState('whatsapp-comercial');
  const [externalAccountId, setExternalAccountId] = useState('');
  const [credentialReference, setCredentialReference] = useState('');
  const [configuration, setConfiguration] = useState<Record<string, string>>(emptyConfiguration);
  const [isDefault, setIsDefault] = useState(true);
  const [editing, setEditing] = useState<KlmChannelConnection | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [channelFilter, setChannelFilter] = useState<'all' | KlmOmnichannelChannel>('all');

  const connectionsQuery = useQuery({
    queryKey: ['klm', 'omnichannel-connections'],
    queryFn: () => klmApi.omnichannelConnections(),
  });
  const connections = useMemo(() => connectionsQuery.data ?? [], [connectionsQuery.data]);
  const filtered = channelFilter === 'all'
    ? connections
    : connections.filter((connection) => connection.channel === channelFilter);
  const whatsappCount = connections.filter((item) => item.channel === 'whatsapp').length;
  const coexistenceCount = connections.filter((item) => item.transportMode === 'coexistence').length;
  const completeCount = connections.filter((item) => item.health.state === 'local_configuration_complete').length;
  const profile = profiles[profileKey];
  const capabilityCount = useMemo(
    () => connections.reduce((total, item) => total + Object.values(item.capabilities).filter((capability) => capability.supported !== false).length, 0),
    [connections],
  );

  const resetForm = (nextProfile: ProfileKey = 'whatsapp_cloud') => {
    const next = profiles[nextProfile];
    setEditing(null);
    setProfileKey(nextProfile);
    setDisplayName(next.label);
    setConnectionKey(`${next.channel}-${slug(next.label)}`);
    setExternalAccountId('');
    setCredentialReference('');
    setConfiguration(emptyConfiguration());
    setIsDefault(connections.every((item) => item.channel !== next.channel));
  };

  const beginEdit = (connection: KlmChannelConnection) => {
    const nextProfile = profileForConnection(connection);
    setEditing(connection);
    setProfileKey(nextProfile);
    setDisplayName(connection.displayName);
    setConnectionKey(connection.connectionKey);
    setExternalAccountId(connection.externalAccountId ?? '');
    setCredentialReference(connection.credentialReference ?? '');
    setConfiguration(Object.fromEntries(Object.entries(connection.configuration).map(([key, value]) => [key, String(value ?? '')])));
    setIsDefault(connection.isDefault);
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      const cleanConfiguration = Object.fromEntries(Object.entries(configuration).filter(([, value]) => value.trim()));
      if (editing) {
        return klmApi.updateOmnichannelConnection(editing.id, {
          expectedVersion: editing.version,
          displayName,
          externalAccountId: externalAccountId || null,
          credentialReference: credentialReference || null,
          configuration: cleanConfiguration,
          isDefault,
          action: 'save',
        });
      }
      return klmApi.createOmnichannelConnection({
        channel: profile.channel,
        provider: profile.provider,
        connectionKey,
        displayName,
        externalAccountId: externalAccountId || null,
        transportMode: profile.transportMode,
        isDefault,
        configuration: cleanConfiguration,
        credentialReference: credentialReference || null,
        routingPolicy: { canonicalConversation: true, transferEnabled: true },
      });
    },
    onSuccess: (connection) => {
      setNotice(`${connection.displayName} salva no registro local. Nenhuma conexão externa foi iniciada.`);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-connections'] });
      resetForm(profileKey);
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Não foi possível salvar a conexão.'),
  });

  const validateMutation = useMutation({
    mutationFn: (id: string) => klmApi.validateOmnichannelConnection(id),
    onSuccess: (result) => {
      setNotice(result.readyForExternalSetup
        ? 'Configuração local completa. Credenciais e OAuth continuam bloqueados até aprovação.'
        : `Campos pendentes: ${result.missingConfiguration.join(', ')}.`);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-connections'] });
    },
    onError: () => setNotice('A validação local falhou.'),
  });

  const stateMutation = useMutation({
    mutationFn: (connection: KlmChannelConnection) => klmApi.updateOmnichannelConnection(connection.id, {
      expectedVersion: connection.version,
      action: connection.status === 'paused' ? 'resume' : 'pause',
    }),
    onSuccess: (connection) => {
      setNotice(`${connection.displayName}: ${connection.status.replaceAll('_', ' ')}.`);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-connections'] });
    },
    onError: () => setNotice('A conexão foi alterada em outra sessão. Atualize e tente novamente.'),
  });

  const defaultMutation = useMutation({
    mutationFn: (connection: KlmChannelConnection) => klmApi.updateOmnichannelConnection(connection.id, {
      expectedVersion: connection.version,
      isDefault: true,
      action: 'save',
    }),
    onSuccess: (connection) => {
      setNotice(`${connection.displayName} definida como rota padrão de ${connection.channel}.`);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-connections'] });
    },
  });

  const updateProfile = (key: ProfileKey) => resetForm(key);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KlmPanel className="p-4"><span className="text-xs uppercase text-muted-foreground">Conexões</span><strong className="mt-2 block text-2xl font-bold tabular-nums">{connections.length}</strong><small className="text-2xs text-muted-foreground">{completeCount} completas localmente</small></KlmPanel>
        <KlmPanel className="p-4"><span className="text-xs uppercase text-muted-foreground">Números WhatsApp</span><strong className="mt-2 block text-2xl font-bold tabular-nums">{whatsappCount}</strong><small className="text-2xs text-muted-foreground">{coexistenceCount} em Coexistence</small></KlmPanel>
        <KlmPanel className="p-4"><span className="text-xs uppercase text-muted-foreground">Capacidades</span><strong className="mt-2 block text-2xl font-bold tabular-nums">{capabilityCount}</strong><small className="text-2xs text-muted-foreground">por canal e transporte</small></KlmPanel>
        <KlmPanel className="p-4"><span className="text-xs uppercase text-muted-foreground">Efeitos externos</span><strong className="mt-2 block text-2xl font-bold tabular-nums">0</strong><small className="text-2xs text-muted-foreground">gate local fechado</small></KlmPanel>
      </div>

      {notice && <KlmPanel className="flex items-center justify-between gap-3 border-primary/30 bg-primary/10 p-3"><div className="flex items-center gap-2 text-2xs"><BadgeCheck className="h-4 w-4 text-[#1a9948]" />{notice}</div><Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setNotice(null)}>Fechar</Button></KlmPanel>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_390px]">
        <KlmPanel className="overflow-hidden">
          <div className="flex flex-col gap-3 border-b p-4 sm:flex-row sm:items-center sm:justify-between"><KlmSectionTitle title="Registro de canais" description="Múltiplas contas convergem na mesma identidade e conversa." action={<KlmPill tone="green">nativo</KlmPill>} /><Select value={channelFilter} onValueChange={(value) => setChannelFilter(value as typeof channelFilter)}><SelectTrigger aria-label="Filtrar conexões por canal" className="h-8 w-40 text-2xs"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Todos os canais</SelectItem><SelectItem value="whatsapp">WhatsApp</SelectItem><SelectItem value="instagram">Instagram</SelectItem><SelectItem value="messenger">Messenger</SelectItem><SelectItem value="facebook">Facebook e Meta Ads</SelectItem><SelectItem value="tiktok">TikTok</SelectItem><SelectItem value="email">E-mail</SelectItem><SelectItem value="webchat">Site e Lovable</SelectItem></SelectContent></Select></div>
          <div className="divide-y">{connectionsQuery.isPending && <p className="p-10 text-center text-2xs text-muted-foreground"><Loader2 className="mx-auto mb-2 h-4 w-4 animate-spin" />Carregando registro</p>}{filtered.map((connection) => { const Icon = profiles[profileForConnection(connection)].icon; const missing = Array.isArray(connection.health.missingConfiguration) ? connection.health.missingConfiguration as string[] : []; return <div key={connection.id} className="p-4"><div className="flex items-start gap-3"><div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-secondary"><Icon className="h-4 w-4" /></div><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><strong className="text-2xs">{connection.displayName}</strong>{connection.isDefault && <KlmPill tone="gold">padrão</KlmPill>}<KlmPill tone={stateTone(connection)}>{connection.status.replaceAll('_', ' ')}</KlmPill></div><p className="mt-1 text-2xs text-muted-foreground">{connection.connectionKey} · {transportLabel(connection.transportMode)} · v{connection.version}</p>{missing.length > 0 && <p className="mt-2 text-2xs text-amber-700">Pendente: {missing.join(', ')}</p>}<div className="mt-2 flex flex-wrap gap-1">{Object.entries(connection.capabilities).filter(([, value]) => value.supported !== false).slice(0, 8).map(([key, value]) => <KlmPill key={key} tone={value.state === 'available_local' ? 'green' : 'blue'}>{key.replaceAll('_', ' ')}</KlmPill>)}</div></div><div className="flex shrink-0 gap-1"><Button variant="ghost" size="icon" className="h-8 w-8" title="Editar" disabled={readOnly} onClick={() => beginEdit(connection)}><Pencil className="h-3.5 w-3.5" /></Button><Button variant="ghost" size="icon" className="h-8 w-8" title="Validar localmente" disabled={readOnly || validateMutation.isPending} onClick={() => validateMutation.mutate(connection.id)}><CheckCircle2 className="h-3.5 w-3.5" /></Button><Button variant="ghost" size="icon" className="h-8 w-8" title={connection.status === 'paused' ? 'Ativar' : 'Pausar'} disabled={readOnly || stateMutation.isPending} onClick={() => stateMutation.mutate(connection)}>{connection.status === 'paused' ? <CirclePlay className="h-3.5 w-3.5" /> : <CirclePause className="h-3.5 w-3.5" />}</Button></div></div>{!connection.isDefault && <Button variant="link" className="mt-2 h-6 px-0 text-2xs" disabled={readOnly || defaultMutation.isPending} onClick={() => defaultMutation.mutate(connection)}>Definir como rota padrão</Button>}</div>; })}{connectionsQuery.isSuccess && !filtered.length && <p className="p-10 text-center text-2xs text-muted-foreground">Nenhuma conexão neste filtro.</p>}</div>
        </KlmPanel>

        <KlmPanel className="p-4">
          <div className="flex items-center justify-between"><div><h3 className="text-sm font-medium">{editing ? 'Editar conexão' : 'Nova conexão'}</h3><p className="mt-1 text-2xs text-muted-foreground">Configuração local sem autenticar no provedor.</p></div>{editing && <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => resetForm()} title="Cancelar edição"><RotateCcw className="h-3.5 w-3.5" /></Button>}</div>
          <div className="mt-4 space-y-3"><div className="space-y-1.5"><Label className="text-2xs">Tipo</Label><Select value={profileKey} onValueChange={(value) => updateProfile(value as ProfileKey)} disabled={Boolean(editing)}><SelectTrigger aria-label="Tipo de conexão" className="h-9 text-2xs"><SelectValue /></SelectTrigger><SelectContent>{profileKeys.map((key) => <SelectItem key={key} value={key}>{profiles[key].label}</SelectItem>)}</SelectContent></Select></div>{profileKey === 'whatsapp_coexistence' && <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-2xs leading-4 text-blue-950 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-100"><strong className="block text-2xs">Mesmo número no App e na API</strong>As mensagens manuais continuam disponíveis no WhatsApp Business App. Disparos e automações programáticas usam a Cloud API e seguem as regras aplicáveis de template, janela e qualidade.</div>}<div className="space-y-1.5"><Label className="text-2xs">Nome interno</Label><Input aria-label="Nome interno da conexão" className="h-9 text-2xs" value={displayName} onChange={(event) => { setDisplayName(event.target.value); if (!editing) setConnectionKey(`${profile.channel}-${slug(event.target.value)}`); }} /></div><div className="space-y-1.5"><Label className="text-2xs">Chave única</Label><Input aria-label="Chave única da conexão" className="h-9 text-2xs" value={connectionKey} disabled={Boolean(editing)} onChange={(event) => setConnectionKey(slug(event.target.value))} /></div>{profile.fields.map((field) => <div key={field.key} className="space-y-1.5"><Label className="text-2xs">{field.label}</Label><Input aria-label={field.label} className="h-9 text-2xs" value={configuration[field.key] ?? ''} placeholder={field.placeholder} onChange={(event) => setConfiguration((current) => ({ ...current, [field.key]: event.target.value }))} /></div>)}<div className="space-y-1.5"><Label className="text-2xs">ID externo</Label><Input aria-label="ID externo da conexão" className="h-9 text-2xs" value={externalAccountId} onChange={(event) => setExternalAccountId(event.target.value)} placeholder="Opcional nesta etapa" /></div><div className="space-y-1.5"><Label className="text-2xs">Referência da credencial</Label><Input aria-label="Referência da credencial" className="h-9 text-2xs" value={credentialReference} onChange={(event) => setCredentialReference(event.target.value)} placeholder="vault://... (não cole segredos)" /></div><div className="flex items-center justify-between rounded-md border bg-secondary p-3"><div><strong className="block text-2xs">Rota padrão</strong><span className="text-2xs text-muted-foreground">Usada para novas abordagens deste canal.</span></div><Switch aria-label="Definir como rota padrão" checked={isDefault} onCheckedChange={setIsDefault} /></div><Button className="h-9 w-full text-2xs" disabled={readOnly || saveMutation.isPending || displayName.length < 2 || connectionKey.length < 2} onClick={() => saveMutation.mutate()}>{saveMutation.isPending ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : editing ? <Save className="mr-2 h-3.5 w-3.5" /> : <Plus className="mr-2 h-3.5 w-3.5" />}{editing ? 'Salvar alterações' : 'Adicionar conexão'}</Button></div>
          <div className="mt-4 flex items-start gap-2 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-2xs text-emerald-900"><ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" /><span>Credenciais não entram neste formulário. O vínculo seguro será feito por referência no passo de APIs.</span></div>
        </KlmPanel>
      </div>
    </div>
  );
}
