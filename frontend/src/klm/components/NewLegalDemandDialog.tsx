import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check, ChevronsUpDown, Loader2, Scale } from 'lucide-react';
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command';
import { useToast } from '@/hooks/use-toast';
import { klmApi, type KlmLegalDemandNature } from '@/klm/api';
import { useCreateLegalDemand } from '@/klm/api/useLegalDemands';
import { LEGAL_DEMAND_NATURES, LEGAL_DEMAND_NATURE_LABELS } from '@/klm/lib/legal-demand-labels';
import { cn } from '@/lib/utils';
import { ApiError } from '@/services/http';

/**
 * Dialog "Nova demanda" — cria a camada jurídica canônica
 * (`POST /legal/demands`, Onda 2). O cliente é escolhido pelos mesmos
 * cards já carregados pelo funil comercial (`klmApi.funnel`), sem endpoint
 * dedicado de busca de clientes — mesmo racional de `AddMonitoredProcessDialog`.
 */
export function NewLegalDemandDialog({
  open,
  onOpenChange,
  canManage,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  canManage: boolean;
  onCreated?: (demandId: string) => void;
}) {
  const { toast } = useToast();
  const [nature, setNature] = useState<KlmLegalDemandNature | ''>('');
  const [area, setArea] = useState('');
  const [clientId, setClientId] = useState<string | null>(null);
  const [clientName, setClientName] = useState<string | null>(null);
  const [clientPickerOpen, setClientPickerOpen] = useState(false);
  const [touched, setTouched] = useState(false);

  const funnelQuery = useQuery({ queryKey: ['klm', 'funnel'], queryFn: klmApi.funnel, enabled: open });
  const clients = useMemo(() => {
    const seen = new Map<string, string>();
    for (const card of funnelQuery.data?.cards ?? []) {
      if (!seen.has(card.clientId)) seen.set(card.clientId, card.name);
    }
    return Array.from(seen.entries()).map(([id, name]) => ({ id, name }));
  }, [funnelQuery.data]);

  const createMutation = useCreateLegalDemand();

  const isValid = nature !== '' && clientId !== null;

  const reset = () => {
    setNature('');
    setArea('');
    setClientId(null);
    setClientName(null);
    setTouched(false);
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (!isValid) return;
    createMutation.mutate(
      {
        nature,
        area: area.trim() || null,
        clientId,
      },
      {
        onSuccess: (demand) => {
          toast({ title: 'Demanda criada', description: `${LEGAL_DEMAND_NATURE_LABELS[demand.nature]} · ${clientName ?? 'cliente selecionado'}.` });
          onOpenChange(false);
          onCreated?.(demand.id);
          reset();
        },
        onError: (error) => {
          const detail = error instanceof ApiError
            ? (error.detail as { message?: string } | undefined)?.message ?? error.message
            : 'Tente novamente.';
          toast({ variant: 'destructive', title: 'Não foi possível criar a demanda', description: detail });
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (createMutation.isPending) return;
        onOpenChange(next);
        if (!next) reset();
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Scale className="h-4 w-4 text-primary" />Nova demanda
          </DialogTitle>
          <DialogDescription>
            Cria a camada jurídica canônica da demanda — natureza, área e cliente. Os demais campos
            (partes, estratégia, prazos) são preenchidos no detalhe.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-3" onSubmit={handleSubmit}>
          <div className="space-y-1">
            <Label htmlFor="demanda-natureza">Natureza</Label>
            <Select value={nature} onValueChange={(value) => setNature(value as KlmLegalDemandNature)}>
              <SelectTrigger id="demanda-natureza">
                <SelectValue placeholder="Selecione a natureza" />
              </SelectTrigger>
              <SelectContent>
                {LEGAL_DEMAND_NATURES.map((value) => (
                  <SelectItem key={value} value={value}>{LEGAL_DEMAND_NATURE_LABELS[value]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            {touched && nature === '' && (
              <p className="text-2xs text-destructive">Selecione a natureza da demanda.</p>
            )}
          </div>
          <div className="space-y-1">
            <Label htmlFor="demanda-area">Área (opcional)</Label>
            <Input
              id="demanda-area"
              value={area}
              onChange={(event) => setArea(event.target.value)}
              placeholder="Ex.: Cível, Trabalhista, Societário..."
              maxLength={120}
            />
          </div>
          <div className="space-y-1">
            <Label>Cliente</Label>
            <Popover open={clientPickerOpen} onOpenChange={setClientPickerOpen}>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  role="combobox"
                  aria-expanded={clientPickerOpen}
                  className="w-full justify-between font-normal"
                >
                  <span className="truncate">{clientName ?? 'Selecione o cliente'}</span>
                  <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-[--radix-popover-trigger-width] p-0">
                <Command>
                  <CommandInput placeholder="Buscar cliente..." />
                  <CommandList>
                    <CommandEmpty>{funnelQuery.isPending ? 'Carregando clientes...' : 'Nenhum cliente encontrado.'}</CommandEmpty>
                    <CommandGroup>
                      {clients.map((client) => (
                        <CommandItem
                          key={client.id}
                          value={client.name}
                          onSelect={() => {
                            setClientId(client.id);
                            setClientName(client.name);
                            setClientPickerOpen(false);
                          }}
                        >
                          <Check className={cn('mr-2 h-4 w-4', clientId === client.id ? 'opacity-100' : 'opacity-0')} />
                          <span className="truncate">{client.name}</span>
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  </CommandList>
                </Command>
              </PopoverContent>
            </Popover>
            {touched && clientId === null && (
              <p className="text-2xs text-destructive">Selecione o cliente da demanda.</p>
            )}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancelar</Button>
            <Button type="submit" disabled={createMutation.isPending || !canManage} title={!canManage ? 'Permissão legal.manage necessária' : undefined}>
              {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Criar demanda
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default NewLegalDemandDialog;
