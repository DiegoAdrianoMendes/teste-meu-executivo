import { PlatformCommercialCatalogPanel } from '@/components/platform/PlatformCommercialCatalogPanel';
import { PlatformCommercialProviderPanel } from '@/components/platform/PlatformCommercialProviderPanel';
import { PlatformPlanPoliciesPanel } from '@/components/platform/PlatformPlanPoliciesPanel';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { canPlatform, isPlatformRoot } from '@/lib/platform-access';
import { useAppStore } from '@/store/app-context';

export default function BlackboxPlanStudio() {
  const { state } = useAppStore();
  const root = isPlatformRoot(state.currentUser);
  const canViewContracts = canPlatform(state.currentUser, 'commercial_contracts', 'view');
  const canDraft = root || canPlatform(state.currentUser, 'plans', 'draft');
  const canPreview = root || canPlatform(state.currentUser, 'plans', 'preview');

  return (
    <div className="mx-auto w-full max-w-[1600px] space-y-5 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Estúdio de Planos</h1>
        <p className="mt-1 max-w-4xl text-sm text-muted-foreground">
          Ofertas, preços, contratos, módulos e limites quantitativos numa única operação versionada.
        </p>
      </div>
      <Tabs defaultValue="catalog">
        <TabsList className="h-auto flex-wrap justify-start">
          <TabsTrigger value="catalog">Ofertas e contratos</TabsTrigger>
          {canViewContracts && <TabsTrigger value="provider">Provedores de cobrança</TabsTrigger>}
          <TabsTrigger value="policies">Legado (somente leitura)</TabsTrigger>
        </TabsList>
        <TabsContent value="catalog" className="mt-5">
          <PlatformCommercialCatalogPanel
            isPlatformRoot={root}
            canViewContracts={canViewContracts}
            canDraft={canDraft}
            canPreview={canPreview}
            canRestore={root}
          />
        </TabsContent>
        {canViewContracts && <TabsContent value="provider" className="mt-5">
          <PlatformCommercialProviderPanel isPlatformRoot={root} />
        </TabsContent>}
        <TabsContent value="policies" className="mt-5">
          <div className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 text-sm">
            <p className="font-medium">Compatibilidade histórica</p>
            <p className="mt-1 text-muted-foreground">
              Esta política antiga permanece disponível apenas para consulta durante a
              migração. Novos preços, limites, contratos e upgrades são publicados em
              Ofertas e contratos.
            </p>
          </div>
          <PlatformPlanPoliciesPanel canManage={false} canPublish={false} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
