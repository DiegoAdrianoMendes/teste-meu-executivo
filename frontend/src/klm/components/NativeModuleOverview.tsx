import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Bot,
  Boxes,
  Database,
  Loader2,
  MessageSquareText,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { klmApi } from '@/klm/api';
import { KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { nativeDomainLabels } from '@/klm/native-display';
import { KLM_NATIVE_DOMAINS, type KlmNativeDomain } from '@/klm/native-domains';

const moduleUi: Record<KlmNativeDomain, { icon: typeof Boxes }> = {
  communications: { icon: MessageSquareText },
  collaboration: { icon: Boxes },
  automation: { icon: Bot },
  crm: { icon: Database },
};

export function NativeModuleOverview() {
  const navigate = useNavigate();
  const registryQuery = useQuery({
    queryKey: ['klm', 'capabilities'],
    queryFn: klmApi.capabilities,
  });
  const capabilities = useMemo(
    () => registryQuery.data?.capabilities ?? [],
    [registryQuery.data?.capabilities],
  );
  const modules = useMemo(() => (
    KLM_NATIVE_DOMAINS.map((domain) => {
      const rows = capabilities.filter((capability) => capability.domain === domain);
      const routes = rows.filter((row) => Boolean(row.nativeRoute)).length;
      const verified = rows.filter((row) => row.verified).length;
      const effects = rows.filter((row) => row.externalEffectVerified).length;
      const controlled = rows.filter((row) => row.disposition.startsWith('blocked_')).length;
      const canonical = rows.filter((row) => ['local_canonical', 'local_equivalent', 'local_reconciled', 'responsive_substitution'].includes(row.mode)).length;
      return {
        domain,
        rows,
        routes,
        verified,
        effects,
        controlled,
        canonical,
        complete: rows.length > 0 && routes === rows.length && verified === rows.length,
      };
    })
  ), [capabilities]);
  const nativeRouteTotal = modules.reduce((total, module) => total + module.routes, 0);
  const verifiedTotal = modules.reduce((total, module) => total + module.verified, 0);
  const effectTotal = modules.reduce((total, module) => total + module.effects, 0);
  const controlledTotal = modules.reduce((total, module) => total + module.controlled, 0);

  return (
    <section data-testid="native-module-overview" className="space-y-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <KlmSectionTitle
          title="Módulos nativos"
          description="Capacidades incorporadas ao núcleo nativo com contratos, rotas, isolamento e auditoria uniformes."
        />
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          <KlmPill className="text-xs tabular-nums" tone={modules.every((module) => module.complete) ? 'green' : 'gold'}>
            {modules.filter((module) => module.complete).length}/4 completos
          </KlmPill>
          <KlmPill className="text-xs tabular-nums" tone="black">{capabilities.length} capacidades</KlmPill>
          <KlmPill className="text-xs tabular-nums" tone={nativeRouteTotal === capabilities.length ? 'green' : 'red'}>{nativeRouteTotal} rotas nativas</KlmPill>
          <KlmPill className="text-xs tabular-nums" tone={verifiedTotal === capabilities.length ? 'green' : 'gold'}>{verifiedTotal} verificadas</KlmPill>
          <KlmPill className="text-xs tabular-nums" tone="muted">{effectTotal} efeitos externos verificados</KlmPill>
          <KlmPill className="text-xs tabular-nums" tone={controlledTotal ? 'gold' : 'green'}>{controlledTotal} efeitos controlados</KlmPill>
          <Button
            variant="outline"
            size="icon"
            className="h-7 w-7"
            onClick={() => void registryQuery.refetch()}
            title="Atualizar registro nativo"
          >
            <RefreshCw className={`h-3 w-3 ${registryQuery.isFetching ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {registryQuery.isPending ? (
        <div className="flex min-h-28 items-center justify-center border border-border text-xs text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />Verificando o registro canônico...
        </div>
      ) : registryQuery.isError ? (
        <div className="border border-red-500/30 bg-red-50 p-4 text-xs text-red-800 dark:bg-red-950/20 dark:text-red-300">
          Não foi possível atualizar o catálogo agora. Suas configurações foram preservadas; tente novamente.
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {modules.map((module) => {
            const Icon = moduleUi[module.domain].icon;
            return (
              <article key={module.domain} className="flex min-h-[178px] flex-col border border-border bg-card p-4 transition-colors hover:border-primary">
                <div className="flex items-start gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center bg-[#031121] text-primary"><Icon className="h-4 w-4" /></span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center justify-between gap-1.5">
                      <strong className="min-w-0 truncate text-sm font-semibold">{nativeDomainLabels[module.domain]}</strong>
                      <KlmPill className="shrink-0 text-xs" tone={module.complete ? 'green' : 'gold'}>{module.complete ? 'completo' : 'parcial'}</KlmPill>
                    </div>
                    <p className="mt-1 truncate text-2xs uppercase tracking-wide text-muted-foreground">núcleo nativo · escopo de workspace</p>
                  </div>
                </div>
                <div className="mt-4 grid grid-cols-2 gap-x-3 gap-y-2 text-2xs">
                  <span className="min-w-0 truncate text-muted-foreground">Catalogadas<strong className="block text-sm font-semibold tabular-nums text-foreground">{module.rows.length}</strong></span>
                  <span className="min-w-0 truncate text-muted-foreground">Rotas nativas<strong className="block text-sm font-semibold tabular-nums text-foreground">{module.routes}</strong></span>
                  <span className="min-w-0 truncate text-muted-foreground">Verificadas<strong className="block text-sm font-semibold tabular-nums text-foreground">{module.verified}</strong></span>
                  <span className="min-w-0 truncate text-muted-foreground">Efeitos externos<strong className="block text-sm font-semibold tabular-nums text-foreground">{module.effects}</strong></span>
                  <span className="min-w-0 truncate text-muted-foreground">Canônicas<strong className="block text-sm font-semibold tabular-nums text-foreground">{module.canonical}</strong></span>
                  <span className="min-w-0 truncate text-muted-foreground">Controladas<strong className="block text-sm font-semibold tabular-nums text-foreground">{module.controlled}</strong></span>
                </div>
                <div className="mt-auto border-t pt-3">
                  <Button variant="outline" size="sm" className="h-7 w-full text-xs" onClick={() => navigate(`/app/ferramentas?tab=coverage&domain=${module.domain}`)}>
                    <ShieldCheck className="mr-1.5 h-3 w-3" />Ver capacidades
                  </Button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      <div className="flex justify-end">
        <button
          type="button"
          className="text-xs text-muted-foreground underline-offset-2 hover:text-primary hover:underline"
          onClick={() => window.dispatchEvent(new CustomEvent('klm:aha:reopen'))}
        >
          Ver "Comece por aqui" novamente
        </button>
      </div>
    </section>
  );
}
