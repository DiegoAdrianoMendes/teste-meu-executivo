import { useEffect, useMemo, useRef, useState } from 'react';
import { useAppStore } from '@/store/app-context';
import { ContactList } from '@/components/mensagens/ContactList';
import { ConversationView } from '@/components/mensagens/ConversationView';
import { CardContextPanel } from '@/components/mensagens/CardContextPanel';
import { ConversationOperationsBar } from '@/components/mensagens/ConversationOperationsBar';
import { apiFetch } from '@/services/http';
import {
  applyConversationFlow,
  assignConversation,
  claimConversation,
  createConversationNote,
  fetchConversationAssignees,
  fetchConversationDrawer,
  fetchMessageTemplates as fetchChatTemplates,
  handoffConversation,
  moveConversationStage,
  optimizeWhatsAppDraft,
  scheduleConversationMeeting,
  scheduleConversationMessage,
  sendConversationContract,
  sendConversationMedia,
  sendConversationProposal,
} from '@/services/whatsapp';
import { updateMatterDetail } from '@/services/matters';
import { fetchWorkspaceOperations } from '@/services/admin';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { AlertTriangle, CheckCircle2, Loader2, MessageSquare, Smartphone } from 'lucide-react';
import { useIsMobile } from '@/hooks/use-mobile';
import { useToast } from '@/hooks/use-toast';
import type {
  ConversationAssignee,
  FlowTemplateReference,
  MessageTemplate,
  LeadDrawerData,
  WhatsAppConnectionSummary,
  WhatsAppConversation,
} from '@/types';

interface WhatsAppRuntimeStatus {
  status: 'connected' | 'pending' | 'disconnected' | 'error';
  phoneNumber?: string | null;
  displayName?: string | null;
  webhookRegistered?: boolean;
  lastError?: string | null;
}

export default function Mensagens() {
  const {
    state,
    dispatch,
    sendMessage,
    toggleAiMode,
    markConversationRead,
    refreshConversation,
    refreshConversations,
    refreshCards,
    refreshWhatsAppConnections,
  } = useAppStore();
  const { toast } = useToast();
  const isMobile = useIsMobile();
  const [selectedConvId, setSelectedConvId] = useState<string | null>(null);
  const [whatsAppStatus, setWhatsAppStatus] = useState<WhatsAppRuntimeStatus | null>(null);
  const [availableConnections, setAvailableConnections] = useState<WhatsAppConnectionSummary[]>([]);
  const [selectedConnectionId, setSelectedConnectionId] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [showContext, setShowContext] = useState(false);
  const [drawerData, setDrawerData] = useState<LeadDrawerData | null>(null);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [assignees, setAssignees] = useState<ConversationAssignee[]>([]);
  const [templates, setTemplates] = useState<MessageTemplate[]>([]);
  const [flowOptions, setFlowOptions] = useState<FlowTemplateReference[]>([]);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [dialog, setDialog] = useState<'schedule' | 'meeting' | 'media' | 'proposal' | 'contract' | null>(null);
  const [scheduleDraft, setScheduleDraft] = useState({ body: '', scheduledAt: '' });
  const [meetingDraft, setMeetingDraft] = useState({ title: '', startsAt: '', endsAt: '', responsibleUserId: '', location: '', meetingUrl: '', notes: '' });
  const [mediaDraft, setMediaDraft] = useState({ url: '', filename: '', caption: '' });
  const [proposalDraft, setProposalDraft] = useState({ templateId: '', format: 'text', customMessage: '' });
  const [contractDraft, setContractDraft] = useState({ templateId: '', authMode: 'assinaturaTela-tokenEmail', customMessage: '' });
  const proposalAttemptRef = useRef<{ fingerprint: string; idempotencyKey: string } | null>(null);

  const selectedConv = state.conversations.find((conversation) => conversation.id === selectedConvId);
  const proposalTemplates = useMemo(() => templates.filter((template) => template.kind === 'proposal'), [templates]);
  const contractTemplates = useMemo(() => templates.filter((template) => template.kind === 'contract'), [templates]);

  useEffect(() => {
    if (!isMobile && !selectedConvId && state.conversations.length > 0) {
      setSelectedConvId(state.conversations[0].id);
    }
  }, [isMobile, selectedConvId, state.conversations]);

  useEffect(() => {
    setShowContext(false);
  }, [selectedConvId]);

  const messagingEnabled = whatsAppStatus?.status === 'connected';

  useEffect(() => {
    let cancelled = false;
    const loadStatus = async () => {
      setStatusLoading(true);
      try {
        const [status, connections, nextAssignees, nextTemplates, operations] = await Promise.all([
          apiFetch<WhatsAppRuntimeStatus>('/api/v1/whatsapp/status'),
          refreshWhatsAppConnections(),
          fetchConversationAssignees(),
          fetchChatTemplates(),
          fetchWorkspaceOperations().catch(() => null),
        ]);
        if (!cancelled) {
          const uniqueFlows = (operations?.areaActivations ?? [])
            .flatMap((area) => area.flowOptions)
            .filter((flow, index, current) => current.findIndex((item) => item.id === flow.id) === index);
          setWhatsAppStatus(status);
          setAvailableConnections(connections.filter((connection) => connection.status === 'connected'));
          setAssignees(nextAssignees);
          setTemplates(nextTemplates);
          setFlowOptions(uniqueFlows);
          if (!proposalDraft.templateId) {
            const defaultProposal = nextTemplates.find((template) => template.kind === 'proposal');
            if (defaultProposal) {
              setProposalDraft((current) => ({ ...current, templateId: defaultProposal.id, format: defaultProposal.format || 'text' }));
            }
          }
          if (!contractDraft.templateId) {
            const defaultContract = nextTemplates.find((template) => template.kind === 'contract');
            if (defaultContract) {
              setContractDraft((current) => ({ ...current, templateId: defaultContract.id }));
            }
          }
        }
      } catch {
        if (!cancelled) {
          setWhatsAppStatus({
            status: 'error',
            lastError: 'Não foi possível confirmar o status do canal de WhatsApp.',
          });
          setAvailableConnections([]);
        }
      } finally {
        if (!cancelled) {
          setStatusLoading(false);
        }
      }
    };
    void loadStatus();
    return () => {
      cancelled = true;
    };
  }, [contractDraft.templateId, proposalDraft.templateId, refreshWhatsAppConnections, state.activeWorkspace]);

  useEffect(() => {
    if (availableConnections.length === 0) {
      setSelectedConnectionId(null);
      return;
    }
    const preferredId = selectedConv?.whatsappConnectionId;
    if (preferredId && availableConnections.some((connection) => connection.id === preferredId)) {
      setSelectedConnectionId(preferredId);
      return;
    }
    if (!selectedConnectionId || !availableConnections.some((connection) => connection.id === selectedConnectionId)) {
      const fallback = availableConnections.find((connection) => connection.isDefault) ?? availableConnections[0];
      setSelectedConnectionId(fallback.id);
    }
  }, [availableConnections, selectedConnectionId, selectedConv?.whatsappConnectionId]);

  useEffect(() => {
    if (selectedConvId) {
      void markConversationRead(selectedConvId);
    }
  }, [selectedConvId, markConversationRead]);

  useEffect(() => {
    if (!selectedConvId) {
      return;
    }
    const selected = state.conversations.find((conversation) => conversation.id === selectedConvId);
    if (!selected || selected.messages.length > 0) {
      return;
    }
    void refreshConversation(selectedConvId);
  }, [refreshConversation, selectedConvId, state.conversations]);

  useEffect(() => {
    if (!selectedConvId) {
      setDrawerData(null);
      return;
    }
    let cancelled = false;
    const loadDrawer = async () => {
      setDrawerLoading(true);
      try {
        const data = await fetchConversationDrawer(selectedConvId);
        if (!cancelled) {
          setDrawerData(data);
        }
      } catch {
        if (!cancelled) {
          setDrawerData(null);
        }
      } finally {
        if (!cancelled) {
          setDrawerLoading(false);
        }
      }
    };
    void loadDrawer();
    return () => {
      cancelled = true;
    };
  }, [selectedConvId]);

  const syncConversation = async (conversation: WhatsAppConversation) => {
    dispatch({ type: 'UPSERT_CONVERSATION', payload: conversation });
    await Promise.all([refreshConversations(), refreshCards()]);
    if (selectedConvId === conversation.id) {
      await Promise.all([
        refreshConversation(conversation.id),
        fetchConversationDrawer(conversation.id).then(setDrawerData).catch(() => undefined),
      ]);
    }
  };

  const refreshDrawer = async () => {
    if (!selectedConvId) return;
    setDrawerLoading(true);
    try {
      const data = await fetchConversationDrawer(selectedConvId);
      setDrawerData(data);
    } finally {
      setDrawerLoading(false);
    }
  };

  const runConversationAction = async (key: string, action: () => Promise<WhatsAppConversation>, successMessage: string): Promise<boolean> => {
    if (!selectedConv) return false;
    setActionBusy(key);
    try {
      const updatedConversation = await action();
      await syncConversation(updatedConversation);
      toast({ title: successMessage });
      return true;
    } catch (error) {
      toast({
        variant: 'destructive',
        title: 'Ação não concluída',
        description: error instanceof Error ? error.message : 'Não foi possível concluir esta ação agora.',
      });
      return false;
    } finally {
      setActionBusy(null);
    }
  };

  const statusBanner = statusLoading ? (
    <div className="flex items-center gap-2 border-b bg-muted/40 px-4 py-2 text-xs text-muted-foreground">
      <Loader2 className="h-3.5 w-3.5 animate-spin" />
      Verificando conexão oficial do WhatsApp...
    </div>
  ) : (
    <div
      className={`flex items-center justify-between gap-3 border-b px-4 py-2 text-xs ${
        messagingEnabled
          ? 'bg-emerald-50 text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200'
          : 'bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200'
      }`}
    >
      <div className="flex items-center gap-2 min-w-0">
        {messagingEnabled ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> : <AlertTriangle className="h-3.5 w-3.5 shrink-0" />}
        <span className="truncate">
          {messagingEnabled
            ? `Canal pronto para envio${whatsAppStatus?.phoneNumber ? ` em ${whatsAppStatus.phoneNumber}` : ''}.`
            : whatsAppStatus?.lastError || 'Nenhum número oficial apto para responder foi encontrado neste workspace.'}
        </span>
      </div>
      <div className="hidden md:flex items-center gap-1 rounded-full border border-current/20 px-2 py-0.5 text-[11px]">
        <Smartphone className="h-3 w-3" />
        {whatsAppStatus?.displayName || whatsAppStatus?.phoneNumber || 'Sem número ativo'}
      </div>
    </div>
  );

  const statusHint = messagingEnabled
    ? `As mensagens sairão pelo número ${
        availableConnections.find((connection) => connection.id === selectedConnectionId)?.displayPhoneNumber
        || whatsAppStatus?.phoneNumber
        || 'oficial conectado'
      }`
    : 'Conecte e valide um número oficial do WhatsApp em Configurações antes de responder por aqui.';

  const selectedConnection = availableConnections.find((connection) => connection.id === selectedConnectionId) ?? availableConnections[0];
  const channelChooser = availableConnections.length > 0 ? (
    <div className="flex flex-wrap items-center gap-2 border-b bg-background px-4 py-2">
      <Label className="text-xs text-muted-foreground whitespace-nowrap">Linha de saída</Label>
      {availableConnections.length > 1 ? (
        <Select value={selectedConnectionId ?? undefined} onValueChange={setSelectedConnectionId}>
          <SelectTrigger className="h-8 max-w-md text-xs">
            <SelectValue placeholder="Escolha o número de saída" />
          </SelectTrigger>
          <SelectContent>
            {availableConnections.map((connection) => (
              <SelectItem key={connection.id} value={connection.id}>
                {[connection.displayName, connection.displayPhoneNumber || connection.phoneNumber, connection.isDefault ? 'padrão' : null]
                  .filter(Boolean)
                  .join(' • ')}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <div className="inline-flex h-8 items-center rounded-md border bg-muted/40 px-3 text-xs">
          {[selectedConnection?.displayName, selectedConnection?.displayPhoneNumber || selectedConnection?.phoneNumber, selectedConnection?.isDefault ? 'padrão' : null]
            .filter(Boolean)
            .join(' • ') || 'Número oficial conectado'}
        </div>
      )}
    </div>
  ) : null;

  const openMeetingDialog = () => {
    if (!selectedConv) return;
    setMeetingDraft((current) => ({
      ...current,
      title: current.title || `Reunião · ${selectedConv.clientName}`,
      responsibleUserId: current.responsibleUserId || selectedConv.assignedUserId || state.currentUser?.id || '',
    }));
    setDialog('meeting');
  };

  const openPhoneDialer = () => {
    if (!selectedConv) return;
    const phone = selectedConv.clientPhone.replace(/[^\d+]/g, '');
    if (!phone) {
      toast({ title: 'Telefone não registrado', description: 'Atualize o contato antes de iniciar a ligação.' });
      return;
    }
    const link = document.createElement('a');
    link.href = `tel:${phone}`;
    link.click();
  };

  const operationsBar = selectedConv ? (
    <ConversationOperationsBar
      conversation={selectedConv}
      currentUserId={state.currentUser?.id}
      assignees={assignees}
      flowOptions={flowOptions}
      onClaim={async () => { await runConversationAction('claim', () => claimConversation(selectedConv.id), 'Conversa atribuída ao usuário atual.'); }}
      onAssign={async (userId) => { await runConversationAction('assign', () => assignConversation(selectedConv.id, userId), userId ? 'Responsável atualizado.' : 'Conversa desatribuída.'); }}
      onMoveToHuman={async () => { await runConversationAction('human', () => handoffConversation(selectedConv.id, { queueStatus: 'human', humanControlState: 'copilot' }), 'Atendimento humano assumiu a conversa.'); }}
      onMoveToAi={async () => { await runConversationAction('ai', () => handoffConversation(selectedConv.id, { queueStatus: 'ai', humanControlState: 'copilot' }), 'IA retomou o atendimento.'); }}
      onPauseAi={async () => { await runConversationAction('pause', () => handoffConversation(selectedConv.id, { queueStatus: 'human', humanControlState: 'manual_pause' }), 'IA pausada para esta conversa.'); }}
      onMoveToFollowUp={async () => { await runConversationAction('follow-up', () => handoffConversation(selectedConv.id, { queueStatus: 'follow_up', humanControlState: 'copilot' }), 'Conversa movida para follow-up.'); }}
      onApplyFlow={async (templateId) => { await runConversationAction('flow', () => applyConversationFlow(selectedConv.id, { templateId }), 'Flow aplicado ao atendimento.'); }}
      onOpenScheduleMessage={() => {
        setScheduleDraft((current) => ({ ...current, body: current.body || selectedConv.lastMessage || '', scheduledAt: current.scheduledAt || '' }));
        setDialog('schedule');
      }}
      onOpenMeeting={openMeetingDialog}
      onOpenMedia={() => setDialog('media')}
      onOpenProposal={() => setDialog('proposal')}
      onOpenContract={() => setDialog('contract')}
    />
  ) : null;

  const handleScheduleMessage = async () => {
    if (!selectedConv || !scheduleDraft.body || !scheduleDraft.scheduledAt) return;
    await runConversationAction(
      'schedule-message',
      () => scheduleConversationMessage(selectedConv.id, {
        connectionId: selectedConnectionId,
        body: scheduleDraft.body,
        scheduledAt: new Date(scheduleDraft.scheduledAt).toISOString(),
      }),
      'Mensagem agendada no follow-up.',
    );
    setDialog(null);
    setScheduleDraft({ body: '', scheduledAt: '' });
  };

  const handleScheduleMeeting = async () => {
    if (!selectedConv || !meetingDraft.title || !meetingDraft.startsAt) return;
    await runConversationAction(
      'schedule-meeting',
      () => scheduleConversationMeeting(selectedConv.id, {
        title: meetingDraft.title,
        startsAt: new Date(meetingDraft.startsAt).toISOString(),
        endsAt: meetingDraft.endsAt ? new Date(meetingDraft.endsAt).toISOString() : null,
        responsibleUserId: meetingDraft.responsibleUserId || null,
        location: meetingDraft.location || null,
        meetingUrl: meetingDraft.meetingUrl || null,
        notes: meetingDraft.notes || null,
      }),
      'Reunião criada a partir do chat.',
    );
    setDialog(null);
    setMeetingDraft({ title: '', startsAt: '', endsAt: '', responsibleUserId: '', location: '', meetingUrl: '', notes: '' });
  };

  const handleSendMedia = async () => {
    if (!selectedConv || !mediaDraft.url || !mediaDraft.filename) return;
    await runConversationAction(
      'send-media',
      () => sendConversationMedia(selectedConv.id, {
        connectionId: selectedConnectionId,
        url: mediaDraft.url,
        filename: mediaDraft.filename,
        caption: mediaDraft.caption || null,
      }),
      'Mídia enviada pelo WhatsApp.',
    );
    setDialog(null);
    setMediaDraft({ url: '', filename: '', caption: '' });
  };

  const handleSendProposal = async () => {
    if (!selectedConv || !proposalDraft.templateId) return;
    const payload = {
      connectionId: selectedConnectionId,
      templateId: proposalDraft.templateId,
      format: proposalDraft.format,
      customMessage: proposalDraft.customMessage || null,
    };
    const fingerprint = JSON.stringify([selectedConv.id, payload]);
    if (proposalAttemptRef.current?.fingerprint !== fingerprint) {
      proposalAttemptRef.current = {
        fingerprint,
        idempotencyKey: `conversation-proposal:${crypto.randomUUID()}`,
      };
    }
    const completed = await runConversationAction(
      'send-proposal',
      () => sendConversationProposal(
        selectedConv.id,
        payload,
        proposalAttemptRef.current!.idempotencyKey,
      ),
      'Proposta enviada pelo inbox.',
    );
    if (completed) {
      proposalAttemptRef.current = null;
      setDialog(null);
    }
  };

  const handleSendContract = async () => {
    if (!selectedConv || !contractDraft.templateId) return;
    await runConversationAction(
      'send-contract',
      () => sendConversationContract(selectedConv.id, {
        connectionId: selectedConnectionId,
        templateId: contractDraft.templateId,
        authMode: contractDraft.authMode,
        customMessage: contractDraft.customMessage || null,
      }),
      'Contrato enviado para assinatura.',
    );
    setDialog(null);
  };

  const renderDialogs = (
    <>
      <Dialog open={dialog === 'schedule'} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Agendar mensagem</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label>Mensagem</Label>
              <Textarea value={scheduleDraft.body} onChange={(event) => setScheduleDraft((current) => ({ ...current, body: event.target.value }))} className="min-h-[130px]" />
            </div>
            <div className="space-y-1.5">
              <Label>Data e horário</Label>
              <Input type="datetime-local" value={scheduleDraft.scheduledAt} onChange={(event) => setScheduleDraft((current) => ({ ...current, scheduledAt: event.target.value }))} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialog(null)}>Cancelar</Button>
            <Button onClick={() => void handleScheduleMessage()} disabled={!scheduleDraft.body || !scheduleDraft.scheduledAt || actionBusy === 'schedule-message'}>
              {actionBusy === 'schedule-message' ? 'Agendando...' : 'Agendar mensagem'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={dialog === 'meeting'} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Criar reunião</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 py-2 md:grid-cols-2">
            <div className="space-y-1.5 md:col-span-2">
              <Label>Título</Label>
              <Input value={meetingDraft.title} onChange={(event) => setMeetingDraft((current) => ({ ...current, title: event.target.value }))} />
            </div>
            <div className="space-y-1.5">
              <Label>Início</Label>
              <Input type="datetime-local" value={meetingDraft.startsAt} onChange={(event) => setMeetingDraft((current) => ({ ...current, startsAt: event.target.value }))} />
            </div>
            <div className="space-y-1.5">
              <Label>Fim</Label>
              <Input type="datetime-local" value={meetingDraft.endsAt} onChange={(event) => setMeetingDraft((current) => ({ ...current, endsAt: event.target.value }))} />
            </div>
            <div className="space-y-1.5">
              <Label>Responsável</Label>
              <Select value={meetingDraft.responsibleUserId || undefined} onValueChange={(value) => setMeetingDraft((current) => ({ ...current, responsibleUserId: value }))}>
                <SelectTrigger>
                  <SelectValue placeholder="Escolha o responsável" />
                </SelectTrigger>
                <SelectContent>
                  {assignees.map((assignee) => (
                    <SelectItem key={assignee.id} value={assignee.id}>{assignee.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Local</Label>
              <Input value={meetingDraft.location} onChange={(event) => setMeetingDraft((current) => ({ ...current, location: event.target.value }))} />
            </div>
            <div className="space-y-1.5 md:col-span-2">
              <Label>Link da reunião</Label>
              <Input value={meetingDraft.meetingUrl} onChange={(event) => setMeetingDraft((current) => ({ ...current, meetingUrl: event.target.value }))} />
            </div>
            <div className="space-y-1.5 md:col-span-2">
              <Label>Notas</Label>
              <Textarea value={meetingDraft.notes} onChange={(event) => setMeetingDraft((current) => ({ ...current, notes: event.target.value }))} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialog(null)}>Cancelar</Button>
            <Button onClick={() => void handleScheduleMeeting()} disabled={!meetingDraft.title || !meetingDraft.startsAt || actionBusy === 'schedule-meeting'}>
              {actionBusy === 'schedule-meeting' ? 'Salvando...' : 'Criar reunião'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={dialog === 'media'} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Enviar mídia</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label>URL pública do arquivo</Label>
              <Input value={mediaDraft.url} onChange={(event) => setMediaDraft((current) => ({ ...current, url: event.target.value }))} placeholder="https://..." />
            </div>
            <div className="space-y-1.5">
              <Label>Nome do arquivo</Label>
              <Input value={mediaDraft.filename} onChange={(event) => setMediaDraft((current) => ({ ...current, filename: event.target.value }))} placeholder="proposta.pdf" />
            </div>
            <div className="space-y-1.5">
              <Label>Legenda</Label>
              <Textarea value={mediaDraft.caption} onChange={(event) => setMediaDraft((current) => ({ ...current, caption: event.target.value }))} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialog(null)}>Cancelar</Button>
            <Button onClick={() => void handleSendMedia()} disabled={!mediaDraft.url || !mediaDraft.filename || actionBusy === 'send-media'}>
              {actionBusy === 'send-media' ? 'Enviando...' : 'Enviar mídia'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={dialog === 'proposal'} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>Enviar proposta</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Template</Label>
                <Select value={proposalDraft.templateId || undefined} onValueChange={(value) => {
                  const matched = proposalTemplates.find((template) => template.id === value);
                  setProposalDraft((current) => ({ ...current, templateId: value, format: matched?.format || current.format }));
                }}>
                  <SelectTrigger>
                    <SelectValue placeholder="Escolha a proposta" />
                  </SelectTrigger>
                  <SelectContent>
                    {proposalTemplates.map((template) => (
                      <SelectItem key={template.id} value={template.id}>{template.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Formato</Label>
                <Select value={proposalDraft.format} onValueChange={(value) => setProposalDraft((current) => ({ ...current, format: value }))}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="text">Texto</SelectItem>
                    <SelectItem value="pdf">PDF</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>Mensagem opcional</Label>
              <Textarea
                value={proposalDraft.customMessage}
                onChange={(event) => setProposalDraft((current) => ({ ...current, customMessage: event.target.value }))}
                placeholder="Se quiser, complemente a proposta com um texto comercial mais humano."
                className="min-h-[140px]"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialog(null)}>Cancelar</Button>
            <Button onClick={() => void handleSendProposal()} disabled={!proposalDraft.templateId || actionBusy === 'send-proposal'}>
              {actionBusy === 'send-proposal' ? 'Enviando...' : 'Enviar proposta'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={dialog === 'contract'} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>Enviar contrato</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Template</Label>
                <Select value={contractDraft.templateId || undefined} onValueChange={(value) => setContractDraft((current) => ({ ...current, templateId: value }))}>
                  <SelectTrigger>
                    <SelectValue placeholder="Escolha o contrato" />
                  </SelectTrigger>
                  <SelectContent>
                    {contractTemplates.map((template) => (
                      <SelectItem key={template.id} value={template.id}>{template.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Modo de autenticação</Label>
                <Select value={contractDraft.authMode} onValueChange={(value) => setContractDraft((current) => ({ ...current, authMode: value }))}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="assinaturaTela-tokenEmail">Tela + token por e-mail</SelectItem>
                    <SelectItem value="assinaturaTela-tokenWhatsapp">Tela + token por WhatsApp</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>Mensagem opcional de envio</Label>
              <Textarea
                value={contractDraft.customMessage}
                onChange={(event) => setContractDraft((current) => ({ ...current, customMessage: event.target.value }))}
                placeholder="O sistema pode usar o template de assinatura automaticamente se você preferir."
                className="min-h-[140px]"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialog(null)}>Cancelar</Button>
            <Button onClick={() => void handleSendContract()} disabled={!contractDraft.templateId || actionBusy === 'send-contract'}>
              {actionBusy === 'send-contract' ? 'Enviando...' : 'Enviar contrato'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );

  if (isMobile) {
    return (
      <>
        <div className="flex flex-col h-[calc(100vh-3.5rem-2rem)] rounded-xl border overflow-hidden bg-background" style={{ boxShadow: 'var(--shadow-elevated)' }}>
          {statusBanner}
          {channelChooser}
          {!selectedConvId || !selectedConv ? (
            <ContactList conversations={state.conversations} selectedId={selectedConvId} onSelect={setSelectedConvId} />
          ) : showContext ? (
            <CardContextPanel
              contactId={selectedConv.id}
              drawerData={drawerData}
              assignees={assignees}
              loading={drawerLoading}
              onBack={() => setShowContext(false)}
              onRefresh={() => void refreshDrawer()}
              onAddNote={async (body) => {
                await createConversationNote(selectedConv.id, { body });
                await refreshDrawer();
              }}
              onMoveStage={async (columnId, microStatus) => {
                const nextDrawer = await moveConversationStage(selectedConv.id, { columnId, microStatus });
                setDrawerData(nextDrawer);
                await Promise.all([refreshCards(), refreshConversation(selectedConv.id)]);
              }}
              onSaveMatter={async (payload) => {
                if (!drawerData?.matter) return;
                const updated = await updateMatterDetail(drawerData.matter.id, {
                  responsibleHumanId: payload.responsibleHuman || null,
                  nextAction: payload.nextAction || null,
                  executiveSummary: payload.executiveSummary || null,
                  strategyNotes: payload.strategyNotes || null,
                  priority: payload.priority,
                  urgency: payload.urgency,
                  classification: payload.classification,
                  nextDeadline: payload.nextDeadline || null,
                });
                setDrawerData((current) => (current ? { ...current, matter: updated } : current));
                await Promise.all([refreshCards(), refreshConversation(selectedConv.id)]);
              }}
              mobile
            />
          ) : (
            <ConversationView
              conversation={selectedConv}
              onSend={(text) => sendMessage(selectedConv.id, text, selectedConnectionId)}
              onToggleAi={(mode) => toggleAiMode(selectedConv.id, mode)}
              onBack={() => setSelectedConvId(null)}
              onShowContext={() => setShowContext(true)}
              onCall={openPhoneDialer}
              onVideoCall={openMeetingDialog}
              onOpenMenu={() => setShowContext(true)}
              mobile
              messagingEnabled={messagingEnabled}
              statusHint={statusHint}
              templates={templates}
              onOpenMedia={() => setDialog('media')}
              onOptimize={optimizeWhatsAppDraft}
              operationsBar={operationsBar}
            />
          )}
        </div>
        {renderDialogs}
      </>
    );
  }

  return (
    <>
      <div className="flex h-[calc(100vh-3.5rem-2rem)] rounded-xl border overflow-hidden bg-background" style={{ boxShadow: 'var(--shadow-elevated)' }}>
        <div className="flex flex-1 flex-col min-w-0">
          {statusBanner}
          {channelChooser}
          <div className="flex flex-1 min-h-0">
            <div className="w-80 shrink-0">
              <ContactList conversations={state.conversations} selectedId={selectedConvId} onSelect={setSelectedConvId} />
            </div>
            {selectedConv ? (
              <>
                <ConversationView
                  conversation={selectedConv}
                  onSend={(text) => sendMessage(selectedConv.id, text, selectedConnectionId)}
                  onToggleAi={(mode) => toggleAiMode(selectedConv.id, mode)}
                  onShowContext={() => setShowContext((current) => !current)}
                  onCall={openPhoneDialer}
                  onVideoCall={openMeetingDialog}
                  onOpenMenu={() => setShowContext((current) => !current)}
                  messagingEnabled={messagingEnabled}
                  statusHint={statusHint}
                  templates={templates}
                  onOpenMedia={() => setDialog('media')}
                  onOptimize={optimizeWhatsAppDraft}
                  operationsBar={operationsBar}
                />
                {showContext && (
                  <CardContextPanel
                    contactId={selectedConv.id}
                    drawerData={drawerData}
                    assignees={assignees}
                    loading={drawerLoading}
                    onRefresh={() => void refreshDrawer()}
                    onAddNote={async (body) => {
                      await createConversationNote(selectedConv.id, { body });
                      await refreshDrawer();
                    }}
                    onMoveStage={async (columnId, microStatus) => {
                      const nextDrawer = await moveConversationStage(selectedConv.id, { columnId, microStatus });
                      setDrawerData(nextDrawer);
                      await Promise.all([refreshCards(), refreshConversation(selectedConv.id)]);
                    }}
                    onSaveMatter={async (payload) => {
                      if (!drawerData?.matter) return;
                      const updated = await updateMatterDetail(drawerData.matter.id, {
                        responsibleHumanId: payload.responsibleHuman || null,
                        nextAction: payload.nextAction || null,
                        executiveSummary: payload.executiveSummary || null,
                        strategyNotes: payload.strategyNotes || null,
                        priority: payload.priority,
                        urgency: payload.urgency,
                        classification: payload.classification,
                        nextDeadline: payload.nextDeadline || null,
                      });
                      setDrawerData((current) => (current ? { ...current, matter: updated } : current));
                      await Promise.all([refreshCards(), refreshConversation(selectedConv.id)]);
                    }}
                  />
                )}
              </>
            ) : (
              <div className="flex-1 flex items-center justify-center">
                <div className="text-center space-y-3">
                  <MessageSquare className="h-12 w-12 text-muted-foreground/30 mx-auto" />
                  <p className="text-sm text-muted-foreground">Selecione uma conversa</p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
      {renderDialogs}
    </>
  );
}
