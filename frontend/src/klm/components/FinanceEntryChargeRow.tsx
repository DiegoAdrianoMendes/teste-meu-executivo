import { useState } from 'react';
import { CircleDollarSign, Copy } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/use-toast';
import type { KlmFinanceCharge, KlmFinanceEntry } from '@/klm/api';
import { FINANCE_CHARGE_STATUS_CLASS, FINANCE_CHARGE_STATUS_LABEL, FinanceChargeDialog } from '@/klm/components/FinanceChargeDialog';

const OPEN_ENTRY_STATUSES: KlmFinanceEntry['status'][] = ['pending', 'scheduled', 'overdue'];

/**
 * Ação de cobrança em 1 clique por lançamento (F6, spec §4.5) — some quando
 * o lançamento não está em aberto e nenhuma cobrança foi registrada, para
 * não poluir a fila de itens já pagos/cancelados.
 */
export function FinanceEntryChargeRow({
  entry,
  charge,
  canManage,
}: {
  entry: KlmFinanceEntry;
  charge: KlmFinanceCharge | undefined;
  canManage: boolean;
}) {
  const { toast } = useToast();
  const [dialogOpen, setDialogOpen] = useState(false);
  const isOpenEntry = OPEN_ENTRY_STATUSES.includes(entry.status);

  if (!isOpenEntry && !charge) return null;

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border bg-secondary/40 px-4 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        {charge ? (
          <Badge variant="outline" className={`text-2xs ${FINANCE_CHARGE_STATUS_CLASS[charge.status]}`}>
            Cobrança: {FINANCE_CHARGE_STATUS_LABEL[charge.status]}
          </Badge>
        ) : (
          <span className="text-2xs text-muted-foreground">Nenhuma cobrança registrada</span>
        )}
        {charge?.status === 'rascunho' && (
          <span className="text-2xs text-muted-foreground">Cobrança local (gateway desligado)</span>
        )}
      </div>
      <div className="flex items-center gap-2">
        {charge?.invoiceUrl && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 text-2xs"
            onClick={async (event) => {
              event.stopPropagation();
              await navigator.clipboard.writeText(charge.invoiceUrl as string);
              toast({ title: 'Link de pagamento copiado.' });
            }}
          >
            <Copy className="mr-1.5 h-3 w-3" />Copiar link
          </Button>
        )}
        {!charge && isOpenEntry && (
          <Button
            type="button"
            size="sm"
            className="h-7 bg-[#031121] text-2xs text-white hover:bg-[#031121]"
            disabled={!canManage}
            title={!canManage ? 'Permissão finance.manage necessária' : undefined}
            onClick={(event) => {
              event.stopPropagation();
              setDialogOpen(true);
            }}
          >
            <CircleDollarSign className="mr-1.5 h-3 w-3" />Cobrar
          </Button>
        )}
      </div>
      <FinanceChargeDialog entry={entry} open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}
