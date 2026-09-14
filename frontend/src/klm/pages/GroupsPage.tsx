import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle2,
  Circle,
  Copy,
  LoaderCircle,
  Lock,
  MessagesSquare,
  Pin,
  PinOff,
  Plus,
  RefreshCw,
  Send,
  Sparkles,
  TriangleAlert,
  UserMinus,
  Users,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { KlmPanel, KlmPill } from '@/klm/components/Primitives';
import { PermissionGate } from '@/klm/components/PermissionGate';
import { useAppStore } from '@/store/app-context';
import { ApiError } from '@/services/http';
import { cn } from '@/lib/utils';
import {
  klmApi,
  type KlmMetaGroup,
} from '@/klm/api';

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail as { message?: string } | undefined;
    return detail?.message ?? error.message;
  }
  return error instanceof Error ? error.message : 'Tente novamente.';
}

function groupInitials(subject: string): string {
  const clean = subject.trim();
  if (!clean) return 'GR';
  const parts = clean.split(/\s+/).slice(0, 2);
  return parts.map((part) => part[0]?.toUpperCase() ?? '').join('') || 'GR';
}

function formatRelativeTime(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return '';
  const diffMs = Date.now() - date.getTime();
  const diffMin = Math.round(diffMs / 60_000);
  if (diffMin < 1) return 'agora';
  if (diffMin < 60) return `${diffMin} min`;
  const diffHours = Math.round(diffMin / 60);
  if (diffHours < 24) return `${diffHours} h`;
  const diffDays = Math.round(diffHours / 24);
  return `${diffDays} d`;
}

function formatDateTime(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return '';
  return date.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
}

type CreateGroupDraft = {
  subject: string;
  description: string;
  kind: 'local_internal' | 'meta_cloud_group';
};

function CreateGroupDialog({
  open,
  onOpenChange,
  onSubmit,
  pending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (draft: CreateGroupDraft) => void;
  pending: boolean;
}) {
  const [draft, setDraft] = useState<CreateGroupDraft>({ subject: '', description: '', kind: 'local_internal' });

  useEffect(() => {
    if (open) setDraft({ subject: '', description: '', kind: 'local_internal' });
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle>Novo grupo</DialogTitle>
          <DialogDescription>Grupos internos ficam prontos na hora. Grupos oficiais são preparados e só ficam ativos quando a Meta confirmar por webhook verificado.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <div className="grid gap-1.5">
            <Label htmlFor="group-subject">Nome do grupo</Label>
            <Input id="group-subject" value={draft.subject} maxLength={128} onChange={(event) => setDraft((current) => ({ ...current, subject: event.target.value }))} placeholder="Ex.: Caso Silva x Unimed" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="group-description">Descrição (opcional)</Label>
            <Textarea id="group-description" value={draft.description} maxLength={2048} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} placeholder="Contexto do grupo, propósito, caso vinculado." rows={3} />
          </div>
          <div className="grid gap-1.5">
            <Label>Tipo</Label>
            <RadioGroup value={draft.kind} onValueChange={(value) => setDraft((current) => ({ ...current, kind: value as CreateGroupDraft['kind'] }))} className="gap-2">
              <label className="flex items-start gap-2 rounded-md border border-border p-2.5 text-2xs leading-4 has-[[data-state=checked]]:border-primary has-[[data-state=checked]]:bg-primary/5">
                <RadioGroupItem value="local_internal" id="kind-internal" className="mt-0.5" />
                <span><strong className="block text-foreground">Interno</strong>Fica pronto imediatamente para uso da equipe; não depende da API da Meta.</span>
              </label>
              <label className="flex items-start gap-2 rounded-md border border-border p-2.5 text-2xs leading-4 has-[[data-state=checked]]:border-primary has-[[data-state=checked]]:bg-primary/5">
                <RadioGroupItem value="meta_cloud_group" id="kind-official" className="mt-0.5" />
                <span><strong className="block text-foreground">Oficial (WhatsApp Groups API)</strong>É apenas preparado agora; vira grupo ativo só quando a Meta confirmar a criação por webhook.</span>
              </label>
            </RadioGroup>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>Cancelar</Button>
          <Button onClick={() => onSubmit(draft)} disabled={pending || !draft.subject.trim()}>
            {pending ? <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Plus className="mr-1.5 h-3.5 w-3.5" />}
            {draft.kind === 'local_internal' ? 'Criar grupo' : 'Preparar criação oficial'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

type WizardStepState = 'done' | 'pending' | 'info';

function WizardStep({ index, title, state, children }: { index: number; title: string; state: WizardStepState; children: ReactNode }) {
  const Icon = state === 'done' ? CheckCircle2 : state === 'info' ? Sparkles : Circle;
  return (
    <div className="flex gap-3 rounded-lg border border-border p-3">
      <div className={cn(
        'mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-2xs font-semibold',
        state === 'done' && 'bg-emerald-500/15 text-[#0b7435] dark:text-emerald-400',
        state === 'pending' && 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
        state === 'info' && 'bg-primary/10 text-[#0057d8] dark:text-[#8fc1ff]',
      )}>
        <Icon className="h-3.5 w-3.5" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-2xs font-semibold text-muted-foreground">Passo {index}</span>
          <KlmPill tone={state === 'done' ? 'green' : state === 'info' ? 'blue' : 'gold'}>
            {state === 'done' ? 'concluído' : state === 'info' ? 'boas práticas' : 'pendente'}
          </KlmPill>
        </div>
        <h3 className="mt-0.5 text-sm font-semibold text-foreground">{title}</h3>
        <div className="mt-1 text-2xs leading-5 text-muted-foreground">{children}</div>
      </div>
    </div>
  );
}

export default function GroupsPage() {
  const { toast } = useToast();
  const { can } = useAppStore();
  const canSend = can('mensagens', 'send_message');
  const queryClient = useQueryClient();

  const [connectionId, setConnectionId] = useState<string | null>(null);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [pinDraft, setPinDraft] = useState('');

  const connectionsQuery = useQuery({
    queryKey: ['klm', 'omnichannel-connections', 'whatsapp'],
    queryFn: () => klmApi.omnichannelConnections('whatsapp'),
  });

  useEffect(() => {
    if (!connectionId && connectionsQuery.data && connectionsQuery.data.length > 0) {
      setConnectionId(connectionsQuery.data[0].id);
    }
  }, [connectionId, connectionsQuery.data]);

  const groupsQuery = useQuery({
    queryKey: ['klm', 'meta-groups', connectionId],
    queryFn: () => klmApi.metaGroups(connectionId as string),
    enabled: Boolean(connectionId),
  });

  const groups = useMemo(() => groupsQuery.data?.items ?? [], [groupsQuery.data]);

  useEffect(() => {
    if (groups.length === 0) return;
    if (!selectedGroupId || !groups.some((group) => group.id === selectedGroupId)) {
      setSelectedGroupId(groups[0].id);
    }
  }, [groups, selectedGroupId]);

  const selectedGroup: KlmMetaGroup | null = groups.find((group) => group.id === selectedGroupId) ?? null;

  const capabilityQuery = useQuery({
    queryKey: ['klm', 'meta-group-capability', connectionId],
    queryFn: () => klmApi.metaGroupCapability(connectionId as string),
    enabled: Boolean(connectionId),
  });

  const messagesQuery = useQuery({
    queryKey: ['klm', 'meta-group-messages', selectedGroupId],
    queryFn: () => klmApi.metaGroupMessages(selectedGroupId as string),
    enabled: Boolean(selectedGroupId),
  });

  const participantsQuery = useQuery({
    queryKey: ['klm', 'meta-group-participants', selectedGroupId],
    queryFn: () => klmApi.metaGroupParticipants(selectedGroupId as string),
    enabled: Boolean(selectedGroupId),
  });

  const invalidateSelectedGroup = () => {
    queryClient.invalidateQueries({ queryKey: ['klm', 'meta-groups', connectionId] });
    queryClient.invalidateQueries({ queryKey: ['klm', 'meta-group-messages', selectedGroupId] });
    queryClient.invalidateQueries({ queryKey: ['klm', 'meta-group-participants', selectedGroupId] });
  };

  const createInternalMutation = useMutation({
    mutationFn: (payload: CreateGroupDraft) =>
      klmApi.createInternalMetaGroup(connectionId as string, {
        subject: payload.subject.trim(),
        description: payload.description.trim() || null,
        joinApprovalMode: 'auto_approve',
      }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['klm', 'meta-groups', connectionId] });
      setSelectedGroupId(result.group.id);
      setCreateOpen(false);
      toast({ title: 'Grupo interno criado', description: 'Já está pronto para uso da equipe.' });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível criar o grupo', description: errorMessage(error) }),
  });

  const prepareOfficialMutation = useMutation({
    mutationFn: (payload: CreateGroupDraft) =>
      klmApi.prepareOfficialMetaGroupCreation(connectionId as string, {
        subject: payload.subject.trim(),
        description: payload.description.trim() || null,
        joinApprovalMode: 'approval_required',
      }),
    onSuccess: (result) => {
      setCreateOpen(false);
      toast({
        title: result.providerRequestPayloadReady ? 'Criação oficial preparada' : 'Criação oficial bloqueada pela capability',
        description: result.providerRequestPayloadReady
          ? 'O payload foi validado e aguarda a confirmação da Meta por webhook verificado para virar um grupo ativo.'
          : (result.blockedReasons[0] ?? 'Configure a capability OBA da conexão antes de preparar grupos oficiais.'),
      });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível preparar o grupo oficial', description: errorMessage(error) }),
  });

  // As cinco mutações abaixo faziam `selectedGroupId as string` — um cast que
  // mente: o valor pode ser null enquanto a lista de grupos ainda carrega. Quem
  // clicasse antes de a seleção assentar disparava a chamada com null no lugar
  // do id, e o cast calava justamente o TypeScript que avisaria. Agora a guarda
  // falha a mutação antes da rede, e o erro aparece no toast como qualquer
  // outro em vez de virar requisição inválida.
  const requireSelectedGroupId = (): string => {
    if (!selectedGroupId) {
      throw new Error('Selecione um grupo antes de executar esta ação.');
    }
    return selectedGroupId;
  };

  const sendMessageMutation = useMutation({
    mutationFn: (body: string) => klmApi.prepareMetaGroupMessage(requireSelectedGroupId(), body),
    onSuccess: (result) => {
      setDraft('');
      invalidateSelectedGroup();
      if (!result.providerPayloadReady) {
        toast({ title: 'Mensagem preparada, aguardando ativação', description: 'Registrada e auditada localmente; o envio real depende da capability oficial ou é apenas interno.' });
      }
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível preparar a mensagem', description: errorMessage(error) }),
  });

  const rotateInviteMutation = useMutation({
    mutationFn: () => klmApi.rotateMetaGroupInviteLink(requireSelectedGroupId()),
    onSuccess: (result) => {
      invalidateSelectedGroup();
      toast({
        title: result.rotated ? 'Link de convite rotacionado' : 'Rotação preparada, aguardando ativação',
        description: result.rotated
          ? 'Link local simulado gerado e salvo neste grupo.'
          : (result.blockedReasons[0] ?? 'O link oficial só chega via webhook confirmado da Meta.'),
      });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível rotacionar o link', description: errorMessage(error) }),
  });

  const removeParticipantMutation = useMutation({
    mutationFn: (participantId: string) => klmApi.removeMetaGroupParticipant(requireSelectedGroupId(), participantId),
    onSuccess: (result) => {
      invalidateSelectedGroup();
      toast({
        title: result.executed ? 'Participante removido' : 'Remoção preparada, aguardando ativação',
        description: result.executed
          ? 'A remoção foi aplicada localmente.'
          : (result.blockedReasons[0] ?? 'A remoção oficial depende de confirmação via webhook da Meta.'),
      });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível remover o participante', description: errorMessage(error) }),
  });

  const pinMutation = useMutation({
    mutationFn: (text: string) => klmApi.setMetaGroupPinnedMessage(requireSelectedGroupId(), text),
    onSuccess: () => {
      setPinDraft('');
      invalidateSelectedGroup();
      toast({ title: 'Mensagem fixada' });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível fixar a mensagem', description: errorMessage(error) }),
  });

  const unpinMutation = useMutation({
    mutationFn: () => klmApi.clearMetaGroupPinnedMessage(requireSelectedGroupId()),
    onSuccess: () => {
      invalidateSelectedGroup();
      toast({ title: 'Fixação removida' });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível remover a fixação', description: errorMessage(error) }),
  });

  const copyInviteLink = async (link: string) => {
    try {
      await navigator.clipboard.writeText(link);
      toast({ title: 'Link copiado' });
    } catch {
      toast({ variant: 'destructive', title: 'Não foi possível copiar o link', description: 'Copie manualmente o texto exibido.' });
    }
  };

  const gate = capabilityQuery.data?.gate ?? null;
  const stepConnectionDone = Boolean(gate?.developerAppReady && gate?.obaGateOpen);
  const stepGroupDone = groups.length > 0;
  const stepInviteDone = Boolean(selectedGroup?.inviteLink);

  return (
    <div className="mx-auto flex h-full min-h-0 max-w-[1600px] flex-col gap-2 overflow-hidden">
      <div data-testid="groups-workspace-toolbar" className="flex min-h-8 shrink-0 flex-col gap-1 border-b border-border pb-1.5 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <h2 className="mr-0.5 text-sm font-semibold"><Users className="mr-1.5 inline h-4 w-4 -translate-y-0.5" />Grupos</h2>
            {!groupsQuery.isError && <KlmPill tone="muted">{groups.length} grupo{groups.length === 1 ? '' : 's'}</KlmPill>}
          </div>
          <p className="mt-0.5 truncate text-2xs text-muted-foreground">Grupos internos e oficiais do WhatsApp (Groups API) nesta conexão.</p>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-1.5 sm:justify-end">
          {connectionsQuery.data && connectionsQuery.data.length > 1 && (
            <Select value={connectionId ?? undefined} onValueChange={setConnectionId}>
              <SelectTrigger className="h-8 w-[220px] text-xs"><SelectValue placeholder="Selecione a conexão" /></SelectTrigger>
              <SelectContent>
                {connectionsQuery.data.map((connection) => (
                  <SelectItem key={connection.id} value={connection.id}>{connection.displayName}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <PermissionGate module="mensagens" action="send_message" actionLabel="criar grupos" variant="lock">
            <Button variant="outline" size="sm" className="h-8 shrink-0 px-2.5 text-xs" onClick={() => setCreateOpen(true)} disabled={!connectionId}>
              <Plus className="mr-1.5 h-3.5 w-3.5" />Novo grupo
            </Button>
          </PermissionGate>
        </div>
      </div>

      {connectionsQuery.isSuccess && connectionsQuery.data.length === 0 && (
        <div role="status" className="rounded-lg border border-dashed border-border bg-secondary/40 px-4 py-3 text-2xs leading-4 text-muted-foreground">
          <strong className="text-foreground">Nenhuma conexão WhatsApp configurada.</strong> Configure uma conexão em Integrações para criar grupos.
        </div>
      )}

      <div className={cn('relative grid min-h-0 flex-1 gap-3', 'lg:grid-cols-[260px_minmax(480px,1fr)_300px]')}>
        <div className="h-full min-h-0 min-w-0 overflow-hidden">
          <KlmPanel className="flex h-full min-h-0 flex-col overflow-hidden">
            <div className="border-b border-border px-3 py-2.5 text-2xs font-semibold text-muted-foreground">Grupos</div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {groupsQuery.isPending && (
                <div className="space-y-2 p-3">{[0, 1, 2].map((index) => <Skeleton key={index} className="h-14 w-full rounded-md" />)}</div>
              )}
              {groupsQuery.isError && (
                <div role="status" className="flex flex-col items-center gap-2 p-6 text-center text-2xs text-muted-foreground">
                  <TriangleAlert className="h-5 w-5 text-[#a53e3c]" />
                  Não foi possível carregar os grupos.
                  <Button variant="outline" size="sm" className="h-7 text-2xs" onClick={() => void groupsQuery.refetch()}><RefreshCw className="mr-1.5 h-3 w-3" />Tentar novamente</Button>
                </div>
              )}
              {groupsQuery.isSuccess && groups.length === 0 && (
                <div className="p-4 text-center text-2xs leading-5 text-muted-foreground">
                  Nenhum grupo ainda. Crie o primeiro grupo interno ou prepare um grupo oficial.
                </div>
              )}
              {groups.map((group) => (
                <button
                  key={group.id}
                  type="button"
                  data-testid={`group-list-item-${group.id}`}
                  onClick={() => setSelectedGroupId(group.id)}
                  className={cn(
                    'flex w-full min-w-0 items-start gap-2.5 border-b border-border/60 px-3 py-2.5 text-left transition hover:bg-secondary',
                    selectedGroupId === group.id && 'bg-secondary',
                  )}
                >
                  <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-2xs font-semibold text-[#0057d8] dark:text-[#8fc1ff]">
                    {groupInitials(group.subject)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <strong className="truncate text-2xs text-foreground">{group.subject}</strong>
                      <KlmPill tone={group.isOfficialMetaGroup ? 'blue' : 'muted'} className="shrink-0">{group.isOfficialMetaGroup ? 'oficial' : 'interno'}</KlmPill>
                    </div>
                    <p className="mt-0.5 truncate text-2xs text-muted-foreground">{group.lastMessagePreview ?? 'Sem mensagens ainda'}</p>
                    <div className="mt-1 flex items-center justify-between text-2xs text-muted-foreground">
                      <span className="tabular-nums">{group.participantCount}/8 membros</span>
                      {group.lastMessageAt && <span className="tabular-nums">{formatRelativeTime(group.lastMessageAt)}</span>}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </KlmPanel>
        </div>

        <div className="h-full min-h-0 min-w-0 overflow-hidden">
          {selectedGroup ? (
            <KlmPanel className="flex h-full min-h-0 flex-col overflow-hidden">
              <div className="flex items-center justify-between gap-2 border-b border-border px-3.5 py-2.5">
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5">
                    <strong className="truncate text-sm text-foreground">{selectedGroup.subject}</strong>
                    <KlmPill tone={selectedGroup.isOfficialMetaGroup ? 'blue' : 'muted'}>{selectedGroup.isOfficialMetaGroup ? 'oficial' : 'interno'}</KlmPill>
                  </div>
                  <p className="mt-0.5 truncate text-2xs text-muted-foreground">{selectedGroup.description || 'Sem descrição'}</p>
                </div>
                <span className="shrink-0 text-2xs tabular-nums text-muted-foreground">{selectedGroup.participantCount}/8</span>
              </div>

              {selectedGroup.pinnedMessage && (
                <div className="flex items-start gap-2 border-b border-border bg-primary/5 px-3.5 py-2 text-2xs leading-4">
                  <Pin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0057d8] dark:text-[#8fc1ff]" />
                  <span className="min-w-0 flex-1 truncate text-foreground">{selectedGroup.pinnedMessage.text}</span>
                  <PermissionGate module="mensagens" action="send_message" actionLabel="remover fixação" variant="lock">
                    <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0" onClick={() => unpinMutation.mutate()} disabled={unpinMutation.isPending} title="Remover fixação">
                      <PinOff className="h-3.5 w-3.5" />
                    </Button>
                  </PermissionGate>
                </div>
              )}

              <div className="min-h-0 flex-1 overflow-y-auto px-3.5 py-3" data-testid="group-thread">
                {messagesQuery.isPending && <div className="space-y-2">{[0, 1].map((index) => <Skeleton key={index} className="h-10 w-2/3 rounded-md" />)}</div>}
                {messagesQuery.isSuccess && messagesQuery.data.items.length === 0 && (
                  <p className="py-6 text-center text-2xs text-muted-foreground">Nenhuma mensagem preparada ainda neste grupo.</p>
                )}
                <div className="flex flex-col gap-2">
                  {[...(messagesQuery.data?.items ?? [])].reverse().map((message) => (
                    <div key={message.id} className="max-w-[80%] self-end rounded-lg bg-primary/10 px-3 py-2 text-2xs leading-4 text-foreground">
                      <p>{message.text ?? 'Mensagem sem texto extraído'}</p>
                      <div className="mt-1 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                        <span>{formatDateTime(message.createdAt)}</span>
                        <KlmPill tone={message.state === 'blocked_by_capability' ? 'gold' : 'muted'}>
                          {message.state === 'blocked_by_capability' ? 'aguardando capability' : 'preparada localmente'}
                        </KlmPill>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="border-t border-border p-3">
                <PermissionGate module="mensagens" action="send_message" actionLabel="enviar mensagens em grupos" variant="readonly">
                  <div className="flex items-end gap-2">
                    <Textarea
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      placeholder="Escreva uma mensagem para o grupo..."
                      rows={2}
                      className="min-h-[2.5rem] resize-none text-xs"
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' && !event.shiftKey) {
                          event.preventDefault();
                          if (draft.trim()) sendMessageMutation.mutate(draft.trim());
                        }
                      }}
                    />
                    <Button size="icon" className="h-9 w-9 shrink-0" disabled={!draft.trim() || sendMessageMutation.isPending} onClick={() => draft.trim() && sendMessageMutation.mutate(draft.trim())}>
                      {sendMessageMutation.isPending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                    </Button>
                  </div>
                </PermissionGate>
                {selectedGroup.isOfficialMetaGroup && (
                  <p className="mt-1.5 text-2xs text-muted-foreground">Fora da janela de 24h ou sem capability oficial, use um template aprovado — a regra da API de grupos da Meta é a mesma das conversas 1:1.</p>
                )}
              </div>
            </KlmPanel>
          ) : (
            <KlmPanel className="flex h-full min-h-0 items-center justify-center overflow-hidden p-8 text-center">
              <div className="max-w-sm">
                {groupsQuery.isPending ? <LoaderCircle className="mx-auto h-6 w-6 animate-spin text-muted-foreground" /> : <MessagesSquare className="mx-auto h-6 w-6 text-muted-foreground" />}
                <p className="mt-3 text-sm font-semibold">{groupsQuery.isPending ? 'Carregando grupos' : groups.length ? 'Selecione um grupo' : 'Nenhum grupo criado ainda'}</p>
                <p className="mt-1 text-2xs leading-4 text-muted-foreground">{groups.length ? 'Escolha um grupo na lista para abrir a conversa.' : 'Use "Novo grupo" para criar o primeiro grupo interno.'}</p>
              </div>
            </KlmPanel>
          )}
        </div>

        {selectedGroup && (
          <div className="h-full min-h-0 min-w-0 overflow-hidden">
            <KlmPanel className="flex h-full min-h-0 flex-col divide-y divide-border overflow-y-auto">
              <div className="p-3.5">
                <h3 className="text-2xs font-semibold uppercase text-muted-foreground">Link de convite</h3>
                {selectedGroup.inviteLink ? (
                  <div className="mt-2 space-y-1.5">
                    <div className="flex items-center gap-1.5">
                      <code className="min-w-0 flex-1 truncate rounded bg-secondary px-2 py-1 text-2xs">{selectedGroup.inviteLink}</code>
                      <Button variant="outline" size="icon" className="h-7 w-7 shrink-0" onClick={() => void copyInviteLink(selectedGroup.inviteLink as string)} title="Copiar link">
                        <Copy className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                    {selectedGroup.inviteLinkSimulated && <KlmPill tone="gold">simulado localmente</KlmPill>}
                  </div>
                ) : (
                  <p className="mt-1.5 text-2xs text-muted-foreground">Ainda sem link gerado.</p>
                )}
                <PermissionGate module="mensagens" action="send_message" actionLabel="rotacionar link de convite" variant="lock">
                  <Button variant="outline" size="sm" className="mt-2 h-7 text-2xs" onClick={() => rotateInviteMutation.mutate()} disabled={rotateInviteMutation.isPending}>
                    {rotateInviteMutation.isPending ? <LoaderCircle className="mr-1.5 h-3 w-3 animate-spin" /> : <RefreshCw className="mr-1.5 h-3 w-3" />}
                    {selectedGroup.inviteLink ? 'Rotacionar link' : 'Gerar link'}
                  </Button>
                </PermissionGate>
                {selectedGroup.isOfficialMetaGroup && (
                  <p className="mt-1.5 text-2xs leading-4 text-muted-foreground">O link oficial só é confirmado quando a Meta enviar o webhook de ciclo de vida do grupo.</p>
                )}
              </div>

              <div className="p-3.5">
                <h3 className="text-2xs font-semibold uppercase text-muted-foreground">Mensagem fixada</h3>
                <PermissionGate module="mensagens" action="send_message" actionLabel="fixar mensagem" variant="readonly">
                  <div className="mt-2 flex items-center gap-1.5">
                    <Input value={pinDraft} onChange={(event) => setPinDraft(event.target.value)} placeholder="Texto para fixar" className="h-8 text-2xs" maxLength={4096} />
                    <Button variant="outline" size="icon" className="h-8 w-8 shrink-0" disabled={!pinDraft.trim() || pinMutation.isPending} onClick={() => pinDraft.trim() && pinMutation.mutate(pinDraft.trim())} title="Fixar mensagem">
                      {pinMutation.isPending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Pin className="h-3.5 w-3.5" />}
                    </Button>
                  </div>
                </PermissionGate>
              </div>

              <div className="p-3.5">
                <h3 className="text-2xs font-semibold uppercase text-muted-foreground">Membros</h3>
                {participantsQuery.isPending && <div className="mt-2 space-y-1.5">{[0, 1].map((index) => <Skeleton key={index} className="h-8 w-full rounded-md" />)}</div>}
                <ul className="mt-2 space-y-1.5" data-testid="group-participants-list">
                  {(participantsQuery.data?.items ?? []).map((participant) => (
                    <li key={participant.id} className="flex items-center justify-between gap-2 rounded-md border border-border/70 px-2 py-1.5 text-2xs">
                      <span className="min-w-0 flex-1 truncate">
                        <strong className="text-foreground">{participant.username ?? participant.waId ?? participant.providerUserId}</strong>
                        <span className="ml-1 text-muted-foreground">{participant.role === 'admin' ? 'admin' : 'membro'}</span>
                      </span>
                      <KlmPill tone={participant.state === 'joined' ? 'green' : participant.state === 'removed' || participant.state === 'left' ? 'muted' : 'gold'}>
                        {participant.state}
                      </KlmPill>
                      {participant.state === 'joined' && (
                        <PermissionGate module="mensagens" action="send_message" actionLabel="remover participante" variant="lock">
                          <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0" onClick={() => removeParticipantMutation.mutate(participant.id)} disabled={removeParticipantMutation.isPending} title="Remover participante">
                            <UserMinus className="h-3.5 w-3.5" />
                          </Button>
                        </PermissionGate>
                      )}
                    </li>
                  ))}
                </ul>
                {selectedGroup.isOfficialMetaGroup && (
                  <p className="mt-2 text-2xs leading-4 text-muted-foreground">Grupo oficial: limite de 8 membros da API da Meta. A remoção aqui fica preparada e auditada, aguardando confirmação da Meta.</p>
                )}
              </div>

              <div className="p-3.5">
                <h3 className="text-2xs font-semibold uppercase text-muted-foreground">Caso vinculado</h3>
                <p className="mt-1.5 flex items-center gap-1.5 text-2xs leading-4 text-muted-foreground"><Lock className="h-3 w-3 shrink-0" />Vínculo de caso a grupos ainda não é suportado neste contrato.</p>
              </div>
            </KlmPanel>
          </div>
        )}
      </div>

      <CreateGroupDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        pending={createInternalMutation.isPending || prepareOfficialMutation.isPending}
        onSubmit={(draft) => {
          if (draft.kind === 'local_internal') createInternalMutation.mutate(draft);
          else prepareOfficialMutation.mutate(draft);
        }}
      />

      <Button
        type="button"
        size="icon"
        className="fixed bottom-6 right-6 z-40 h-14 w-14 rounded-full shadow-lg"
        onClick={() => setAssistantOpen(true)}
        aria-haspopup="dialog"
        aria-label="Abrir assistente de configuração de grupos"
        title="Assistente de configuração"
        data-testid="groups-assistant-fab"
      >
        <Sparkles className="h-5 w-5" />
      </Button>

      <Sheet open={assistantOpen} onOpenChange={setAssistantOpen}>
        <SheetContent className="flex w-[96vw] flex-col gap-4 overflow-y-auto sm:max-w-[460px]" data-testid="groups-assistant-sheet">
          <SheetHeader className="text-left">
            <div className="mb-1 flex h-9 w-9 items-center justify-center rounded-md bg-[#031121] text-primary dark:bg-white dark:text-[#0057d8]"><Sparkles className="h-4 w-4" /></div>
            <SheetTitle>Assistente de configuração de grupos</SheetTitle>
            <SheetDescription>Passo a passo para colocar grupos WhatsApp no ar, do jeito honesto: o que já está pronto e o que ainda depende da Meta.</SheetDescription>
          </SheetHeader>

          <WizardStep index={1} title="Conexão WhatsApp e OBA" state={stepConnectionDone ? 'done' : 'pending'}>
            {capabilityQuery.isPending ? 'Consultando a capability da conexão…' : stepConnectionDone
              ? 'App em Meta for Developers configurado e elegibilidade OBA comprovada.'
              : (gate?.capabilityDenialReasons[0] ?? 'Configure o app em Meta for Developers e comprove a elegibilidade OBA para habilitar grupos oficiais.')}
          </WizardStep>

          <WizardStep index={2} title="Criar o primeiro grupo" state={stepGroupDone ? 'done' : 'pending'}>
            {stepGroupDone
              ? `${groups.length} grupo${groups.length === 1 ? '' : 's'} já criado${groups.length === 1 ? '' : 's'} nesta conexão.`
              : 'Nenhum grupo ainda. Um grupo interno fica pronto na hora; um grupo oficial é preparado e aguarda confirmação da Meta.'}
            {!stepGroupDone && (
              <Button variant="outline" size="sm" className="mt-2 h-7 text-2xs" onClick={() => { setAssistantOpen(false); setCreateOpen(true); }}>
                <Plus className="mr-1.5 h-3 w-3" />Criar grupo
              </Button>
            )}
          </WizardStep>

          <WizardStep index={3} title="Link de convite" state={stepInviteDone ? 'done' : 'pending'}>
            {stepInviteDone
              ? 'O grupo selecionado já tem um link de convite gerado. Compartilhe apenas com quem deve entrar — a entrada é só por convite.'
              : selectedGroup
                ? 'Gere um link no painel de detalhes do grupo selecionado.'
                : 'Selecione ou crie um grupo para gerar o link de convite.'}
          </WizardStep>

          <WizardStep index={4} title="Boas práticas da API de grupos da Meta" state="info">
            <ul className="list-disc space-y-1 pl-4">
              <li>Entrada só por convite (invite-only) — não existe link público de descoberta.</li>
              <li>Limite de 8 membros por grupo, incluindo o número comercial.</li>
              <li>Até 10.000 grupos por número comercial.</li>
              <li>Fora da janela de atendimento de 24h, use um template aprovado — igual às conversas 1:1.</li>
              <li>Sem chamadas de voz ou vídeo em grupos pela API.</li>
            </ul>
          </WizardStep>
        </SheetContent>
      </Sheet>
    </div>
  );
}
