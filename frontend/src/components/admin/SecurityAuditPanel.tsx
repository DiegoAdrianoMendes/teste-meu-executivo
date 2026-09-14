import { useEffect, useState } from 'react';
import { Shield } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import type { SecurityAuditSettingsProfile } from '@/types';

interface SecurityAuditPanelProps {
  value: SecurityAuditSettingsProfile | null;
  loading?: boolean;
  readOnly?: boolean;
  onSave: (value: SecurityAuditSettingsProfile) => Promise<void>;
}

export function SecurityAuditPanel({ value, loading, readOnly = false, onSave }: SecurityAuditPanelProps) {
  const [draft, setDraft] = useState<SecurityAuditSettingsProfile | null>(value);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  const update = <K extends keyof SecurityAuditSettingsProfile>(key: K, nextValue: SecurityAuditSettingsProfile[K]) => {
    setDraft((current) => (current ? { ...current, [key]: nextValue } : current));
  };

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      await onSave(draft);
    } finally {
      setSaving(false);
    }
  };

  if (loading || !draft) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          Carregando segurança e auditoria...
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <Shield className="h-4 w-4" />
          Segurança & auditoria
        </CardTitle>
        <CardDescription>
          Políticas de sessão, revisão e retenção para o MVP, junto com a trilha operacional do sistema.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1.5">
            <Label>Timeout de sessão (min)</Label>
            <Input
              type="number"
              min={5}
              value={String(draft.sessionTimeoutMinutes)}
              onChange={(event) => update('sessionTimeoutMinutes', Number(event.target.value))}
              disabled={readOnly}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Retenção de auditoria (dias)</Label>
            <Input
              type="number"
              min={7}
              value={String(draft.auditRetentionDays)}
              onChange={(event) => update('auditRetentionDays', Number(event.target.value))}
              disabled={readOnly}
            />
          </div>
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <label className="flex items-center justify-between rounded-lg border p-3">
            <div className="space-y-0.5">
              <span className="text-sm font-medium">Revisão humana em risco alto</span>
              <p className="text-xs text-muted-foreground">Mantém o humano no loop para saídas sensíveis.</p>
            </div>
            <Switch
              checked={draft.requireHumanReviewForHighRisk}
              onCheckedChange={(checked) => update('requireHumanReviewForHighRisk', checked)}
              disabled={readOnly}
            />
          </label>
          <label className="flex items-center justify-between rounded-lg border p-3">
            <div className="space-y-0.5">
              <span className="text-sm font-medium">Reforçar revisão de novos prompts</span>
              <p className="text-xs text-muted-foreground">Evita mudanças arriscadas em prompt sem supervisão.</p>
            </div>
            <Switch
              checked={draft.requireStrongerReviewForNewPrompts}
              onCheckedChange={(checked) => update('requireStrongerReviewForNewPrompts', checked)}
              disabled={readOnly}
            />
          </label>
          <label className="flex items-center justify-between rounded-lg border p-3 md:col-span-2">
            <div className="space-y-0.5">
              <span className="text-sm font-medium">API aberta habilitada</span>
              <p className="text-xs text-muted-foreground">Controla a superfície aberta e integrações externas do workspace.</p>
            </div>
            <Switch
              checked={draft.openApiEnabled}
              onCheckedChange={(checked) => update('openApiEnabled', checked)}
              disabled={readOnly}
            />
          </label>
        </div>

        <div className="flex justify-end">
          <Button onClick={handleSave} disabled={saving || readOnly}>
            {saving ? 'Salvando...' : 'Salvar segurança'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
