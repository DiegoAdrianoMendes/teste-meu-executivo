import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronRight, Link2, LoaderCircle, Plus, Tag, Trash2 } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  addTaskDependency,
  createTaskSubtask,
  getTaskLegalResources,
  listTaskLabelCatalog,
  listTaskDependencyCandidates,
  removeTaskDependency,
  replaceTaskLabels,
  type CollaborativeTask,
} from '@/services/tasks';

const labelPalette = ['#2563EB', '#0F766E', '#7C3AED', '#B45309', '#BE123C'];

interface Props {
  task: CollaborativeTask;
  canEdit: boolean;
  onConflict: (message: string) => void;
  onOpenTask?: (task: CollaborativeTask) => void;
}

export function TaskLegalResourcesSection({ task, canEdit, onConflict, onOpenTask }: Props) {
  const queryClient = useQueryClient();
  const [labelName, setLabelName] = useState('');
  const [catalogLabelHandle, setCatalogLabelHandle] = useState('');
  const [subtaskTitle, setSubtaskTitle] = useState('');
  const [predecessorRef, setPredecessorRef] = useState('');
  const resources = useQuery({
    queryKey: ['task-legal-resources', task.id],
    queryFn: () => getTaskLegalResources(task.id),
    retry: false,
  });
  const dependencyCandidates = useQuery({
    queryKey: ['task-dependency-candidates', task.id],
    queryFn: () => listTaskDependencyCandidates(task.id),
    enabled: canEdit,
    retry: false,
  });
  const labelCatalog = useQuery({
    queryKey: ['task-label-catalog'],
    queryFn: listTaskLabelCatalog,
    enabled: canEdit,
    staleTime: 60_000,
    retry: false,
  });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['task-legal-resources', task.id] }),
      queryClient.invalidateQueries({ queryKey: ['collaborative-task', task.id] }),
      queryClient.invalidateQueries({ queryKey: ['collaborative-tasks'] }),
    ]);
  };
  const fail = (error: unknown) => {
    onConflict(error instanceof Error ? error.message : 'A alteração não pôde ser concluída. Atualize a tarefa e tente novamente.');
  };
  const labelsMutation = useMutation({
    mutationFn: (labels: Array<{ name: string; color: string }>) => replaceTaskLabels(task.id, task.version, labels, 'Etiquetas atualizadas no painel jurídico'),
    onSuccess: refresh,
    onError: fail,
  });
  const subtaskMutation = useMutation({
    mutationFn: (title: string) => createTaskSubtask(task.id, task.version, title),
    onSuccess: async () => { setSubtaskTitle(''); await refresh(); },
    onError: fail,
  });
  const dependencyMutation = useMutation({
    mutationFn: (id: string) => addTaskDependency(task.id, task.version, id, 'Dependência adicionada no painel jurídico'),
    onSuccess: async () => { setPredecessorRef(''); await refresh(); },
    onError: fail,
  });
  const removalMutation = useMutation({
    mutationFn: (id: string) => removeTaskDependency(task.id, id, task.version, 'Dependência removida no painel jurídico'),
    onSuccess: refresh,
    onError: fail,
  });
  const busy = labelsMutation.isPending || subtaskMutation.isPending || dependencyMutation.isPending || removalMutation.isPending;

  if (resources.isLoading) return <p className="text-sm text-muted-foreground">Carregando recursos jurídicos…</p>;
  if (resources.isError || !resources.data) {
    return <Alert><AlertTitle>Recursos jurídicos indisponíveis</AlertTitle><AlertDescription>A tarefa continua acessível, mas etiquetas, subtarefas e vínculos não puderam ser carregados.</AlertDescription></Alert>;
  }

  const data = resources.data;
  return (
    <div className="space-y-5">
      <section className="space-y-3 rounded-lg border p-4">
        <div className="flex items-center gap-2"><Tag className="h-4 w-4 text-primary" /><h3 className="text-sm font-semibold">Etiquetas</h3></div>
        <div className="flex flex-wrap gap-2">
          {data.labels.length === 0 && <span className="text-xs text-muted-foreground">Nenhuma etiqueta.</span>}
          {data.labels.map((item) => (
            <span key={item.id} className="inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs">
              <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: item.color }} />{item.name}
              {canEdit && <button aria-label={`Remover etiqueta ${item.name}`} onClick={() => labelsMutation.mutate(data.labels.filter((label) => label.id !== item.id).map(({ name, color }) => ({ name, color })))}><Trash2 className="h-3 w-3" /></button>}
            </span>
          ))}
        </div>
        {canEdit && (
          <div className="space-y-2">
            <div className="flex gap-2">
              <Select value={catalogLabelHandle} onValueChange={setCatalogLabelHandle} disabled={labelCatalog.isLoading || labelCatalog.isError}>
                <SelectTrigger aria-label="Selecionar etiqueta do catálogo"><SelectValue placeholder={labelCatalog.isLoading ? 'Carregando catálogo…' : 'Reutilizar etiqueta'} /></SelectTrigger>
                <SelectContent>
                  {(labelCatalog.data ?? []).filter((candidate) => !data.labels.some((label) => label.id === candidate.id)).map((candidate) => (
                    <SelectItem key={candidate.id} value={candidate.handle}>
                      <span className="inline-flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: candidate.color }} />{candidate.name}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button variant="outline" disabled={busy || !catalogLabelHandle} onClick={() => {
                const selected = labelCatalog.data?.find((candidate) => candidate.handle === catalogLabelHandle);
                if (!selected) return;
                labelsMutation.mutate([...data.labels.map(({ name, color }) => ({ name, color })), { name: selected.name, color: selected.color }]);
                setCatalogLabelHandle('');
              }}>Aplicar</Button>
            </div>
            {labelCatalog.isError && <p className="text-xs text-destructive">O catálogo de etiquetas não pôde ser carregado.</p>}
            <div className="flex gap-2"><Input value={labelName} onChange={(event) => setLabelName(event.target.value)} placeholder="Nova etiqueta" /><Button variant="outline" disabled={busy || !labelName.trim()} onClick={() => { labelsMutation.mutate([...data.labels.map(({ name, color }) => ({ name, color })), { name: labelName.trim(), color: labelPalette[data.labels.length % labelPalette.length] }]); setLabelName(''); }}><Plus className="mr-1 h-4 w-4" />Criar</Button></div>
          </div>
        )}
      </section>

      <section className="space-y-3 rounded-lg border p-4">
        <h3 className="text-sm font-semibold">Subtarefas</h3>
        {data.subtasks.length === 0 ? <p className="text-xs text-muted-foreground">Nenhuma subtarefa.</p> : data.subtasks.map((item) => (
          <button
            key={item.id}
            type="button"
            className="flex w-full items-center justify-between rounded-md bg-secondary px-3 py-2 text-left text-sm transition-colors hover:bg-secondary/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => onOpenTask?.(item)}
            disabled={!onOpenTask}
            aria-label={`Abrir subtarefa ${item.title}`}
          >
            <span className="min-w-0 truncate">{item.title}</span>
            <span className="ml-3 inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground">{item.status === 'done' ? 'Concluída' : 'Em aberto'}{onOpenTask && <ChevronRight className="h-3.5 w-3.5" />}</span>
          </button>
        ))}
        {canEdit && <div className="flex gap-2"><Input value={subtaskTitle} onChange={(event) => setSubtaskTitle(event.target.value)} placeholder="Título da subtarefa" /><Button variant="outline" disabled={busy || !subtaskTitle.trim()} onClick={() => subtaskMutation.mutate(subtaskTitle.trim())}><Plus className="mr-1 h-4 w-4" />Criar</Button></div>}
      </section>

      <section className="space-y-3 rounded-lg border p-4">
        <div className="flex items-center gap-2"><Link2 className="h-4 w-4 text-primary" /><h3 className="text-sm font-semibold">Dependências</h3></div>
        {data.dependencies.length === 0 ? <p className="text-xs text-muted-foreground">Nenhuma dependência.</p> : data.dependencies.map((dependency) => {
          return <div key={dependency.id} className="flex items-center justify-between rounded-md bg-secondary px-3 py-2 text-sm"><span>Tarefa relacionada</span>{canEdit && dependency.successorTaskId === task.id && <Button aria-label="Remover dependência" size="icon" variant="ghost" onClick={() => removalMutation.mutate(dependency.id)}><Trash2 className="h-4 w-4" /></Button>}</div>;
        })}
        {canEdit && <div className="space-y-2"><Label>Esta tarefa depende de</Label>{dependencyCandidates.isError && <p className="text-xs text-destructive">Não foi possível carregar as tarefas visíveis deste contexto.</p>}<div className="flex gap-2"><Select value={predecessorRef} onValueChange={setPredecessorRef} disabled={dependencyCandidates.isLoading || dependencyCandidates.isError}><SelectTrigger><SelectValue placeholder={dependencyCandidates.isLoading ? 'Carregando tarefas…' : 'Selecione uma tarefa do mesmo contexto'} /></SelectTrigger><SelectContent>{(dependencyCandidates.data ?? []).map((item) => <SelectItem key={item.handle} value={item.handle}>{item.label}</SelectItem>)}</SelectContent></Select><Button variant="outline" disabled={busy || !predecessorRef} onClick={() => dependencyMutation.mutate(predecessorRef)}>{busy && <LoaderCircle className="mr-1 h-4 w-4 animate-spin" />}Vincular</Button></div></div>}
      </section>

      <section className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border p-4"><h3 className="text-sm font-semibold">Arquivos vinculados</h3><p className="mt-2 text-xs text-muted-foreground">{data.attachments.length} arquivo(s) no histórico da tarefa.</p></div>
        <div className="rounded-lg border p-4"><h3 className="text-sm font-semibold">Menções</h3><p className="mt-2 text-xs text-muted-foreground">{data.mentions.length} menção(ões) registrada(s).</p></div>
      </section>
    </div>
  );
}
