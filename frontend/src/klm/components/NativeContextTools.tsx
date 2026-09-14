import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  ArrowRight,
  Bot,
  Boxes,
  Database,
  Loader2,
  MessageSquareText,
  Search,
  ShieldAlert,
  UsersRound,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { KlmPill } from '@/klm/components/Primitives';
import { klmApi, type KlmNativeCapability } from '@/klm/api';
import { nativeDisplayText } from '@/klm/native-display';
import type { KlmNativeDomain } from '@/klm/native-domains';

const domainUi: Record<KlmNativeDomain, { label: string; icon: typeof Boxes }> = {
  communications: { label: 'Comunicações', icon: MessageSquareText },
  collaboration: { label: 'Colaboração', icon: UsersRound },
  automation: { label: 'Automação', icon: Bot },
  crm: { label: 'CRM jurídico', icon: Database },
};

const exposureLabels: Record<KlmNativeCapability['exposure'], string> = {
  none: 'sem exposição',
  bridge: 'ponte nativa',
  native: 'nativa',
  microfrontend: 'incorporada',
  full_app: 'experiência integral',
};

const nativeModeLabels: Record<KlmNativeCapability['mode'], string> = {
  local_reconciled: 'sincronizada',
  local_command: 'comando local',
  local_canonical: 'fonte nativa',
  local_equivalent: 'equivalente nativo',
  controlled_configuration: 'simulação e gate',
  responsive_substitution: 'responsiva',
};

const blocked = (capability: KlmNativeCapability) => (
  capability.disposition.startsWith('blocked_') || capability.disposition === 'not_applicable'
);

function routePath(route: string | null) {
  return route?.split(/[?#]/, 1)[0] ?? null;
}

function preserveNativeContext(destination: string, currentSearch: string) {
  const url = new URL(destination, 'http://klm.local');
  const destinationKeys = new Set(url.searchParams.keys());
  new URLSearchParams(currentSearch).forEach((value, key) => {
    if (!destinationKeys.has(key)) url.searchParams.append(key, value);
  });
  const query = url.searchParams.toString();
  return `${url.pathname}${query ? `?${query}` : ''}${url.hash}`;
}

export function NativeContextTools() {
  const location = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const registryQuery = useQuery({
    queryKey: ['klm', 'capabilities'],
    queryFn: klmApi.capabilities,
    retry: false,
  });
  const currentPath = location.pathname;
  const relevant = useMemo(() => (registryQuery.data?.capabilities ?? []).filter((capability) => (
    capability.relatedRoutes.some((route) => routePath(route) === currentPath)
    || routePath(capability.nativeRoute) === currentPath
  )), [currentPath, registryQuery.data?.capabilities]);
  const visible = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase('pt-BR');
    if (!normalized) return relevant;
    return relevant.filter((capability) => (
      `${capability.label} ${capability.description} ${capability.category} ${domainUi[capability.domain].label}`
        .toLocaleLowerCase('pt-BR')
        .includes(normalized)
    ));
  }, [query, relevant]);
  const goToKlmSurface = (capability: KlmNativeCapability) => {
    setOpen(false);
    if (routePath(capability.nativeRoute) !== currentPath) {
      navigate(preserveNativeContext(capability.nativeRoute, location.search));
    }
  };

  return (
    <>
      <Button
        variant="outline"
        size="icon"
        className="relative h-9 w-9"
        onClick={() => setOpen(true)}
        title="Abrir recursos conectados desta tela"
        aria-label="Recursos conectados"
      >
        <Boxes className="h-4 w-4" />
        {relevant.length > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-2xs font-semibold text-black">
            {relevant.length}
          </span>
        )}
      </Button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="flex w-[94vw] flex-col overflow-hidden p-0 sm:max-w-[480px]">
          <SheetHeader className="border-b p-5 text-left">
            <div className="flex items-start justify-between gap-4 pr-7">
              <div>
                <SheetTitle>Recursos conectados</SheetTitle>
                <SheetDescription className="mt-1">
                  {relevant.length} recurso{relevant.length === 1 ? '' : 's'} relacionado{relevant.length === 1 ? '' : 's'} a esta tela.
                </SheetDescription>
              </div>
              <KlmPill tone="green">{relevant.length} nativos</KlmPill>
            </div>
            <div className="relative mt-3">
              <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Buscar recurso nesta tela"
                className="h-9 bg-secondary pl-9 text-2xs"
              />
            </div>
          </SheetHeader>

          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {registryQuery.isPending ? (
              <div className="flex min-h-40 items-center justify-center text-2xs text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />Carregando matriz funcional...
              </div>
            ) : registryQuery.isError ? (
              <div className="border border-red-500/30 bg-red-50 p-4 text-2xs text-red-800 dark:bg-red-950/20 dark:text-red-300">
                A matriz funcional não respondeu. As operações permanecem protegidas na plataforma.
              </div>
            ) : visible.length ? (
              <div className="space-y-2">
                {visible.map((capability) => {
                  const domain = domainUi[capability.domain];
                  const Icon = domain.icon;
                  const isBlocked = blocked(capability);
                  const destination = capability.nativeRoute;
                  const isCurrentSurface = routePath(destination) === currentPath;
                  return (
                    <article key={capability.id} className="rounded-lg border border-border bg-card p-3">
                      <div className="flex items-start gap-3">
                        <span className="flex h-8 w-8 shrink-0 items-center justify-center bg-[#031121] text-primary">
                          <Icon className="h-3.5 w-3.5" />
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <strong className="text-2xs">{nativeDisplayText(capability.label)}</strong>
                            <KlmPill tone={isBlocked ? 'gold' : 'green'}>
                              {nativeModeLabels[capability.mode]}
                            </KlmPill>
                          </div>
                          <p className="mt-1 text-2xs font-semibold uppercase text-muted-foreground">{domain.label} · {nativeDisplayText(capability.category)}</p>
                          <p className="mt-2 text-2xs leading-4 text-foreground/75">{nativeDisplayText(capability.description)}</p>
                          <p className="mt-1.5 text-2xs leading-4 text-muted-foreground">{nativeDisplayText(capability.reason)}</p>
                          <p className="mt-1 text-2xs text-muted-foreground/80">Disponibilidade: {exposureLabels[capability.exposure]}</p>
                        </div>
                      </div>
                      <div className="mt-3 flex items-center justify-between gap-2 border-t pt-3">
                        {isCurrentSurface ? (
                          <KlmPill tone="muted">nesta tela</KlmPill>
                        ) : (
                          <Button variant="outline" size="sm" className="h-7 text-2xs" aria-label="Abrir no Meu Executivo" disabled={!destination} onClick={() => goToKlmSurface(capability)}>
                            <span className="sr-only">Abrir no Meu Executivo</span>
                            <span aria-hidden="true">Abrir recurso</span><ArrowRight className="ml-1.5 h-3 w-3" />
                          </Button>
                        )}
                        {isBlocked ? (
                          <span className="flex items-center gap-1 text-2xs font-semibold text-amber-700 dark:text-amber-300"><ShieldAlert className="h-3 w-3" />efeito controlado</span>
                        ) : <KlmPill tone="green">operação local</KlmPill>}
                      </div>
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className="flex min-h-40 flex-col items-center justify-center border border-dashed border-border p-6 text-center">
                <Boxes className="h-5 w-5 text-muted-foreground" />
                <p className="mt-3 text-2xs font-semibold">Nenhum recurso encontrado</p>
                <p className="mt-1 text-2xs text-muted-foreground">Ajuste a busca ou consulte a matriz completa em Ferramentas.</p>
              </div>
            )}
          </div>

          <div className="border-t p-4">
            <Button variant="outline" className="w-full text-2xs" onClick={() => { setOpen(false); navigate(preserveNativeContext('/app/ferramentas?tab=coverage', location.search)); }}>
              <ShieldAlert className="mr-2 h-3.5 w-3.5" />Abrir matriz completa
            </Button>
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
