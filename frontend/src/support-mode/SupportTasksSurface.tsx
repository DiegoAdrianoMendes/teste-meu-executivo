import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, ChevronLeft, ChevronRight, ListTodo, RefreshCw, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { TaskCollaborationDrawer } from '@/klm/components/TaskCollaborationDrawer';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import {
  listCollaborativeTasks,
  type CollaborativeTask,
} from '@/services/tasks';
import {
  getActiveSupportMode,
  type SupportModeSession,
} from '@/support-mode/session';

const STATUS_LABEL: Record<string, string> = {
  pending: 'Pendente',
  in_progress: 'Em andamento',
  blocked: 'Bloqueada',
  done: 'Concluída',
};

function formatDate(value: string | null | undefined): string {
  if (!value) return 'Sem prazo';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Prazo indisponível' : parsed.toLocaleString('pt-BR');
}

function activeSessionMatches(session: SupportModeSession): boolean {
  const active = getActiveSupportMode();
  return Boolean(
    active
    && active.grantId === session.grantId
    && active.workspaceId === session.workspaceId
    && active.expiresAt === session.expiresAt
    && ['root_direct', 'support_approved'].includes(active.sessionType ?? 'support_approved'),
  );
}

export function SupportTasksSurface({ session }: { session: SupportModeSession }) {
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([]);
  const [selectedTask, setSelectedTask] = useState<CollaborativeTask | null>(null);
  const canRead = session.scopes.includes('tasks:read');
  const administrativeSession = activeSessionMatches(session);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  useEffect(() => {
    setCursor(null);
    setCursorHistory([]);
  }, [debouncedSearch]);

  const tasksQuery = useQuery({
    queryKey: ['support-mode', session.grantId, session.workspaceId, 'tasks', cursor, debouncedSearch],
    queryFn: () => listCollaborativeTasks({
      view: 'workspace',
      cursor,
      limit: 50,
      search: debouncedSearch,
    }),
    enabled: canRead && administrativeSession && (debouncedSearch.length === 0 || debouncedSearch.length >= 2),
    retry: false,
  });
  const tasks = useMemo(() => tasksQuery.data?.items ?? [], [tasksQuery.data?.items]);
  const taskIds = useMemo(() => new Set(tasks.map((task) => task.id)), [tasks]);
  useEffect(() => {
    if (selectedTask && !taskIds.has(selectedTask.id)) setSelectedTask(null);
  }, [selectedTask, taskIds]);

  if (!canRead || !administrativeSession) {
    return (
      <Alert variant="destructive" data-testid="support-tasks-session-required">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Sessão administrativa ativa obrigatória</AlertTitle>
        <AlertDescription>
          Nenhuma tarefa foi consultada. Ative uma sessão `root_direct` ou
          `support_approved` da mesma conta com o escopo `tasks:read`.
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="space-y-4" data-testid="support-tasks-surface">
      <Alert>
        <ListTodo className="h-4 w-4" />
        <AlertTitle>Conteúdo tenant autorizado nesta sessão</AlertTitle>
        <AlertDescription>
          A lista abaixo pertence exclusivamente a {session.workspaceName}. O conteúdo
          não é copiado para a Conta 360 nem para tarefas internas da plataforma.
        </AlertDescription>
      </Alert>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[260px] flex-1">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            className="pl-9"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Buscar tarefa ou caso autorizado"
            aria-label="Buscar tarefas da conta em suporte"
          />
        </div>
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label="Atualizar tarefas da conta"
          disabled={tasksQuery.isFetching}
          onClick={() => void tasksQuery.refetch()}
        >
          <RefreshCw className={`h-4 w-4 ${tasksQuery.isFetching ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {search.trim().length === 1 ? (
        <p className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
          Digite ao menos dois caracteres para pesquisar.
        </p>
      ) : null}
      {tasksQuery.isPending ? (
        <div className="space-y-3" role="status" aria-live="polite">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
          <span className="sr-only">Carregando tarefas autorizadas</span>
        </div>
      ) : null}
      {tasksQuery.isError ? (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Consulta de tarefas bloqueada</AlertTitle>
          <AlertDescription>
            A sessão expirou, foi revogada, perdeu o escopo ou a API está indisponível.
            Nenhum resultado parcial foi apresentado como completo.
          </AlertDescription>
        </Alert>
      ) : null}
      {tasksQuery.isSuccess && tasks.length === 0 ? (
        <div className="rounded-lg border border-dashed p-10 text-center">
          <ListTodo className="mx-auto h-8 w-8 text-muted-foreground" />
          <p className="mt-3 font-medium">Nenhuma tarefa neste recorte</p>
          <p className="text-sm text-muted-foreground">A consulta da conta foi concluída sem resultados.</p>
        </div>
      ) : null}

      <div className="space-y-3">
        {tasks.map((task) => (
          <button
            key={task.id}
            type="button"
            className="flex w-full flex-wrap items-start justify-between gap-4 rounded-lg border p-4 text-left transition-colors hover:bg-muted/40"
            onClick={() => setSelectedTask(task)}
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap gap-2">
                <Badge variant={task.status === 'blocked' ? 'destructive' : 'outline'}>
                  {STATUS_LABEL[task.status] ?? task.status}
                </Badge>
                {task.reviewRequired ? <Badge variant="secondary">Revisão: {task.reviewState}</Badge> : null}
              </div>
              <h2 className="mt-2 truncate font-semibold">{task.title}</h2>
              <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                {task.description || 'Sem descrição.'}
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                {task.clientLabel || 'Cliente protegido'} · {task.matterLabel || 'Caso'} · prazo {formatDate(task.dueAt ?? task.dueDate)}
              </p>
            </div>
            <span className="text-xs font-medium text-primary">Abrir tarefa</span>
          </button>
        ))}
      </div>

      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-muted-foreground">Até 50 registros por página</span>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={cursorHistory.length === 0 || tasksQuery.isFetching}
            onClick={() => {
              const history = [...cursorHistory];
              setCursor(history.pop() ?? null);
              setCursorHistory(history);
            }}
          >
            <ChevronLeft className="mr-1 h-4 w-4" />Anterior
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!tasksQuery.data?.hasMore || !tasksQuery.data.nextCursor || tasksQuery.isFetching}
            onClick={() => {
              const next = tasksQuery.data?.nextCursor;
              if (!next) return;
              setCursorHistory((history) => [...history, cursor]);
              setCursor(next);
            }}
          >
            Próxima<ChevronRight className="ml-1 h-4 w-4" />
          </Button>
        </div>
      </div>

      {selectedTask ? (
        <TaskCollaborationDrawer
          taskId={selectedTask.id}
          summary={selectedTask}
          contextLabels={{
            clientLabel: selectedTask.clientLabel,
            matterLabel: selectedTask.matterLabel,
          }}
          open
          onOpenChange={(next) => { if (!next) setSelectedTask(null); }}
        />
      ) : null}
    </div>
  );
}
