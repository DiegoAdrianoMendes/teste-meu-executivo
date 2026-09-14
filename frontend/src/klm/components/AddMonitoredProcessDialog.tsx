import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check, ChevronsUpDown, Landmark, Loader2 } from 'lucide-react';
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
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command';
import { useToast } from '@/hooks/use-toast';
import { klmApi } from '@/klm/api';
import { useCreateMonitoredProcess } from '@/klm/api/useProcessos';
import { cn } from '@/lib/utils';
import { maskNumeroCnj, numeroCnjValido, onlyDigits } from '@/klm/lib/cnj';
import { normalizarOab } from '@/klm/lib/oab';
import { ApiError } from '@/services/http';

/**
 * Dialog "Adicionar processo" — cadastra um número CNJ para monitoramento
 * (`POST /processos`, consome quota do add-on). O vínculo a caso é
 * opcional e usa os mesmos cards já carregados pelo funil comercial
 * (`klmApi.funnel`), sem endpoint dedicado de busca.
 */
export function AddMonitoredProcessDialog({
  open,
  onOpenChange,
  canManage,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  canManage: boolean;
}) {
  const { toast } = useToast();
  const [numeroCnj, setNumeroCnj] = useState('');
  const [oab, setOab] = useState('');
  const [matterId, setMatterId] = useState<string | null>(null);
  const [matterPickerOpen, setMatterPickerOpen] = useState(false);
  const [touched, setTouched] = useState(false);

  const funnelQuery = useQuery({ queryKey: ['klm', 'funnel'], queryFn: klmApi.funnel, enabled: open });
  const cards = funnelQuery.data?.cards ?? [];
  const selectedCard = useMemo(() => cards.find((card) => card.id === matterId) ?? null, [cards, matterId]);

  const createMutation = useCreateMonitoredProcess();

  const digits = onlyDigits(numeroCnj);
  const isComplete = digits.length === 20;
  const isValid = isComplete && numeroCnjValido(numeroCnj);
  // A OAB é opcional, mas quando vem tem de ser canônica: letra não é descartada,
  // é erro sobre o que o usuário digitou.
  const oabResultado = oab.trim() ? normalizarOab(oab) : null;
  const oabInvalida = oabResultado !== null && !oabResultado.ok;

  const reset = () => {
    setNumeroCnj('');
    setOab('');
    setMatterId(null);
    setTouched(false);
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (!isValid || oabInvalida) return;
    // Sem `tribunal`: o backend deriva do número CNJ, e o que se digitava aqui era ignorado.
    createMutation.mutate(
      {
        numeroCnj,
        oabVinculada: oabResultado && oabResultado.ok ? oabResultado.canonica : null,
        matterId: matterId ?? null,
      },
      {
        onSuccess: () => {
          toast({ title: 'Processo cadastrado', description: `${numeroCnj} entrou no monitoramento.` });
          onOpenChange(false);
          reset();
        },
        onError: (error) => {
          const detail = error instanceof ApiError
            ? (error.detail as { message?: string } | undefined)?.message ?? error.message
            : 'Tente novamente.';
          toast({ variant: 'destructive', title: 'Não foi possível cadastrar o processo', description: detail });
        },
      },
    );
  };

  const fechar = (next: boolean) => {
    if (createMutation.isPending) return;
    onOpenChange(next);
    if (!next) reset();
  };

  return (
    <Dialog open={open} onOpenChange={fechar}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Landmark className="h-4 w-4 text-primary" />Adicionar processo
          </DialogTitle>
          <DialogDescription>
            Monitoramento de intimações (DJEN) e movimentações (DataJud) — consome uma vaga da quota contratada.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-3" onSubmit={handleSubmit}>
          <div className="space-y-1">
            <Label htmlFor="processo-numero-cnj">Número CNJ</Label>
            <Input
              id="processo-numero-cnj"
              inputMode="numeric"
              autoComplete="off"
              value={numeroCnj}
              onChange={(event) => setNumeroCnj(maskNumeroCnj(event.target.value))}
              onBlur={() => setTouched(true)}
              placeholder="0000000-00.0000.0.00.0000"
              className="tabular-nums"
              required
            />
            {touched && isComplete && !isValid && (
              <p className="text-2xs text-destructive">Dígito verificador não confere — confira o número CNJ.</p>
            )}
            {touched && !isComplete && (
              <p className="text-2xs text-destructive">Número CNJ incompleto (formato NNNNNNN-DD.AAAA.J.TR.OOOO).</p>
            )}
          </div>
          <div className="space-y-1">
            <Label htmlFor="processo-oab">OAB vinculada (para as intimações do DJEN)</Label>
            <Input
              id="processo-oab"
              value={oab}
              onChange={(event) => setOab(event.target.value)}
              onBlur={() => setTouched(true)}
              placeholder="Ex.: 123456/DF"
              maxLength={40}
              aria-invalid={touched && oabInvalida ? true : undefined}
            />
            {oabResultado && oabResultado.ok && oabResultado.canonica !== oab.trim() && (
              <p className="text-2xs text-muted-foreground">Será gravada como {oabResultado.canonica}.</p>
            )}
            {touched && oabInvalida && oabResultado && oabResultado.ok === false && (
              <p className="text-2xs text-destructive">{oabResultado.erro}</p>
            )}
            <p className="text-2xs text-muted-foreground">
              Sem OAB, o processo é acompanhado só pelo DataJud (movimentações); as intimações do DJEN não chegam.
              O tribunal é identificado pelo próprio número CNJ.
            </p>
          </div>
          <div className="space-y-1">
            <Label>Vincular a um caso (opcional)</Label>
            <Popover open={matterPickerOpen} onOpenChange={setMatterPickerOpen}>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  role="combobox"
                  aria-expanded={matterPickerOpen}
                  className="w-full justify-between font-normal"
                >
                  <span className="truncate">{selectedCard ? `${selectedCard.name} · ${selectedCard.subject}` : 'Nenhum caso vinculado'}</span>
                  <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-[--radix-popover-trigger-width] p-0">
                <Command>
                  <CommandInput placeholder="Buscar caso por cliente ou assunto..." />
                  <CommandList>
                    <CommandEmpty>{funnelQuery.isPending ? 'Carregando casos...' : 'Nenhum caso encontrado.'}</CommandEmpty>
                    <CommandGroup>
                      <CommandItem value="__nenhum__" onSelect={() => { setMatterId(null); setMatterPickerOpen(false); }}>
                        <Check className={cn('mr-2 h-4 w-4', matterId === null ? 'opacity-100' : 'opacity-0')} />
                        Nenhum caso vinculado
                      </CommandItem>
                      {cards.map((card) => (
                        <CommandItem
                          key={card.id}
                          value={`${card.name} ${card.subject}`}
                          onSelect={() => { setMatterId(card.id); setMatterPickerOpen(false); }}
                        >
                          <Check className={cn('mr-2 h-4 w-4', matterId === card.id ? 'opacity-100' : 'opacity-0')} />
                          <span className="truncate">{card.name} · {card.subject}</span>
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  </CommandList>
                </Command>
              </PopoverContent>
            </Popover>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => fechar(false)}>Cancelar</Button>
            <Button type="submit" disabled={createMutation.isPending || !canManage} title={!canManage ? 'Permissão processos.manage necessária' : undefined}>
              {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Adicionar processo
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default AddMonitoredProcessDialog;
