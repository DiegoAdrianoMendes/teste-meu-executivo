import { useEffect, useMemo, useState } from 'react';
import { Bot, Brain, Cpu, Sparkles } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import type { AiGovernanceSettings } from '@/types';

interface AiGovernancePanelProps {
  value: AiGovernanceSettings | null;
  loading?: boolean;
  readOnly?: boolean;
  onSave: (value: AiGovernanceSettings) => Promise<void>;
}

export function AiGovernancePanel({ value, loading, readOnly = false, onSave }: AiGovernancePanelProps) {
  const [draft, setDraft] = useState<AiGovernanceSettings | null>(value);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  const groupedModels = useMemo(() => {
    const byTier = new Map<string, AiGovernanceSettings['availableModels']>();
    (draft?.availableModels ?? []).forEach((model) => {
      byTier.set(model.tier, [...(byTier.get(model.tier) ?? []), model]);
    });
    return byTier;
  }, [draft?.availableModels]);

  const update = <K extends keyof AiGovernanceSettings>(key: K, nextValue: AiGovernanceSettings[K]) => {
    setDraft((current) => (current ? { ...current, [key]: nextValue } : current));
  };

  const updatePrompt = (agentKey: string, prompt: string) => {
    setDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        agentPrompts: current.agentPrompts.map((item) =>
          item.agentKey === agentKey ? { ...item, prompt } : item,
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
          Carregando governança de IA...
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Sparkles className="h-4 w-4" />
            Estratégia de modelos do MVP
          </CardTitle>
          <CardDescription>
            Só o admin decide onde testar Gemma no Vertex e onde cada modelo entra no sistema.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 lg:grid-cols-2 xl:grid-cols-4">
          <label className="flex items-center justify-between rounded-lg border p-3 lg:col-span-2 xl:col-span-4">
            <div className="space-y-0.5">
              <span className="text-sm font-medium flex items-center gap-2">
                <Brain className="h-4 w-4" />
                Gemma ativo nas faixas simples e médias
              </span>
              <p className="text-xs text-muted-foreground">
                Quando ativo, o admin pode usar Gemma oficial do Vertex nas faixas simples e médias sem expor detalhes técnicos ao usuário final.
              </p>
            </div>
            <Switch
              checked={draft.gemmaEnabled}
              onCheckedChange={(checked) => update('gemmaEnabled', checked)}
              disabled={readOnly}
            />
          </label>

          {[
            { key: 'simpleModel', tier: 'simple', label: 'Faixa simples', icon: Cpu },
            { key: 'mediumModel', tier: 'medium', label: 'Faixa média', icon: Cpu },
            { key: 'complexModel', tier: 'complex', label: 'Faixa complexa', icon: Brain },
            { key: 'premiumModel', tier: 'premium', label: 'Faixa premium', icon: Brain },
          ].map((item) => (
            <div key={item.key} className="space-y-1.5">
              <Label>{item.label}</Label>
              <Select
                value={draft[item.key as keyof AiGovernanceSettings] as string}
                onValueChange={(nextValue) => update(item.key as keyof AiGovernanceSettings, nextValue as never)}
                disabled={readOnly}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(groupedModels.get(item.tier) ?? draft.availableModels).map((model) => (
                    <SelectItem key={model.key} value={model.key}>
                      {model.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Bot className="h-4 w-4" />
            Prompts-base dos agentes
          </CardTitle>
          <CardDescription>
            Ajuste rapidamente os prompts mestres usados pelos agentes sem expor detalhes técnicos ao usuário do workspace.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {draft.agentPrompts.map((agent) => (
            <div key={agent.agentKey} className="space-y-1.5 rounded-lg border p-3">
              <div>
                <p className="text-sm font-medium">{agent.label}</p>
                <p className="text-xs text-muted-foreground">{agent.agentKey}</p>
              </div>
              <Textarea
                className="min-h-[140px]"
                value={agent.prompt}
                onChange={(event) => updatePrompt(agent.agentKey, event.target.value)}
                disabled={readOnly}
              />
            </div>
          ))}
        </CardContent>
      </Card>

      <div className="flex justify-end">
        <Button onClick={handleSave} disabled={saving || readOnly}>
          {saving ? 'Salvando...' : 'Salvar governança de IA'}
        </Button>
      </div>
    </div>
  );
}
