/**
 * AddMatterDialog
 * Dialog para criar um novo lead/caso no CRM.
 * Fluxo: preenche dados do cliente → cria Cliente → cria Matter vinculado.
 */
import { useState } from 'react';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Loader2 } from 'lucide-react';
import { toast } from '@/components/ui/use-toast';
import { apiFetch } from '@/services/http';
import type { KanbanColumnId } from '@/types';

const COLUMNS: { id: KanbanColumnId; label: string }[] = [
  { id: 'entrada',              label: 'Entrada' },
  { id: 'qualificacao',         label: 'Qualificação' },
  { id: 'proposta_fechamento',  label: 'Proposta / Fechamento' },
  { id: 'coleta_dados',         label: 'Coleta de Dados' },
  { id: 'triagem_juridica',     label: 'Triagem Jurídica' },
  { id: 'pesquisa_validacao',   label: 'Pesquisa / Validação' },
  { id: 'redacao_peca',         label: 'Redação da Peça' },
  { id: 'aprovacao_pendente',   label: 'Aprovação Pendente' },
  { id: 'pronto_protocolo',     label: 'Pronto para Protocolo' },
  { id: 'migrar_sistema',       label: 'Migrar do Sistema Anterior' },
];

const LEAD_SOURCES = [
  'whatsapp', 'instagram', 'facebook', 'indicacao', 'google', 'site', 'ligacao', 'outro',
];

interface Props {
  open: boolean;
  onClose: () => void;
  defaultColumnId?: KanbanColumnId;
  onCreated?: (matter: { id: string; clientName: string }) => void;
}

interface NewClientData {
  name: string;
  phone: string;
  email: string;
  cpf: string;
  employer: string;
}

export function AddMatterDialog({ open, onClose, defaultColumnId = 'entrada', onCreated }: Props) {
  const [step, setStep] = useState<'client' | 'matter'>('client');
  const [loading, setLoading] = useState(false);

  // Dados do cliente
  const [client, setClient] = useState<NewClientData>({
    name: '', phone: '', email: '', cpf: '', employer: '',
  });
  const [clientId, setClientId] = useState<string | null>(null);

  // Dados do matter
  const [demandType, setDemandType] = useState('');
  const [columnId, setColumnId] = useState<KanbanColumnId>(defaultColumnId);
  const [leadSource, setLeadSource] = useState('whatsapp');
  const [summary, setSummary] = useState('');

  const resetForm = () => {
    setStep('client');
    setClient({ name: '', phone: '', email: '', cpf: '', employer: '' });
    setClientId(null);
    setDemandType('');
    setColumnId(defaultColumnId);
    setLeadSource('whatsapp');
    setSummary('');
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  const handleCreateClient = async () => {
    if (!client.name.trim()) {
      toast({ variant: 'destructive', title: 'Nome do cliente é obrigatório' });
      return;
    }
    setLoading(true);
    try {
      // Tentar buscar cliente existente por telefone/CPF antes de criar
      const created = await apiFetch<{ id: string }>('/api/v1/clients', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: client.name.trim(),
          phone: client.phone.trim() || undefined,
          email: client.email.trim() || undefined,
          cpf: client.cpf.trim() || undefined,
          employer: client.employer.trim() || undefined,
        }),
      });
      setClientId(created.id);
      setStep('matter');
    } catch (e: unknown) {
      const msg = (e as { message?: string })?.message ?? 'Erro ao criar cliente';
      // Se CPF duplicado, tentar buscar o existente
      if (msg.includes('duplicate') || msg.includes('uq_clients')) {
        const list = await apiFetch<{ id: string; name: string }[]>(
          `/api/v1/clients?search=${encodeURIComponent(client.name)}`
        );
        if (list.length > 0) {
          setClientId(list[0].id);
          setStep('matter');
          return;
        }
      }
      toast({ variant: 'destructive', title: 'Erro ao criar cliente', description: msg });
    } finally {
      setLoading(false);
    }
  };

  const handleCreateMatter = async () => {
    if (!demandType.trim()) {
      toast({ variant: 'destructive', title: 'Tipo de demanda é obrigatório' });
      return;
    }
    if (!clientId) return;
    setLoading(true);
    try {
      const matter = await apiFetch<{ id: string }>('/api/v1/matters', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          clientId,
          demand_type: demandType.trim(),
          column_id: columnId,
          lead_source: leadSource,
          executive_summary: summary.trim() || undefined,
        }),
      });
      toast({ title: '✅ Caso criado!', description: `Lead "${client.name}" adicionado ao CRM.` });
      onCreated?.({ id: matter.id, clientName: client.name });
      handleClose();
    } catch (e: unknown) {
      toast({
        variant: 'destructive',
        title: 'Erro ao criar caso',
        description: (e as { message?: string })?.message ?? 'Tente novamente.',
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && handleClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-lg font-semibold">
            {step === 'client' ? '👤 Dados do Cliente' : '📋 Dados do Caso'}
          </DialogTitle>
        </DialogHeader>

        {/* Step indicator */}
        <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
          <span className={step === 'client' ? 'text-primary font-medium' : ''}>1. Cliente</span>
          <span>→</span>
          <span className={step === 'matter' ? 'text-primary font-medium' : ''}>2. Caso</span>
        </div>

        {step === 'client' && (
          <div className="space-y-3">
            <div>
              <Label className="text-xs">Nome completo *</Label>
              <Input
                className="h-8 text-sm mt-1"
                placeholder="Ex: João da Silva"
                value={client.name}
                onChange={(e) => setClient(p => ({ ...p, name: e.target.value }))}
                onKeyDown={(e) => e.key === 'Enter' && handleCreateClient()}
              />
            </div>
            <div>
              <Label className="text-xs">Telefone (WhatsApp)</Label>
              <Input
                className="h-8 text-sm mt-1"
                placeholder="+55 11 99999-0000"
                value={client.phone}
                onChange={(e) => setClient(p => ({ ...p, phone: e.target.value }))}
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <Label className="text-xs">E-mail</Label>
                <Input className="h-8 text-sm mt-1" placeholder="joao@email.com" value={client.email} onChange={(e) => setClient(p => ({ ...p, email: e.target.value }))} />
              </div>
              <div>
                <Label className="text-xs">CPF</Label>
                <Input className="h-8 text-sm mt-1" placeholder="000.000.000-00" value={client.cpf} onChange={(e) => setClient(p => ({ ...p, cpf: e.target.value }))} />
              </div>
            </div>
            <div>
              <Label className="text-xs">Empregador / Empresa</Label>
              <Input className="h-8 text-sm mt-1" placeholder="Ex: Empresa ABC Ltda" value={client.employer} onChange={(e) => setClient(p => ({ ...p, employer: e.target.value }))} />
            </div>
          </div>
        )}

        {step === 'matter' && (
          <div className="space-y-3">
            <div>
              <Label className="text-xs">Tipo de demanda *</Label>
              <Input
                className="h-8 text-sm mt-1"
                placeholder="Ex: Rescisão indireta, BPC/LOAS, Plano de Saúde..."
                value={demandType}
                onChange={(e) => setDemandType(e.target.value)}
                autoFocus
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <Label className="text-xs">Coluna inicial</Label>
                <Select value={columnId} onValueChange={(v) => setColumnId(v as KanbanColumnId)}>
                  <SelectTrigger className="h-8 text-xs mt-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {COLUMNS.map(c => (
                      <SelectItem key={c.id} value={c.id} className="text-xs">{c.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-xs">Origem do lead</Label>
                <Select value={leadSource} onValueChange={setLeadSource}>
                  <SelectTrigger className="h-8 text-xs mt-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {LEAD_SOURCES.map(s => (
                      <SelectItem key={s} value={s} className="text-xs capitalize">{s}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <Label className="text-xs">Resumo inicial (opcional)</Label>
              <Textarea
                className="text-sm mt-1 resize-none"
                rows={3}
                placeholder="Descreva brevemente a situação do cliente..."
                value={summary}
                onChange={(e) => setSummary(e.target.value)}
              />
            </div>
          </div>
        )}

        <DialogFooter className="gap-2 mt-2">
          {step === 'matter' && (
            <Button variant="outline" size="sm" className="text-xs h-8" onClick={() => setStep('client')}>
              Voltar
            </Button>
          )}
          <Button variant="ghost" size="sm" className="text-xs h-8" onClick={handleClose}>
            Cancelar
          </Button>
          <Button
            size="sm"
            className="text-xs h-8 bg-brand-gradient text-white hover:opacity-90"
            onClick={step === 'client' ? handleCreateClient : handleCreateMatter}
            disabled={loading || (step === 'client' ? !client.name.trim() : !demandType.trim())}
          >
            {loading && <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />}
            {step === 'client' ? 'Próximo →' : 'Criar caso'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
