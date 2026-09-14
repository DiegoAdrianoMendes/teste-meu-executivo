import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowLeft,
  ArrowRight,
  Boxes,
  CheckCircle2,
  ChevronRight,
  CloudOff,
  Fingerprint,
  Layers3,
  Loader2,
  PlayCircle,
  Search,
  ShieldAlert,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { KlmMetricCard, KlmPanel, KlmPill, KlmProgress, KlmSectionTitle } from '@/klm/components/Primitives';
import {
  klmApi,
  type KlmFunctionUnitQuery,
  type KlmFunctionUnitState,
} from '@/klm/api';
import { nativeCapabilityLabel, nativeDomainLabels, nativeFunctionReference } from '@/klm/native-display';
import { isKlmNativeDomain, type KlmNativeDomain } from '@/klm/native-domains';

const stateLabels: Record<KlmFunctionUnitState, string> = {
  discovered: 'Descoberta',
  mapped: 'Mapeada',
  native_shadow: 'Em comparação',
  native_primary: 'Primária no Meu Executivo',
  native_verified: 'Verificada',
  blocked_proprietary: 'Bloqueada',
  blocked_license: 'Bloqueada',
};

function stateTone(state: KlmFunctionUnitState): 'green' | 'gold' | 'red' | 'blue' | 'muted' {
  if (state === 'native_verified') return 'green';
  if (state.startsWith('blocked_')) return 'red';
  if (state === 'native_primary') return 'blue';
  if (state === 'discovered') return 'muted';
  return 'gold';
}

function SelectFilter({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="min-w-0">
      <span className="mb-1 block text-2xs font-semibold uppercase text-muted-foreground">{label}</span>
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 w-full rounded-md border border-input bg-background px-3 text-xs outline-none focus:ring-2 focus:ring-ring"
      >
        <option value="">Todos</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {label === 'Domínio'
              ? nativeDomainLabels[option as KlmNativeDomain] ?? option
              : stateLabels[option as KlmFunctionUnitState] ?? option}
          </option>
        ))}
      </select>
    </label>
  );
}

function DetailValue({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div className="min-w-0 border-b border-border py-2 last:border-b-0">
      <p className="text-2xs font-semibold uppercase text-muted-foreground">{label}</p>
      <p className="mt-1 break-words text-2xs leading-4 text-foreground">{value ?? 'Não informado'}</p>
    </div>
  );
}

function FunctionUnitDetail({ unitId, onClose }: { unitId: string | null; onClose: () => void }) {
  const navigate = useNavigate();
  const query = useQuery({
    queryKey: ['klm', 'capability-function', unitId],
    queryFn: () => klmApi.functionUnit(unitId!),
    enabled: Boolean(unitId),
  });
  const unit = query.data;
  const runnable = Boolean(unit && !unit.disposition.startsWith('blocked_'));
  const runtimeQuery = useQuery({
    queryKey: ['klm', 'capability-function-runtime', unitId],
    queryFn: () => klmApi.functionUnitRuntime(unitId!),
    enabled: Boolean(unitId && runnable),
    retry: false,
  });
  const validationMutation = useMutation({
    mutationFn: () => klmApi.executeFunctionUnit(unitId!, {
      mode: 'dry-run',
      idempotencyKey: `function-validation:${crypto.randomUUID()}`,
      payload: {},
    }),
  });

  return (
    <Dialog open={Boolean(unitId)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="pr-8 text-base">Detalhes da função Meu Executivo</DialogTitle>
          <DialogDescription>Identidade pública, classificação e disponibilidade operacional.</DialogDescription>
        </DialogHeader>
        {query.isPending ? (
          <div className="flex min-h-52 items-center justify-center text-xs text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />Carregando função...
          </div>
        ) : query.isError || !unit ? (
          <div className="p-8 text-center text-xs text-muted-foreground">Não foi possível carregar esta função.</div>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <KlmPill tone="black">{nativeDomainLabels[unit.domain]}</KlmPill>
              <KlmPill tone={stateTone(unit.disposition)}>{stateLabels[unit.disposition]}</KlmPill>
              <KlmPill tone="muted">{unit.category}</KlmPill>
            </div>
            <div>
              <h3 className="text-sm font-semibold">{unit.name}</h3>
              <p className="mt-1 font-mono text-2xs text-muted-foreground">referência {nativeFunctionReference(unit.id)}</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <KlmPanel className="p-3">
                <p className="flex items-center gap-2 text-xs font-semibold"><Layers3 className="h-3.5 w-3.5 text-[#0057d8]" />Classificação nativa</p>
                <div className="mt-2">
                  <DetailValue label="Domínio" value={nativeDomainLabels[unit.domain]} />
                  <DetailValue label="Capacidade" value={nativeCapabilityLabel(unit.capability)} />
                  <DetailValue label="Maturidade" value={stateLabels[unit.maturity]} />
                </div>
              </KlmPanel>
              <KlmPanel className="p-3">
                <p className="text-xs font-semibold">Resumo</p>
                <p className="mt-3 text-2xs leading-5 text-muted-foreground">{unit.summary}</p>
              </KlmPanel>
            </div>
            {runnable && (
              <KlmPanel className="p-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <p className="text-xs font-semibold">Disponibilidade nativa</p>
                    <p className="mt-1 text-2xs text-muted-foreground">Validação local, isolada por workspace e sem efeito externo.</p>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-8 text-2xs"
                    disabled={runtimeQuery.isPending || runtimeQuery.isError || validationMutation.isPending}
                    onClick={() => validationMutation.mutate()}
                  >
                    {validationMutation.isPending
                      ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                      : <PlayCircle className="mr-1.5 h-3.5 w-3.5" />}
                    Validar localmente
                  </Button>
                </div>
                {runtimeQuery.isPending ? (
                  <div className="mt-4 flex items-center text-2xs text-muted-foreground"><Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />Consultando disponibilidade...</div>
                ) : runtimeQuery.isError || !runtimeQuery.data ? (
                  <p className="mt-4 text-2xs text-amber-700">Validação indisponível para esta função.</p>
                ) : (
                  <div className="mt-4 grid gap-x-5 sm:grid-cols-2">
                    <DetailValue label="Modos disponíveis" value={runtimeQuery.data.state.availableModes.join(', ')} />
                    <DetailValue label="Maturidade" value={stateLabels[runtimeQuery.data.state.maturity]} />
                    <DetailValue label="Eventos auditados" value={runtimeQuery.data.state.auditEventCount} />
                    <DetailValue label="Última atividade" value={runtimeQuery.data.state.lastActivityAt ? new Date(runtimeQuery.data.state.lastActivityAt).toLocaleString('pt-BR') : 'Sem atividade'} />
                  </div>
                )}
                {validationMutation.isSuccess && (
                  <p className="mt-3 text-2xs font-semibold text-emerald-700">{validationMutation.data.summary}</p>
                )}
                {validationMutation.isError && (
                  <p className="mt-3 text-2xs font-semibold text-red-700">{validationMutation.error instanceof Error ? validationMutation.error.message : 'A validação local falhou.'}</p>
                )}
              </KlmPanel>
            )}
            <div className="flex justify-end">
              <Button onClick={() => navigate(`/app/capacidades/${unit.domain}/${encodeURIComponent(unit.capability)}`)} className="h-9 text-2xs">
                Abrir capacidade Meu Executivo <ChevronRight className="ml-1.5 h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function FunctionUnitInventoryPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState(() => searchParams.get('search') ?? '');
  const deferredSearch = useDeferredValue(search.trim());
  const initialDomain = searchParams.get('domain') ?? '';
  const [domain, setDomain] = useState<'' | KlmNativeDomain>(() => isKlmNativeDomain(initialDomain) ? initialDomain : '');
  const [category, setCategory] = useState(() => searchParams.get('category') ?? '');
  const [disposition, setDisposition] = useState(() => searchParams.get('disposition') ?? '');
  const [capability, setCapability] = useState(() => searchParams.get('capability') ?? '');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const filters: KlmFunctionUnitQuery = useMemo(() => ({
    page,
    pageSize: 50,
    search: deferredSearch || undefined,
    domain: domain || undefined,
    category: category || undefined,
    disposition: (disposition || undefined) as KlmFunctionUnitState | undefined,
    capability: capability || undefined,
  }), [capability, category, deferredSearch, disposition, domain, page]);
  const query = useQuery({
    queryKey: ['klm', 'capability-functions', filters],
    queryFn: () => klmApi.functionUnits(filters),
  });

  useEffect(() => setPage(1), [capability, category, deferredSearch, disposition, domain]);

  useEffect(() => {
    const next = new URLSearchParams();
    if (search) next.set('search', search);
    if (domain) next.set('domain', domain);
    if (category) next.set('category', category);
    if (disposition) next.set('disposition', disposition);
    if (capability) next.set('capability', capability);
    setSearchParams(next, { replace: true });
  }, [capability, category, disposition, domain, search, setSearchParams]);

  const summary = query.data?.summary;
  const coverage = summary?.available ? ((summary.verified ?? 0) / summary.available) * 100 : 0;

  return (
    <div className="mx-auto max-w-[1380px] space-y-5">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <Button variant="ghost" size="sm" className="mb-2 h-7 px-0 text-2xs" onClick={() => navigate('/app/ferramentas?tab=coverage')}>
            <ArrowLeft className="mr-1.5 h-3.5 w-3.5" />Módulos e ferramentas
          </Button>
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Cobertura funcional</p>
          <h1 className="mt-1 text-2xl font-semibold">Inventário de funções</h1>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">Funções públicas organizadas por domínio, capacidade e maturidade operacional.</p>
        </div>
        <KlmPill tone="black"><Fingerprint className="mr-1 h-3 w-3" />escopo klm-capability-functions</KlmPill>
      </header>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KlmMetricCard label="Funções" value={(summary?.total ?? 0).toLocaleString('pt-BR')} delta="catálogo público" trend="neutral" />
        <KlmMetricCard label="Disponíveis" value={(summary?.available ?? 0).toLocaleString('pt-BR')} delta="operação nativa" trend="up" />
        <KlmMetricCard label="Verificadas" value={(summary?.verified ?? 0).toLocaleString('pt-BR')} delta={`${coverage.toLocaleString('pt-BR', { maximumFractionDigits: 1 })}% disponíveis`} trend={coverage === 100 ? 'up' : 'neutral'} />
        <KlmMetricCard label="Controladas" value={(summary?.blocked ?? 0).toLocaleString('pt-BR')} delta="efeito indisponível" trend="neutral" />
      </div>

      <KlmPanel className="p-4">
        <KlmSectionTitle
          title="Cobertura por domínio"
          description="A maturidade é promovida somente após validação funcional no núcleo nativo."
          action={<KlmPill tone={coverage === 100 ? 'green' : 'gold'}>{summary?.remaining ?? 0} pendentes</KlmPill>}
        />
        <div className="mt-4"><KlmProgress value={coverage} tone={coverage === 100 ? 'green' : 'gold'} /></div>
        <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {Object.entries(summary?.byDomain ?? {}).map(([domainId, count]) => (
            <button key={domainId} type="button" onClick={() => isKlmNativeDomain(domainId) && setDomain(domainId)} className="border border-border bg-secondary/60 p-3 text-left transition hover:border-primary">
              <span className="block text-2xs font-semibold">{isKlmNativeDomain(domainId) ? nativeDomainLabels[domainId] : domainId}</span>
              <span className="mt-1 block text-lg font-medium">{count}</span>
            </button>
          ))}
        </div>
      </KlmPanel>

      <KlmPanel className="p-4">
        {capability && (
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-border pb-3">
            <div><p className="text-2xs font-semibold uppercase text-muted-foreground">Capacidade selecionada</p><p className="mt-1 text-2xs">{nativeCapabilityLabel(capability)}</p></div>
            <Button variant="outline" size="sm" className="h-8 text-2xs" onClick={() => setCapability('')}>Ver inventário completo</Button>
          </div>
        )}
        <div className="grid gap-3 lg:grid-cols-[minmax(260px,1.5fr)_repeat(3,minmax(130px,1fr))]">
          <label className="min-w-0">
            <span className="mb-1 block text-2xs font-semibold uppercase text-muted-foreground">Buscar</span>
            <span className="relative block"><Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" /><Input aria-label="Buscar funções" value={search} onChange={(event) => setSearch(event.target.value)} className="h-9 pl-9 text-xs" placeholder="Nome, capacidade ou resumo" /></span>
          </label>
          <SelectFilter label="Domínio" value={domain} options={query.data?.filters.domains ?? []} onChange={(value) => setDomain(isKlmNativeDomain(value) ? value : '')} />
          <SelectFilter label="Categoria" value={category} options={query.data?.filters.categories ?? []} onChange={setCategory} />
          <SelectFilter label="Estado" value={disposition} options={query.data?.filters.dispositions ?? []} onChange={setDisposition} />
        </div>
      </KlmPanel>

      {query.isPending ? (
        <KlmPanel className="flex min-h-72 items-center justify-center text-xs text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />Consultando o catálogo...</KlmPanel>
      ) : query.isError ? (
        <KlmPanel className="p-10 text-center"><CloudOff className="mx-auto h-7 w-7 text-muted-foreground" /><p className="mt-3 text-xs font-semibold">O inventário não pôde ser carregado</p><Button variant="outline" size="sm" className="mt-4" onClick={() => void query.refetch()}>Tentar novamente</Button></KlmPanel>
      ) : (
        <KlmPanel className="overflow-hidden">
          <div className="flex items-center justify-between border-b border-border px-4 py-3"><p className="text-2xs font-semibold">{query.data.total.toLocaleString('pt-BR')} resultados</p><p className="text-2xs text-muted-foreground">Página {query.data.page} de {query.data.pages || 1}</p></div>
          <div className="hidden grid-cols-[minmax(220px,1.25fr)_130px_minmax(180px,.9fr)_130px_100px] gap-3 border-b border-border bg-secondary/70 px-4 py-2 text-2xs font-semibold uppercase text-muted-foreground lg:grid">
            <span>Função</span><span>Domínio</span><span>Capacidade</span><span>Categoria</span><span>Estado</span>
          </div>
          <div className="divide-y divide-border">
            {query.data.items.map((unit) => (
              <button key={unit.id} type="button" onClick={() => setSelectedId(unit.id)} className="grid w-full gap-3 px-4 py-3 text-left transition hover:bg-secondary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring lg:grid-cols-[minmax(220px,1.25fr)_130px_minmax(180px,.9fr)_130px_100px] lg:items-center">
                <span className="min-w-0"><span className="block truncate text-xs font-semibold">{unit.name}</span><span className="mt-1 block truncate font-mono text-2xs text-muted-foreground">{nativeFunctionReference(unit.id)}</span></span>
                <span className="text-2xs"><span className="mr-1 text-2xs uppercase text-muted-foreground lg:hidden">Domínio</span>{nativeDomainLabels[unit.domain]}</span>
                <span className="truncate text-2xs"><span className="mr-1 text-2xs uppercase text-muted-foreground lg:hidden">Capacidade</span>{nativeCapabilityLabel(unit.capability)}</span>
                <span className="truncate text-2xs text-muted-foreground"><span className="mr-1 text-2xs uppercase lg:hidden">Categoria</span>{unit.category}</span>
                <span className="flex items-center justify-between gap-2"><KlmPill tone={stateTone(unit.disposition)}>{stateLabels[unit.disposition]}</KlmPill><ChevronRight className="h-3.5 w-3.5 text-muted-foreground lg:hidden" /></span>
              </button>
            ))}
            {!query.data.items.length && <div className="p-10 text-center text-2xs text-muted-foreground">Nenhuma função corresponde aos filtros atuais.</div>}
          </div>
          <div className="flex items-center justify-between border-t border-border p-3">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} className="h-8 text-2xs"><ArrowLeft className="mr-1.5 h-3.5 w-3.5" />Anterior</Button>
            <div className="hidden items-center gap-2 text-2xs text-muted-foreground sm:flex"><Boxes className="h-3.5 w-3.5" />50 funções por página</div>
            <Button variant="outline" size="sm" disabled={page >= query.data.pages} onClick={() => setPage((value) => value + 1)} className="h-8 text-2xs">Próxima<ArrowRight className="ml-1.5 h-3.5 w-3.5" /></Button>
          </div>
        </KlmPanel>
      )}

      <KlmPanel className="flex items-start gap-3 p-4">
        {summary?.verified === summary?.available ? <CheckCircle2 className="mt-0.5 h-4 w-4 text-emerald-700" /> : <ShieldAlert className="mt-0.5 h-4 w-4 text-amber-700" />}
        <div><p className="text-2xs font-semibold">Leitura operacional</p><p className="mt-1 text-2xs leading-4 text-muted-foreground">A interface apresenta somente a identidade pública e o estado funcional necessário para operar cada capacidade.</p></div>
      </KlmPanel>

      <FunctionUnitDetail unitId={selectedId} onClose={() => setSelectedId(null)} />
    </div>
  );
}
