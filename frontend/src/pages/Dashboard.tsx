import { SectionHeader } from '@/components/common/SectionHeader';
import { KPIStatCard } from '@/components/common/KPIStatCard';
import { MatterCard } from '@/components/cards/MatterCard';
import { useAppStore } from '@/store/app-context';
import { Badge } from '@/components/ui/badge';
import { useQuery } from '@tanstack/react-query';
import { RadialBarChart, RadialBar, ResponsiveContainer, Tooltip } from 'recharts';
import { TrendingUp, DollarSign, CheckCircle2, Users } from 'lucide-react';
import { apiFetch } from '@/services/http';
import { formatBRL, formatDecimal } from '@/lib/formatters';

interface LiveMetrics {
  proposals_accepted_rate: number;
  conversion_rate: number;
  leads_this_week: number;
  pipeline_value: number;
  top_column_counts: Record<string, number>;
  avg_response_time_minutes?: number;
}

async function fetchLiveMetrics(): Promise<LiveMetrics> {
  return apiFetch<LiveMetrics>('/api/v1/reports/metrics/live');
}

function ProposalsGauge({ rate }: { rate: number }) {
  const data = [{ name: 'taxa', value: rate, fill: 'hsl(var(--primary))' }];
  return (
    <div className="bg-card border border-border rounded-xl p-4 flex flex-col items-center justify-center gap-1">
      <p className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">Propostas Aceitas</p>
      <div className="w-24 h-24">
        <ResponsiveContainer width="100%" height="100%">
          <RadialBarChart cx="50%" cy="50%" innerRadius="65%" outerRadius="90%" data={data} startAngle={90} endAngle={-270}>
            <RadialBar dataKey="value" cornerRadius={4} background={{ fill: 'hsl(var(--muted))' }} />
          </RadialBarChart>
        </ResponsiveContainer>
      </div>
      <p className="text-2xl font-bold -mt-12 relative z-10">{formatDecimal(rate)}%</p>
      <div className="h-4" />
      <div className="flex items-center gap-1 text-green-500 text-[11px] font-medium mt-1">
        <TrendingUp className="h-3 w-3" />
        Ótimo resultado
      </div>
    </div>
  );
}

function PipelineCard({ value }: { value: number }) {
  return (
    <div className="bg-card border border-border rounded-xl p-4 flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-lg bg-emerald-500/10 flex items-center justify-center">
          <DollarSign className="h-4 w-4 text-emerald-500" />
        </div>
        <p className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">Pipeline</p>
      </div>
      <p className="text-2xl font-bold">{formatBRL(value)}</p>
      <p className="text-[11px] text-muted-foreground">Valor em negociação</p>
    </div>
  );
}

function ConversionCard({ rate, leads }: { rate: number; leads: number }) {
  return (
    <div className="bg-card border border-border rounded-xl p-4 flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center">
          <Users className="h-4 w-4 text-blue-500" />
        </div>
        <p className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">Leads esta semana</p>
      </div>
      <p className="text-2xl font-bold">{leads}</p>
      <div className="flex items-center gap-1 text-[11px]">
        <span className="text-muted-foreground">Conversão:</span>
        <span className="font-semibold text-primary">{formatDecimal(rate)}%</span>
      </div>
    </div>
  );
}

export default function Dashboard() {
  const { state } = useAppStore();
  const topMetrics = state.metrics.slice(0, 4);
  const recentCards = [...state.cards]
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
    .slice(0, 4);

  const { data: live } = useQuery({
    queryKey: ['live-metrics'],
    queryFn: fetchLiveMetrics,
    staleTime: 60_000,
    retry: 1,
  });

  return (
    <div className="space-y-4 lg:space-y-6 max-w-6xl">
      <SectionHeader
        title="Dashboard"
        description="Visão geral — Direito Trabalhista Reclamante"
        action={<Badge variant="outline" className="text-[10px]">Trabalhista</Badge>}
      />

      {/* Live Metrics Row */}
      {live && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 sm:gap-3">
          <ProposalsGauge rate={live.proposals_accepted_rate} />
          <PipelineCard value={live.pipeline_value} />
          <ConversionCard rate={live.conversion_rate} leads={live.leads_this_week} />
        </div>
      )}

      {/* KPIs */}
      <div className="grid grid-cols-2 sm:grid-cols-2 lg:grid-cols-4 gap-2 sm:gap-3">
        {topMetrics.map(m => (
          <KPIStatCard key={m.id} metric={m} />
        ))}
      </div>

      {/* Recent Cards */}
      <div>
        <h3 className="text-sm font-semibold mb-2 sm:mb-3">Últimas atualizações</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 sm:gap-3">
          {recentCards.map(card => (
            <MatterCard key={card.id} card={card} />
          ))}
        </div>
      </div>
    </div>
  );
}
