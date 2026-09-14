import { useEffect, useMemo, useState } from 'react';
import { BellRing, CalendarClock, CircleDollarSign, ReceiptText, WalletCards } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { FinanceUpgradePauseNotice } from '@/components/finance/FinanceUpgradePauseNotice';
import type {
  FinanceEntry,
  FinanceOverview,
  FinanceReminder,
  FinanceSettingsProfile,
} from '@/types';

interface FinanceControlPanelProps {
  overview: FinanceOverview | null;
  settings: FinanceSettingsProfile | null;
  entries: FinanceEntry[];
  reminders: FinanceReminder[];
  loading?: boolean;
  onSaveSettings: (payload: Partial<FinanceSettingsProfile>) => Promise<void>;
  onSaveEntry: (payload: Partial<FinanceEntry> & { id?: string }) => Promise<void>;
  onUpdateReminder: (reminderId: string, payload: Partial<FinanceReminder>) => Promise<void>;
}

type EntryDraft = {
  title: string;
  amount: string;
  dueDate: string;
  description: string;
  entryType: FinanceEntry['entryType'];
  status: FinanceEntry['status'];
  notifyClient: boolean;
};

const defaultEntryDraft: EntryDraft = {
  title: '',
  amount: '',
  dueDate: '',
  description: '',
  entryType: 'receivable',
  status: 'pending',
  notifyClient: true,
};

function formatMoney(value: number) {
  return value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

export function FinanceControlPanel({
  overview,
  settings,
  entries,
  reminders,
  loading,
  onSaveSettings,
  onSaveEntry,
  onUpdateReminder,
}: FinanceControlPanelProps) {
  const [settingsDraft, setSettingsDraft] = useState<FinanceSettingsProfile | null>(settings);
  const [entryDraft, setEntryDraft] = useState<EntryDraft>(defaultEntryDraft);
  const [savingSettings, setSavingSettings] = useState(false);
  const [savingEntry, setSavingEntry] = useState(false);

  useEffect(() => {
    setSettingsDraft(settings);
  }, [settings]);

  const topEntries = useMemo(() => entries.slice(0, 8), [entries]);
  const topReminders = useMemo(() => reminders.slice(0, 8), [reminders]);

  const handleSaveSettings = async () => {
    if (!settingsDraft) return;
    setSavingSettings(true);
    try {
      await onSaveSettings(settingsDraft);
    } finally {
      setSavingSettings(false);
    }
  };

  const handleCreateEntry = async () => {
    if (!entryDraft.title || !entryDraft.amount) return;
    setSavingEntry(true);
    try {
      await onSaveEntry({
        title: entryDraft.title,
        amount: Number(entryDraft.amount),
        description: entryDraft.description || null,
        dueDate: entryDraft.dueDate ? new Date(`${entryDraft.dueDate}T12:00:00`).toISOString() : null,
        entryType: entryDraft.entryType,
        status: entryDraft.status,
        notifyClient: entryDraft.notifyClient,
        currency: 'BRL',
      });
      setEntryDraft(defaultEntryDraft);
    } finally {
      setSavingEntry(false);
    }
  };

  if (loading || !settingsDraft || !overview) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          Carregando governança financeira do workspace...
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <FinanceUpgradePauseNotice />

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {[
          { label: 'Recebíveis em aberto', value: formatMoney(overview.totalOpenReceivables), icon: WalletCards },
          { label: 'Em atraso', value: formatMoney(overview.overdueAmount), icon: CircleDollarSign },
          { label: 'Agendado', value: formatMoney(overview.scheduledAmount), icon: CalendarClock },
          { label: 'Lembretes pendentes', value: String(overview.pendingReminders), icon: BellRing },
        ].map((item) => (
          <Card key={item.label}>
            <CardContent className="flex items-center gap-3 py-5">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
                <item.icon className="h-4 w-4 text-primary" />
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-muted-foreground">{item.label}</p>
                <p className="text-2xl font-bold tabular-nums">{item.value}</p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {overview.highlights.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Leitura rápida do financeiro</CardTitle>
            <CardDescription>Resumo operacional para o gestor agir sem precisar interpretar tabelas.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-2">
            {overview.highlights.map((highlight) => (
              <div key={highlight} className="rounded-md border bg-muted/30 px-3 py-2 text-sm">
                {highlight}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-[1.1fr,0.9fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Política financeira do workspace</CardTitle>
            <CardDescription>
              Configure lembretes, avisos ao cliente e abertura da API financeira para integrações externas.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <label className="flex items-center justify-between rounded-md border p-3 md:col-span-2">
              <div className="space-y-0.5">
                <span className="text-sm font-medium">Lembretes automáticos ativos</span>
                <p className="text-xs text-muted-foreground">Dispara lembretes de vencimento e pendência sem depender do time lembrar manualmente.</p>
              </div>
              <Switch
                checked={settingsDraft.remindersEnabled}
                onCheckedChange={(checked) => setSettingsDraft((current) => current ? { ...current, remindersEnabled: checked } : current)}
              />
            </label>

            <div className="space-y-1.5">
              <Label>Dias antes do vencimento</Label>
              <Input
                type="number"
                min={0}
                value={settingsDraft.defaultReminderDaysBefore}
                onChange={(event) => setSettingsDraft((current) => current ? { ...current, defaultReminderDaysBefore: Number(event.target.value || 0) } : current)}
              />
            </div>

            <div className="space-y-1.5">
              <Label>Canal padrão de lembrete</Label>
              <Select
                value={settingsDraft.defaultReminderChannel}
                onValueChange={(value) => setSettingsDraft((current) => current ? { ...current, defaultReminderChannel: value } : current)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="whatsapp">WhatsApp</SelectItem>
                  <SelectItem value="email">Email</SelectItem>
                  <SelectItem value="interno">Aviso interno</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <Label>Sincronização externa</Label>
                <Badge variant="secondary">Em breve</Badge>
              </div>
              <Select
                value={settingsDraft.externalSyncMode}
                onValueChange={(value) => setSettingsDraft((current) => current ? { ...current, externalSyncMode: value } : current)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="manual">Manual</SelectItem>
                  <SelectItem value="assisted">Assistida</SelectItem>
                  <SelectItem value="automatic">Automática</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs leading-4 text-muted-foreground">
                A preferência fica salva localmente; nenhuma sincronização externa é iniciada nesta fase.
              </p>
            </div>

            <div className="space-y-3 md:col-span-2">
              <label className="flex items-center justify-between rounded-md border p-3">
                <div className="space-y-0.5">
                  <span className="text-sm font-medium">Notificar cliente quando fizer sentido</span>
                  <p className="text-xs text-muted-foreground">Permite lembretes de cobrança e recebimento diretamente no canal do cliente.</p>
                </div>
                <Switch
                  checked={settingsDraft.allowClientNotification}
                  onCheckedChange={(checked) => setSettingsDraft((current) => current ? { ...current, allowClientNotification: checked } : current)}
                />
              </label>

              <label className="flex items-center justify-between rounded-md border p-3">
                <div className="space-y-0.5">
                  <span className="text-sm font-medium">Abrir recebível ao fechar</span>
                  <p className="text-xs text-muted-foreground">Quando o cliente fecha, o financeiro já nasce pronto para cobrança e lembretes.</p>
                </div>
                <Switch
                  checked={settingsDraft.autoCreateReceivableOnClose}
                  onCheckedChange={(checked) => setSettingsDraft((current) => current ? { ...current, autoCreateReceivableOnClose: checked } : current)}
                />
              </label>

              <label className="flex items-center justify-between rounded-md border p-3">
                <div className="space-y-0.5">
                  <span className="text-sm font-medium">API aberta habilitada</span>
                  <p className="text-xs text-muted-foreground">Mantém o módulo pronto para integrar com outros sistemas por API própria.</p>
                </div>
                <Switch
                  checked={settingsDraft.openApiEnabled}
                  onCheckedChange={(checked) => setSettingsDraft((current) => current ? { ...current, openApiEnabled: checked } : current)}
                />
              </label>
            </div>

            <div className="md:col-span-2 flex justify-end">
              <Button onClick={handleSaveSettings} disabled={savingSettings}>
                {savingSettings ? 'Salvando política...' : 'Salvar política financeira'}
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Novo lançamento</CardTitle>
            <CardDescription>
              Use para registrar honorários, parcelas, reembolsos ou despesas com lembrete desde o primeiro momento.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="finance-entry-title">Título</Label>
              <Input id="finance-entry-title" value={entryDraft.title} onChange={(event) => setEntryDraft((current) => ({ ...current, title: event.target.value }))} />
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="finance-entry-amount">Valor</Label>
                <Input id="finance-entry-amount" type="number" min={0} value={entryDraft.amount} onChange={(event) => setEntryDraft((current) => ({ ...current, amount: event.target.value }))} />
              </div>
              <div className="space-y-1.5">
                <Label>Vencimento</Label>
                <Input type="date" value={entryDraft.dueDate} onChange={(event) => setEntryDraft((current) => ({ ...current, dueDate: event.target.value }))} />
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Tipo</Label>
                <Select value={entryDraft.entryType} onValueChange={(value) => setEntryDraft((current) => ({ ...current, entryType: value as FinanceEntry['entryType'] }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="receivable">Recebível</SelectItem>
                    <SelectItem value="expense">Despesa</SelectItem>
                    <SelectItem value="refund">Estorno</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Status</Label>
                <Select value={entryDraft.status} onValueChange={(value) => setEntryDraft((current) => ({ ...current, status: value as FinanceEntry['status'] }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="pending">Pendente</SelectItem>
                    <SelectItem value="scheduled">Agendado</SelectItem>
                    <SelectItem value="paid">Pago</SelectItem>
                    <SelectItem value="overdue">Em atraso</SelectItem>
                    <SelectItem value="cancelled">Cancelado</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>Observação</Label>
              <Textarea value={entryDraft.description} onChange={(event) => setEntryDraft((current) => ({ ...current, description: event.target.value }))} />
            </div>
            <label className="flex items-center justify-between rounded-md border p-3">
              <div className="space-y-0.5">
                <span className="text-sm font-medium">Permitir aviso ao cliente</span>
                <p className="text-xs text-muted-foreground">Usa o canal oficial do onboarding para lembrar a pendência financeira.</p>
              </div>
              <Switch checked={entryDraft.notifyClient} onCheckedChange={(checked) => setEntryDraft((current) => ({ ...current, notifyClient: checked }))} />
            </label>
            <div className="flex justify-end">
              <Button onClick={handleCreateEntry} disabled={savingEntry || !entryDraft.title || !entryDraft.amount}>
                {savingEntry ? 'Salvando lançamento...' : 'Salvar lançamento'}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.2fr,0.8fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <ReceiptText className="h-4 w-4" />
              Lançamentos mais recentes
            </CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Título</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Vencimento</TableHead>
                  <TableHead className="text-right">Valor</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {topEntries.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell>
                      <div>
                        <p className="text-sm font-medium">{entry.title}</p>
                        <p className="text-xs text-muted-foreground">{entry.provider ?? 'interno'}</p>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={entry.status === 'overdue' ? 'destructive' : entry.status === 'paid' ? 'default' : 'secondary'}>
                        {entry.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {entry.dueDate ? new Date(entry.dueDate).toLocaleDateString('pt-BR') : '—'}
                    </TableCell>
                    <TableCell className="text-right font-medium">{formatMoney(entry.amount)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <BellRing className="h-4 w-4" />
              Lembretes
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {topReminders.map((reminder) => (
              <div key={reminder.id} className="rounded-md border p-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium">{reminder.message ?? 'Lembrete financeiro'}</p>
                    <p className="text-xs text-muted-foreground">
                      {new Date(reminder.remindAt).toLocaleString('pt-BR')} • {reminder.channel}
                    </p>
                  </div>
                  <Badge variant={reminder.status === 'pending' ? 'secondary' : 'outline'}>{reminder.status}</Badge>
                </div>
                {reminder.status === 'pending' && (
                  <div className="mt-3 flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => onUpdateReminder(reminder.id, { status: 'sent', resolvedAt: new Date().toISOString() })}>
                      Marcar enviado
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => onUpdateReminder(reminder.id, { status: 'dismissed', resolvedAt: new Date().toISOString() })}>
                      Dispensar
                    </Button>
                  </div>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
