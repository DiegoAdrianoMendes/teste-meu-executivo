import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Checkbox } from '@/components/ui/checkbox';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { PERMISSION_MODULES_LABELS, PERMISSION_ACTIONS_LABELS } from '@/constants/permissions';
import type { AdminUser, PermissionModule, PermissionAction } from '@/types';

const modules = Object.keys(PERMISSION_MODULES_LABELS) as PermissionModule[];
const actions = Object.keys(PERMISSION_ACTIONS_LABELS) as PermissionAction[];

interface BotEditDialogProps {
  bot: AdminUser | null;
  open: boolean;
  onClose: () => void;
  onSave: (bot: AdminUser) => void;
}

export function BotEditDialog({ bot, open, onClose, onSave }: BotEditDialogProps) {
  const isNew = !bot;
  const [form, setForm] = useState<AdminUser>(bot ?? {
    id: `bot-${Date.now()}`, name: '', initials: 'AI', email: '', type: 'ai',
    role: '', color: 'hsl(199 89% 48%)', isActive: true, lastLogin: '-',
    roles: [], directPermissions: [], aiModel: 'GPT-4o', maxActionsPerDay: 100,
    requiresHumanApproval: true, description: '',
  });

  const update = (patch: Partial<AdminUser>) => setForm(prev => ({ ...prev, ...patch }));

  const allowedModules = new Set(form.directPermissions.map(p => p.module));
  const toggleModule = (mod: PermissionModule) => {
    if (allowedModules.has(mod)) {
      update({ directPermissions: form.directPermissions.filter(p => p.module !== mod) });
    } else {
      update({ directPermissions: [...form.directPermissions, { module: mod, action: 'view' as PermissionAction, scope: 'all' as const }] });
    }
  };

  return (
    <Dialog open={open} onOpenChange={() => onClose()}>
      <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isNew ? 'Novo Agente IA' : `Editar: ${form.name}`}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Nome</Label>
              <Input value={form.name} onChange={e => update({ name: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>Função</Label>
              <Input value={form.role} onChange={e => update({ role: e.target.value })} />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>Descrição</Label>
            <Textarea value={form.description ?? ''} onChange={e => update({ description: e.target.value })} rows={2} />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Modelo IA</Label>
              <Select value={form.aiModel ?? 'GPT-4o'} onValueChange={v => update({ aiModel: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="GPT-4o">GPT-4o</SelectItem>
                  <SelectItem value="GPT-4o-mini">GPT-4o-mini</SelectItem>
                  <SelectItem value="Claude 3.5">Claude 3.5</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Max ações/dia</Label>
              <Input type="number" value={form.maxActionsPerDay ?? 100} onChange={e => update({ maxActionsPerDay: Number(e.target.value) })} />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Switch checked={form.requiresHumanApproval ?? true} onCheckedChange={v => update({ requiresHumanApproval: v })} />
            <Label>Requer aprovação humana</Label>
          </div>
          <div className="flex items-center gap-3">
            <Switch checked={form.isActive} onCheckedChange={v => update({ isActive: v })} />
            <Label>{form.isActive ? 'Ativo' : 'Inativo'}</Label>
          </div>
          <div>
            <Label className="mb-2 block">Módulos com acesso</Label>
            <div className="grid grid-cols-2 gap-2">
              {modules.map(mod => (
                <label key={mod} className="flex items-center gap-2 text-sm cursor-pointer">
                  <Checkbox checked={allowedModules.has(mod)} onCheckedChange={() => toggleModule(mod)} />
                  {PERMISSION_MODULES_LABELS[mod]}
                </label>
              ))}
            </div>
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <Button variant="outline" onClick={onClose}>Cancelar</Button>
          <Button onClick={() => { onSave(form); onClose(); }}>Salvar</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
