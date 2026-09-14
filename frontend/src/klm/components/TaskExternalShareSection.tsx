import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Clipboard, Eye, Link2, LoaderCircle, ShieldCheck, XCircle } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { TaskWhatsAppSharePreparation } from './TaskWhatsAppSharePreparation';
import {
  createTaskExternalShare,
  listTaskExternalShares,
  previewTaskExternalShare,
  revokeTaskExternalShare,
  type TaskExternalShareIssued,
  type TaskExternalSharePreview,
} from '@/services/task-external-shares';

function defaultExpiry(): string {
  const value = new Date(Date.now() + 7 * 86400_000);
  value.setSeconds(0, 0);
  const offset = value.getTimezoneOffset() * 60_000;
  return new Date(value.getTime() - offset).toISOString().slice(0, 16);
}
function formatDate(value: string): string {
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value));
}

export function TaskExternalShareSection({ taskId, canEdit, canPrepareWhatsApp = false }: { taskId: string; canEdit: boolean; canPrepareWhatsApp?: boolean }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [expiresAt, setExpiresAt] = useState(defaultExpiry);
  const [safeContext, setSafeContext] = useState('');
  const [preview, setPreview] = useState<TaskExternalSharePreview | null>(null);
  const [issued, setIssued] = useState<TaskExternalShareIssued | null>(null);
  const expiryIso = useMemo(() => {
    const date = new Date(expiresAt);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
  }, [expiresAt]);

  const linksQuery = useQuery({
    queryKey: ['task-external-shares', taskId],
    queryFn: () => listTaskExternalShares(taskId),
    enabled: canEdit,
    retry: false,
  });
  const previewMutation = useMutation({
    mutationFn: () => previewTaskExternalShare(taskId, expiryIso!, safeContext.trim() || null),
    onSuccess: (result) => { setPreview(result); setIssued(null); },
  });
  const createMutation = useMutation({
    mutationFn: () => createTaskExternalShare(taskId, expiryIso!, safeContext.trim() || null, preview!.previewFingerprint),
    onSuccess: async (result) => {
      setIssued(result);
      setPreview(null);
      await queryClient.invalidateQueries({ queryKey: ['task-external-shares', taskId] });
    },
  });
  const revokeMutation = useMutation({
    mutationFn: (shareId: string) => revokeTaskExternalShare(taskId, shareId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['task-external-shares', taskId] });
      toast({ title: 'Link revogado', description: 'O acesso externo foi encerrado.' });
    },
  });

  const copyIssuedLink = async () => {
    if (!issued) return;
    await navigator.clipboard.writeText(issued.url);
    toast({ title: 'Link copiado', description: 'Nenhuma mensagem foi enviada automaticamente.' });
  };

  if (!canEdit) {
    return <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">Criar ou revogar links exige permissão para editar tarefas.</p>;
  }

  return (
    <div className="space-y-5" data-testid="task-external-share-section">
      <Alert>
        <ShieldCheck className="h-4 w-4" />
        <AlertTitle>Compartilhamento externo somente leitura</AlertTitle>
        <AlertDescription>O link mostra apenas título, status, prazo e o contexto seguro escrito abaixo. Comentários, histórico, equipe, descrição interna e anexos ficam excluídos.</AlertDescription>
      </Alert>
      <div className="grid gap-4 rounded-lg border bg-card p-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="task-share-expiry">Validade do link</Label>
          <Input id="task-share-expiry" type="datetime-local" value={expiresAt} onChange={(event) => { setExpiresAt(event.target.value); setPreview(null); }} />
          <p className="text-xs text-muted-foreground">Máximo de 30 dias. Confirme a validade na prévia antes de criar.</p>
        </div>
        <div className="space-y-2 sm:col-span-2">
          <Label htmlFor="task-share-context">Contexto seguro opcional</Label>
          <Textarea id="task-share-context" maxLength={1000} value={safeContext} onChange={(event) => { setSafeContext(event.target.value); setPreview(null); }} placeholder="Escreva somente o que pode ser visto fora do escritório." />
        </div>
        <div className="sm:col-span-2">
          <Button type="button" variant="outline" disabled={!expiryIso || previewMutation.isPending} onClick={() => previewMutation.mutate()}>
            {previewMutation.isPending ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Eye className="mr-2 h-4 w-4" />}Revisar prévia
          </Button>
        </div>
      </div>
      {preview && (
        <section className="space-y-3 rounded-lg border border-amber-500/40 bg-amber-500/5 p-4" aria-label="Prévia do link externo">
          <h3 className="font-semibold">Confirme exatamente o que será compartilhado</h3>
          <dl className="grid gap-2 text-sm sm:grid-cols-2"><div><dt className="text-xs text-muted-foreground">Título</dt><dd>{preview.title}</dd></div><div><dt className="text-xs text-muted-foreground">Status</dt><dd>{preview.status}</dd></div><div><dt className="text-xs text-muted-foreground">Prazo</dt><dd>{preview.dueAt ? formatDate(preview.dueAt) : 'Não definido'}</dd></div><div><dt className="text-xs text-muted-foreground">Validade</dt><dd>{formatDate(preview.expiresAt)}</dd></div></dl>
          {preview.safeContext && <p className="rounded-md bg-background p-3 text-sm">{preview.safeContext}</p>}
          <p className="text-xs text-muted-foreground">Excluído: {preview.excludedContent.join(', ')}.</p>
          <Button type="button" disabled={createMutation.isPending} onClick={() => createMutation.mutate()}>{createMutation.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Confirmar e criar link</Button>
        </section>
      )}
      {issued && (
        <><Alert className="border-emerald-600/30 bg-emerald-500/5">
          <Check className="h-4 w-4" /><AlertTitle>Link criado</AlertTitle>
          <AlertDescription className="space-y-3"><p>Copie agora. Por segurança, o token não será mostrado novamente. Validade: {formatDate(issued.expiresAt)}.</p><Button size="sm" onClick={() => void copyIssuedLink()}><Clipboard className="mr-2 h-4 w-4" />Copiar link</Button><p className="text-xs">Nenhum download ou envio por WhatsApp foi realizado.</p></AlertDescription>
        </Alert>{canPrepareWhatsApp && <TaskWhatsAppSharePreparation taskId={taskId} shareId={issued.id} shareUrl={issued.url} />}</>
      )}
      <section className="space-y-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold"><Link2 className="h-4 w-4" />Links criados</h3>
        {linksQuery.isPending && <p className="text-sm text-muted-foreground">Carregando links…</p>}
        {linksQuery.data?.length === 0 && <p className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground">Nenhum link criado.</p>}
        {linksQuery.data?.map((link) => <div key={link.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3"><div><p className="text-sm font-medium">{link.revokedAt ? 'Revogado' : new Date(link.expiresAt) <= new Date() ? 'Expirado' : 'Ativo'}</p><p className="text-xs text-muted-foreground">Validade: {formatDate(link.expiresAt)}</p></div>{!link.revokedAt && new Date(link.expiresAt) > new Date() && <Button size="sm" variant="destructive" disabled={revokeMutation.isPending} onClick={() => revokeMutation.mutate(link.id)}><XCircle className="mr-2 h-4 w-4" />Revogar</Button>}</div>)}
      </section>
    </div>
  );
}
