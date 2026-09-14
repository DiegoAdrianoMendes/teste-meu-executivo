import { useState } from 'react';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Calendar, Clock, MessageSquare } from 'lucide-react';
import { toast } from '@/components/ui/use-toast';
import { apiFetch } from '@/services/http';

interface ScheduleMessageDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  defaultPhone?: string;
  matterId?: string;
}

export function ScheduleMessageDialog({
  open,
  onOpenChange,
  defaultPhone = '',
  matterId,
}: ScheduleMessageDialogProps) {
  const [phone, setPhone] = useState(defaultPhone);
  const [message, setMessage] = useState('');
  const [date, setDate] = useState('');
  const [time, setTime] = useState('09:00');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    if (!phone || !message || !date) {
      toast({ title: 'Preencha todos os campos obrigatórios', variant: 'destructive' });
      return;
    }
    const scheduledAt = new Date(`${date}T${time}:00`).toISOString();
    setLoading(true);
    try {
      await apiFetch('/api/v1/flows/scheduled-messages', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          contact_phone: phone,
          message,
          scheduled_at: scheduledAt,
          matter_id: matterId ?? null,
        }),
      });
      toast({ title: 'Mensagem agendada', description: `Para ${phone} em ${new Date(scheduledAt).toLocaleDateString('pt-BR')} às ${time}` });
      onOpenChange(false);
      setMessage('');
      setDate('');
    } catch {
      toast({ title: 'Erro ao agendar mensagem', variant: 'destructive' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-lg font-semibold">
            <MessageSquare className="h-4 w-4 text-primary" />
            Agendar Mensagem
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-1.5">
            <Label className="text-xs">Destinatário (WhatsApp)</Label>
            <Input
              className="h-9 text-sm"
              placeholder="+55 62 9 9999-9999"
              value={phone}
              onChange={e => setPhone(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">Mensagem</Label>
            <Textarea
              className="min-h-[90px] text-sm resize-none"
              placeholder="Olá {nome}, ..."
              value={message}
              onChange={e => setMessage(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">Variáveis: {'{nome}'}, {'{prazo}'}, {'{area}'}</p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs flex items-center gap-1"><Calendar className="h-3 w-3" /> Data</Label>
              <Input type="date" className="h-9 text-sm" value={date} onChange={e => setDate(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs flex items-center gap-1"><Clock className="h-3 w-3" /> Horário</Label>
              <Input type="time" className="h-9 text-sm" value={time} onChange={e => setTime(e.target.value)} />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" size="sm" className="text-xs h-8" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button size="sm" className="text-xs h-8 bg-brand-gradient text-white hover:opacity-90" onClick={handleSubmit} disabled={loading}>
            {loading ? 'Agendando...' : 'Agendar mensagem'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
