import { Loader2, ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/hooks/use-toast';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import {
  useApproveAllLegalBatchItems,
  useApproveLegalBatchItem,
  useLegalBatchItems,
  useLegalBatchPanel,
  useProcessLegalBatch,
} from '@/klm/api/useLegalBatches';
import {
  KLM_LEGAL_BATCH_PROTECTED_DECISION_KINDS,
  type KlmLegalBatchDecisionKind,
  type KlmLegalBatchItem,
  type KlmLegalBatchItemState,
} from '@/klm/api';
import { ApiError } from '@/services/http';

/**
 * Painel reusável de um lote (Onda 8, Sprint B UI — "lotes por cliente").
 * Consumido pelo botão "Processar em lote" tanto na Demanda
 * (`DemandaDetailPage`) quanto no Cliente (`LeadPage`) — sempre o mesmo
 * componente, o que muda é só o `batchId` recebido (criado por contexto
 * antes de abrir este painel).
 *
 * REGRA DURA (Plano Mestre §7): "Aprovar itens elegíveis" NUNCA cobre
 * tese/prazo fatal/texto final/protocolo — o backend sempre devolve esses
 * itens pulados; este painel mostra os pulados explicitamente, nunca
 * finge 100% de aprovação. Os contadores vêm sempre do painel real
 * (`GET /legal/batches/{id}/panel`), nunca de um contador incrementado à
 * parte no cliente.
 */

const DECISION_KIND_LABELS: Record<KlmLegalBatchDecisionKind, string> = {
  classificacao: 'Classificação',
  agrupamento: 'Agrupamento',
  vinculo: 'Vínculo',
  tarefa_sugerida: 'Tarefa sugerida',
  variavel_preenchida: 'Variável preenchida',
  rascunho: 'Rascunho',
  revisao: 'Revisão',
  documento_solicitado: 'Documento solicitado',
  organizacao: 'Organização',
  tese: 'Tese',
  prazo_fatal: 'Prazo fatal',
  texto_final: 'Texto final',
  protocolo: 'Protocolo',
};

const ITEM_STATE_LABELS: Record<KlmLegalBatchItemState, string> = {
  pendente: 'Pendente',
  classificado: 'Classificado',
  vinculado: 'Vinculado',
  rascunho: 'Rascunho',
  revisado: 'Revisado',
  bloqueado: 'Bloqueado',
  baixa_confianca: 'Baixa confiança',
  divergente: 'Divergente',
  aguardando_humano: 'Aguardando humano',
  concluido: 'Concluído',
};

const ITEM_STATE_TONE: Record<KlmLegalBatchItemState, 'green' | 'gold' | 'red' | 'muted' | 'blue'> = {
  pendente: 'muted',
  classificado: 'blue',
  vinculado: 'blue',
  rascunho: 'blue',
  revisado: 'blue',
  bloqueado: 'red',
  baixa_confianca: 'red',
  divergente: 'red',
  aguardando_humano: 'gold',
  concluido: 'green',
};

// Espelha `_APPROVABLE_STATES` em `app/services/legal_batch_service.py` —
// só para desabilitar o botão sem uma volta ao servidor; o backend
// permanece a fonte de verdade (409 se o estado não permitir).
const APPROVABLE_STATES: readonly KlmLegalBatchItemState[] = [
  'aguardando_humano',
  'classificado',
  'vinculado',
  'rascunho',
  'revisado',
];

function isProtectedDecision(decisionKind: KlmLegalBatchDecisionKind): boolean {
  return (KLM_LEGAL_BATCH_PROTECTED_DECISION_KINDS as readonly string[]).includes(decisionKind);
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail as { message?: string } | undefined;
    return detail?.message ?? error.message;
  }
  return error instanceof Error ? error.message : 'Tente novamente.';
}

function CountTile({ label, value, tone }: { label: string; value: number; tone?: 'red' | 'gold' | 'green' }) {
  return (
    <div className="rounded-lg border border-border bg-secondary/40 px-3 py-2 text-center">
      <p className={
        tone === 'red' ? 'text-lg font-semibold tabular-nums text-[#a53e3c] dark:text-red-400'
        : tone === 'gold' ? 'text-lg font-semibold tabular-nums text-[#8a6400] dark:text-amber-400'
        : tone === 'green' ? 'text-lg font-semibold tabular-nums text-[#0b7435] dark:text-emerald-400'
        : 'text-lg font-semibold tabular-nums text-foreground'
      }>{value}</p>
      <p className="text-2xs text-muted-foreground">{label}</p>
    </div>
  );
}

function ItemRow({
  item,
  canManage,
  onApprove,
  approving,
}: {
  item: KlmLegalBatchItem;
  canManage: boolean;
  onApprove: (itemId: string) => void;
  approving: boolean;
}) {
  const approvable = APPROVABLE_STATES.includes(item.state);
  const protectedDecision = isProtectedDecision(item.decisionKind);
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-border bg-card px-3 py-2 text-xs">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <KlmPill tone={ITEM_STATE_TONE[item.state]}>{ITEM_STATE_LABELS[item.state]}</KlmPill>
          <KlmPill tone={protectedDecision ? 'red' : 'muted'}>{DECISION_KIND_LABELS[item.decisionKind]}</KlmPill>
          {protectedDecision && <span className="text-2xs text-muted-foreground">só aprovação individual</span>}
        </div>
        {item.errorReason && <p className="mt-1 text-2xs text-destructive">{item.errorReason}</p>}
        {item.confidence != null && <p className="mt-1 text-2xs text-muted-foreground">Confiança: {Math.round(item.confidence * 100)}%</p>}
      </div>
      {approvable && (
        <Button
          size="sm"
          variant="outline"
          className="h-7 shrink-0 text-2xs"
          disabled={!canManage || approving}
          title={!canManage ? 'Permissão legal.manage necessária' : undefined}
          onClick={() => onApprove(item.id)}
        >
          {approving && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}Aprovar
        </Button>
      )}
    </div>
  );
}

export function LegalBatchPanel({ batchId, canManage }: { batchId: string; canManage: boolean }) {
  const { toast } = useToast();
  const panelQuery = useLegalBatchPanel(batchId);
  const itemsQuery = useLegalBatchItems(batchId);
  const processBatch = useProcessLegalBatch(batchId);
  const approveItem = useApproveLegalBatchItem(batchId);
  const approveAll = useApproveAllLegalBatchItems(batchId);

  const panel = panelQuery.data;
  const items = itemsQuery.data ?? [];
  const attentionItems = items.filter((item) => item.state !== 'concluido' && item.state !== 'pendente');

  const handleProcess = () => {
    processBatch.mutate(undefined, {
      onSuccess: (result) => {
        toast({
          title: `Lote processado — ${result.succeededItemIds.length} item(ns) avançaram`,
          description: result.failedItemIds.length > 0
            ? `${result.failedItemIds.length} item(ns) ficaram bloqueados — veja a lista abaixo.`
            : undefined,
          variant: result.failedItemIds.length > 0 ? 'destructive' : undefined,
        });
      },
      onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível processar o lote', description: errorMessage(error) }),
    });
  };

  const handleApproveAll = () => {
    approveAll.mutate(undefined, {
      onSuccess: (result) => {
        const skippedProtected = result.skippedProtectedItemIds.length;
        toast({
          title: `${result.approvedItemIds.length} item(ns) aprovado(s)`,
          description: skippedProtected > 0
            ? `${skippedProtected} item(ns) de tese/prazo fatal/texto final/protocolo NUNCA são aprovados em lote — aprove-os individualmente abaixo.`
            : (result.skippedNotPendingItemIds.length > 0
              ? `${result.skippedNotPendingItemIds.length} item(ns) não estavam num estado aprovável.`
              : undefined),
        });
      },
      onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível aprovar os itens', description: errorMessage(error) }),
    });
  };

  const handleApproveItem = (itemId: string) => {
    approveItem.mutate(itemId, {
      onSuccess: () => toast({ title: 'Item aprovado' }),
      onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível aprovar o item', description: errorMessage(error) }),
    });
  };

  if (panelQuery.isPending) {
    return <div className="grid gap-3" data-testid="legal-batch-panel-loading">{Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-16 rounded-lg" />)}</div>;
  }

  if (panelQuery.isError || !panel) {
    return (
      <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
        Não foi possível carregar o painel deste lote agora. Tente novamente em instantes.
      </p>
    );
  }

  return (
    <div className="space-y-4" data-testid="legal-batch-panel">
      <KlmPanel className="p-4">
        <KlmSectionTitle
          title="Painel do lote"
          description="Contagem sempre real dos itens — nunca um agregado escondendo falha parcial."
          action={
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={!canManage || processBatch.isPending || panel.pendente === 0}
                title={!canManage ? 'Permissão legal.manage necessária' : undefined}
                onClick={handleProcess}
              >
                {processBatch.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                Processar lote {panel.pendente > 0 ? `(${panel.pendente} pendente${panel.pendente === 1 ? '' : 's'})` : ''}
              </Button>
              <Button
                size="sm"
                disabled={!canManage || approveAll.isPending}
                title={!canManage ? 'Permissão legal.manage necessária' : 'Nunca aprova tese, prazo fatal, texto final ou protocolo'}
                onClick={handleApproveAll}
              >
                {approveAll.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                Aprovar itens elegíveis
              </Button>
            </div>
          }
        />

        <div className="mt-3 rounded-lg border border-amber-600/25 bg-amber-500/[0.08] px-3 py-2 text-2xs leading-4 text-[#8a6400] dark:text-amber-300">
          <ShieldAlert className="mr-1 inline-block h-3.5 w-3.5 align-text-bottom" />
          Itens de tese, prazo fatal, texto final ou protocolo nunca são aprovados em lote — sempre item a item, sempre humano.
        </div>

        <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-5">
          <CountTile label="Total" value={panel.totalItems} />
          <CountTile label="Concluídos" value={panel.concluido} tone="green" />
          <CountTile label="Aguardando humano" value={panel.aguardandoHumano} tone="gold" />
          <CountTile label="Bloqueados" value={panel.bloqueado} tone="red" />
          <CountTile label="Divergentes" value={panel.divergente} tone="red" />
        </div>
        <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-5">
          <CountTile label="Pendentes" value={panel.pendente} />
          <CountTile label="Classificados" value={panel.classificado} />
          <CountTile label="Vinculados" value={panel.vinculado} />
          <CountTile label="Rascunhos" value={panel.rascunho} />
          <CountTile label="Baixa confiança" value={panel.baixaConfianca} tone="gold" />
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2 text-2xs text-muted-foreground">
          <span>Custo estimado: <span className="tabular-nums text-foreground">{panel.estimatedCostTotal.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}</span></span>
          <span>Tempo economizado (estimado): <span className="tabular-nums text-foreground">{panel.estimatedTimeSavedMinutesTotal} min</span></span>
        </div>
      </KlmPanel>

      <KlmPanel className="p-4">
        <KlmSectionTitle title="Itens que precisam de atenção" description={`${attentionItems.length} de ${items.length} item(ns) do lote`} />
        {itemsQuery.isPending && <div className="mt-3 grid gap-2">{Array.from({ length: 2 }).map((_, index) => <Skeleton key={index} className="h-12 rounded-lg" />)}</div>}
        {itemsQuery.isSuccess && attentionItems.length === 0 && (
          <p className="mt-3 text-xs text-muted-foreground">Nenhum item pendente de atenção — tudo pendente ou já concluído.</p>
        )}
        <div className="mt-3 space-y-2">
          {attentionItems.map((item) => (
            <ItemRow
              key={item.id}
              item={item}
              canManage={canManage}
              onApprove={handleApproveItem}
              approving={approveItem.isPending && approveItem.variables === item.id}
            />
          ))}
        </div>
      </KlmPanel>
    </div>
  );
}
