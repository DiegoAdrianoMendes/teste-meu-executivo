import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Bot,
  Check,
  ChevronRight,
  FileText,
  GitBranch,
  LockKeyhole,
  Package,
  Plus,
  Search,
  ShieldCheck,
  Snowflake,
  Sparkles,
  UserRoundCog,
  WandSparkles,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { KlmMetricCard, KlmPanel, KlmPill, KlmProgress, KlmSectionTitle } from '@/klm/components/Primitives';
import { EffectivePlaybookLink } from '@/klm/components/playbooks/EffectivePlaybookLink';
import { klmApi, type KlmArea, type KlmAreaBlueprint, type KlmAreaSubagent } from '@/klm/api';
import { cn } from '@/lib/utils';
import { useAppStore } from '@/store/app-context';

type AreaView = {
  id: string;
  raw: KlmArea;
  name: string;
  state: 'Ativa' | 'Congelada' | 'Personalizando';
  readiness: number;
  description: string;
  products: number;
  subagents: number;
};

const prettify = (value: string) => value
  .replaceAll('_', ' ')
  .replace(/\b\w/g, (letter) => letter.toUpperCase());

const cloneBlueprint = (value: KlmAreaBlueprint): KlmAreaBlueprint => JSON.parse(JSON.stringify(value));

function mapArea(area: KlmArea): AreaView {
  const products = area.blueprint.products ?? [];
  const subagents = area.blueprint.subagents ?? [];
  return {
    id: area.id,
    raw: area,
    name: area.name,
    state: area.isActive ? 'Ativa' : area.blueprint.catalogManaged === false ? 'Personalizando' : 'Congelada',
    readiness: area.readiness,
    description: area.description ?? 'Área jurídica pronta para receber produtos, playbooks e agentes especializados.',
    products: products.length,
    subagents: subagents.length,
  };
}

const coreAgentLabels: Record<string, string> = {
  orquestrador: 'Orquestrador',
  hermes: 'Hermes',
  secretaria: 'Secretaria',
  juridico: 'Jurídico',
  closer: 'Closer',
  sdr: 'SDR',
};

function parentLabel(agent: KlmAreaSubagent, subagents: KlmAreaSubagent[]) {
  return subagents.find((candidate) => candidate.key === agent.parentAgentKey)?.name
    ?? coreAgentLabels[agent.parentAgentKey]
    ?? prettify(agent.parentAgentKey);
}

export default function AreasPage() {
  const { state } = useAppStore();
  const queryClient = useQueryClient();
  const areasQuery = useQuery({ queryKey: ['klm', 'areas'], queryFn: klmApi.areas });
  const [selectedId, setSelectedId] = useState('');
  const [query, setQuery] = useState('');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [draftName, setDraftName] = useState('');
  const [draftDescription, setDraftDescription] = useState('');
  const [draftTone, setDraftTone] = useState<'formal' | 'balanced' | 'conversational'>('balanced');
  const [draftSource, setDraftSource] = useState<KlmArea | null>(null);
  const [dialogPurpose, setDialogPurpose] = useState<'create' | 'adapt' | 'duplicate'>('create');
  const [notice, setNotice] = useState<string | null>(null);
  const areas = useMemo(() => (areasQuery.data ?? []).map(mapArea), [areasQuery.data]);
  const selected = areas.find((area) => area.id === selectedId) ?? areas[0] ?? null;
  const filtered = useMemo(
    () => areas.filter((area) => `${area.name} ${area.description}`.toLocaleLowerCase('pt-BR').includes(query.toLocaleLowerCase('pt-BR'))),
    [areas, query],
  );
  const products = selected?.raw.blueprint.products ?? [];
  const subagents = selected?.raw.blueprint.subagents ?? [];
  const governance = selected?.raw.blueprint.agentGovernance;
  const currentUser = state?.currentUser;
  const canUnlock = Boolean(
    currentUser?.isPlatformAdmin
      || (
        currentUser?.isInternalStaff
        && currentUser?.platformAccessActive
        && ['platform_owner', 'platform_admin', 'onboarding', 'support', 'technical_support', 'engineering'].includes(currentUser.platformRolePackage ?? '')
      ),
  );

  useEffect(() => {
    if (areas.length && !areas.some((area) => area.id === selectedId)) setSelectedId(areas[0].id);
  }, [areas, selectedId]);

  const replaceArea = (area: KlmArea) => {
    queryClient.setQueryData<KlmArea[]>(['klm', 'areas'], (current = []) => current.map((item) => item.id === area.id ? area : item));
  };

  const createMutation = useMutation({
    mutationFn: async ({ name, description, tone, source }: { name: string; description: string; tone: typeof draftTone; source: KlmArea | null }) => {
      const created = await klmApi.createArea(name, source);
      const blueprint = cloneBlueprint(created.blueprint);
      blueprint.communicationProfile = {
        ...(blueprint.communicationProfile ?? {}),
        tone,
        formality: tone === 'formal' ? 'formal' : 'balanced',
        neutralLanguage: true,
        plainLanguage: true,
        signatureStyle: 'office',
        suggestionsRequireHumanConfirmation: true,
      };
      return klmApi.updateArea(created.id, { description, blueprint });
    },
    onSuccess: (area) => {
      queryClient.setQueryData<KlmArea[]>(['klm', 'areas'], (current = []) => [...current.filter((item) => item.id !== area.id), area]);
      setSelectedId(area.id);
      setDialogOpen(false);
      setDraftName('');
      setDraftDescription('');
      setDraftSource(null);
      setDialogPurpose('create');
      setNotice('A nova área foi criada com escopo básico, comunicação configurável e revisão humana obrigatória.');
    },
    onError: () => setNotice('Não foi possível criar a área. Revise o nome e tente novamente.'),
  });

  const activationMutation = useMutation({
    mutationFn: ({ areaId, enabled }: { areaId: string; enabled: boolean }) => klmApi.setAreaActivation(areaId, enabled),
    onSuccess: (area) => {
      replaceArea(area);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'agents'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
      setNotice(area.isActive
        ? `${area.name} foi liberada com produtos, playbooks e agentes vinculados.`
        : `${area.name} foi congelada. Histórico e dados permanecem preservados.`);
    },
    onError: () => setNotice('A liberação foi recusada. Somente o Super Admin ou a equipe SaaS autorizada pode alterar este estado.'),
  });

  const requestUnlockMutation = useMutation({
    mutationFn: (area: KlmArea) => klmApi.createTechnicalRequest({
      title: `Solicitação de liberação: ${area.name}`,
      description: `O escritório solicitou a liberação da área ${area.name}. Validar limite do plano, escopo e responsáveis antes de ativar.`,
      route: '/app/areas',
      severity: 'medium',
      evidence: [{ areaId: area.id, areaKey: area.areaKey, currentState: area.status }],
    }),
    onSuccess: () => setNotice('Solicitação registrada para a equipe responsável pelo seu onboarding.'),
    onError: () => setNotice('Não foi possível registrar a solicitação agora.'),
  });

  const openCreate = (source: KlmArea | null = null) => {
    setDraftSource(source);
    setDialogPurpose(source ? 'adapt' : 'create');
    setDraftName(source ? `${source.name} adaptada` : '');
    setDraftDescription(source?.description ?? '');
    setDraftTone('balanced');
    setDialogOpen(true);
  };

  const openDuplicate = () => {
    if (!selected) return;
    setDraftSource(selected.raw);
    setDialogPurpose('duplicate');
    setDraftName(`${selected.name} (cópia)`);
    setDraftDescription(selected.description);
    setDraftTone('balanced');
    setDialogOpen(true);
  };

  const createDraft = () => {
    const name = draftName.trim();
    if (name) createMutation.mutate({ name, description: draftDescription.trim(), tone: draftTone, source: draftSource });
  };

  return (
    <div className="mx-auto max-w-[1220px] space-y-5">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Meu Executivo / Estrutura jurídica</p>
          <h2 className="mt-2 text-2xl font-semibold">Áreas, produtos e agentes</h2>
          <p className="mt-1 max-w-3xl text-xs leading-4 text-muted-foreground">Ative novas operações sem misturar clientes, produtos, playbooks ou autoridade entre agentes.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <KlmPill tone="green">{areas.filter((area) => area.state === 'Ativa').length} em operação</KlmPill>
          <KlmPill tone="muted">{areas.filter((area) => area.state === 'Congelada').length} congeladas</KlmPill>
          <Button onClick={() => openCreate()} className="h-9 bg-[#031121] text-xs text-white hover:bg-[#031121]"><Plus className="mr-2 h-3.5 w-3.5" />Criar área</Button>
        </div>
      </header>

      {notice && <div className="flex items-center justify-between gap-3 rounded-lg border border-primary/35 bg-primary/10 px-3 py-2 text-2xs"><span>{notice}</span><Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setNotice(null)}>Fechar</Button></div>}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KlmMetricCard label="Áreas disponíveis" value={String(areas.length)} delta="catálogo jurídico" trend="neutral" />
        <KlmMetricCard label="Produtos jurídicos" value={String(areas.reduce((total, area) => total + area.products, 0))} delta="rotas especializadas" trend="up" />
        <KlmMetricCard label="Agentes especializados" value={String(areas.reduce((total, area) => total + area.subagents, 0))} delta="hierarquia rígida" trend="up" />
        <KlmMetricCard label="Áreas ativas" value={String(areas.filter((area) => area.state === 'Ativa').length)} delta="conforme o plano" trend="neutral" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[.8fr_1.2fr]">
        <KlmPanel className="min-h-0 p-4">
          <KlmSectionTitle title="Catálogo jurídico" description="Escolha uma área para ver produtos, agentes e regras." />
          <div className="relative mt-4"><Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" /><Input className="h-9 bg-secondary pl-9 text-xs" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar área" /></div>
          <div className="mt-3 max-h-[690px] space-y-2 overflow-y-auto pr-1">
            {filtered.map((area) => (
              <button key={area.id} onClick={() => setSelectedId(area.id)} className={cn('w-full rounded-lg border p-3 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring', selected?.id === area.id ? 'border-[#0057d8] bg-[#0057d8]/10' : 'border-border bg-card hover:border-[#0057d8]/50')}>
                <div className="flex items-start gap-3">
                  <span className={cn('flex h-8 w-8 shrink-0 items-center justify-center rounded-md', area.state === 'Ativa' ? 'bg-emerald-100 text-emerald-700' : 'bg-secondary text-muted-foreground')}>{area.state === 'Ativa' ? <Check className="h-4 w-4" /> : <Snowflake className="h-4 w-4" />}</span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center justify-between gap-2"><strong className="text-xs">{area.name}</strong><KlmPill tone={area.state === 'Ativa' ? 'green' : area.state === 'Personalizando' ? 'gold' : 'muted'}>{area.state}</KlmPill></span>
                    <span className="mt-1 block text-2xs text-muted-foreground">{area.products} produtos · {area.subagents} agentes especializados</span>
                    <span className="mt-2 grid grid-cols-[1fr_auto] items-center gap-3"><KlmProgress value={area.readiness} tone={area.state === 'Ativa' ? 'green' : 'gold'} /><span className="text-2xs font-semibold">{area.readiness}%</span></span>
                  </span>
                  <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />
                </div>
              </button>
            ))}
          </div>
        </KlmPanel>

        {selected ? <div className="min-w-0 space-y-4">
          <KlmPanel tone="black" className="overflow-hidden">
            <div className="border-b border-white/10 p-5">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div><p className="text-2xs font-semibold uppercase text-primary">Operação jurídica</p><h2 className="mt-2 text-lg font-semibold">{selected.name}</h2><p className="mt-2 max-w-2xl text-2xs leading-4 text-white/60">{selected.description}</p></div>
                <div className="flex items-center gap-3 rounded-lg border border-white/15 px-3 py-2">
                  <div className="text-right"><strong className="block text-2xs">{selected.state}</strong><span className="text-2xs text-white/45">Histórico sempre preservado</span></div>
                  {canUnlock ? <Switch checked={selected.raw.isActive} disabled={activationMutation.isPending} onCheckedChange={(enabled) => activationMutation.mutate({ areaId: selected.id, enabled })} aria-label={`${selected.raw.isActive ? 'Congelar' : 'Liberar'} ${selected.name}`} /> : <LockKeyhole className="h-4 w-4 text-primary" />}
                </div>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-px bg-white/10 sm:grid-cols-4">
              {[
                ['Produtos', String(products.length)],
                ['Agentes', String(subagents.length)],
                ['Contratos', String(selected.raw.blueprint.contracts?.length ?? 0)],
                ['Preparação', `${selected.readiness}%`],
              ].map(([label, value]) => <div key={label} className="bg-[#031121] p-4"><span className="text-2xs text-white/45">{label}</span><strong className="mt-1 block text-sm font-medium text-white">{value}</strong></div>)}
            </div>
          </KlmPanel>

          <div className="flex flex-wrap gap-2">
            <EffectivePlaybookLink selectors={{ area: selected.raw.areaKey }} className="min-w-0 flex-1 justify-start" />
            <Button variant="outline" className="h-10 text-2xs" onClick={openDuplicate}><FileText className="mr-2 h-3.5 w-3.5" />Duplicar</Button>
            {!canUnlock && !selected.raw.isActive && <Button variant="outline" className="h-10 text-2xs" disabled={requestUnlockMutation.isPending} onClick={() => requestUnlockMutation.mutate(selected.raw)}><LockKeyhole className="mr-2 h-3.5 w-3.5" />Solicitar liberação</Button>}
            <Button variant="outline" className="h-10 text-2xs" onClick={() => openCreate(selected.raw)}><Plus className="mr-2 h-3.5 w-3.5" />Adaptar como nova área</Button>
          </div>

          <KlmPanel className="p-4">
            <KlmSectionTitle title="Produtos e subprodutos" description="Cada rota possui público, documentos, contrato, especialista e closer próprios." action={<KlmPill tone="green">{products.length} rotas</KlmPill>} />
            <div className="mt-4 grid max-h-[360px] gap-2 overflow-y-auto pr-1 md:grid-cols-2">
              {products.map((product) => (
                <div key={product.key} className="rounded-lg border border-border bg-secondary/45 p-3">
                  <div className="flex items-start justify-between gap-2"><div className="flex min-w-0 items-center gap-2"><Package className="h-4 w-4 shrink-0 text-[#0057d8]" /><strong className="truncate text-2xs">{product.label}</strong></div><KlmPill tone="muted">{product.requiredDocuments.length} docs</KlmPill></div>
                  <p className="mt-2 line-clamp-2 text-2xs leading-4 text-muted-foreground">{product.description}</p>
                  <div className="mt-3 grid grid-cols-2 gap-2 text-2xs"><div><span className="block text-muted-foreground">Especialista</span><strong>{prettify(product.specialistAgentKey)}</strong></div><div><span className="block text-muted-foreground">Closer</span><strong>{prettify(product.closerAgentKey)}</strong></div></div>
                </div>
              ))}
              {!products.length && <div className="col-span-full rounded-lg border border-dashed p-6 text-center text-2xs text-muted-foreground">Use o assistente de configuração para definir os primeiros produtos desta área.</div>}
            </div>
          </KlmPanel>

          <KlmPanel className="p-4">
            <KlmSectionTitle title="Hierarquia dos agentes" description="A delegação só acontece pelo pai aprovado e dentro do produto do caso." action={<KlmPill tone="black">profundidade máxima {governance?.maxDelegationDepth ?? 4}</KlmPill>} />
            <div className="mt-4 max-h-[340px] space-y-2 overflow-y-auto pr-1">
              {subagents.map((agent) => (
                <div key={agent.key} className="grid gap-3 rounded-lg border p-3 sm:grid-cols-[34px_1fr_auto] sm:items-center">
                  <span className="flex h-8 w-8 items-center justify-center rounded-md bg-[#031121] text-primary"><Bot className="h-4 w-4" /></span>
                  <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><strong className="text-2xs">{agent.name}</strong>{agent.productKey && <KlmPill tone="gold">{prettify(agent.productKey)}</KlmPill>}</div><p className="mt-1 text-2xs text-muted-foreground">Recebe de <strong className="text-foreground">{parentLabel(agent, subagents)}</strong> · {agent.tools.length} ferramentas permitidas</p></div>
                  <KlmPill tone="muted">{prettify(agent.taskClass)}</KlmPill>
                </div>
              ))}
            </div>
          </KlmPanel>

          <KlmPanel className="p-4">
            <KlmSectionTitle title="Regras invioláveis" description="Nem prompt, modelo ou agente pode mudar estas decisões." action={<KlmPill tone="green"><ShieldCheck className="mr-1 h-3 w-3" />Política {governance?.policyVersion ?? 'vigente'}</KlmPill>} />
            <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {[
                ['Correção manual prevalece', governance?.manualOverrideWins],
                ['Cliente e conta isolados', governance?.crossClientAccessDenied && governance?.crossWorkspaceAccessDenied],
                ['Área e produto isolados', governance?.crossAreaAccessDisabled && governance?.crossProductAccessDisabled],
                ['Ferramentas por menor privilégio', governance?.leastPrivilegeTools],
                ['Hermes não orquestra', governance?.hermesCannotOrchestrate],
                ['Orquestrador coordena a fila humana', selected.raw.blueprint.routing?.coordinationAgentKey === 'orquestrador'],
                ['Revisão independente', governance?.independentReviewRequired],
              ].map(([label, enabled]) => <div key={String(label)} className="flex items-center gap-2 rounded-lg border bg-secondary/45 p-3"><span className={cn('flex h-6 w-6 items-center justify-center rounded-full', enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700')}>{enabled ? <Check className="h-3.5 w-3.5" /> : <LockKeyhole className="h-3.5 w-3.5" />}</span><strong className="text-2xs">{String(label)}</strong></div>)}
            </div>
          </KlmPanel>
        </div> : <KlmPanel className="flex min-h-80 items-center justify-center p-8 text-xs text-muted-foreground">Carregando áreas jurídicas...</KlmPanel>}
      </div>

      <Dialog open={dialogOpen} onOpenChange={(open) => { setDialogOpen(open); if (!open) setDraftSource(null); }}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle>{dialogPurpose === 'duplicate' ? 'Duplicar blueprint' : draftSource ? 'Adaptar nova área' : 'Criar área jurídica'}</DialogTitle><DialogDescription>O assistente monta o escopo inicial com hierarquia, isolamento e aprovação humana. A liberação continua restrita à equipe autorizada do SaaS.</DialogDescription></DialogHeader>
          <div className="space-y-4">
            <div><label htmlFor="area-name" className="text-2xs font-semibold">Nome da área</label><Input id="area-name" value={draftName} onChange={(event) => setDraftName(event.target.value)} className="mt-2" placeholder="Ex.: Direito ambiental" /></div>
            <div><label htmlFor="area-description" className="text-2xs font-semibold">Escopo e público</label><Textarea id="area-description" value={draftDescription} onChange={(event) => setDraftDescription(event.target.value)} className="mt-2 min-h-24" placeholder="Descreva demandas, produtos e público atendido." /></div>
            <div><label htmlFor="area-tone" className="text-2xs font-semibold">Forma de comunicação</label><select id="area-tone" value={draftTone} onChange={(event) => setDraftTone(event.target.value as typeof draftTone)} className="mt-2 h-10 w-full rounded-md border border-input bg-background px-3 text-xs"><option value="formal">Formal e institucional</option><option value="balanced">Profissional e acolhedora</option><option value="conversational">Próxima e conversacional</option></select></div>
            <div className="flex items-start gap-3 rounded-lg border border-[#0057d8]/25 bg-[#0057d8]/5 p-3"><WandSparkles className="mt-0.5 h-4 w-4 shrink-0 text-[#0057d8]" /><div><strong className="text-2xs">Configuração assistida</strong><p className="mt-1 text-2xs leading-4 text-muted-foreground">O sistema prepara taxonomia, documentos, funil, comunicação e gates. Sugestões nunca são publicadas sem confirmação humana.</p></div></div>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setDialogOpen(false)}>Cancelar</Button><Button onClick={createDraft} disabled={!draftName.trim() || createMutation.isPending} className="bg-[#031121] text-white hover:bg-[#031121]">{createMutation.isPending ? <Sparkles className="mr-2 h-4 w-4 animate-pulse" /> : <UserRoundCog className="mr-2 h-4 w-4" />}{createMutation.isPending ? 'Preparando' : dialogPurpose === 'duplicate' ? 'Criar cópia' : 'Criar estrutura'}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
