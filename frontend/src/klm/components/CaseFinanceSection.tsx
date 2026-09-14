import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { HandCoins, Loader2, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useToast } from '@/hooks/use-toast';
import { FinanceEntryChargeRow } from '@/klm/components/FinanceEntryChargeRow';
import { KlmPill } from '@/klm/components/Primitives';
import {
  klmApi,
  type KlmFeeArrangement,
  type KlmFeeArrangementKind,
  type KlmFinanceCharge,
  type KlmMatterDetail,
} from '@/klm/api';
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

const SUCCESS_KINDS: KlmFeeArrangementKind[] = ['exito', 'quota_litis', 'hibrido'];

const ENTRY_STATUS_TONE: Record<string, 'green' | 'red' | 'gold' | 'muted'> = {
  paid: 'green',
  overdue: 'red',
  cancelled: 'muted',
  pending: 'gold',
  scheduled: 'gold',
};

function arrangementValueLabel(arrangement: KlmFeeArrangement): string {
  const parts: string[] = [];
  if (arrangement.fixedValue !== null) parts.push(formatBRL(arrangement.fixedValue));
  if (arrangement.percent !== null) parts.push(`${arrangement.percent}%`);
  if (arrangement.hourlyRate !== null) parts.push(`${formatBRL(arrangement.hourlyRate)}/h`);
  return parts.length > 0 ? parts.join(' · ') : '—';
}

/**
 * Seção "Financeiro do caso" do Caso 360 (Frente C) — arranjo de honorários
 * vigente, cobranças do caso com ação "Cobrar" em 1 clique (reaproveita
 * FinanceChargeDialog/FinanceEntryChargeRow) e atalho "Registrar êxito"
 * quando o arranjo vigente é de êxito/quota litis/híbrido. Consome os mesmos
 * endpoints e chaves de cache do painel Financeiro completo
 * (FeeArrangementsPanel/OperationalModulePage) — nenhuma chamada nova de
 * rede além das já usadas nesse fluxo.
 */
export function CaseFinanceSection({
  matter,
  canViewFinance,
  canManageFinance,
}: {
  matter: KlmMatterDetail;
  canViewFinance: boolean;
  canManageFinance: boolean;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [successTarget, setSuccessTarget] = useState<KlmFeeArrangement | null>(null);
  const [successBaseValue, setSuccessBaseValue] = useState('');

  const arrangementsQuery = useQuery({
    queryKey: ['klm', 'fee-arrangements', matter.id],
    queryFn: () => klmApi.feeArrangements(matter.id),
    enabled: canViewFinance,
  });
  const entriesQuery = useQuery({
    queryKey: ['klm', 'finance-entries', matter.id],
    queryFn: () => klmApi.financeEntries(matter.id),
    enabled: canViewFinance,
  });
  const chargesQuery = useQuery({
    queryKey: ['klm', 'finance-charges'],
    queryFn: () => klmApi.listFinanceCharges({ limit: 300 }),
    enabled: canViewFinance,
  });

  const chargeByEntryId = useMemo(() => {
    const map = new Map<string, KlmFinanceCharge>();
    for (const charge of chargesQuery.data ?? []) map.set(charge.entryId, charge);
    return map;
  }, [chargesQuery.data]);

  const arrangements = arrangementsQuery.data ?? [];
  const activeArrangement = arrangements.find((item) => item.status === 'ativo');
  const successArrangement = arrangements.find((item) => item.status === 'ativo' && SUCCESS_KINDS.includes(item.kind));
  const entries = entriesQuery.data ?? [];

  const registerSuccessMutation = useMutation({
    mutationFn: () => {
      if (!successTarget) throw new Error('Selecione um arranjo.');
      const baseValue = Number(successBaseValue.replace(',', '.'));
      if (!Number.isFinite(baseValue) || baseValue <= 0) throw new Error('Informe o valor base do êxito.');
      return klmApi.registerFeeSuccessEvent(successTarget.id, { baseValue, requestedBy: null });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'approvals'] });
      setSuccessTarget(null);
      setSuccessBaseValue('');
      toast({ title: 'Evento de êxito registrado', description: 'Foi para a fila de aprovação do financeiro — a cobrança só nasce depois de aprovada.' });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Não foi possível registrar o êxito',
      description: error instanceof Error ? error.message : 'Tente novamente.',
    }),
  });

  if (!canViewFinance) {
    return (
      <p className="flex items-start gap-2 text-2xs leading-4 text-muted-foreground">
        <ShieldCheck className="mt-0.5 h-3 w-3 shrink-0" />Seu perfil não possui acesso ao financeiro.
      </p>
    );
  }

  return (
    <div className="space-y-3" data-testid="case360-finance-section">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-2xs text-muted-foreground">Arranjo vigente</span>
        {arrangementsQuery.isPending ? (
          <span className="text-2xs text-muted-foreground">carregando...</span>
        ) : activeArrangement ? (
          <>
            <KlmPill tone="blue">{KIND_LABEL[activeArrangement.kind]}</KlmPill>
            <KlmPill tone="green">Ativo</KlmPill>
            <span className="text-2xs font-medium tabular-nums">{arrangementValueLabel(activeArrangement)}</span>
          </>
        ) : (
          <KlmPill tone="muted">Nenhum arranjo cadastrado</KlmPill>
        )}
        {successArrangement && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="ml-auto h-7 text-2xs"
            disabled={!canManageFinance}
            title={!canManageFinance ? 'Permissão finance.manage necessária' : undefined}
            onClick={() => { setSuccessTarget(successArrangement); setSuccessBaseValue(''); }}
          >
            <HandCoins className="mr-1.5 h-3 w-3" />Registrar êxito
          </Button>
        )}
      </div>

      <div className="divide-y divide-border rounded-md border border-border">
        {entriesQuery.isPending ? (
          <p className="p-3 text-2xs text-muted-foreground">Carregando cobranças...</p>
        ) : entries.length === 0 ? (
          <p className="p-3 text-2xs text-muted-foreground">Nenhuma cobrança registrada para este caso.</p>
        ) : entries.map((entry) => (
          <div key={entry.id}>
            <div className="flex items-center justify-between gap-2 p-2.5 text-2xs">
              <div className="min-w-0">
                <p className="truncate font-medium">{entry.title}</p>
                <p className="mt-0.5 text-muted-foreground">{entry.dueDate ? new Date(entry.dueDate).toLocaleDateString('pt-BR') : 'sem vencimento'}</p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <KlmPill tone={ENTRY_STATUS_TONE[entry.status] ?? 'muted'}>{entry.status}</KlmPill>
                <strong className="tabular-nums">{formatBRL(entry.amount)}</strong>
              </div>
            </div>
            <FinanceEntryChargeRow entry={entry} charge={chargeByEntryId.get(entry.id)} canManage={canManageFinance} />
          </div>
        ))}
      </div>

      <Dialog open={Boolean(successTarget)} onOpenChange={(open) => { if (!open) setSuccessTarget(null); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Registrar evento de êxito</DialogTitle>
            <DialogDescription>Não cobra ainda — cria uma aprovação humana na fila do financeiro. A cobrança só nasce quando o financeiro aprovar.</DialogDescription>
          </DialogHeader>
          <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); registerSuccessMutation.mutate(); }}>
            <div className="space-y-1">
              <Label htmlFor="case360-success-base-value">Valor base do êxito (R$)</Label>
              <Input id="case360-success-base-value" inputMode="decimal" value={successBaseValue} onChange={(event) => setSuccessBaseValue(event.target.value)} placeholder="Ex.: 50000" required />
            </div>
            {successTarget?.percent !== null && successTarget?.percent !== undefined && (
              <p className="text-2xs text-muted-foreground">Percentual contratado: {successTarget.percent}%.</p>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setSuccessTarget(null)}>Cancelar</Button>
              <Button type="submit" disabled={registerSuccessMutation.isPending || !canManageFinance}>
                {registerSuccessMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Enviar para aprovação
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default CaseFinanceSection;
