import { Link } from 'react-router-dom';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { useAppStore } from '@/store/app-context';
import { useToast } from '@/hooks/use-toast';
import { ApiError } from '@/services/http';
import { StatusBadge } from '@/components/common/StatusBadge';
import { PriorityBadge } from '@/components/common/PriorityBadge';
import { ClassificationChip } from '@/components/common/ClassificationChip';
import { StarsRating } from '@/components/common/StarsRating';
import { DocScoreBar } from '@/components/common/DocScoreBar';
import { ChatBubble } from '@/components/common/ChatBubble';
import { AuditTrailItem } from '@/components/common/AuditTrailItem';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import {
  FileText, CheckSquare, Clock, MessageSquare, BookOpen, History, Lightbulb,
  AlertTriangle, Lock, Bot, User, Calendar, Shield, TrendingUp, Target, Receipt,
  CalendarClock,
} from 'lucide-react';
import { getAgentById } from '@/lib/agent-display';
import type { DocumentStatus } from '@/types';
import { useState } from 'react';
import { ScheduleMessageDialog } from '@/components/cards/ScheduleMessageDialog';
import { formatBRL } from '@/lib/formatters';

const docStatusConfig: Record<DocumentStatus, { label: string; className: string }> = {
  aguardando: { label: 'Aguardando', className: 'bg-[hsl(var(--warning))]/10 text-[hsl(var(--warning))] border-[hsl(var(--warning))]/30' },
  parcial: { label: 'Parcial', className: 'bg-[hsl(var(--info))]/10 text-[hsl(var(--info))] border-[hsl(var(--info))]/30' },
  invalido: { label: 'Inválido', className: 'bg-destructive/10 text-destructive border-destructive/30' },
  completo: { label: 'Completo', className: 'bg-[hsl(var(--success))]/10 text-[hsl(var(--success))] border-[hsl(var(--success))]/30' },
};

/** Selo "gerado por falha" — a task nasceu de uma `LegalException`
 * materializada pela Central de Exceções (ver `Task.sourceExceptionId`,
 * `app/services/exception_center_service.py::_materialize_task_for_exception`).
 * Mesma lista de tarefas de sempre, só reconhece a origem — nunca uma
 * tela nova. O link leva ao caso onde a exceção aconteceu. */
function ExceptionOriginBadge({ matterId }: { matterId: string }) {
  return (
    <Link
      to={`/app/leads?matter=${matterId}`}
      onClick={(event) => event.stopPropagation()}
      className="inline-flex items-center gap-0.5 rounded-full border border-destructive/30 bg-destructive/10 px-1.5 py-0 text-2xs font-medium text-destructive hover:bg-destructive/20"
      title="Esta tarefa nasceu de uma exceção (falha registrada pela Central de Exceções) — abrir o caso"
    >
      <AlertTriangle className="h-2.5 w-2.5 shrink-0" />gerado por falha
    </Link>
  );
}

export function DetailDrawer() {
  const { state, closeDrawer, getSelectedCard, toggleTask, updateCardField, addNote, overrideRun, retryRun, can } = useAppStore();
  const { toast } = useToast();
  const canEditKanban = can('kanban', 'edit');
  // O toggle exige tasks:complete (derivado de kanban.edit só sem grant/denial explícito em tasks).
  const canCompleteTask = can('tasks', 'complete');
  const explainTaskError = (error: unknown): string => {
    if (error instanceof ApiError) {
      const detail = error.detail as { code?: unknown } | null | undefined;
      const code = detail && typeof detail === 'object' && typeof detail.code === 'string' ? detail.code : '';
      if (code === 'TASK_VERSION_CONFLICT') return 'A tarefa mudou em outro lugar; a lista foi atualizada, tente de novo.';
      if (error.status === 403) return 'Sem permissão para concluir tarefas.';
    }
    return error instanceof Error && error.message ? error.message : 'Tente novamente em instantes.';
  };
  const handleToggleTask = (taskId: string) => {
    if (!card) return;
    void Promise.resolve(toggleTask(card.id, taskId)).catch((error: unknown) => {
      toast({ title: 'A tarefa não foi atualizada.', description: explainTaskError(error), variant: 'destructive' });
    });
  };
  const handleRate = (rating: number) => {
    if (!card) return;
    void Promise.resolve(updateCardField(card.id, 'starsRating', rating)).catch((error: unknown) => {
      toast({
        title: 'A avaliação não foi salva.',
        description: error instanceof Error && error.message ? error.message : 'Tente novamente em instantes.',
        variant: 'destructive',
      });
    });
  };
  const card = getSelectedCard();
  const [noteText, setNoteText] = useState('');
  const [savingNote, setSavingNote] = useState(false);
  const [busyRunId, setBusyRunId] = useState<string | null>(null);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [runReview, setRunReview] = useState<{ runId: string; mode: 'rejected' | 'corrected' } | null>(null);
  const [runReviewText, setRunReviewText] = useState('');

  const handleAddNote = async () => {
    if (!card || !noteText.trim() || savingNote || !canEditKanban) return;
    setSavingNote(true);
    try {
      await addNote(card.id, noteText.trim());
      setNoteText('');
    } catch (error) {
      toast({
        title: 'A observação não foi salva.',
        description: error instanceof Error && error.message ? error.message : 'Tente novamente em instantes.',
        variant: 'destructive',
      });
    } finally {
      setSavingNote(false);
    }
  };

  const handleAcceptRun = async (runId: string) => {
    if (!card) return;
    setBusyRunId(runId);
    try {
      await overrideRun(card.id, runId, 'accepted', 'Saída validada por humano.');
    } finally {
      setBusyRunId(null);
    }
  };

  const handleRejectRun = (runId: string) => {
    setRunReviewText('');
    setRunReview({ runId, mode: 'rejected' });
  };

  const handleCorrectRun = (runId: string) => {
    setRunReviewText('');
    setRunReview({ runId, mode: 'corrected' });
  };

  const submitRunReview = async () => {
    if (!card || !runReview || !runReviewText.trim()) return;
    const { runId, mode } = runReview;
    const reviewText = runReviewText.trim();
    setBusyRunId(runId);
    try {
      await overrideRun(
        card.id,
        runId,
        mode,
        reviewText,
        mode === 'corrected' ? { manualCorrection: reviewText } : undefined,
      );
      setRunReview(null);
      setRunReviewText('');
    } finally {
      setBusyRunId(null);
    }
  };

  const handleRetryRun = async (runId: string) => {
    if (!card) return;
    setBusyRunId(runId);
    try {
      await retryRun(card.id, runId);
    } finally {
      setBusyRunId(null);
    }
  };

  return (
    <>
    <Sheet open={state.drawerOpen} onOpenChange={open => { if (!open) closeDrawer(); }}>
      <SheetContent side="right" className="w-full sm:max-w-xl p-0 flex flex-col" style={{ boxShadow: 'var(--shadow-drawer)' }}>
        {card ? (
          <>
            {/* Header */}
            <SheetHeader className="p-4 pb-3 border-b space-y-2">
              <div className="flex items-center justify-between gap-2">
                <SheetTitle className="min-w-0 text-lg font-semibold truncate">{card.client.name}</SheetTitle>
                <div className="flex items-center gap-1.5 shrink-0">
                  <PriorityBadge priority={card.priority} />
                  <ClassificationChip classification={card.classification} />
                </div>
              </div>
              <SheetDescription className="text-xs text-muted-foreground truncate">
                {card.demandType} — {card.client.employer}
              </SheetDescription>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="inline-flex" title={canEditKanban ? undefined : 'Permissão kanban.edit necessária'}>
                  <StarsRating
                    rating={card.starsRating}
                    interactive={canEditKanban}
                    size="md"
                    onRate={handleRate}
                  />
                </span>
                <StatusBadge status={card.microStatus} />
                {card.caseNumber && <Badge variant="outline" className="max-w-[220px] text-xs font-mono tabular-nums"><span className="truncate">{card.caseNumber}</span></Badge>}
                {card.isBlocked && (
                  <Badge variant="outline" className="text-xs bg-destructive/10 text-destructive border-destructive/30 gap-0.5">
                    <Lock className="h-2.5 w-2.5 shrink-0" /> Bloqueado
                  </Badge>
                )}
                {card.needsApproval && (
                  <Badge variant="outline" className="text-xs bg-[hsl(var(--warning))]/10 text-[hsl(var(--warning))] border-[hsl(var(--warning))]/30 gap-0.5">
                    <AlertTriangle className="h-2.5 w-2.5 shrink-0" /> Aprovação
                  </Badge>
                )}
              </div>
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <div className="flex items-center gap-3">
                  <span className="flex items-center gap-1"><User className="h-3 w-3" /> {getAgentById(card.responsibleHuman)?.name}</span>
                  <span className="flex items-center gap-1"><Bot className="h-3 w-3" /> {getAgentById(card.currentAgent)?.name}</span>
                </div>
                <div className="flex items-center gap-2">
                  {card.claimValue && (
                    <span className="text-xs font-semibold tabular-nums text-foreground">
                      {formatBRL(card.claimValue)}
                    </span>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-6 shrink-0 text-xs px-2 gap-1"
                    onClick={() => setScheduleOpen(true)}
                  >
                    <CalendarClock className="h-3 w-3 shrink-0" />
                    Agendar msg
                  </Button>
                </div>
              </div>
            </SheetHeader>
            <ScheduleMessageDialog
              open={scheduleOpen}
              onOpenChange={setScheduleOpen}
              defaultPhone={card.client.phone}
              matterId={card.id}
            />

            {/* Tabs */}
            <Tabs defaultValue="resumo" className="flex-1 flex flex-col min-h-0">
              <TabsList className="w-full justify-start rounded-none border-b bg-transparent h-9 px-2 gap-0 overflow-x-auto">
                {[
                  { value: 'resumo', icon: BookOpen, label: 'Resumo' },
                  { value: 'tarefas', icon: CheckSquare, label: 'Tarefas' },
                  { value: 'andamentos', icon: Clock, label: 'Andamentos' },
                  { value: 'conversas', icon: MessageSquare, label: 'Conversas' },
                  { value: 'documentos', icon: FileText, label: 'Docs' },
                  { value: 'estrategia', icon: Lightbulb, label: 'Estratégia' },
                  { value: 'historico', icon: History, label: 'Histórico' },
                ].map(tab => (
                  <TabsTrigger
                    key={tab.value}
                    value={tab.value}
                    className="shrink-0 text-xs data-[state=active]:shadow-none data-[state=active]:border-b-2 data-[state=active]:border-primary rounded-none gap-0.5 px-2"
                  >
                    <tab.icon className="h-3 w-3" /> {tab.label}
                  </TabsTrigger>
                ))}
              </TabsList>

              <ScrollArea className="flex-1">
                {/* ── RESUMO ── */}
                <TabsContent value="resumo" className="p-4 mt-0 space-y-4">
                  {/* Next action */}
                  <div className="rounded-lg border bg-primary/5 p-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">Próxima Ação</p>
                    <p className="text-sm font-medium">{card.nextAction}</p>
                  </div>

                  {card.executiveSummary && (
                    <div className="rounded-lg border bg-card p-3">
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">Resumo Executivo</p>
                      <p className="text-sm leading-relaxed text-foreground/85">{card.executiveSummary}</p>
                    </div>
                  )}

                  {!!card.knowledgeChunks?.length && (
                    <div className="rounded-lg border bg-card p-3">
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                        Base de Contexto da IA
                      </p>
                      <div className="space-y-2">
                        {card.knowledgeChunks.slice(0, 4).map((chunk) => (
                          <div key={chunk.id} className="rounded-md border border-border/60 bg-muted/30 p-2">
                            <div className="flex items-center justify-between gap-2">
                              <p className="min-w-0 truncate text-sm font-medium text-foreground">
                                {chunk.title || chunk.sourceType}
                              </p>
                              <Badge variant="outline" className="shrink-0 text-xs">
                                {chunk.sourceType}
                              </Badge>
                            </div>
                            <p className="mt-1 text-xs text-muted-foreground leading-relaxed">
                              {chunk.summary || chunk.contentText}
                            </p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Scores */}
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                      <DocScoreBar score={card.docScore} />
                    </div>
                    <div className="space-y-1">
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-muted-foreground">Produção</span>
                        <span className="text-xs font-medium tabular-nums">{card.productionScore}%</span>
                      </div>
                      <div className="w-full h-1.5 rounded-full bg-muted overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all bg-primary"
                          style={{ width: `${card.productionScore}%` }}
                        />
                      </div>
                    </div>
                  </div>

                  {card.cardScore && (
                    <div className="grid grid-cols-2 gap-3">
                      <div className="rounded-lg border bg-card p-3">
                        <p className="text-xs uppercase tracking-wide text-muted-foreground mb-1 flex items-center gap-1">
                          <Target className="h-3 w-3" /> Score Geral
                        </p>
                        <p className="text-2xl font-bold tabular-nums">{card.cardScore.overallScore ?? 0}</p>
                        {card.cardScore.explanation && (
                          <p className="mt-1 text-xs text-muted-foreground">{card.cardScore.explanation}</p>
                        )}
                      </div>
                      <div className="rounded-lg border bg-card p-3">
                        <p className="text-xs uppercase tracking-wide text-muted-foreground mb-1">Pilares</p>
                        <div className="space-y-1 text-xs tabular-nums text-muted-foreground">
                          <div className="flex items-center justify-between"><span>Viabilidade</span><span>{card.cardScore.viabilityScore ?? '—'}</span></div>
                          <div className="flex items-center justify-between"><span>Prova</span><span>{card.cardScore.evidenceScore ?? '—'}</span></div>
                          <div className="flex items-center justify-between"><span>Conversão</span><span>{card.cardScore.conversionScore ?? '—'}</span></div>
                        </div>
                      </div>
                    </div>
                  )}

                  {(card.revenueProjection || card.honorariumEstimate) && (
                    <div className="grid gap-3 md:grid-cols-2">
                      {card.revenueProjection && (
                        <div className="rounded-lg border bg-card p-3">
                          <p className="text-xs uppercase tracking-wide text-muted-foreground mb-2 flex items-center gap-1">
                            <TrendingUp className="h-3 w-3" /> Projeção
                          </p>
                          <div className="space-y-1 text-xs tabular-nums text-muted-foreground">
                            <div className="flex items-center justify-between">
                              <span>Receita projetada</span>
                              <span className="font-medium text-foreground">
                                {card.revenueProjection.projectedRevenue
                                  ? formatBRL(card.revenueProjection.projectedRevenue)
                                  : '—'}
                              </span>
                            </div>
                            <div className="flex items-center justify-between">
                              <span>Chance de êxito</span>
                              <span>{card.revenueProjection.successProbability ?? '—'}%</span>
                            </div>
                            <div className="flex items-center justify-between">
                              <span>Confiança</span>
                              <span>{card.revenueProjection.confidenceLevel ?? '—'}%</span>
                            </div>
                          </div>
                        </div>
                      )}
                      {card.honorariumEstimate && (
                        <div className="rounded-lg border bg-card p-3">
                          <p className="text-xs uppercase tracking-wide text-muted-foreground mb-2 flex items-center gap-1">
                            <Receipt className="h-3 w-3" /> Honorários
                          </p>
                          <div className="space-y-1 text-xs tabular-nums text-muted-foreground">
                            <div className="flex items-center justify-between">
                              <span>Tipo</span>
                              <span>{card.honorariumEstimate.contractType ?? '—'}</span>
                            </div>
                            <div className="flex items-center justify-between">
                              <span>Quota litis</span>
                              <span>{card.honorariumEstimate.quotaLitisPercentage ?? '—'}%</span>
                            </div>
                            <div className="flex items-center justify-between">
                              <span>Total estimado</span>
                              <span className="font-medium text-foreground">
                                {card.honorariumEstimate.totalEstimatedValue
                                  ? formatBRL(card.honorariumEstimate.totalEstimatedValue)
                                  : '—'}
                              </span>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Deadline */}
                  {card.nextDeadline && (
                    <div className="flex items-center gap-2 text-xs">
                      <Calendar className="h-3.5 w-3.5 text-muted-foreground" />
                      <span className="text-muted-foreground">Prazo crítico:</span>
                      <span className="font-semibold tabular-nums">{new Date(card.nextDeadline).toLocaleDateString('pt-BR')}</span>
                    </div>
                  )}

                  {/* Risks */}
                  {card.risks.length > 0 && (
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5 flex items-center gap-1">
                        <AlertTriangle className="h-3 w-3 text-[hsl(var(--warning))]" /> Riscos
                      </p>
                      <ul className="space-y-1">
                        {card.risks.map((risk, i) => (
                          <li key={i} className="text-xs text-muted-foreground pl-2 border-l-2 border-[hsl(var(--warning))]/40">{risk}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Blocks */}
                  {card.blocks.length > 0 && (
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5 flex items-center gap-1">
                        <Lock className="h-3 w-3 text-destructive" /> Bloqueios
                      </p>
                      <ul className="space-y-1">
                        {card.blocks.map((block, i) => (
                          <li key={i} className="text-xs text-muted-foreground pl-2 border-l-2 border-destructive/40">{block}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Quick note */}
                  <div className="space-y-2">
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Observação rápida</p>
                    <div className="flex gap-2">
                      <input
                        type="text"
                        value={noteText}
                        onChange={e => setNoteText(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') void handleAddNote(); }}
                        placeholder={canEditKanban ? 'Adicionar observação...' : 'Sem permissão para anotar neste caso'}
                        className="flex-1 text-xs border rounded-md px-2 py-1.5 bg-background"
                        disabled={!canEditKanban || savingNote}
                        aria-label="Observação rápida"
                      />
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-xs h-7"
                        onClick={() => { void handleAddNote(); }}
                        disabled={!canEditKanban || savingNote || !noteText.trim()}
                        title={canEditKanban ? undefined : 'Permissão kanban.edit necessária'}
                      >
                        {savingNote ? 'Salvando...' : 'Salvar'}
                      </Button>
                    </div>
                  </div>

                  {card.campaignAttribution && (
                    <div className="rounded-lg border bg-card p-3">
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">Origem e Campanha</p>
                      <div className="grid gap-2 text-xs text-muted-foreground md:grid-cols-2">
                        <div><span className="text-foreground font-medium">Lead source:</span> {card.leadSource ?? card.campaignAttribution.source ?? '—'}</div>
                        <div><span className="text-foreground font-medium">Canal:</span> {card.campaignAttribution.channel ?? '—'}</div>
                        <div><span className="text-foreground font-medium">Campanha:</span> {card.campaignAttribution.campaign ?? '—'}</div>
                        <div><span className="text-foreground font-medium">Medium:</span> {card.campaignAttribution.medium ?? '—'}</div>
                      </div>
                    </div>
                  )}

                  {(card.agentRuns?.length ?? 0) > 0 && (
                    <div className="rounded-lg border bg-card p-3 space-y-3">
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground flex items-center gap-1">
                        <Bot className="h-3 w-3" /> Runtime de Agentes
                      </p>
                      <div className="space-y-2">
                        {(card.agentRuns ?? []).map((run) => (
                          <div key={run.id} className="rounded-md border p-2 space-y-2">
                            <div className="flex items-center justify-between gap-2">
                              <div className="min-w-0">
                                <p className="truncate text-sm font-semibold">{run.agentName}</p>
                                <p className="truncate text-xs text-muted-foreground">{run.triggerSource}</p>
                              </div>
                              <Badge variant="outline" className="shrink-0 text-xs">
                                {run.status}
                              </Badge>
                            </div>
                            {(run.outputPayload?.summary || run.errorMessage) && (
                              <p className="text-xs text-muted-foreground">
                                {String(run.outputPayload?.summary ?? run.errorMessage)}
                              </p>
                            )}
                            <div className="flex flex-wrap gap-2">
                              {run.status === 'waiting_human' && (
                                <>
                                  <Button size="sm" variant="outline" className="h-7 text-xs" disabled={busyRunId === run.id} onClick={() => handleAcceptRun(run.id)}>
                                    Aceitar
                                  </Button>
                                  <Button size="sm" variant="outline" className="h-7 text-xs" disabled={busyRunId === run.id} onClick={() => handleCorrectRun(run.id)}>
                                    Corrigir
                                  </Button>
                                  <Button size="sm" variant="outline" className="h-7 text-xs" disabled={busyRunId === run.id} onClick={() => handleRejectRun(run.id)}>
                                    Rejeitar
                                  </Button>
                                </>
                              )}
                              {(run.status === 'failed' || run.status === 'completed' || run.status === 'cancelled') && (
                                <Button size="sm" variant="outline" className="h-7 text-xs" disabled={busyRunId === run.id} onClick={() => handleRetryRun(run.id)}>
                                  Reprocessar
                                </Button>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </TabsContent>

                {/* ── TAREFAS ── */}
                <TabsContent value="tarefas" className="p-4 mt-0 space-y-4">
                  {/* Human tasks */}
                  {(() => {
                    const humanTasks = card.tasks.filter(t => !t.isAiGenerated);
                    const aiTasks = card.tasks.filter(t => t.isAiGenerated);
                    return (
                      <>
                        {humanTasks.length > 0 && (
                          <div>
                            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2 flex items-center gap-1">
                              <User className="h-3 w-3 shrink-0" /> <span className="tabular-nums">Tarefas Humanas ({humanTasks.length})</span>
                            </p>
                            <div className="space-y-1.5">
                              {humanTasks.map(task => {
                                const assignee = getAgentById(task.assignedTo);
                                return (
                                  <div key={task.id} className="flex items-start gap-2 p-2 rounded-lg border bg-card hover:bg-muted/50 transition-colors">
                                    <Checkbox
                                      checked={task.status === 'done'}
                                      onCheckedChange={() => handleToggleTask(task.id)}
                                      disabled={!canCompleteTask || task.status === 'done'}
                                      title={!canCompleteTask
                                        ? 'Permissão tasks.complete necessária'
                                        : task.status === 'done'
                                          ? 'Concluída; reabertura só pela página de tarefas'
                                          : undefined}
                                      aria-label={`${task.status === 'done' ? 'Tarefa concluída' : 'Concluir tarefa'} ${task.title}`}
                                      className="mt-0.5"
                                    />
                                    <div className="min-w-0 flex-1">
                                      <p className={`text-sm font-medium ${task.status === 'done' ? 'line-through text-muted-foreground' : ''}`}>{task.title}</p>
                                      <div className="flex flex-wrap items-center gap-2 mt-1 text-xs text-muted-foreground">
                                        <span>{assignee?.name}</span>
                                        {task.dueDate && <span>• {new Date(task.dueDate).toLocaleDateString('pt-BR')}</span>}
                                        {task.priority && <PriorityBadge priority={task.priority} />}
                                        {task.sourceExceptionId && <ExceptionOriginBadge matterId={card.id} />}
                                      </div>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        )}
                        {aiTasks.length > 0 && (
                          <div>
                            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2 flex items-center gap-1">
                              <Bot className="h-3 w-3 shrink-0" /> <span className="tabular-nums">Tarefas de IA ({aiTasks.length})</span>
                            </p>
                            <div className="space-y-1.5">
                              {aiTasks.map(task => {
                                const assignee = getAgentById(task.assignedTo);
                                return (
                                  <div key={task.id} className="flex items-start gap-2 p-2 rounded-lg border bg-[hsl(var(--info))]/5 hover:bg-[hsl(var(--info))]/10 transition-colors">
                                    <Checkbox
                                      checked={task.status === 'done'}
                                      onCheckedChange={() => handleToggleTask(task.id)}
                                      disabled={!canCompleteTask || task.status === 'done'}
                                      title={!canCompleteTask
                                        ? 'Permissão tasks.complete necessária'
                                        : task.status === 'done'
                                          ? 'Concluída; reabertura só pela página de tarefas'
                                          : undefined}
                                      aria-label={`${task.status === 'done' ? 'Tarefa concluída' : 'Concluir tarefa'} ${task.title}`}
                                      className="mt-0.5"
                                    />
                                    <div className="min-w-0 flex-1">
                                      <p className={`text-sm font-medium ${task.status === 'done' ? 'line-through text-muted-foreground' : ''}`}>{task.title}</p>
                                      <div className="flex flex-wrap items-center gap-2 mt-1 text-xs text-muted-foreground">
                                        <Badge variant="secondary" className="text-2xs h-4 px-1 gap-0.5"><Bot className="h-2 w-2 shrink-0" /> {assignee?.name}</Badge>
                                        {task.dueDate && <span>• {new Date(task.dueDate).toLocaleDateString('pt-BR')}</span>}
                                        {task.priority && <PriorityBadge priority={task.priority} />}
                                        {task.sourceExceptionId && <ExceptionOriginBadge matterId={card.id} />}
                                      </div>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        )}
                        {card.tasks.length === 0 && <p className="text-xs text-muted-foreground text-center py-8">Nenhuma tarefa</p>}
                      </>
                    );
                  })()}
                </TabsContent>

                {/* ── ANDAMENTOS ── */}
                <TabsContent value="andamentos" className="p-4 mt-0 space-y-3">
                  {card.timeline.map(event => {
                    const agent = getAgentById(event.agentId);
                    return (
                      <div key={event.id} className="flex gap-3">
                        <div className="w-1.5 rounded-full shrink-0 mt-1" style={{ backgroundColor: agent?.color ?? 'hsl(var(--muted))' }} />
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">{event.title}</p>
                          {event.description && <p className="text-xs text-muted-foreground mt-0.5">{event.description}</p>}
                          <div className="flex items-center gap-2 mt-1">
                            <span className="text-2xs text-muted-foreground">{agent?.name}</span>
                            <span className="text-2xs tabular-nums text-muted-foreground/50">
                              {new Date(event.timestamp).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
                            </span>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                  {card.timeline.length === 0 && <p className="text-xs text-muted-foreground text-center py-8">Nenhum evento registrado</p>}
                </TabsContent>

                {/* ── CONVERSAS ── */}
                <TabsContent value="conversas" className="p-4 mt-0 space-y-3">
                  {card.conversations.map(conv => (
                    <ChatBubble key={conv.id} item={conv} />
                  ))}
                  {card.conversations.length === 0 && <p className="text-xs text-muted-foreground text-center py-8">Nenhuma conversa registrada</p>}
                </TabsContent>

                {/* ── DOCUMENTOS ── */}
                <TabsContent value="documentos" className="p-4 mt-0 space-y-3">
                  {/* Doc score summary */}
                  <DocScoreBar score={card.docScore} />

                  {/* Document list */}
                  <div className="space-y-1.5">
                    {card.documents.map(doc => {
                      const statusConf = docStatusConfig[doc.status];
                      return (
                        <div key={doc.id} className="flex items-center gap-3 p-2.5 rounded-lg border bg-card hover:bg-muted/50 transition-colors">
                          <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
                          <div className="min-w-0 flex-1">
                            <p className="text-sm font-medium truncate">{doc.name}</p>
                            <p className="text-xs tabular-nums text-muted-foreground">{doc.size} • {new Date(doc.createdAt).toLocaleDateString('pt-BR')}</p>
                          </div>
                          <Badge variant="outline" className={`text-2xs px-1.5 py-0 h-[18px] shrink-0 ${statusConf.className}`}>
                            {statusConf.label}
                          </Badge>
                        </div>
                      );
                    })}
                  </div>
                  {card.documents.length === 0 && <p className="text-xs text-muted-foreground text-center py-8">Nenhum documento</p>}
                </TabsContent>

                {/* ── ESTRATÉGIA ── */}
                <TabsContent value="estrategia" className="p-4 mt-0 space-y-4">
                  {card.strategyNotes ? (
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5 flex items-center gap-1">
                        <Shield className="h-3 w-3" /> Tese e Estratégia
                      </p>
                      <div className="text-sm leading-relaxed text-foreground/80 bg-muted/50 rounded-lg p-3 border">
                        {card.strategyNotes}
                      </div>
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground text-center py-4">Nenhuma estratégia definida</p>
                  )}

                  {card.risks.length > 0 && (
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5 flex items-center gap-1">
                        <AlertTriangle className="h-3 w-3 text-[hsl(var(--warning))]" /> Riscos Identificados
                      </p>
                      <ul className="space-y-1.5">
                        {card.risks.map((risk, i) => (
                          <li key={i} className="text-xs text-muted-foreground pl-3 border-l-2 border-[hsl(var(--warning))]/40 py-0.5">{risk}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {card.blocks.length > 0 && (
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5 flex items-center gap-1">
                        <Lock className="h-3 w-3 text-destructive" /> Bloqueios Ativos
                      </p>
                      <ul className="space-y-1.5">
                        {card.blocks.map((block, i) => (
                          <li key={i} className="text-xs text-muted-foreground pl-3 border-l-2 border-destructive/40 py-0.5">{block}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </TabsContent>

                {/* ── HISTÓRICO ── */}
                <TabsContent value="historico" className="p-4 mt-0">
                  {(card.auditLogs.length > 0 || (card.fieldChanges?.length ?? 0) > 0 || (card.humanOverrides?.length ?? 0) > 0) ? (
                    <div className="space-y-0">
                      {card.auditLogs.map(log => (
                        <AuditTrailItem key={log.id} log={log} />
                      ))}
                      {(card.fieldChanges ?? []).map(change => (
                        <div key={change.id} className="border-b last:border-b-0 py-3">
                          <p className="text-sm font-medium">{change.summary ?? `Campo ${change.fieldName} atualizado`}</p>
                          <p className="text-xs text-muted-foreground mt-1">
                            Origem: {change.changeSource ?? 'system'} • Ator: {change.actorType}
                          </p>
                        </div>
                      ))}
                      {(card.humanOverrides ?? []).map((override) => (
                        <div key={override.id} className="border-b last:border-b-0 py-3">
                          <p className="text-sm font-medium">Override humano: {override.decision}</p>
                          {override.notes && <p className="text-xs text-muted-foreground mt-1">{override.notes}</p>}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground text-center py-8">Nenhum registro no histórico</p>
                  )}
                </TabsContent>
              </ScrollArea>
            </Tabs>
          </>
        ) : (
          <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
            Selecione um caso para ver detalhes
          </div>
        )}
      </SheetContent>
    </Sheet>
    <Dialog
      open={Boolean(runReview)}
      onOpenChange={(open) => {
        if (!open && !busyRunId) {
          setRunReview(null);
          setRunReviewText('');
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{runReview?.mode === 'corrected' ? 'Corrigir saída do agente' : 'Rejeitar saída do agente'}</DialogTitle>
          <DialogDescription>
            {runReview?.mode === 'corrected'
              ? 'Registre a correção que substituirá a saída anterior e ficará vinculada à auditoria.'
              : 'Explique o motivo da rejeição para preservar contexto e orientar a próxima execução.'}
          </DialogDescription>
        </DialogHeader>
        <Textarea
          autoFocus
          value={runReviewText}
          onChange={(event) => setRunReviewText(event.target.value)}
          className="min-h-32"
          placeholder={runReview?.mode === 'corrected' ? 'Correção humana...' : 'Motivo da rejeição...'}
        />
        <DialogFooter>
          <Button variant="outline" disabled={Boolean(busyRunId)} onClick={() => setRunReview(null)}>
            Cancelar
          </Button>
          <Button
            variant={runReview?.mode === 'rejected' ? 'destructive' : 'default'}
            disabled={!runReviewText.trim() || Boolean(busyRunId)}
            onClick={submitRunReview}
          >
            {busyRunId ? 'Registrando' : runReview?.mode === 'corrected' ? 'Salvar correção' : 'Confirmar rejeição'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    </>
  );
}
