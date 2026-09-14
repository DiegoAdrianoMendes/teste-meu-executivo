import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowRight,
  Bug,
  Clock3,
  KeyRound,
  LifeBuoy,
  RefreshCw,
  ServerCog,
  ShieldCheck,
  TicketCheck,
} from 'lucide-react';
import { Link } from 'react-router-dom';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { apiFetch } from '@/services/http';

interface TechnicalTicket {
  id: string;
  publicNumber: string;
  subject: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  status: string;
  firstResponseDueAt: string;
  resolutionDueAt: string;
  firstRespondedAt?: string | null;
  assignee?: { name: string } | null;
}

interface TicketPage {
  items: TechnicalTicket[];
}

interface SupportMetrics {
  open: number;
  overdue: number;
  nearDue: number;
  backlogByQueue: Array<{ label: string; value: number }>;
}

interface ErrorSummary {
  total_open: number;
  total_in_progress: number;
  by_severity: Record<string, number>;
}

const OPEN_STATUSES = [
  'new',
  'triage',
  'in_progress',
  'waiting_customer',
  'waiting_internal',
  'validating',
];

function technicalTicketPath(): string {
  const params = new URLSearchParams();
  params.set('queue', 'technical');
  return `/blackbox/support-tickets?${params.toString()}`;
}

function deadline(ticket: TechnicalTicket): { label: string; overdue: boolean } {
  const dueAt = ticket.firstRespondedAt
    ? ticket.resolutionDueAt
    : ticket.firstResponseDueAt;
  const milliseconds = new Date(dueAt).getTime() - Date.now();
  if (!Number.isFinite(milliseconds)) return { label: 'Prazo indisponível', overdue: false };
  const absoluteMinutes = Math.max(1, Math.round(Math.abs(milliseconds) / 60_000));
  const hours = Math.floor(absoluteMinutes / 60);
  const minutes = absoluteMinutes % 60;
  const duration = hours > 0 ? `${hours}h ${minutes}min` : `${minutes}min`;
  return {
    label: milliseconds < 0 ? `${duration} em atraso` : `${duration} restantes`,
    overdue: milliseconds < 0,
  };
}

function severityLabel(severity: TechnicalTicket['severity']): string {
  return {
    low: 'Baixa',
    medium: 'Média',
    high: 'Alta',
    critical: 'Crítica',
  }[severity];
}

function SummaryCard({
  title,
  value,
  detail,
  icon: Icon,
  tone,
}: {
  title: string;
  value: number | string;
  detail: string;
  icon: React.ElementType;
  tone: 'blue' | 'amber' | 'red' | 'slate';
}) {
  const toneClass = {
    blue: 'border-blue-200 bg-blue-50 text-blue-700',
    amber: 'border-amber-200 bg-amber-50 text-amber-700',
    red: 'border-red-200 bg-red-50 text-red-700',
    slate: 'border-slate-200 bg-white text-slate-700',
  }[tone];
  return (
    <Card className={toneClass}>
      <CardContent className="flex items-start justify-between gap-4 p-4">
        <div>
          <p className="text-xs font-semibold">{title}</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-950">{value}</p>
          <p className="mt-1 text-xs text-slate-600">{detail}</p>
        </div>
        <Icon className="h-5 w-5 shrink-0" />
      </CardContent>
    </Card>
  );
}

export default function BlackboxTechnicalHome() {
  const metricsQuery = useQuery({
    queryKey: ['blackbox-technical-home', 'support-metrics'],
    queryFn: () => apiFetch<SupportMetrics>('/api/v1/platform/support/metrics?periodDays=30'),
  });
  const ticketsQuery = useQuery({
    queryKey: ['blackbox-technical-home', 'tickets'],
    queryFn: () => {
      const params = new URLSearchParams({ limit: '8' });
      params.append('queues', 'technical');
      OPEN_STATUSES.forEach((status) => params.append('statuses', status));
      return apiFetch<TicketPage>(`/api/v1/platform/support/tickets?${params.toString()}`);
    },
  });
  const errorsQuery = useQuery({
    queryKey: ['blackbox-technical-home', 'errors'],
    queryFn: () => apiFetch<ErrorSummary>('/api/v1/error-logs/summary'),
  });

  const metrics = metricsQuery.data;
  const errors = errorsQuery.data;
  const technicalBacklog = metrics?.backlogByQueue.find(
    (item) => item.label === 'technical',
  )?.value ?? ticketsQuery.data?.items.length ?? 0;
  const criticalSignals = (errors?.by_severity.critical ?? 0) + (errors?.by_severity.high ?? 0);
  const isRefreshing = metricsQuery.isFetching || ticketsQuery.isFetching || errorsQuery.isFetching;
  const refresh = () => {
    void Promise.all([
      metricsQuery.refetch(),
      ticketsQuery.refetch(),
      errorsQuery.refetch(),
    ]);
  };

  return (
    <div className="mx-auto max-w-[1440px] space-y-5 p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase text-blue-600">Operação técnica</p>
          <h1 className="mt-1 text-xl font-semibold">TI Meu Executivo</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-600">
            Tickets, falhas e acessos assistidos em uma fila operacional única, sem abrir
            automaticamente os dados das contas contratantes.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="gap-1 border-emerald-200 bg-emerald-50 text-emerald-700">
            <ShieldCheck className="h-3.5 w-3.5" />
            Ambiente interno isolado
          </Badge>
          <Button type="button" variant="outline" size="sm" onClick={refresh} disabled={isRefreshing}>
            <RefreshCw className={`mr-2 h-4 w-4 ${isRefreshing ? 'animate-spin' : ''}`} />
            Atualizar
          </Button>
        </div>
      </div>

      {(metricsQuery.isError || ticketsQuery.isError || errorsQuery.isError) && (
        <div className="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-900">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          Parte da operação técnica não respondeu. Os módulos disponíveis permanecem acessíveis abaixo.
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          title="Fila técnica"
          value={technicalBacklog}
          detail="Chamados técnicos em aberto"
          icon={TicketCheck}
          tone="blue"
        />
        <SummaryCard
          title="Prazos vencidos"
          value={metrics?.overdue ?? 0}
          detail={`${metrics?.nearDue ?? 0} próximos do vencimento`}
          icon={Clock3}
          tone={(metrics?.overdue ?? 0) > 0 ? 'red' : 'slate'}
        />
        <SummaryCard
          title="Erros não corrigidos"
          value={errors?.total_open ?? 0}
          detail={`${errors?.total_in_progress ?? 0} em tratamento`}
          icon={Bug}
          tone={(errors?.total_open ?? 0) > 0 ? 'amber' : 'slate'}
        />
        <SummaryCard
          title="Sinais críticos"
          value={criticalSignals}
          detail="Erros críticos ou de alta severidade"
          icon={ServerCog}
          tone={criticalSignals > 0 ? 'red' : 'slate'}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(320px,0.8fr)]">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-3 pb-3">
            <div>
              <CardTitle className="text-base">Fila técnica prioritária</CardTitle>
              <p className="mt-1 text-xs text-slate-600">
                Chamados reais ordenados pelo núcleo de SAC.
              </p>
            </div>
            <Button variant="outline" size="sm" asChild>
              <Link to={technicalTicketPath()}>
                Abrir fila
                <ArrowRight className="ml-2 h-4 w-4" />
              </Link>
            </Button>
          </CardHeader>
          <CardContent>
            {ticketsQuery.isLoading ? (
              <p className="py-8 text-center text-sm text-slate-500">Carregando fila técnica...</p>
            ) : ticketsQuery.data?.items.length ? (
              <div className="divide-y">
                {ticketsQuery.data.items.map((ticket) => {
                  const ticketDeadline = deadline(ticket);
                  return (
                    <Link
                      key={ticket.id}
                      to={`${technicalTicketPath()}&ticket=${ticket.id}`}
                      className="flex items-center justify-between gap-4 py-3 transition-colors hover:bg-slate-50"
                    >
                      <span className="min-w-0">
                        <span className="flex flex-wrap items-center gap-2">
                          <strong className="text-xs text-blue-700">{ticket.publicNumber}</strong>
                          <Badge variant="outline" className="text-[10px]">
                            {severityLabel(ticket.severity)}
                          </Badge>
                        </span>
                        <span className="mt-1 block truncate text-sm font-medium">{ticket.subject}</span>
                        <span className="mt-1 block text-xs text-slate-500">
                          {ticket.assignee?.name ?? 'Sem responsável'}
                        </span>
                      </span>
                      <span className={`shrink-0 text-xs font-medium ${ticketDeadline.overdue ? 'text-red-700' : 'text-slate-600'}`}>
                        {ticketDeadline.label}
                      </span>
                    </Link>
                  );
                })}
              </div>
            ) : (
              <div className="py-8 text-center">
                <TicketCheck className="mx-auto h-7 w-7 text-emerald-500" />
                <p className="mt-2 text-sm font-medium">Fila técnica sem chamados abertos</p>
                <p className="mt-1 text-xs text-slate-500">Novos tickets aparecerão aqui em tempo real.</p>
              </div>
            )}
          </CardContent>
        </Card>

        <div className="space-y-3">
          {[
            {
              to: technicalTicketPath(),
              icon: LifeBuoy,
              title: 'SAC técnico',
              text: 'Triar, responder, atribuir e acompanhar prazo.',
            },
            {
              to: '/blackbox/errors',
              icon: Bug,
              title: 'Erros e incidentes',
              text: 'Investigar recorrência, severidade e resolução.',
            },
            {
              to: '/blackbox/support-access',
              icon: KeyRound,
              title: 'Acesso assistido',
              text: 'Solicitar acesso temporário, justificado e auditado.',
            },
            {
              to: '/blackbox/workspaces',
              icon: ServerCog,
              title: 'Contas e integrações',
              text: 'Localizar a conta antes de qualquer diagnóstico.',
            },
          ].map((action) => (
            <Link
              key={action.to}
              to={action.to}
              className="flex items-center gap-3 rounded-lg border bg-white p-4 transition-colors hover:border-blue-300 hover:bg-blue-50"
            >
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-slate-100 text-slate-700">
                <action.icon className="h-4 w-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold">{action.title}</span>
                <span className="mt-0.5 block text-xs leading-5 text-slate-600">{action.text}</span>
              </span>
              <ArrowRight className="h-4 w-4 shrink-0 text-slate-400" />
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
