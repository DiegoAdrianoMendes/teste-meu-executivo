import { useEffect, useState } from 'react';
import { CalendarRange } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';

type CustomPeriodButtonProps = {
  value: number;
  onChange: (days: number) => void;
  presets?: number[];
  min?: number;
  max?: number;
  compact?: boolean;
  className?: string;
};

export function CustomPeriodButton({
  value,
  onChange,
  presets = [1, 7, 30, 90],
  min = 1,
  max = 3_650,
  compact = false,
  className,
}: CustomPeriodButtonProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(String(value));
  const isCustom = !presets.includes(value);

  useEffect(() => {
    if (open) setDraft(String(value));
  }, [open, value]);

  const apply = () => {
    const parsed = Math.round(Number(draft));
    if (!Number.isFinite(parsed)) return;
    onChange(Math.min(max, Math.max(min, parsed)));
    setOpen(false);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          type="button"
          variant={isCustom ? 'default' : 'outline'}
          size="sm"
          className={cn('h-9 shrink-0 gap-1.5 text-xs', compact && 'w-9 px-0', className)}
          aria-label={isCustom ? `Período personalizado de ${value} dias` : 'Escolher período personalizado'}
        >
          <CalendarRange className="h-3.5 w-3.5" />
          {!compact && <span>{isCustom ? `${value} dias` : 'Período personalizado'}</span>}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Período personalizado</DialogTitle>
          <DialogDescription>Defina o número de dias até hoje.</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="custom-lookback-days">Últimos dias</Label>
          <Input id="custom-lookback-days" type="number" min={min} max={max} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') apply(); }} />
        </div>
        <DialogFooter><Button variant="outline" onClick={() => setOpen(false)}>Cancelar</Button><Button onClick={apply}>Aplicar</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
