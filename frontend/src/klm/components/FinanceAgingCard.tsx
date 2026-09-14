import { Loader2 } from 'lucide-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { KlmMetricCard, KlmPanel, KlmSectionTitle } from '@/klm/components/Primitives';
import { useFinanceReports } from '@/klm/api/useFinanceReports';
import { formatBRL } from '@/lib/formatters';

/**
 * Card "Vencidos" com aging 1-15/16-30/31-60/61+ (spec §4.5, F6) — consome
 * GET /finance/reports/kpis via o hook useFinanceReports.
 */
export function FinanceAgingCard() {
  const kpisQuery = useFinanceReports();
  const kpis = kpisQuery.data;

  return (
    <KlmPanel className="p-4">
      <KlmSectionTitle
        title="Vencidos e resumo do período"
        description="Aging dos recebíveis em atraso e indicadores financeiros do período corrente."
      />
      <Tabs defaultValue="vencidos" className="mt-4">
        <TabsList className="h-9 bg-secondary p-1">
          <TabsTrigger value="vencidos" className="text-xs">Vencidos</TabsTrigger>
          <TabsTrigger value="resumo" className="text-xs">Resumo do período</TabsTrigger>
        </TabsList>

        <TabsContent value="vencidos" className="mt-4">
          {kpisQuery.isPending ? (
            <div className="flex min-h-24 items-center justify-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />Carregando aging de vencidos...
            </div>
          ) : kpisQuery.isError ? (
            <p className="text-xs text-muted-foreground">Não foi possível carregar o aging de vencidos.</p>
          ) : kpis ? (
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <KlmMetricCard label="1 a 15 dias" value={formatBRL(kpis.vencido.dias1A15)} delta="vencido" trend="neutral" />
              <KlmMetricCard label="16 a 30 dias" value={formatBRL(kpis.vencido.dias16A30)} delta="vencido" trend="neutral" />
              <KlmMetricCard label="31 a 60 dias" value={formatBRL(kpis.vencido.dias31A60)} delta="vencido" trend="down" />
              <KlmMetricCard label="61+ dias" value={formatBRL(kpis.vencido.dias61Mais)} delta="vencido" trend="down" />
            </div>
          ) : null}
        </TabsContent>

        <TabsContent value="resumo" className="mt-4">
          {kpisQuery.isPending ? (
            <div className="flex min-h-24 items-center justify-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />Carregando resumo do período...
            </div>
          ) : kpisQuery.isError ? (
            <p className="text-xs text-muted-foreground">Não foi possível carregar o resumo do período.</p>
          ) : kpis ? (
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <KlmMetricCard
                label="Recebido"
                value={formatBRL(kpis.recebido)}
                delta={`${new Date(kpis.periodStart).toLocaleDateString('pt-BR')} a ${new Date(kpis.periodEnd).toLocaleDateString('pt-BR')}`}
                trend="neutral"
              />
              <KlmMetricCard label="A receber" value={formatBRL(kpis.aReceber)} delta="saldo em aberto" trend="neutral" />
              <KlmMetricCard label="Ticket médio" value={formatBRL(kpis.ticketMedio)} delta="por lançamento" trend="neutral" />
              <KlmMetricCard
                label="Inadimplência"
                value={`${kpis.inadimplenciaPct.toFixed(1)}%`}
                delta="do período"
                trend={kpis.inadimplenciaPct > 0 ? 'down' : 'neutral'}
              />
            </div>
          ) : null}
        </TabsContent>
      </Tabs>
    </KlmPanel>
  );
}
