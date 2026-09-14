import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle2,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldAlert,
  SlidersHorizontal,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  klmApi,
  type KlmOmnichannelAuditFinding,
} from '@/klm/api';
import { KlmPill, KlmProgress } from '@/klm/components/Primitives';

type AuditFilters = {
  state: string;
  severity: string;
  findingType: string;
  query: string;
};

const EMPTY_FILTERS: AuditFilters = {
  state: '',
  severity: '',
  findingType: '',
  query: '',
};

const FINDING_LABELS: Record<string, string> = {
  unanswered_lead: 'Sem resposta',
  delivery_failed: 'Falha de entrega',
  delivery_stalled: 'Fila parada',
  projection_mismatch: 'Status divergente',
  duplicate_outbound: 'Possível duplicidade',
  potential_internal_leak: 'Conteúdo interno',
};

function findingTone(finding: KlmOmnichannelAuditFinding): 'green' | 'gold' | 'red' | 'muted' {
  if (finding.state === 'resolved' || finding.state === 'dismissed') return 'green';
  if (finding.severity === 'critical') return 'red';
  if (finding.state === 'retry_scheduled') return 'gold';
  return 'muted';
}

export function ConversationAuditPanel({
  threadId,
  canAct = true,
}: {
  threadId: string;
  canAct?: boolean;
}) {
  const queryClient = useQueryClient();
  const [customizing, setCustomizing] = useState(false);
  const [filters, setFilters] = useState<AuditFilters>(EMPTY_FILTERS);
  const queryKey = useMemo(
    () => ['klm', 'omnichannel-audit', threadId, filters] as const,
    [filters, threadId],
  );
  const auditQuery = useQuery({
    queryKey,
    queryFn: () => klmApi.omnichannelAudit({
      threadId,
      state: filters.state || undefined,
      severity: filters.severity || undefined,
      findingType: filters.findingType || undefined,
      query: filters.query.trim() || undefined,
      limit: 100,
    }),
  });
  const runMutation = useMutation({
    mutationFn: () => klmApi.runOmnichannelAudit({ threadId, limit: 1 }),
    onSuccess: (result) => {
      queryClient.setQueryData(queryKey, result);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'quality'] });
    },
  });
  const actionMutation = useMutation({
    mutationFn: ({
      finding,
      action,
    }: {
      finding: KlmOmnichannelAuditFinding;
      action: 'resolve' | 'dismiss' | 'retry';
    }) => klmApi.actOnOmnichannelAuditFinding(finding.id, {
      action,
      expectedVersion: finding.version,
    }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-audit'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'quality'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'messages', threadId] });
    },
  });

  useEffect(() => {
    const refresh = (event: Event) => {
      const detail = (event as CustomEvent<{
        event?: string;
        data?: { thread_ids?: string[]; thread_id?: string };
      }>).detail;
      if (detail?.event !== 'OMNICHANNEL_AUDIT_UPDATED') return;
      const affected = detail.data?.thread_ids ?? (detail.data?.thread_id ? [detail.data.thread_id] : []);
      if (!affected.length || affected.includes(threadId)) {
        void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-audit', threadId] });
      }
    };
    window.addEventListener('klm:realtime', refresh);
    return () => window.removeEventListener('klm:realtime', refresh);
  }, [queryClient, threadId]);

  const data = auditQuery.data;
  const coverage = data?.coveragePercent ?? 100;
  const responseCoverage = data?.responseCoveragePercent ?? 100;
  const deliveryIntegrity = data?.deliveryIntegrityPercent ?? 100;
  const pending = runMutation.isPending || auditQuery.isFetching;

  return (
    <div className="space-y-3" data-testid="conversation-audit-panel">
      <div className="grid grid-cols-3 gap-2">
        {[
          ['Cobertura', coverage],
          ['Respostas', responseCoverage],
          ['Entregas', deliveryIntegrity],
        ].map(([label, value]) => (
          <div key={String(label)} className="min-w-0 border-l-2 border-primary pl-2">
            <p className="truncate text-2xs font-semibold uppercase text-muted-foreground">{label}</p>
            <p className="mt-1 text-sm font-semibold">{Number(value).toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%</p>
          </div>
        ))}
      </div>
      <KlmProgress value={coverage} tone={coverage >= 95 ? 'green' : coverage >= 80 ? 'gold' : 'red'} />

      <div className="flex flex-wrap items-center gap-1.5">
        <KlmPill tone={(data?.criticalCount ?? 0) ? 'red' : 'green'}>{data?.criticalCount ?? 0} crítico(s)</KlmPill>
        <KlmPill tone={(data?.waitingHumanCount ?? 0) ? 'gold' : 'muted'}>{data?.waitingHumanCount ?? 0} revisão</KlmPill>
        <KlmPill tone={(data?.retryScheduledCount ?? 0) ? 'gold' : 'muted'}>{data?.retryScheduledCount ?? 0} reenvio(s)</KlmPill>
      </div>

      <div className="flex items-center gap-1.5">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 flex-1 text-2xs"
          disabled={!canAct || pending}
          onClick={() => runMutation.mutate()}
        >
          {pending ? <LoaderCircle className="mr-1.5 h-3 w-3 animate-spin" /> : <RefreshCw className="mr-1.5 h-3 w-3" />}
          Verificar agora
        </Button>
        <Button
          type="button"
          variant={customizing ? 'secondary' : 'ghost'}
          size="icon"
          className="h-7 w-7"
          title="Personalizar filtros"
          onClick={() => setCustomizing((current) => !current)}
        >
          <SlidersHorizontal className="h-3.5 w-3.5" />
        </Button>
      </div>

      {customizing ? (
        <div className="space-y-2 border-y border-border py-3" data-testid="audit-custom-filters">
          <label className="flex h-8 items-center gap-2 border border-border bg-background px-2">
            <Search className="h-3 w-3 text-muted-foreground" />
            <input
              value={filters.query}
              onChange={(event) => setFilters((current) => ({ ...current, query: event.target.value }))}
              placeholder="Filtro personalizado"
              className="min-w-0 flex-1 bg-transparent text-2xs outline-none"
            />
          </label>
          <div className="grid grid-cols-2 gap-2">
            <select
              aria-label="Estado do apontamento"
              value={filters.state}
              onChange={(event) => setFilters((current) => ({ ...current, state: event.target.value }))}
              className="h-8 border border-border bg-background px-2 text-2xs"
            >
              <option value="">Todos os estados</option>
              <option value="open">Aberto</option>
              <option value="waiting_human">Revisão humana</option>
              <option value="retry_scheduled">Reenvio agendado</option>
              <option value="resolved">Resolvido</option>
              <option value="dismissed">Descartado</option>
            </select>
            <select
              aria-label="Severidade do apontamento"
              value={filters.severity}
              onChange={(event) => setFilters((current) => ({ ...current, severity: event.target.value }))}
              className="h-8 border border-border bg-background px-2 text-2xs"
            >
              <option value="">Toda severidade</option>
              <option value="critical">Crítica</option>
              <option value="warning">Atenção</option>
              <option value="info">Informativa</option>
            </select>
          </div>
          <div className="flex items-center justify-between gap-2">
            <select
              aria-label="Tipo de apontamento"
              value={filters.findingType}
              onChange={(event) => setFilters((current) => ({ ...current, findingType: event.target.value }))}
              className="h-8 min-w-0 flex-1 border border-border bg-background px-2 text-2xs"
            >
              <option value="">Qualquer tipo</option>
              {Object.entries(FINDING_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
            <Button type="button" variant="ghost" size="sm" className="h-8 text-2xs" onClick={() => setFilters(EMPTY_FILTERS)}>Limpar</Button>
          </div>
        </div>
      ) : null}

      {auditQuery.isError ? (
        <div className="flex items-start gap-2 border-l-2 border-red-500 pl-2 text-2xs leading-4 text-muted-foreground">
          <ShieldAlert className="mt-0.5 h-3 w-3 shrink-0 text-red-600" />
          Não foi possível confirmar a integridade desta conversa.
        </div>
      ) : null}

      <div className="divide-y divide-border border-y border-border">
        {(data?.findings ?? []).map((finding) => (
          <div key={finding.id} className="py-2.5" data-testid={`audit-finding-${finding.findingType}`}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-2xs font-semibold">{finding.title}</p>
                <p className="mt-1 text-2xs leading-4 text-muted-foreground">{finding.summary}</p>
              </div>
              <KlmPill tone={findingTone(finding)}>{FINDING_LABELS[finding.findingType] ?? finding.findingType}</KlmPill>
            </div>
            {finding.state === 'retry_scheduled' && finding.nextRetryAt ? (
              <p className="mt-1.5 text-2xs text-[#0057d8]">Reenvio seguro: {new Date(finding.nextRetryAt).toLocaleString('pt-BR')}</p>
            ) : null}
            {finding.state === 'open' || finding.state === 'waiting_human' ? (
              <div className="mt-2 flex items-center gap-1.5">
                {finding.remediation.automaticRetryEligible === true ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 text-2xs"
                    disabled={!canAct || actionMutation.isPending}
                    onClick={() => actionMutation.mutate({ finding, action: 'retry' })}
                  >
                    <RotateCcw className="mr-1 h-3 w-3" />Tentar com segurança
                  </Button>
                ) : null}
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 text-2xs"
                  disabled={!canAct || actionMutation.isPending}
                  onClick={() => actionMutation.mutate({ finding, action: 'resolve' })}
                >
                  <CheckCircle2 className="mr-1 h-3 w-3" />Resolver
                </Button>
              </div>
            ) : null}
          </div>
        ))}
        {!auditQuery.isPending && !(data?.findings.length) ? (
          <div className="flex items-center gap-2 py-3 text-2xs text-muted-foreground">
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />Nenhum apontamento neste filtro.
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default ConversationAuditPanel;
