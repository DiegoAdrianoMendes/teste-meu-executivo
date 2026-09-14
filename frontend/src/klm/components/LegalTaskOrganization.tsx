import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Archive, LoaderCircle, Plus } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  archiveTask,
  assignTaskToList,
  createTaskBoard,
  createTaskBoardColumn,
  createTaskList,
  getTaskOrganization,
  placeTaskOnBoard,
  type CollaborativeTask,
  type TaskBoardPlacement,
} from '@/services/tasks';

interface Props {
  tasks: CollaborativeTask[];
  canEdit: boolean;
  canArchive: boolean;
  onOpen: (task: CollaborativeTask) => void;
}

const statusLabel: Record<CollaborativeTask['status'], string> = {
  pending: 'Pendente',
  in_progress: 'Em andamento',
  blocked: 'Bloqueada',
  done: 'Concluída',
};

export default function LegalTaskOrganization({ tasks, canEdit, canArchive, onOpen }: Props) {
  const queryClient = useQueryClient();
  const organization = useQuery({ queryKey: ['task-organization'], queryFn: getTaskOrganization });
  const [selectedBoardId, setSelectedBoardId] = useState('');
  const [selectedListId, setSelectedListId] = useState('');
  const [newBoardName, setNewBoardName] = useState('');
  const [newListName, setNewListName] = useState('');
  const [newColumnName, setNewColumnName] = useState('');
  const [archiveReason, setArchiveReason] = useState('');

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['task-organization'] }),
      queryClient.invalidateQueries({ queryKey: ['tasks'] }),
      queryClient.invalidateQueries({ queryKey: ['collaborative-tasks'] }),
    ]);
  };
  const createBoardMutation = useMutation({
    mutationFn: () => createTaskBoard({ name: newBoardName.trim() }),
    onSuccess: async (board) => { setSelectedBoardId(board.id); setNewBoardName(''); await refresh(); },
  });
  const createListMutation = useMutation({
    mutationFn: () => createTaskList({ name: newListName.trim() }),
    onSuccess: async (list) => { setSelectedListId(list.id); setNewListName(''); await refresh(); },
  });
  const selectedBoard = organization.data?.boards.find((board) => board.id === selectedBoardId)
    ?? organization.data?.boards[0];
  const selectedList = organization.data?.lists.find((list) => list.id === selectedListId)
    ?? organization.data?.lists[0];
  const createColumnMutation = useMutation({
    mutationFn: () => createTaskBoardColumn(selectedBoard!.id, {
      name: newColumnName.trim(), expectedBoardVersion: selectedBoard!.version,
    }),
    onSuccess: async () => { setNewColumnName(''); await refresh(); },
  });
  const moveMutation = useMutation({
    mutationFn: ({ task, columnId, placement }: { task: CollaborativeTask; columnId: string; placement?: TaskBoardPlacement }) => placeTaskOnBoard({
      taskId: task.id,
      boardId: selectedBoard!.id,
      columnId,
      expectedTaskVersion: task.version,
      expectedPlacementVersion: placement?.version,
    }),
    onSuccess: refresh,
  });
  const listMutation = useMutation({
    mutationFn: ({ task, listId }: { task: CollaborativeTask; listId: string }) => assignTaskToList({
      taskId: task.id, listId, expectedTaskVersion: task.version,
    }),
    onSuccess: refresh,
  });
  const archiveMutation = useMutation({
    mutationFn: (task: CollaborativeTask) => archiveTask(task.id, {
      expectedVersion: task.version, reason: archiveReason.trim(),
    }),
    onSuccess: async () => { setArchiveReason(''); await refresh(); },
  });

  const placements = organization.data?.placements ?? [];
  const membershipTaskIds = useMemo(
    () => new Set((organization.data?.listMemberships ?? [])
      .filter((item) => item.listId === selectedList?.id).map((item) => item.taskId)),
    [organization.data?.listMemberships, selectedList?.id],
  );

  if (organization.isPending) return <div className="grid gap-3 md:grid-cols-3" aria-live="polite"><div className="h-40 animate-pulse rounded-lg bg-muted" /><div className="h-40 animate-pulse rounded-lg bg-muted" /><div className="h-40 animate-pulse rounded-lg bg-muted" /></div>;
  if (organization.isError) return <Alert variant="destructive"><AlertCircle className="h-4 w-4" /><AlertTitle>Organização jurídica indisponível</AlertTitle><AlertDescription>As tarefas continuam acessíveis em lista. Tente carregar os quadros novamente.</AlertDescription></Alert>;

  const taskCard = (task: CollaborativeTask, placement?: TaskBoardPlacement) => (
    <article key={task.id} className="space-y-2 rounded-lg border bg-card p-3" data-testid={`legal-task-card-${task.id}`}>
      <button type="button" className="w-full text-left font-medium hover:underline" onClick={() => onOpen(task)}>{task.title}</button>
      <div className="flex flex-wrap gap-2"><Badge variant="outline">Estado: {statusLabel[task.status]}</Badge>{task.priority && <Badge variant="secondary">{task.priority}</Badge>}</div>
      {canEdit && selectedBoard && selectedBoard.columns.length > 0 && (
        <Select value={placement?.columnId ?? ''} onValueChange={(columnId) => moveMutation.mutate({ task, columnId, placement })}>
          <SelectTrigger aria-label={`Mover ${task.title} para coluna`}><SelectValue placeholder="Mover para coluna" /></SelectTrigger>
          <SelectContent>{selectedBoard.columns.map((column) => <SelectItem key={column.id} value={column.id}>{column.name}</SelectItem>)}</SelectContent>
        </Select>
      )}
      {canEdit && organization.data!.lists.length > 0 && (
        <Select onValueChange={(listId) => listMutation.mutate({ task, listId })}>
          <SelectTrigger aria-label={`Adicionar ${task.title} a lista`}><SelectValue placeholder="Adicionar a uma lista" /></SelectTrigger>
          <SelectContent>{organization.data!.lists.map((list) => <SelectItem key={list.id} value={list.id}>{list.name}</SelectItem>)}</SelectContent>
        </Select>
      )}
      {canArchive && <Button size="sm" variant="ghost" disabled={archiveReason.trim().length < 8 || archiveMutation.isPending} onClick={() => archiveMutation.mutate(task)}><Archive className="mr-2 h-4 w-4" />Arquivar</Button>}
    </article>
  );

  return (
    <section className="space-y-4" data-testid="legal-task-organization">
      <div className="rounded-lg border bg-card p-4">
        <h2 className="font-semibold">Organização jurídica</h2>
        <p className="text-sm text-muted-foreground">Listas e colunas organizam o trabalho. O estado jurídico da tarefa permanece independente.</p>
        {canArchive && <div className="mt-3 max-w-xl space-y-1"><Label htmlFor="task-archive-reason">Motivo para arquivar</Label><Input id="task-archive-reason" value={archiveReason} onChange={(event) => setArchiveReason(event.target.value)} placeholder="Informe ao menos 8 caracteres" /></div>}
      </div>
      <Tabs defaultValue="boards">
        <TabsList><TabsTrigger value="boards">Quadros</TabsTrigger><TabsTrigger value="lists">Listas</TabsTrigger></TabsList>
        <TabsContent value="boards" className="space-y-4">
          <div className="flex flex-col gap-2 sm:flex-row">
            {organization.data!.boards.length > 0 && <Select value={selectedBoard?.id} onValueChange={setSelectedBoardId}><SelectTrigger className="sm:max-w-xs"><SelectValue /></SelectTrigger><SelectContent>{organization.data!.boards.map((board) => <SelectItem key={board.id} value={board.id}>{board.name}</SelectItem>)}</SelectContent></Select>}
            {canEdit && <><Input value={newBoardName} onChange={(event) => setNewBoardName(event.target.value)} placeholder="Novo quadro" className="sm:max-w-xs" /><Button variant="outline" disabled={newBoardName.trim().length < 1 || createBoardMutation.isPending} onClick={() => createBoardMutation.mutate()}><Plus className="mr-2 h-4 w-4" />Criar quadro</Button></>}
          </div>
          {selectedBoard ? <>
            {canEdit && <div className="flex gap-2"><Input value={newColumnName} onChange={(event) => setNewColumnName(event.target.value)} placeholder="Nova coluna" className="max-w-xs" /><Button variant="outline" disabled={newColumnName.trim().length < 1 || createColumnMutation.isPending} onClick={() => createColumnMutation.mutate()}>Adicionar coluna</Button></div>}
            <div className="grid min-w-max gap-3" style={{ gridTemplateColumns: `repeat(${Math.max(selectedBoard.columns.length, 1)}, minmax(260px, 1fr))` }}>
              {selectedBoard.columns.length === 0 ? <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">Crie a primeira coluna do quadro.</p> : selectedBoard.columns.map((column) => <section key={column.id} className="min-h-40 space-y-2 rounded-lg bg-muted/40 p-3"><h3 className="font-medium">{column.name}</h3>{tasks.filter((task) => placements.some((item) => item.boardId === selectedBoard.id && item.columnId === column.id && item.taskId === task.id)).map((task) => taskCard(task, placements.find((item) => item.boardId === selectedBoard.id && item.taskId === task.id)))}</section>)}
            </div>
            {tasks.some((task) => !placements.some((item) => item.boardId === selectedBoard.id && item.taskId === task.id)) && <section className="space-y-2"><h3 className="text-sm font-medium">Sem coluna neste quadro</h3><div className="grid gap-2 lg:grid-cols-2">{tasks.filter((task) => !placements.some((item) => item.boardId === selectedBoard.id && item.taskId === task.id)).map((task) => taskCard(task))}</div></section>}
          </> : <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">Nenhum quadro criado.</p>}
        </TabsContent>
        <TabsContent value="lists" className="space-y-4">
          <div className="flex flex-col gap-2 sm:flex-row">
            {organization.data!.lists.length > 0 && <Select value={selectedList?.id} onValueChange={setSelectedListId}><SelectTrigger className="sm:max-w-xs"><SelectValue /></SelectTrigger><SelectContent>{organization.data!.lists.map((list) => <SelectItem key={list.id} value={list.id}>{list.name}</SelectItem>)}</SelectContent></Select>}
            {canEdit && <><Input value={newListName} onChange={(event) => setNewListName(event.target.value)} placeholder="Nova lista" className="sm:max-w-xs" /><Button variant="outline" disabled={newListName.trim().length < 1 || createListMutation.isPending} onClick={() => createListMutation.mutate()}><Plus className="mr-2 h-4 w-4" />Criar lista</Button></>}
          </div>
          {selectedList ? <div className="grid gap-2 lg:grid-cols-2">{tasks.filter((task) => membershipTaskIds.has(task.id)).map((task) => taskCard(task))}{membershipTaskIds.size === 0 && <p className="text-sm text-muted-foreground">Esta lista ainda não possui tarefas.</p>}</div> : <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">Nenhuma lista criada.</p>}
        </TabsContent>
      </Tabs>
      {(moveMutation.isPending || listMutation.isPending) && <p className="flex items-center text-sm text-muted-foreground" aria-live="polite"><LoaderCircle className="mr-2 h-4 w-4 animate-spin" />Salvando organização…</p>}
    </section>
  );
}
