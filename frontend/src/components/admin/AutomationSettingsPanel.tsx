import { useEffect, useMemo, useState } from 'react';
import { Bot, Coins, FileSearch, Image, ShieldCheck, UserCog } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import type { AutomationPreset, WorkspaceAutomationSettings, WorkspaceStagePolicy } from '@/types';

interface AutomationSettingsPanelProps {
  value: WorkspaceAutomationSettings | null;
  loading?: boolean;
  readOnly?: boolean;
  onSave: (settings: WorkspaceAutomationSettings) => Promise<void>;
}

const modeLabels: Record<WorkspaceStagePolicy['mode'], string> = {
  human_only: 'Só humano',
  ai_only: 'Só IA',
  human_in_the_loop: 'IA com revisão humana',
  ai_with_human_approval: 'IA com aprovação obrigatória',
};

const costLabels: Record<WorkspaceStagePolicy['costProfile'], string> = {
  economic: 'Econômico',
  balanced: 'Equilibrado',
  quality: 'Máxima qualidade',
};

const contextLabels: Record<WorkspaceStagePolicy['contextMode'], string> = {
  essential: 'Essencial',
  balanced: 'Balanceado',
  full: 'Completo',
};

const modeHints: Record<WorkspaceStagePolicy['mode'], string> = {
  human_only: 'A etapa só cria pendência para o time humano, sem execução automática da IA.',
  ai_only: 'A IA executa sozinha, sem esperar revisão humana por padrão.',
  human_in_the_loop: 'A IA trabalha, mas uma pessoa revisa a saída antes da etapa avançar.',
  ai_with_human_approval: 'A IA produz e o sistema exige aprovação humana para concluir.',
};

const modelSuggestions = [
  'google/gemma3@gemma-3-4b-it',
  'google/gemma3@gemma-3-27b-it',
  'qwen3:4b',
  'qwen3:14b',
  'gemma4:12b',
  'gemma4:e4b',
  'qwen3:30b',
  'gemma4:31b',
  'gemini-2.5-flash-lite',
  'gemini-2.5-flash',
  'gpt-4o-mini',
  'gpt-4o',
];

export function AutomationSettingsPanel({ value, loading, readOnly = false, onSave }: AutomationSettingsPanelProps) {
  const [form, setForm] = useState<WorkspaceAutomationSettings | null>(value);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setForm(value);
  }, [value]);

  const sortedStages = useMemo(
    () => [...(form?.stagePolicies ?? [])],
    [form],
  );

  const updateStage = (stageKey: string, patch: Partial<WorkspaceStagePolicy>) => {
    setForm((current) => {
      if (!current) return current;
      return {
        ...current,
        preset: 'custom',
        stagePolicies: current.stagePolicies.map((stage) =>
          stage.stageKey === stageKey ? { ...stage, ...patch } : stage
        ),
      };
    });
  };

  const applyPreset = (preset: AutomationPreset) => {
    setForm((current) => {
      if (!current) return current;

      const stagePolicies = current.stagePolicies.map<WorkspaceStagePolicy>((stage) => {
        if (preset === 'economic') {
          if (stage.stageKey === 'proposta_fechamento') {
            return {
              ...stage,
              mode: 'human_only',
              agentKey: 'closer_humano',
              model: null,
              costProfile: 'balanced',
              contextMode: 'balanced',
              useRag: true,
              multimodalEnabled: false,
              humanReviewRequired: true,
            };
          }
          return {
            ...stage,
            mode: stage.stageKey === 'entrada' ? 'ai_only' : 'human_in_the_loop',
            costProfile: 'economic',
            contextMode: stage.stageKey === 'entrada' ? 'essential' : 'balanced',
            model: 'gemini-2.5-flash-lite',
            useRag: true,
            multimodalEnabled: stage.stageKey === 'entrada',
            humanReviewRequired: stage.stageKey !== 'entrada',
          };
        }

        if (preset === 'supervised') {
          return {
            ...stage,
            mode: stage.stageKey === 'proposta_fechamento' ? 'human_only' : 'ai_with_human_approval',
            costProfile: 'quality',
            contextMode: 'full',
            model: stage.stageKey === 'proposta_fechamento' ? null : 'gemini-2.5-flash',
            useRag: true,
            multimodalEnabled: true,
            humanReviewRequired: true,
          };
        }

        if (stage.stageKey === 'proposta_fechamento') {
          return {
            ...stage,
            mode: 'human_only',
            agentKey: 'closer_humano',
            model: null,
            costProfile: 'balanced',
            contextMode: 'balanced',
            useRag: true,
            multimodalEnabled: false,
            humanReviewRequired: true,
          };
        }

        return {
          ...stage,
          mode: stage.stageKey === 'entrada' ? 'ai_only' : 'human_in_the_loop',
          costProfile: 'balanced',
          contextMode: stage.stageKey === 'entrada' ? 'balanced' : 'full',
          model: stage.stageKey === 'entrada' ? 'gemini-2.5-flash-lite' : 'gemini-2.5-flash',
          useRag: true,
          multimodalEnabled: true,
          humanReviewRequired: stage.stageKey !== 'entrada',
        };
      });

      return { ...current, preset, stagePolicies };
    });
  };

  const handleSave = async () => {
    if (!form) return;
    setSaving(true);
    try {
      await onSave(form);
    } finally {
      setSaving(false);
    }
  };

  if (loading || !form) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          Carregando automações do workspace...
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <UserCog className="h-4 w-4" />
            Automação do Escritório
          </CardTitle>
          <CardDescription>
            Configure etapa por etapa em linguagem simples. Tudo aqui pode ser ajustado por um gestor do workspace sem editar banco ou código.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-2 md:grid-cols-3">
          <div className="rounded-md border p-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Onboarding</p>
            <p className="text-sm font-medium">{form.onboardingMode === 'assisted' ? 'Assistido' : 'Self-service'}</p>
          </div>
          <div className="rounded-md border p-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Etapas configuráveis</p>
            <p className="text-sm font-medium">{form.stagePolicies.length}</p>
          </div>
          <div className="rounded-md border p-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Objetivo</p>
            <p className="text-sm font-medium">Menos custo onde der, mais contexto onde precisar</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Presets rápidos</CardTitle>
          <CardDescription>
            Comece com um modelo pronto e depois ajuste etapa por etapa se quiser.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="rounded-md border p-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Preset ativo</p>
            <p className="text-sm font-medium">
              {form.preset === 'custom'
                ? 'Personalizado'
                : form.availablePresets.find((item) => item.key === form.preset)?.label ?? form.preset}
            </p>
          </div>
          <div className="grid gap-3 md:grid-cols-3">
            {form.availablePresets.map((preset) => (
              <button
                key={preset.key}
                type="button"
                onClick={() => applyPreset(preset.key)}
                disabled={readOnly}
                className="rounded-lg border bg-card p-4 text-left transition-colors hover:bg-muted/40"
              >
                <p className="text-sm font-medium">{preset.label}</p>
                <p className="mt-1 text-xs text-muted-foreground">{preset.description}</p>
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {sortedStages.map((stage) => (
        <Card key={stage.stageKey}>
          <CardHeader>
            <CardTitle className="text-base">{stage.label}</CardTitle>
            <CardDescription>{stage.description}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              <div className="space-y-1.5">
                <Label>Quem conduz esta etapa</Label>
                <Select value={stage.mode} onValueChange={(value) => updateStage(stage.stageKey, { mode: value as WorkspaceStagePolicy['mode'] })} disabled={readOnly}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(modeLabels).map(([value, label]) => (
                      <SelectItem key={value} value={value}>{label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">{modeHints[stage.mode]}</p>
              </div>

              <div className="space-y-1.5">
                <Label>Agente responsável</Label>
                <Input
                  value={stage.agentKey}
                  onChange={(event) => updateStage(stage.stageKey, { agentKey: event.target.value })}
                  placeholder="Ex.: klm_sdr ou closer_humano"
                  disabled={readOnly}
                />
                <p className="text-xs text-muted-foreground">Você pode trocar o agente sem reestruturar o sistema.</p>
              </div>

              <div className="space-y-1.5">
                <Label>Modelo preferido</Label>
                <Select value={stage.model ?? '__none__'} onValueChange={(value) => updateStage(stage.stageKey, { model: value === '__none__' ? null : value })} disabled={readOnly}>
                  <SelectTrigger>
                    <SelectValue placeholder="Usar padrão do workspace" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Usar padrão do workspace</SelectItem>
                    {modelSuggestions.map((model) => (
                      <SelectItem key={model} value={model}>{model}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">Escolha um modelo mais barato quando a etapa não for complexa.</p>
              </div>

              <div className="space-y-1.5">
                <Label className="flex items-center gap-2"><Coins className="h-3.5 w-3.5" /> Perfil de custo</Label>
                <Select value={stage.costProfile} onValueChange={(value) => updateStage(stage.stageKey, { costProfile: value as WorkspaceStagePolicy['costProfile'] })} disabled={readOnly}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(costLabels).map(([value, label]) => (
                      <SelectItem key={value} value={value}>{label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label className="flex items-center gap-2"><FileSearch className="h-3.5 w-3.5" /> Contexto entregue</Label>
                <Select value={stage.contextMode} onValueChange={(value) => updateStage(stage.stageKey, { contextMode: value as WorkspaceStagePolicy['contextMode'] })} disabled={readOnly}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(contextLabels).map(([value, label]) => (
                      <SelectItem key={value} value={value}>{label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">Use “Essencial” para economizar e “Completo” quando a etapa exigir visão total do caso.</p>
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-3">
              <label className="flex items-center justify-between rounded-md border p-3">
                <div className="space-y-0.5">
                  <span className="text-sm font-medium">Usar RAG</span>
                  <p className="text-xs text-muted-foreground">Puxa fatos e memória do card/task quando necessário.</p>
                </div>
                <Switch checked={stage.useRag} onCheckedChange={(checked) => updateStage(stage.stageKey, { useRag: checked })} disabled={readOnly} />
              </label>

              <label className="flex items-center justify-between rounded-md border p-3">
                <div className="space-y-0.5">
                  <span className="text-sm font-medium flex items-center gap-2"><Image className="h-3.5 w-3.5" /> Ler mídia</span>
                  <p className="text-xs text-muted-foreground">Permite usar imagem, áudio e documento como contexto.</p>
                </div>
                <Switch checked={stage.multimodalEnabled} onCheckedChange={(checked) => updateStage(stage.stageKey, { multimodalEnabled: checked })} disabled={readOnly} />
              </label>

              <label className="flex items-center justify-between rounded-md border p-3">
                <div className="space-y-0.5">
                  <span className="text-sm font-medium flex items-center gap-2"><ShieldCheck className="h-3.5 w-3.5" /> Revisão humana</span>
                  <p className="text-xs text-muted-foreground">Força validação humana antes da etapa avançar.</p>
                </div>
                <Switch checked={stage.humanReviewRequired} onCheckedChange={(checked) => updateStage(stage.stageKey, { humanReviewRequired: checked })} disabled={readOnly} />
              </label>
            </div>
          </CardContent>
        </Card>
      ))}

      <div className="flex justify-end">
        <Button onClick={handleSave} disabled={saving || readOnly}>
          <Bot className="mr-2 h-4 w-4" />
          {readOnly ? 'Somente leitura' : saving ? 'Salvando...' : 'Salvar automações'}
        </Button>
      </div>
    </div>
  );
}
