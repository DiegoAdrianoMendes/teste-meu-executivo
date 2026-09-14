import { CalendarClock, Users } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/hooks/use-toast';
import type { KlmDistributionStrategy } from '@/klm/api';
import { KlmMetricCard, KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import {
  DeadlineMemoryDialog,
  DeadlinePublicationRow,
  ProcessMonitoringModuleGate,
  useDeadlineMemoryDialog,
} from '@/klm/components/ProcessMonitoringShared';
import {
  isModuleNotContractedError,
  useConfirmProcessDeadline,
  useDismissProcessDeadline,
  useProcessDeadlines,
} from '@/klm/api/useProcessos';
import {
  isControladoriaAccessDeniedError,
  useControladoriaPainel,
  useDistributionRules,
} from '@/klm/api/useLegalControladoria';
import { useAppStore } from '@/store/app-context';
import { ApiError } from '@/services/http';

/** Rótulos PT-BR das estratégias de distribuição — espelha
 * `app.models.legal_controladoria.DistributionStrategy` (round_robin /
 * least_load / seniority / fixed). */
const DISTRIBUTION_STRATEGY_LABEL: Record<KlmDistributionStrategy, string> = {
  round_robin: 'Rodízio',
  least_load: 'Menor carga',
  seniority: 'Antiguidade (ordem de cadastro)',
  fixed: 'Ordem fixa de cadastro',
};

const MODULE_GATE_DESCRIPTION =
  'A agenda de prazos faz parte do módulo de monitoramento processual — um módulo adicional. ' +
  'Ative o monitoramento de processos (DJEN/DataJud) para ter prazos calculados automaticamente, ' +
  'com memória de cálculo auditável e confirmação humana antes de qualquer prazo virar fatal.';

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail as { message?: string } | undefined;
    return detail?.message ?? error.message;
  }
  return error instanceof Error ? error.message : 'Tente novamente.';
}

/** Painel diário da controladoria jurídica (11 categorias reais de
 * `GET /legal/controladoria/painel`) — fundido com a agenda de prazos
 * nesta mesma tela (Sprint B UI, decisão do dono: Publicações e
 * Controladoria são a mesma coisa). */
function ControladoriaPanelSection() {
  const painelQuery = useControladoriaPainel();
  const accessDenied = isControladoriaAccessDeniedError(painelQuery.error);

  if (accessDenied) return null; // sem permissão jurídica: a tela ainda mostra a agenda de prazos abaixo.

  const painel = painelQuery.data;
  const loading = painelQuery.isPending;
  const janela = painel?.prazosPorJanela;

  return (
    <KlmPanel>
      <div className="p-4">
        <KlmSectionTitle
          title="Painel diário da controladoria"
          description={painel ? `Gerado às ${new Date(painel.generatedAt).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })} — publicações, prazos, tarefas e exceções de todo o workspace.` : 'Carregando o resumo agregado do dia.'}
          action={painelQuery.isError && !accessDenied ? (
            <KlmPill tone="red">Painel indisponível</KlmPill>
          ) : undefined}
        />
      </div>
      <div className="grid grid-cols-2 gap-2 border-t border-border p-4 sm:grid-cols-3 xl:grid-cols-6">
        <KlmMetricCard label="Publicações não tratadas" value={String(painel?.publicacoesNaoTratadas ?? 0)} delta="Aguardando triagem" loading={loading} />
        <KlmMetricCard label="Prazos sugeridos" value={String(painel?.prazosSugeridos ?? 0)} delta="Aguardando confirmação" loading={loading} />
        <KlmMetricCard label="Prazos fatais" value={String(painel?.prazosFatais ?? 0)} delta="Confirmados, inviolável" trend={painel && painel.prazosFatais > 0 ? 'down' : 'neutral'} loading={loading} />
        <KlmMetricCard label="Tarefas sem responsável" value={String(painel?.tarefasSemResponsavel ?? 0)} delta="Sem distribuição" loading={loading} />
        <KlmMetricCard label="Processos sem movimentação" value={String(painel?.processosSemMovimentacao ?? 0)} delta="Monitoramento parado" loading={loading} />
        <KlmMetricCard label="Audiências próximas" value={String(painel?.audienciasProximas ?? 0)} delta="Próximos dias" loading={loading} />
        <KlmMetricCard label="Documentos pendentes" value={String(painel?.documentosPendentes ?? 0)} delta="Aguardando anexo" loading={loading} />
        <KlmMetricCard label="Minutas bloqueadas" value={String(painel?.minutasBloqueadas ?? 0)} delta="Geração travada" loading={loading} />
        <KlmMetricCard label="Exceções de agentes" value={String(painel?.excecoesDeAgentes ?? 0)} delta="Precisam de humano" loading={loading} />
        <KlmMetricCard label="Clientes sem atualização" value={String(painel?.clientesSemAtualizacao ?? 0)} delta="SLA de contato" loading={loading} />
      </div>
      <div className="border-t border-border p-4">
        <div className="mb-2 flex items-center gap-2"><CalendarClock className="h-3.5 w-3.5 text-muted-foreground" /><p className="text-2xs font-semibold uppercase text-muted-foreground">SLA — prazos por janela</p></div>
        <div className="grid grid-cols-5 gap-px overflow-hidden rounded-md border border-border bg-border text-center">
          {([
            ['D-7', janela?.d7, 'muted'],
            ['D-3', janela?.d3, 'gold'],
            ['D-1', janela?.d1, 'gold'],
            ['D-0', janela?.d0, 'red'],
            ['Atrasados', janela?.atrasados, 'red'],
          ] as const).map(([label, value]) => (
            <div key={label} className="bg-card px-2 py-3">
              <strong className="block text-base font-semibold tabular-nums">{loading ? '—' : (value ?? 0)}</strong>
              <span className="mt-0.5 block text-2xs text-muted-foreground">{label}</span>
            </div>
          ))}
        </div>
      </div>
    </KlmPanel>
  );
}

/** Resumo de distribuição de trabalho jurídico — regras ativas do
 * workspace (`GET /legal/controladoria/distribution-rules`). */
function DistributionSummarySection() {
  const rulesQuery = useDistributionRules();
  if (isControladoriaAccessDeniedError(rulesQuery.error)) return null;

  const rules = rulesQuery.data?.items ?? [];
  const activeCount = rules.filter((rule) => rule.active).length;

  return (
    <KlmPanel className="overflow-hidden">
      <KlmSectionTitle
        title="Distribuição de trabalho jurídico"
        description="Regras de distribuição do workspace — quem recebe demandas novas automaticamente."
        action={<KlmPill tone={activeCount > 0 ? 'green' : 'muted'}><Users className="mr-1 h-3 w-3" />{activeCount} ativa(s)</KlmPill>}
      />
      <div className="mt-3 border-t border-border pt-3">
        {rulesQuery.isPending ? (
          <div className="grid gap-2 sm:grid-cols-2">{Array.from({ length: 2 }).map((_, index) => <Skeleton key={index} className="h-14 rounded-lg" />)}</div>
        ) : rules.length === 0 ? (
          <p className="text-center text-xs text-muted-foreground">Nenhuma regra de distribuição cadastrada ainda — a atribuição de demandas segue manual.</p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {rules.map((rule) => (
              <div key={rule.id} className="flex items-center justify-between gap-3 rounded-md border border-border p-3">
                <div className="min-w-0">
                  <p className="truncate text-xs font-semibold text-foreground">{rule.name}</p>
                  <p className="mt-0.5 truncate text-2xs text-muted-foreground">{DISTRIBUTION_STRATEGY_LABEL[rule.strategy]} · prioridade {rule.priority}</p>
                </div>
                <KlmPill tone={rule.active ? 'green' : 'muted'}>{rule.active ? 'Ativa' : 'Inativa'}</KlmPill>
              </div>
            ))}
          </div>
        )}
      </div>
    </KlmPanel>
  );
}

export default function PrazosPage() {
  const { toast } = useToast();
  const { can } = useAppStore();
  const canManage = can('processos', 'manage');

  const pendingQuery = useProcessDeadlines('prazo_sugerido');
  const confirmedQuery = useProcessDeadlines('confirmado');
  const confirmMutation = useConfirmProcessDeadline();
  const dismissMutation = useDismissProcessDeadline();
  const memoryDialog = useDeadlineMemoryDialog();

  const moduleNotContracted = isModuleNotContractedError(pendingQuery.error) || isModuleNotContractedError(confirmedQuery.error);
  const isPending = pendingQuery.isPending || confirmedQuery.isPending;

  return (
    <div className="space-y-4 p-4">
      <KlmSectionTitle
        title="Publicações e Controladoria"
        description="Painel diário da controladoria jurídica, publicações e intimações do módulo processual, e a agenda de prazos — sugeridos aguardando confirmação humana e confirmados ordenados pela data fatal."
      />

      <ControladoriaPanelSection />

      {isPending && (
        <div className="grid gap-3">{Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-16 rounded-lg" />)}</div>
      )}

      {!isPending && moduleNotContracted && (
        <ProcessMonitoringModuleGate description={MODULE_GATE_DESCRIPTION} />
      )}

      {!isPending && !moduleNotContracted && (pendingQuery.isError || confirmedQuery.isError) && (
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          Não foi possível consultar a agenda de prazos agora. Tente novamente em instantes.
        </p>
      )}

      {!isPending && !moduleNotContracted && pendingQuery.isSuccess && confirmedQuery.isSuccess && (
        <div className="space-y-4">
          <KlmPanel>
            <KlmSectionTitle
              title="Publicações e intimações — aguardando confirmação"
              description="Prazos sugeridos pelo cálculo determinístico — nunca viram compromisso oficial sem confirmação humana."
              action={<KlmPill tone={pendingQuery.data.length > 0 ? 'gold' : 'muted'}>{pendingQuery.data.length}</KlmPill>}
            />
            <div className="mt-2 border-t">
              {pendingQuery.data.length === 0 && (
                <p className="p-6 text-center text-xs text-muted-foreground">Nenhum prazo sugerido aguardando confirmação.</p>
              )}
              {pendingQuery.data.map((publication) => (
                <DeadlinePublicationRow
                  key={publication.id}
                  publication={publication}
                  canManage={canManage}
                  onViewMemory={() => memoryDialog.show(publication)}
                  onConfirm={() => confirmMutation.mutate(publication.id, {
                    onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível confirmar o prazo', description: errorMessage(error) }),
                  })}
                  onDismiss={() => dismissMutation.mutate(publication.id, {
                    onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível dispensar o prazo', description: errorMessage(error) }),
                  })}
                  confirmPending={confirmMutation.isPending}
                  dismissPending={dismissMutation.isPending}
                />
              ))}
            </div>
          </KlmPanel>

          <KlmPanel>
            <KlmSectionTitle
              title="Confirmados"
              description="Prazos já confirmados por um humano — ordenados pela data fatal mais próxima primeiro."
              action={<KlmPill tone="blue"><CalendarClock className="mr-1 h-3 w-3" />{confirmedQuery.data.length}</KlmPill>}
            />
            <div className="mt-2 border-t">
              {confirmedQuery.data.length === 0 && (
                <p className="p-6 text-center text-xs text-muted-foreground">Nenhum prazo confirmado ainda.</p>
              )}
              {confirmedQuery.data.map((publication) => (
                <DeadlinePublicationRow
                  key={publication.id}
                  publication={publication}
                  canManage={canManage}
                  onViewMemory={() => memoryDialog.show(publication)}
                />
              ))}
            </div>
          </KlmPanel>
        </div>
      )}

      <DistributionSummarySection />

      <DeadlineMemoryDialog publication={memoryDialog.publication} open={memoryDialog.open} onOpenChange={memoryDialog.onOpenChange} />
    </div>
  );
}
