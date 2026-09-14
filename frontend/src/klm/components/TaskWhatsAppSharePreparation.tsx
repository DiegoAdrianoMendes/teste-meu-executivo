import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ExternalLink, Eye, LoaderCircle, MessageCircle, ShieldAlert } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  listTaskWhatsAppContacts,
  prepareTaskWhatsAppDraft,
  previewTaskWhatsAppDraft,
  type TaskWhatsAppDraftPrepared,
  type TaskWhatsAppDraftPreview,
} from '@/services/task-whatsapp-share';

export function TaskWhatsAppSharePreparation({ taskId, shareId, shareUrl }: { taskId: string; shareId: string; shareUrl: string }) {
  const [contactRef, setContactRef] = useState('');
  const [messageBody, setMessageBody] = useState(`Olá! Segue o link seguro e somente leitura da tarefa jurídica:\n${shareUrl}`);
  const [preview, setPreview] = useState<TaskWhatsAppDraftPreview | null>(null);
  const [prepared, setPrepared] = useState<TaskWhatsAppDraftPrepared | null>(null);
  const contactsQuery = useQuery({ queryKey: ['task-whatsapp-share-contacts', taskId], queryFn: () => listTaskWhatsAppContacts(taskId), retry: false });
  useEffect(() => {
    if (!contactRef && contactsQuery.data?.length === 1) setContactRef(contactsQuery.data[0].contactRef);
  }, [contactRef, contactsQuery.data]);
  const input = { contactRef, shareId, shareUrl, messageBody };
  const previewMutation = useMutation({ mutationFn: () => previewTaskWhatsAppDraft(taskId, input), onSuccess: (value) => { setPreview(value); setPrepared(null); } });
  const prepareMutation = useMutation({ mutationFn: () => prepareTaskWhatsAppDraft(taskId, { ...input, previewFingerprint: preview!.previewFingerprint, confirmed: true }), onSuccess: setPrepared });

  return (
    <section className="space-y-4 rounded-lg border border-emerald-600/30 bg-emerald-500/5 p-4" data-testid="task-whatsapp-share-preparation">
      <div><h3 className="flex items-center gap-2 font-semibold"><MessageCircle className="h-4 w-4" />Preparar compartilhamento via WhatsApp</h3><p className="text-xs text-muted-foreground">A ação abaixo cria somente um rascunho persistido em Conversas. Não abre WhatsApp Web e não envia mensagem.</p></div>
      {contactsQuery.data?.length ? <div className="space-y-2"><Label htmlFor="task-wa-contact">Contato humano</Label><select id="task-wa-contact" className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={contactRef} onChange={(event) => { setContactRef(event.target.value); setPreview(null); }}><option value="">Selecione pelo nome</option>{contactsQuery.data.map((contact) => <option key={contact.contactRef} value={contact.contactRef}>{contact.label}{contact.maskedAddress ? ` — ${contact.maskedAddress}` : ''}</option>)}</select></div> : <Alert><ShieldAlert className="h-4 w-4" /><AlertTitle>Sem contato elegível no contexto</AlertTitle><AlertDescription>Vincule a tarefa a um caso ou cliente com conversa WhatsApp canônica para preparar o rascunho.</AlertDescription></Alert>}
      <div className="space-y-2"><Label htmlFor="task-wa-message">Texto editável</Label><Textarea id="task-wa-message" value={messageBody} maxLength={4096} onChange={(event) => { setMessageBody(event.target.value); setPreview(null); }} /></div>
      <Button type="button" variant="outline" disabled={!contactRef || !messageBody.trim() || previewMutation.isPending} onClick={() => previewMutation.mutate()}>{previewMutation.isPending ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Eye className="mr-2 h-4 w-4" />}Validar e revisar rascunho</Button>
      {preview && <Alert className={preview.contact.sendEligible ? 'border-emerald-600/30' : 'border-amber-500/40'}><ShieldAlert className="h-4 w-4" /><AlertTitle>{preview.contact.label}: {preview.contact.sendEligible ? 'gate atual apto' : 'gate atual exige revisão'}</AlertTitle><AlertDescription className="space-y-2"><p>Consentimento: {preview.contact.consentState}. Janela de 24h: {preview.contact.serviceWindowState === 'open' ? 'aberta' : 'fechada'}.</p>{preview.contact.gateReasons.length > 0 && <p>Motivos: {preview.contact.gateReasons.join(', ')}.</p>}<p className="rounded bg-background p-2 whitespace-pre-wrap">{preview.messageBody}</p><p>Nenhuma ação externa será executada. Confirme para salvar como rascunho local.</p><Button type="button" disabled={prepareMutation.isPending} onClick={() => prepareMutation.mutate()}>{prepareMutation.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Confirmar e preparar em Conversas</Button></AlertDescription></Alert>}
      {prepared && <Alert className="border-emerald-600/30"><MessageCircle className="h-4 w-4" /><AlertTitle>Rascunho preparado, sem envio</AlertTitle><AlertDescription className="space-y-2"><p>Status: rascunho local. Ações externas executadas: {prepared.externalActionsExecuted}.</p><Button asChild size="sm"><a href={prepared.conversationPath}><ExternalLink className="mr-2 h-4 w-4" />Abrir Conversas para revisão humana</a></Button></AlertDescription></Alert>}
    </section>
  );
}
