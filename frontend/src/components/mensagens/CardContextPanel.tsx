import { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, CalendarClock, CheckSquare, Clock, ExternalLink, FileText, Loader2, MessageSquareQuote, Save, UserRound } from 'lucide-react';

import { ClassificationChip } from '@/components/common/ClassificationChip';
import { PriorityBadge } from '@/components/common/PriorityBadge';
import { StatusBadge } from '@/components/common/StatusBadge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Textarea } from '@/components/ui/textarea';
import { useAppStore } from '@/store/app-context';
import { formatBRL } from '@/lib/formatters';
import { formatWhatsAppPhoneDisplay } from '@/lib/whatsapp-phone';
import type {
  ConversationAssignee,
  KanbanColumnId,
  LeadDrawerData,
  MatterCard,
  MicroStatus,
  Priority,
  Urgency,
  ClientClassification,
} from '@/types';

interface CardContextPanelProps {
  contactId: string;
  drawerData: LeadDrawerData | null;
  assignees?: ConversationAssignee[];
  loading?: boolean;
  onBack?: () => void;
  mobile?: boolean;
  onRefresh?: () => void;
  onAddNote?: (body: string) => Promise<void>;
  onMoveStage?: (columnId: string, microStatus?: string | null) => Promise<void>;
  onSaveMatter?: (payload: Partial<MatterCard>) => Promise<void>;
}

const COLUMN_OPTIONS = [
  { value: 'entrada', label: 'Entrada' },
  { value: 'qualificacao', label: 'Qualificação' },
  { value: 'proposta_fechamento', label: 'Proposta & fechamento' },
  { value: 'coleta_dados', label: 'Coleta de dados' },
  { value: 'triagem_juridica', label: 'Triagem jurídica' },
  { value: 'pesquisa_validacao', label: 'Pesquisa & validação' },
  { value: 'redacao_peca', label: 'Redação da peça' },
  { value: 'aprovacao_pendente', label: 'Aprovação pendente' },
  { value: 'pronto_protocolo', label: 'Pronto para protocolo' },
  { value: 'migrar_sistema', label: 'Migrar sistema' },
] as const;

const PRIORITY_OPTIONS: Array<{ value: Priority; label: string }> = [
  { value: 'alta', label: 'Alta' },
  { value: 'media', label: 'Média' },
  { value: 'baixa', label: 'Baixa' },
];

const URGENCY_OPTIONS: Array<{ value: Urgency; label: string }> = [
  { value: 'urgente', label: 'Urgente' },
  { value: 'normal', label: 'Normal' },
  { value: 'baixa', label: 'Baixa' },
];

const CLASSIFICATION_OPTIONS: Array<{ value: ClientClassification; label: string }> = [
  { value: 'Premium', label: 'Premium' },
  { value: 'Forte', label: 'Forte' },
  { value: 'Bom', label: 'Bom' },
  { value: 'Frio', label: 'Frio' },
];

export function CardContextPanel({
  contactId,
  drawerData,
  assignees = [],
  loading = false,
  onBack,
  mobile,
  onRefresh,
  onAddNote,
  onMoveStage,
  onSaveMatter,
}: CardContextPanelProps) {
  const { selectCard } = useAppStore();
  const matter = drawerData?.matter ?? null;
  const [noteText, setNoteText] = useState('');
  const [stageColumnId, setStageColumnId] = useState(matter?.columnId ?? 'entrada');
  const [stageMicroStatus, setStageMicroStatus] = useState(matter?.microStatus ?? '');
  const [savingNote, setSavingNote] = useState(false);
  const [savingStage, setSavingStage] = useState(false);
  const [savingMatter, setSavingMatter] = useState(false);
  const [form, setForm] = useState({
    responsibleHuman: matter?.responsibleHuman ?? '',
    nextAction: matter?.nextAction ?? '',
    executiveSummary: matter?.executiveSummary ?? '',
    strategyNotes: matter?.strategyNotes ?? '',
    priority: matter?.priority ?? 'media',
    urgency: matter?.urgency ?? 'normal',
    classification: matter?.classification ?? 'Bom',
    nextDeadline: matter?.nextDeadline ? matter.nextDeadline.slice(0, 16) : '',
  });

  useEffect(() => {
    setStageColumnId(matter?.columnId ?? 'entrada');
    setStageMicroStatus(matter?.microStatus ?? '');
    setForm({
      responsibleHuman: matter?.responsibleHuman ?? '',
      nextAction: matter?.nextAction ?? '',
      executiveSummary: matter?.executiveSummary ?? '',
      strategyNotes: matter?.strategyNotes ?? '',
      priority: matter?.priority ?? 'media',
      urgency: matter?.urgency ?? 'normal',
      classification: matter?.classification ?? 'Bom',
      nextDeadline: matter?.nextDeadline ? matter.nextDeadline.slice(0, 16) : '',
    });
  }, [matter]);

  const editable = drawerData?.canEditMatter && Boolean(matter);

  const notes = useMemo(() => drawerData?.notes ?? [], [drawerData?.notes]);

  const handleSaveNote = async () => {
    if (!noteText.trim() || !onAddNote) return;
    setSavingNote(true);
    try {
      await onAddNote(noteText.trim());
      setNoteText('');
    } finally {
      setSavingNote(false);
    }
  };

  const handleMoveStage = async () => {
    if (!onMoveStage || !matter) return;
    setSavingStage(true);
    try {
      await onMoveStage(stageColumnId, stageMicroStatus || null);
    } finally {
      setSavingStage(false);
    }
  };

  const handleSaveMatter = async () => {
    if (!onSaveMatter || !matter) return;
    setSavingMatter(true);
    try {
      await onSaveMatter({
        responsibleHuman: form.responsibleHuman || '',
        nextAction: form.nextAction,
        executiveSummary: form.executiveSummary,
        strategyNotes: form.strategyNotes,
        priority: form.priority as Priority,
        urgency: form.urgency as Urgency,
        classification: form.classification as ClientClassification,
        nextDeadline: form.nextDeadline ? new Date(form.nextDeadline).toISOString() : '',
      });
    } finally {
      setSavingMatter(false);
    }
  };

  if (loading) {
    return (
      <div className={`flex h-full items-center justify-center border-l bg-card ${mobile ? 'w-full' : 'w-[420px] shrink-0'}`}>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Carregando contexto do lead...
        </div>
      </div>
    );
  }

  if (!drawerData) {
    return (
      <div className={`flex h-full items-center justify-center border-l bg-card ${mobile ? 'w-full' : 'w-[420px] shrink-0'}`}>
        <div className="text-center text-sm text-muted-foreground">
          Clique na foto do lead para abrir o card lateral com o contexto completo.
        </div>
      </div>
    );
  }

  return (
    <div className={`flex h-full flex-col border-l bg-card ${mobile ? 'w-full' : 'w-[420px] shrink-0'}`}>
      <div className="border-b p-4 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            {onBack && (
              <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0" onClick={onBack}>
                <ArrowLeft className="h-4 w-4" />
              </Button>
            )}
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">{drawerData.contact.clientName}</p>
              <p className="truncate text-xs text-muted-foreground">{formatWhatsAppPhoneDisplay(drawerData.contact.clientPhone)}</p>
            </div>
          </div>
          {matter && (
            <Button variant="outline" size="sm" className="gap-1.5" onClick={() => selectCard(matter.id)}>
              <ExternalLink className="h-3.5 w-3.5" />
              Abrir card
            </Button>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">{drawerData.contact.queueStatus}</Badge>
          {drawerData.contact.assignedUserName && (
            <Badge variant="secondary">{drawerData.contact.assignedUserName}</Badge>
          )}
          {matter && (
            <>
              <StatusBadge status={matter.microStatus} />
              <PriorityBadge priority={matter.priority} />
              <ClassificationChip classification={matter.classification} />
            </>
          )}
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="space-y-5 p-4">
          <section className="space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Resumo do lead</p>
            <div className="rounded-xl border bg-primary/5 p-4">
              <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                <span className="inline-flex items-center gap-1">
                  <UserRound className="h-3.5 w-3.5" />
                  {drawerData.contact.assignedUserName || 'Sem responsável'}
                </span>
                {drawerData.contact.nextFollowUpAt && (
                  <span className="inline-flex items-center gap-1 tabular-nums">
                    <CalendarClock className="h-3.5 w-3.5 shrink-0" />
                    {new Date(drawerData.contact.nextFollowUpAt).toLocaleString('pt-BR')}
                  </span>
                )}
              </div>
              {matter ? (
                <div className="mt-3 space-y-2">
                  <p className="text-sm font-medium">{matter.demandType}</p>
                  {matter.nextAction && <p className="text-sm text-foreground/80">{matter.nextAction}</p>}
                  {matter.claimValue && (
                    <p className="text-sm font-semibold tabular-nums">{formatBRL(matter.claimValue)}</p>
                  )}
                </div>
              ) : (
                <p className="mt-3 text-sm text-muted-foreground">Esta conversa ainda não está vinculada a um card do CRM.</p>
              )}
            </div>
          </section>

          {matter && (
            <>
              <section className="space-y-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Mover etapa</p>
                  {drawerData.canEditMatter && (
                    <Button variant="outline" size="sm" onClick={() => void handleMoveStage()} disabled={savingStage}>
                      {savingStage ? 'Salvando...' : 'Mover card'}
                    </Button>
                  )}
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label>Coluna</Label>
                    <Select value={stageColumnId} onValueChange={(value) => setStageColumnId(value as KanbanColumnId)} disabled={!drawerData.canEditMatter}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {COLUMN_OPTIONS.map((item) => (
                          <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label>Microstatus</Label>
                    <Input value={stageMicroStatus} onChange={(event) => setStageMicroStatus(event.target.value as MicroStatus)} disabled={!drawerData.canEditMatter} />
                  </div>
                </div>
              </section>

              <section className="space-y-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Dados do card</p>
                  {editable && (
                    <Button size="sm" onClick={() => void handleSaveMatter()} disabled={savingMatter}>
                      <Save className="mr-1.5 h-3.5 w-3.5" />
                      {savingMatter ? 'Salvando...' : 'Salvar dados'}
                    </Button>
                  )}
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label>Responsável humano</Label>
                    <Select
                      value={form.responsibleHuman || 'none'}
                      onValueChange={(value) => setForm((current) => ({ ...current, responsibleHuman: value === 'none' ? '' : value }))}
                      disabled={!editable}
                    >
                      <SelectTrigger><SelectValue placeholder="Não definido" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">Não definido</SelectItem>
                        {assignees.map((assignee) => (
                          <SelectItem key={assignee.id} value={assignee.id}>{assignee.name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label>Próximo prazo</Label>
                    <Input type="datetime-local" value={form.nextDeadline} onChange={(event) => setForm((current) => ({ ...current, nextDeadline: event.target.value }))} disabled={!editable} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Prioridade</Label>
                    <Select value={form.priority} onValueChange={(value) => setForm((current) => ({ ...current, priority: value as Priority }))} disabled={!editable}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {PRIORITY_OPTIONS.map((item) => (
                          <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label>Urgência</Label>
                    <Select value={form.urgency} onValueChange={(value) => setForm((current) => ({ ...current, urgency: value as Urgency }))} disabled={!editable}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {URGENCY_OPTIONS.map((item) => (
                          <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5 md:col-span-2">
                    <Label>Classificação</Label>
                    <Select value={form.classification} onValueChange={(value) => setForm((current) => ({ ...current, classification: value as ClientClassification }))} disabled={!editable}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {CLASSIFICATION_OPTIONS.map((item) => (
                          <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5 md:col-span-2">
                    <Label>Próxima ação</Label>
                    <Input value={form.nextAction} onChange={(event) => setForm((current) => ({ ...current, nextAction: event.target.value }))} disabled={!editable} />
                  </div>
                  <div className="space-y-1.5 md:col-span-2">
                    <Label>Resumo executivo</Label>
                    <Textarea value={form.executiveSummary} onChange={(event) => setForm((current) => ({ ...current, executiveSummary: event.target.value }))} className="min-h-[110px]" disabled={!editable} />
                  </div>
                  <div className="space-y-1.5 md:col-span-2">
                    <Label>Estratégia e observações</Label>
                    <Textarea value={form.strategyNotes} onChange={(event) => setForm((current) => ({ ...current, strategyNotes: event.target.value }))} className="min-h-[120px]" disabled={!editable} />
                  </div>
                </div>
              </section>
            </>
          )}

          <section className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Notas internas</p>
              {onRefresh && (
                <Button variant="ghost" size="sm" onClick={onRefresh}>
                  Atualizar
                </Button>
              )}
            </div>
            <div className="space-y-2">
              {notes.map((note) => (
                <div key={note.id} className="rounded-xl border p-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="min-w-0 truncate text-sm font-semibold">{note.authorName || 'Equipe interna'}</p>
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{new Date(note.createdAt).toLocaleString('pt-BR')}</span>
                  </div>
                  <p className="mt-2 whitespace-pre-wrap text-sm text-foreground/85">{note.body}</p>
                </div>
              ))}
              {notes.length === 0 && (
                <div className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">
                  Nenhuma nota interna registrada nesta conversa ainda.
                </div>
              )}
            </div>
            {drawerData.canManageConversation && onAddNote && (
              <div className="space-y-2">
                <Textarea
                  value={noteText}
                  onChange={(event) => setNoteText(event.target.value)}
                  placeholder="Registre contexto para a equipe: objeções, combinados, pendências, próximos passos..."
                  className="min-h-[110px]"
                />
                <div className="flex justify-end">
                  <Button onClick={() => void handleSaveNote()} disabled={savingNote || !noteText.trim()}>
                    <MessageSquareQuote className="mr-1.5 h-4 w-4" />
                    {savingNote ? 'Salvando...' : 'Adicionar nota'}
                  </Button>
                </div>
              </div>
            )}
          </section>

          {!!drawerData.recentMeetings.length && (
            <section className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Reuniões recentes</p>
              <div className="space-y-2">
                {drawerData.recentMeetings.map((meeting) => (
                  <div key={meeting.id} className="rounded-xl border p-3 text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <span className="min-w-0 truncate font-medium">{meeting.title}</span>
                      <Badge variant="outline" className="shrink-0">{meeting.status}</Badge>
                    </div>
                    <p className="mt-1 text-xs tabular-nums text-muted-foreground">{new Date(meeting.startsAt).toLocaleString('pt-BR')}</p>
                  </div>
                ))}
              </div>
            </section>
          )}

          {matter && (
            <>
              {!!matter.documents.length && (
                <section className="space-y-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Documentos</p>
                  <div className="space-y-2">
                    {matter.documents.slice(0, 6).map((document) => (
                      <div key={document.id} className="flex items-center gap-3 rounded-xl border p-3 text-sm">
                        <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-medium">{document.name}</p>
                          <p className="text-xs text-muted-foreground">{document.status}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {!!matter.tasks.length && (
                <section className="space-y-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Tarefas</p>
                  <div className="space-y-2">
                    {matter.tasks.slice(0, 6).map((task) => (
                      <div key={task.id} className="flex items-start gap-3 rounded-xl border p-3 text-sm">
                        <CheckSquare className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-medium">{task.title}</p>
                          <p className="text-xs text-muted-foreground">{task.status}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {!!matter.timeline.length && (
                <section className="space-y-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Timeline operacional</p>
                  <div className="space-y-2">
                    {matter.timeline.slice(0, 6).map((event) => (
                      <div key={event.id} className="rounded-xl border p-3">
                        <div className="flex items-center justify-between gap-2">
                          <p className="min-w-0 truncate text-sm font-medium">{event.title}</p>
                          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                            <Clock className="mr-1 inline h-3 w-3" />
                            {new Date(event.timestamp).toLocaleString('pt-BR')}
                          </span>
                        </div>
                        {event.description && <p className="mt-1 text-sm text-muted-foreground">{event.description}</p>}
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
