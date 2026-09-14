import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, CheckCheck, Clock, HandCoins, Loader2, Plus, ReceiptText, ShieldAlert } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { KlmPanel, KlmPill } from '@/klm/components/Primitives';
import {
  klmApi,
  type KlmFeeArrangement,
  type KlmFeeArrangementKind,
  type KlmFeeBillingType,
} from '@/klm/api';
import { fetchTeamMembers } from '@/services/admin';
import { formatBRL } from '@/lib/formatters';

const KIND_LABEL: Record<KlmFeeArrangementKind, string> = {
  fixo: 'Valor fixo',
  entrada_exito: 'Entrada + êxito',
  mensalidade: 'Mensalidade',
  por_hora: 'Por hora',
  exito: 'Êxito',
  quota_litis: 'Quota litis',
  hibrido: 'Híbrido',
};

const BILLING_TYPE_LABEL: Record<KlmFeeBillingType, string> = {
  UNDEFINED: 'Cliente escolhe',
  BOLETO: 'Boleto',
  CREDIT_CARD: 'Cartão de crédito',
  PIX: 'Pix',
};

const GENERATABLE_KINDS: KlmFeeArrangementKind[] = ['fixo', 'entrada_exito', 'mensalidade'];
const SUCCESS_KINDS: KlmFeeArrangementKind[] = ['exito', 'quota_litis', 'hibrido'];

function arrangementValueLabel(arrangement: KlmFeeArrangement): string {
  const parts: string[] = [];
  if (arrangement.fixedValue !== null) parts.push(formatBRL(arrangement.fixedValue));
  if (arrangement.percent !== null) parts.push(`${arrangement.percent}%`);
  if (arrangement.hourlyRate !== null) parts.push(`${formatBRL(arrangement.hourlyRate)}/h`);
  return parts.length > 0 ? parts.join(' · ') : '—';
}

type ArrangementDraft = {
  matterId: string;
  kind: KlmFeeArrangementKind;
  fixedValue: string;
  percent: string;
  baseCalculoExito: string;
  hourlyRate: string;
  notes: string;
};

const EMPTY_DRAFT: ArrangementDraft = {
  matterId: '',
  kind: 'fixo',
  fixedValue: '',
  percent: '',
  baseCalculoExito: '',
  hourlyRate: '',
  notes: '',
};

type GenerateDraft = {
  firstDueDate: string;
  billingType: KlmFeeBillingType;
  customerExternalId: string;
  installmentCount: string;
};

const EMPTY_GENERATE_DRAFT: GenerateDraft = {
  firstDueDate: '',
  billingType: 'UNDEFINED',
  customerExternalId: '',
  installmentCount: '2',
};

type TimeEntryDraft = {
  userId: string;
  date: string;
  minutes: string;
  description: string;
};

const EMPTY_TIME_ENTRY_DRAFT: TimeEntryDraft = {
  userId: '',
  date: new Date().toISOString().slice(0, 10),
  minutes: '',
  description: '',
};

function toNumberOrUndefined(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const normalized = Number(value.replace(',', '.'));
  return Number.isFinite(normalized) ? normalized : undefined;
}

/**
 * Painel "Honorários" (F9) — arranjos de honorários por caso (fixo, entrada+êxito,
 * mensalidade, por hora, êxito/quota litis/híbrido), lançamento de horas, fechamento
 * de fatura mensal, fila de aprovação de êxito e renegociação de débito vencido.
 * Consome app/api/v1/endpoints/fee_arrangements.py.
 */
export function FeeArrangementsPanel({ canManage }: { canManage: boolean }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [matterFilter, setMatterFilter] = useState('todos');
  const [createOpen, setCreateOpen] = useState(false);
  const [draft, setDraft] = useState<ArrangementDraft>(EMPTY_DRAFT);

  const [generateTarget, setGenerateTarget] = useState<KlmFeeArrangement | null>(null);
  const [generateDraft, setGenerateDraft] = useState<GenerateDraft>(EMPTY_GENERATE_DRAFT);
  const [generateResultNotice, setGenerateResultNotice] = useState<string | null>(null);

  const [timeEntryTarget, setTimeEntryTarget] = useState<KlmFeeArrangement | null>(null);
  const [timeEntryDraft, setTimeEntryDraft] = useState<TimeEntryDraft>(EMPTY_TIME_ENTRY_DRAFT);

  const [invoiceTarget, setInvoiceTarget] = useState<KlmFeeArrangement | null>(null);
  const [invoiceCompetence, setInvoiceCompetence] = useState(new Date().toISOString().slice(0, 7));

  const [successTarget, setSuccessTarget] = useState<KlmFeeArrangement | null>(null);
  const [successBaseValue, setSuccessBaseValue] = useState('');

  const [renegotiateEntryId, setRenegotiateEntryId] = useState('');
  const [renegotiateOpen, setRenegotiateOpen] = useState(false);
  const [renegotiateInstallments, setRenegotiateInstallments] = useState('2');
  const [renegotiateValue, setRenegotiateValue] = useState('');
  const [renegotiateFirstDueDate, setRenegotiateFirstDueDate] = useState('');

  const matterFunnelQuery = useQuery({ queryKey: ['klm', 'fee-matters-funnel'], queryFn: klmApi.funnel });
  const matterOptions = useMemo(
    () => matterFunnelQuery.data?.cards ?? [],
    [matterFunnelQuery.data?.cards],
  );
  const matterById = useMemo(() => new Map(matterOptions.map((card) => [card.id, card])), [matterOptions]);

  const teamQuery = useQuery({ queryKey: ['klm', 'fee-team-members'], queryFn: fetchTeamMembers });
  const teamMembers = teamQuery.data ?? [];

  const arrangementsQuery = useQuery({
    queryKey: ['klm', 'fee-arrangements', matterFilter],
    queryFn: () => klmApi.feeArrangements(matterFilter === 'todos' ? undefined : matterFilter),
  });
  const arrangements = arrangementsQuery.data ?? [];

  const overdueEntriesQuery = useQuery({
    queryKey: ['klm', 'fee-overdue-entries'],
    queryFn: () => klmApi.financeEntries(),
  });
  const overdueEntries = (overdueEntriesQuery.data ?? []).filter((entry) => entry.status === 'overdue');

  const successApprovalsQuery = useQuery({ queryKey: ['klm', 'fee-success-approvals'], queryFn: klmApi.approvals });
  const pendingSuccessApprovals = (successApprovalsQuery.data ?? []).filter(
    (approval) => approval.type === 'fee_success_event' && approval.status === 'pending',
  );

  const invalidateAll = () => {
    void queryClient.invalidateQueries({ queryKey: ['klm', 'fee-arrangements'] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'fee-success-approvals'] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'fee-overdue-entries'] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'finance'] });
  };

  const createMutation = useMutation({
    mutationFn: () => klmApi.createFeeArrangement({
      matterId: draft.matterId,
      kind: draft.kind,
      fixedValue: toNumberOrUndefined(draft.fixedValue) ?? null,
      percent: toNumberOrUndefined(draft.percent) ?? null,
      baseCalculoExito: draft.baseCalculoExito.trim() || null,
      hourlyRate: toNumberOrUndefined(draft.hourlyRate) ?? null,
      notes: draft.notes.trim() || null,
    }),
    onSuccess: () => {
      invalidateAll();
      setCreateOpen(false);
      setDraft(EMPTY_DRAFT);
      toast({ title: 'Arranjo criado', description: 'O arranjo já está ativo e disponível para gerar cobranças.' });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível criar o arranjo', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const closeArrangementMutation = useMutation({
    mutationFn: (arrangementId: string) => klmApi.closeFeeArrangement(arrangementId),
    onSuccess: () => {
      invalidateAll();
      toast({ title: 'Arranjo encerrado' });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível encerrar o arranjo', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const generateMutation = useMutation({
    mutationFn: () => {
      if (!generateTarget) throw new Error('Selecione um arranjo.');
      return klmApi.generateFeeArrangementCharge(generateTarget.id, {
        firstDueDate: generateDraft.firstDueDate || null,
        billingType: generateDraft.billingType,
        customerExternalId: generateDraft.customerExternalId.trim() || null,
        installmentCount: generateTarget.kind === 'entrada_exito' ? Number(generateDraft.installmentCount) : undefined,
      });
    },
    onSuccess: (result) => {
      invalidateAll();
      const description = result.kind === 'fixo'
        ? `Lançamento ${formatBRL(result.entry.amount)} gerado. Status da cobrança: ${result.charge.status}.`
        : result.kind === 'entrada_exito'
          ? `${result.entries.length} parcela(s) geradas.`
          : `Mensalidade programada — próxima geração em ${new Date(result.recurrence.nextRun).toLocaleDateString('pt-BR')}.`;
      setGenerateResultNotice(description);
      toast({ title: 'Cobrança gerada', description });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível gerar a cobrança', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const timeEntryMutation = useMutation({
    mutationFn: () => {
      if (!timeEntryTarget) throw new Error('Selecione um arranjo.');
      return klmApi.logFeeTimeEntry(timeEntryTarget.id, {
        userId: timeEntryDraft.userId,
        date: timeEntryDraft.date,
        minutes: Number(timeEntryDraft.minutes),
        description: timeEntryDraft.description.trim() || null,
      });
    },
    onSuccess: () => {
      invalidateAll();
      setTimeEntryTarget(null);
      setTimeEntryDraft(EMPTY_TIME_ENTRY_DRAFT);
      toast({ title: 'Horas lançadas', description: 'As horas ficam pendentes de fatura até o próximo fechamento mensal.' });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível lançar as horas', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const closeInvoiceMutation = useMutation({
    mutationFn: () => {
      if (!invoiceTarget) throw new Error('Selecione um arranjo.');
      return klmApi.closeFeeMonthlyInvoice(invoiceTarget.id, `${invoiceCompetence}-01`);
    },
    onSuccess: (entry) => {
      invalidateAll();
      setInvoiceTarget(null);
      toast({ title: 'Fatura do mês fechada', description: `${entry.title} · ${formatBRL(entry.amount)}` });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível fechar a fatura', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const registerSuccessMutation = useMutation({
    mutationFn: () => {
      if (!successTarget) throw new Error('Selecione um arranjo.');
      const baseValue = toNumberOrUndefined(successBaseValue);
      if (!baseValue) throw new Error('Informe o valor base do êxito.');
      return klmApi.registerFeeSuccessEvent(successTarget.id, { baseValue, requestedBy: null });
    },
    onSuccess: () => {
      invalidateAll();
      setSuccessTarget(null);
      setSuccessBaseValue('');
      toast({ title: 'Evento de êxito registrado', description: 'Foi para a fila de aprovação — a cobrança só nasce depois de aprovada.' });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível registrar o êxito', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const approveSuccessMutation = useMutation({
    mutationFn: (approvalId: string) => klmApi.approveFeeSuccessEvent(approvalId),
    onSuccess: (result) => {
      invalidateAll();
      toast({ title: 'Honorário de êxito aprovado', description: `Cobrança ${formatBRL(result.entry.amount)} criada — status ${result.charge.status}.` });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível aprovar', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const renegotiateMutation = useMutation({
    mutationFn: () => klmApi.renegotiateFeeOverdueEntry(renegotiateEntryId, {
      installmentCount: Number(renegotiateInstallments),
      agreedValue: Number(renegotiateValue.replace(',', '.')),
      firstDueDate: renegotiateFirstDueDate || null,
      requestedBy: null,
    }),
    onSuccess: (result) => {
      invalidateAll();
      setRenegotiateOpen(false);
      setRenegotiateEntryId('');
      setRenegotiateValue('');
      toast({ title: 'Débito renegociado', description: `${result.entries.length} nova(s) parcela(s) criadas. A cobrança anterior foi cancelada.` });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível renegociar', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const openCreateDialog = () => { setDraft(EMPTY_DRAFT); setCreateOpen(true); };
  const openGenerateDialog = (arrangement: KlmFeeArrangement) => {
    setGenerateTarget(arrangement);
    setGenerateDraft(EMPTY_GENERATE_DRAFT);
    setGenerateResultNotice(null);
  };
  const openTimeEntryDialog = (arrangement: KlmFeeArrangement) => {
    setTimeEntryTarget(arrangement);
    setTimeEntryDraft(EMPTY_TIME_ENTRY_DRAFT);
  };
  const openInvoiceDialog = (arrangement: KlmFeeArrangement) => {
    setInvoiceTarget(arrangement);
    setInvoiceCompetence(new Date().toISOString().slice(0, 7));
  };
  const openSuccessDialog = (arrangement: KlmFeeArrangement) => {
    setSuccessTarget(arrangement);
    setSuccessBaseValue('');
  };

  return (
    <div className="space-y-4">
      <KlmPanel className="p-4">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-lg font-semibold leading-6 text-foreground">Arranjos de honorários</h3>
            <p className="mt-0.5 text-xs leading-4 text-muted-foreground">
              Modelo contratual de cobrança de cada caso — fixo, entrada + êxito, mensalidade, por hora, êxito, quota litis ou híbrido.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Select value={matterFilter} onValueChange={setMatterFilter}>
              <SelectTrigger className="h-9 w-56 text-xs"><SelectValue placeholder="Todos os casos" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="todos">Todos os casos</SelectItem>
                {matterOptions.map((card) => (
                  <SelectItem key={card.id} value={card.id}>{card.name} · {card.object}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button type="button" size="sm" onClick={openCreateDialog} disabled={!canManage}>
              <Plus className="mr-2 h-3.5 w-3.5" />Novo arranjo
            </Button>
          </div>
        </div>

        <div className="mt-4 overflow-x-auto rounded-lg border border-border">
          {arrangementsQuery.isPending ? (
            <div className="flex min-h-24 items-center justify-center gap-2 p-6 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />Carregando arranjos...
            </div>
          ) : arrangementsQuery.isError ? (
            <p className="p-6 text-sm text-muted-foreground">Não foi possível carregar os arranjos de honorários.</p>
          ) : arrangements.length === 0 ? (
            <p className="p-6 text-sm text-muted-foreground">Nenhum arranjo de honorários cadastrado ainda.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Caso</TableHead>
                  <TableHead>Tipo</TableHead>
                  <TableHead>Valor/Percentual</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Ações</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {arrangements.map((arrangement) => {
                  const matter = matterById.get(arrangement.matterId);
                  const isActive = arrangement.status === 'ativo';
                  return (
                    <TableRow key={arrangement.id}>
                      <TableCell className="text-sm font-medium">{matter?.name ?? `Caso ${arrangement.matterId.slice(0, 8)}`}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">{KIND_LABEL[arrangement.kind]}</TableCell>
                      <TableCell className="text-sm tabular-nums">{arrangementValueLabel(arrangement)}</TableCell>
                      <TableCell><KlmPill tone={isActive ? 'green' : 'muted'}>{isActive ? 'Ativo' : 'Encerrado'}</KlmPill></TableCell>
                      <TableCell className="text-right">
                        <div className="flex flex-wrap justify-end gap-1.5">
                          {isActive && GENERATABLE_KINDS.includes(arrangement.kind) && (
                            <Button type="button" size="sm" variant="outline" disabled={!canManage} onClick={() => openGenerateDialog(arrangement)}>
                              <ReceiptText className="mr-1.5 h-3.5 w-3.5" />Gerar cobrança
                            </Button>
                          )}
                          {isActive && arrangement.kind === 'por_hora' && (
                            <>
                              <Button type="button" size="sm" variant="outline" disabled={!canManage} onClick={() => openTimeEntryDialog(arrangement)}>
                                <Clock className="mr-1.5 h-3.5 w-3.5" />Lançar horas
                              </Button>
                              <Button type="button" size="sm" variant="outline" disabled={!canManage} onClick={() => openInvoiceDialog(arrangement)}>
                                <CalendarClock className="mr-1.5 h-3.5 w-3.5" />Fechar fatura do mês
                              </Button>
                            </>
                          )}
                          {isActive && SUCCESS_KINDS.includes(arrangement.kind) && (
                            <Button type="button" size="sm" variant="outline" disabled={!canManage} onClick={() => openSuccessDialog(arrangement)}>
                              <HandCoins className="mr-1.5 h-3.5 w-3.5" />Registrar êxito
                            </Button>
                          )}
                          {isActive && (
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              disabled={!canManage || closeArrangementMutation.isPending}
                              onClick={() => closeArrangementMutation.mutate(arrangement.id)}
                            >
                              Encerrar
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </div>
      </KlmPanel>

      <KlmPanel className="p-4">
        <h3 className="text-lg font-semibold leading-6 text-foreground">Fila de aprovação de êxito</h3>
        <p className="mt-0.5 text-xs leading-4 text-muted-foreground">
          Todo evento de êxito registrado espera aqui — a cobrança só nasce depois que alguém com permissão financeira aprova.
        </p>
        <div className="mt-4 overflow-x-auto rounded-lg border border-border">
          {successApprovalsQuery.isPending ? (
            <div className="flex min-h-20 items-center justify-center gap-2 p-6 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />Carregando fila...
            </div>
          ) : pendingSuccessApprovals.length === 0 ? (
            <p className="p-6 text-sm text-muted-foreground">Nenhum evento de êxito aguardando aprovação.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Caso</TableHead>
                  <TableHead>Descrição</TableHead>
                  <TableHead className="text-right">Ações</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pendingSuccessApprovals.map((approval) => (
                  <TableRow key={approval.id}>
                    <TableCell className="text-sm font-medium">{approval.clientName || approval.title}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{approval.description ?? approval.title}</TableCell>
                    <TableCell className="text-right">
                      <Button
                        type="button"
                        size="sm"
                        disabled={!canManage || approveSuccessMutation.isPending}
                        onClick={() => approveSuccessMutation.mutate(approval.id)}
                      >
                        {approveSuccessMutation.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <CheckCheck className="mr-1.5 h-3.5 w-3.5" />}
                        Aprovar êxito
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </KlmPanel>

      <KlmPanel className="p-4">
        <h3 className="text-lg font-semibold leading-6 text-foreground">Renegociar débito vencido</h3>
        <p className="mt-0.5 text-xs leading-4 text-muted-foreground">
          Cancela a cobrança vencida e cria uma nova série de parcelas com o valor combinado.
        </p>
        <div className="mt-4 overflow-x-auto rounded-lg border border-border">
          {overdueEntriesQuery.isPending ? (
            <div className="flex min-h-20 items-center justify-center gap-2 p-6 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />Carregando débitos vencidos...
            </div>
          ) : overdueEntries.length === 0 ? (
            <p className="p-6 text-sm text-muted-foreground">Nenhum débito vencido no momento.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Lançamento</TableHead>
                  <TableHead className="text-right">Valor</TableHead>
                  <TableHead>Vencimento</TableHead>
                  <TableHead className="text-right">Ações</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {overdueEntries.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell className="text-sm font-medium">{entry.title}</TableCell>
                    <TableCell className="text-right text-sm tabular-nums">{formatBRL(entry.amount)}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{entry.dueDate ? new Date(entry.dueDate).toLocaleDateString('pt-BR') : '—'}</TableCell>
                    <TableCell className="text-right">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={!canManage}
                        onClick={() => {
                          setRenegotiateEntryId(entry.id);
                          setRenegotiateInstallments('2');
                          setRenegotiateValue(String(entry.amount));
                          setRenegotiateFirstDueDate('');
                          setRenegotiateOpen(true);
                        }}
                      >
                        Renegociar
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </KlmPanel>

      {/* Novo arranjo */}
      <Dialog open={createOpen} onOpenChange={(open) => { setCreateOpen(open); if (!open) setDraft(EMPTY_DRAFT); }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2"><HandCoins className="h-4 w-4 text-primary" />Novo arranjo de honorários</DialogTitle>
            <DialogDescription>Decisão humana registrada no contrato — nunca gerada por IA.</DialogDescription>
          </DialogHeader>
          <form
            className="grid gap-3 sm:grid-cols-2"
            onSubmit={(event) => { event.preventDefault(); createMutation.mutate(); }}
          >
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="fee-arrangement-matter">Caso</Label>
              <Select value={draft.matterId} onValueChange={(value) => setDraft((prev) => ({ ...prev, matterId: value }))}>
                <SelectTrigger id="fee-arrangement-matter" className="h-10 text-sm"><SelectValue placeholder="Selecione um caso" /></SelectTrigger>
                <SelectContent>
                  {matterOptions.map((card) => (
                    <SelectItem key={card.id} value={card.id}>{card.name} · {card.object}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="fee-arrangement-kind">Tipo</Label>
              <Select value={draft.kind} onValueChange={(value) => setDraft((prev) => ({ ...prev, kind: value as KlmFeeArrangementKind }))}>
                <SelectTrigger id="fee-arrangement-kind" className="h-10 text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(Object.keys(KIND_LABEL) as KlmFeeArrangementKind[]).map((kind) => (
                    <SelectItem key={kind} value={kind}>{KIND_LABEL[kind]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {(draft.kind === 'fixo' || draft.kind === 'entrada_exito' || draft.kind === 'mensalidade' || draft.kind === 'hibrido') && (
              <div className="space-y-1">
                <Label htmlFor="fee-arrangement-fixed-value">Valor fixo (R$)</Label>
                <Input id="fee-arrangement-fixed-value" inputMode="decimal" value={draft.fixedValue} onChange={(event) => setDraft((prev) => ({ ...prev, fixedValue: event.target.value }))} placeholder="Ex.: 5000" required />
              </div>
            )}
            {(draft.kind === 'exito' || draft.kind === 'quota_litis' || draft.kind === 'hibrido') && (
              <div className="space-y-1">
                <Label htmlFor="fee-arrangement-percent">Percentual sobre o êxito (%)</Label>
                <Input id="fee-arrangement-percent" inputMode="decimal" value={draft.percent} onChange={(event) => setDraft((prev) => ({ ...prev, percent: event.target.value }))} placeholder="Ex.: 15" required />
              </div>
            )}
            {draft.kind === 'por_hora' && (
              <div className="space-y-1">
                <Label htmlFor="fee-arrangement-hourly-rate">Valor da hora (R$)</Label>
                <Input id="fee-arrangement-hourly-rate" inputMode="decimal" value={draft.hourlyRate} onChange={(event) => setDraft((prev) => ({ ...prev, hourlyRate: event.target.value }))} placeholder="Ex.: 250" required />
              </div>
            )}
            {(draft.kind === 'exito' || draft.kind === 'quota_litis' || draft.kind === 'hibrido') && (
              <div className="space-y-1 sm:col-span-2">
                <Label htmlFor="fee-arrangement-base">Base de cálculo do êxito (opcional)</Label>
                <Input id="fee-arrangement-base" value={draft.baseCalculoExito} onChange={(event) => setDraft((prev) => ({ ...prev, baseCalculoExito: event.target.value }))} placeholder="Ex.: valor líquido recebido" />
              </div>
            )}
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="fee-arrangement-notes">Notas (opcional)</Label>
              <Textarea id="fee-arrangement-notes" value={draft.notes} onChange={(event) => setDraft((prev) => ({ ...prev, notes: event.target.value }))} rows={2} />
            </div>
            <DialogFooter className="sm:col-span-2">
              <Button type="button" variant="outline" onClick={() => setCreateOpen(false)}>Cancelar</Button>
              <Button type="submit" disabled={createMutation.isPending || !canManage || !draft.matterId}>
                {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Criar arranjo
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Gerar cobrança */}
      <Dialog open={Boolean(generateTarget)} onOpenChange={(open) => { if (!open) { setGenerateTarget(null); generateMutation.reset(); } }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Gerar cobrança do arranjo</DialogTitle>
            <DialogDescription>{generateTarget ? KIND_LABEL[generateTarget.kind] : ''}</DialogDescription>
          </DialogHeader>
          {!generateResultNotice ? (
            <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); generateMutation.mutate(); }}>
              {generateTarget?.kind === 'entrada_exito' && (
                <div className="space-y-1">
                  <Label htmlFor="fee-generate-installments">Número de parcelas</Label>
                  <Select value={generateDraft.installmentCount} onValueChange={(value) => setGenerateDraft((prev) => ({ ...prev, installmentCount: value }))}>
                    <SelectTrigger id="fee-generate-installments" aria-label="Número de parcelas da cobrança" className="h-10 text-sm"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {Array.from({ length: 59 }, (_, index) => index + 2).map((count) => (
                        <SelectItem key={count} value={String(count)}>{count}x</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
              <div className="space-y-1">
                <Label htmlFor="fee-generate-due-date">Primeiro vencimento (opcional)</Label>
                <Input id="fee-generate-due-date" type="date" value={generateDraft.firstDueDate} onChange={(event) => setGenerateDraft((prev) => ({ ...prev, firstDueDate: event.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="fee-generate-billing-type">Meio de cobrança</Label>
                <Select value={generateDraft.billingType} onValueChange={(value) => setGenerateDraft((prev) => ({ ...prev, billingType: value as KlmFeeBillingType }))}>
                  <SelectTrigger id="fee-generate-billing-type" className="h-10 text-sm"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(Object.keys(BILLING_TYPE_LABEL) as KlmFeeBillingType[]).map((value) => (
                      <SelectItem key={value} value={value}>{BILLING_TYPE_LABEL[value]}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setGenerateTarget(null)}>Cancelar</Button>
                <Button type="submit" disabled={generateMutation.isPending || !canManage}>
                  {generateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Gerar cobrança
                </Button>
              </DialogFooter>
            </form>
          ) : (
            <>
              <p className="text-sm leading-5 text-foreground">{generateResultNotice}</p>
              <DialogFooter><Button variant="outline" onClick={() => setGenerateTarget(null)}>Fechar</Button></DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Lançar horas */}
      <Dialog open={Boolean(timeEntryTarget)} onOpenChange={(open) => { if (!open) setTimeEntryTarget(null); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Lançar horas trabalhadas</DialogTitle>
            <DialogDescription>Fica pendente de faturamento até o próximo fechamento mensal.</DialogDescription>
          </DialogHeader>
          <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); timeEntryMutation.mutate(); }}>
            <div className="space-y-1">
              <Label htmlFor="fee-time-entry-user">Profissional</Label>
              <Select value={timeEntryDraft.userId} onValueChange={(value) => setTimeEntryDraft((prev) => ({ ...prev, userId: value }))}>
                <SelectTrigger id="fee-time-entry-user" className="h-10 text-sm"><SelectValue placeholder="Selecione" /></SelectTrigger>
                <SelectContent>
                  {teamMembers.map((member) => (
                    <SelectItem key={member.id} value={member.id}>{member.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label htmlFor="fee-time-entry-date">Data</Label>
                <Input id="fee-time-entry-date" type="date" value={timeEntryDraft.date} onChange={(event) => setTimeEntryDraft((prev) => ({ ...prev, date: event.target.value }))} required />
              </div>
              <div className="space-y-1">
                <Label htmlFor="fee-time-entry-minutes">Minutos</Label>
                <Input id="fee-time-entry-minutes" type="number" min={1} value={timeEntryDraft.minutes} onChange={(event) => setTimeEntryDraft((prev) => ({ ...prev, minutes: event.target.value }))} placeholder="Ex.: 90" required />
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="fee-time-entry-description">Descrição (opcional)</Label>
              <Textarea id="fee-time-entry-description" value={timeEntryDraft.description} onChange={(event) => setTimeEntryDraft((prev) => ({ ...prev, description: event.target.value }))} rows={2} />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setTimeEntryTarget(null)}>Cancelar</Button>
              <Button type="submit" disabled={timeEntryMutation.isPending || !canManage || !timeEntryDraft.userId}>
                {timeEntryMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Lançar horas
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Fechar fatura do mês */}
      <Dialog open={Boolean(invoiceTarget)} onOpenChange={(open) => { if (!open) setInvoiceTarget(null); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Fechar fatura do mês</DialogTitle>
            <DialogDescription>Consolida todas as horas não faturadas da competência num único lançamento — idempotente.</DialogDescription>
          </DialogHeader>
          <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); closeInvoiceMutation.mutate(); }}>
            <div className="space-y-1">
              <Label htmlFor="fee-invoice-competence">Competência</Label>
              <Input id="fee-invoice-competence" type="month" value={invoiceCompetence} onChange={(event) => setInvoiceCompetence(event.target.value)} required />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setInvoiceTarget(null)}>Cancelar</Button>
              <Button type="submit" disabled={closeInvoiceMutation.isPending || !canManage}>
                {closeInvoiceMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Fechar fatura
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Registrar êxito */}
      <Dialog open={Boolean(successTarget)} onOpenChange={(open) => { if (!open) setSuccessTarget(null); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Registrar evento de êxito</DialogTitle>
            <DialogDescription>Não cobra ainda — cria uma aprovação humana na fila. A cobrança só nasce quando o financeiro aprovar.</DialogDescription>
          </DialogHeader>
          <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); registerSuccessMutation.mutate(); }}>
            <div className="space-y-1">
              <Label htmlFor="fee-success-base-value">Valor base do êxito (R$)</Label>
              <Input id="fee-success-base-value" inputMode="decimal" value={successBaseValue} onChange={(event) => setSuccessBaseValue(event.target.value)} placeholder="Ex.: 50000" required />
            </div>
            {successTarget?.percent !== null && successTarget?.percent !== undefined && (
              <p className="text-xs text-muted-foreground">Percentual contratado: {successTarget.percent}%.</p>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setSuccessTarget(null)}>Cancelar</Button>
              <Button type="submit" disabled={registerSuccessMutation.isPending || !canManage}>
                {registerSuccessMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Enviar para aprovação
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Renegociar débito vencido */}
      <Dialog open={renegotiateOpen} onOpenChange={(open) => { setRenegotiateOpen(open); if (!open) setRenegotiateEntryId(''); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Renegociar débito vencido</DialogTitle>
            <DialogDescription>Cancela a cobrança vencida e cria uma nova série de parcelas.</DialogDescription>
          </DialogHeader>
          <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-2.5 text-xs text-amber-700 dark:text-amber-400">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            Toda renegociação fica registrada na auditoria do caso.
          </div>
          <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); renegotiateMutation.mutate(); }}>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label htmlFor="fee-renegotiate-installments">Número de parcelas</Label>
                <Input id="fee-renegotiate-installments" type="number" min={1} max={60} value={renegotiateInstallments} onChange={(event) => setRenegotiateInstallments(event.target.value)} required />
              </div>
              <div className="space-y-1">
                <Label htmlFor="fee-renegotiate-value">Valor combinado (R$)</Label>
                <Input id="fee-renegotiate-value" inputMode="decimal" value={renegotiateValue} onChange={(event) => setRenegotiateValue(event.target.value)} required />
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="fee-renegotiate-due-date">Primeiro vencimento (opcional)</Label>
              <Input id="fee-renegotiate-due-date" type="date" value={renegotiateFirstDueDate} onChange={(event) => setRenegotiateFirstDueDate(event.target.value)} />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setRenegotiateOpen(false)}>Cancelar</Button>
              <Button type="submit" disabled={renegotiateMutation.isPending || !canManage}>
                {renegotiateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Renegociar
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
