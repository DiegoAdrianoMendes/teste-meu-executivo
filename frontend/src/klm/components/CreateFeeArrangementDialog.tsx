import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { HandCoins, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { klmApi, type KlmFeeArrangementKind } from '@/klm/api';

const KIND_LABEL: Record<KlmFeeArrangementKind, string> = {
  fixo: 'Valor fixo',
  entrada_exito: 'Entrada + êxito',
  mensalidade: 'Mensalidade',
  por_hora: 'Por hora',
  exito: 'Êxito',
  quota_litis: 'Quota litis',
  hibrido: 'Híbrido',
};

function toNumberOrUndefined(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const normalized = Number(value.replace(',', '.'));
  return Number.isFinite(normalized) ? normalized : undefined;
}

/**
 * Dialog compacto para criar um arranjo de honorários com o caso já
 * pré-selecionado — usado no Kanban comercial (Frente C) quando um card
 * chega em "Contrato"/"Pagamento" sem nenhuma estrutura financeira.
 * Consome o mesmo endpoint do painel Financeiro completo
 * (app/api/v1/endpoints/fee_arrangements.py) e invalida as mesmas chaves.
 */
export function CreateFeeArrangementDialog({
  matterId,
  matterName,
  open,
  onOpenChange,
  canManage,
}: {
  matterId: string;
  matterName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  canManage: boolean;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<KlmFeeArrangementKind>('fixo');
  const [fixedValue, setFixedValue] = useState('');
  const [percent, setPercent] = useState('');
  const [hourlyRate, setHourlyRate] = useState('');
  const [notes, setNotes] = useState('');

  const reset = () => {
    setKind('fixo');
    setFixedValue('');
    setPercent('');
    setHourlyRate('');
    setNotes('');
  };

  const createMutation = useMutation({
    mutationFn: () => klmApi.createFeeArrangement({
      matterId,
      kind,
      fixedValue: toNumberOrUndefined(fixedValue) ?? null,
      percent: toNumberOrUndefined(percent) ?? null,
      hourlyRate: toNumberOrUndefined(hourlyRate) ?? null,
      notes: notes.trim() || null,
    }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'fee-arrangements'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'finance-entries'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'finance'] });
      onOpenChange(false);
      reset();
      toast({ title: 'Arranjo criado', description: `Estrutura financeira criada para ${matterName}.` });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Não foi possível criar o arranjo',
      description: error instanceof Error ? error.message : 'Tente novamente.',
    }),
  });

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!createMutation.isPending) { onOpenChange(next); if (!next) reset(); } }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><HandCoins className="h-4 w-4 text-primary" />Criar cobrança / arranjo</DialogTitle>
          <DialogDescription>{matterName} · decisão humana registrada no contrato, nunca gerada por IA.</DialogDescription>
        </DialogHeader>
        <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); createMutation.mutate(); }}>
          <div className="space-y-1">
            <Label htmlFor="funnel-arrangement-kind">Tipo</Label>
            <Select value={kind} onValueChange={(value) => setKind(value as KlmFeeArrangementKind)}>
              <SelectTrigger id="funnel-arrangement-kind" className="h-10 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                {(Object.keys(KIND_LABEL) as KlmFeeArrangementKind[]).map((value) => (
                  <SelectItem key={value} value={value}>{KIND_LABEL[value]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {(kind === 'fixo' || kind === 'entrada_exito' || kind === 'mensalidade' || kind === 'hibrido') && (
            <div className="space-y-1">
              <Label htmlFor="funnel-arrangement-fixed-value">Valor fixo (R$)</Label>
              <Input id="funnel-arrangement-fixed-value" inputMode="decimal" value={fixedValue} onChange={(event) => setFixedValue(event.target.value)} placeholder="Ex.: 5000" required />
            </div>
          )}
          {(kind === 'exito' || kind === 'quota_litis' || kind === 'hibrido') && (
            <div className="space-y-1">
              <Label htmlFor="funnel-arrangement-percent">Percentual sobre o êxito (%)</Label>
              <Input id="funnel-arrangement-percent" inputMode="decimal" value={percent} onChange={(event) => setPercent(event.target.value)} placeholder="Ex.: 15" required />
            </div>
          )}
          {kind === 'por_hora' && (
            <div className="space-y-1">
              <Label htmlFor="funnel-arrangement-hourly-rate">Valor da hora (R$)</Label>
              <Input id="funnel-arrangement-hourly-rate" inputMode="decimal" value={hourlyRate} onChange={(event) => setHourlyRate(event.target.value)} placeholder="Ex.: 250" required />
            </div>
          )}
          <div className="space-y-1">
            <Label htmlFor="funnel-arrangement-notes">Notas (opcional)</Label>
            <Textarea id="funnel-arrangement-notes" value={notes} onChange={(event) => setNotes(event.target.value)} rows={2} />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancelar</Button>
            <Button type="submit" disabled={createMutation.isPending || !canManage}>
              {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Criar arranjo
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default CreateFeeArrangementDialog;
