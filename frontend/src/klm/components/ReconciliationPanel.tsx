import { useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Download, FileSpreadsheet, FileUp, Loader2, ShieldCheck, Upload, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { useToast } from '@/hooks/use-toast';
import {
  klmApi,
  type KlmFinanceHarmonizationDecision,
  type KlmFinanceHarmonizationMatchKind,
  type KlmPendingReconciliation,
  type KlmReconciliationTargetKind,
} from '@/klm/api';
import { formatBRL } from '@/lib/formatters';

/**
 * Painel "Conciliação" (F7) — importação de extrato OFX/CSV, lista de
 * transações importadas com sugestão de conciliação e confirmação/rejeição
 * por linha. Consome `/finance/reconciliation/*`.
 */

function suggestionTone(match: KlmPendingReconciliation['match']): { tone: 'green' | 'gold' | 'muted'; label: string } {
  if (!match.suggestedCategory) return { tone: 'muted', label: 'Sem sugestão' };
  if (match.method === 'deterministico') return { tone: 'green', label: 'Alta confiança' };
  return { tone: 'gold', label: 'Sugestão IA · confirmar' };
}

function _isoDaysAgo(days: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

function _isoToday(): string {
  return new Date().toISOString().slice(0, 10);
}

function ConfirmMatchDialog({
  pending,
  open,
  onOpenChange,
}: {
  pending: KlmPendingReconciliation | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [targetKind, setTargetKind] = useState<KlmReconciliationTargetKind>('entry');
  const [targetId, setTargetId] = useState<string>('');

  const entriesQuery = useQuery({
    queryKey: ['klm', 'finance-entries', 'reconciliation-picker'],
    queryFn: () => klmApi.financeEntries(),
    enabled: open && targetKind === 'entry',
  });
  const chargesQuery = useQuery({
    queryKey: ['klm', 'finance-charges', 'reconciliation-picker'],
    queryFn: () => klmApi.listFinanceCharges({ limit: 300 }),
    enabled: open && targetKind === 'charge',
  });

  const openEntries = useMemo(
    () => (entriesQuery.data ?? []).filter((entry) => entry.status !== 'paid' && entry.status !== 'cancelled'),
    [entriesQuery.data],
  );
  const openCharges = useMemo(
    () => (chargesQuery.data ?? []).filter((charge) => charge.status !== 'recebida'),
    [chargesQuery.data],
  );

  const confirmMutation = useMutation({
    mutationFn: () => {
      if (!pending || !targetId) throw new Error('Selecione um lançamento ou cobrança para confirmar.');
      return klmApi.reconciliationConfirmMatch(pending.match.id, {
        targetKind,
        targetId,
        category: pending.match.suggestedCategory,
        tags: pending.match.suggestedTags,
      });
    },
    onSuccess: async () => {
      toast({ title: 'Conciliação confirmada.', description: 'A baixa e a categorização foram aplicadas ao financeiro local.' });
      await queryClient.invalidateQueries({ queryKey: ['klm', 'reconciliation-pending'] });
      await queryClient.invalidateQueries({ queryKey: ['klm', 'finance'] });
      await queryClient.invalidateQueries({ queryKey: ['klm', 'finance-charges'] });
      onOpenChange(false);
    },
    onError: (error) => {
      toast({ variant: 'destructive', title: 'Não foi possível confirmar', description: error instanceof Error ? error.message : 'Tente novamente.' });
    },
  });

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!confirmMutation.isPending) { onOpenChange(next); if (!next) { setTargetKind('entry'); setTargetId(''); } } }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Confirmar conciliação</DialogTitle>
          <DialogDescription>
            Selecione o lançamento ou a cobrança do financeiro local que corresponde a esta transação bancária.
          </DialogDescription>
        </DialogHeader>
        {pending && (
          <div className="rounded-lg border bg-secondary p-3 text-sm">
            <p className="font-medium">{pending.transaction.description ?? 'Sem descrição'}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {new Date(pending.transaction.date).toLocaleDateString('pt-BR')} · <span className="tabular-nums">{formatBRL(pending.transaction.value)}</span>
            </p>
          </div>
        )}
        <div className="grid gap-3">
          <div>
            <p className="mb-1.5 text-xs font-medium text-foreground">Tipo</p>
            <Select value={targetKind} onValueChange={(value) => { setTargetKind(value as KlmReconciliationTargetKind); setTargetId(''); }}>
              <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="entry">Lançamento financeiro</SelectItem>
                <SelectItem value="charge">Cobrança</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <p className="mb-1.5 text-xs font-medium text-foreground">{targetKind === 'entry' ? 'Lançamento' : 'Cobrança'}</p>
            <Select value={targetId} onValueChange={setTargetId}>
              <SelectTrigger className="h-9 text-sm">
                <SelectValue placeholder={
                  targetKind === 'entry'
                    ? (entriesQuery.isPending ? 'Carregando lançamentos...' : 'Selecionar lançamento')
                    : (chargesQuery.isPending ? 'Carregando cobranças...' : 'Selecionar cobrança')
                } />
              </SelectTrigger>
              <SelectContent>
                {targetKind === 'entry'
                  ? openEntries.map((entry) => (
                    <SelectItem key={entry.id} value={entry.id}>{entry.title} · {formatBRL(entry.amount)}</SelectItem>
                  ))
                  : openCharges.map((charge) => (
                    <SelectItem key={charge.id} value={charge.id}>{formatBRL(charge.value)} · {charge.billingType ?? 'cobrança'}</SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={confirmMutation.isPending} onClick={() => onOpenChange(false)}>Cancelar</Button>
          <Button
            disabled={!targetId || confirmMutation.isPending}
            onClick={() => confirmMutation.mutate()}
            className="bg-[#031121] text-white hover:bg-[#031121]"
          >
            {confirmMutation.isPending ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : <Check className="mr-2 h-3.5 w-3.5" />}
            Confirmar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ReconciliationPanel({ canManage }: { canManage: boolean }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const ofxInputRef = useRef<HTMLInputElement>(null);
  const csvInputRef = useRef<HTMLInputElement>(null);
  const [confirmTarget, setConfirmTarget] = useState<KlmPendingReconciliation | null>(null);
  const [asaasDialogOpen, setAsaasDialogOpen] = useState(false);
  const [asaasStart, setAsaasStart] = useState(() => _isoDaysAgo(30));
  const [asaasFinish, setAsaasFinish] = useState(() => _isoToday());
  const [selectedMatchIds, setSelectedMatchIds] = useState<Set<string>>(new Set());

  const pendingQuery = useQuery({
    queryKey: ['klm', 'reconciliation-pending'],
    queryFn: klmApi.reconciliationPending,
  });

  const invalidateAfterImport = async () => {
    await queryClient.invalidateQueries({ queryKey: ['klm', 'reconciliation-pending'] });
    await queryClient.invalidateQueries({ queryKey: ['klm', 'finance'] });
    await queryClient.invalidateQueries({ queryKey: ['klm', 'finance-charges'] });
  };

  const importOfxMutation = useMutation({
    mutationFn: (file: File) => klmApi.reconciliationImportOfx(file),
    onSuccess: async (result) => {
      await invalidateAfterImport();
      toast({
        title: 'Extrato OFX importado.',
        description: `${result.imported} transação(ões) importada(s) · ${result.matchedDeterministic} conciliada(s) automaticamente · ${result.suggested} aguardando confirmação · ${result.duplicates} já importada(s) antes.`,
      });
    },
    onError: (error) => {
      toast({ variant: 'destructive', title: 'Não foi possível importar o OFX', description: error instanceof Error ? error.message : 'Tente novamente.' });
    },
  });

  const importCsvMutation = useMutation({
    mutationFn: (file: File) => klmApi.reconciliationImportCsv(file),
    onSuccess: async (result) => {
      await invalidateAfterImport();
      toast({
        title: 'Extrato CSV importado.',
        description: `${result.imported} transação(ões) importada(s) · ${result.matchedDeterministic} conciliada(s) automaticamente · ${result.suggested} aguardando confirmação · ${result.duplicates} já importada(s) antes.`,
      });
    },
    onError: (error) => {
      toast({ variant: 'destructive', title: 'Não foi possível importar o CSV', description: error instanceof Error ? error.message : 'Tente novamente.' });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: (matchId: string) => klmApi.reconciliationRejectMatch(matchId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['klm', 'reconciliation-pending'] });
      toast({ title: 'Sugestão rejeitada.' });
    },
    onError: (error) => {
      toast({ variant: 'destructive', title: 'Não foi possível rejeitar', description: error instanceof Error ? error.message : 'Tente novamente.' });
    },
  });

  const importAsaasMutation = useMutation({
    mutationFn: () => klmApi.reconciliationImportAsaas({ start: asaasStart, finish: asaasFinish }),
    onSuccess: async (result) => {
      await invalidateAfterImport();
      setAsaasDialogOpen(false);
      toast({
        title: 'Extrato Asaas importado.',
        description: `${result.imported} transação(ões) importada(s) · ${result.matchedDeterministic} conciliada(s) automaticamente · ${result.suggested} aguardando confirmação · ${result.duplicates} já importada(s) antes.`,
      });
    },
    onError: (error) => {
      toast({ variant: 'destructive', title: 'Não foi possível importar o extrato Asaas', description: error instanceof Error ? error.message : 'Tente novamente.' });
    },
  });

  const confirmBatchMutation = useMutation({
    mutationFn: (items: KlmPendingReconciliation[]) =>
      klmApi.reconciliationConfirmMatchesBatch(
        items.map((item) => ({
          matchId: item.match.id,
          targetKind: item.match.targetKind as KlmReconciliationTargetKind,
          targetId: item.match.targetId as string,
          category: item.match.suggestedCategory,
          tags: item.match.suggestedTags,
        })),
      ),
    onSuccess: async (matches) => {
      setSelectedMatchIds(new Set());
      await queryClient.invalidateQueries({ queryKey: ['klm', 'reconciliation-pending'] });
      await queryClient.invalidateQueries({ queryKey: ['klm', 'finance'] });
      await queryClient.invalidateQueries({ queryKey: ['klm', 'finance-charges'] });
      toast({ title: `${matches.length} conciliação(ões) confirmada(s) em lote.` });
    },
    onError: (error) => {
      toast({ variant: 'destructive', title: 'Não foi possível confirmar em lote', description: error instanceof Error ? error.message : 'Tente novamente.' });
    },
  });

  const rejectBatchMutation = useMutation({
    mutationFn: (matchIds: string[]) => klmApi.reconciliationRejectMatchesBatch(matchIds),
    onSuccess: async (matches) => {
      setSelectedMatchIds(new Set());
      await queryClient.invalidateQueries({ queryKey: ['klm', 'reconciliation-pending'] });
      toast({ title: `${matches.length} sugestão(ões) rejeitada(s) em lote.` });
    },
    onError: (error) => {
      toast({ variant: 'destructive', title: 'Não foi possível rejeitar em lote', description: error instanceof Error ? error.message : 'Tente novamente.' });
    },
  });

  const pending = pendingQuery.data ?? [];
  const selectedItems = pending.filter((item) => selectedMatchIds.has(item.match.id));
  const selectedCanConfirm = selectedItems.length > 0 && selectedItems.every((item) => item.match.targetKind && item.match.targetId);
  const allSelected = pending.length > 0 && selectedMatchIds.size === pending.length;

  function toggleSelectAll(checked: boolean) {
    setSelectedMatchIds(checked ? new Set(pending.map((item) => item.match.id)) : new Set());
  }

  function toggleSelectOne(matchId: string, checked: boolean) {
    setSelectedMatchIds((previous) => {
      const next = new Set(previous);
      if (checked) next.add(matchId);
      else next.delete(matchId);
      return next;
    });
  }

  return (
    <KlmPanel className="p-4">
      <KlmSectionTitle
        title="Conciliação bancária"
        description="Importe o extrato OFX ou CSV, confira a sugestão de cada transação e confirme ou rejeite por linha."
        action={
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={ofxInputRef}
              type="file"
              accept=".ofx"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) importOfxMutation.mutate(file);
              }}
            />
            <input
              ref={csvInputRef}
              type="file"
              accept=".csv"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) importCsvMutation.mutate(file);
              }}
            />
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              disabled={!canManage || importOfxMutation.isPending}
              title={!canManage ? 'Permissão finance.manage necessária' : undefined}
              onClick={() => ofxInputRef.current?.click()}
            >
              {importOfxMutation.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Upload className="mr-1.5 h-3.5 w-3.5" />}
              Importar OFX
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              disabled={!canManage || importCsvMutation.isPending}
              title={!canManage ? 'Permissão finance.manage necessária' : undefined}
              onClick={() => csvInputRef.current?.click()}
            >
              {importCsvMutation.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <FileSpreadsheet className="mr-1.5 h-3.5 w-3.5" />}
              Importar CSV
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              disabled={!canManage}
              title={!canManage ? 'Permissão finance.manage necessária' : undefined}
              onClick={() => setAsaasDialogOpen(true)}
            >
              <Download className="mr-1.5 h-3.5 w-3.5" />
              Importar extrato Asaas
            </Button>
          </div>
        }
      />

      {selectedMatchIds.size > 0 && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-secondary/60 p-2.5">
          <p className="text-xs text-muted-foreground">
            {selectedMatchIds.size} selecionada(s)
            {!selectedCanConfirm && ' · itens sem correspondência automática precisam ser confirmados individualmente'}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-2xs"
              disabled={!canManage || rejectBatchMutation.isPending}
              title={!canManage ? 'Permissão finance.manage necessária' : undefined}
              onClick={() => rejectBatchMutation.mutate(Array.from(selectedMatchIds))}
            >
              {rejectBatchMutation.isPending ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <X className="mr-1 h-3 w-3" />}
              Rejeitar selecionados
            </Button>
            <Button
              size="sm"
              className="h-7 bg-[#031121] text-2xs text-white hover:bg-[#031121]"
              disabled={!canManage || !selectedCanConfirm || confirmBatchMutation.isPending}
              title={!canManage ? 'Permissão finance.manage necessária' : (!selectedCanConfirm ? 'Todos os itens selecionados precisam ter correspondência automática' : undefined)}
              onClick={() => confirmBatchMutation.mutate(selectedItems)}
            >
              {confirmBatchMutation.isPending ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Check className="mr-1 h-3 w-3" />}
              Confirmar selecionados
            </Button>
          </div>
        </div>
      )}

      <Dialog open={asaasDialogOpen} onOpenChange={(next) => { if (!importAsaasMutation.isPending) setAsaasDialogOpen(next); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Importar extrato Asaas</DialogTitle>
            <DialogDescription>
              Puxa as movimentações financeiras do Asaas no período selecionado e gera sugestões de conciliação automaticamente.
            </DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <p className="mb-1.5 text-xs font-medium text-foreground">De</p>
              <input type="date" value={asaasStart} onChange={(event) => setAsaasStart(event.target.value)} className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm" />
            </div>
            <div>
              <p className="mb-1.5 text-xs font-medium text-foreground">Até</p>
              <input type="date" value={asaasFinish} onChange={(event) => setAsaasFinish(event.target.value)} className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={importAsaasMutation.isPending} onClick={() => setAsaasDialogOpen(false)}>Cancelar</Button>
            <Button
              disabled={!asaasStart || !asaasFinish || importAsaasMutation.isPending}
              onClick={() => importAsaasMutation.mutate()}
              className="bg-[#031121] text-white hover:bg-[#031121]"
            >
              {importAsaasMutation.isPending ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : <Download className="mr-2 h-3.5 w-3.5" />}
              Importar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <div className="mt-4 overflow-x-auto rounded-lg border border-border">
        {pendingQuery.isPending ? (
          <div className="flex min-h-32 items-center justify-center gap-2 p-6 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />Carregando sugestões de conciliação...
          </div>
        ) : pendingQuery.isError ? (
          <div className="p-6 text-center text-xs text-muted-foreground">Não foi possível carregar as sugestões de conciliação.</div>
        ) : pending.length === 0 ? (
          <div className="p-10 text-center text-xs text-muted-foreground">
            Nenhuma transação aguardando conferência. Importe um extrato OFX, CSV ou Asaas para começar.
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10 text-xs">
                  <Checkbox checked={allSelected} onCheckedChange={(checked) => toggleSelectAll(Boolean(checked))} aria-label="Selecionar todas" />
                </TableHead>
                <TableHead className="text-xs">Transação</TableHead>
                <TableHead className="text-xs">Sugestão</TableHead>
                <TableHead className="w-[190px] text-right text-xs">Ação</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {pending.map((item) => {
                const suggestion = suggestionTone(item.match);
                return (
                  <TableRow key={item.match.id}>
                    <TableCell className="align-top">
                      <Checkbox
                        checked={selectedMatchIds.has(item.match.id)}
                        onCheckedChange={(checked) => toggleSelectOne(item.match.id, Boolean(checked))}
                        aria-label="Selecionar transação"
                      />
                    </TableCell>
                    <TableCell className="align-top">
                      <p className="text-sm font-medium">{item.transaction.description ?? 'Sem descrição'}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {new Date(item.transaction.date).toLocaleDateString('pt-BR')} · <span className="tabular-nums">{formatBRL(item.transaction.value)}</span> · {item.transaction.source.toUpperCase()}
                      </p>
                    </TableCell>
                    <TableCell className="align-top">
                      <KlmPill tone={suggestion.tone}>{suggestion.label}</KlmPill>
                      {item.match.suggestedCategory && (
                        <p className="mt-1.5 text-xs text-muted-foreground">Categoria sugerida: {item.match.suggestedCategory}</p>
                      )}
                    </TableCell>
                    <TableCell className="align-top text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 text-2xs"
                          disabled={!canManage || rejectMutation.isPending}
                          title={!canManage ? 'Permissão finance.manage necessária' : undefined}
                          onClick={() => rejectMutation.mutate(item.match.id)}
                        >
                          <X className="mr-1 h-3 w-3" />Rejeitar
                        </Button>
                        <Button
                          size="sm"
                          className="h-7 bg-[#031121] text-2xs text-white hover:bg-[#031121]"
                          disabled={!canManage}
                          title={!canManage ? 'Permissão finance.manage necessária' : undefined}
                          onClick={() => setConfirmTarget(item)}
                        >
                          <Check className="mr-1 h-3 w-3" />Confirmar
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </div>

      <ConfirmMatchDialog pending={confirmTarget} open={Boolean(confirmTarget)} onOpenChange={(open) => !open && setConfirmTarget(null)} />
    </KlmPanel>
  );
}

/**
 * Card "Verificação completa do mês" (F14) — harmonização de qualquer
 * documento financeiro (PDF/XML/CSV) contra o financeiro local, com a
 * tabela comparativa (bate | não bate | só no documento | só no sistema)
 * para validação humana. Consome `/finance/harmonization/*`. Extensão
 * ADD-ONLY da aba Conciliação — o `ReconciliationPanel` acima não muda.
 */

function _isoMonthStart(date: Date): string {
  return new Date(Date.UTC(date.getFullYear(), date.getMonth(), 1)).toISOString().slice(0, 10);
}

function _isoMonthEnd(date: Date): string {
  return new Date(Date.UTC(date.getFullYear(), date.getMonth() + 1, 0)).toISOString().slice(0, 10);
}

function harmonizationMatchTone(kind: KlmFinanceHarmonizationMatchKind): { tone: 'green' | 'gold' | 'blue' | 'red'; label: string } {
  if (kind === 'exato') return { tone: 'green', label: 'Bate' };
  if (kind === 'aproximado') return { tone: 'gold', label: 'Não bate exato' };
  if (kind === 'so_no_documento') return { tone: 'blue', label: 'Só no documento' };
  return { tone: 'red', label: 'Só no sistema' };
}

export function FinanceHarmonizationCard({ canManage }: { canManage: boolean }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const now = useMemo(() => new Date(), []);
  const [periodStart, setPeriodStart] = useState(() => _isoMonthStart(now));
  const [periodEnd, setPeriodEnd] = useState(() => _isoMonthEnd(now));
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

  const quotaQuery = useQuery({
    queryKey: ['klm', 'finance-harmonization-quota'],
    queryFn: klmApi.financeHarmonizationQuota,
  });
  const runsQuery = useQuery({
    queryKey: ['klm', 'finance-harmonization-runs'],
    queryFn: klmApi.financeHarmonizationRuns,
  });

  const currentRunId = activeRunId ?? runsQuery.data?.[0]?.id ?? null;
  const runDetailQuery = useQuery({
    queryKey: ['klm', 'finance-harmonization-run', currentRunId],
    queryFn: () => klmApi.financeHarmonizationRun(currentRunId as string),
    enabled: Boolean(currentRunId),
  });

  const invalidateQuotaAndRuns = async () => {
    await queryClient.invalidateQueries({ queryKey: ['klm', 'finance-harmonization-quota'] });
    await queryClient.invalidateQueries({ queryKey: ['klm', 'finance-harmonization-runs'] });
  };

  const uploadMutation = useMutation({
    mutationFn: (file: File) => klmApi.financeHarmonizationCreateRun({ file, periodStart, periodEnd }),
    onSuccess: async (run) => {
      setActiveRunId(run.id);
      await invalidateQuotaAndRuns();
      await queryClient.invalidateQueries({ queryKey: ['klm', 'finance-harmonization-run', run.id] });
      toast({
        title: 'Verificação completa gerada.',
        description: run.parserWarning
          ?? `${run.totals.batem ?? 0} bate(m) · ${run.totals.naoBatem ?? 0} não bate(m) exato · ${run.totals.soNoDocumento ?? 0} só no documento · ${run.totals.soNoSistema ?? 0} só no sistema.`,
      });
    },
    onError: (error) => {
      toast({ variant: 'destructive', title: 'Não foi possível gerar a verificação', description: error instanceof Error ? error.message : 'Tente novamente.' });
    },
  });

  const decideMutation = useMutation({
    mutationFn: ({ itemId, decision }: { itemId: string; decision: KlmFinanceHarmonizationDecision }) =>
      klmApi.financeHarmonizationDecideItem(currentRunId as string, itemId, decision),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['klm', 'finance-harmonization-run', currentRunId] });
    },
    onError: (error) => {
      toast({ variant: 'destructive', title: 'Não foi possível registrar a decisão', description: error instanceof Error ? error.message : 'Tente novamente.' });
    },
  });

  const validateMutation = useMutation({
    mutationFn: () => klmApi.financeHarmonizationValidateRun(currentRunId as string),
    onSuccess: async () => {
      toast({ title: 'Verificação validada.', description: 'O run foi encerrado — nada foi aplicado automaticamente ao financeiro.' });
      await queryClient.invalidateQueries({ queryKey: ['klm', 'finance-harmonization-run', currentRunId] });
      await queryClient.invalidateQueries({ queryKey: ['klm', 'finance-harmonization-runs'] });
    },
    onError: (error) => {
      toast({ variant: 'destructive', title: 'Não foi possível validar', description: error instanceof Error ? error.message : 'Tente novamente.' });
    },
  });

  const quota = quotaQuery.data;
  const run = runDetailQuery.data;
  const items = run?.items ?? [];
  const groups: { key: KlmFinanceHarmonizationMatchKind; title: string }[] = [
    { key: 'exato', title: 'Bate' },
    { key: 'aproximado', title: 'Não bate exato' },
    { key: 'so_no_documento', title: 'Só no documento' },
    { key: 'so_no_sistema', title: 'Só no sistema' },
  ];
  const runClosed = run?.status === 'validado' || run?.status === 'rejeitado';

  return (
    <KlmPanel className="mt-4 p-4">
      <KlmSectionTitle
        title="Verificação completa do mês"
        description="Envie o extrato ou DRE (PDF, XML ou CSV) do período e confira a tabela comparativa contra o financeiro local antes de validar."
        action={
          <div className="flex flex-wrap items-center gap-2">
            {quota && (
              <KlmPill tone={quota.remaining > 0 ? 'blue' : 'muted'}>
                {quota.used}/{quota.limit} usadas este mês
              </KlmPill>
            )}
            <input type="date" value={periodStart} onChange={(event) => setPeriodStart(event.target.value)} className="h-8 rounded-md border border-input bg-background px-2 text-xs" />
            <input type="date" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} className="h-8 rounded-md border border-input bg-background px-2 text-xs" />
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.xml,.csv"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) uploadMutation.mutate(file);
              }}
            />
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              disabled={!canManage || uploadMutation.isPending || (quota ? quota.remaining <= 0 : false)}
              title={!canManage ? 'Permissão finance.manage necessária' : (quota && quota.remaining <= 0 ? `Cota esgotada — renova em ${new Date(quota.renewsAt).toLocaleDateString('pt-BR')}` : undefined)}
              onClick={() => fileInputRef.current?.click()}
            >
              {uploadMutation.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <FileUp className="mr-1.5 h-3.5 w-3.5" />}
              Enviar documento
            </Button>
          </div>
        }
      />

      {!run ? (
        <div className="mt-4 rounded-lg border border-border p-10 text-center text-xs text-muted-foreground">
          {runDetailQuery.isPending && currentRunId ? 'Carregando verificação...' : 'Nenhuma verificação completa ainda este mês. Envie um documento para começar.'}
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          {run.parserWarning && (
            <p className="rounded-md border border-amber-500/30 bg-amber-500/10 p-2.5 text-xs text-amber-700 dark:text-amber-400">{run.parserWarning}</p>
          )}
          <div className="overflow-x-auto rounded-lg border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-xs">Grupo</TableHead>
                  <TableHead className="text-xs">Linha</TableHead>
                  <TableHead className="w-[170px] text-right text-xs">Decisão</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {groups.flatMap((group) => {
                  const groupItems = items.filter((item) => item.matchKind === group.key);
                  if (groupItems.length === 0) return [];
                  const tone = harmonizationMatchTone(group.key);
                  return groupItems.map((item, index) => (
                    <TableRow key={item.id}>
                      {index === 0 && (
                        <TableCell rowSpan={groupItems.length} className="align-top">
                          <KlmPill tone={tone.tone}>{tone.label}</KlmPill>
                          <p className="mt-1 text-2xs text-muted-foreground">{groupItems.length} linha(s)</p>
                        </TableCell>
                      )}
                      <TableCell className="align-top text-sm">
                        <p className="font-medium">{item.docDescription ?? 'Sem descrição no documento'}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {item.docDate ? new Date(item.docDate).toLocaleDateString('pt-BR') : '—'} · {item.docValue != null ? <span className="tabular-nums">{formatBRL(item.docValue)}</span> : '—'}
                          {item.deltaDays ? ` · ${item.deltaDays}d de diferença` : ''}
                        </p>
                      </TableCell>
                      <TableCell className="align-top text-right">
                        {item.decision !== 'pendente' ? (
                          <KlmPill tone="muted">{item.decision}</KlmPill>
                        ) : (
                          <div className="flex justify-end gap-1.5">
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-7 text-2xs"
                              disabled={!canManage || runClosed || decideMutation.isPending}
                              onClick={() => decideMutation.mutate({ itemId: item.id, decision: 'rejeitado' })}
                            >
                              <X className="mr-1 h-3 w-3" />Rejeitar
                            </Button>
                            <Button
                              size="sm"
                              className="h-7 bg-[#031121] text-2xs text-white hover:bg-[#031121]"
                              disabled={!canManage || runClosed || decideMutation.isPending}
                              onClick={() => decideMutation.mutate({ itemId: item.id, decision: 'confirmado' })}
                            >
                              <Check className="mr-1 h-3 w-3" />Confirmar
                            </Button>
                          </div>
                        )}
                      </TableCell>
                    </TableRow>
                  ));
                })}
              </TableBody>
            </Table>
          </div>
          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">
              {run.status === 'validado' ? 'Verificação validada.' : run.status === 'rejeitado' ? 'Verificação rejeitada.' : 'Aguardando validação humana — nada foi aplicado ao financeiro.'}
            </p>
            <Button
              size="sm"
              className="h-8 bg-[#031121] text-xs text-white hover:bg-[#031121]"
              disabled={!canManage || runClosed || validateMutation.isPending}
              title={!canManage ? 'Permissão finance.manage necessária' : undefined}
              onClick={() => validateMutation.mutate()}
            >
              {validateMutation.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="mr-1.5 h-3.5 w-3.5" />}
              Validar verificação
            </Button>
          </div>
        </div>
      )}
    </KlmPanel>
  );
}
