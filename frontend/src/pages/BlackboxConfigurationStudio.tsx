import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Bot,
  CheckCircle2,
  FileText,
  GitBranch,
  Loader2,
  Paperclip,
  Rocket,
  RotateCcw,
  Save,
  Sparkles,
  UsersRound,
  WandSparkles,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { StudioWorkspaceCombobox } from '@/components/platform/StudioWorkspaceCombobox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { canPlatform } from '@/lib/platform-access';
import {
  assistPlatformConfiguration,
  createPlatformConfigurationDraft,
  fetchPlatformConfigurationDraftPage,
  fetchPlatformConfigurationDraftVersionPage,
  fetchPlatformConfigurationWorkspace,
  fetchPlatformConfigurationWorkspacePage,
  publishPlatformConfigurationDraft,
  restorePlatformConfigurationDraft,
  updatePlatformConfigurationDraft,
  type StudioDraft,
  type StudioKind,
  type StudioProposal,
  type StudioWorkspaceOption,
} from '@/services/platform-configuration-studio';
import { useAppStore } from '@/store/app-context';

const STUDIO_KIND: Record<StudioKind, {
  label: string;
  description: string;
  example: string;
  icon: React.ElementType;
}> = {
  agent: {
    label: 'Agente',
    description: 'Crie uma função especializada, defina seu limite e publique pausada para revisão.',
    example: 'Crie um agente de onboarding que confira dados, documentos e prepare as próximas tarefas.',
    icon: Bot,
  },
  agent_package: {
    label: 'Pacote de agentes',
    description: 'Descreva a operação. O Estúdio prepara especialistas coordenados e seguros para a conta.',
    example: 'Monte um pacote para onboarding com especialista em dados, revisor e governança pelo Orquestrador.',
    icon: UsersRound,
  },
  flow: {
    label: 'Fluxo',
    description: 'Descreva a automação. O Estúdio monta uma primeira versão nativa e segura.',
    example: 'Crie um fluxo de recuperação com duas mensagens, espera de dois dias e pausa humana.',
    icon: GitBranch,
  },
  experience: {
    label: 'Experiência',
    description: 'Personalize textos, atalhos e visibilidade de recursos para a conta selecionada.',
    example: 'Adapte a experiência para uma operação previdenciária e destaque agenda e documentos.',
    icon: WandSparkles,
  },
};

function textPayload(payload: Record<string, unknown>, key: string): string {
  return typeof payload[key] === 'string' ? String(payload[key]) : '';
}

const DRAFT_PAGE_SIZE = 10;
const VERSION_PAGE_SIZE = 10;

export default function BlackboxConfigurationStudio() {
  const { state } = useAppStore();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [workspaceId, setWorkspaceId] = useState('');
  const [selectedWorkspaceOption, setSelectedWorkspaceOption] = useState<StudioWorkspaceOption | null>(null);
  const [kind, setKind] = useState<StudioKind>('agent');
  const [title, setTitle] = useState('');
  const [prompt, setPrompt] = useState('');
  const [attachments, setAttachments] = useState<string[]>([]);
  const [proposal, setProposal] = useState<StudioProposal | null>(null);
  const [selectedDraft, setSelectedDraft] = useState<StudioDraft | null>(null);
  const [draftPage, setDraftPage] = useState(0);
  const [versionPage, setVersionPage] = useState(0);
  const [permissionClock, setPermissionClock] = useState(() => Date.now());
  const [restoreReason, setRestoreReason] = useState('');
  const canManage = canPlatform(state.currentUser, 'workspace_configuration', 'manage');
  const canPublish = canPlatform(state.currentUser, 'workspace_configuration', 'approve');

  const workspaces = useQuery({
    queryKey: ['blackbox', 'configuration-studio', 'workspaces'],
    queryFn: () => fetchPlatformConfigurationWorkspacePage({ limit: 20, offset: 0 }),
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
  });
  const configurationWorkspaces = workspaces.data?.items;
  const refetchConfigurationWorkspaces = workspaces.refetch;
  const selectedWorkspaceRefresh = useQuery({
    queryKey: ['blackbox', 'configuration-studio', 'selected-workspace', selectedWorkspaceOption?.id],
    queryFn: () => fetchPlatformConfigurationWorkspace(selectedWorkspaceOption!.id),
    enabled: Boolean(selectedWorkspaceOption?.id),
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
    retry: false,
  });
  const refetchSelectedWorkspace = selectedWorkspaceRefresh.refetch;
  const selectedWorkspace = useMemo(() => {
    if (!selectedWorkspaceOption) return null;
    if (selectedWorkspaceRefresh.isError) return null;
    if (!selectedWorkspaceRefresh.data) return selectedWorkspaceOption;
    return selectedWorkspaceRefresh.data;
  }, [selectedWorkspaceOption, selectedWorkspaceRefresh.data, selectedWorkspaceRefresh.isError]);
  const canReadWorkspace = Boolean(
    selectedWorkspace?.canRead
    && (
      !selectedWorkspace.readExpiresAt
      || Date.parse(selectedWorkspace.readExpiresAt) > permissionClock
    ),
  );
  const canWriteWorkspace = Boolean(
    selectedWorkspace?.canWrite
    && (
      !selectedWorkspace.writeExpiresAt
      || Date.parse(selectedWorkspace.writeExpiresAt) > permissionClock
    ),
  );
  const drafts = useQuery({
    queryKey: ['blackbox', 'configuration-studio', 'drafts', workspaceId, draftPage],
    queryFn: () => fetchPlatformConfigurationDraftPage(workspaceId, {
      limit: DRAFT_PAGE_SIZE,
      offset: draftPage * DRAFT_PAGE_SIZE,
    }),
    enabled: Boolean(workspaceId && canReadWorkspace),
  });
  const versions = useQuery({
    queryKey: ['blackbox', 'configuration-studio', 'versions', workspaceId, selectedDraft?.id, versionPage],
    queryFn: () => fetchPlatformConfigurationDraftVersionPage(workspaceId, selectedDraft!.id, {
      limit: VERSION_PAGE_SIZE,
      offset: versionPage * VERSION_PAGE_SIZE,
    }),
    enabled: Boolean(workspaceId && selectedDraft?.id && canReadWorkspace),
  });

  useEffect(() => {
    if (!workspaceId && workspaces.data?.items[0]) {
      setWorkspaceId(workspaces.data.items[0].id);
      setSelectedWorkspaceOption(workspaces.data.items[0]);
    }
  }, [workspaceId, workspaces.data]);

  useEffect(() => {
    const expiryCandidates = [
      ...(configurationWorkspaces ?? []),
      ...(selectedWorkspace ? [selectedWorkspace] : []),
    ];
    const expiries = expiryCandidates
      .flatMap((workspace) => [workspace.readExpiresAt, workspace.writeExpiresAt])
      .filter((value): value is string => Boolean(value))
      .map((value) => Date.parse(value))
      .filter((value) => Number.isFinite(value) && value > Date.now());
    if (!expiries.length) return undefined;
    const nextExpiry = Math.min(...expiries);
    const timeout = window.setTimeout(() => {
      setPermissionClock(Date.now());
      void refetchConfigurationWorkspaces();
      void refetchSelectedWorkspace();
    }, Math.max(50, nextExpiry - Date.now() + 50));
    return () => window.clearTimeout(timeout);
  }, [
    configurationWorkspaces,
    refetchConfigurationWorkspaces,
    refetchSelectedWorkspace,
    selectedWorkspace,
  ]);

  useEffect(() => {
    if (canReadWorkspace) return;
    queryClient.removeQueries({
      queryKey: ['blackbox', 'configuration-studio', 'drafts', workspaceId],
      exact: false,
    });
    if (selectedDraft) {
      setSelectedDraft(null);
      setProposal(null);
      setTitle('');
      setPrompt('');
    }
  }, [
    canReadWorkspace,
    queryClient,
    selectedDraft,
    workspaceId,
  ]);

  const assist = useMutation({
    mutationFn: () => assistPlatformConfiguration(workspaceId, {
      kind,
      prompt,
      title: title.trim() || undefined,
      attachmentNames: attachments,
      currentPayload: proposal?.kind === kind ? proposal.payload : undefined,
    }),
    onSuccess: (value) => {
      setProposal(value);
      setSelectedDraft(null);
      setTitle(value.title);
    },
    onError: (error: Error) => {
      toast({ title: 'Não foi possível preparar a proposta', description: error.message, variant: 'destructive' });
    },
  });

  const save = useMutation({
    mutationFn: async () => {
      if (!proposal) throw new Error('Prepare uma proposta antes de salvar.');
      if (selectedDraft) {
        return updatePlatformConfigurationDraft(workspaceId, selectedDraft.id, {
          title: title.trim() || proposal.title,
          payload: proposal.payload,
          expectedEtag: selectedDraft.etag,
          reason: 'Rascunho revisado no Estúdio assistido.',
        });
      }
      return createPlatformConfigurationDraft(workspaceId, {
        kind: proposal.kind,
        title: title.trim() || proposal.title,
        key: proposal.key,
        payload: proposal.payload,
        reason: 'Rascunho criado no Estúdio assistido.',
      });
    },
    onSuccess: async (value) => {
      setDraftPage(0);
      setVersionPage(0);
      setSelectedDraft(value);
      await queryClient.invalidateQueries({ queryKey: ['blackbox', 'configuration-studio', 'drafts', workspaceId] });
      toast({ title: 'Rascunho salvo', description: `Versão ${value.currentVersion} registrada com auditoria.` });
    },
    onError: (error: Error) => {
      toast({ title: 'Não foi possível salvar', description: error.message, variant: 'destructive' });
    },
  });

  const publish = useMutation({
    mutationFn: () => {
      if (!selectedDraft) throw new Error('Salve o rascunho antes de publicar.');
      return publishPlatformConfigurationDraft(workspaceId, selectedDraft.id, {
        expectedEtag: selectedDraft.etag,
        reason: 'Publicação aprovada no Estúdio Blackbox.',
      });
    },
    onSuccess: async (value) => {
      setDraftPage(0);
      setVersionPage(0);
      setSelectedDraft(value.draft);
      await queryClient.invalidateQueries({ queryKey: ['blackbox', 'configuration-studio', 'drafts', workspaceId] });
      toast({
        title: value.alreadyPublished ? 'Configuração já publicada' : 'Configuração publicada',
        description: 'A versão aprovada foi aplicada no motor nativo da conta.',
      });
    },
    onError: (error: Error) => {
      toast({ title: 'Não foi possível publicar', description: error.message, variant: 'destructive' });
    },
  });

  const restore = useMutation({
    mutationFn: (targetVersion: number) => {
      if (!selectedDraft) throw new Error('Selecione um rascunho.');
      return restorePlatformConfigurationDraft(workspaceId, selectedDraft.id, {
        targetVersion,
        expectedEtag: selectedDraft.etag,
        reason: restoreReason.trim(),
      });
    },
    onSuccess: async (value) => {
      setDraftPage(0);
      setVersionPage(0);
      setSelectedDraft(value);
      setRestoreReason('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['blackbox', 'configuration-studio', 'drafts', workspaceId] }),
        queryClient.invalidateQueries({ queryKey: ['blackbox', 'configuration-studio', 'versions', workspaceId, value.id] }),
      ]);
      toast({ title: 'Versão restaurada como rascunho', description: `Nova versão ${value.currentVersion} pronta para revisão e publicação.` });
    },
    onError: (error: Error) => {
      toast({ title: 'Não foi possível restaurar', description: error.message, variant: 'destructive' });
    },
  });

  const loadDraft = (draft: StudioDraft) => {
    setVersionPage(0);
    setSelectedDraft(draft);
    setKind(draft.kind);
    setTitle(draft.title);
    setPrompt(textPayload(draft.payload, 'description') || textPayload(draft.payload, 'brief'));
    setProposal({
      kind: draft.kind,
      title: draft.title,
      key: draft.key,
      payload: draft.payload,
      summary: 'Rascunho versionado carregado para revisão.',
      suggestions: ['Revise os dados e salve uma nova versão antes de publicar.'],
      warnings: [],
      approvalRequired: true,
      assistantMode: 'local_structured',
    });
  };

  const resetComposer = (nextKind: StudioKind) => {
    setKind(nextKind);
    setTitle('');
    setPrompt('');
    setAttachments([]);
    setProposal(null);
    setSelectedDraft(null);
    setVersionPage(0);
  };

  const updateProposalField = (field: string, value: unknown) => {
    setProposal((current) => current ? {
      ...current,
      payload: { ...current.payload, [field]: value },
    } : current);
  };

  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-5 p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-blue-600" />
            <h1 className="text-xl font-semibold">Estúdio assistido</h1>
          </div>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Configure agentes, pacotes, fluxos e a experiência da conta em linguagem natural.
            Tudo começa como rascunho, exige revisão e mantém histórico de versões.
          </p>
        </div>
        <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">
          Motores nativos · publicação controlada
        </Badge>
      </div>

      <Card>
        <CardContent className="grid gap-4 pt-6 lg:grid-cols-[minmax(280px,420px)_1fr]">
          <div className="space-y-2">
            <Label htmlFor="studio-workspace">Conta que receberá a configuração</Label>
            <StudioWorkspaceCombobox value={workspaceId} selected={selectedWorkspace} onChange={(workspace) => {
              setWorkspaceId(workspace.id);
              setSelectedWorkspaceOption(workspace);
              setDraftPage(0);
              setVersionPage(0);
              setProposal(null);
              setSelectedDraft(null);
            }} />
          </div>
          <div className="rounded-md border bg-slate-50 px-4 py-3">
            <p className="text-xs font-semibold uppercase text-slate-500">Escopo atual</p>
            <p className="mt-1 text-sm font-medium">{selectedWorkspace?.name ?? 'Nenhuma conta selecionada'}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              O suporte não entra na conta. Ele prepara configuração versionada no Blackbox e publica somente com permissão.
            </p>
          </div>
        </CardContent>
      </Card>

      <div className="grid min-h-[620px] gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle className="text-base">Criar com assistência</CardTitle>
            <CardDescription>Descreva o resultado. O Estúdio converte o pedido para o contrato nativo.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <Tabs value={kind} onValueChange={(value) => resetComposer(value as StudioKind)}>
              <TabsList className="grid w-full grid-cols-4">
                {(Object.keys(STUDIO_KIND) as StudioKind[]).map((item) => {
                  const Icon = STUDIO_KIND[item].icon;
                  return (
                    <TabsTrigger key={item} value={item} className="gap-2">
                      <Icon className="h-4 w-4" />
                      {STUDIO_KIND[item].label}
                    </TabsTrigger>
                  );
                })}
              </TabsList>
              {(Object.keys(STUDIO_KIND) as StudioKind[]).map((item) => (
                <TabsContent key={item} value={item} className="mt-4">
                  <p className="text-sm text-muted-foreground">{STUDIO_KIND[item].description}</p>
                </TabsContent>
              ))}
            </Tabs>

            <div className="grid gap-4 md:grid-cols-[minmax(180px,280px)_1fr]">
              <div className="space-y-2">
                <Label htmlFor="studio-title">Nome</Label>
                <Input
                  id="studio-title"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder={`Nome do ${STUDIO_KIND[kind].label.toLocaleLowerCase('pt-BR')}`}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="studio-files">Referências opcionais</Label>
                <label
                  htmlFor="studio-files"
                  className="flex min-h-10 cursor-pointer items-center gap-2 rounded-md border bg-white px-3 text-sm"
                >
                  <Paperclip className="h-4 w-4" />
                  <span className="truncate">
                    {attachments.length ? attachments.join(', ') : 'Selecionar imagens ou documentos'}
                  </span>
                </label>
                <input
                  id="studio-files"
                  className="sr-only"
                  type="file"
                  multiple
                  accept="image/*,.pdf,.doc,.docx,.txt"
                  onChange={(event) => {
                    setAttachments(Array.from(event.target.files ?? []).map((file) => file.name));
                  }}
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="studio-prompt">O que você precisa?</Label>
              <Textarea
                id="studio-prompt"
                className="min-h-32 resize-y"
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder={STUDIO_KIND[kind].example}
              />
              <p className="text-xs text-muted-foreground">
                Os arquivos selecionados são apenas referenciados nesta etapa local; nenhuma ação externa é executada.
              </p>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                disabled={!canManage || !canWriteWorkspace || !workspaceId || prompt.trim().length < 8 || assist.isPending}
                onClick={() => assist.mutate()}
              >
                {assist.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
                Preparar proposta
              </Button>
              {proposal && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={!canManage || !canWriteWorkspace || save.isPending}
                  onClick={() => save.mutate()}
                >
                  {save.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                  {selectedDraft ? 'Salvar nova versão' : 'Salvar rascunho'}
                </Button>
              )}
              {selectedDraft && selectedDraft.status !== 'published' && (
                <Button
                  type="button"
                  variant="secondary"
                  disabled={!canPublish || !canWriteWorkspace || publish.isPending}
                  onClick={() => publish.mutate()}
                  title={canPublish ? 'Publicar versão aprovada' : 'Sua função pode preparar, mas não publicar'}
                >
                  {publish.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Rocket className="mr-2 h-4 w-4" />}
                  Publicar na conta
                </Button>
              )}
            </div>

            {proposal && (
              <section className="space-y-4 rounded-md border border-blue-200 bg-blue-50/40 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold">{title || proposal.title}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{proposal.summary}</p>
                  </div>
                  <Badge variant="outline">Aprovação obrigatória</Badge>
                </div>

                {kind === 'agent' && (
                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="studio-agent-role">Função do agente</Label>
                      <Input
                        id="studio-agent-role"
                        value={textPayload(proposal.payload, 'role')}
                        onChange={(event) => updateProposalField('role', event.target.value)}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="studio-agent-parent">Responde a</Label>
                      <Select
                        value={textPayload(proposal.payload, 'parentAgentKey') || 'orquestrador'}
                        onValueChange={(value) => updateProposalField('parentAgentKey', value)}
                      >
                        <SelectTrigger id="studio-agent-parent"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="orquestrador">Orquestrador</SelectItem>
                          <SelectItem value="juridico">Jurídico</SelectItem>
                          <SelectItem value="financeiro">Financeiro</SelectItem>
                          <SelectItem value="marketing">Marketing</SelectItem>
                          <SelectItem value="secretaria">Secretaria</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-2 md:col-span-2">
                      <Label htmlFor="studio-agent-description">Instrução operacional</Label>
                      <Textarea
                        id="studio-agent-description"
                        value={textPayload(proposal.payload, 'description')}
                        onChange={(event) => updateProposalField('description', event.target.value)}
                      />
                    </div>
                  </div>
                )}

                {kind === 'agent_package' && (
                  <div className="space-y-3">
                    <div className="rounded-md border bg-white p-3">
                      <p className="text-xs font-semibold uppercase text-muted-foreground">Governança do pacote</p>
                      <p className="mt-1 text-sm">
                        Orquestrador coordena, publicação pausada e aprovação humana obrigatória.
                      </p>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      {Array.isArray(proposal.payload.agents) && proposal.payload.agents.map((agent, index) => {
                        const current = agent as { key?: string; name?: string; role?: string; tools?: string[] };
                        return (
                          <div key={current.key ?? index} className="rounded-md border bg-white p-3">
                            <p className="text-xs font-semibold uppercase text-muted-foreground">
                              Agente {index + 1}
                            </p>
                            <p className="mt-1 text-sm font-medium">{current.name ?? current.key}</p>
                            <p className="mt-1 text-xs text-muted-foreground">{current.role}</p>
                            <p className="mt-2 text-xs">
                              {(current.tools ?? []).length} ferramenta(s) nativa(s)
                            </p>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {kind === 'flow' && (
                  <div className="grid gap-3 sm:grid-cols-2">
                    {Array.isArray(proposal.payload.nodes) && proposal.payload.nodes.map((node, index) => {
                      const current = node as { config?: { label?: string }; type?: string };
                      return (
                        <div key={`${current.type}-${index}`} className="rounded-md border bg-white p-3">
                          <p className="text-xs font-semibold uppercase text-muted-foreground">Etapa {index + 1}</p>
                          <p className="mt-1 text-sm">{current.config?.label || current.type || 'Etapa'}</p>
                        </div>
                      );
                    })}
                  </div>
                )}

                {kind === 'experience' && (
                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="studio-experience-support">Nome do suporte</Label>
                      <Input
                        id="studio-experience-support"
                        value={textPayload(proposal.payload, 'supportLabel')}
                        onChange={(event) => updateProposalField('supportLabel', event.target.value)}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="studio-experience-welcome">Mensagem de boas-vindas</Label>
                      <Input
                        id="studio-experience-welcome"
                        value={textPayload(proposal.payload, 'welcomeMessage')}
                        onChange={(event) => updateProposalField('welcomeMessage', event.target.value)}
                      />
                    </div>
                  </div>
                )}

                <div className="flex items-center gap-2 text-xs text-emerald-700">
                  <CheckCircle2 className="h-4 w-4" />
                  Publicação versionada, reversível e limitada à conta selecionada.
                </div>
              </section>
            )}
          </CardContent>
        </Card>

        <Card className="min-w-0">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <FileText className="h-4 w-4" />
              Rascunhos da conta
            </CardTitle>
            <CardDescription>Selecione para revisar, versionar ou publicar.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {!workspaceId && <p className="text-sm text-muted-foreground">Selecione uma conta.</p>}
            {workspaceId && !canReadWorkspace && (
              <p className="text-sm text-muted-foreground">
                O acesso atual permite preparar mudanças, mas não consultar rascunhos desta conta.
              </p>
            )}
            {canReadWorkspace && drafts.isLoading && <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />}
            {canReadWorkspace && drafts.isError && (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4">
                <p className="text-sm font-medium text-destructive">Não foi possível carregar os rascunhos.</p>
                <Button type="button" size="sm" variant="outline" className="mt-3" onClick={() => void drafts.refetch()}>
                  Tentar novamente
                </Button>
              </div>
            )}
            {canReadWorkspace && workspaceId && !drafts.isLoading && !drafts.isError && !(drafts.data?.items ?? []).length && (
              <div className="rounded-md border border-dashed p-5 text-center">
                <p className="text-sm font-medium">Nenhum rascunho</p>
                <p className="mt-1 text-xs text-muted-foreground">A primeira proposta salva aparecerá aqui.</p>
              </div>
            )}
            {canReadWorkspace && (drafts.data?.items ?? []).map((draft) => (
              <button
                key={draft.id}
                type="button"
                onClick={() => loadDraft(draft)}
                className={[
                  'w-full rounded-md border p-3 text-left transition-colors',
                  selectedDraft?.id === draft.id ? 'border-blue-500 bg-blue-50' : 'bg-white hover:bg-slate-50',
                ].join(' ')}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold">{draft.title}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {STUDIO_KIND[draft.kind].label} · versão {draft.currentVersion}
                    </p>
                  </div>
                  <Badge variant={draft.status === 'published' ? 'default' : 'outline'}>
                    {draft.status === 'published' ? 'Publicado' : 'Rascunho'}
                  </Badge>
                </div>
                <p className="mt-2 text-[11px] text-muted-foreground">
                  Atualizado em {new Date(draft.updatedAt).toLocaleString('pt-BR')}
                </p>
              </button>
            ))}
            {canReadWorkspace && drafts.data && drafts.data.total > 0 && (
              <div className="flex items-center justify-between gap-3 border-t pt-3">
                <p className="text-xs text-muted-foreground">
                  {drafts.data.offset + 1}–{drafts.data.offset + drafts.data.items.length} de {drafts.data.total}
                </p>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={draftPage === 0 || drafts.isFetching}
                    onClick={() => setDraftPage((current) => Math.max(0, current - 1))}
                  >
                    Anterior
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={!drafts.data.hasMore || drafts.isFetching}
                    onClick={() => setDraftPage((current) => current + 1)}
                  >
                    Próxima
                  </Button>
                </div>
              </div>
            )}
            {selectedDraft && (
              <section className="space-y-3 border-t pt-4">
                <div>
                  <p className="text-sm font-semibold">Histórico imutável</p>
                  <p className="text-xs text-muted-foreground">Restaurar cria uma nova versão em rascunho; não apaga o histórico.</p>
                </div>
                <Input
                  aria-label="Motivo da restauração"
                  value={restoreReason}
                  onChange={(event) => setRestoreReason(event.target.value)}
                  placeholder="Motivo obrigatório para restaurar"
                  disabled={!canPublish || !canWriteWorkspace}
                />
                {versions.isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : versions.isError ? (
                  <p className="text-xs text-destructive">Não foi possível carregar o histórico.</p>
                ) : (versions.data?.items ?? []).map((version) => (
                  <div key={version.version} className="rounded-md border p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-medium">Versão {version.version}</p>
                        <p className="text-[11px] text-muted-foreground">{version.reason || 'Sem motivo registrado'} · {new Date(version.createdAt).toLocaleString('pt-BR')}</p>
                      </div>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={!canPublish || !canWriteWorkspace || restoreReason.trim().length < 3 || restore.isPending || version.version === selectedDraft.currentVersion}
                        onClick={() => restore.mutate(version.version)}
                      >
                        <RotateCcw className="mr-1.5 h-3.5 w-3.5" />Restaurar
                      </Button>
                    </div>
                  </div>
                ))}
                {versions.data && versions.data.total > 0 && (
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-xs text-muted-foreground">
                      {versions.data.offset + 1}–{versions.data.offset + versions.data.items.length} de {versions.data.total}
                    </p>
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={versionPage === 0 || versions.isFetching}
                        onClick={() => setVersionPage((current) => Math.max(0, current - 1))}
                      >
                        Anterior
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={!versions.data.hasMore || versions.isFetching}
                        onClick={() => setVersionPage((current) => current + 1)}
                      >
                        Próxima
                      </Button>
                    </div>
                  </div>
                )}
              </section>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
