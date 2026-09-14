import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, RefreshCw, Search, Send, UsersRound, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { KlmPill } from '@/klm/components/Primitives';
import { SequenceBuilder, type SequenceMessage, type SequenceTemplateOption } from '@/components/flow/SequenceBuilder';
import { WhatsAppBubblePreview } from '@/pages/WhatsAppMetaTemplates';
import {
  fetchTemplatePicker,
  fetchTemplateVariables,
  quickSendWhatsAppTemplate,
  startWhatsAppSequence,
} from '@/services/campaigns';
import { ApiError } from '@/services/http';
import type { TemplatePickerCard, TemplatePickerResponse, TemplatePickerVariable, WhatsAppSequenceStartPayload } from '@/types';
import { cn } from '@/lib/utils';

const INTENT_LABELS: Record<string, string> = {
  follow_up: 'Follow-up', reativacao: 'Reativação', documentos: 'Documentos',
  audiencia: 'Audiência', cobranca: 'Cobrança', proposta: 'Proposta',
};
const SECTOR_LABELS: Record<string, string> = {
  comercial: 'Comercial',
  juridico: 'Jurídico',
  financeiro: 'Financeiro',
};

function errorCode(error: unknown): string {
  if (error instanceof ApiError && error.detail && typeof error.detail === 'object' && 'code' in error.detail) {
    return typeof error.detail.code === 'string' ? error.detail.code : `HTTP-${error.status}`;
  }
  return error instanceof ApiError ? `HTTP-${error.status}` : 'KLM-TPL-UNKNOWN';
}

function cardComponents(card: TemplatePickerCard) {
  return {
    headerType: (card.header?.type ?? 'NONE') as 'NONE' | 'TEXT' | 'IMAGE' | 'VIDEO' | 'DOCUMENT',
    headerText: card.header?.text ?? null,
    headerExample: null,
    bodyText: card.bodyText,
    bodyExamples: card.variables.filter((item) => item.scope === 'body').sort((a, b) => a.position - b.position).map((item) => item.example ?? ''),
    footerText: card.footerText ?? null,
    buttons: card.buttons.map((button) => ({ type: (button.type ?? 'QUICK_REPLY') as 'QUICK_REPLY' | 'URL' | 'PHONE_NUMBER' | 'COPY_CODE', text: button.label ?? button.text ?? '', url: button.url ?? null })),
  };
}

function normalizedStatus(value: string | null | undefined) {
  return String(value ?? '').toLocaleLowerCase('pt-BR');
}

function hasFieldBinding(variable: TemplatePickerVariable): variable is TemplatePickerVariable & {
  binding: { kind: 'field'; fieldKey: string };
} {
  return variable.binding?.kind === 'field' && typeof variable.binding.fieldKey === 'string' && variable.binding.fieldKey.trim().length > 0;
}

function resolvedSequenceBody(message: SequenceMessage | undefined, fallback: string): string {
  const body = message?.body ?? fallback;
  const bindings = message?.bindings ?? [];
  return body
    .replace(/\{\{\s*(\d+)\s*\}\}/g, (_, position) => bindings[Number(position) - 1]?.value?.trim() || `[valor ${position}]`)
    .replace(/\{sequence_(\d+)\}/g, (_, position) => bindings[Number(position) - 1]?.value?.trim() || `[valor ${position}]`);
}

export type ConversationTemplatePickerProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  threadId: string;
  matterId?: string | null;
  contactId?: string | null;
  contactName?: string;
  onCreateTemplate?: (templateId?: string) => void;
  onOpenCampaigns?: () => void;
  onSent?: (payload: { card: TemplatePickerCard; body: string }) => void;
  /** Optional host-owned override used by embedded conversation surfaces. */
  onStartSequence?: (payload: {
    card: TemplatePickerCard;
    messages: SequenceMessage[];
    interruptOnReply: boolean;
    threadId: string;
    matterId?: string | null;
    contactId?: string | null;
  }) => void;
  onOpenFlowBuilder?: (href: string) => void;
};

export function ConversationTemplatePicker({
  open,
  onOpenChange,
  threadId,
  matterId,
  contactId,
  contactName,
  onCreateTemplate,
  onOpenCampaigns,
  onSent,
  onStartSequence,
  onOpenFlowBuilder,
}: ConversationTemplatePickerProps) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [intent, setIntent] = useState<string | null>(null);
  const [selected, setSelected] = useState<TemplatePickerCard | null>(null);
  const [sequenceMode, setSequenceMode] = useState(false);
  const [showAllSectors, setShowAllSectors] = useState(false);
  const [bindings, setBindings] = useState<Record<string, string>>({});
  const [attemptKey, setAttemptKey] = useState<string | null>(null);
  const [sequenceMessages, setSequenceMessages] = useState<SequenceMessage[]>([]);
  const [sequenceInterruptOnReply, setSequenceInterruptOnReply] = useState(true);
  const [sequenceDraftKey, setSequenceDraftKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pickerQuery = useQuery<TemplatePickerResponse>({
    queryKey: ['whatsapp-template-picker', threadId, matterId ?? null],
    queryFn: () => fetchTemplatePicker({ threadId, matterId }),
    enabled: open,
    staleTime: 30_000,
    retry: false,
  });
  const variablesQuery = useQuery({
    queryKey: ['whatsapp-template-variables', contactId ?? null, matterId ?? null],
    queryFn: () => fetchTemplateVariables({ contactId, matterId }),
    enabled: open && Boolean(selected),
    staleTime: 30_000,
    retry: false,
  });
  const sendMutation = useMutation({
    mutationFn: (card: TemplatePickerCard) => quickSendWhatsAppTemplate({
      threadId,
      matterId,
      templateId: card.id,
      idempotencyKey: attemptKey ?? crypto.randomUUID(),
      headerParameters: card.variables.filter((item) => item.scope === 'header').sort((a, b) => a.position - b.position).map((item) => bindings[`header:${item.position}`] ?? ''),
      bodyParameters: card.variables.filter((item) => item.scope === 'body').sort((a, b) => a.position - b.position).map((item) => bindings[`body:${item.position}`] ?? ''),
      buttonParameters: card.variables.filter((item) => item.scope === 'button').sort((a, b) => a.position - b.position).map((item) => ({ index: item.position, value: bindings[`button:${item.position}`] ?? '' })),
      bindings,
    }),
    onSuccess: (_, card) => {
      const body = card.bodyText.replace(/\{\{\s*(\d+)\s*\}\}/g, (_, position) => bindings[`body:${position}`] ?? `[valor ${position}]`);
      onSent?.({ card, body });
      setSelected(null);
      setBindings({});
      setAttemptKey(null);
      onOpenChange(false);
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ['whatsapp-template-picker', threadId, matterId ?? null] });
    },
    onError: (cause) => {
      const code = errorCode(cause);
      const message = cause instanceof ApiError && cause.status === 409
        ? 'A Meta recusou o envio porque o modelo não está aprovado.'
        : 'Não foi possível enviar o modelo aprovado.';
      setError(`${message} Código: ${code}`);
    },
  });
  const startSequenceMutation = useMutation({
    mutationFn: (payload: WhatsAppSequenceStartPayload) => startWhatsAppSequence(payload),
    onSuccess: (result) => {
      if (result.dispatchState !== 'accepted') {
        const code = result.code?.trim() || `WA-SEQUENCE-DISPATCH-${result.dispatchState || 'UNKNOWN'}`;
        const message = result.dispatchState === 'failed'
          ? 'A primeira etapa da sequência falhou e continua disponível para revisão.'
          : result.dispatchState === 'uncertain'
            ? 'O resultado da primeira etapa é incerto; não repetimos o envio automaticamente.'
            : 'A resposta do início da sequência não confirmou o despacho.';
        setError(`${result.message?.trim() || message} Código: ${code}`);
        return;
      }
      if (!result.firstRecipientId.trim()) {
        setError('A sequência foi aceita sem destinatário inicial. Código: WA-SEQUENCE-RESPONSE-MISSING-FIRST-RECIPIENT');
        return;
      }
      if (selected) {
        onSent?.({
          card: selected,
          body: resolvedSequenceBody(sequenceMessages[0], selected.bodyText),
        });
      }
      setSelected(null);
      setSequenceMode(false);
      setShowAllSectors(false);
      setSequenceMessages([]);
      setSequenceDraftKey(null);
      setError(null);
      onOpenChange(false);
      void queryClient.invalidateQueries({ queryKey: ['whatsapp-template-picker', threadId, matterId ?? null] });
    },
    onError: (cause) => setError(`Não foi possível iniciar a sequência. Código: ${errorCode(cause)}`),
  });

  useEffect(() => {
    if (!open) {
      setSelected(null);
      setSequenceMode(false);
      setError(null);
      setQuery('');
      setIntent(null);
      setSequenceMessages([]);
      setSequenceInterruptOnReply(true);
      setSequenceDraftKey(null);
    }
  }, [open]);

  useEffect(() => {
    setShowAllSectors(false);
  }, [pickerQuery.data?.sectorMode, pickerQuery.data?.userSector]);

  useEffect(() => {
    if (!sequenceMode || !variablesQuery.data || !sequenceMessages.length) return;
    const resolved = new Map(
      variablesQuery.data.groups.flatMap((group) => group.values.map((item) => [item.fieldKey, item.resolvedValue ?? ''] as const)),
    );
    setSequenceMessages((current) => current.map((message) => ({
      ...message,
      bindings: message.bindings?.map((binding) => ({ ...binding, value: resolved.get(binding.fieldKey) ?? binding.value ?? '' })),
    })));
  }, [selected?.id, sequenceMode, variablesQuery.data, sequenceMessages.length]);

  useEffect(() => {
    const handleRealtime = (event: Event) => {
      const detail = (event as CustomEvent<{ event?: string }>).detail;
      if (detail?.event === 'message_template_status_update') {
        void queryClient.invalidateQueries({ queryKey: ['whatsapp-template-picker', threadId, matterId ?? null] });
      }
    };
    window.addEventListener('klm:realtime', handleRealtime);
    return () => window.removeEventListener('klm:realtime', handleRealtime);
  }, [matterId, queryClient, threadId]);

  const data = pickerQuery.data;
  const cards = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase('pt-BR');
    const approved = (data?.cards ?? []).filter((card) => normalizedStatus(card.status) === 'approved');
    return approved
      .filter((card) => !intent || card.intent === intent)
      .map((card) => {
        const fields = [card.bodyText, INTENT_LABELS[card.intent ?? ''] ?? '', card.name].map((value) => value.toLocaleLowerCase('pt-BR'));
        const matchRank = !needle ? 0 : fields[0].includes(needle) ? 0 : fields[1].includes(needle) ? 1 : fields[2].includes(needle) ? 2 : 99;
        return { card, matchRank };
      })
      .filter((row) => row.matchRank < 99)
      .sort((left, right) => left.matchRank - right.matchRank || (right.card.usageCount30d ?? 0) - (left.card.usageCount30d ?? 0))
      .map((row) => row.card);
  }, [data?.cards, intent, query]);
  const visibleCards = useMemo(() => {
    if (data?.sectorMode !== 'by_sector' || !data.userSector || showAllSectors) return cards;
    return cards.filter((card) => card.sector === data.userSector);
  }, [cards, data?.sectorMode, data?.userSector, showAllSectors]);
  const suggestions = useMemo(() => (data?.suggestions ?? [])
    .filter((item) => item.reasonText.trim())
    .sort((a, b) => a.rank - b.rank)
    .slice(0, 3), [data?.suggestions]);
  const intents = useMemo(() => Array.from(new Set((data?.cards ?? []).filter((card) => normalizedStatus(card.status) === 'approved').map((card) => card.intent).filter(Boolean))), [data?.cards]);
  const cardVariablePositions = selected?.variables.filter((item) => item.scope === 'body' || item.scope === 'header' || item.scope === 'button').map((item) => item.position) ?? [];
  const previewValues = Object.fromEntries(cardVariablePositions.map((position) => [position, bindings[`body:${position}`] ?? selected?.variables.find((item) => item.scope === 'body' && item.position === position)?.example ?? ''])) as Record<number, string>;

  const selectCard = (card: TemplatePickerCard, asSequence = Boolean(card.isSequence)) => {
    const initial: Record<string, string> = {};
    card.variables.forEach((variable: TemplatePickerVariable) => {
      if (variable.position > 0) initial[`${variable.scope}:${variable.position}`] = variable.example ?? '';
    });
    setBindings(initial);
    if (asSequence) {
      const sequenceVariables = card.variables.filter((variable) => variable.scope === 'body').sort((left, right) => left.position - right.position);
      const unsupportedVariableScopes = Array.from(new Set(card.variables.filter((variable) => variable.scope !== 'body').map((variable) => variable.scope))) as Array<'header' | 'button'>;
      const count = Math.max(1, card.sequenceStepCount ?? 1);
      const bindingsForSequence = sequenceVariables
        .map((variable) => ({
          fieldKey: hasFieldBinding(variable) ? variable.binding.fieldKey : '',
          value: variable.example ?? '',
        }));
      setSequenceMessages(Array.from({ length: count }, (_, index) => ({
        id: `${card.id}-sequence-${index + 1}`,
        templateId: card.id,
        templateName: card.name,
        templateLanguage: card.language,
        body: card.bodyText,
        status: normalizedStatus(card.status),
        windowStatus: 'unverified' as const,
        ...(index > 0 ? { delayValue: 24, delayUnit: 'hours' as const } : {}),
        bindings: bindingsForSequence,
        unsupportedVariableScopes,
      })));
      setSequenceInterruptOnReply(true);
      setSequenceDraftKey(`${card.id}-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    } else {
      setSequenceMessages([]);
      setSequenceInterruptOnReply(true);
      setSequenceDraftKey(null);
    }
    setSequenceMode(asSequence);
    setSelected(card);
    setAttemptKey(crypto.randomUUID());
    setError(null);
  };
  useEffect(() => {
    if (!sequenceDraftKey || !sequenceMessages.length || typeof window === 'undefined') return;
    window.sessionStorage.setItem(`klm-sequence-draft:${sequenceDraftKey}`, JSON.stringify({ messages: sequenceMessages, interruptOnReply: sequenceInterruptOnReply }));
  }, [sequenceDraftKey, sequenceInterruptOnReply, sequenceMessages]);
  const refresh = () => void pickerQuery.refetch();
  const sendSelected = (card: TemplatePickerCard) => {
    const missing = card.variables.find((variable) => !String(bindings[`${variable.scope}:${variable.position}`] ?? '').trim());
    if (missing) {
      setError(`Preencha ${missing.scope} · variável ${missing.position} antes de enviar. Código: WA-QUICK-SEND-BINDING`);
      return;
    }
    setAttemptKey((current) => current ?? crypto.randomUUID());
    sendMutation.mutate(card);
  };
  const flowBuilderHref = (() => {
    const params = new URLSearchParams({ thread: threadId });
    if (matterId) params.set('matter', matterId);
    if (contactId) params.set('contact', contactId);
    if (sequenceDraftKey) params.set('sequence_draft', sequenceDraftKey);
    return `/app/flows?${params.toString()}`;
  })();
  const handleStartSequence = (messages: SequenceMessage[], interruptOnReply: boolean) => {
    if (!selected || !sequenceMode) return;
    if (onStartSequence) {
      onStartSequence({ card: selected, messages, interruptOnReply, threadId, matterId, contactId });
      return;
    }
    startSequenceMutation.mutate({
      threadId,
      matterId,
      contactId,
      templateId: selected.id,
      stages: messages,
      interruptOnReply,
      idempotencyKey: attemptKey ?? crypto.randomUUID(),
    });
  };

  const sequenceTemplates: SequenceTemplateOption[] = useMemo(() => (data?.sequenceTemplates ?? data?.cards ?? [])
    .filter((card) => normalizedStatus(card.status) === 'approved' || normalizedStatus(card.status) === 'pending')
    .map((card) => ({
      id: card.id,
      name: card.name,
      status: normalizedStatus(card.status),
      language: card.language,
      bodyText: card.bodyText,
      windowStatus: 'unverified' as const,
      variableCount: card.variables.filter((variable) => variable.scope === 'body').length,
      sequenceEligible: card.variables.every((variable) => variable.scope === 'body'),
      unsupportedVariableScopes: Array.from(new Set(card.variables.filter((variable) => variable.scope !== 'body').map((variable) => variable.scope))) as Array<'header' | 'button'>,
      variables: card.variables.filter((variable) => variable.scope === 'body').sort((left, right) => left.position - right.position).map((variable) => ({
        fieldKey: hasFieldBinding(variable) ? variable.binding.fieldKey : '',
        resolvedValue: variable.example ?? '',
      })),
    })), [data?.cards, data?.sequenceTemplates]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-[900px]">
        <h3 className="sr-only">Disparos e listas</h3>
        <DialogHeader>
          <div className="flex flex-wrap items-center justify-between gap-2 pr-8">
            <DialogTitle>{sequenceMode ? 'Configurar sequência' : selected ? 'Prévia do modelo aprovado' : 'Enviar modelo aprovado'}</DialogTitle>
            {!selected && <div className="flex items-center gap-1.5"><Button size="sm" variant="outline" onClick={() => onCreateTemplate?.()}><Plus className="mr-1.5 h-3.5 w-3.5" />Criar modelo</Button><Button size="sm" variant="outline" onClick={onOpenCampaigns}><UsersRound className="mr-1.5 h-3.5 w-3.5" />Vários</Button></div>}
          </div>
          <DialogDescription>{sequenceMode ? 'Revise a cadência e os valores resolvidos antes de iniciar ou abrir o editor.' : selected ? 'Confira os campos preenchidos antes de registrar o disparo de 1 destinatário.' : 'A biblioteca mostra somente modelos aprovados pela Meta.'}</DialogDescription>
        </DialogHeader>
        {error && <div role="alert" className="flex items-start justify-between gap-3 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-900"><span>{error}</span><button type="button" aria-label="Fechar erro" onClick={() => setError(null)}><X className="h-4 w-4" /></button></div>}
        {sequenceMode && selected ? (
          <SequenceBuilder
            value={sequenceMessages}
            templates={sequenceTemplates}
            availableBindingFields={variablesQuery.data?.groups.flatMap((group) => group.values.map((value) => ({ fieldKey: value.fieldKey, label: value.label, resolvedValue: value.resolvedValue ?? '' }))) ?? []}
            interruptOnReply={sequenceInterruptOnReply}
            onChange={(messages, interruptOnReply) => {
              setSequenceMessages(messages);
              setSequenceInterruptOnReply(interruptOnReply);
            }}
            onStart={handleStartSequence}
            editSequenceHref={flowBuilderHref}
            onEditSequence={onOpenFlowBuilder ? () => onOpenFlowBuilder(flowBuilderHref) : undefined}
            onCreateTemplate={() => onCreateTemplate?.()}
          />
        ) : selected ? (
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-2"><div><h3 className="text-sm font-semibold">{selected.name}</h3><p className="text-2xs text-muted-foreground">{INTENT_LABELS[selected.intent ?? ''] ?? 'Sem intenção'} · {selected.language}</p></div><KlmPill tone="green">APPROVED</KlmPill></div>
              <WhatsAppBubblePreview {...cardComponents(selected)} variableValues={previewValues} editable onVariableChange={(position, value) => setBindings((current) => ({ ...current, [`body:${position}`]: value }))} />
              <p className="text-2xs text-muted-foreground">O texto fixo não pode ser alterado neste envio. <button type="button" className="font-semibold text-primary underline" onClick={() => onCreateTemplate?.(selected.id)}>Editar modelo</button></p>
              <div className="rounded-md border bg-secondary/50 p-3 text-2xs leading-5"><p>Consentimento do contato: <strong>data não verificada</strong></p><p>Conversa iniciada pela empresa pode gerar custo segundo a tabela vigente da Meta.</p><p>Registrado como campanha de 1 destinatário.</p></div>
              <div className="flex flex-wrap justify-end gap-2"><Button type="button" variant="outline" onClick={() => setSelected(null)}>Voltar</Button><Button type="button" variant="outline" onClick={() => selectCard(selected, true)}>Criar sequência</Button><Button type="button" onClick={() => sendSelected(selected)} disabled={sendMutation.isPending}>{sendMutation.isPending ? <RefreshCw className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Send className="mr-1.5 h-3.5 w-3.5" />}Enviar modelo</Button></div>
            </div>
              <aside className="rounded-md border p-3"><h4 className="text-xs font-semibold">Bindings editáveis</h4><p className="mt-1 text-2xs text-muted-foreground">{contactName ? `Valores para ${contactName}.` : 'Valores do cadastro e do caso.'}</p>{variablesQuery.isError && <p className="mt-2 text-2xs text-red-700">Variáveis não carregadas. Código: {errorCode(variablesQuery.error)}</p>}<div className="mt-3 space-y-2">{selected.variables.map((variable) => { const key = `${variable.scope}:${variable.position}`; return <label key={key} className="block text-2xs"><span className="mb-1 block font-medium">{variable.scope} · variável {variable.position}</span><Input value={bindings[key] ?? ''} onChange={(event) => setBindings((current) => ({ ...current, [key]: event.target.value }))} placeholder={variable.example ?? 'Informe um valor'} className="h-8 text-xs" /></label>; })}</div></aside>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center gap-2"><div className="relative min-w-0 flex-1"><Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar no corpo, intenção ou nome" className="h-9 pl-8 text-xs" aria-label="Buscar modelo aprovado" /></div><Button type="button" variant="outline" size="sm" className="h-9" onClick={refresh} disabled={pickerQuery.isFetching} title="Atualizar modelos da Meta"><RefreshCw className={cn('mr-1.5 h-3.5 w-3.5', pickerQuery.isFetching && 'animate-spin')} />Atualizar</Button></div>
            <div className="flex flex-wrap gap-1.5"><button type="button" className={cn('rounded-full border px-2.5 py-1 text-2xs', !intent && 'bg-primary text-primary-foreground')} onClick={() => setIntent(null)}>Todas</button>{intents.map((key) => <button type="button" key={key} className={cn('rounded-full border px-2.5 py-1 text-2xs', intent === key && 'bg-primary text-primary-foreground')} onClick={() => setIntent(key)}>{INTENT_LABELS[key!] ?? key}</button>)}</div>
            {pickerQuery.isFetching && <p className="text-2xs text-muted-foreground">Atualizando modelos em segundo plano…</p>}
            {data?.refreshedAt && !pickerQuery.isFetching && <p className="text-2xs text-muted-foreground">Atualizado {new Date(data.refreshedAt).toLocaleString('pt-BR')}</p>}
            {pickerQuery.isError && <div role="alert" className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-900">Não foi possível carregar os modelos. Código: {errorCode(pickerQuery.error)} <Button variant="link" size="sm" onClick={refresh}>Tentar novamente</Button></div>}
            {suggestions.length > 0 && <section className="rounded-lg border border-primary/30 bg-primary/5 p-3"><h3 className="text-xs font-semibold">Sugerido para este caso</h3><div className="mt-2 grid gap-2 sm:grid-cols-3">{suggestions.map((suggestion) => { const card = data?.cards.find((item) => item.id === suggestion.templateId && normalizedStatus(item.status) === 'approved'); return card ? <button key={suggestion.templateId} type="button" className="rounded-md border bg-background p-2 text-left hover:border-primary" onClick={() => selectCard(card)}><strong className="block truncate text-2xs">{card.name}</strong><span className="mt-1 block text-2xs text-muted-foreground">{suggestion.reasonText}</span></button> : null; })}</div></section>}
            <section><div className="mb-2 flex flex-wrap items-center justify-between gap-2"><div><h3 className="text-xs font-semibold">Mais usados nos últimos 30 dias</h3><span className="text-2xs text-muted-foreground">{visibleCards.length} aprovado{visibleCards.length === 1 ? '' : 's'}</span></div>{data?.sectorMode === 'by_sector' && data.userSector ? <div className="flex items-center gap-2 text-2xs"><span className="text-muted-foreground">Setor: <strong>{SECTOR_LABELS[data.userSector] ?? data.userSector}</strong></span><Button type="button" variant="link" size="sm" className="h-7 px-1 text-2xs" onClick={() => setShowAllSectors((current) => !current)}>{showAllSectors ? 'Ver só meu setor' : 'Ver todos'}</Button></div> : null}</div>{visibleCards.length ? <div className="grid gap-2 sm:grid-cols-2">{visibleCards.map((card) => <div key={card.id} role="button" tabIndex={0} onClick={() => selectCard(card)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') selectCard(card); }} className="rounded-lg border p-3 text-left transition hover:border-primary"><div className="flex items-start justify-between gap-2"><strong className="text-xs">{card.name}</strong><KlmPill tone="muted">{card.metaCategory ?? 'não verificada'}</KlmPill></div><p className="mt-2 line-clamp-3 whitespace-pre-wrap text-2xs leading-4">{card.bodyText}</p><div className="mt-2 flex flex-wrap items-center gap-1.5">{card.intent && <KlmPill tone="blue">{INTENT_LABELS[card.intent] ?? card.intent}</KlmPill>}{card.sector && <KlmPill tone="muted">{SECTOR_LABELS[card.sector] ?? card.sector}</KlmPill>}{card.buttons.map((button, index) => <span key={index} className="rounded-full border px-2 py-0.5 text-[10px]">{button.label ?? button.text ?? 'Botão'}</span>)}<span className="ml-auto text-2xs text-muted-foreground">{card.lastSentToContactAt && Date.now() - new Date(card.lastSentToContactAt).getTime() < 7 * 86400000 ? 'Repetição: contato recebeu nos últimos 7 dias' : `${card.usageCount30d ?? 0} uso${card.usageCount30d === 1 ? '' : 's'} em 30 dias`}</span></div><div className="mt-3 flex justify-end"><Button type="button" variant="outline" size="sm" className="h-7 text-[11px]" onClick={(event) => { event.stopPropagation(); selectCard(card, true); }}>Criar sequência</Button></div></div>)}</div> : <p className="rounded-md border border-dashed p-6 text-center text-xs text-muted-foreground">Nenhum modelo aprovado corresponde à busca.</p>}</section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
