import { useMemo, useState } from 'react';
import { AlertTriangle, Check, Clock3, ExternalLink, Plus, Send, ShieldCheck, Trash2 } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';

/** A binding deliberately has no template placeholder or literal/prompt kind.
 * The field key is the only source of truth; value is the resolved preview and
 * is empty when the workspace cannot resolve it yet. */
export interface SequenceBinding {
  fieldKey: string;
  value: string;
}

export type SequenceWindowStatus = 'new' | 'paid' | 'unverified';

export interface SequenceMessage {
  id: string;
  templateId?: string | null;
  templateName?: string | null;
  templateLanguage?: string | null;
  body?: string;
  status?: string | null;
  windowStatus?: SequenceWindowStatus;
  delayValue?: number;
  delayUnit?: 'hours' | 'days';
  scheduleMode?: 'now' | 'delay';
  scheduledAt?: string | null;
  bindings?: SequenceBinding[];
  /** Sequence supports only body variables; keep unsupported scopes visible
   * so an imported/selected stage cannot silently lose a header or button
   * placeholder. */
  unsupportedVariableScopes?: Array<'header' | 'button'>;
}

export interface SequenceTemplateOption {
  id: string;
  name: string;
  status: string;
  language?: string | null;
  bodyText?: string | null;
  windowStatus?: SequenceWindowStatus;
  variables?: Array<{ fieldKey?: string; label?: string; resolvedValue?: string | null }>;
  variableCount?: number;
  sequenceEligible?: boolean;
  unsupportedVariableScopes?: Array<'header' | 'button'>;
}

export interface SequenceBindingFieldOption {
  fieldKey: string;
  label?: string;
  resolvedValue?: string | null;
}

export interface SequenceBuilderProps {
  value?: SequenceMessage[];
  templates?: SequenceTemplateOption[];
  availableBindingFields?: SequenceBindingFieldOption[];
  interruptOnReply?: boolean;
  onChange?: (messages: SequenceMessage[], interruptOnReply: boolean) => void;
  onStart?: (messages: SequenceMessage[], interruptOnReply: boolean) => void;
  onCreateTemplate?: () => void;
  onEditSequence?: () => void;
  editSequenceHref?: string;
  className?: string;
}

const fallbackMessage: SequenceMessage = {
  id: 'sequence-message-1',
  body: '',
  status: 'approved',
  windowStatus: 'unverified',
  bindings: [],
};

function statusIsApproved(status: string | null | undefined): boolean {
  return String(status ?? '').toLocaleLowerCase('pt-BR') === 'approved';
}

function windowLabel(status: SequenceWindowStatus | undefined): string {
  if (status === 'new') return 'Janela nova';
  if (status === 'paid') return 'Janela paga';
  return 'Tarifa não verificada';
}

function normalizeMessages(messages: SequenceMessage[] | undefined): SequenceMessage[] {
  if (!messages?.length) return [fallbackMessage];
  return messages.map((message, index) => ({
    ...message,
    id: message.id || `sequence-message-${index + 1}`,
    windowStatus: message.windowStatus ?? 'unverified',
    bindings: (message.bindings ?? []).map((binding) => ({
      fieldKey: binding.fieldKey,
      value: binding.value ?? '',
    })),
  }));
}

export function SequenceBuilder({
  value,
  templates = [],
  availableBindingFields = [],
  interruptOnReply = true,
  onChange,
  onStart,
  onCreateTemplate,
  onEditSequence,
  editSequenceHref,
  className,
}: SequenceBuilderProps) {
  const messages = normalizeMessages(value);
  const [selectedMessage, setSelectedMessage] = useState(0);
  const approvedTemplates = useMemo(() => templates.filter((template) => statusIsApproved(template.status) && template.sequenceEligible !== false), [templates]);
  const pendingTemplates = useMemo(() => templates.filter((template) => !statusIsApproved(template.status)), [templates]);

  const emit = (nextMessages: SequenceMessage[], nextInterrupt = interruptOnReply) => {
    onChange?.(normalizeMessages(nextMessages), nextInterrupt);
  };

  const updateMessage = (index: number, patch: Partial<SequenceMessage>) => {
    emit(messages.map((message, messageIndex) => messageIndex === index ? { ...message, ...patch } : message));
  };

  const applyTemplate = (templateId: string, index = selectedMessage) => {
    const template = templates.find((candidate) => candidate.id === templateId);
    if (!template) return;
    const bindings = (template.variables ?? []).map((variable) => ({
      fieldKey: variable.fieldKey ?? '',
      value: variable.resolvedValue ?? '',
    }));
    updateMessage(index, {
      templateId: template.id,
      templateName: template.name,
      templateLanguage: template.language ?? null,
      body: template.bodyText ?? '',
      status: template.status,
      windowStatus: template.windowStatus ?? 'unverified',
      bindings,
      unsupportedVariableScopes: template.unsupportedVariableScopes,
    });
  };

  const addTemplateStage = (templateId: string) => {
    const template = templates.find((candidate) => candidate.id === templateId);
    if (!template) return;
    const next = [...messages, {
      id: `sequence-message-${Date.now()}`,
      templateId: template.id,
      templateName: template.name,
      templateLanguage: template.language ?? null,
      body: template.bodyText ?? '',
      status: template.status,
      windowStatus: template.windowStatus ?? 'unverified' as SequenceWindowStatus,
      delayValue: 24,
      delayUnit: 'hours' as const,
      bindings: (template.variables ?? []).map((variable) => ({
        fieldKey: variable.fieldKey ?? '',
        value: variable.resolvedValue ?? '',
      })),
      unsupportedVariableScopes: template.unsupportedVariableScopes,
    }];
    setSelectedMessage(next.length - 1);
    emit(next);
  };

  const addMessage = () => {
    const next = [...messages, {
      id: `sequence-message-${Date.now()}`,
      body: '',
      status: 'approved',
      windowStatus: 'unverified' as SequenceWindowStatus,
      delayValue: 24,
      delayUnit: 'hours' as const,
      bindings: [],
    }];
    setSelectedMessage(next.length - 1);
    emit(next);
  };

  const removeMessage = (index: number) => {
    if (messages.length === 1) return;
    const next = messages.filter((_, messageIndex) => messageIndex !== index);
    setSelectedMessage(Math.min(selectedMessage, next.length - 1));
    emit(next);
  };

  const canStart = messages.length > 0 && messages.every((message, index) => {
    const template = templates.find((candidate) => candidate.id === message.templateId);
    const expectedBindings = template?.variableCount ?? template?.variables?.length ?? 0;
    const bindings = message.bindings ?? [];
    const statusAllowed = index === 0
      ? statusIsApproved(message.status)
      : statusIsApproved(message.status) || String(message.status ?? '').toLocaleLowerCase('pt-BR') === 'pending';
    return statusAllowed
      && !(message.unsupportedVariableScopes?.length)
      && Boolean(message.templateId && message.templateName?.trim() && message.body?.trim())
      && (expectedBindings === 0
        ? bindings.length === 0
        : bindings.length >= expectedBindings && bindings.slice(0, expectedBindings).every((binding) => binding.fieldKey.trim().length > 0))
      && (index === 0 || (typeof message.delayValue === 'number' && message.delayValue > 0 && Boolean(message.delayUnit)));
  });
  const activeTemplateId = messages[selectedMessage]?.templateId ?? '';
  const activeTemplate = templates.find((template) => template.id === activeTemplateId);
  const activeVariableCount = activeTemplate?.variableCount ?? activeTemplate?.variables?.length ?? 0;
  const activeBindings = Array.from({ length: activeVariableCount }, (_, index) => messages[selectedMessage]?.bindings?.[index] ?? { fieldKey: '', value: '' });
  const updateBinding = (bindingIndex: number, fieldKey: string) => {
    const nextBindings = activeBindings.map((binding, index) => index === bindingIndex
      ? {
          fieldKey,
          value: availableBindingFields.find((field) => field.fieldKey === fieldKey)?.resolvedValue ?? binding.value ?? '',
        }
      : binding);
    updateMessage(selectedMessage, { bindings: nextBindings });
  };
  const start = () => {
    if (canStart) onStart?.(messages, interruptOnReply);
  };

  return (
    <div className={cn('space-y-4', className)} data-testid="sequence-builder">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold">Sequência de mensagens</p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">Organize a cadência e revise cada janela antes de iniciar.</p>
        </div>
        {editSequenceHref ? (
          <a href={editSequenceHref} onClick={(event) => { if (onEditSequence) { event.preventDefault(); onEditSequence(); } }} className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-primary underline-offset-4 hover:underline">
            Editar sequência <ExternalLink className="h-3 w-3" />
          </a>
        ) : onEditSequence ? (
          <button type="button" className="shrink-0 text-xs font-medium text-primary underline-offset-4 hover:underline" onClick={onEditSequence}>Editar sequência</button>
        ) : null}
      </div>

      <section className="space-y-3 rounded-xl border bg-background/60 p-3" aria-label="Templates da sequência">
        <div className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-emerald-600" /><p className="text-xs font-semibold">Escolher template aprovado</p><Badge variant="outline" className="ml-auto text-[10px]">Meta</Badge></div>
        <select
          aria-label="Template aprovado da sequência"
          className="h-9 w-full rounded-md border border-input bg-background px-3 text-xs"
          value={activeTemplateId}
          onChange={(event) => applyTemplate(event.target.value)}
        >
          <option value="">Selecione um template aprovado</option>
          {approvedTemplates.map((template) => <option key={template.id} value={template.id}>{template.name}{template.language ? ` · ${template.language}` : ''}</option>)}
        </select>
        {messages.length > 1 ? <label className="block text-[11px] text-muted-foreground">Etapa em edição<select aria-label="Etapa da sequência" className="mt-1 h-8 w-full rounded-md border border-input bg-background px-3 text-xs" value={String(selectedMessage)} onChange={(event) => setSelectedMessage(Number(event.target.value))}>{messages.map((message, index) => <option key={message.id} value={index}>Mensagem {index + 1}</option>)}</select></label> : null}
        {activeVariableCount > 0 ? <div className="space-y-2 rounded-md border border-primary/20 bg-primary/5 p-2.5"><p className="text-[11px] font-medium">Mapear variáveis para campos</p><p className="text-[11px] leading-relaxed text-muted-foreground">A sequência só pode iniciar quando cada variável tiver um fieldKey. O valor abaixo é apenas a resolução atual.</p>{activeBindings.map((binding, index) => <div key={`${selectedMessage}-${index}`} className="grid gap-1"><label className="text-[11px] text-muted-foreground" htmlFor={`sequence-binding-${selectedMessage}-${index}`}>Campo da variável {index + 1}</label>{availableBindingFields.length ? <select id={`sequence-binding-${selectedMessage}-${index}`} aria-label={`Campo da variável ${index + 1}`} className="h-8 rounded-md border border-input bg-background px-2 text-xs" value={binding.fieldKey} onChange={(event) => updateBinding(index, event.target.value)}><option value="">Escolha um campo</option>{availableBindingFields.map((field) => <option key={field.fieldKey} value={field.fieldKey}>{field.label ?? field.fieldKey}</option>)}</select> : <Input id={`sequence-binding-${selectedMessage}-${index}`} aria-label={`Campo da variável ${index + 1}`} className="h-8 text-xs" value={binding.fieldKey} onChange={(event) => updateBinding(index, event.target.value)} placeholder="ex.: contact.first_name" />}</div>)}</div> : null}
        {messages[selectedMessage]?.unsupportedVariableScopes?.length ? <div role="alert" className="rounded-md border border-amber-300 bg-amber-50 px-2.5 py-2 text-[11px] leading-relaxed text-amber-950">Este template não pode ser usado em uma sequência: a sequência ainda aceita apenas variáveis no corpo. Variáveis de {messages[selectedMessage]?.unsupportedVariableScopes?.join(' e ')} precisam ser removidas do template antes de continuar.</div> : null}
        {pendingTemplates.length ? (
          <div className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-2.5 text-xs text-amber-950">
            <div className="flex items-center gap-1.5 font-medium"><AlertTriangle className="h-3.5 w-3.5" />Templates pendentes não iniciam a sequência</div>
            {pendingTemplates.map((template) => <div key={template.id} className="flex items-center justify-between gap-2"><span className="truncate">{template.name}</span><div className="flex items-center gap-1.5"><Badge variant="outline" className="border-amber-400 text-[10px]">Pendente</Badge><Button type="button" size="sm" variant="ghost" className="h-7 px-2 text-[11px]" onClick={() => addTemplateStage(template.id)}>Adicionar etapa</Button></div></div>)}
            <p className="leading-relaxed">A Meta precisa aprovar o modelo antes do envio. A etapa aguarda por até 15 minutos; se continuar pendente, será pulada e registrada.</p>
          </div>
        ) : null}
        <div className="border-t pt-3">
          <Button type="button" variant="outline" size="sm" className="w-full text-xs" onClick={onCreateTemplate} disabled={!onCreateTemplate}><Plus className="mr-1.5 h-3.5 w-3.5" />Criar novo template</Button>
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">Novos templates passam por análise e aprovação da Meta antes de poderem ser enviados.</p>
        </div>
      </section>

      <div className="space-y-0" aria-label="Timeline da sequência">
        {messages.map((message, index) => {
          const pending = !statusIsApproved(message.status);
          return (
            <div key={message.id} className="relative flex gap-3 pb-4 last:pb-0">
              <div className="relative flex w-5 shrink-0 justify-center">
                {index < messages.length - 1 ? <span aria-hidden="true" className="absolute top-5 h-full w-px bg-border" /> : null}
                <span className={cn('z-10 mt-1 flex h-5 w-5 items-center justify-center rounded-full border bg-background text-[10px] font-semibold', pending ? 'border-amber-400 text-amber-700' : 'border-primary text-primary')}>
                  {pending ? <AlertTriangle className="h-3 w-3" /> : index === 0 ? <Check className="h-3 w-3" /> : index + 1}
                </span>
              </div>
              <article className={cn('min-w-0 flex-1 space-y-3 rounded-lg border p-3', pending && 'border-amber-300 bg-amber-50/40')}>
                <div className="flex items-start justify-between gap-2">
                  <div><p className="text-xs font-semibold">Mensagem {index + 1}</p><p className="mt-0.5 text-[11px] text-muted-foreground">{index === 0 ? 'Agora' : 'Depois da etapa anterior'}</p></div>
                  <div className="flex items-center gap-1.5"><Badge variant={pending ? 'outline' : 'secondary'} className={cn('text-[10px]', pending && 'border-amber-400 text-amber-800')}>{pending ? 'Pendente' : windowLabel(message.windowStatus)}</Badge>{messages.length > 1 ? <Button type="button" variant="ghost" size="icon" className="h-7 w-7 text-destructive" aria-label={`Remover mensagem ${index + 1}`} onClick={() => removeMessage(index)}><Trash2 className="h-3.5 w-3.5" /></Button> : null}</div>
                </div>
                {index > 0 ? <div className="grid grid-cols-[minmax(0,1fr)_120px] gap-2"><label className="text-[11px] text-muted-foreground">Prazo<input aria-label={`Prazo da mensagem ${index + 1}`} type="number" min={1} className="mt-1 h-8 w-full rounded-md border border-input bg-background px-2 text-xs text-foreground" value={message.delayValue ?? 24} onChange={(event) => updateMessage(index, { delayValue: Math.max(1, Number(event.target.value) || 1) })} /></label><label className="text-[11px] text-muted-foreground">Unidade<select aria-label={`Unidade do prazo da mensagem ${index + 1}`} className="mt-1 h-8 w-full rounded-md border border-input bg-background px-2 text-xs text-foreground" value={message.delayUnit ?? 'hours'} onChange={(event) => updateMessage(index, { delayUnit: event.target.value as 'hours' | 'days' })}><option value="hours">horas</option><option value="days">dias</option></select></label></div> : <div className="flex items-center gap-1.5 text-xs font-medium text-emerald-700"><Clock3 className="h-3.5 w-3.5" />Agora</div>}
                {message.templateName ? <div className="rounded-md bg-muted/50 px-2.5 py-2 text-xs"><span className="font-medium">{message.templateName}</span>{message.body ? <span className="mt-1 block whitespace-pre-wrap text-muted-foreground">{message.body}</span> : null}</div> : <p className="rounded-md border border-dashed p-2.5 text-xs text-muted-foreground">Escolha um template aprovado acima.</p>}
                {pending ? <p className="flex items-start gap-1.5 text-xs leading-relaxed text-amber-900"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /><span>Pendente: esta mensagem não será enviada até a aprovação da Meta.</span> <span>Ela aguarda por até 15 minutos; depois, será pulada e registrada.</span></p> : null}
                {message.bindings?.length ? <div className="space-y-2 border-t pt-2"><p className="text-[11px] font-medium">Bindings resolvidos</p>{message.bindings.map((binding, bindingIndex) => <label key={`${message.id}-${bindingIndex}`} className="block text-[11px] text-muted-foreground"><span className="font-mono text-foreground">{binding.fieldKey}</span><Input readOnly aria-label={`Valor resolvido para ${binding.fieldKey}`} className="mt-1 h-8 text-xs" value={binding.value} placeholder="Valor não resolvido" /></label>)}</div> : null}
              </article>
            </div>
          );
        })}
      </div>

      <Button type="button" variant="outline" className="w-full text-xs" onClick={addMessage}><Plus className="mr-1.5 h-3.5 w-3.5" />Adicionar mensagem</Button>

      <div className="flex items-center justify-between gap-3 rounded-lg border bg-muted/30 p-3"><div><p className="text-xs font-medium">Interromper ao responder</p><p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">Uma resposta do contato encerra as próximas etapas.</p></div><Switch aria-label="Interromper sequência ao responder" checked={interruptOnReply} onCheckedChange={(checked) => emit(messages, checked)} /></div>
      <Button type="button" className="w-full" onClick={start} disabled={!canStart}><Send className="mr-1.5 h-4 w-4" />Iniciar sequência</Button>
    </div>
  );
}
