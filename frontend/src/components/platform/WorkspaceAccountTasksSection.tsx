import { CalendarClock, ListChecks, LockKeyhole, ShieldCheck } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { WorkspaceOverviewTaskSummary } from '@/types';

function formatDate(value: string | null): string {
  if (!value) return 'Sem atualização registrada';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? 'Atualização indisponível'
    : `Atualizado em ${parsed.toLocaleString('pt-BR')}`;
}

export function WorkspaceAccountTasksSection({
  summary,
  isPlatformRoot,
  canEnterDirect,
  directAccessReady,
  entering,
  onEnterDirect,
}: {
  summary: WorkspaceOverviewTaskSummary;
  isPlatformRoot: boolean;
  canEnterDirect: boolean;
  directAccessReady: boolean;
  entering: boolean;
  onEnterDirect: () => void;
}) {
  const metrics = [
    ['Em aberto', summary.open],
    ['Vencidas', summary.overdue],
    ['Bloqueadas', summary.blocked],
    ['Em revisão', summary.pending_review],
    ['Concluídas', summary.completed],
    ['Canceladas', summary.cancelled],
  ] as const;

  return (
    <section className="space-y-3 rounded-xl border p-4" data-testid="workspace-task-safe-summary">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <ListChecks className="h-4 w-4 text-primary" />
            <h3 className="text-sm font-semibold">Tarefas da conta</h3>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Contagens operacionais seguras. A Conta 360 não recebe títulos, descrições,
            participantes, comentários ou dados do caso.
          </p>
        </div>
        <Badge variant="outline">{summary.total} no total</Badge>
      </div>

      <div className="grid gap-2 sm:grid-cols-3">
        {metrics.map(([label, value]) => (
          <div key={label} className="rounded-lg bg-muted/40 p-3">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="mt-1 text-lg font-semibold tabular-nums">{value}</p>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/20 p-3">
        <div className="flex min-w-0 items-start gap-2">
          {isPlatformRoot ? (
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          ) : (
            <LockKeyhole className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
          )}
          <div>
            <p className="text-xs font-medium">
              {isPlatformRoot
                ? 'Conteúdo tenant somente após entrada direta auditada'
                : 'Conteúdo tenant exige acesso assistido aprovado'}
            </p>
            <p className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
              <CalendarClock className="h-3.5 w-3.5" />{formatDate(summary.last_updated_at)}
            </p>
          </div>
        </div>
        {isPlatformRoot && canEnterDirect ? (
          <Button type="button" size="sm" disabled={entering || !directAccessReady} onClick={onEnterDirect}>
            {entering ? 'Entrando…' : 'Entrar e abrir tarefas'}
          </Button>
        ) : (
          <span className="max-w-xs text-right text-xs text-muted-foreground">
            Use Acesso assistido e solicite os escopos `tasks:read` e, para alterações,
            `tasks:write`.
          </span>
        )}
      </div>
    </section>
  );
}
