import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { AlertTriangle, Bell, BellOff, CheckCircle2, Clock3, FileUp, History, Loader2, MessageSquareText, Plus, Save, Tag } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { klmApi, sha256File } from '@/klm/api';
import { KlmPill, KlmProgress } from '@/klm/components/Primitives';
import { TaskCollaborationDrawer } from '@/klm/components/TaskCollaborationDrawer';

type WorkspaceCard = {
  id: string;
  name: string;
  owner: string;
  priority: string;
  urgency: string;
  tags: string[];
  nextAction: string | null;
  nextDeadline: string | null;
  docs: number;
  documentScore: number;
  openTasks: number;
  stage: string;
  threadId?: string | null;
};

const WATCHED_KEY = 'klm-funnel-watched-cards';

function watchedCards() {
  try { return new Set<string>(JSON.parse(localStorage.getItem(WATCHED_KEY) || '[]')); }
  catch { return new Set<string>(); }
}

export function FunnelCardWorkspaceSheet({
  card,
  open,
  onOpenChange,
  stageLabel,
  stageOptions,
  onStageChange,
  movePending,
  canEdit,
}: {
  card: WorkspaceCard | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  stageLabel: string;
  stageOptions: Array<{ id: string; label: string }>;
  onStageChange: (stage: string) => void;
  movePending: boolean;
  canEdit: boolean;
}) {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [tags, setTags] = useState('');
  const [priority, setPriority] = useState('normal');
  const [urgency, setUrgency] = useState('normal');
  const [nextAction, setNextAction] = useState('');
  const [nextDeadline, setNextDeadline] = useState('');
  const [taskTitle, setTaskTitle] = useState('');
  const [internalNote, setInternalNote] = useState('');
  const [watched, setWatched] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);

  const matterQuery = useQuery({
    queryKey: ['klm', 'matter', card?.id],
    queryFn: () => klmApi.matter(card!.id),
    enabled: open && Boolean(card?.id),
  });
  const tasksQuery = useQuery({
    queryKey: ['klm', 'matter-tasks', card?.id],
    queryFn: () => klmApi.matterTasks(card!.id),
    enabled: open && Boolean(card?.id),
  });

  useEffect(() => {
    if (!card) return;
    const matter = matterQuery.data;
    setTags((matter?.tags ?? card.tags).join(', '));
    setPriority(matter?.priority ?? card.priority ?? 'normal');
    setUrgency(matter?.urgency ?? card.urgency ?? 'normal');
    setNextAction(matter?.nextAction ?? card.nextAction ?? '');
    setNextDeadline((matter?.nextDeadline ?? card.nextDeadline ?? '').slice(0, 16));
    setWatched(watchedCards().has(card.id));
  }, [card, matterQuery.data]);

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] }),
      queryClient.invalidateQueries({ queryKey: ['klm', 'matter', card?.id] }),
      queryClient.invalidateQueries({ queryKey: ['klm', 'matter-tasks', card?.id] }),
    ]);
  };

  const saveMutation = useMutation({
    mutationFn: () => klmApi.updateMatter(card!.id, {
      tags: tags.split(',').map((item) => item.trim()).filter(Boolean),
      priority,
      urgency,
      nextAction: nextAction.trim() || null,
      nextDeadline: nextDeadline ? new Date(nextDeadline).toISOString() : null,
    }),
    onSuccess: invalidate,
  });
  const createTaskMutation = useMutation({
    mutationFn: () => klmApi.createMatterTask(card!.id, {
      title: taskTitle.trim(),
      priority: priority === 'alta' ? 'alta' : priority === 'baixa' ? 'baixa' : 'media',
      idempotencyKey: `funnel-checklist:${crypto.randomUUID()}`,
    }),
    onSuccess: async () => { setTaskTitle(''); await invalidate(); },
  });
  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      const checksumSha256 = await sha256File(file);
      const prepared = await klmApi.createDocumentUpload(card!.id, {
        name: file.name,
        checksumSha256,
        docType: 'documento',
        status: 'pendente',
        fileSize: String(file.size),
        mimeType: file.type || 'application/octet-stream',
      });
      return klmApi.completeDocumentUpload(card!.id, prepared, file);
    },
    onSuccess: invalidate,
  });
  const noteMutation = useMutation({
    mutationFn: () => klmApi.addMatterNote(card!.id, internalNote.trim()),
    onSuccess: async () => { setInternalNote(''); await invalidate(); },
  });

  const tasks = tasksQuery.data ?? matterQuery.data?.tasks ?? [];
  const doneTasks = tasks.filter((task) => task.status === 'done' || task.status === 'concluida' || task.status === 'completed').length;
  const progress = tasks.length ? (doneTasks / tasks.length) * 100 : 0;
  const timeline = useMemo(() => [
    ...(matterQuery.data?.timeline ?? []).map((item) => ({ id: item.id, title: item.title, detail: item.description, at: item.timestamp })),
    ...(matterQuery.data?.fieldChanges ?? []).map((item) => ({ id: item.id, title: item.summary || 'Dados do card atualizados', detail: item.fieldName, at: item.changedAt })),
  ].sort((a, b) => String(b.at).localeCompare(String(a.at))), [matterQuery.data]);

  const toggleWatch = () => {
    if (!card) return;
    const current = watchedCards();
    if (current.has(card.id)) current.delete(card.id); else current.add(card.id);
    localStorage.setItem(WATCHED_KEY, JSON.stringify([...current]));
    setWatched(current.has(card.id));
  };

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex h-dvh w-[96vw] max-w-none flex-col gap-0 overflow-hidden p-0 sm:max-w-[720px]">
        <SheetHeader className="border-b px-6 py-5 pr-14 text-left">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0"><SheetTitle className="truncate">{card?.name ?? 'Card'}</SheetTitle><SheetDescription>{stageLabel} · operação do lead sem sair do Kanban</SheetDescription></div>
            <Button variant="outline" size="sm" className="shrink-0" onClick={toggleWatch}>{watched ? <BellOff className="mr-1.5 h-3.5 w-3.5" /> : <Bell className="mr-1.5 h-3.5 w-3.5" />}{watched ? 'Deixar de observar' : 'Observar'}</Button>
          </div>
        </SheetHeader>
        <Tabs defaultValue="resumo" className="flex min-h-0 flex-1 flex-col">
          <TabsList className="mx-6 mt-4 grid h-10 grid-cols-4 bg-secondary p-1">
            <TabsTrigger value="resumo">Resumo</TabsTrigger><TabsTrigger value="checklist">Checklist</TabsTrigger><TabsTrigger value="arquivos">Arquivos</TabsTrigger><TabsTrigger value="historico">Histórico</TabsTrigger>
          </TabsList>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6">
            <TabsContent value="resumo" className="mt-5 space-y-5">
              <label className="block space-y-1.5 text-xs font-semibold">
                Etapa
                <Select
                  value={card?.stage}
                  onValueChange={onStageChange}
                  disabled={!canEdit || movePending}
                >
                  <SelectTrigger aria-label="Mover lead para etapa"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {stageOptions.map((stage) => <SelectItem key={stage.id} value={stage.id}>{stage.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </label>
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="space-y-1.5 text-xs font-semibold">Prioridade<Select value={priority} onValueChange={setPriority} disabled={!canEdit}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="baixa">Baixa</SelectItem><SelectItem value="normal">Normal</SelectItem><SelectItem value="alta">Alta</SelectItem><SelectItem value="urgente">Urgente</SelectItem></SelectContent></Select></label>
                <label className="space-y-1.5 text-xs font-semibold">Urgência<Select value={urgency} onValueChange={setUrgency} disabled={!canEdit}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="baixa">Baixa</SelectItem><SelectItem value="normal">Normal</SelectItem><SelectItem value="alta">Alta</SelectItem><SelectItem value="critica">Crítica</SelectItem></SelectContent></Select></label>
              </div>
              <label className="block space-y-1.5 text-xs font-semibold"><span className="flex items-center gap-1.5"><Tag className="h-3.5 w-3.5" />Etiquetas</span><Input value={tags} onChange={(event) => setTags(event.target.value)} disabled={!canEdit} placeholder="saúde, urgente, indicação" /><span className="block font-normal text-muted-foreground">Separe as etiquetas por vírgula.</span></label>
              <label className="block space-y-1.5 text-xs font-semibold">Próxima ação<Input value={nextAction} onChange={(event) => setNextAction(event.target.value)} disabled={!canEdit} /></label>
              <label className="block space-y-1.5 text-xs font-semibold">Prazo<Input type="datetime-local" value={nextDeadline} onChange={(event) => setNextDeadline(event.target.value)} disabled={!canEdit} /></label>
              <div className="grid grid-cols-3 gap-px overflow-hidden border bg-border text-center"><div className="bg-background p-3"><strong className="block text-lg">{tasks.length}</strong><span className="text-2xs text-muted-foreground">itens</span></div><div className="bg-background p-3"><strong className="block text-lg">{matterQuery.data?.documents.length ?? card?.docs ?? 0}</strong><span className="text-2xs text-muted-foreground">arquivos</span></div><div className="bg-background p-3"><strong className="block truncate text-sm">{card?.owner || 'Sem responsável'}</strong><span className="text-2xs text-muted-foreground">responsável</span></div></div>
              <div className="space-y-2 border-t pt-4"><div className="flex items-center gap-2"><MessageSquareText className="h-4 w-4 text-[#0057d8]" /><strong className="text-xs">Comentário interno</strong><KlmPill tone="muted">não enviado ao cliente</KlmPill></div><Textarea value={internalNote} onChange={(event) => setInternalNote(event.target.value)} placeholder="Registre uma decisão, contexto ou orientação para a equipe." className="min-h-20" /><Button variant="outline" size="sm" disabled={!internalNote.trim() || noteMutation.isPending} onClick={() => noteMutation.mutate()}>{noteMutation.isPending ? 'Salvando...' : 'Salvar comentário'}</Button></div>
              <Button disabled={!canEdit || saveMutation.isPending} onClick={() => saveMutation.mutate()}><Save className="mr-2 h-4 w-4" />{saveMutation.isPending ? 'Salvando...' : 'Salvar alterações'}</Button>
            </TabsContent>
            <TabsContent value="checklist" className="mt-5 space-y-4">
              <div><div className="flex items-center justify-between text-xs"><strong>{doneTasks} de {tasks.length} concluídos</strong><span>{Math.round(progress)}%</span></div><KlmProgress value={progress} tone="green" /></div>
              <div className="divide-y border-y">
                {tasks.map((task) => {
                  const done = task.status === 'done' || task.status === 'concluida' || task.status === 'completed';
                  return (
                    <button
                      key={task.id}
                      type="button"
                      className="flex w-full items-start gap-3 py-3 text-left text-sm hover:bg-secondary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      onClick={() => setSelectedTaskId(task.id)}
                    >
                      <span className="mt-0.5 text-2xs font-semibold text-muted-foreground">{done ? 'Concluída' : 'Abrir'}</span>
                      <span className="min-w-0 flex-1">
                        <strong className={`block text-xs ${done ? 'text-muted-foreground line-through' : ''}`}>{task.title}</strong>
                        <span className="mt-1 flex flex-wrap items-center gap-1.5">
                          {task.dueDate && <span className="text-2xs text-muted-foreground">Prazo: {new Date(task.dueDate).toLocaleString('pt-BR')}</span>}
                          {task.sourceExceptionId && <Link to={`/app/leads?matter=${card?.id ?? ''}`} onClick={(event) => event.stopPropagation()} className="inline-flex items-center gap-0.5 rounded-full border border-destructive/30 bg-destructive/10 px-1.5 py-0 text-2xs font-medium text-destructive hover:bg-destructive/20" title="Esta tarefa nasceu de uma exceção — abrir o caso"><AlertTriangle className="h-2.5 w-2.5 shrink-0" />gerado por falha</Link>}
                        </span>
                      </span>
                      <KlmPill tone={task.priority === 'alta' ? 'red' : 'muted'}>{task.priority || 'normal'}</KlmPill>
                    </button>
                  );
                })}
                {!tasks.length && <p className="py-8 text-center text-xs text-muted-foreground">Nenhum item ainda. Crie o primeiro passo desta etapa.</p>}
              </div>
              <div className="flex gap-2"><Input value={taskTitle} onChange={(event) => setTaskTitle(event.target.value)} placeholder="Adicionar item ao checklist" onKeyDown={(event) => { if (event.key === 'Enter' && taskTitle.trim()) createTaskMutation.mutate(); }} /><Button size="icon" title="Adicionar item" disabled={!canEdit || !taskTitle.trim() || createTaskMutation.isPending} onClick={() => createTaskMutation.mutate()}>{createTaskMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}</Button></div>
            </TabsContent>
            <TabsContent value="arquivos" className="mt-5 space-y-4">
              <input ref={fileRef} type="file" className="sr-only" multiple onChange={(event) => { const files = [...(event.target.files ?? [])]; files.forEach((file) => uploadMutation.mutate(file)); event.target.value = ''; }} />
              <button type="button" disabled={!canEdit || uploadMutation.isPending} onClick={() => fileRef.current?.click()} className="flex w-full items-center justify-center gap-2 border border-dashed p-6 text-sm font-semibold hover:border-primary hover:bg-primary/5 disabled:opacity-50"><FileUp className="h-4 w-4" />{uploadMutation.isPending ? 'Enviando arquivo...' : 'Adicionar documentos'}</button>
              <div className="divide-y border-y">{matterQuery.data?.documents.map((document) => <div key={document.id} className="flex items-center gap-3 py-3"><CheckCircle2 className="h-4 w-4 text-emerald-600" /><span className="min-w-0 flex-1"><strong className="block truncate text-xs">{document.name}</strong><span className="text-2xs text-muted-foreground">{document.type || 'documento'} · {new Date(document.createdAt).toLocaleDateString('pt-BR')}</span></span><KlmPill tone={document.status === 'completo' ? 'green' : 'gold'}>{document.status}</KlmPill></div>)}{!matterQuery.isPending && !matterQuery.data?.documents.length && <p className="py-8 text-center text-xs text-muted-foreground">Nenhum documento vinculado.</p>}</div>
            </TabsContent>
            <TabsContent value="historico" className="mt-5">
              <div className="space-y-0">{timeline.map((item) => <div key={item.id} className="grid grid-cols-[20px_1fr] gap-3 border-l pb-5 pl-4"><History className="-ml-[25px] h-4 w-4 bg-background text-[#0057d8]" /><div><strong className="block text-xs">{item.title}</strong>{item.detail && <p className="mt-1 text-2xs text-muted-foreground">{item.detail}</p>}<span className="mt-1 flex items-center gap-1 text-2xs text-muted-foreground"><Clock3 className="h-3 w-3" />{item.at ? new Date(item.at).toLocaleString('pt-BR') : 'sem data'}</span></div></div>)}{!timeline.length && !matterQuery.isPending && <p className="py-8 text-center text-xs text-muted-foreground">A movimentação deste card aparecerá aqui.</p>}</div>
            </TabsContent>
          </div>
        </Tabs>
      </SheetContent>
      </Sheet>
      <TaskCollaborationDrawer
        taskId={selectedTaskId}
        contextLabels={{
          clientLabel: matterQuery.data?.client.name ?? card?.name,
          matterLabel: matterQuery.data?.demandType ?? stageLabel,
        }}
        open={Boolean(selectedTaskId)}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) setSelectedTaskId(null);
        }}
      />
    </>
  );
}
