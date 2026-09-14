import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  AlertTriangle,
  Bug,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  ClipboardCheck,
  Code2,
  FileCode2,
  FileSearch,
  GitPullRequest,
  HardDrive,
  ListChecks,
  LoaderCircle,
  LockKeyhole,
  MessageSquareText,
  MonitorSmartphone,
  Paperclip,
  Play,
  Plus,
  SearchCode,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  TestTube2,
  Wrench,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { KlmMetricCard, KlmPanel, KlmPill, KlmProgress, KlmSectionTitle } from '@/klm/components/Primitives';
import { cn } from '@/lib/utils';
import {
  klmApi,
  type KlmSupportSeverity,
  type KlmSupportStatus,
  type KlmSupportTicket,
  type KlmSupportTicketCreate,
} from '@/klm/api';
import { collectSupportTechnicalContext } from '@/klm/support-context';
import { submitSupportMessage, submitSupportTicket } from '@/klm/support-offline-queue';
import { useAppStore } from '@/store/app-context';

type Ticket = { id: string; title: string; route: string; severity: string; state: string; detail: string; raw: KlmSupportTicket };

const severityLabels: Record<KlmSupportSeverity, string> = { low: 'Baixa', medium: 'Média', high: 'Alta', critical: 'Crítica' };
const severityKeys: Record<string, KlmSupportSeverity> = { Baixa: 'low', Média: 'medium', Alta: 'high', Crítica: 'critical' };
const statusLabels: Record<KlmSupportStatus, string> = {
  new: 'Recebido',
  triage: 'Em triagem',
  in_progress: 'Em atendimento',
  waiting_customer: 'Aguardando você',
  waiting_internal: 'Aguardando equipe interna',
  validating: 'Em validação',
  resolved: 'Resolvido',
  closed: 'Encerrado',
  cancelled: 'Cancelado',
};

function mapTicket(ticket: KlmSupportTicket): Ticket {
  return {
    id: ticket.publicNumber,
    title: ticket.subject,
    route: ticket.route ?? '/app',
    severity: severityLabels[ticket.severity],
    state: statusLabels[ticket.status],
    detail: ticket.description,
    raw: ticket,
  };
}

const diagnosticIcons: Record<string, typeof HardDrive> = {
  database: HardDrive,
  external_effects_gate: ShieldCheck,
  aws_gate: LockKeyhole,
  model_api_gate: Sparkles,
  local_modules: MonitorSmartphone,
  migrations: FileCode2,
};

const scopes = [
  ['Ler código e documentação', true, 'Leitura local auditável'],
  ['Executar build, lint e testes', true, 'Comandos locais permitidos'],
  ['Criar patch em branch de trabalho', true, 'Mudanças revisáveis'],
  ['Alterar contrato de banco', false, 'Requer aprovação'],
  ['Usar credenciais ou APIs', false, 'Bloqueado nesta fase'],
  ['Fazer deploy ou editar AWS', false, 'Bloqueado até autorização'],
];

export default function TiAgentPage() {
  const queryClient = useQueryClient();
  const { state } = useAppStore();
  const queueScope = state.session?.activeWorkspace?.id && state.currentUser?.id
    ? { workspaceId: state.session.activeWorkspace.id, userId: state.currentUser.id }
    : null;
  const ticketsQuery = useQuery({ queryKey: ['klm', 'support', 'tickets'], queryFn: klmApi.supportTickets });
  const [title, setTitle] = useState('');
  const [detail, setDetail] = useState('');
  const [route, setRoute] = useState('/app');
  const [severity, setSeverity] = useState('Média');
  const [notice, setNotice] = useState<string | null>(null);
  const [evidenceName, setEvidenceName] = useState<string | null>(null);
  const [evidenceFile, setEvidenceFile] = useState<File | null>(null);
  const [selectedTicket, setSelectedTicket] = useState<Ticket | null>(null);
  const [selectedResolution, setSelectedResolution] = useState('');
  const evidenceInputRef = useRef<HTMLInputElement>(null);
  const supportTickets = ticketsQuery.data?.items ?? [];
  const tickets = supportTickets.filter((ticket) => ticket.queue === 'technical').map(mapTicket);
  const openTickets = tickets.filter((ticket) => !['Resolvido', 'Encerrado', 'Cancelado'].includes(ticket.state));
  const resolvedTickets = tickets.filter((ticket) => ticket.state === 'Resolvido');
  const selectedDetailQuery = useQuery({
    queryKey: ['klm', 'support', 'tickets', selectedTicket?.raw.id],
    queryFn: () => klmApi.supportTicket(selectedTicket!.raw.id),
    enabled: Boolean(selectedTicket),
  });

  const createMutation = useMutation({
    mutationFn: () => {
      if (!queueScope) throw new Error('Conta ou usuário não identificado para o envio seguro.');
      const selectedSeverity = severityKeys[severity] ?? 'medium';
      const payload: KlmSupportTicketCreate = {
        ticketType: 'problem',
        subject: title.trim(),
        description: detail.trim(),
        priority: selectedSeverity === 'critical' ? 'urgent' : selectedSeverity === 'high' ? 'high' : 'normal',
        severity: selectedSeverity,
        technicalContext: collectSupportTechnicalContext({ route }),
      };
      return submitSupportTicket(queueScope, payload, evidenceFile ? [evidenceFile] : []);
    },
    onSuccess: async (result) => {
      setTitle('');
      setDetail('');
      setEvidenceName(null);
      setEvidenceFile(null);
      if (result.state === 'queued') {
        setNotice('Solicitação guardada neste dispositivo. Ela entrará na fila técnica após confirmação do backend.');
        return;
      }
      await queryClient.invalidateQueries({ queryKey: ['klm', 'support'] });
      setNotice(`Chamado ${result.record?.publicNumber ?? ''} confirmado e adicionado à fila técnica.`);
    },
    onError: () => setNotice('Não foi possível registrar a solicitação agora.'),
  });

  const createTicket = () => {
    if (!title.trim() || !detail.trim()) return;
    createMutation.mutate();
  };

  const diagnosticsMutation = useMutation({
    mutationFn: klmApi.runLocalDiagnostics,
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
      setNotice(`Diagnóstico ${result.status === 'healthy' ? 'saudável' : 'degradado'}: ${result.summary.healthy}/${result.summary.total} verificações verdes.`);
    },
    onError: () => setNotice('O endpoint local de diagnóstico não respondeu.'),
  });
  const diagnosticItems = diagnosticsMutation.data?.checks ?? [];

  const updateMutation = useMutation({
    mutationFn: () => {
      if (!selectedTicket) throw new Error('Solicitação técnica indisponível');
      if (!queueScope) throw new Error('Conta ou usuário não identificado para o envio seguro.');
      if (!selectedResolution.trim()) throw new Error('Escreva a mensagem antes de enviar.');
      return submitSupportMessage(queueScope, selectedTicket.raw.id, selectedResolution.trim(), []);
    },
    onSuccess: async (result) => {
      setSelectedResolution('');
      if (result.state === 'confirmed') {
        await queryClient.invalidateQueries({ queryKey: ['klm', 'support', 'tickets', selectedTicket?.raw.id] });
        setNotice('Mensagem confirmada no histórico do chamado.');
      } else {
        setNotice('Mensagem guardada neste dispositivo e aguardando confirmação.');
      }
    },
    onError: () => setNotice('Não foi possível enviar a mensagem agora.'),
  });

  const lifecycleMutation = useMutation({
    mutationFn: (action: 'close' | 'reopen') => {
      if (!selectedTicket) throw new Error('Solicitação técnica indisponível');
      return action === 'close'
        ? klmApi.closeSupportTicket(selectedTicket.raw.id)
        : klmApi.reopenSupportTicket(selectedTicket.raw.id);
    },
    onSuccess: async (updated) => {
      await queryClient.invalidateQueries({ queryKey: ['klm', 'support'] });
      setSelectedTicket(mapTicket(updated));
      setNotice('Situação do chamado atualizada com trilha de auditoria.');
    },
    onError: () => setNotice('Não foi possível alterar a situação do chamado.'),
  });

  const openTicket = (ticket: Ticket) => {
    setSelectedTicket(ticket);
    setSelectedResolution('');
  };
  const frontendVersion = String(import.meta.env.VITE_APP_VERSION ?? '').trim() || 'não configurada';
  const selectedDetail = selectedDetailQuery.data;
  const selectedTicketStatus = selectedDetail?.status ?? selectedTicket?.raw.status ?? null;
  const selectedTicketIsFinal = selectedTicketStatus != null
    && ['resolved', 'closed', 'cancelled'].includes(selectedTicketStatus);
  const selectedTicketCanClose = selectedTicketStatus != null
    && ['resolved', 'validating'].includes(selectedTicketStatus);

  return (
    <div className="mx-auto max-w-[1220px] space-y-5">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between"><div><p className="text-2xs font-semibold uppercase text-[#0057d8]">Meu Executivo / Engenharia interna</p><h2 className="mt-2 text-2xl font-semibold">TI Agent</h2><p className="mt-1 max-w-3xl text-xs leading-4 text-muted-foreground">Bugs, melhorias, diagnóstico técnico, patches locais e validação com trilha de aprovação.</p></div><div className="flex items-center gap-2"><KlmPill tone="green">acesso local controlado</KlmPill><Button onClick={() => diagnosticsMutation.mutate()} disabled={diagnosticsMutation.isPending} className="h-9 bg-[#031121] text-xs text-white hover:bg-[#031121]">{diagnosticsMutation.isPending ? <LoaderCircle className="mr-2 h-3.5 w-3.5 animate-spin" /> : <Play className="mr-2 h-3.5 w-3.5" />}Diagnóstico</Button></div></header>

      {notice && <div className="flex items-center justify-between gap-3 rounded-lg border border-primary/35 bg-primary/10 px-3 py-2 text-2xs"><span>{notice}</span><Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setNotice(null)}>Fechar</Button></div>}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4"><KlmMetricCard label="Solicitações abertas" value={String(openTickets.length)} delta={`${openTickets.filter((ticket) => ['Alta', 'Crítica'].includes(ticket.severity)).length} alta/crítica`} trend="neutral" /><KlmMetricCard label="Resolvidas localmente" value={String(resolvedTickets.length)} delta="fila atual" trend="up" /><KlmMetricCard label="Checks saudáveis" value={diagnosticsMutation.data ? `${diagnosticsMutation.data.summary.healthy}/${diagnosticsMutation.data.summary.total}` : '—'} delta="última execução real" trend={diagnosticsMutation.data?.status === 'healthy' ? 'up' : 'neutral'} /><KlmMetricCard label="Indisponíveis" value={diagnosticsMutation.data ? String(diagnosticsMutation.data.summary.unavailable) : '—'} delta="última execução real" trend="neutral" /></div>

      {(diagnosticsMutation.isPending || diagnosticsMutation.data) && <KlmPanel className="p-4"><div className="flex items-center justify-between text-2xs"><span className="flex items-center gap-2">{diagnosticsMutation.isPending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin text-primary" /> : <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />}{diagnosticsMutation.isPending ? 'Executando verificações reais no runtime local' : `Diagnóstico concluído em ${new Date(diagnosticsMutation.data!.checkedAt).toLocaleString('pt-BR')}`}</span>{diagnosticsMutation.data && <KlmPill tone={diagnosticsMutation.data.status === 'healthy' ? 'green' : 'gold'}>{diagnosticsMutation.data.status}</KlmPill>}</div></KlmPanel>}

      <Tabs defaultValue="solicitar" className="space-y-4"><TabsList className="h-9 w-full justify-start overflow-x-auto bg-secondary p-1 sm:w-auto"><TabsTrigger className="text-xs" value="solicitar">Nova solicitação</TabsTrigger><TabsTrigger className="text-xs" value="fila">Fila técnica</TabsTrigger><TabsTrigger className="text-xs" value="diagnostico">Diagnóstico</TabsTrigger><TabsTrigger className="text-xs" value="acessos">Acessos</TabsTrigger></TabsList>

        <TabsContent value="solicitar"><div className="grid gap-4 lg:grid-cols-[1fr_340px]">
              <KlmPanel className="p-5"><KlmSectionTitle title="Pedir correção ou melhoria" description="A solicitação nasce no SAC canônico, com contexto suficiente para reprodução e teste." action={<KlmPill tone="black">nativo</KlmPill>} /><div className="mt-5 space-y-4"><div><label htmlFor="ticket-title" className="text-2xs font-semibold">Título</label><Input id="ticket-title" value={title} onChange={(event) => setTitle(event.target.value)} className="mt-2" placeholder="Ex.: Ajustar filtros do funil no mobile" /></div><div className="grid gap-3 sm:grid-cols-2"><div><label className="text-2xs font-semibold">Tela</label><Select value={route} onValueChange={setRoute}><SelectTrigger aria-label="Tela da solicitação" className="mt-2"><SelectValue /></SelectTrigger><SelectContent>{['/app', '/app/conversas', '/app/leads', '/app/funil', '/app/kpis', '/app/flows', '/app/agenda', '/app/configuracoes', '/app/ti'].map((path) => <SelectItem key={path} value={path}>{path}</SelectItem>)}</SelectContent></Select></div><div><label className="text-2xs font-semibold">Prioridade</label><Select value={severity} onValueChange={setSeverity}><SelectTrigger aria-label="Prioridade da solicitação" className="mt-2"><SelectValue /></SelectTrigger><SelectContent>{['Baixa', 'Média', 'Alta', 'Crítica'].map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}</SelectContent></Select></div></div><div><label htmlFor="ticket-detail" className="text-2xs font-semibold">Resultado esperado e contexto</label><Textarea id="ticket-detail" value={detail} onChange={(event) => setDetail(event.target.value)} className="mt-2 min-h-[150px] text-xs" placeholder="Descreva o que aconteceu, o que deveria acontecer e em qual fluxo percebeu o problema." /></div><input ref={evidenceInputRef} type="file" className="sr-only" aria-label="Selecionar evidência local" accept="image/*,.pdf,.txt,.log" onChange={(event) => { const file = event.target.files?.[0] ?? null; setEvidenceName(file?.name ?? null); setEvidenceFile(file); if (file) setNotice(`“${file.name}” será enviado ao armazenamento privado e vinculado ao chamado após confirmação.`); }} /><div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"><Button variant="outline" className="text-xs" onClick={() => evidenceInputRef.current?.click()}><Paperclip className="mr-2 h-3.5 w-3.5" />{evidenceName ?? 'Adicionar evidência'}</Button><Button onClick={createTicket} disabled={!title.trim() || !detail.trim() || createMutation.isPending} className="bg-[#031121] text-xs text-white hover:bg-[#031121]"><Plus className="mr-2 h-3.5 w-3.5" />{createMutation.isPending ? 'Registrando' : 'Criar solicitação'}</Button></div></div></KlmPanel>
          <div className="space-y-4"><KlmPanel tone="black" className="p-5"><Sparkles className="h-5 w-5 text-primary" /><h2 className="mt-4 text-lg font-semibold">Contexto incluído ao criar</h2><div className="mt-4 space-y-2">{[['Rota selecionada', route], ['Viewport', `${window.innerWidth} × ${window.innerHeight}`], ['Versão do frontend', frontendVersion], ['Navegador e idioma', 'capturados no envio'], ['Console e rede', 'não capturados'], ['Gate externo', '0 ações executadas']].map(([label, value]) => <div key={label} className="flex justify-between gap-4 border-b border-white/10 py-2 text-2xs"><span className="text-white/50">{label}</span><strong className="text-right text-primary">{value}</strong></div>)}</div></KlmPanel><KlmPanel className="p-4"><div className="flex gap-3"><LockKeyhole className="h-5 w-5 text-[#0057d8]" /><div><h3 className="text-sm font-semibold">Limite atual</h3><p className="mt-1 text-2xs leading-4 text-muted-foreground">O TI Agent pode preparar e testar patches locais. Deploy, infraestrutura, credenciais e integrações permanecem bloqueados.</p></div></div></KlmPanel></div>
        </div></TabsContent>

        <TabsContent value="fila"><KlmPanel className="p-4"><KlmSectionTitle title="Fila técnica" description="Os mesmos chamados técnicos da Central de SAC, sem registros paralelos." action={<KlmPill tone="gold">{openTickets.length} abertos</KlmPill>} /><div className="mt-4 divide-y rounded-lg border">{tickets.map((ticket) => <button key={ticket.id} type="button" data-testid="technical-ticket-row" onClick={() => openTicket(ticket)} className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-4 p-4 text-left hover:bg-secondary"><span className="min-w-0"><span className="flex flex-wrap items-center gap-2"><KlmPill tone="black">{ticket.id}</KlmPill><strong className="break-words text-xs">{ticket.title}</strong></span><span className="mt-2 block break-words text-2xs text-muted-foreground">{ticket.route} · {ticket.detail}</span></span><span className="flex flex-wrap items-center justify-end gap-2"><KlmPill tone={ticket.severity === 'Alta' || ticket.severity === 'Crítica' ? 'red' : 'gold'}>{ticket.severity}</KlmPill><KlmPill tone={ticket.state === 'Resolvido' || ticket.state === 'Encerrado' ? 'green' : 'muted'}>{ticket.state}</KlmPill><ChevronRight className="hidden h-4 w-4 text-muted-foreground sm:block" /></span></button>)}{!ticketsQuery.isLoading && tickets.length === 0 && <div className="p-8 text-center text-xs text-muted-foreground">Nenhum chamado técnico registrado nesta conta.</div>}</div></KlmPanel></TabsContent>

        <TabsContent value="diagnostico"><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{diagnosticItems.map((item) => { const Icon = diagnosticIcons[item.key] ?? Activity; return <KlmPanel key={item.key} className="p-4"><div className="flex items-start justify-between"><div className="flex h-9 w-9 items-center justify-center rounded-md bg-[#031121] text-primary"><Icon className="h-4 w-4" /></div><KlmPill tone={item.status === 'healthy' ? 'green' : 'gold'}>{item.state}</KlmPill></div><h2 className="mt-4 text-sm font-semibold">{item.label}</h2><p className="mt-1 text-2xs text-muted-foreground">{item.detail}</p><p className="mt-3 text-2xs text-muted-foreground">{new Date(item.checkedAt).toLocaleString('pt-BR')}</p></KlmPanel>; })}{!diagnosticsMutation.data && !diagnosticsMutation.isPending && <KlmPanel className="col-span-full p-8 text-center text-xs text-muted-foreground">Execute o diagnóstico para consultar banco, gates, módulos locais e migrations em tempo real.</KlmPanel>}</div></TabsContent>

        <TabsContent value="acessos"><KlmPanel className="p-4"><KlmSectionTitle title="Escopos do TI Agent" description="Acessos mínimos e limites de aprovação." action={<KlmPill tone="green">least privilege</KlmPill>} /><div className="mt-4 divide-y rounded-lg border">{scopes.map(([label, allowed, detail]) => <div key={String(label)} className="grid grid-cols-[28px_1fr_auto] items-center gap-3 p-4">{allowed ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <LockKeyhole className="h-4 w-4 text-red-500" />}<span><strong className="block text-xs">{label}</strong><span className="mt-1 block text-2xs text-muted-foreground">{detail}</span></span><KlmPill tone={allowed ? 'green' : 'red'}>{allowed ? 'Permitido' : 'Bloqueado'}</KlmPill></div>)}</div></KlmPanel></TabsContent>
      </Tabs>
      <Dialog open={Boolean(selectedTicket)} onOpenChange={(open) => { if (!open) setSelectedTicket(null); }}>
        <DialogContent className="max-w-xl">
          <DialogHeader><DialogTitle>{selectedTicket?.id} · {selectedTicket?.title}</DialogTitle><DialogDescription>Mesmo chamado da Central de SAC, com atendimento e auditoria compartilhados.</DialogDescription></DialogHeader>
          <div className="space-y-4 text-2xs">
            <div className="grid gap-3 rounded-lg border bg-secondary/40 p-3 sm:grid-cols-3">
              <span><strong className="block">Tela</strong>{selectedTicket?.route}</span>
              <span><strong className="block">Situação</strong>{selectedTicketStatus ? statusLabels[selectedTicketStatus] : 'Carregando'}</span>
              <span><strong className="block">Anexos</strong>{selectedDetail?.attachments.length ?? 0} registro(s)</span>
            </div>
            <p className="break-words border-l-2 border-primary bg-secondary p-3 leading-5">{selectedTicket?.raw.description}</p>
            <section aria-label="Conversa pública do chamado">
              <h3 className="font-semibold">Conversa com Suporte e TI</h3>
              <div className="mt-2 max-h-48 space-y-2 overflow-y-auto rounded-lg border p-3">
                {selectedDetailQuery.isLoading && <p className="text-muted-foreground">Carregando histórico...</p>}
                {!selectedDetailQuery.isLoading && (selectedDetail?.messages.length ?? 0) === 0 && <p className="text-muted-foreground">Nenhuma resposta pública registrada ainda.</p>}
                {selectedDetail?.messages.map((message) => (
                  <article key={message.id} className="rounded-md bg-secondary p-3">
                    <div className="flex items-center justify-between gap-3">
                      <strong>{message.authorLabel}</strong>
                      <time className="text-muted-foreground">{new Date(message.createdAt).toLocaleString('pt-BR')}</time>
                    </div>
                    <p className="mt-1 whitespace-pre-wrap break-words leading-5">{message.body}</p>
                  </article>
                ))}
              </div>
            </section>
            <div>
              <label htmlFor="technical-message" className="font-semibold">Mensagem para Suporte e TI</label>
              <Textarea id="technical-message" value={selectedResolution} onChange={(event) => setSelectedResolution(event.target.value)} className="mt-2 min-h-24 text-xs" placeholder="Acrescente contexto, responda à equipe ou confirme o resultado." />
            </div>
          </div>
          <DialogFooter className="flex-wrap">
            <Button variant="outline" onClick={() => setSelectedTicket(null)}>Fechar</Button>
            {selectedTicketIsFinal && <Button variant="outline" onClick={() => lifecycleMutation.mutate('reopen')} disabled={lifecycleMutation.isPending}>Reabrir chamado</Button>}
            {!selectedTicketIsFinal && selectedTicketCanClose && <Button variant="outline" onClick={() => lifecycleMutation.mutate('close')} disabled={lifecycleMutation.isPending}>Confirmar solução</Button>}
            <Button onClick={() => updateMutation.mutate()} disabled={!selectedTicket || !selectedResolution.trim() || updateMutation.isPending}>{updateMutation.isPending ? 'Enviando' : 'Enviar mensagem'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
