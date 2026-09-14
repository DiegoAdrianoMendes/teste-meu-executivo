import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { FieldHelp, FieldHelpLabel } from '@/components/ui/field-help';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useToast } from '@/hooks/use-toast';
import { DesbLogo } from '@/components/ui/DesbLogo';
import { WhatsAppAutomationRulesPanel } from '@/components/admin/WhatsAppAutomationRulesPanel';
import { klmApi } from '@/klm/api';
import {
  fetchWhatsAppAutomationRules,
  fetchWorkspaceOnboarding,
  saveWhatsAppAutomationRules,
  saveWorkspaceOnboarding,
} from '@/services/admin';
import { listPlaybooks } from '@/services/playbooks';
import type { OnboardingStepId, WorkspaceAreaActivation, WorkspaceNotificationRecipient, WorkspaceOnboardingState } from '@/types';
import { ArrowRight, BookOpenCheck, CheckCircle2, CircleDashed, ExternalLink, LockKeyhole, MessageSquare, PlugZap, RefreshCcw, ShieldCheck } from 'lucide-react';

const STEP_ORDER: OnboardingStepId[] = [
  'welcome',
  'whatsapp',
  'areas',
  'template',
  'follow_up',
  'playbook',
  'integrations',
  'review',
];

const STEP_TITLES: Record<OnboardingStepId, string> = {
  welcome: 'Boas-vindas e contexto',
  whatsapp: 'Conectar WhatsApp',
  areas: 'Confirmar áreas liberadas',
  template: 'Escolher template inicial',
  follow_up: 'Configurar follow-up e intervenção humana',
  playbook: 'Definir política comercial e honorários',
  integrations: 'Integrações opcionais',
  review: 'Revisão final e ativação',
};

const ONBOARDING_FIELD_HELP = {
  recipientName: {
    label: 'Nome do contato de alerta',
    description: 'É o nome da pessoa que receberá avisos operacionais do workspace.',
    whereToFind: 'Use o nome da pessoa responsável no escritório. Depois, esta lista fica em Configurações > Notificações.',
  },
  recipientEmail: {
    label: 'E-mail para alertas',
    description: 'É o endereço que receberá avisos de contratos, reuniões e resumos quando o envio por e-mail estiver habilitado.',
    whereToFind: 'Use o e-mail profissional da pessoa. Ele também pode ser alterado em Configurações > Notificações.',
  },
  recipientWhatsapp: {
    label: 'WhatsApp para alertas',
    description: 'É o telefone que receberá avisos operacionais quando a regra permitir envio por WhatsApp.',
    whereToFind: 'Use o número profissional com código do país e DDD, por exemplo +55 11 99999-0000.',
  },
  officialWhatsapp: {
    label: 'Conexão oficial do WhatsApp',
    description: 'É o cadastro local de um número WhatsApp Business conectado à API oficial da Meta.',
    whereToFind: 'Em Configurações > Integrações > WhatsApp. Cadastre uma conexão Cloud API ou Coexistence e conclua a validação local.',
  },
  enabledArea: {
    label: 'Área liberada',
    description: 'Mostra uma área de atuação já incluída no plano. Esta etapa apenas confirma o que foi liberado.',
    whereToFind: 'Veja o plano do workspace ou peça a liberação à equipe autorizada de onboarding, suporte ou TI.',
  },
  initialTemplate: {
    label: 'Template inicial',
    description: 'É o flow que dará o primeiro roteiro de atendimento para esta área.',
    whereToFind: 'Os flows permitidos aparecem nesta lista. A biblioteca completa fica em Automação > Flows.',
  },
  followUpProfile: {
    label: 'Perfil de follow-up',
    description: 'Define o tom e a insistência dos lembretes enviados depois do primeiro contato.',
    whereToFind: 'Escolha aqui a opção aprovada pelo escritório; a política detalhada fica no playbook publicado.',
  },
  humanIntervention: {
    label: 'Intervenção humana',
    description: 'Define quanto a IA pode avançar sozinha e em quais momentos uma pessoa precisa participar ou aprovar.',
    whereToFind: 'Use a regra interna do escritório. Para ações externas, valores e decisões sensíveis, preserve revisão humana.',
  },
  publishedPlaybook: {
    label: 'Playbook publicado',
    description: 'É a versão aprovada das regras de linguagem, honorários, objeções e limites de atuação dos agentes.',
    whereToFind: 'No Playbook Studio. Preencha, revise e publique uma versão antes de voltar a esta etapa.',
  },
  optionalIntegration: {
    label: 'Status da integração',
    description: 'Registra se um serviço opcional já foi configurado ou se ficará para depois.',
    whereToFind: 'Em Configurações > Integrações. Marque como conectada somente depois de validar o serviço no workspace.',
  },
} as const;

function nextStep(step: OnboardingStepId): OnboardingStepId {
  const currentIndex = STEP_ORDER.indexOf(step);
  return STEP_ORDER[Math.min(currentIndex + 1, STEP_ORDER.length - 1)];
}

function statusTone(status: string) {
  if (status === 'completed') return 'secondary';
  if (status === 'blocked') return 'destructive';
  return 'outline';
}

export default function Onboarding() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [draftAreas, setDraftAreas] = useState<WorkspaceAreaActivation[]>([]);
  const [draftRecipients, setDraftRecipients] = useState<WorkspaceNotificationRecipient[]>([]);
  const [selectedPlaybookId, setSelectedPlaybookId] = useState('');

  const onboardingQuery = useQuery({
    queryKey: ['workspace-onboarding-page'],
    queryFn: fetchWorkspaceOnboarding,
    staleTime: 30_000,
  });

  useEffect(() => {
    if (onboardingQuery.data?.areaActivations) {
      setDraftAreas(onboardingQuery.data.areaActivations);
    }
    if (onboardingQuery.data?.notificationRecipients) {
      setDraftRecipients(onboardingQuery.data.notificationRecipients);
    }
    if (onboardingQuery.data?.playbookId) {
      setSelectedPlaybookId(onboardingQuery.data.playbookId);
    }
  }, [onboardingQuery.data]);

  const saveMutation = useMutation({
    mutationFn: saveWorkspaceOnboarding,
    onSuccess: (data) => {
      queryClient.setQueryData(['workspace-onboarding-page'], data);
      queryClient.invalidateQueries({ queryKey: ['workspace-onboarding-banner'] }).catch(() => undefined);
      queryClient.invalidateQueries({ queryKey: ['workspace-onboarding-settings'] }).catch(() => undefined);
      queryClient.invalidateQueries({ queryKey: ['settings-surface'] }).catch(() => undefined);
      queryClient.invalidateQueries({ queryKey: ['platform', 'workspaces'] }).catch(() => undefined);
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Erro ao atualizar onboarding',
        description: error.message,
      });
    },
  });

  const onboarding = onboardingQuery.data;
  const currentStep = (onboarding?.currentStep ?? onboarding?.nextOnboardingStep ?? 'welcome') as OnboardingStepId;
  const whatsappConnectionsQuery = useQuery({
    queryKey: ['klm', 'omnichannel-connections', 'whatsapp', 'onboarding'],
    queryFn: () => klmApi.omnichannelConnections('whatsapp'),
    enabled: currentStep === 'whatsapp',
    retry: false,
  });
  const whatsappAutomationQuery = useQuery({
    queryKey: ['whatsapp-automation-onboarding'],
    queryFn: fetchWhatsAppAutomationRules,
    enabled: currentStep === 'whatsapp',
  });
  const whatsappAutomationMutation = useMutation({
    mutationFn: saveWhatsAppAutomationRules,
    onSuccess: (data) => {
      queryClient.setQueryData(['whatsapp-automation-onboarding'], data);
      queryClient.setQueryData(['whatsapp-automation-workspace'], data);
      toast({
        title: 'Escolha de automação confirmada.',
        description: data.enabled ? 'Somente as regras ligadas poderão responder.' : 'O atendimento permanece totalmente manual.',
      });
    },
    onError: (error: Error) => {
      toast({ variant: 'destructive', title: 'Não foi possível salvar as automações', description: error.message });
    },
  });
  const readyWhatsappConnections = useMemo(() => (whatsappConnectionsQuery.data ?? []).filter((connection) => (
    connection.channel === 'whatsapp'
    && connection.provider === 'meta_cloud'
    && ['cloud_api', 'coexistence'].includes(connection.transportMode)
    && connection.health.state === 'local_configuration_complete'
    && !['paused', 'disconnected_local'].includes(connection.status)
  )), [whatsappConnectionsQuery.data]);
  const playbooksQuery = useQuery({
    queryKey: ['playbooks', 'onboarding'],
    queryFn: listPlaybooks,
    enabled: currentStep === 'playbook' || currentStep === 'review',
  });
  const publishedPlaybooks = useMemo(
    () => (playbooksQuery.data ?? []).filter((playbook) => playbook.state === 'published'),
    [playbooksQuery.data],
  );
  const enabledAreas = useMemo(() => draftAreas.filter((area) => area.enabled), [draftAreas]);

  const patch = async (payload: Parameters<typeof saveWorkspaceOnboarding>[0]) => {
    const data = await saveMutation.mutateAsync(payload);
    if (data.onboardingCompleted) {
      toast({ title: 'Workspace ativado.', description: 'O setup mínimo foi concluído com sucesso.' });
    }
    return data;
  };

  const openWorkspaceSettings = () => {
    navigate('/app/configuracoes');
  };

  const openWhatsappSettings = () => {
    navigate('/app/configuracoes/avancadas?tab=integracoes');
  };

  const updateArea = (key: string, patchValue: Partial<WorkspaceAreaActivation>) => {
    setDraftAreas((current) =>
      current.map((area) => (area.key === key ? { ...area, ...patchValue } : area)),
    );
  };

  const saveAreas = async (step: OnboardingStepId) => {
    const payload = { areaActivations: draftAreas, markStepCompleted: step, currentStep: nextStep(step) };
    await patch(payload);
    toast({ title: 'Onboarding atualizado.' });
  };

  const completePlaybookStep = async () => {
    await patch({
      playbookId: selectedPlaybookId,
      markStepCompleted: 'playbook',
      currentStep: 'integrations',
    });
    toast({
      title: 'Playbook vinculado.',
      description: 'Os agentes usarão somente esta versão publicada e as permissões do caso.',
    });
  };

  const markWelcomeDone = async () => {
    await patch({
      notificationRecipients: draftRecipients.filter((recipient) => recipient.role !== 'workspace_owner'),
      markStepCompleted: 'welcome',
      currentStep: 'whatsapp',
    });
  };

  const updateIntegration = async (key: string, status: 'connected' | 'skipped' | 'not_started') => {
    const data = await patch({
      integrations: [{ key, status }],
      markStepCompleted: key === 'whatsapp' && status === 'connected' ? 'whatsapp' : undefined,
      currentStep: key === 'whatsapp' && status === 'connected' ? 'areas' : currentStep,
    });
    if (key === 'whatsapp' && data.integrations.find((item) => item.key === 'whatsapp')?.status === 'connected') {
      toast({ title: 'Configuração local do WhatsApp confirmada.' });
    }
  };

  const completeIntegrationsStep = async () => {
    await patch({ markStepCompleted: 'integrations', currentStep: 'review' });
  };

  const skipIntegrationsStep = async () => {
    await patch({ skipStep: 'integrations', currentStep: 'review' });
  };

  const completeReview = async () => {
    const data = await patch({
      areaActivations: draftAreas,
      reviewCompleted: true,
      markStepCompleted: 'review',
      currentStep: 'review',
    });
    if (data.onboardingCompleted) {
      navigate('/app');
    }
  };

  if (onboardingQuery.isLoading || !onboarding) {
    return (
      <div className="min-h-screen bg-background px-4 py-10">
        <div className="mx-auto max-w-5xl rounded-2xl border bg-card p-8 text-sm text-muted-foreground">
          Carregando onboarding guiado...
        </div>
      </div>
    );
  }

  const whatsappStatus = onboarding.integrations.find((item) => item.key === 'whatsapp')?.status ?? 'not_started';
  const extraRecipients = draftRecipients.filter((item) => item.role !== 'workspace_owner');

  return (
    <div className="min-h-screen bg-gradient-to-b from-background via-background to-primary/5 px-4 py-8">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 xl:flex-row">
        <div className="xl:w-[320px] xl:shrink-0">
          <Card className="sticky top-6">
            <CardHeader className="space-y-4">
              <div className="flex items-center gap-3">
                <DesbLogo variant="icon" size={42} dark />
                <div>
                  <CardTitle className="text-lg">Onboarding do workspace</CardTitle>
                  <CardDescription>O app já está liberado parcialmente enquanto concluímos o setup.</CardDescription>
                </div>
              </div>
              <div className="space-y-2">
                <Progress value={onboarding.onboardingProgress} className="h-2" />
                <p className="text-xs text-muted-foreground tabular-nums">
                  {onboarding.onboardingProgress}% concluído · próxima etapa: {STEP_TITLES[(onboarding.nextOnboardingStep ?? currentStep) as OnboardingStepId]}
                </p>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {onboarding.steps.map((step) => (
                <button
                  key={step.id}
                  type="button"
                  onClick={() => patch({ currentStep: step.id })}
                  className={`flex w-full items-start justify-between rounded-xl border px-3 py-3 text-left transition-colors ${currentStep === step.id ? 'border-primary bg-primary/5' : 'hover:bg-muted/40'}`}
                >
                  <div>
                    <p className="text-sm font-medium">{step.title}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{step.description}</p>
                  </div>
                  <Badge variant={statusTone(step.status)}>
                    {step.completed ? 'ok' : step.skipped ? 'adiado' : step.blocked ? 'bloqueado' : 'pendente'}
                  </Badge>
                </button>
              ))}
            </CardContent>
          </Card>
        </div>

        <div className="flex-1 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg font-semibold">{STEP_TITLES[currentStep]}</CardTitle>
              <CardDescription>
                {currentStep === 'welcome' && 'Vamos confirmar o contexto, deixar o workspace pronto e te mostrar o que ainda falta para a ativação.'}
                {currentStep === 'whatsapp' && 'Este é o único passo obrigatório para a operação principal do MVP.'}
                {currentStep === 'areas' && 'Confirme as áreas já liberadas para este plano. Somente a equipe autorizada do SaaS pode desbloquear novas áreas.'}
                {currentStep === 'template' && 'Cada área ativa precisa começar com pelo menos um flow/template permitido.'}
                {currentStep === 'follow_up' && 'Defina o tom do follow-up e o grau de intervenção humana.'}
                {currentStep === 'playbook' && 'Escolha a versão publicada que governará linguagem, objeções, propostas, honorários e limites de autonomia.'}
                {currentStep === 'integrations' && 'Você pode deixar estas integrações para depois. O importante é registrar o status e seguir.'}
                {currentStep === 'review' && 'Confira as pendências obrigatórias e finalize a ativação inicial do workspace.'}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {currentStep === 'welcome' && (
                <div className="space-y-4">
                  <div className="grid gap-4 md:grid-cols-3">
                    <Card className="border-dashed">
                      <CardContent className="pt-6">
                        <p className="text-sm font-medium">Entrada unificada</p>
                        <p className="mt-2 text-sm text-muted-foreground">
                          Tanto o checkout público quanto a criação pela plataforma caem neste mesmo fluxo guiado.
                        </p>
                      </CardContent>
                    </Card>
                    <Card className="border-dashed">
                      <CardContent className="pt-6">
                        <p className="text-sm font-medium">App parcial liberado</p>
                        <p className="mt-2 text-sm text-muted-foreground">
                          Você já pode navegar pelo produto, mas algumas operações continuam pendentes até concluir o setup.
                        </p>
                      </CardContent>
                    </Card>
                    <Card className="border-dashed">
                      <CardContent className="pt-6">
                        <p className="text-sm font-medium">IA governada</p>
                        <p className="mt-2 text-sm text-muted-foreground">
                          Neste fluxo você escolhe áreas, follow-up e intervenção humana. Modelos e prompts ficam no admin.
                        </p>
                      </CardContent>
                    </Card>
                  </div>
                  <Card className="border-primary/20 bg-primary/5">
                    <CardHeader className="pb-3">
                      <CardTitle className="text-base font-medium">Quem recebe os alertas operacionais</CardTitle>
                      <CardDescription>
                        O gestor já entra por padrão. Você pode registrar contatos extras para contratos, reuniões e resumo diário.
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {draftRecipients.map((recipient) => (
                        <div key={recipient.id} className="grid gap-3 rounded-xl border bg-background p-3 md:grid-cols-3">
                          <div className="space-y-1.5">
                            <FieldHelpLabel
                              htmlFor={`onboarding-recipient-name-${recipient.id}`}
                              {...ONBOARDING_FIELD_HELP.recipientName}
                            />
                            <Input
                              id={`onboarding-recipient-name-${recipient.id}`}
                              value={recipient.name ?? ''}
                              disabled={recipient.role === 'workspace_owner'}
                              onChange={(event) => setDraftRecipients((current) => current.map((item) => item.id === recipient.id ? { ...item, name: event.target.value } : item))}
                            />
                          </div>
                          <div className="space-y-1.5">
                            <FieldHelpLabel
                              htmlFor={`onboarding-recipient-email-${recipient.id}`}
                              {...ONBOARDING_FIELD_HELP.recipientEmail}
                            />
                            <Input
                              id={`onboarding-recipient-email-${recipient.id}`}
                              type="email"
                              value={recipient.email ?? ''}
                              onChange={(event) => setDraftRecipients((current) => current.map((item) => item.id === recipient.id ? { ...item, email: event.target.value } : item))}
                            />
                          </div>
                          <div className="space-y-1.5">
                            <FieldHelpLabel
                              htmlFor={`onboarding-recipient-phone-${recipient.id}`}
                              {...ONBOARDING_FIELD_HELP.recipientWhatsapp}
                            />
                            <Input
                              id={`onboarding-recipient-phone-${recipient.id}`}
                              type="tel"
                              value={recipient.phone ?? ''}
                              placeholder="+55 11 99999-0000"
                              onChange={(event) => setDraftRecipients((current) => current.map((item) => item.id === recipient.id ? { ...item, phone: event.target.value } : item))}
                            />
                          </div>
                        </div>
                      ))}
                      <div className="flex justify-between gap-3">
                        <p className="text-xs text-muted-foreground">
                          Contatos extras agora: {extraRecipients.length}. Você pode ajustar isso depois em Configurações &gt; Notificações.
                        </p>
                        <Button
                          type="button"
                          variant="outline"
                          onClick={() => setDraftRecipients((current) => [
                            ...current,
                            {
                              id: `recipient-${Date.now()}`,
                              name: '',
                              email: '',
                              phone: '',
                              role: 'custom',
                            },
                          ])}
                        >
                          Adicionar contato
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                  <div className="flex justify-end">
                    <Button onClick={markWelcomeDone} disabled={saveMutation.isPending}>
                      Entendi, vamos começar
                      <ArrowRight className="ml-2 h-4 w-4" />
                    </Button>
                  </div>
                </div>
              )}

              {currentStep === 'whatsapp' && (
                <div className="space-y-4">
                  <Card className="border-primary/20 bg-primary/5">
                    <CardContent className="flex flex-col gap-4 py-5 md:flex-row md:items-center md:justify-between">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <MessageSquare className="h-4 w-4 text-primary" />
                          <p className="text-sm font-medium">WhatsApp é obrigatório no MVP</p>
                          <FieldHelp {...ONBOARDING_FIELD_HELP.officialWhatsapp} />
                        </div>
                        <p className="text-sm text-muted-foreground">
                          Sem ele, o workspace continua navegável, mas não conclui a ativação operacional.
                        </p>
                      </div>
                      <Badge variant={whatsappStatus === 'connected' ? 'secondary' : 'destructive'}>
                        {whatsappStatus === 'connected' ? 'Conectado' : 'Pendente'}
                      </Badge>
                    </CardContent>
                  </Card>
                  <div className="flex flex-wrap gap-3">
                    <Button variant="outline" onClick={openWhatsappSettings}>
                      Configurar WhatsApp oficial
                      <ExternalLink className="ml-2 h-4 w-4" />
                    </Button>
                    <Button variant="outline" onClick={() => void whatsappConnectionsQuery.refetch()} disabled={whatsappConnectionsQuery.isFetching}>
                      {whatsappConnectionsQuery.isFetching ? 'Verificando...' : 'Verificar novamente'}
                    </Button>
                    <Button
                      onClick={() => updateIntegration('whatsapp', 'connected')}
                      disabled={
                        saveMutation.isPending
                        || readyWhatsappConnections.length === 0
                        || !whatsappAutomationQuery.data?.onboardingCompleted
                      }
                    >
                      Confirmar configuração local
                    </Button>
                  </div>
                  {whatsappConnectionsQuery.isError && <p role="alert" className="text-sm text-destructive">Não foi possível conferir o registro nativo. O onboarding não será confirmado enquanto a leitura falhar.</p>}
                  {whatsappConnectionsQuery.isSuccess && readyWhatsappConnections.length === 0 && <p className="text-sm text-muted-foreground">Cadastre e valide uma conexão WhatsApp Cloud API ou Coexistence em Configurações. Apenas um registro local completo libera esta etapa; nenhuma API externa será acionada agora.</p>}
                  {readyWhatsappConnections.length > 0 && <p className="text-sm text-emerald-700">{readyWhatsappConnections.length} conexão(ões) oficial(is) com configuração local completa: {readyWhatsappConnections.map((connection) => connection.displayName).join(', ')}.</p>}
                  {!whatsappAutomationQuery.data?.onboardingCompleted && (
                    <p className="text-sm text-amber-800">
                      Confirme abaixo se o atendimento ficará manual ou quais respostas automáticas serão ligadas antes de concluir esta etapa.
                    </p>
                  )}
                  <WhatsAppAutomationRulesPanel
                    value={whatsappAutomationQuery.data ?? null}
                    loading={whatsappAutomationQuery.isLoading}
                    onSave={async (value) => { await whatsappAutomationMutation.mutateAsync(value); }}
                  />
                </div>
              )}

              {currentStep === 'areas' && (
                <div className="space-y-4">
                  <div className="grid gap-4 md:grid-cols-2">
                    {draftAreas.map((area) => (
                      <Card key={area.key} className={area.enabled ? 'border-primary bg-primary/5' : ''}>
                        <CardContent className="space-y-3 pt-6">
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <div className="flex items-center gap-1">
                                <p className="text-sm font-medium">{area.label}</p>
                                <FieldHelp {...ONBOARDING_FIELD_HELP.enabledArea} />
                              </div>
                              <p className="mt-1 text-sm text-muted-foreground">{area.description}</p>
                            </div>
                            <div className="flex items-center gap-2">
                              <Badge variant={area.enabled ? 'secondary' : 'outline'}>
                                {area.enabled ? 'Liberada' : 'Bloqueada'}
                              </Badge>
                              <Checkbox checked={area.enabled} disabled aria-label={`${area.label}: ${area.enabled ? 'liberada' : 'bloqueada'}`} />
                            </div>
                          </div>
                          <div className="space-y-1">
                            <p className="text-xs uppercase tracking-wide text-muted-foreground">Checklist sugerido</p>
                            <ul className="space-y-1 text-sm text-muted-foreground">
                              {area.checklist.slice(0, 3).map((item) => (
                                <li key={item}>• {item}</li>
                              ))}
                            </ul>
                          </div>
                        </CardContent>
                      </Card>
                    ))}
                  </div>
                  <Card className="border-dashed">
                    <CardContent className="flex flex-col gap-3 py-4 text-sm text-muted-foreground md:flex-row md:items-center md:justify-between">
                      <div className="flex items-start gap-2">
                        <LockKeyhole className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                        <p>O dono da conta não consegue ampliar o próprio pacote. A liberação exige Super Admin do SaaS ou equipe interna de onboarding, suporte ou TI com permissão expressa.</p>
                      </div>
                      <div className="flex shrink-0 gap-2">
                        <Button type="button" variant="outline" onClick={() => void onboardingQuery.refetch()} disabled={onboardingQuery.isFetching}>
                          <RefreshCcw className="mr-2 h-4 w-4" />
                          Atualizar
                        </Button>
                        {!draftAreas.some((area) => area.enabled) && (
                          <Button type="button" variant="outline" onClick={() => navigate('/app/areas')}>Solicitar liberação</Button>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                  <div className="flex justify-end">
                    <Button onClick={() => saveAreas('areas')} disabled={saveMutation.isPending || !draftAreas.some((area) => area.enabled)}>
                      Confirmar áreas liberadas
                    </Button>
                  </div>
                </div>
              )}

              {currentStep === 'template' && (
                <div className="space-y-4">
                  {enabledAreas.length === 0 ? (
                    <Card className="border-dashed">
                      <CardContent className="py-8 text-sm text-muted-foreground">
                        Ative pelo menos uma área antes de escolher o template inicial.
                      </CardContent>
                    </Card>
                  ) : (
                    enabledAreas.map((area) => (
                      <Card key={area.key}>
                        <CardHeader className="pb-3">
                          <CardTitle className="text-base font-medium">{area.label}</CardTitle>
                          <CardDescription>Escolha o flow/template inicial que será usado nesta área.</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-3">
                          <FieldHelpLabel
                            htmlFor={`onboarding-template-${area.key}`}
                            {...ONBOARDING_FIELD_HELP.initialTemplate}
                          />
                          <Select
                            value={area.selectedFlowTemplateId ?? undefined}
                            onValueChange={(value) => updateArea(area.key, { selectedFlowTemplateId: value })}
                          >
                            <SelectTrigger id={`onboarding-template-${area.key}`}>
                              <SelectValue placeholder="Selecionar template inicial" />
                            </SelectTrigger>
                            <SelectContent>
                              {area.flowOptions.map((flow) => (
                                <SelectItem key={flow.id} value={flow.id}>
                                  {flow.name}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </CardContent>
                      </Card>
                    ))
                  )}
                  <div className="flex justify-end">
                    <Button
                      onClick={() => saveAreas('template')}
                      disabled={saveMutation.isPending || !enabledAreas.some((area) => area.selectedFlowTemplateId)}
                    >
                      Confirmar template inicial
                    </Button>
                  </div>
                </div>
              )}

              {currentStep === 'follow_up' && (
                <div className="space-y-4">
                  {enabledAreas.length === 0 ? (
                    <Card className="border-dashed">
                      <CardContent className="py-8 text-sm text-muted-foreground">
                        Ative uma área primeiro para configurar follow-up e intervenção humana.
                      </CardContent>
                    </Card>
                  ) : (
                    enabledAreas.map((area) => (
                      <Card key={area.key}>
                        <CardHeader className="pb-3">
                          <CardTitle className="text-base font-medium">{area.label}</CardTitle>
                          <CardDescription>Defina o comportamento operacional inicial desta área.</CardDescription>
                        </CardHeader>
                        <CardContent className="grid gap-4 md:grid-cols-2">
                          <div className="space-y-2">
                            <FieldHelpLabel
                              htmlFor={`onboarding-follow-up-${area.key}`}
                              {...ONBOARDING_FIELD_HELP.followUpProfile}
                            />
                            <Select
                              value={area.followUpProfile}
                              onValueChange={(value) => updateArea(area.key, { followUpProfile: value as WorkspaceAreaActivation['followUpProfile'] })}
                            >
                              <SelectTrigger id={`onboarding-follow-up-${area.key}`}>
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="gentle">Gentil</SelectItem>
                                <SelectItem value="balanced">Balanceado</SelectItem>
                                <SelectItem value="assertive">Assertivo</SelectItem>
                              </SelectContent>
                            </Select>
                          </div>
                          <div className="space-y-2">
                            <FieldHelpLabel
                              htmlFor={`onboarding-human-intervention-${area.key}`}
                              {...ONBOARDING_FIELD_HELP.humanIntervention}
                            />
                            <Select
                              value={area.humanInterventionMode}
                              onValueChange={(value) => updateArea(area.key, { humanInterventionMode: value as WorkspaceAreaActivation['humanInterventionMode'] })}
                            >
                              <SelectTrigger id={`onboarding-human-intervention-${area.key}`}>
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="human_only">Só humano</SelectItem>
                                <SelectItem value="human_in_the_loop">IA com humano no loop</SelectItem>
                                <SelectItem value="ai_with_human_approval">IA com aprovação humana</SelectItem>
                                <SelectItem value="ai_only">IA direta</SelectItem>
                              </SelectContent>
                            </Select>
                          </div>
                        </CardContent>
                      </Card>
                    ))
                  )}
                  <div className="flex justify-end">
                    <Button onClick={() => saveAreas('follow_up')} disabled={saveMutation.isPending || enabledAreas.length === 0}>
                      Salvar follow-up e intervenção
                    </Button>
                  </div>
                </div>
              )}

              {currentStep === 'playbook' && (
                <div className="space-y-4">
                  <Card className="border-primary/20 bg-primary/5">
                    <CardHeader className="pb-3">
                      <div className="flex items-center gap-2">
                        <BookOpenCheck className="h-4 w-4 text-primary" />
                        <CardTitle className="text-base font-medium">Regra operacional dos agentes</CardTitle>
                      </div>
                      <CardDescription>
                        O playbook é preenchido no onboarding, passa por revisão e só pode governar agentes depois de publicado. Mudanças manuais continuam prevalecendo.
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      {playbooksQuery.isLoading ? (
                        <p className="text-sm text-muted-foreground">Carregando playbooks publicados...</p>
                      ) : publishedPlaybooks.length === 0 ? (
                        <div className="rounded-xl border border-dashed bg-background p-4">
                          <p className="text-sm font-medium">Nenhum playbook publicado</p>
                          <p className="mt-1 text-sm text-muted-foreground">
                            Preencha linguagem do escritório, honorários, documentos, objeções, autonomia e aprovações no Playbook Studio; depois publique a versão aprovada.
                          </p>
                        </div>
                      ) : (
                        <div className="space-y-2">
                          <FieldHelpLabel htmlFor="onboarding-playbook" {...ONBOARDING_FIELD_HELP.publishedPlaybook} />
                          <Select value={selectedPlaybookId || undefined} onValueChange={setSelectedPlaybookId}>
                            <SelectTrigger id="onboarding-playbook">
                              <SelectValue placeholder="Selecionar política publicada" />
                            </SelectTrigger>
                            <SelectContent>
                              {publishedPlaybooks.map((playbook) => (
                                <SelectItem key={playbook.id} value={playbook.id}>
                                  {playbook.name} · versão {playbook.publishedVersion ?? playbook.currentVersion}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                      )}
                      <div className="grid gap-3 text-sm md:grid-cols-3">
                        <div className="rounded-xl border bg-background p-3"><strong>Escopo fechado</strong><p className="mt-1 text-muted-foreground">Conta, área, produto, agente e etapa.</p></div>
                        <div className="rounded-xl border bg-background p-3"><strong>Veto humano</strong><p className="mt-1 text-muted-foreground">Contratos, valores e atos externos respeitam aprovação.</p></div>
                        <div className="rounded-xl border bg-background p-3"><strong>Versão fixada</strong><p className="mt-1 text-muted-foreground">Cada execução registra a política efetiva.</p></div>
                      </div>
                    </CardContent>
                  </Card>
                  <div className="flex flex-wrap justify-end gap-3">
                    <Button type="button" variant="outline" onClick={() => navigate('/app/playbooks')}>
                      Abrir Playbook Studio
                      <ExternalLink className="ml-2 h-4 w-4" />
                    </Button>
                    <Button onClick={completePlaybookStep} disabled={saveMutation.isPending || !selectedPlaybookId}>
                      Vincular política publicada
                    </Button>
                  </div>
                </div>
              )}

              {currentStep === 'integrations' && (
                <div className="space-y-4">
                  <div className="grid gap-4 md:grid-cols-2">
                    {onboarding.integrations.filter((item) => item.key !== 'whatsapp').map((integration) => (
                      <Card key={integration.key}>
                        <CardContent className="space-y-4 pt-6">
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <div className="flex items-center gap-1">
                                <p className="text-sm font-medium">{integration.label}</p>
                                <FieldHelp
                                  label={`Integração ${integration.label}`}
                                  description={`${ONBOARDING_FIELD_HELP.optionalIntegration.description} ${integration.description}`}
                                  whereToFind={ONBOARDING_FIELD_HELP.optionalIntegration.whereToFind}
                                />
                              </div>
                              <p className="mt-1 text-sm text-muted-foreground">{integration.description}</p>
                            </div>
                            <Badge variant={integration.status === 'connected' ? 'secondary' : integration.status === 'skipped' ? 'outline' : 'destructive'}>
                              {integration.status === 'connected' ? 'Conectada' : integration.status === 'skipped' ? 'Adiada' : 'Pendente'}
                            </Badge>
                          </div>
                          <div className="flex flex-wrap gap-2">
                            <Button variant="outline" size="sm" onClick={() => updateIntegration(integration.key, 'connected')}>
                              Marcar como conectada
                            </Button>
                            <Button variant="ghost" size="sm" onClick={() => updateIntegration(integration.key, 'skipped')}>
                              Configurar depois
                            </Button>
                          </div>
                        </CardContent>
                      </Card>
                    ))}
                  </div>
                  <div className="flex flex-wrap justify-end gap-3">
                    <Button variant="ghost" onClick={skipIntegrationsStep} disabled={saveMutation.isPending}>
                      Pular por agora
                    </Button>
                    <Button onClick={completeIntegrationsStep} disabled={saveMutation.isPending}>
                      Continuar para revisão
                    </Button>
                  </div>
                </div>
              )}

              {currentStep === 'review' && (
                <div className="space-y-4">
                  <div className="grid gap-4 md:grid-cols-2">
                    <Card>
                      <CardHeader className="pb-3">
                        <CardTitle className="text-base font-medium">Checklist obrigatório</CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-2">
                        {onboarding.steps.filter((step) => step.required).map((step) => (
                          <div key={step.id} className="flex items-center justify-between rounded-lg border px-3 py-2">
                            <div className="flex items-center gap-2">
                              {step.completed ? <CheckCircle2 className="h-4 w-4 text-emerald-500" /> : <CircleDashed className="h-4 w-4 text-amber-500" />}
                              <span className="text-sm">{step.title}</span>
                            </div>
                            <Badge variant={step.completed ? 'secondary' : 'outline'}>
                              {step.completed ? 'ok' : 'pendente'}
                            </Badge>
                          </div>
                        ))}
                      </CardContent>
                    </Card>

                    <Card>
                      <CardHeader className="pb-3">
                        <CardTitle className="text-base font-medium">Resumo operacional</CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-3 text-sm text-muted-foreground">
                        <div className="flex items-center justify-between">
                          <span>Áreas ativas</span>
                          <span className="font-medium text-foreground tabular-nums">{enabledAreas.length}</span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span>Templates escolhidos</span>
                          <span className="font-medium text-foreground tabular-nums">
                            {enabledAreas.filter((area) => area.selectedFlowTemplateId).length}
                          </span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span>Playbook governante</span>
                          <span className="font-medium text-foreground">{onboarding.playbookId ? 'Publicado e vinculado' : 'Pendente'}</span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span>WhatsApp</span>
                          <span className="font-medium text-foreground">
                            {whatsappStatus === 'connected' ? 'Conectado' : 'Pendente'}
                          </span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span>Integrações opcionais conectadas</span>
                          <span className="font-medium text-foreground tabular-nums">
                            {onboarding.integrations.filter((item) => !item.required && item.status === 'connected').length}
                          </span>
                        </div>
                      </CardContent>
                    </Card>
                  </div>

                  <Card className="border-primary/20 bg-primary/5">
                    <CardContent className="flex flex-col gap-4 py-5 md:flex-row md:items-center md:justify-between">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <ShieldCheck className="h-4 w-4 text-primary" />
                          <p className="text-sm font-medium">Ativação final do workspace</p>
                        </div>
                        <p className="text-sm text-muted-foreground">
                          Quando os passos obrigatórios estiverem completos, o workspace passa a contar como onboarding concluído.
                        </p>
                      </div>
                      <Button onClick={completeReview} disabled={saveMutation.isPending || onboarding.pendingRequiredSteps.length > 1 || (onboarding.pendingRequiredSteps.length === 1 && onboarding.pendingRequiredSteps[0] !== 'review')}>
                        Concluir onboarding
                      </Button>
                    </CardContent>
                  </Card>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-lg font-semibold">Integrações e operação plug and play</CardTitle>
              <CardDescription>
                As áreas são liberadas conforme o plano; aqui você configura templates, playbook, follow-up e intervenção humana com regras auditáveis.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div className="flex flex-wrap gap-2">
                <Badge variant="outline" className="gap-1"><PlugZap className="h-3 w-3" /> WhatsApp obrigatório</Badge>
                <Badge variant="outline">Email opcional</Badge>
                <Badge variant="outline">Calendário opcional</Badge>
                <Badge variant="outline">Assinatura opcional</Badge>
                <Badge variant="outline">Financeiro opcional</Badge>
                <Badge variant="outline">Webhooks opcionais</Badge>
              </div>
              <Button variant="outline" onClick={openWorkspaceSettings}>
                Abrir configurações do workspace
                <ExternalLink className="ml-2 h-4 w-4" />
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
