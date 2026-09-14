import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  ArrowLeft,
  Beaker,
  BookOpenCheck,
  CheckCircle2,
  CircleAlert,
  FileText,
  History,
  LibraryBig,
  LoaderCircle,
  MessageCircleQuestion,
  RotateCcw,
  Save,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  Stamp,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/components/ui/use-toast';
import { PlaybookContentEditor } from '@/klm/components/playbooks/PlaybookContentEditor';
import { PlaybookGovernancePanel } from '@/klm/components/playbooks/PlaybookGovernancePanel';
import { PlaybookLibraryEditor } from '@/klm/components/playbooks/PlaybookLibraryEditor';
import { PlaybookObjectionsEditor } from '@/klm/components/playbooks/PlaybookObjectionsEditor';
import { PlaybookOfficeTemplatesEditor } from '@/klm/components/playbooks/PlaybookOfficeTemplatesEditor';
import { PlaybookPolicyEditor } from '@/klm/components/playbooks/PlaybookPolicyEditor';
import { PlaybookProposalEditor } from '@/klm/components/playbooks/PlaybookProposalEditor';
import { PlaybookSandboxPanel } from '@/klm/components/playbooks/PlaybookSandboxPanel';
import { KlmPanel, KlmPill, KlmProgress } from '@/klm/components/Primitives';
import { lifecycleLabel, lifecycleTone } from '@/klm/playbooks/presentation';
import {
  approvePlaybook,
  getPlaybook,
  getPlaybookMetrics,
  listPlaybookSimulations,
  publishPlaybook,
  rollbackPlaybook,
  savePlaybookDraft,
  simulatePlaybook,
  submitPlaybook,
  type PlaybookDetail,
  type PlaybookLifecycleState,
  type PlaybookSimulation,
} from '@/services/playbooks';
import { useAppStore } from '@/store/app-context';

type WorkflowAction = 'submit' | 'approve' | 'publish' | 'rollback';

const actionCopy: Record<WorkflowAction, { title: string; description: string; confirm: string }> = {
  submit: { title: 'Enviar para revisão', description: 'O rascunho ficará bloqueado para edição até ser aprovado ou devolvido por uma nova versão.', confirm: 'Enviar para revisão' },
  approve: { title: 'Aprovar versão', description: 'A aprovação registra o responsável, mas ainda não altera a política efetiva do runtime.', confirm: 'Aprovar versão' },
  publish: { title: 'Publicar playbook', description: 'Esta versão passará a ser a política efetiva para o escopo configurado.', confirm: 'Publicar versão' },
  rollback: { title: 'Restaurar versão', description: 'A versão escolhida será copiada para um novo rascunho. O histórico existente não será alterado.', confirm: 'Criar rascunho restaurado' },
};

function clonePlaybook(playbook: PlaybookDetail): PlaybookDetail {
  return structuredClone(playbook);
}

function editableSnapshot(playbook: PlaybookDetail | null) {
  if (!playbook) return null;
  return {
    name: playbook.name,
    description: playbook.description,
    scope: playbook.scope,
    content: playbook.content,
  };
}

function csv(values: string[]) {
  return values.join(', ');
}

function parseCsv(value: string) {
  return [...new Set(value.split(',').map((item) => item.trim()).filter(Boolean))];
}

function nextActionForState(state: PlaybookLifecycleState): WorkflowAction | null {
  if (state === 'draft') return 'submit';
  if (state === 'in_review') return 'approve';
  if (state === 'approved') return 'publish';
  return null;
}

export default function PlaybookEditorPage() {
  const { playbookId = '' } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const { can } = useAppStore();
  const canEditPermission = can('configuracoes', 'edit');
  const detailQuery = useQuery({ queryKey: ['playbook', playbookId], queryFn: () => getPlaybook(playbookId), enabled: Boolean(playbookId) });
  const metricsQuery = useQuery({ queryKey: ['playbook-metrics', playbookId], queryFn: () => getPlaybookMetrics(playbookId), enabled: Boolean(playbookId) });
  const simulationsQuery = useQuery({ queryKey: ['playbook-simulations', playbookId], queryFn: () => listPlaybookSimulations(playbookId), enabled: Boolean(playbookId) });
  const [draft, setDraft] = useState<PlaybookDetail | null>(null);
  const [reason, setReason] = useState('');
  const [workflowAction, setWorkflowAction] = useState<WorkflowAction | null>(null);
  const [rollbackVersion, setRollbackVersion] = useState<number | null>(null);

  useEffect(() => {
    if (detailQuery.data) setDraft(clonePlaybook(detailQuery.data));
  }, [detailQuery.data]);

  const dirty = useMemo(
    () => JSON.stringify(editableSnapshot(draft)) !== JSON.stringify(editableSnapshot(detailQuery.data ?? null)),
    [detailQuery.data, draft],
  );
  const canEditDraft = canEditPermission && draft?.state === 'draft';

  const refresh = async (saved: PlaybookDetail) => {
    queryClient.setQueryData(['playbook', playbookId], saved);
    setDraft(clonePlaybook(saved));
    await queryClient.invalidateQueries({ queryKey: ['playbooks'] });
    await queryClient.invalidateQueries({ queryKey: ['playbook-metrics', playbookId] });
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!draft) throw new Error('Playbook indisponível.');
      return savePlaybookDraft(playbookId, {
        name: draft.name,
        description: draft.description,
        scope: draft.scope,
        content: draft.content,
        reason: reason.trim() || 'Ajuste do rascunho no Playbook Studio.',
        expectedEtag: draft.etag,
      });
    },
    onSuccess: async (saved) => {
      await refresh(saved);
      setReason('');
      toast({ title: `Playbook salvo como v${saved.currentVersion}.` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível salvar', description: error.message }),
  });

  const workflowMutation = useMutation({
    mutationFn: async () => {
      if (!workflowAction || workflowAction === 'rollback') throw new Error('Ação inválida.');
      if (workflowAction === 'submit') return submitPlaybook(playbookId, reason.trim());
      if (workflowAction === 'approve') return approvePlaybook(playbookId, reason.trim());
      return publishPlaybook(playbookId, reason.trim());
    },
    onSuccess: async (saved) => {
      await refresh(saved);
      toast({ title: `${actionCopy[workflowAction ?? 'submit'].confirm} concluído.` });
      setWorkflowAction(null);
      setReason('');
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'A transição foi recusada', description: error.message }),
  });

  const rollbackMutation = useMutation({
    mutationFn: () => {
      if (rollbackVersion === null) throw new Error('Versão não selecionada.');
      return rollbackPlaybook(playbookId, rollbackVersion, reason.trim());
    },
    onSuccess: async (saved) => {
      await refresh(saved);
      toast({ title: `Versão restaurada em novo rascunho v${saved.currentVersion}.` });
      setWorkflowAction(null);
      setRollbackVersion(null);
      setReason('');
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível restaurar', description: error.message }),
  });

  const simulationMutation = useMutation({
    mutationFn: (input: Parameters<typeof simulatePlaybook>[1]) => simulatePlaybook(playbookId, input),
    onSuccess: async (simulation) => {
      queryClient.setQueryData<PlaybookSimulation[]>(['playbook-simulations', playbookId], (current = []) => [simulation, ...current]);
      await queryClient.invalidateQueries({ queryKey: ['playbook-metrics', playbookId] });
      toast({ title: `Simulação ${simulation.status === 'passed' ? 'aprovada' : 'concluída com revisão'}.` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Simulação indisponível', description: error.message }),
  });

  const openRollback = (version: number) => {
    setRollbackVersion(version);
    setReason(`Restaurar conteúdo da versão ${version}.`);
    setWorkflowAction('rollback');
  };

  if (detailQuery.isError) {
    return <div className="flex min-h-[65vh] flex-col items-center justify-center text-center"><CircleAlert className="h-7 w-7 text-[#a53e3c]" /><p className="mt-3 text-sm font-semibold">Não foi possível abrir o playbook</p><p className="mt-1 text-xs text-muted-foreground">Nenhum conteúdo de demonstração foi carregado.</p><Button className="mt-4" variant="outline" onClick={() => void detailQuery.refetch()}>Tentar novamente</Button></div>;
  }
  if (detailQuery.isLoading || !draft) {
    return <div className="flex min-h-[65vh] items-center justify-center gap-2 text-xs text-muted-foreground" role="status"><LoaderCircle className="h-4 w-4 animate-spin" />Carregando contrato versionado...</div>;
  }

  const workflowNext = nextActionForState(draft.state);
  const workflowBusy = workflowMutation.isPending || rollbackMutation.isPending;
  const tabItems = [
    { value: 'content', label: 'Conteúdo', icon: BookOpenCheck },
    { value: 'objections', label: 'Objeções', icon: MessageCircleQuestion },
    { value: 'policy', label: 'Autonomia', icon: SlidersHorizontal },
    { value: 'library', label: 'Biblioteca', icon: LibraryBig },
    { value: 'modelos', label: 'Modelos do escritório', icon: Stamp },
    { value: 'proposals', label: 'Propostas', icon: FileText },
    { value: 'sandbox', label: 'Sandbox', icon: Beaker },
    { value: 'governance', label: 'Governança', icon: History },
  ];
  const requestedTab = searchParams.get('tab');
  const activeTab = tabItems.some((tab) => tab.value === requestedTab) ? requestedTab! : 'content';

  return (
    <div className="mx-auto max-w-[1380px] space-y-4" data-testid="playbook-editor">
      <header className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <Button type="button" size="icon" variant="ghost" aria-label="Voltar para playbooks" onClick={() => navigate('/app/playbooks')}><ArrowLeft className="h-4 w-4" /></Button>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2"><p className="text-2xs font-semibold uppercase text-[#0057d8]">Playbook Studio</p><KlmPill tone={lifecycleTone[draft.state]}>{lifecycleLabel[draft.state]}</KlmPill><KlmPill tone="black">v{draft.currentVersion}</KlmPill>{dirty && <KlmPill tone="gold">Alterações não salvas</KlmPill>}</div>
            <h1 className="mt-2 truncate text-2xl font-semibold">{draft.name}</h1>
            <p className="mt-1 max-w-3xl text-2xs leading-4 text-muted-foreground">{draft.description}</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 xl:justify-end">
          <div className="min-w-36"><div className="flex items-center justify-between text-2xs"><span className="text-muted-foreground">Cobertura</span><strong>{Math.round(draft.structuralCoverage)}%</strong></div><KlmProgress value={draft.structuralCoverage} tone={draft.structuralCoverage >= 95 ? 'green' : 'gold'} /></div>
          {draft.state === 'draft' && <Button type="button" variant="outline" disabled={!canEditDraft || !dirty || saveMutation.isPending} onClick={() => saveMutation.mutate()}>{saveMutation.isPending ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}Salvar rascunho</Button>}
          {workflowNext && <Button type="button" className="bg-[#031121] text-white hover:bg-[#031121]" disabled={!canEditPermission || dirty || workflowBusy} onClick={() => { setReason(''); setWorkflowAction(workflowNext); }}>{workflowNext === 'submit' ? <Send className="mr-2 h-4 w-4" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}{actionCopy[workflowNext].confirm}</Button>}
          {draft.state === 'published' && <Button type="button" className="bg-[#031121] text-white hover:bg-[#031121]" disabled={!canEditPermission || workflowBusy} onClick={() => openRollback(draft.publishedVersion ?? draft.currentVersion)}><RotateCcw className="mr-2 h-4 w-4" />Criar revisão</Button>}
        </div>
      </header>

      {!canEditDraft && <div className="flex items-center justify-between gap-3 border-l-2 border-primary bg-primary/5 px-3 py-2 text-2xs"><span><strong>Versão bloqueada para edição.</strong> Use governança para comparar ou restaurar uma versão; a simulação continua disponível.</span><ShieldCheck className="h-4 w-4 shrink-0 text-primary" /></div>}

      <Tabs
        value={activeTab}
        onValueChange={(tab) => {
          const next = new URLSearchParams(searchParams);
          next.set('tab', tab);
          setSearchParams(next, { replace: true });
        }}
        className="space-y-3"
      >
        <div className="overflow-x-auto pb-1"><TabsList className="h-10 min-w-max justify-start bg-secondary p-1">{tabItems.map((tab) => { const Icon = tab.icon; return <TabsTrigger key={tab.value} value={tab.value} className="h-8 gap-1.5 px-3 text-2xs"><Icon className="h-3.5 w-3.5" />{tab.label}</TabsTrigger>; })}</TabsList></div>
        <TabsContent value="content" className="mt-0">
          <KlmPanel className="overflow-hidden">
            <div className={canEditDraft ? '' : 'pointer-events-none opacity-65'}>
              <div className="grid gap-3 border-b bg-secondary/30 p-4 md:grid-cols-2 xl:grid-cols-[1fr_1.4fr]">
                <div className="space-y-1.5"><Label className="text-xs">Nome</Label><Input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></div>
                <div className="space-y-1.5"><Label className="text-xs">Objetivo</Label><Input value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} /></div>
                {(['areas', 'channels', 'stages', 'agents', 'offers'] as const).map((key) => <div key={key} className="space-y-1.5"><Label className="text-xs">{key === 'areas' ? 'Áreas' : key === 'channels' ? 'Canais' : key === 'stages' ? 'Etapas' : key === 'agents' ? 'Agentes' : 'Ofertas'}</Label><Input value={csv(draft.scope[key])} onChange={(event) => setDraft({ ...draft, scope: { ...draft.scope, [key]: parseCsv(event.target.value) } })} /></div>)}
              </div>
              <PlaybookContentEditor content={draft.content} onChange={(content) => setDraft({ ...draft, content })} />
            </div>
          </KlmPanel>
        </TabsContent>
        <TabsContent value="objections" className="mt-0"><KlmPanel className="overflow-hidden"><div className={canEditDraft ? '' : 'pointer-events-none opacity-65'}><PlaybookObjectionsEditor content={draft.content} onChange={(content) => setDraft({ ...draft, content })} /></div></KlmPanel></TabsContent>
        <TabsContent value="policy" className="mt-0"><KlmPanel className="overflow-hidden"><div className={canEditDraft ? '' : 'pointer-events-none opacity-65'}><PlaybookPolicyEditor content={draft.content} onChange={(content) => setDraft({ ...draft, content })} /></div></KlmPanel></TabsContent>
        <TabsContent value="library" className="mt-0"><KlmPanel className="overflow-hidden"><div className={canEditDraft ? '' : 'pointer-events-none opacity-65'}><PlaybookLibraryEditor content={draft.content} onChange={(content) => setDraft({ ...draft, content })} /></div></KlmPanel></TabsContent>
        <TabsContent value="modelos" className="mt-0"><KlmPanel className="overflow-hidden"><div className={canEditDraft ? '' : 'pointer-events-none opacity-65'}><PlaybookOfficeTemplatesEditor content={draft.content} onChange={(content) => setDraft({ ...draft, content })} /></div></KlmPanel></TabsContent>
        <TabsContent value="proposals" className="mt-0"><KlmPanel className="overflow-hidden"><div className={canEditDraft ? '' : 'pointer-events-none opacity-65'}><PlaybookProposalEditor content={draft.content} onChange={(content) => setDraft({ ...draft, content })} /></div></KlmPanel></TabsContent>
        <TabsContent value="sandbox" className="mt-0"><KlmPanel className="overflow-hidden"><PlaybookSandboxPanel simulations={simulationsQuery.data ?? []} pending={simulationMutation.isPending} onSimulate={(input) => simulationMutation.mutate(input)} /></KlmPanel></TabsContent>
        <TabsContent value="governance" className="mt-0"><KlmPanel className="overflow-hidden"><PlaybookGovernancePanel playbookId={playbookId} versions={draft.versions} metrics={metricsQuery.data} canEdit={canEditPermission} rollbackPending={rollbackMutation.isPending} onRollback={openRollback} /></KlmPanel></TabsContent>
      </Tabs>

      <Dialog open={workflowAction !== null} onOpenChange={(open) => { if (!open && !workflowBusy) { setWorkflowAction(null); setRollbackVersion(null); } }}>
        <DialogContent className="sm:max-w-[520px]">
          {workflowAction && <><DialogHeader><DialogTitle>{actionCopy[workflowAction].title}</DialogTitle><DialogDescription>{actionCopy[workflowAction].description}</DialogDescription></DialogHeader><div className="space-y-1.5 py-2"><Label htmlFor="playbook-action-reason">Justificativa auditável</Label><Textarea id="playbook-action-reason" className="min-h-24" value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Explique por que esta mudança deve avançar." /></div><DialogFooter><Button type="button" variant="outline" disabled={workflowBusy} onClick={() => { setWorkflowAction(null); setRollbackVersion(null); }}>Cancelar</Button><Button type="button" className="bg-[#031121] text-white hover:bg-[#031121]" disabled={!reason.trim() || workflowBusy} onClick={() => workflowAction === 'rollback' ? rollbackMutation.mutate() : workflowMutation.mutate()}>{workflowBusy && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}{actionCopy[workflowAction].confirm}</Button></DialogFooter></>}
        </DialogContent>
      </Dialog>
    </div>
  );
}
