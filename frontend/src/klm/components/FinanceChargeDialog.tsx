import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Copy, Loader2, ShieldAlert } from 'lucide-react';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { useToast } from '@/hooks/use-toast';
import { klmApi, type KlmFinanceCharge, type KlmFinanceChargeStatus, type KlmFinanceEntry, type KlmFinanceInstallmentSeries } from '@/klm/api';
import { formatBRL } from '@/lib/formatters';

/**
 * Cobrança em 1 clique + confirmação (F2/F6) — ver
 * docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §4.5. Abre já pronto para
 * confirmar (1º clique abriu o dialog, 2º clique confirma a cobrança).
 */

export const FINANCE_CHARGE_STATUS_LABEL: Record<KlmFinanceChargeStatus, string> = {
  rascunho: 'Rascunho local',
  criada: 'Criada',
  visualizada: 'Visualizada',
  confirmada: 'Confirmada',
  recebida: 'Recebida',
  vencida: 'Vencida',
  estornada: 'Estornada',
  chargeback: 'Chargeback',
  cancelada: 'Cancelada',
};

export const FINANCE_CHARGE_STATUS_CLASS: Record<KlmFinanceChargeStatus, string> = {
  rascunho: 'border-border bg-secondary text-muted-foreground',
  criada: 'border-primary/25 bg-primary/10 text-[#0057d8] dark:text-[#8fc1ff]',
  visualizada: 'border-primary/25 bg-primary/10 text-[#0057d8] dark:text-[#8fc1ff]',
  confirmada: 'border-amber-500/30 bg-amber-500/[0.12] text-amber-800 dark:text-amber-300',
  recebida: 'border-emerald-600/20 bg-emerald-500/[0.12] text-[#0b7435] dark:text-emerald-400',
  vencida: 'border-red-700/20 bg-red-500/[0.12] text-[#a53e3c] dark:text-red-400',
  estornada: 'border-red-700/20 bg-red-500/[0.12] text-[#a53e3c] dark:text-red-400',
  chargeback: 'border-red-700/20 bg-red-500/[0.12] text-[#a53e3c] dark:text-red-400',
  cancelada: 'border-border bg-secondary text-muted-foreground',
};

export function FinanceChargeDialog({
  entry,
  open,
  onOpenChange,
}: {
  entry: KlmFinanceEntry;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<'unica' | 'parcelado'>('unica');
  const [installmentCount, setInstallmentCount] = useState('2');
  const [chargeResult, setChargeResult] = useState<KlmFinanceCharge | null>(null);
  const [seriesResult, setSeriesResult] = useState<KlmFinanceInstallmentSeries | null>(null);

  const invalidateFinance = () => {
    void queryClient.invalidateQueries({ queryKey: ['klm', 'finance-charges'] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'finance'] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'finance-reports-kpis'] });
  };

  const chargeMutation = useMutation({
    mutationFn: () => klmApi.createFinanceCharge(entry.id),
    onSuccess: (charge) => {
      setChargeResult(charge);
      invalidateFinance();
    },
  });

  const seriesMutation = useMutation({
    mutationFn: () => klmApi.createFinanceInstallmentSeries(entry.id, { installmentCount: Number(installmentCount) }),
    onSuccess: (series) => {
      setSeriesResult(series);
      invalidateFinance();
    },
  });

  useEffect(() => {
    if (open) {
      setMode('unica');
      setInstallmentCount('2');
      setChargeResult(null);
      setSeriesResult(null);
      chargeMutation.reset();
      seriesMutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, entry.id]);

  const isPending = chargeMutation.isPending || seriesMutation.isPending;
  const hasResult = Boolean(chargeResult || seriesResult);
  const errorMessage = chargeMutation.isError
    ? (chargeMutation.error instanceof Error ? chargeMutation.error.message : 'Não foi possível registrar a cobrança.')
    : seriesMutation.isError
      ? (seriesMutation.error instanceof Error ? seriesMutation.error.message : 'Não foi possível registrar o parcelamento.')
      : null;

  const handleConfirm = () => {
    if (mode === 'parcelado') seriesMutation.mutate();
    else chargeMutation.mutate();
  };

  const copyLink = async (url: string) => {
    await navigator.clipboard.writeText(url);
    toast({ title: 'Link de pagamento copiado.' });
  };

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!isPending) onOpenChange(next); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Cobrar lançamento</DialogTitle>
          <DialogDescription>
            {entry.title} · {formatBRL(entry.amount)}
            {entry.dueDate ? ` · vence ${new Date(entry.dueDate).toLocaleDateString('pt-BR')}` : ' · sem vencimento'}
          </DialogDescription>
        </DialogHeader>

        {!hasResult ? (
          <>
            <RadioGroup value={mode} onValueChange={(value) => setMode(value as 'unica' | 'parcelado')} className="gap-3">
              <div className="flex items-start gap-2 rounded-lg border p-3">
                <RadioGroupItem value="unica" id="finance-charge-mode-unica" className="mt-0.5" />
                <Label htmlFor="finance-charge-mode-unica" className="flex-1 cursor-pointer text-xs font-normal">
                  <span className="block text-xs font-semibold">Cobrança única</span>
                  <span className="mt-0.5 block text-2xs text-muted-foreground">Cria uma cobrança para o valor integral do lançamento.</span>
                </Label>
              </div>
              <div className="flex items-start gap-2 rounded-lg border p-3">
                <RadioGroupItem value="parcelado" id="finance-charge-mode-parcelado" className="mt-0.5" />
                <Label htmlFor="finance-charge-mode-parcelado" className="flex-1 cursor-pointer text-xs font-normal">
                  <span className="block text-xs font-semibold">Parcelar em N vezes</span>
                  <span className="mt-0.5 block text-2xs text-muted-foreground">Divide o lançamento em parcelas, cada uma com cobrança própria.</span>
                </Label>
              </div>
            </RadioGroup>

            {mode === 'parcelado' && (
              <div className="flex items-center gap-3 rounded-lg border bg-secondary p-3">
                <Label htmlFor="finance-installment-count" className="text-2xs text-muted-foreground">Número de parcelas</Label>
                <Select value={installmentCount} onValueChange={setInstallmentCount}>
                  <SelectTrigger id="finance-installment-count" aria-label="Quantidade de parcelas" className="h-8 w-24 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {Array.from({ length: 11 }, (_, index) => index + 2).map((count) => (
                      <SelectItem key={count} value={String(count)}>{count}x</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            {errorMessage && (
              <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-2xs text-destructive">
                <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />{errorMessage}
              </div>
            )}

            <DialogFooter>
              <Button variant="outline" disabled={isPending} onClick={() => onOpenChange(false)}>Cancelar</Button>
              <Button className="bg-[#031121] text-white hover:bg-[#031121]" disabled={isPending} onClick={handleConfirm}>
                {isPending && <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />}Confirmar cobrança
              </Button>
            </DialogFooter>
          </>
        ) : chargeResult ? (
          <>
            <div className="flex items-center justify-between rounded-lg border bg-secondary p-3">
              <span className="text-2xs text-muted-foreground">Status da cobrança</span>
              <Badge variant="outline" className={`text-2xs ${FINANCE_CHARGE_STATUS_CLASS[chargeResult.status]}`}>
                {FINANCE_CHARGE_STATUS_LABEL[chargeResult.status]}
              </Badge>
            </div>
            {chargeResult.status === 'rascunho' ? (
              <div className="rounded-lg border border-amber-300 bg-amber-50/70 p-3 text-2xs leading-4 text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-100">
                <strong className="block">Cobrança local (gateway desligado).</strong>
                {chargeResult.draftNotice}
              </div>
            ) : chargeResult.invoiceUrl ? (
              <div className="flex items-center gap-2">
                <Input readOnly value={chargeResult.invoiceUrl} className="h-9 text-xs" />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-9 shrink-0 text-2xs"
                  onClick={() => copyLink(chargeResult.invoiceUrl as string)}
                >
                  <Copy className="mr-1.5 h-3.5 w-3.5" />Copiar link
                </Button>
              </div>
            ) : (
              <p className="text-2xs text-muted-foreground">Nenhum link de pagamento disponível para esta cobrança ainda.</p>
            )}
            <DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Fechar</Button></DialogFooter>
          </>
        ) : seriesResult ? (
          <>
            <p className="text-2xs leading-4 text-muted-foreground">
              Lançamento parcelado em {seriesResult.entries.length} vezes. Cada parcela virou um lançamento próprio; cobre cada uma individualmente na fila financeira.
            </p>
            <div className="max-h-48 overflow-y-auto rounded-lg border">
              {seriesResult.entries.map((installment) => (
                <div key={installment.id} className="flex items-center justify-between border-b p-2.5 text-2xs last:border-0">
                  <span>
                    Parcela {installment.installmentIndex}/{installment.installmentCount}
                    {installment.dueDate ? ` · vence ${new Date(installment.dueDate).toLocaleDateString('pt-BR')}` : ''}
                  </span>
                  <span className="font-semibold tabular-nums">{formatBRL(installment.amount)}</span>
                </div>
              ))}
            </div>
            <DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Fechar</Button></DialogFooter>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
