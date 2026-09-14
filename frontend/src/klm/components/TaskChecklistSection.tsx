import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, CheckCircle2, ListChecks, LoaderCircle, Plus } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  addTaskChecklistItem,
  archiveTaskChecklist,
  classifyTaskError,
  createTaskChecklist,
  listTaskChecklists,
  taskErrorMessage,
  updateTaskChecklistItem,
  type CollaborativeTask,
  type TaskChecklist,
} from '@/services/tasks';

interface Props {
  task: CollaborativeTask;
  canEdit: boolean;
  onConflict: (message: string) => void;
}

export function TaskChecklistSection({ task, canEdit, onConflict }: Props) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [title, setTitle] = useState('');
  const [itemByChecklist, setItemByChecklist] = useState<Record<string, string>>({});
  const [archiveTarget, setArchiveTarget] = useState<TaskChecklist | null>(null);
  const [archiveReason, setArchiveReason] = useState('');
  const queryKey = ['task-checklists', task.id];
  const checklistsQuery = useQuery({
    queryKey,
    queryFn: () => listTaskChecklists(task.id),
    retry: false,
  });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey }),
      queryClient.invalidateQueries({ queryKey: ['collaborative-task', task.id] }),
      queryClient.invalidateQueries({ queryKey: ['collaborative-tasks'] }),
      queryClient.invalidateQueries({ queryKey: ['task-events', task.id] }),
    ]);
  };
  const handleError = (error: unknown) => {
    if (classifyTaskError(error) === 'conflict') {
      onConflict(taskErrorMessage(error, 'A tarefa mudou. Recarregue antes de continuar.'));
      void refresh();
      return;
    }
    toast({ variant: 'destructive', title: 'Não foi possível atualizar o checklist', description: taskErrorMessage(error, 'Tente novamente.') });
  };

  const createMutation = useMutation({
    mutationFn: () => createTaskChecklist(task.id, task.version, title.trim()),
    onSuccess: async () => { setTitle(''); await refresh(); },
    onError: handleError,
  });
  const addMutation = useMutation({
    mutationFn: ({ checklistId, label }: { checklistId: string; label: string }) => addTaskChecklistItem(task.id, checklistId, task.version, label),
    onSuccess: async (_, variables) => { setItemByChecklist((current) => ({ ...current, [variables.checklistId]: '' })); await refresh(); },
    onError: handleError,
  });
  const stateMutation = useMutation({
    mutationFn: ({ checklistId, itemId, itemVersion, completed }: { checklistId: string; itemId: string; itemVersion: number; completed: boolean }) => updateTaskChecklistItem(task.id, checklistId, itemId, task.version, itemVersion, completed),
    onSuccess: refresh,
    onError: handleError,
  });
  const archiveMutation = useMutation({
    mutationFn: () => archiveTarget ? archiveTaskChecklist(task.id, archiveTarget.id, task.version, archiveTarget.version, archiveReason.trim()) : Promise.resolve(),
    onSuccess: async () => { setArchiveTarget(null); setArchiveReason(''); await refresh(); },
    onError: handleError,
  });

  if (checklistsQuery.isPending) return <div className="flex min-h-36 items-center justify-center" aria-live="polite"><LoaderCircle className="h-5 w-5 animate-spin text-primary" /><span className="sr-only">Carregando checklists</span></div>;
  if (checklistsQuery.isError) return <Alert><ListChecks className="h-4 w-4" /><AlertTitle>Checklists indisponíveis</AlertTitle><AlertDescription>{taskErrorMessage(checklistsQuery.error, 'Não foi possível consultar os checklists desta tarefa.')}</AlertDescription></Alert>;

  return (
    <div className="space-y-4">
      {canEdit && (
        <div className="flex flex-col gap-2 rounded-lg border bg-card p-4 sm:flex-row">
          <Input aria-label="Nome do checklist" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={240} placeholder="Ex.: Documentos para protocolo" />
          <Button disabled={!title.trim() || createMutation.isPending} onClick={() => createMutation.mutate()}>{createMutation.isPending ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}Criar checklist</Button>
        </div>
      )}
      {(checklistsQuery.data ?? []).length === 0 ? (
        <div className="rounded-lg border border-dashed p-8 text-center"><ListChecks className="mx-auto h-8 w-8 text-muted-foreground" /><p className="mt-3 text-sm font-semibold">Nenhum checklist estruturado</p><p className="mt-1 text-xs text-muted-foreground">Checklists controlam etapas simples; subtarefas continuam sendo tarefas independentes.</p></div>
      ) : checklistsQuery.data?.map((checklist) => {
        const completed = checklist.items.filter((item) => item.completedAt).length;
        const percent = checklist.items.length ? Math.round((completed / checklist.items.length) * 100) : 0;
        return (
          <section key={checklist.id} className="rounded-lg border bg-card p-4">
            <div className="flex items-start justify-between gap-3"><div><h3 className="text-sm font-semibold">{checklist.title}</h3><p className="mt-1 text-xs text-muted-foreground">{completed} de {checklist.items.length} concluídos · {percent}%</p></div>{canEdit && <Button size="icon" variant="ghost" aria-label={`Arquivar ${checklist.title}`} onClick={() => { setArchiveTarget(checklist); setArchiveReason(''); }}><Archive className="h-4 w-4" /></Button>}</div>
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-secondary"><div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${percent}%` }} /></div>
            <div className="mt-4 space-y-2">
              {checklist.items.map((item) => <label key={item.id} className="flex min-h-10 items-center gap-3 rounded-md border px-3 py-2 text-sm"><Checkbox disabled={!canEdit || stateMutation.isPending} checked={Boolean(item.completedAt)} onCheckedChange={(checked) => stateMutation.mutate({ checklistId: checklist.id, itemId: item.id, itemVersion: item.version, completed: checked === true })} /><span className={item.completedAt ? 'text-muted-foreground line-through' : ''}>{item.label}</span>{item.completedAt && <CheckCircle2 className="ml-auto h-4 w-4 text-emerald-600" />}</label>)}
            </div>
            {canEdit && <div className="mt-3 flex flex-col gap-2 sm:flex-row"><Input aria-label={`Novo item em ${checklist.title}`} value={itemByChecklist[checklist.id] ?? ''} onChange={(event) => setItemByChecklist((current) => ({ ...current, [checklist.id]: event.target.value }))} maxLength={500} placeholder="Adicionar item" /><Button variant="outline" disabled={!itemByChecklist[checklist.id]?.trim() || addMutation.isPending} onClick={() => addMutation.mutate({ checklistId: checklist.id, label: itemByChecklist[checklist.id]!.trim() })}>Adicionar</Button></div>}
          </section>
        );
      })}

      <Dialog open={Boolean(archiveTarget)} onOpenChange={(open) => { if (!open) setArchiveTarget(null); }}>
        <DialogContent>
          <DialogHeader><DialogTitle>Arquivar checklist</DialogTitle><DialogDescription>O checklist sairá da operação ativa, mas seus itens e o histórico serão preservados.</DialogDescription></DialogHeader>
          <div className="space-y-2"><Label htmlFor="archive-checklist-reason">Motivo</Label><Textarea id="archive-checklist-reason" value={archiveReason} onChange={(event) => setArchiveReason(event.target.value)} minLength={8} maxLength={500} /></div>
          <DialogFooter><Button variant="outline" onClick={() => setArchiveTarget(null)}>Cancelar</Button><Button variant="destructive" disabled={archiveReason.trim().length < 8 || archiveMutation.isPending} onClick={() => archiveMutation.mutate()}>{archiveMutation.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Arquivar</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
