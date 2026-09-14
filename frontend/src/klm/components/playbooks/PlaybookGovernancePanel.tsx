import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, CircleAlert, FileDiff, History, LoaderCircle, RotateCcw, ShieldCheck } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { KlmMetricCard, KlmPill } from '@/klm/components/Primitives';
import { formatPlaybookDate, lifecycleLabel, lifecycleTone } from '@/klm/playbooks/presentation';
import { getPlaybookDiff, type PlaybookDiffEntry, type PlaybookMetrics, type PlaybookVersion } from '@/services/playbooks';

interface Props {
  playbookId: string;
  versions: PlaybookVersion[];
  metrics?: PlaybookMetrics;
  canEdit: boolean;
  rollbackPending: boolean;
  onRollback: (version: number) => void;
}

function printable(value: unknown): string {
  if (value === null || value === undefined) return 'vazio';
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2);
}

const tenantPrivateFieldMarkers = [
  'aiprovider',
  'aimodel',
  'averagecost',
  'costpolicy',
  'coststate',
  'dailybudget',
  'estimatedcost',
  'modelkey',
  'modelused',
  'providercost',
];

function isTenantPrivateField(value: string): boolean {
  const normalized = value.replace(/[^a-z0-9]/gi, '').toLocaleLowerCase('en-US');
  return normalized.includes('token')
    || tenantPrivateFieldMarkers.some((marker) => normalized.includes(marker));
}

function tenantSafeValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(tenantSafeValue);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter(([key]) => !isTenantPrivateField(key))
      .map(([key, child]) => [key, tenantSafeValue(child)]),
  );
}

function tenantSafeDiffEntries(entries: PlaybookDiffEntry[]): PlaybookDiffEntry[] {
  return entries
    .filter((entry) => !entry.path.split(/[.[\]]/).filter(Boolean).some(isTenantPrivateField))
    .map((entry) => ({
      ...entry,
      before: tenantSafeValue(entry.before),
      after: tenantSafeValue(entry.after),
    }));
}

export function PlaybookGovernancePanel({ playbookId, versions, metrics, canEdit, rollbackPending, onRollback }: Props) {
  const ordered = useMemo(() => [...versions].sort((a, b) => b.version - a.version), [versions]);
  const [fromVersion, setFromVersion] = useState(String(ordered[1]?.version ?? ordered[0]?.version ?? 1));
  const [toVersion, setToVersion] = useState(String(ordered[0]?.version ?? 1));

  useEffect(() => {
    if (ordered.length === 0) return;
    setToVersion(String(ordered[0].version));
    setFromVersion(String(ordered[1]?.version ?? ordered[0].version));
  }, [ordered]);

  const diffQuery = useQuery({
    queryKey: ['playbook-diff', playbookId, fromVersion, toVersion],
    queryFn: () => getPlaybookDiff(playbookId, Number(fromVersion), Number(toVersion)),
    enabled: Boolean(fromVersion && toVersion && fromVersion !== toVersion),
  });
  const visibleDiffEntries = useMemo(
    () => tenantSafeDiffEntries(diffQuery.data?.entries ?? []),
    [diffQuery.data?.entries],
  );

  return (
    <div className="space-y-5 p-4 md:p-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <KlmMetricCard label="Cobertura" value={`${Math.round(metrics?.structuralCoverage ?? 0)}%`} delta="blocos obrigatórios" trend={(metrics?.structuralCoverage ?? 0) >= 95 ? 'up' : 'neutral'} />
        <KlmMetricCard label="Aderência" value={`${Math.round(metrics?.passRate ?? 0)}%`} delta={`${metrics?.simulations ?? 0} simulações`} trend={(metrics?.passRate ?? 0) >= 95 ? 'up' : 'neutral'} />
        <KlmMetricCard label="Handoff" value={`${Math.round(metrics?.handoffRate ?? 0)}%`} delta="escalonamentos simulados" trend="neutral" />
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(300px,.75fr)_minmax(0,1.25fr)]">
        <section>
          <div className="flex items-center gap-2"><History className="h-4 w-4 text-primary" /><div><h2 className="text-[14px] font-semibold">Histórico imutável</h2><p className="text-[10px] text-muted-foreground">A reversão sempre cria uma nova versão rascunho.</p></div></div>
          <div className="mt-4 divide-y border-y">
            {ordered.map((version, index) => (
              <article key={version.id} className="py-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2"><strong className="text-[12px]">v{version.version}</strong><KlmPill tone={lifecycleTone[version.state]}>{lifecycleLabel[version.state]}</KlmPill>{index === 0 && <KlmPill tone="blue">Atual</KlmPill>}</div>
                    <p className="mt-2 text-[10px] leading-4 text-muted-foreground">{version.reason || 'Sem justificativa registrada.'}</p>
                    <p className="mt-1 font-mono text-[8px] text-muted-foreground">{version.payloadHash.slice(0, 16)} · {formatPlaybookDate(version.createdAt)}</p>
                  </div>
                  {index > 0 && <Button type="button" size="icon" variant="ghost" disabled={!canEdit || rollbackPending} title={`Restaurar versão ${version.version}`} aria-label={`Restaurar versão ${version.version}`} onClick={() => onRollback(version.version)}>{rollbackPending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}</Button>}
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="min-w-0">
          <div className="flex items-center gap-2"><FileDiff className="h-4 w-4 text-primary" /><div><h2 className="text-[14px] font-semibold">Comparar versões</h2><p className="text-[10px] text-muted-foreground">Alterações estruturais antes de aprovar ou publicar.</p></div></div>
          <div className="mt-4 grid gap-2 sm:grid-cols-[1fr_auto_1fr] sm:items-center">
            <Select value={fromVersion} onValueChange={setFromVersion}><SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger><SelectContent>{ordered.map((version) => <SelectItem key={version.id} value={String(version.version)}>v{version.version} · {lifecycleLabel[version.state]}</SelectItem>)}</SelectContent></Select>
            <ArrowRight className="mx-auto h-4 w-4 text-muted-foreground" />
            <Select value={toVersion} onValueChange={setToVersion}><SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger><SelectContent>{ordered.map((version) => <SelectItem key={version.id} value={String(version.version)}>v{version.version} · {lifecycleLabel[version.state]}</SelectItem>)}</SelectContent></Select>
          </div>
          <div className="mt-4 min-h-80 border-y py-3">
            {fromVersion === toVersion && <div className="flex min-h-72 flex-col items-center justify-center text-center"><FileDiff className="h-6 w-6 text-primary" /><p className="mt-3 text-xs font-semibold">Selecione versões diferentes</p></div>}
            {diffQuery.isLoading && <div className="flex min-h-72 items-center justify-center gap-2 text-xs text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin" />Calculando diff...</div>}
            {diffQuery.isError && <div className="flex min-h-72 flex-col items-center justify-center text-center"><CircleAlert className="h-6 w-6 text-[#a53e3c]" /><p className="mt-3 text-xs font-semibold">Não foi possível comparar</p></div>}
            {diffQuery.data && visibleDiffEntries.length === 0 && <div className="flex min-h-72 flex-col items-center justify-center text-center"><ShieldCheck className="h-6 w-6 text-emerald-600" /><p className="mt-3 text-xs font-semibold">Sem diferenças visíveis</p></div>}
            {diffQuery.data && visibleDiffEntries.length > 0 && <div className="space-y-3">{visibleDiffEntries.map((entry) => <article key={`${entry.path}-${entry.change}`} className="rounded-md border p-3"><div className="flex flex-wrap items-center justify-between gap-2"><code className="break-all text-[10px] font-semibold">{entry.path}</code><KlmPill tone={entry.change === 'removed' ? 'red' : entry.change === 'added' ? 'green' : 'gold'}>{entry.change === 'added' ? 'Adicionado' : entry.change === 'removed' ? 'Removido' : 'Alterado'}</KlmPill></div><div className="mt-3 grid gap-2 md:grid-cols-2"><pre className="max-h-36 overflow-auto whitespace-pre-wrap rounded-md bg-red-500/5 p-2 text-[9px] text-muted-foreground">{printable(entry.before)}</pre><pre className="max-h-36 overflow-auto whitespace-pre-wrap rounded-md bg-emerald-500/5 p-2 text-[9px] text-muted-foreground">{printable(entry.after)}</pre></div></article>)}</div>}
          </div>
        </section>
      </div>
    </div>
  );
}
