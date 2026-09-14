import { useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, MessageCircleMore, PhoneCall, Plug, Send, ShieldCheck, Wrench, XCircle } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useToast } from '@/hooks/use-toast';
import {
  connectPlatformOfficialWhatsAppEmbedded,
  connectPlatformOfficialWhatsAppManual,
  disconnectPlatformOfficialWhatsApp,
  fetchPlatformOfficialWhatsAppConnections,
  fetchPlatformWhatsAppMetaOperations,
  fetchPlatformWorkspaceWhatsAppMetaOperations,
  platformOfficialWhatsAppUncertainOperationId,
  previewPlatformWhatsAppMetaSubscriptions,
  reconcilePlatformWhatsAppMetaSubscriptions,
  retryPlatformWhatsAppMetaReceipt,
  setPlatformOfficialWhatsAppActive,
  testPlatformOfficialWhatsApp,
  updatePlatformWhatsAppMetaAutomationPolicy,
} from '@/services/platform';
import type {
  PlatformOfficialWhatsAppConnectManual,
  PlatformOfficialWhatsAppConnection,
  PlatformOfficialWhatsAppEmbeddedCallback,
  PlatformOfficialWhatsAppRole,
} from '@/types';
import { MetaEmbeddedSignupButton } from '@/components/meta/MetaEmbeddedSignupButton';
import { fetchMetaEmbeddedSignupConfig } from '@/services/whatsapp';
import { MetaWebhookOperationsPanel } from '@/components/whatsapp/MetaWebhookOperationsPanel';
import { StudioWorkspaceCombobox } from '@/components/platform/StudioWorkspaceCombobox';
import type { StudioWorkspaceOption } from '@/services/platform-configuration-studio';

const ROLE_META: Record<PlatformOfficialWhatsAppRole, { title: string; description: string; icon: ReactNode }> = {
  notifications: {
    title: 'Notificações',
    description: 'Usado para alertas operacionais, lembretes e digest da manhã.',
    icon: <ShieldCheck className="h-4 w-4" />,
  },
  outbound: {
    title: 'Disparo',
    description: 'Usado para disparos oficiais da plataforma e campanhas institucionais.',
    icon: <Send className="h-4 w-4" />,
  },
  support: {
    title: 'Suporte',
    description: 'Usado para atendimento e suporte institucional da Meu Executivo.',
    icon: <PhoneCall className="h-4 w-4" />,
  },
};

function emptyManualDraft(): PlatformOfficialWhatsAppConnectManual {
  return {
    accessToken: '',
    phoneNumberId: '',
    businessAccountId: '',
    registrationPin: '',
    displayName: '',
    isActive: true,
  };
}

export function PlatformOfficialWhatsAppPanel() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [manualDrafts, setManualDrafts] = useState<Record<PlatformOfficialWhatsAppRole, PlatformOfficialWhatsAppConnectManual>>({
    notifications: emptyManualDraft(),
    outbound: emptyManualDraft(),
    support: emptyManualDraft(),
  });
  const [testPhones, setTestPhones] = useState<Record<PlatformOfficialWhatsAppRole, string>>({
    notifications: '',
    outbound: '',
    support: '',
  });
  const [webhookWorkspace, setWebhookWorkspace] = useState<StudioWorkspaceOption | null>(null);

  const connectionsQuery = useQuery({
    queryKey: ['platform-official-whatsapp'],
    queryFn: fetchPlatformOfficialWhatsAppConnections,
    staleTime: 15_000,
  });
  const metaConfigQuery = useQuery({
    queryKey: ['meta-embedded-signup-config'],
    queryFn: fetchMetaEmbeddedSignupConfig,
    staleTime: 60_000,
  });

  const connections = useMemo(() => {
    const base = connectionsQuery.data ?? [];
    return {
      notifications: base.find((item) => item.role === 'notifications'),
      outbound: base.find((item) => item.role === 'outbound'),
      support: base.find((item) => item.role === 'support'),
    } as Record<PlatformOfficialWhatsAppRole, PlatformOfficialWhatsAppConnection | undefined>;
  }, [connectionsQuery.data]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['platform-official-whatsapp'] });

  const manualMutation = useMutation({
    mutationFn: ({ role, payload }: { role: PlatformOfficialWhatsAppRole; payload: PlatformOfficialWhatsAppConnectManual }) =>
      connectPlatformOfficialWhatsAppManual(role, payload),
    onSuccess: (_, variables) => {
      void invalidate();
      setManualDrafts((current) => ({ ...current, [variables.role]: emptyManualDraft() }));
      toast({ title: `Canal ${ROLE_META[variables.role].title.toLowerCase()} conectado.` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao conectar canal oficial', description: error.message }),
  });

  const embeddedMutation = useMutation({
    mutationFn: ({ role, payload }: { role: PlatformOfficialWhatsAppRole; payload: PlatformOfficialWhatsAppEmbeddedCallback }) =>
      connectPlatformOfficialWhatsAppEmbedded(role, payload),
    onSuccess: (_, variables) => {
      void invalidate();
      toast({ title: `Canal ${ROLE_META[variables.role].title.toLowerCase()} conectado via Meta.` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Falha no Embedded Signup', description: error.message }),
  });

  const activeMutation = useMutation({
    mutationFn: ({ role, isActive }: { role: PlatformOfficialWhatsAppRole; isActive: boolean }) =>
      setPlatformOfficialWhatsAppActive(role, { isActive }),
    onSuccess: (_, variables) => {
      void invalidate();
      toast({ title: `Canal ${ROLE_META[variables.role].title.toLowerCase()} ${variables.isActive ? 'ativado' : 'desativado'}.` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao alterar status do canal', description: error.message }),
  });

  const disconnectMutation = useMutation({
    mutationFn: (role: PlatformOfficialWhatsAppRole) => disconnectPlatformOfficialWhatsApp(role),
    onSuccess: (_, role) => {
      void invalidate();
      toast({ title: `Canal ${ROLE_META[role].title.toLowerCase()} desconectado.` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao desconectar canal', description: error.message }),
  });

  const testMutation = useMutation({
    mutationFn: ({ role, to }: { role: PlatformOfficialWhatsAppRole; to: string }) =>
      testPlatformOfficialWhatsApp(role, {
        to,
        body: `Teste do canal oficial ${ROLE_META[role].title} da Meu Executivo.`,
      }),
    onSuccess: (result, variables) => {
      toast({ title: `Teste enviado pelo canal ${ROLE_META[variables.role].title.toLowerCase()}.`, description: result.message });
    },
    onError: (error: Error) => {
      const operationId = platformOfficialWhatsAppUncertainOperationId(error);
      if (operationId) {
        toast({
          title: 'Resultado do envio não confirmado — não repita',
          description: `A Meta pode ter aceitado a mensagem. Reconcilie a operação ${operationId} antes de um novo envio.`,
        });
        return;
      }
      toast({ variant: 'destructive', title: 'Erro ao enviar teste', description: error.message });
    },
  });

  const setManualDraft = (role: PlatformOfficialWhatsAppRole, patch: Partial<PlatformOfficialWhatsAppConnectManual>) => {
    setManualDrafts((current) => ({
      ...current,
      [role]: { ...current[role], ...patch },
    }));
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">WhatsApps Oficiais</h2>
        <p className="text-sm text-muted-foreground">
          Conecte os três números oficiais da plataforma para notificações, disparos e suporte usando Meta Manual ou Embedded Signup.
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Diagnóstico técnico: Meta Graph API {metaConfigQuery.data?.graphApiVersion ?? 'indisponível'}.
          {' '}Coexistência {metaConfigQuery.data?.coexistenceAvailable ? 'disponível' : 'pendente'};
          Tech Provider {metaConfigQuery.data?.techProviderReady ? 'completo' : 'aguardando Partner Solution'}.
        </p>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        {(['notifications', 'outbound', 'support'] as PlatformOfficialWhatsAppRole[]).map((role) => {
          const connection = connections[role];
          const meta = ROLE_META[role];
          const manualDraft = manualDrafts[role];
          const isBusy = manualMutation.isPending || disconnectMutation.isPending || embeddedMutation.isPending || activeMutation.isPending || testMutation.isPending;
          return (
            <Card key={role}>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  {meta.icon}
                  {meta.title}
                </CardTitle>
                <CardDescription>{meta.description}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex flex-wrap gap-2">
                  <Badge variant="outline">status: {connection?.status ?? 'disconnected'}</Badge>
                  <Badge variant={connection?.isActive ? 'secondary' : 'outline'}>
                    {connection?.isActive ? 'ativo' : 'inativo'}
                  </Badge>
                  {connection?.connectionType ? <Badge variant="outline">{connection.connectionType}</Badge> : null}
                </div>

                <div className="rounded-2xl border p-4 space-y-1">
                  <p className="text-sm font-medium">{connection?.displayName ?? 'Canal ainda não conectado'}</p>
                  <p className="text-xs text-muted-foreground">{connection?.displayPhoneNumber ?? 'Sem número oficial configurado.'}</p>
                  {connection?.lastError ? <p className="text-xs text-destructive">{connection.lastError}</p> : null}
                  {connection?.connectionType === 'TECH_PROVIDER' && connection.tokenLifecycleState === 'unknown' ? (
                    <p className="text-xs text-amber-700">Vencimento não informado; reconecte pelo Facebook para registrar a validade.</p>
                  ) : null}
                  {connection?.tokenLifecycleState === 'expiring' ? (
                    <p className="text-xs font-medium text-amber-700">Token vence em {connection.tokenExpiresInDays ?? 0} dia(s).</p>
                  ) : null}
                  {connection?.tokenLifecycleState === 'expired' ? (
                    <p className="text-xs font-medium text-destructive">Token expirado; reconexão obrigatória.</p>
                  ) : null}
                </div>

                <div className="rounded-2xl border border-dashed p-4 space-y-2 text-xs text-muted-foreground">
                  <p className="font-medium text-foreground">Checklist Meta</p>
                  <div className="flex items-center justify-between gap-2">
                    <span>Phone Number ID informado</span>
                    <Badge variant={connection?.phoneNumberId ? 'secondary' : 'outline'}>{connection?.phoneNumberId ? 'ok' : 'pendente'}</Badge>
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    <span>WABA vinculado</span>
                    <Badge variant={connection?.wabaId || connection?.businessAccountId ? 'secondary' : 'outline'}>
                      {connection?.wabaId || connection?.businessAccountId ? 'ok' : 'pendente'}
                    </Badge>
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    <span>Webhook inscrito</span>
                    <Badge variant={connection?.webhookRegistered ? 'secondary' : 'outline'}>
                      {connection?.webhookRegistered ? 'ok' : 'revisar'}
                    </Badge>
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    <span>Canal apto para envio</span>
                    <Badge variant={connection?.status === 'connected' && connection?.isActive ? 'secondary' : 'outline'}>
                      {connection?.status === 'connected' && connection?.isActive ? 'pronto' : 'incompleto'}
                    </Badge>
                  </div>
                </div>

                <div className="flex items-center justify-between rounded-2xl border px-4 py-3">
                  <div>
                    <p className="text-sm font-medium">Canal ativo</p>
                    <p className="text-xs text-muted-foreground">Quando desligado, não é usado pela plataforma.</p>
                  </div>
                  <Switch
                    checked={Boolean(connection?.isActive)}
                    onCheckedChange={(checked) => activeMutation.mutate({ role, isActive: checked })}
                    disabled={!connection?.id || isBusy}
                  />
                </div>

                <Tabs defaultValue="manual" className="space-y-3">
                  <TabsList className="grid w-full grid-cols-2">
                    <TabsTrigger value="manual">Meta manual</TabsTrigger>
                    <TabsTrigger value="embedded">Embedded</TabsTrigger>
                  </TabsList>

                  <TabsContent value="manual" className="space-y-3">
                    <div className="space-y-1.5">
                      <Label>Access Token</Label>
                      <Input
                        type="password"
                        value={manualDraft.accessToken}
                        onChange={(event) => setManualDraft(role, { accessToken: event.target.value })}
                        placeholder="EAA..."
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label>Phone Number ID</Label>
                      <Input
                        value={manualDraft.phoneNumberId}
                        onChange={(event) => setManualDraft(role, { phoneNumberId: event.target.value })}
                        placeholder="1234567890123"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label>WABA ID</Label>
                      <Input
                        value={manualDraft.businessAccountId}
                        onChange={(event) => setManualDraft(role, { businessAccountId: event.target.value })}
                        placeholder="987654321"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label>PIN de registro (opcional)</Label>
                      <Input
                        value={manualDraft.registrationPin ?? ''}
                        onChange={(event) => setManualDraft(role, { registrationPin: event.target.value })}
                        placeholder="123456"
                        maxLength={6}
                      />
                    </div>
                    <Button
                      className="w-full"
                      onClick={() => manualMutation.mutate({ role, payload: manualDraft })}
                      disabled={!manualDraft.accessToken || !manualDraft.phoneNumberId || !manualDraft.businessAccountId || isBusy}
                    >
                      {manualMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plug className="mr-2 h-4 w-4" />}
                      Conectar via Meta manual
                    </Button>
                  </TabsContent>

                  <TabsContent value="embedded" className="space-y-3">
                    <div className="rounded-2xl border bg-muted/20 p-4 text-xs text-muted-foreground">
                      O Embedded Signup abre o fluxo oficial do Facebook para o número deste papel sem precisar colar credenciais manualmente.
                    </div>
                    <MetaEmbeddedSignupButton
                      disabled={embeddedMutation.isPending}
                      label="Conectar com Facebook"
                      onComplete={(result) => embeddedMutation.mutateAsync({
                        role,
                        payload: { ...result, isActive: true },
                      }).then(() => undefined)}
                    />
                  </TabsContent>
                </Tabs>

                <div className="space-y-2 rounded-2xl border p-4">
                  <Label>Teste rápido</Label>
                  <Input
                    value={testPhones[role]}
                    onChange={(event) => setTestPhones((current) => ({ ...current, [role]: event.target.value }))}
                    placeholder="+55 11 99999-0000"
                  />
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      className="flex-1"
                      onClick={() => testMutation.mutate({ role, to: testPhones[role] })}
                      disabled={!testPhones[role] || !connection?.id || isBusy}
                    >
                      <MessageCircleMore className="mr-2 h-4 w-4" />
                      Testar envio
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() => disconnectMutation.mutate(role)}
                      disabled={!connection?.id || isBusy}
                    >
                      {disconnectMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <XCircle className="h-4 w-4" />}
                    </Button>
                  </div>
                </div>

                <div className="rounded-2xl border border-dashed p-3 text-xs text-muted-foreground">
                  <div className="flex items-start gap-2">
                    <Wrench className="mt-0.5 h-4 w-4 shrink-0" />
                    <p>
                      O canal <strong>{meta.title}</strong> fica pronto para uso oficial da plataforma. O papel de
                      notificações é o usado pelo digest da manhã e pelos alertas operacionais dos workspaces.
                    </p>
                  </div>
                  <p className="mt-2 text-2xs">
                    Para uso real, valide no Meta Developers: permissões aprovadas, webhook verificando corretamente,
                    número registrado e token permanente da system user ainda válido.
                  </p>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
      <div className="space-y-3">
        <div className="space-y-2">
          <Label htmlFor="studio-workspace">Conta para operações dos webhooks</Label>
          <StudioWorkspaceCombobox
            value={webhookWorkspace?.id ?? ''}
            selected={webhookWorkspace}
            onChange={setWebhookWorkspace}
          />
          <p className="text-xs text-muted-foreground">
            Sem conta selecionada, o Blackbox mostra somente a visão sanitizada transversal. Reconciliação, retry e políticas sempre exigem uma conta explícita.
          </p>
        </div>
        <MetaWebhookOperationsPanel
          queryKey={['platform-whatsapp-meta-operations', webhookWorkspace?.id ?? 'all']}
          queryFn={webhookWorkspace
            ? () => fetchPlatformWorkspaceWhatsAppMetaOperations(webhookWorkspace.id)
            : fetchPlatformWhatsAppMetaOperations}
          title="Webhooks Meta da plataforma"
          canManageSubscriptions={Boolean(webhookWorkspace?.canWrite)}
          canManagePolicies={Boolean(webhookWorkspace?.canWrite)}
          previewSubscriptions={webhookWorkspace ? () => previewPlatformWhatsAppMetaSubscriptions(webhookWorkspace.id) : undefined}
          reconcileSubscriptions={webhookWorkspace ? () => reconcilePlatformWhatsAppMetaSubscriptions(webhookWorkspace.id) : undefined}
          retryReceipt={webhookWorkspace ? (receiptId) => retryPlatformWhatsAppMetaReceipt(webhookWorkspace.id, receiptId) : undefined}
          updatePolicy={webhookWorkspace ? (field, payload) => updatePlatformWhatsAppMetaAutomationPolicy(webhookWorkspace.id, field, payload) : undefined}
        />
      </div>
    </div>
  );
}
