import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import {
  CalendarCheck2,
  CalendarPlus2,
  CircleCheckBig,
  CheckSquare2,
  ClipboardPlus,
  FileQuestion,
  Loader2,
  MessageSquareQuote,
  ListPlus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { KlmPill } from '@/klm/components/Primitives';
import { klmApi, type KlmOmnichannelCopilot } from '@/klm/api';
import { cn } from '@/lib/utils';

type CopilotAction = 'summarize' | 'suggest_reply' | 'rewrite' | 'unanswered' | 'extract_tasks' | 'meeting_brief';
type CopilotTone = 'acolhedor' | 'objetivo' | 'formal' | 'simples';

const actions: Array<{ key: CopilotAction; label: string; icon: typeof Sparkles }> = [
  { key: 'summarize', label: 'Resumo', icon: ClipboardPlus },
  { key: 'suggest_reply', label: 'Responder', icon: MessageSquareQuote },
  { key: 'unanswered', label: 'Pendências', icon: FileQuestion },
  { key: 'extract_tasks', label: 'Tarefas', icon: CheckSquare2 },
  { key: 'meeting_brief', label: 'Reunião', icon: CalendarCheck2 },
];

const tones: Array<{ key: CopilotTone; label: string }> = [
  { key: 'acolhedor', label: 'Acolhedor' },
  { key: 'objetivo', label: 'Objetivo' },
  { key: 'formal', label: 'Formal' },
  { key: 'simples', label: 'Simples' },
];

function stableTaskKey(
  threadId: string,
  task: KlmOmnichannelCopilot['tasks'][number],
  evidenceMessageIds: string[],
) {
  const value = `${threadId}|${task.kind}|${task.source}|${task.title}|${evidenceMessageIds.join(',')}`;
  let first = 2166136261;
  let second = 2246822519;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    first = Math.imul(first ^ code, 16777619);
    second = Math.imul(second ^ code, 3266489917);
  }
  return `copilot-task:${threadId}:${(first >>> 0).toString(16)}${(second >>> 0).toString(16)}`;
}

function taskPriority(kind: string): 'alta' | 'media' | 'baixa' {
  return ['legal_review', 'documents'].includes(kind) ? 'alta' : kind === 'meeting' ? 'media' : 'baixa';
}

export function ConversationCopilotPanel({
  threadId,
  matterId,
  canEdit,
  canCreateTasks,
  draft,
  onUseResponse,
  onOpenSchedule,
}: {
  threadId?: string | null;
  matterId?: string | null;
  canEdit: boolean;
  canCreateTasks?: boolean;
  draft: string;
  onUseResponse: (content: string) => void;
  onOpenSchedule?: () => void;
}) {
  const [tone, setTone] = useState<CopilotTone>('acolhedor');
  const [result, setResult] = useState<KlmOmnichannelCopilot | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [createdTasks, setCreatedTasks] = useState<Record<string, string>>({});

  const mutation = useMutation({
    mutationFn: (action: CopilotAction) => {
      if (!threadId) throw new Error('Selecione uma conversa.');
      if (action === 'rewrite' && !draft.trim()) throw new Error('Escreva um rascunho antes de reescrever.');
      return klmApi.omnichannelCopilot(threadId, {
        action,
        draft: action === 'rewrite' ? draft.trim() : null,
        tone,
        goal: 'Conduzir a triagem jurídica e comercial sem inventar fatos ou promessas.',
      });
    },
    onSuccess: (next) => {
      setResult(next);
      setNotice(next.externalRequests === 0 ? 'Análise concluída localmente e vinculada às evidências.' : null);
    },
    onError: (error) => {
      setResult(null);
      setNotice(error instanceof Error ? error.message : 'Não foi possível gerar o apoio.');
    },
  });

  const taskMutation = useMutation({
    mutationFn: async ({ task, key }: { task: KlmOmnichannelCopilot['tasks'][number]; key: string }) => {
      if (!matterId) throw new Error('Vincule a conversa a um card antes de criar a tarefa.');
      if (!canCreateTasks) throw new Error('Seu perfil não pode criar tarefas no card.');
      return klmApi.createMatterTask(matterId, {
        title: task.title,
        description: `Criada pelo Assistente Executivo a partir da conversa ${threadId}. Origem: ${task.source}; tipo: ${task.kind}.`,
        priority: taskPriority(task.kind),
        isAiGenerated: true,
        idempotencyKey: key,
      });
    },
    onSuccess: (task, variables) => {
      setCreatedTasks((current) => ({ ...current, [variables.key]: task.id }));
      setNotice('Tarefa criada no card e registrada no histórico. Nenhuma ação externa foi executada.');
    },
    onError: (error) => {
      setNotice(error instanceof Error ? error.message : 'Não foi possível criar a tarefa.');
    },
  });

  if (!threadId) return <p className="p-4 text-2xs text-muted-foreground">Selecione uma conversa para abrir o Assistente Executivo.</p>;

  return (
    <div className="divide-y divide-border">
      <section className="space-y-3 p-3">
        <div className="flex items-start justify-between gap-2">
          <div><p className="flex items-center gap-1.5 text-2xs font-semibold"><Sparkles className="h-3.5 w-3.5 text-[#0057d8]" />Assistente Meu Executivo</p><p className="mt-0.5 text-2xs leading-3 text-muted-foreground">Apoio comercial e jurídico baseado apenas neste atendimento.</p></div>
          <KlmPill tone="green"><ShieldCheck className="mr-1 h-3 w-3" />com evidências</KlmPill>
        </div>
        <div role="group" aria-label="Tom da resposta" className="grid grid-cols-2 gap-1.5">
          {tones.map((item) => <button key={item.key} type="button" aria-pressed={tone === item.key} onClick={() => setTone(item.key)} className={cn('h-7 rounded-md border px-2 text-2xs font-semibold', tone === item.key ? 'border-[#031121] bg-[#031121] text-white dark:border-primary dark:bg-primary dark:text-black' : 'border-border bg-card text-muted-foreground')}>{item.label}</button>)}
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          {actions.map(({ key, label, icon: Icon }) => <Button key={key} variant="outline" size="sm" className="h-8 justify-start px-2 text-2xs" disabled={mutation.isPending} onClick={() => mutation.mutate(key)}><Icon className="mr-1.5 h-3 w-3" />{label}</Button>)}
        </div>
        <Button variant="outline" size="sm" className="h-8 w-full text-2xs" disabled={mutation.isPending || !draft.trim()} onClick={() => mutation.mutate('rewrite')}><RefreshCw className="mr-1.5 h-3 w-3" />Reescrever meu rascunho</Button>
      </section>

      {mutation.isPending && <section className="flex items-center gap-2 p-4 text-2xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Lendo o histórico canônico...</section>}

      {result && !mutation.isPending && (
        <section className="space-y-3 p-3">
          <div className="flex items-center justify-between gap-2"><p className="text-2xs font-semibold uppercase text-[#0057d8]">Resultado</p><KlmPill tone="muted">{Math.round(result.confidence * 100)}% · {result.evidenceMessageIds.length} evidências</KlmPill></div>
          <Textarea readOnly value={result.content} className="min-h-[118px] resize-none bg-secondary text-2xs leading-4" aria-label="Resultado do Assistente Executivo" />
          {result.suggestions.length > 0 && (
            <div className="space-y-1.5">
              {result.suggestions.map((suggestion, index) => <button key={`${suggestion}:${index}`} type="button" disabled={!canEdit} onClick={() => onUseResponse(suggestion)} className="w-full rounded-md border border-border p-2 text-left text-2xs leading-4 hover:border-primary disabled:opacity-50"><strong className="block text-[#0057d8]">Usar como rascunho</strong><span>{suggestion}</span></button>)}
            </div>
          )}
          {result.unansweredQuestions.length > 0 && <div className="space-y-1.5"><p className="text-2xs font-semibold">Perguntas ainda sem resposta</p>{result.unansweredQuestions.map((question) => <div key={question} className="flex items-start gap-2 border-l-2 border-amber-500 pl-2"><p className="min-w-0 flex-1 text-2xs leading-4">{question}</p><Button type="button" variant="ghost" size="sm" className="h-7 shrink-0 px-2 text-2xs" disabled={!canEdit} aria-label={`Preparar resposta para: ${question}`} onClick={() => onUseResponse(`Entendi sua pergunta: “${question}” Vou conferir essa informação no seu caso e orientar você por aqui. Se puder, envie também os documentos relacionados para avançarmos com segurança.`)}><MessageSquareQuote className="mr-1 h-3 w-3" />Preparar resposta</Button></div>)}</div>}
          {result.tasks.length > 0 && <div className="space-y-1.5"><p className="text-2xs font-semibold">Próximas tarefas</p>{result.tasks.map((task, index) => {
            const key = stableTaskKey(threadId, task, result.evidenceMessageIds);
            const wasCreated = Boolean(createdTasks[key]);
            return <div key={`${task.title}:${index}`} className="flex items-center gap-2 rounded-md border border-border p-2"><CheckSquare2 className="h-3 w-3 shrink-0 text-[#1a9948]" /><span className="min-w-0 flex-1 text-2xs leading-4">{task.title}</span>{task.kind === 'meeting' && onOpenSchedule ? <Button type="button" variant="ghost" size="icon" className="h-7 w-7 shrink-0" aria-label={`Abrir agenda para: ${task.title}`} title="Abrir agenda" onClick={onOpenSchedule}><CalendarPlus2 className="h-3 w-3" /></Button> : null}<Button type="button" variant="outline" size="sm" className="h-7 shrink-0 px-2 text-2xs" disabled={!matterId || !canCreateTasks || wasCreated || taskMutation.isPending} aria-label={`${wasCreated ? 'Tarefa criada' : 'Criar tarefa'}: ${task.title}`} onClick={() => taskMutation.mutate({ task, key })}>{wasCreated ? <CircleCheckBig className="mr-1 h-3 w-3 text-[#1a9948]" /> : <ListPlus className="mr-1 h-3 w-3" />}{wasCreated ? 'Criada' : 'Criar'}</Button></div>;
          })}</div>}
          {result.action === 'meeting_brief' && onOpenSchedule && <Button type="button" variant="outline" size="sm" className="h-8 w-full text-2xs" onClick={onOpenSchedule}><CalendarPlus2 className="mr-1.5 h-3 w-3" />Abrir agenda e disponibilidades</Button>}
          {(result.action === 'suggest_reply' || result.action === 'rewrite') && result.content.trim() && <Button size="sm" className="h-8 w-full bg-[#031121] text-2xs text-white hover:bg-[#031121]" disabled={!canEdit} onClick={() => onUseResponse(result.content)}><MessageSquareQuote className="mr-1.5 h-3 w-3" />Colocar no campo de resposta</Button>}
          <p className="text-2xs leading-3 text-muted-foreground">Motor {result.engine} · {result.externalRequests} chamada(s) externa(s) · nenhuma mensagem é enviada automaticamente.</p>
        </section>
      )}

      {notice && <section className="p-3"><p aria-live="polite" className="rounded-md border border-primary/30 bg-primary/10 p-2 text-2xs leading-4">{notice}</p></section>}
    </div>
  );
}
