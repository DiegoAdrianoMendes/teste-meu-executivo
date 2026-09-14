import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  BookOpenCheck,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Copy,
  FileDiff,
  Filter,
  LoaderCircle,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import {
  KlmMetricCard,
  KlmPanel,
  KlmPill,
  KlmProgress,
  KlmSectionTitle,
} from '@/klm/components/Primitives';
import { OperationalCopilot } from '@/klm/components/OperationalCopilot';
import type { KlmOperationalCopilotAction } from '@/klm/api';
import {
  autonomyLabel,
  formatPlaybookDate,
  lifecycleLabel,
  lifecycleTone,
} from '@/klm/playbooks/presentation';
import {
  createPlaybook,
  listPlaybooks,
  type PlaybookAutonomyMode,
  type PlaybookLifecycleState,
  type PlaybookSummary,
} from '@/services/playbooks';
import { useAppStore } from '@/store/app-context';
import { PersonalizedFilterBuilder } from '@/components/common/PersonalizedFilterBuilder';
import {
  EMPTY_PERSONALIZED_FILTER,
  matchesPersonalizedFilter,
  type PersonalizedFilterDefinition,
  type PersonalizedFilterField,
} from '@/lib/personalized-filters';

const defaultPlaybookArea = 'cobranca_indevida';

function average(values: number[]): number {
  return values.length ? values.reduce((total, value) => total + value, 0) / values.length : 0;
}

export default function PlaybooksPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { can, state } = useAppStore();
  const canEdit = can('configuracoes', 'edit');
  const query = useQuery({ queryKey: ['playbooks'], queryFn: listPlaybooks });
  const [search, setSearch] = useState('');
  const [stateFilter, setStateFilter] = useState<'all' | PlaybookLifecycleState>('all');
  const [areaFilter, setAreaFilter] = useState('all');
  const [personalizedFilter, setPersonalizedFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [cloneFromId, setCloneFromId] = useState<string>('none');
  const [autonomyMode, setAutonomyMode] = useState<PlaybookAutonomyMode>('ai_triage_human_meeting');
  const [reviewGaps, setReviewGaps] = useState(false);

  const playbooks = useMemo(() => query.data ?? [], [query.data]);
  const areaOptions = useMemo(
    () => [...new Set(playbooks.flatMap((playbook) => playbook.scope.areas))].sort(),
    [playbooks],
  );
  const personalizedFields = useMemo<PersonalizedFilterField<PlaybookSummary>[]>(() => [
    { key: 'name', label: 'Playbook', type: 'text' },
    { key: 'description', label: 'Descrição', type: 'text' },
    { key: 'state', label: 'Estado', type: 'select', options: Object.entries(lifecycleLabel).map(([value, label]) => ({ value, label })) },
    { key: 'scope.areas', label: 'Área', type: 'text' },
    { key: 'scope.channels', label: 'Canal', type: 'text' },
    { key: 'scope.agents', label: 'Agente', type: 'text' },
    { key: 'autonomyMode', label: 'Autonomia', type: 'select', options: [...new Set(playbooks.map((item) => item.autonomyMode))].map((value) => ({ value, label: autonomyLabel[value] })) },
    { key: 'structuralCoverage', label: 'Cobertura estrutural (%)', type: 'number' },
    { key: 'simulationPassRate', label: 'Aderência (%)', type: 'number' },
    { key: 'simulations', label: 'Simulações', type: 'number' },
  ], [playbooks]);
  const filtered = useMemo(() => {
    const normalizedSearch = search.trim().toLocaleLowerCase('pt-BR');
    return playbooks.filter((playbook) => {
      if (stateFilter !== 'all' && playbook.state !== stateFilter) return false;
      if (areaFilter !== 'all' && !playbook.scope.areas.includes(areaFilter)) return false;
      if (!matchesPersonalizedFilter(playbook, personalizedFilter, personalizedFields)) return false;
      if (!normalizedSearch) return true;
      return [
        playbook.name,
        playbook.description,
        ...playbook.scope.areas,
        ...playbook.scope.channels,
        ...playbook.scope.agents,
      ].join(' ').toLocaleLowerCase('pt-BR').includes(normalizedSearch);
    });
  }, [areaFilter, personalizedFields, personalizedFilter, playbooks, search, stateFilter]);
  const displayed = useMemo(
    () => reviewGaps
      ? [...filtered].sort((left, right) => left.structuralCoverage - right.structuralCoverage)
      : filtered,
    [filtered, reviewGaps],
  );
  const copilotContext = useMemo(() => ({
    filters: {
      search,
      state: stateFilter,
      area: areaFilter,
      review_gaps: reviewGaps,
    },
    playbooks: playbooks.slice(0, 50).map((playbook) => ({
      id: playbook.id,
      name: playbook.name,
      state: playbook.state,
      areas: playbook.scope.areas,
      autonomy_mode: playbook.autonomyMode,
      structural_coverage: playbook.structuralCoverage,
      simulation_pass_rate: playbook.simulationPassRate,
    })),
  }), [areaFilter, playbooks, reviewGaps, search, stateFilter]);

  const createMutation = useMutation({
    mutationFn: () => createPlaybook({
      name: name.trim(),
      description: description.trim(),
      autonomyMode,
      cloneFromId: cloneFromId === 'none' ? null : cloneFromId,
      scope: {
        areas: [defaultPlaybookArea],
        channels: ['whatsapp'],
        stages: ['triagem', 'qualificacao', 'proposta', 'fechamento'],
        agents: ['orquestrador', 'sdr', 'juridico', 'closer', 'hermes'],
        offers: ['servicos_advocaticios'],
      },
    }),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ['playbooks'] });
      setDialogOpen(false);
      setName('');
      setDescription('');
      setCloneFromId('none');
      navigate(`/app/playbooks/${created.id}`);
    },
  });

  const published = playbooks.filter((playbook) => playbook.state === 'published').length;
  const simulations = playbooks.reduce((total, playbook) => total + playbook.simulations, 0);
  const coverage = average(playbooks.map((playbook) => playbook.structuralCoverage));
  const passRate = average(playbooks.map((playbook) => playbook.simulationPassRate));

  const openCreate = (source?: PlaybookSummary) => {
    setCloneFromId(source?.id ?? 'none');
    setName(source ? `${source.name} · cópia` : 'Cobrança indevida');
    setDescription(source?.description ?? 'Atendimento, triagem, qualificação e fechamento para cobrança indevida.');
    setAutonomyMode(source?.autonomyMode ?? 'ai_triage_human_meeting');
    setDialogOpen(true);
  };

  const applyCopilotActions = async (actions: KlmOperationalCopilotAction[]) => {
    for (const action of actions) {
      const actionType = typeof action.payload.type === 'string' ? action.payload.type : '';
      if (actionType === 'playbooks.create_draft') {
        if (!canEdit) throw new Error('Seu perfil não pode criar playbooks.');
        const sourceId = typeof action.payload.clone_from_id === 'string'
          && playbooks.some((playbook) => playbook.id === action.payload.clone_from_id)
          ? action.payload.clone_from_id
          : 'none';
        const source = sourceId === 'none'
          ? undefined
          : playbooks.find((playbook) => playbook.id === sourceId);
        setCloneFromId(sourceId);
        setName(
          typeof action.payload.name === 'string' && action.payload.name.trim()
            ? action.payload.name.trim().slice(0, 160)
            : source
              ? `${source.name} · cópia`
              : 'Novo playbook',
        );
        setDescription(
          typeof action.payload.description === 'string'
            ? action.payload.description.trim().slice(0, 600)
            : source?.description ?? 'Atendimento estruturado com regras, critérios e aprovação humana.',
        );
        setAutonomyMode(source?.autonomyMode ?? 'ai_triage_human_meeting');
        setDialogOpen(true);
        continue;
      }
      if (actionType === 'playbooks.open_editor') {
        const playbookId = typeof action.payload.playbook_id === 'string'
          ? action.payload.playbook_id
          : '';
        if (!playbooks.some((playbook) => playbook.id === playbookId)) {
          throw new Error('O playbook indicado não está mais disponível nesta biblioteca.');
        }
        navigate(`/app/playbooks/${playbookId}`);
        continue;
      }
      if (actionType === 'playbooks.review_gaps') {
        setSearch('');
        setStateFilter('all');
        setAreaFilter('all');
        setPersonalizedFilter(EMPTY_PERSONALIZED_FILTER);
        setReviewGaps(true);
      }
    }
    return 'A proposta foi aplicada à biblioteca. Nenhuma política foi publicada.';
  };

  return (
    <div className="mx-auto max-w-[1220px] space-y-5" data-testid="playbook-library">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Meu Executivo / Inteligência comercial</p>
          <h1 className="mt-2 text-2xl font-semibold">Playbooks</h1>
          <p className="mt-1 max-w-3xl text-xs leading-4 text-muted-foreground">
            Política executável para voz, triagem, honorários, objeções, follow-up, handoffs e autonomia dos agentes.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <KlmPill tone="green"><ShieldCheck className="mr-1 h-3 w-3" />Versionado e auditável</KlmPill>
          <Button
            type="button"
            className="h-9 bg-[#031121] text-xs text-white hover:bg-[#031121]"
            disabled={!canEdit}
            onClick={() => openCreate()}
          >
            <Plus className="mr-2 h-3.5 w-3.5" />Novo playbook
          </Button>
        </div>
      </header>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KlmMetricCard label="Publicados" value={String(published)} delta={`${playbooks.length} playbooks no workspace`} trend="up" />
        <KlmMetricCard label="Cobertura estrutural" value={`${Math.round(coverage)}%`} delta="13 blocos obrigatórios" trend={coverage >= 95 ? 'up' : 'neutral'} />
        <KlmMetricCard label="Simulações" value={String(simulations)} delta="sem ações externas" trend="neutral" />
        <KlmMetricCard label="Aderência média" value={`${Math.round(passRate)}%`} delta="respostas com política atribuída" trend={passRate >= 95 ? 'up' : 'neutral'} />
      </div>

      <KlmPanel className="overflow-hidden">
        <div className="border-b p-4">
          <KlmSectionTitle
            title="Biblioteca do escritório"
            description="Um registro canônico por política; áreas, agentes, flows e propostas apontam para esta mesma versão."
            action={<KlmPill tone="muted">{displayed.length} exibidos</KlmPill>}
          />
          <div className="mt-4 grid gap-2 md:grid-cols-[minmax(0,1fr)_180px_200px_auto]">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="h-9 bg-secondary pl-9 text-xs"
                placeholder="Buscar por nome, área, canal ou agente"
                aria-label="Buscar playbooks"
              />
            </div>
            <Select value={stateFilter} onValueChange={(value) => setStateFilter(value as typeof stateFilter)}>
              <SelectTrigger className="h-9 text-xs"><Filter className="mr-2 h-3.5 w-3.5" /><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todos os estados</SelectItem>
                {Object.entries(lifecycleLabel).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}
              </SelectContent>
            </Select>
            <PersonalizedFilterBuilder scope="playbooks" fields={personalizedFields} value={personalizedFilter} onChange={setPersonalizedFilter} className="h-9" />
            <Select value={areaFilter} onValueChange={setAreaFilter}>
              <SelectTrigger className="h-9 text-xs"><SelectValue placeholder="Todas as áreas" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todas as áreas</SelectItem>
                {areaOptions.map((area) => <SelectItem key={area} value={area}>{area.replaceAll('_', ' ')}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </div>

        {query.isLoading && (
          <div className="flex min-h-56 items-center justify-center gap-2 text-xs text-muted-foreground" role="status">
            <LoaderCircle className="h-4 w-4 animate-spin" />Carregando playbooks versionados...
          </div>
        )}
        {query.isError && (
          <div className="flex min-h-56 flex-col items-center justify-center p-6 text-center">
            <CircleAlert className="h-6 w-6 text-[#a53e3c]" />
            <p className="mt-3 text-sm font-semibold">Não foi possível carregar a biblioteca</p>
            <p className="mt-1 text-xs text-muted-foreground">Nenhum playbook de demonstração foi usado como substituto.</p>
            <Button className="mt-4" variant="outline" onClick={() => void query.refetch()}>Tentar novamente</Button>
          </div>
        )}
        {query.isSuccess && displayed.length === 0 && (
          <div className="flex min-h-56 flex-col items-center justify-center p-6 text-center">
            <BookOpenCheck className="h-7 w-7 text-primary" />
            <p className="mt-3 text-sm font-semibold">Nenhum playbook neste filtro</p>
            <p className="mt-1 text-xs text-muted-foreground">Ajuste os filtros ou crie a primeira política estruturada.</p>
          </div>
        )}
        {query.isSuccess && displayed.length > 0 && (
          <div className="divide-y">
            {displayed.map((playbook) => (
              <article key={playbook.id} className="grid gap-4 p-4 transition hover:bg-secondary/45 lg:grid-cols-[minmax(240px,1.2fr)_minmax(200px,.8fr)_150px_110px_auto] lg:items-center">
                <button type="button" onClick={() => navigate(`/app/playbooks/${playbook.id}`)} className="min-w-0 text-left">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="truncate text-sm font-semibold">{playbook.name}</h2>
                    <KlmPill tone={lifecycleTone[playbook.state]}>{lifecycleLabel[playbook.state]}</KlmPill>
                  </div>
                  <p className="mt-1 line-clamp-2 text-2xs leading-4 text-muted-foreground">{playbook.description}</p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {playbook.scope.areas.slice(0, 2).map((area) => <KlmPill key={area} tone="muted">{area.replaceAll('_', ' ')}</KlmPill>)}
                    {playbook.scope.channels.slice(0, 2).map((channel) => <KlmPill key={channel} tone="blue">{channel}</KlmPill>)}
                  </div>
                </button>
                <div>
                  <div className="flex items-center justify-between text-2xs"><span className="text-muted-foreground">Cobertura do contrato</span><strong>{Math.round(playbook.structuralCoverage)}%</strong></div>
                  <KlmProgress value={playbook.structuralCoverage} tone={playbook.structuralCoverage >= 95 ? 'green' : 'gold'} />
                  <div className="mt-2 flex items-center gap-1.5 text-2xs text-muted-foreground"><Bot className="h-3 w-3" />{autonomyLabel[playbook.autonomyMode]}</div>
                </div>
                <div className="text-2xs">
                  <p className="text-muted-foreground">Versão efetiva</p>
                  <p className="mt-1 font-semibold">v{playbook.publishedVersion ?? playbook.currentVersion}</p>
                  <p className="mt-1 text-muted-foreground">{formatPlaybookDate(playbook.updatedAt)}</p>
                </div>
                <div className="text-2xs">
                  <p className="text-muted-foreground">Simulações</p>
                  <p className="mt-1 flex items-center gap-1 font-semibold"><CheckCircle2 className="h-3 w-3 text-emerald-600" />{Math.round(playbook.simulationPassRate)}%</p>
                  <p className="mt-1 text-muted-foreground">{playbook.simulations} execuções</p>
                </div>
                <div className="flex items-center justify-end gap-1">
                  <Button type="button" size="icon" variant="ghost" title="Clonar playbook" aria-label={`Clonar ${playbook.name}`} disabled={!canEdit} onClick={() => openCreate(playbook)}><Copy className="h-4 w-4" /></Button>
                  <Button type="button" size="icon" variant="ghost" title="Abrir editor" aria-label={`Abrir ${playbook.name}`} onClick={() => navigate(`/app/playbooks/${playbook.id}`)}><ChevronRight className="h-4 w-4" /></Button>
                </div>
              </article>
            ))}
          </div>
        )}
      </KlmPanel>

      <div className="grid gap-3 md:grid-cols-3">
        <div className="flex items-start gap-3 border-l-2 border-primary px-3 py-2"><FileDiff className="mt-0.5 h-4 w-4 text-primary" /><div><p className="text-xs font-semibold">Diff antes de publicar</p><p className="mt-0.5 text-2xs text-muted-foreground">Toda mudança mostra caminho, valor anterior e valor proposto.</p></div></div>
        <div className="flex items-start gap-3 border-l-2 border-[#031121] px-3 py-2"><Sparkles className="mt-0.5 h-4 w-4 text-[#031121] dark:text-blue-300" /><div><p className="text-xs font-semibold">Sandbox local</p><p className="mt-0.5 text-2xs text-muted-foreground">Respostas apontam versão, regras, fontes e necessidade de aprovação.</p></div></div>
        <div className="flex items-start gap-3 border-l-2 border-emerald-600 px-3 py-2"><ShieldCheck className="mt-0.5 h-4 w-4 text-emerald-600" /><div><p className="text-xs font-semibold">Publicação governada</p><p className="mt-0.5 text-2xs text-muted-foreground">Rascunho, revisão, aprovação, publicação e reversão auditáveis.</p></div></div>
      </div>

      <OperationalCopilot
        domain="playbooks"
        title="Copiloto de Playbooks"
        subtitle="Rascunhos, lacunas e governança"
        welcome="Descreva a política, o público e os limites. Eu preparo um rascunho ou organizo as lacunas para sua aprovação."
        placeholder="Ex.: crie um playbook para reajuste abusivo com reunião humana obrigatória."
        contextKey="library"
        contextSnapshot={copilotContext}
        workspaceId={state?.activeWorkspace?.id}
        userId={state?.currentUser?.id}
        canApply={can('configuracoes', 'view')}
        onApply={applyCopilotActions}
      />

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-[620px]">
          <DialogHeader>
            <DialogTitle>{cloneFromId === 'none' ? 'Novo playbook' : 'Clonar playbook'}</DialogTitle>
            <DialogDescription>
              O novo registro nasce como rascunho isolado. Nenhuma política publicada é alterada.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="space-y-1.5"><Label htmlFor="playbook-name">Nome</Label><Input id="playbook-name" value={name} onChange={(event) => setName(event.target.value)} maxLength={160} /></div>
            <div className="space-y-1.5"><Label htmlFor="playbook-description">Objetivo</Label><Textarea id="playbook-description" value={description} onChange={(event) => setDescription(event.target.value)} maxLength={600} className="min-h-24" /></div>
            <div className="space-y-1.5">
              <Label>Base</Label>
              <Select value={cloneFromId} onValueChange={setCloneFromId}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="none">Contrato padrão</SelectItem>{playbooks.map((playbook) => <SelectItem key={playbook.id} value={playbook.id}>{playbook.name} · v{playbook.currentVersion}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Autonomia inicial</Label>
              <Select value={autonomyMode} onValueChange={(value) => setAutonomyMode(value as PlaybookAutonomyMode)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{Object.entries(autonomyLabel).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            {createMutation.isError && <p className="text-xs text-destructive" role="alert">{createMutation.error instanceof Error ? createMutation.error.message : 'Não foi possível criar o playbook.'}</p>}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>Cancelar</Button>
            <Button disabled={!name.trim() || createMutation.isPending} onClick={() => createMutation.mutate()} className="bg-[#031121] text-white hover:bg-[#031121]">
              {createMutation.isPending ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : cloneFromId === 'none' ? <Plus className="mr-2 h-4 w-4" /> : <Copy className="mr-2 h-4 w-4" />}
              {cloneFromId === 'none' ? 'Criar rascunho' : 'Criar cópia'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
