import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  Bot,
  Boxes,
  CircleAlert,
  CircleCheck,
  Database,
  Loader2,
  MessageSquareText,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { KlmPanel, KlmPill } from '@/klm/components/Primitives';
import {
  klmApi,
  type KlmNativeCapability,
} from '@/klm/api';
import { nativeDisplayText } from '@/klm/native-display';
import type { KlmNativeDomain } from '@/klm/native-domains';
import { cn } from '@/lib/utils';
import { useAppStore } from '@/store/app-context';

const domainUi: Record<KlmNativeDomain, {
  label: string;
  icon: typeof Boxes;
}> = {
  communications: { label: 'Comunicações', icon: MessageSquareText },
  collaboration: { label: 'Colaboração', icon: Boxes },
  automation: { label: 'Automação', icon: Bot },
  crm: { label: 'CRM jurídico', icon: Database },
};

function isBlocked(capability: KlmNativeCapability) {
  return capability.disposition.startsWith('blocked_') || capability.disposition === 'not_applicable';
}

export function NativeModuleBar({
  domain,
  capabilityIds,
  matterId,
  threadId,
  flowId,
  returnTo,
  hideSync = false,
  className,
  onNavigate,
}: {
  domain: KlmNativeDomain;
  capabilityIds?: string[];
  matterId?: string | null;
  threadId?: string | null;
  flowId?: string | null;
  surface: string;
  returnTo?: string;
  hideSync?: boolean;
  className?: string;
  onNavigate?: (path: string) => void;
}) {
  const ui = domainUi[domain];
  const Icon = ui.icon;
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const { can } = useAppStore();
  const canView = can('integrations', 'view');
  const isFlowMirror = domain === 'automation' && Boolean(flowId);
  const canManage = isFlowMirror
    ? can('automations', 'manage')
    : domain === 'automation'
      ? can('agents', 'manage')
    : can('integrations', 'manage');
  const canRefresh = isFlowMirror ? canManage : canView;
  const capabilityQuery = useQuery({
    queryKey: ['klm', 'capabilities'],
    queryFn: klmApi.capabilities,
    retry: false,
  });
  const capabilities = useMemo(() => {
    const domainCapabilities = (capabilityQuery.data?.capabilities ?? [])
      .filter((item) => item.domain === domain);
    if (!capabilityIds?.length) return domainCapabilities;
    const orderedIds = capabilityIds.map((id) => id.includes('.') ? id.split('.').at(-1)! : id);
    const order = new Map(orderedIds.map((id, index) => [id, index]));
    return domainCapabilities
      .filter((item) => order.has(item.key))
      .sort((left, right) => (order.get(left.key) ?? 0) - (order.get(right.key) ?? 0));
  }, [capabilityIds, capabilityQuery.data?.capabilities, domain]);
  const [selectedId, setSelectedId] = useState<string>('');
  const selected = capabilities.find((item) => item.id === selectedId) ?? capabilities[0];
  const [notice, setNotice] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const nativeCount = capabilities.filter((item) => item.exposure !== 'none').length;
  const verifiedCount = capabilities.filter((item) => item.verified).length;
  const blockedCount = capabilities.filter(isBlocked).length;
  const nativeOpenable = Boolean(selected?.nativeRoute && canView);
  const openInternal = (path: string) => onNavigate ? onNavigate(path) : navigate(path);
  const refreshLocal = async () => {
    if (refreshing) return;
    setRefreshing(true);
    setNotice(null);
    try {
      if (isFlowMirror && flowId) {
        const result = await klmApi.reconcileNativeFlow(flowId);
        setNotice(
          result.graphAction === 'unchanged'
            ? `A revisão ${result.revision} já estava íntegra no runtime nativo.`
            : `Revisão ${result.revision} reconciliada com ${result.nodeCount} etapas e ${result.connectionCount} conexões.`,
        );
      } else {
        await capabilityQuery.refetch();
        setNotice(`${ui.label} atualizado a partir da fonte canônica da plataforma.`);
      }
      await queryClient.invalidateQueries({ queryKey: ['klm'] });
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Não foi possível atualizar o módulo nativo.');
    } finally {
      setRefreshing(false);
    }
  };

  const openNative = () => {
    if (!selected || !nativeOpenable) return;
    const destination = new URL(selected.nativeRoute, 'http://klm.local');
    const params = destination.searchParams;
    if (matterId) params.set('matter', matterId);
    if (threadId) params.set('thread', threadId);
    if (flowId) params.set('flow', flowId);
    params.set('returnTo', returnTo ?? `${location.pathname}${location.search}`);
    openInternal(`${destination.pathname}?${params.toString()}${destination.hash}`);
  };

  const openMatrix = () => {
    const params = new URLSearchParams({ tab: 'coverage', domain });
    if (selected) params.set('capability', selected.id);
    openInternal(`/app/ferramentas?${params.toString()}`);
  };

  const loading = capabilityQuery.isPending;
  const failed = capabilityQuery.isError;
  const statusTone = failed ? 'gold' : loading ? 'gold' : 'green';

  return (
    <KlmPanel data-testid={`native-domain-bar-${domain}`} className={cn('overflow-hidden', className)}>
      <div className="grid gap-0 xl:grid-cols-[minmax(250px,.8fr)_minmax(320px,1.2fr)_auto] xl:items-stretch">
        <div className="flex min-w-0 items-center gap-3 border-b p-4 xl:border-b-0 xl:border-r">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center bg-[#031121] text-primary dark:border dark:border-white/10">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Icon className="h-4 w-4" />}
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-xs font-semibold">{ui.label}</p>
              <KlmPill tone={statusTone}>{failed ? 'atualizar' : loading ? 'verificando' : 'nativo'}</KlmPill>
            </div>
            <p className="mt-1 text-2xs leading-4 text-muted-foreground">
              {failed
                ? 'As configurações foram preservadas. Atualize para revisar os recursos deste workspace.'
                : `${capabilities.length} capacidades executadas na plataforma, sem dependência de aplicativo auxiliar.`}
            </p>
          </div>
        </div>

        <div className="min-w-0 border-b p-4 xl:border-b-0 xl:border-r">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <Select value={selected?.id ?? ''} onValueChange={setSelectedId} disabled={!capabilities.length}>
              <SelectTrigger
                className="h-9 min-w-0 flex-1 bg-secondary text-left text-2xs"
                aria-label={`Recurso de ${ui.label}`}
                title={selected ? `${nativeDisplayText(selected.label)} · operação nativa` : undefined}
              >
                <SelectValue placeholder={failed ? 'Recursos aguardando atualização' : 'Nenhum recurso catalogado'} />
              </SelectTrigger>
              <SelectContent>
                {capabilities.map((capability) => (
                  <SelectItem key={capability.id} value={capability.id} className="text-2xs">
                    {nativeDisplayText(capability.label)} · operação nativa
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="flex shrink-0 gap-1.5">
              <KlmPill tone={nativeCount === capabilities.length ? 'green' : 'gold'}>{nativeCount} disponíveis</KlmPill>
              <KlmPill tone="muted">{verifiedCount} verificadas</KlmPill>
              {blockedCount > 0 && <KlmPill tone="red">{blockedCount} bloqueado{blockedCount === 1 ? '' : 's'}</KlmPill>}
            </div>
          </div>
          {selected && (
            <div className="mt-2 flex items-start gap-2 text-2xs leading-4 text-muted-foreground">
              {isBlocked(selected)
                ? <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-600" />
                : selected.exposure === 'none'
                  ? <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
                  : <CircleCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-700" />}
              <span>
                <strong className="text-foreground">
                  {selected.verified ? 'Operação pronta:' : 'Operação disponível:'}
                </strong>{' '}
                {selected.verified
                  ? nativeDisplayText(selected.reason)
                  : 'A configuração local está preservada; revise permissões e parâmetros antes da primeira execução.'}
              </span>
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 p-4 xl:w-[300px] xl:justify-end">
          <Button variant="outline" size="sm" className="h-8 text-2xs" onClick={openMatrix} disabled={capabilityQuery.isPending}>
            <ShieldAlert className="mr-1.5 h-3 w-3" />Matriz
          </Button>
          {!hideSync && (
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-2xs"
              disabled={!canRefresh || refreshing}
              onClick={() => void refreshLocal()}
              title={!canRefresh
                ? `Permissão ${isFlowMirror ? 'automations.manage' : 'integrations.view'} necessária`
                : isFlowMirror
                  ? 'Reconciliar esta revisão no runtime nativo'
                  : 'Atualizar dados do módulo nativo'}
            >
              {refreshing ? <Loader2 className="mr-1.5 h-3 w-3 animate-spin" /> : <RefreshCw className="mr-1.5 h-3 w-3" />}
              {isFlowMirror ? 'Reconciliar fluxo' : 'Atualizar'}
            </Button>
          )}
          <Button
            size="sm"
            className="h-8 bg-[#031121] text-2xs text-white hover:bg-[#031121]"
            disabled={!nativeOpenable}
            onClick={openNative}
            title={!canView
              ? 'Permissão integrations.view necessária'
              : `Abrir ${nativeDisplayText(selected?.label) || 'recurso'} na rota nativa da plataforma`}
          >
            <Boxes className="mr-1.5 h-3 w-3" />Abrir recurso
          </Button>
        </div>
      </div>
      {notice && (
        <div className="flex items-center justify-between gap-3 border-t bg-secondary/70 px-4 py-2 text-2xs leading-4">
          <span>{notice}</span>
          <Button variant="ghost" size="sm" className="h-6 shrink-0 px-2 text-2xs" onClick={() => setNotice(null)}>Fechar</Button>
        </div>
      )}
    </KlmPanel>
  );
}
