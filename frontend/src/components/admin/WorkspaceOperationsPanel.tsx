import { useEffect, useState } from 'react';
import { BookOpenCheck } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import type { AutomationStageMode, FollowUpProfile, WorkspaceOperationsSettings } from '@/types';

interface WorkspaceOperationsPanelProps {
  value: WorkspaceOperationsSettings | null;
  loading?: boolean;
  readOnly?: boolean;
  onSave: (value: WorkspaceOperationsSettings) => Promise<void>;
}

const followUpLabels: Record<FollowUpProfile, string> = {
  gentle: 'Leve',
  balanced: 'Balanceado',
  assertive: 'Intensivo',
};

const interventionLabels: Record<AutomationStageMode, string> = {
  human_only: 'Só humano',
  ai_only: 'Só IA',
  human_in_the_loop: 'IA com revisão',
  ai_with_human_approval: 'IA com aprovação',
};

export function WorkspaceOperationsPanel({ value, loading, readOnly = false, onSave }: WorkspaceOperationsPanelProps) {
  const [draft, setDraft] = useState<WorkspaceOperationsSettings | null>(value);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  const updateArea = (key: string, patch: Partial<WorkspaceOperationsSettings['areaActivations'][number]>) => {
    setDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        areaActivations: current.areaActivations.map((area) =>
          area.key === key ? { ...area, ...patch } : area,
        ),
      };
    });
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
          Carregando operação do workspace...
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <BookOpenCheck className="h-4 w-4" />
          Áreas ativas do workspace
        </CardTitle>
        <CardDescription>
          O usuário final escolhe áreas, follow-up e intervenção humana sem ver modelos, prompts ou detalhes técnicos.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {draft.areaActivations.map((area) => (
          <div key={area.key} className="rounded-lg border p-4 space-y-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-sm font-medium">{area.label}</p>
                <p className="text-xs text-muted-foreground">{area.description}</p>
              </div>
              <Switch
                checked={area.enabled}
                onCheckedChange={(checked) => updateArea(area.key, { enabled: checked })}
                disabled={readOnly}
              />
            </div>

            <div className="grid gap-4 md:grid-cols-3">
              <div className="space-y-1.5">
                <Label>Flow permitido</Label>
                <Select
                  value={area.selectedFlowTemplateId ?? '__none__'}
                  onValueChange={(value) => updateArea(area.key, { selectedFlowTemplateId: value === '__none__' ? null : value })}
                  disabled={readOnly || !area.enabled}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Selecionar flow" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Usar padrão da área</SelectItem>
                    {area.flowOptions.map((flow) => (
                      <SelectItem key={flow.id} value={flow.id}>{flow.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Follow-up</Label>
                <Select
                  value={area.followUpProfile}
                  onValueChange={(value) => updateArea(area.key, { followUpProfile: value as FollowUpProfile })}
                  disabled={readOnly || !area.enabled}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(followUpLabels).map(([value, label]) => (
                      <SelectItem key={value} value={value}>{label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Intervenção humana</Label>
                <Select
                  value={area.humanInterventionMode}
                  onValueChange={(value) => updateArea(area.key, { humanInterventionMode: value as AutomationStageMode })}
                  disabled={readOnly || !area.enabled}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(interventionLabels).map(([value, label]) => (
                      <SelectItem key={value} value={value}>{label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="rounded-md bg-muted/40 p-3">
              <p className="text-xs font-medium text-foreground">Checklist padrão</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {area.checklist.length ? area.checklist.join(' • ') : 'Sem checklist definido para esta área.'}
              </p>
            </div>
          </div>
        ))}

        <div className="flex justify-end">
          <Button onClick={handleSave} disabled={saving || readOnly}>
            {saving ? 'Salvando...' : 'Salvar operação'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
