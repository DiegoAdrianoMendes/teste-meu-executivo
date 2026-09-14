import { useEffect, useState } from 'react';
import { Loader2, Pencil } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useToast } from '@/hooks/use-toast';
import type { KlmMonitoredProcess } from '@/klm/api';
import { useUpdateMonitoredProcess } from '@/klm/api/useProcessos';
import { normalizarOab } from '@/klm/lib/oab';
import { ApiError } from '@/services/http';

/**
 * Dialog "Editar processo": só a OAB vinculada (`PATCH /processos/{id}`).
 * Cadastrou sem OAB, nunca mais tinha; agora tem. O tribunal não é editável
 * porque o worker o deriva do número CNJ a cada tick.
 */
export function EditMonitoredProcessDialog({
  process,
  open,
  onOpenChange,
  canManage,
}: {
  process: KlmMonitoredProcess | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  canManage: boolean;
}) {
  const { toast } = useToast();
  const [oab, setOab] = useState('');
  const [touched, setTouched] = useState(false);
  const updateMutation = useUpdateMonitoredProcess();

  // Abrir na linha A, fechar, abrir na B: o campo tem de ser o da B.
  useEffect(() => {
    setOab(process?.oabVinculada ?? '');
    setTouched(false);
  }, [process?.id, process?.oabVinculada]);

  const resultado = oab.trim() ? normalizarOab(oab) : null;
  const invalida = resultado !== null && !resultado.ok;
  const canonica = resultado && resultado.ok ? resultado.canonica : null;
  const semMudanca = (canonica ?? null) === (process?.oabVinculada ?? null);

  const enviar = (oabVinculada: string | null) => {
    if (!process) return;
    updateMutation.mutate(
      { processId: process.id, payload: { oabVinculada } },
      {
        onSuccess: () => {
          toast({
            title: oabVinculada ? 'OAB atualizada' : 'OAB removida',
            description: oabVinculada
              ? `${process.numeroCnj} passa a ser consultado no DJEN pela OAB ${oabVinculada}.`
              : `${process.numeroCnj} fica só com o DataJud; as intimações do DJEN não chegam.`,
          });
          onOpenChange(false);
        },
        onError: (error) => {
          const detail = error instanceof ApiError
            ? (error.detail as { message?: string } | undefined)?.message ?? error.message
            : 'Tente novamente.';
          toast({ variant: 'destructive', title: 'Não foi possível editar o processo', description: detail });
        },
      },
    );
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (invalida || !canonica) return;
    enviar(canonica);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (updateMutation.isPending) return;
        onOpenChange(next);
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Pencil className="h-4 w-4 text-primary" />Editar processo
          </DialogTitle>
          <DialogDescription className="tabular-nums">{process?.numeroCnj ?? ''}</DialogDescription>
        </DialogHeader>
        <form className="space-y-3" onSubmit={handleSubmit}>
          <div className="space-y-1">
            <Label htmlFor="editar-processo-oab">OAB vinculada (para as intimações do DJEN)</Label>
            <Input
              id="editar-processo-oab"
              value={oab}
              onChange={(event) => setOab(event.target.value)}
              onBlur={() => setTouched(true)}
              placeholder="Ex.: 123456/DF"
              maxLength={40}
              aria-invalid={touched && invalida ? true : undefined}
            />
            {touched && invalida && resultado && resultado.ok === false && (
              <p className="text-2xs text-destructive">{resultado.erro}</p>
            )}
            {canonica && canonica !== oab.trim() && (
              <p className="text-2xs text-muted-foreground">Será gravada como {canonica}.</p>
            )}
            {!oab.trim() && process?.oabVinculada && (
              <p className="text-2xs text-muted-foreground">Para tirar a OAB, use "Remover OAB".</p>
            )}
            <p className="text-2xs text-muted-foreground">
              Sem OAB, o processo é acompanhado só pelo DataJud (movimentações); as intimações do DJEN não chegam.
            </p>
          </div>
          {process?.lastError && (
            <p className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-2xs text-destructive" data-testid="editar-processo-last-error">
              Último erro: {process.lastError}
            </p>
          )}
          <DialogFooter className="gap-2 sm:justify-between">
            <Button
              type="button"
              variant="ghost"
              disabled={!canManage || updateMutation.isPending || !process?.oabVinculada}
              onClick={() => enviar(null)}
            >
              Remover OAB
            </Button>
            <div className="flex gap-2">
              <Button type="button" variant="outline" disabled={updateMutation.isPending} onClick={() => onOpenChange(false)}>Cancelar</Button>
              <Button
                type="submit"
                disabled={!canManage || updateMutation.isPending || !canonica || semMudanca}
                title={!canManage ? 'Permissão processos.manage necessária' : undefined}
              >
                {updateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Salvar OAB
              </Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default EditMonitoredProcessDialog;
