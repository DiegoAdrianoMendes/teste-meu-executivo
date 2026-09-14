import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LoaderCircle, Workflow } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useToast } from '@/hooks/use-toast';
import { klmApi } from '@/klm/api';
import { applyTaskWorkflow, listTaskWorkflowRuns } from '@/services/task-workflows';
import type { CollaborativeTask } from '@/services/tasks';

const statusLabel: Record<string, string> = {
  active: 'Em execução', paused: 'Pausado', waiting_human: 'Aguardando revisão',
  completed: 'Concluído', failed: 'Falhou', cancelled: 'Cancelado',
};

export function TaskWorkflowSection({ task, canManage }: { task: CollaborativeTask; canManage: boolean }) {
  const [flowId, setFlowId] = useState('');
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const flows = useQuery({ queryKey: ['task-workflow-options'], queryFn: klmApi.flows, enabled: canManage });
  const runs = useQuery({
    queryKey: ['task-workflow-runs', task.id],
    queryFn: () => listTaskWorkflowRuns(task.id),
    enabled: canManage,
  });
  const options = useMemo(
    () => (flows.data ?? []).filter((flow) => flow.is_active && !flow.is_template),
    [flows.data],
  );
  const apply = useMutation({
    mutationFn: () => applyTaskWorkflow(
      task.id,
      flowId,
      `task-flow:${task.id}:${flowId}:${crypto.randomUUID()}`,
    ),
    onSuccess: async () => {
      setFlowId('');
      await queryClient.invalidateQueries({ queryKey: ['task-workflow-runs', task.id] });
      toast({ title: 'Workflow aplicado', description: 'A execução ficou vinculada a esta tarefa.' });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Não foi possível aplicar o workflow',
      description: error instanceof Error ? error.message : 'Tente novamente.',
    }),
  });

  return (
    <section className="rounded-lg border bg-card p-4">
      <div className="flex items-center gap-2"><Workflow className="h-4 w-4 text-primary" /><h3 className="text-sm font-semibold">Workflow da tarefa</h3></div>
      {!canManage ? (
        <p className="mt-2 text-xs text-muted-foreground">Você precisa das permissões de edição de tarefas e gestão de automações.</p>
      ) : !task.matterId ? (
        <p className="mt-2 text-xs text-muted-foreground">Disponível apenas para tarefas vinculadas a um Caso 360.</p>
      ) : (
        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <Select value={flowId} onValueChange={setFlowId}>
            <SelectTrigger aria-label="Selecionar workflow"><SelectValue placeholder="Selecione pelo nome" /></SelectTrigger>
            <SelectContent>{options.map((flow) => <SelectItem key={flow.id} value={flow.id}>{flow.name}</SelectItem>)}</SelectContent>
          </Select>
          <Button disabled={!flowId || apply.isPending} onClick={() => apply.mutate()}>
            {apply.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Aplicar
          </Button>
        </div>
      )}
      {(runs.data ?? []).length > 0 && (
        <div className="mt-4 space-y-2">
          {runs.data!.map((run) => (
            <div key={run.id} className="flex items-center justify-between rounded-md bg-secondary px-3 py-2 text-xs">
              <span className="font-medium">{run.flowName}</span><span className="text-muted-foreground">{statusLabel[run.status] ?? run.status}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
