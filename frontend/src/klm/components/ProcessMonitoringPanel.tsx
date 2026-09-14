import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, ExternalLink, FilePlus2, Gavel, Loader2, MessageSquareText, Plus, RotateCw } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { KlmMetricCard, KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { klmApi, type KlmProcessMonitoringRecord } from '@/klm/api';

type ProcessDraft = {
  matterId: string;
  caseNumber: string;
  court: string;
  status: string;
  nextDeadlineAt: string;
  lastEventSummary: string;
};

const emptyDraft = (matterId = ''): ProcessDraft => ({
  matterId,
  caseNumber: '',
  court: '',
  status: 'cadastrado',
  nextDeadlineAt: '',
  lastEventSummary: '',
});

const toIsoOrNull = (value: string) => value ? new Date(value).toISOString() : null;
const statusLabel = (value: string) => value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());

export function ProcessMonitoringPanel({
  matterId,
  canManageIntegrations = false,
  onNavigate,
}: {
  matterId?: string | null;
  canManageIntegrations?: boolean;
  onNavigate?: (path: string) => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [eventRecord, setEventRecord] = useState<KlmProcessMonitoringRecord | null>(null);
  const [detailRecord, setDetailRecord] = useState<KlmProcessMonitoringRecord | null>(null);
  const [draft, setDraft] = useState<ProcessDraft>(emptyDraft(matterId ?? ''));
  const [eventDraft, setEventDraft] = useState({ title: '', description: '' });
  const [notice, setNotice] = useState<string | null>(null);

  const recordsQuery = useQuery({
    queryKey: ['klm', 'process-monitoring', matterId ?? 'all'],
    queryFn: () => klmApi.processMonitoring(matterId ?? undefined),
  });
  const funnelQuery = useQuery({ queryKey: ['klm', 'funnel'], queryFn: klmApi.funnel });
  const cardsById = useMemo(() => new Map((funnelQuery.data?.cards ?? []).map((card) => [card.id, card])), [funnelQuery.data?.cards]);
  const metrics = useMemo(() => {
    const now = Date.now();
    const sevenDays = now + 7 * 24 * 60 * 60 * 1000;
    const records = recordsQuery.data ?? [];
    return {
      total: records.length,
      withDeadline: records.filter((record) => record.nextDeadlineAt).length,
      overdue: records.filter((record) => record.nextDeadlineAt && Date.parse(record.nextDeadlineAt) < now).length,
      nextSevenDays: records.filter((record) => {
        const timestamp = record.nextDeadlineAt ? Date.parse(record.nextDeadlineAt) : Number.NaN;
        return Number.isFinite(timestamp) && timestamp >= now && timestamp <= sevenDays;
      }).length,
    };
  }, [recordsQuery.data]);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['klm', 'process-monitoring'] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
  };
  const createMutation = useMutation({
    mutationFn: () => klmApi.createProcessMonitoring({
      matterId: draft.matterId,
      caseNumber: draft.caseNumber.trim(),
      court: draft.court.trim() || null,
      status: draft.status.trim(),
      nextDeadlineAt: toIsoOrNull(draft.nextDeadlineAt),
      lastEventSummary: draft.lastEventSummary.trim() || null,
    }),
    onSuccess: (record) => {
      setCreateOpen(false);
      setDetailRecord(record);
      setNotice(`Processo ${record.caseNumber} vinculado ao card.`);
      refresh();
    },
    onError: () => setNotice('O processo não foi cadastrado. Confira o número e o vínculo com o card.'),
  });
  const eventMutation = useMutation({
    mutationFn: () => {
      if (!eventRecord) throw new Error('Processo não selecionado');
      return klmApi.addProcessMonitoringEvent(eventRecord.id, {
        title: eventDraft.title.trim(),
        description: eventDraft.description.trim() || null,
        source: 'manual_local',
      });
    },
    onSuccess: (record) => {
      setEventRecord(null);
      setDetailRecord(record);
      setEventDraft({ title: '', description: '' });
      setNotice('Movimentação persistida no histórico do processo.');
      refresh();
    },
    onError: () => setNotice('A movimentação não foi registrada.'),
  });
  const updateDraftMutation = useMutation({
    mutationFn: (record: KlmProcessMonitoringRecord) => klmApi.createLegalArtifact({
      matterId: record.matterId,
      kind: 'client_update',
      title: `Atualização do processo ${record.caseNumber}`,
      content: {
        processMonitoringId: record.id,
        body: record.lastEventSummary || 'Atualização ainda sem texto. Revise antes de enviar para aprovação.',
        channel: 'whatsapp',
        externalEffectsExecuted: 0,
      },
    }),
    onSuccess: () => {
      setNotice('Rascunho de atualização criado. Revise-o e envie para aprovação; nada foi enviado ao cliente.');
      void queryClient.invalidateQueries({ queryKey: ['klm', 'legal-artifacts'] });
    },
    onError: () => setNotice('Não foi possível preparar o rascunho de atualização.'),
  });
  const courtIntentMutation = useMutation({
    mutationFn: (record: KlmProcessMonitoringRecord) => klmApi.createExternalIntent({
      matterId: record.matterId,
      actionType: 'process_external_check',
      provider: 'court_connector',
      idempotencyKey: `process-check:${record.id}:${crypto.randomUUID()}`,
      payload: { processMonitoringId: record.id, externalEffectsExecuted: 0 },
    }),
    onSuccess: () => {
      setNotice('Consulta externa registrada como intenção bloqueada. Nenhum tribunal foi acessado.');
      void queryClient.invalidateQueries({ queryKey: ['klm', 'external-intents'] });
    },
    onError: () => setNotice('A intenção de consulta não foi registrada.'),
  });

  const openCreate = () => {
    setDraft(emptyDraft(matterId ?? ''));
    setCreateOpen(true);
  };
  const canCreate = draft.matterId && draft.caseNumber.trim().length >= 5 && draft.status.trim().length >= 2;

  return (
    <div className="space-y-4" data-testid="process-monitoring-panel">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KlmMetricCard label="Processos" value={String(metrics.total)} delta="registros do workspace" trend="neutral" />
        <KlmMetricCard label="Com prazo" value={String(metrics.withDeadline)} delta="prazo persistido" trend="neutral" />
        <KlmMetricCard label="Próximos 7 dias" value={String(metrics.nextSevenDays)} delta="comparação com agora" trend="neutral" />
        <KlmMetricCard label="Vencidos" value={String(metrics.overdue)} delta="exigem conferência humana" trend={metrics.overdue ? 'down' : 'neutral'} />
      </div>
      <KlmPanel className="p-4 sm:p-5">
        <KlmSectionTitle
          title="Monitoramento processual"
          description="Processos vinculados ao card, histórico local e prazos. Consultas externas permanecem bloqueadas até a conexão ser autorizada."
          action={<Button size="sm" className="h-8 bg-[#031121] text-2xs text-white" onClick={openCreate}><Plus className="mr-1.5 h-3.5 w-3.5" />Vincular processo</Button>}
        />
        {notice ? <div className="mt-3 flex items-center justify-between gap-3 border-l-2 border-[#b98224] bg-[#b98224]/8 px-3 py-2 text-2xs"><span>{notice}</span><Button variant="ghost" size="sm" className="h-6 px-2 text-2xs" onClick={() => setNotice(null)}>Fechar</Button></div> : null}
        {recordsQuery.isPending ? (
          <div className="mt-4 flex min-h-36 items-center justify-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Carregando processos...</div>
        ) : recordsQuery.isError ? (
          <div className="mt-4 flex items-center justify-between border p-4 text-xs"><span>O monitoramento não pôde ser consultado.</span><Button variant="outline" size="sm" onClick={() => void recordsQuery.refetch()}><RotateCw className="mr-1.5 h-3.5 w-3.5" />Tentar novamente</Button></div>
        ) : (recordsQuery.data ?? []).length === 0 ? (
          <div className="mt-4 border border-dashed p-6 text-center text-xs text-muted-foreground">Nenhum processo foi vinculado aos cards retornados.</div>
        ) : (
          <div className="mt-4 divide-y border">
            {(recordsQuery.data ?? []).map((record) => {
              const card = cardsById.get(record.matterId);
              const deadline = record.nextDeadlineAt ? new Date(record.nextDeadlineAt) : null;
              const overdue = deadline ? deadline.getTime() < Date.now() : false;
              return (
                <div key={record.id} className="grid gap-3 p-4 xl:grid-cols-[1fr_auto] xl:items-center">
                  <button className="min-w-0 text-left" onClick={() => setDetailRecord(record)}>
                    <div className="flex flex-wrap items-center gap-2"><span className="text-xs font-semibold">{record.caseNumber}</span><KlmPill tone={overdue ? 'red' : 'gold'}>{statusLabel(record.status)}</KlmPill></div>
                    <p className="mt-1 truncate text-2xs text-muted-foreground">{card?.name ?? `caso ${record.matterId.slice(0, 8)}`} · {record.court || 'tribunal não informado'} · {deadline ? `prazo ${deadline.toLocaleString('pt-BR')}` : 'sem prazo registrado'}</p>
                    <p className="mt-1 truncate text-2xs">{record.lastEventSummary || 'Sem movimentação registrada.'}</p>
                  </button>
                  <div className="flex flex-wrap gap-2">
                    <Button variant="outline" size="sm" className="h-7 text-2xs" onClick={() => { setEventRecord(record); setEventDraft({ title: '', description: '' }); }}><FilePlus2 className="mr-1.5 h-3 w-3" />Movimentação</Button>
                    <Button variant="outline" size="sm" className="h-7 text-2xs" onClick={() => updateDraftMutation.mutate(record)}><MessageSquareText className="mr-1.5 h-3 w-3" />Atualização</Button>
                    <Button variant="outline" size="sm" className="h-7 text-2xs" disabled={!canManageIntegrations || courtIntentMutation.isPending} title={!canManageIntegrations ? 'Permissão integrations.manage necessária' : 'Registrar intenção local de consulta'} onClick={() => courtIntentMutation.mutate(record)}><ExternalLink className="mr-1.5 h-3 w-3" />Consulta bloqueada</Button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </KlmPanel>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader><DialogTitle>Vincular processo ao card</DialogTitle><DialogDescription>O cadastro é local. Nenhuma consulta automática será feita neste momento.</DialogDescription></DialogHeader>
          <label className="space-y-1 text-2xs font-medium">Caso
            <Select value={draft.matterId} onValueChange={(value) => setDraft((current) => ({ ...current, matterId: value }))} disabled={Boolean(matterId)}>
              <SelectTrigger className="mt-1 h-9 text-xs"><SelectValue placeholder="Selecione um card" /></SelectTrigger>
              <SelectContent>{(funnelQuery.data?.cards ?? []).map((card) => <SelectItem key={card.id} value={card.id}>{card.name} · {card.object}</SelectItem>)}</SelectContent>
            </Select>
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="space-y-1 text-2xs font-medium">Número do processo<Input className="mt-1" value={draft.caseNumber} onChange={(event) => setDraft((current) => ({ ...current, caseNumber: event.target.value }))} /></label>
            <label className="space-y-1 text-2xs font-medium">Tribunal<Input className="mt-1" value={draft.court} onChange={(event) => setDraft((current) => ({ ...current, court: event.target.value }))} /></label>
            <label className="space-y-1 text-2xs font-medium">Status<Input className="mt-1" value={draft.status} onChange={(event) => setDraft((current) => ({ ...current, status: event.target.value }))} /></label>
            <label className="space-y-1 text-2xs font-medium">Próximo prazo<Input type="datetime-local" className="mt-1" value={draft.nextDeadlineAt} onChange={(event) => setDraft((current) => ({ ...current, nextDeadlineAt: event.target.value }))} /></label>
          </div>
          <label className="space-y-1 text-2xs font-medium">Movimentação inicial<Textarea className="mt-1 min-h-24" value={draft.lastEventSummary} onChange={(event) => setDraft((current) => ({ ...current, lastEventSummary: event.target.value }))} /></label>
          <DialogFooter><Button variant="outline" onClick={() => setCreateOpen(false)}>Cancelar</Button><Button className="bg-[#031121] text-white" disabled={!canCreate || createMutation.isPending} onClick={() => createMutation.mutate()}>{createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Cadastrar</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(eventRecord)} onOpenChange={(open) => !open && setEventRecord(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>Registrar movimentação</DialogTitle><DialogDescription>{eventRecord?.caseNumber} · histórico local e auditado.</DialogDescription></DialogHeader>
          <label className="space-y-1 text-2xs font-medium">Título<Input className="mt-1" value={eventDraft.title} onChange={(event) => setEventDraft((current) => ({ ...current, title: event.target.value }))} /></label>
          <label className="space-y-1 text-2xs font-medium">Descrição<Textarea className="mt-1 min-h-28" value={eventDraft.description} onChange={(event) => setEventDraft((current) => ({ ...current, description: event.target.value }))} /></label>
          <DialogFooter><Button variant="outline" onClick={() => setEventRecord(null)}>Cancelar</Button><Button className="bg-[#031121] text-white" disabled={eventDraft.title.trim().length < 2 || eventMutation.isPending} onClick={() => eventMutation.mutate()}>Registrar</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(detailRecord)} onOpenChange={(open) => !open && setDetailRecord(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>{detailRecord?.caseNumber}</DialogTitle><DialogDescription>{detailRecord?.court || 'Tribunal não informado'} · {detailRecord ? statusLabel(detailRecord.status) : ''}</DialogDescription></DialogHeader>
          <div className="grid grid-cols-2 gap-3 text-2xs"><div className="border bg-secondary p-3"><CalendarClock className="mb-2 h-4 w-4" /><span className="text-muted-foreground">Próximo prazo</span><strong className="mt-1 block text-xs">{detailRecord?.nextDeadlineAt ? new Date(detailRecord.nextDeadlineAt).toLocaleString('pt-BR') : 'Não registrado'}</strong></div><div className="border bg-secondary p-3"><Gavel className="mb-2 h-4 w-4" /><span className="text-muted-foreground">Origem</span><strong className="mt-1 block text-xs">{detailRecord?.sourceState === 'manual_local' ? 'Cadastro manual local' : detailRecord?.sourceState}</strong></div></div>
          <div className="max-h-[42vh] space-y-2 overflow-y-auto">
            {(detailRecord?.events ?? []).length ? [...(detailRecord?.events ?? [])].reverse().map((event) => <div key={event.id} className="border-l-2 border-[#b98224] bg-secondary px-3 py-2"><div className="flex items-center justify-between gap-3"><strong className="text-xs">{event.title}</strong><span className="text-2xs text-muted-foreground">{new Date(event.occurredAt).toLocaleString('pt-BR')}</span></div>{event.description ? <p className="mt-1 text-2xs leading-4 text-muted-foreground">{event.description}</p> : null}</div>) : <p className="border border-dashed p-4 text-center text-xs text-muted-foreground">Sem movimentações.</p>}
          </div>
          <DialogFooter><Button variant="outline" onClick={() => { const path = `/app/leads?matter=${detailRecord?.matterId ?? ''}`; if (onNavigate) onNavigate(path); else navigate(path); }}>Abrir card</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
