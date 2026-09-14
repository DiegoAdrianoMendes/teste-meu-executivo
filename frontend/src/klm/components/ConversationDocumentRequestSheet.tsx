import { useEffect, useMemo, useState } from 'react';
import { CheckCircle2, FilePlus2, LoaderCircle, RotateCcw } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Textarea } from '@/components/ui/textarea';
import type { KlmMatterDetail } from '@/klm/api';
import { KlmPill } from '@/klm/components/Primitives';

type DocumentOption = {
  id: string;
  label: string;
  aliases: string[];
  available: boolean;
};

type DocumentRequestPayload = {
  body: string;
  documents: string[];
  followUpHours: number;
};

type ConversationDocumentRequestSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  contactName: string;
  matter?: KlmMatterDetail;
  loading?: boolean;
  submitting?: boolean;
  onPrepare: (payload: DocumentRequestPayload) => void;
};

const baseOptions = [
  { id: 'medical-report', label: 'Relatório ou laudo médico atualizado', aliases: ['relatorio', 'laudo'] },
  { id: 'prescription', label: 'Prescrição médica', aliases: ['prescricao', 'receita'] },
  { id: 'denial', label: 'Negativa formal do plano de saúde', aliases: ['negativa', 'indeferimento'] },
  { id: 'health-card', label: 'Carteirinha do plano de saúde', aliases: ['carteirinha', 'cartao do plano'] },
  { id: 'identity', label: 'Documento pessoal com foto', aliases: ['identidade', 'rg', 'cnh'] },
  { id: 'address', label: 'Comprovante de endereço', aliases: ['comprovante de endereco', 'endereco'] },
];

function normalize(value: string) {
  return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('pt-BR').trim();
}

function buildMessage(contactName: string, documents: string[]) {
  const name = contactName.trim() || 'Olá';
  if (!documents.length) return `${name}, para concluir a análise do seu caso, preciso que você envie os documentos pendentes por aqui.`;
  const list = documents.map((document) => `- ${document}`).join('\n');
  return `${name}, para concluir a análise do seu caso, preciso destes documentos:\n\n${list}\n\nVocê consegue enviá-los por aqui? Se algum ainda não estiver disponível, me avise para organizarmos o próximo passo.`;
}

export function ConversationDocumentRequestSheet({
  open,
  onOpenChange,
  contactName,
  matter,
  loading = false,
  submitting = false,
  onPrepare,
}: ConversationDocumentRequestSheetProps) {
  const options = useMemo<DocumentOption[]>(() => {
    const uploaded = (matter?.documents ?? []).map((document) => normalize(`${document.name} ${document.type ?? ''}`));
    const requested = matter?.conversationInsight?.requestedDocuments ?? [];
    const seen = new Set<string>();
    const rows = [
      ...baseOptions,
      ...requested.map((label, index) => ({ id: `insight-${index}`, label, aliases: [label] })),
    ];
    return rows.flatMap((row) => {
      const key = normalize(row.label);
      if (!key || seen.has(key)) return [];
      seen.add(key);
      return [{
        ...row,
        available: uploaded.some((document) => row.aliases.some((alias) => document.includes(normalize(alias)))),
      }];
    });
  }, [matter]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [customRequest, setCustomRequest] = useState('');
  const [followUpHours, setFollowUpHours] = useState(18);
  const [body, setBody] = useState('');
  const [bodyTouched, setBodyTouched] = useState(false);

  const selectedDocuments = useMemo(() => {
    const selected = options.filter((option) => selectedIds.includes(option.id)).map((option) => option.label);
    const custom = customRequest.trim();
    return custom ? [...selected, custom] : selected;
  }, [customRequest, options, selectedIds]);
  const suggestedBody = useMemo(() => buildMessage(contactName, selectedDocuments), [contactName, selectedDocuments]);

  useEffect(() => {
    if (!open) return;
    const pending = options.filter((option) => !option.available).map((option) => option.id);
    setSelectedIds(pending.length ? pending : options.map((option) => option.id));
    setCustomRequest('');
    setFollowUpHours(18);
    setBodyTouched(false);
  }, [matter?.id, open, options]);

  useEffect(() => {
    if (open && !bodyTouched) setBody(suggestedBody);
  }, [bodyTouched, open, suggestedBody]);

  const toggleDocument = (id: string, checked: boolean) => {
    setSelectedIds((current) => checked ? [...new Set([...current, id])] : current.filter((item) => item !== id));
  };

  const boundedHours = Math.min(720, Math.max(1, Number.isFinite(followUpHours) ? followUpHours : 18));
  const canPrepare = Boolean(body.trim() && selectedDocuments.length && !loading && !submitting);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-[96vw] flex-col overflow-hidden p-0 sm:max-w-[620px]">
        <SheetHeader className="border-b border-border px-5 py-5 pr-12 text-left">
          <div className="mb-2 flex h-9 w-9 items-center justify-center rounded-md bg-[#031121] text-primary dark:bg-white dark:text-[#0057d8]"><FilePlus2 className="h-4 w-4" /></div>
          <SheetTitle>Solicitar documentos</SheetTitle>
          <SheetDescription>Escolha o que falta, revise a mensagem e programe a retomada. Nada será enviado antes da confirmação humana no composer.</SheetDescription>
        </SheetHeader>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-5 scrollbar-thin">
          <section>
            <div className="mb-3 flex items-center justify-between gap-3">
              <div><h3 className="text-sm font-semibold">Checklist do caso</h3><p className="mt-1 text-2xs text-muted-foreground">Itens já presentes no card continuam disponíveis para pedir uma versão atualizada.</p></div>
              <KlmPill tone="gold">{selectedDocuments.length} selecionado{selectedDocuments.length === 1 ? '' : 's'}</KlmPill>
            </div>
            {loading ? <div className="flex min-h-36 items-center justify-center gap-2 text-2xs text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin" />Conferindo arquivos do Caso 360</div> : <div className="grid gap-2 sm:grid-cols-2">{options.map((option) => (
              <label key={option.id} className="flex min-h-14 cursor-pointer items-start gap-2.5 rounded-md border border-border p-3 hover:border-primary/60">
                <Checkbox checked={selectedIds.includes(option.id)} onCheckedChange={(checked) => toggleDocument(option.id, checked === true)} aria-label={option.label} />
                <span className="min-w-0 text-2xs leading-4"><strong className="block font-medium">{option.label}</strong>{option.available ? <span className="mt-0.5 inline-flex items-center gap-1 text-[#0b7435] dark:text-emerald-400"><CheckCircle2 className="h-3 w-3" />já consta no card</span> : <span className="mt-0.5 block text-muted-foreground">pendente ou não identificado</span>}</span>
              </label>
            ))}</div>}
          </section>

          <div className="space-y-1.5">
            <Label htmlFor="conversation-custom-document">Outro documento ou informação</Label>
            <Input id="conversation-custom-document" value={customRequest} onChange={(event) => setCustomRequest(event.target.value)} placeholder="Ex.: orçamento atualizado do medicamento" />
          </div>

          <div className="space-y-1.5">
            <div className="flex items-center justify-between gap-2"><Label htmlFor="conversation-document-message">Mensagem preparada</Label><Button type="button" variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => { setBody(suggestedBody); setBodyTouched(false); }}><RotateCcw className="mr-1 h-3 w-3" />Restaurar sugestão</Button></div>
            <Textarea id="conversation-document-message" className="min-h-36 text-2xs leading-5" value={body} onChange={(event) => { setBody(event.target.value); setBodyTouched(true); }} />
            <p className="text-2xs leading-4 text-muted-foreground">O texto será colocado no rascunho da conversa. O atendente ainda precisará clicar em Preparar envio.</p>
          </div>

          <div className="grid gap-2 sm:grid-cols-[180px_minmax(0,1fr)] sm:items-end">
            <div className="space-y-1.5"><Label htmlFor="conversation-document-follow-up">Retomar se não responder</Label><Input id="conversation-document-follow-up" type="number" min={1} max={720} value={followUpHours} onChange={(event) => setFollowUpHours(Number(event.target.value))} /></div>
            <p className="rounded-md border border-primary/25 bg-primary/5 px-3 py-2 text-2xs leading-4 text-muted-foreground">Follow-up local em <strong className="text-foreground">{boundedHours} hora{boundedHours === 1 ? '' : 's'}</strong>, vinculado a este caso e atendimento.</p>
          </div>
        </div>

        <SheetFooter className="gap-2 border-t border-border bg-card px-5 py-4">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancelar</Button>
          <Button type="button" disabled={!canPrepare} onClick={() => onPrepare({ body: body.trim(), documents: selectedDocuments, followUpHours: boundedHours })}>{submitting ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <FilePlus2 className="mr-2 h-4 w-4" />}{submitting ? 'Programando' : 'Preparar pedido e follow-up'}</Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
