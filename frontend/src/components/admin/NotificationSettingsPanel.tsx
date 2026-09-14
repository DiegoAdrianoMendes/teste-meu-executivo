import { useEffect, useMemo, useState } from 'react';
import { BellRing, Mail, MessageCircle, Plus, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import type {
  NotificationSettingsProfile,
  WorkspaceNotificationRecipient,
  WorkspaceNotificationRule,
  WorkspacePlanSummary,
} from '@/types';

interface NotificationSettingsPanelProps {
  value: NotificationSettingsProfile | null;
  loading?: boolean;
  readOnly?: boolean;
  planSummary?: WorkspacePlanSummary | null;
  onSave: (value: NotificationSettingsProfile) => Promise<void>;
}

const EMPTY_RECIPIENT: WorkspaceNotificationRecipient = {
  id: '',
  name: '',
  email: '',
  phone: '',
  role: 'custom',
  userId: null,
  locked: false,
};

function formatLimit(limit?: number | null) {
  return limit == null ? 'Ilimitado' : `${limit}`;
}

export function NotificationSettingsPanel({
  value,
  loading,
  readOnly = false,
  planSummary,
  onSave,
}: NotificationSettingsPanelProps) {
  const [draft, setDraft] = useState<NotificationSettingsProfile | null>(value);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  const extraRecipients = useMemo(
    () => (draft?.recipients ?? []).filter((recipient) => recipient.role !== 'workspace_owner'),
    [draft?.recipients],
  );

  const update = <K extends keyof NotificationSettingsProfile>(key: K, nextValue: NotificationSettingsProfile[K]) => {
    setDraft((current) => (current ? { ...current, [key]: nextValue } : current));
  };

  const updateRecipient = (id: string, patch: Partial<WorkspaceNotificationRecipient>) => {
    setDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        recipients: current.recipients.map((recipient) => (
          recipient.id === id ? { ...recipient, ...patch } : recipient
        )),
      };
    });
  };

  const removeRecipient = (id: string) => {
    setDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        recipients: current.recipients.filter((recipient) => recipient.id !== id),
      };
    });
  };

  const addRecipient = () => {
    setDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        recipients: [
          ...current.recipients,
          {
            ...EMPTY_RECIPIENT,
            id: `recipient-${Date.now()}`,
          },
        ],
      };
    });
  };

  const updateRule = (key: string, patch: Partial<WorkspaceNotificationRule>) => {
    setDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        rules: current.rules.map((rule) => (rule.key === key ? { ...rule, ...patch } : rule)),
      };
    });
  };

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      await onSave({
        ...draft,
        recipients: draft.recipients
          .filter((recipient) => recipient.role === 'workspace_owner' || recipient.name.trim() || recipient.email || recipient.phone)
          .map((recipient) => ({
            ...recipient,
            name: recipient.name.trim() || 'Contato operacional',
          })),
      });
    } finally {
      setSaving(false);
    }
  };

  if (loading || !draft) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          Carregando notificações...
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <BellRing className="h-4 w-4" />
          Notificações operacionais
        </CardTitle>
        <CardDescription>
          Avise o responsável do caso e o gestor sobre contratos, reuniões e o resumo diário. O WhatsApp sai do número oficial institucional da Meu Executivo.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="grid gap-4 md:grid-cols-2">
          <label className="flex items-center justify-between rounded-xl border p-4">
            <div className="space-y-1">
              <span className="text-sm font-medium">Email operacional</span>
              <p className="text-xs text-muted-foreground">Canal principal de resumo diário, alertas com CTA e fallback institucional.</p>
            </div>
            <Switch checked={draft.emailEnabled} onCheckedChange={(checked) => update('emailEnabled', checked)} disabled={readOnly} />
          </label>
          <label className="flex items-center justify-between rounded-xl border p-4">
            <div className="space-y-1">
              <span className="text-sm font-medium">WhatsApp institucional</span>
              <p className="text-xs text-muted-foreground">Usa o número oficial da empresa para avisos rápidos de contratos e reuniões.</p>
            </div>
            <Switch checked={draft.whatsappEnabled} onCheckedChange={(checked) => update('whatsappEnabled', checked)} disabled={readOnly} />
          </label>
        </div>

        <div className="rounded-xl border p-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-medium">Quem recebe</p>
              <p className="mt-1 text-xs text-muted-foreground">
                O gestor entra por padrão e não consome limite. Os contatos abaixo contam como contatos extras do plano.
              </p>
            </div>
            <Badge variant="outline">
              {planSummary?.operationalRecipientsUsed ?? extraRecipients.length}/{formatLimit(planSummary?.maxOperationalRecipients)} extras
            </Badge>
          </div>

          <div className="mt-4 space-y-3">
            {draft.recipients.map((recipient) => (
              <div key={recipient.id} className="rounded-xl border bg-muted/20 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium">{recipient.role === 'workspace_owner' ? 'Gestor do workspace' : recipient.name || 'Contato operacional'}</p>
                    <p className="text-xs text-muted-foreground">
                      {recipient.role === 'workspace_owner'
                        ? 'Sempre incluído nos alertas operacionais.'
                        : 'Contato extra para contratos, reuniões e digest diário.'}
                    </p>
                  </div>
                  {recipient.role === 'workspace_owner' ? (
                    <Badge variant="secondary">Padrão</Badge>
                  ) : (
                    <Button variant="ghost" size="icon" disabled={readOnly} onClick={() => removeRecipient(recipient.id)}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  )}
                </div>

                <div className="mt-4 grid gap-3 md:grid-cols-3">
                  <div className="space-y-1.5">
                    <Label>Nome</Label>
                    <Input value={recipient.name ?? ''} onChange={(event) => updateRecipient(recipient.id, { name: event.target.value })} disabled={readOnly || recipient.role === 'workspace_owner'} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Email</Label>
                    <Input value={recipient.email ?? ''} onChange={(event) => updateRecipient(recipient.id, { email: event.target.value })} disabled={readOnly} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>WhatsApp</Label>
                    <Input value={recipient.phone ?? ''} onChange={(event) => updateRecipient(recipient.id, { phone: event.target.value })} placeholder="+55 11 99999-0000" disabled={readOnly} />
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-4 flex justify-end">
            <Button variant="outline" onClick={addRecipient} disabled={readOnly || (planSummary?.maxOperationalRecipients != null && extraRecipients.length >= planSummary.maxOperationalRecipients)}>
              <Plus className="mr-2 h-4 w-4" />
              Adicionar contato extra
            </Button>
          </div>
        </div>

        <div className="rounded-xl border p-4 space-y-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-medium">Eventos e canais</p>
              <p className="mt-1 text-xs text-muted-foreground">Cada evento pode ser ligado/desligado e ter canais específicos.</p>
            </div>
            <div className="w-[120px] space-y-1.5">
              <Label>Digest diário</Label>
              <Input
                type="number"
                min={0}
                max={23}
                value={draft.dailyDigestHour}
                onChange={(event) => update('dailyDigestHour', Number(event.target.value))}
                disabled={readOnly}
              />
            </div>
          </div>

          <div className="space-y-3">
            {draft.rules.map((rule) => (
              <div key={rule.key} className="rounded-xl border bg-background p-4">
                <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                  <div className="space-y-1">
                    <p className="text-sm font-medium">{rule.label}</p>
                    <p className="text-xs text-muted-foreground">{rule.description}</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-4">
                    <label className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Switch checked={rule.enabled} onCheckedChange={(checked) => updateRule(rule.key, { enabled: checked })} disabled={readOnly} />
                      Ativo
                    </label>
                    <label className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Mail className="h-3.5 w-3.5" />
                      <Switch checked={rule.emailEnabled} onCheckedChange={(checked) => updateRule(rule.key, { emailEnabled: checked })} disabled={readOnly || !rule.enabled} />
                    </label>
                    <label className="flex items-center gap-2 text-xs text-muted-foreground">
                      <MessageCircle className="h-3.5 w-3.5" />
                      <Switch checked={rule.whatsappEnabled} onCheckedChange={(checked) => updateRule(rule.key, { whatsappEnabled: checked })} disabled={readOnly || !rule.enabled} />
                    </label>
                    {rule.key === 'meetingReminder' && (
                      <div className="flex items-center gap-2">
                        <Label className="text-xs text-muted-foreground">Antecedência</Label>
                        <Input
                          className="w-24"
                          type="number"
                          min={5}
                          max={1440}
                          value={rule.reminderMinutesBefore ?? 120}
                          onChange={(event) => updateRule(rule.key, { reminderMinutesBefore: Number(event.target.value) })}
                          disabled={readOnly || !rule.enabled}
                        />
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-xl border bg-muted/20 p-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-medium">Prévia do resumo da manhã</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Sai às {String(draft.dailyDigestHour).padStart(2, '0')}:00 por email e, se ativo, também pelo WhatsApp oficial de notificações da plataforma.
              </p>
            </div>
            <Badge variant="outline">07:00 padrão</Badge>
          </div>
          <div className="mt-4 rounded-xl border bg-background p-4 text-sm text-muted-foreground">
            <p><strong className="text-foreground">Bom dia.</strong> Hoje o workspace recebe um resumo com:</p>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              <li>Pendências abertas e aprovações que exigem ação</li>
              <li>Reuniões do dia e lembretes próximos</li>
              <li>Contratos enviados e assinados recentes</li>
              <li>Riscos e insights práticos do funil</li>
            </ul>
          </div>
        </div>

        <div className="flex justify-end">
          <Button onClick={handleSave} disabled={saving || readOnly}>
            {saving ? 'Salvando...' : 'Salvar notificações'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
