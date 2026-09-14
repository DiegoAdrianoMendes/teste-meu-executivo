import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { BookTemplate, GitBranch, MessageSquare, Workflow } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import type { AutomationStageMode, FollowUpProfile, PracticeAreaTemplate } from '@/types';
import { AGENT_OPTIONS, STAGE_OPTIONS } from '@/lib/flow-editor';

interface PracticeAreaLibraryPanelProps {
  value: PracticeAreaTemplate[];
  loading?: boolean;
  readOnly?: boolean;
  onSave: (areas: PracticeAreaTemplate[]) => Promise<void>;
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

export function PracticeAreaLibraryPanel({
  value,
  loading,
  readOnly = false,
  onSave,
}: PracticeAreaLibraryPanelProps) {
  const navigate = useNavigate();
  const [draft, setDraft] = useState<PracticeAreaTemplate[]>(value);
  const [selectedKey, setSelectedKey] = useState<string>(value[0]?.key ?? '');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(value);
    setSelectedKey((current) => (value.some((area) => area.key === current) ? current : value[0]?.key ?? ''));
  }, [value]);

  const selectedArea = useMemo(
    () => draft.find((area) => area.key === selectedKey) ?? draft[0] ?? null,
    [draft, selectedKey],
  );

  const updateArea = (key: string, patch: Partial<PracticeAreaTemplate>) => {
    setDraft((current) => current.map((area) => (area.key === key ? { ...area, ...patch } : area)));
  };

  const toggleFlow = (areaKey: string, flowId: string) => {
    setDraft((current) =>
      current.map((area) => {
        if (area.key !== areaKey) return area;
        const exists = area.allowedFlowTemplateIds.includes(flowId);
        return {
          ...area,
          allowedFlowTemplateIds: exists
            ? area.allowedFlowTemplateIds.filter((id) => id !== flowId)
            : [...area.allowedFlowTemplateIds, flowId],
        };
      }),
    );
  };

  const updateChecklist = (areaKey: string, valueText: string) => {
    updateArea(areaKey, {
      checklist: valueText
        .split('\n')
        .map((item) => item.trim())
        .filter(Boolean),
    });
  };

  const updateStagePrompt = (areaKey: string, stageKey: string, prompt: string) => {
    setDraft((current) =>
      current.map((area) => {
        if (area.key !== areaKey) return area;
        return {
          ...area,
          stagePrompts: area.stagePrompts.map((item) =>
            item.stageKey === stageKey ? { ...item, prompt } : item,
          ),
        };
      }),
    );
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(draft);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          Carregando biblioteca de áreas...
        </CardContent>
      </Card>
    );
  }

  if (!selectedArea) {
    return null;
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[280px,1fr]">
      <Card className="xl:sticky xl:top-4 h-fit">
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <BookTemplate className="h-4 w-4" />
            Biblioteca de Áreas
          </CardTitle>
          <CardDescription>
            O admin governa os templates e o usuário final só ativa o que for permitido.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {draft.map((area) => (
            <button
              key={area.key}
              type="button"
              onClick={() => setSelectedKey(area.key)}
              className={`w-full rounded-lg border px-3 py-3 text-left transition-colors ${
                selectedKey === area.key ? 'border-primary bg-primary/5' : 'hover:bg-muted/40'
              }`}
            >
              <p className="text-sm font-medium">{area.label}</p>
              <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{area.description}</p>
            </button>
          ))}
        </CardContent>
      </Card>

      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{selectedArea.label}</CardTitle>
            <CardDescription>
              Ajuste prompt, gatilho, follow-up, intervenção humana e flows liberados para esta área.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Nome da área</Label>
              <Input
                value={selectedArea.label}
                onChange={(event) => updateArea(selectedArea.key, { label: event.target.value })}
                disabled={readOnly}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Área-base dos flows</Label>
              <Input
                value={selectedArea.flowPracticeArea}
                onChange={(event) => updateArea(selectedArea.key, { flowPracticeArea: event.target.value })}
                disabled={readOnly}
              />
            </div>
            <div className="space-y-1.5 lg:col-span-2">
              <Label>Descrição operacional</Label>
              <Textarea
                value={selectedArea.description}
                onChange={(event) => updateArea(selectedArea.key, { description: event.target.value })}
                disabled={readOnly}
              />
            </div>
            <div className="space-y-1.5 lg:col-span-2">
              <Label>Gatilho resumido</Label>
              <Textarea
                value={selectedArea.triggerSummary}
                onChange={(event) => updateArea(selectedArea.key, { triggerSummary: event.target.value })}
                disabled={readOnly}
              />
            </div>
            <div className="space-y-1.5 lg:col-span-2">
              <Label>Prompt base da área</Label>
              <Textarea
                className="min-h-[160px]"
                value={selectedArea.basePrompt}
                onChange={(event) => updateArea(selectedArea.key, { basePrompt: event.target.value })}
                disabled={readOnly}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Follow-up padrão</Label>
              <Select
                value={selectedArea.defaultFollowUpProfile}
                onValueChange={(value) => updateArea(selectedArea.key, { defaultFollowUpProfile: value as FollowUpProfile })}
                disabled={readOnly}
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
              <Label>Intervenção humana padrão</Label>
              <Select
                value={selectedArea.humanInterventionMode}
                onValueChange={(value) => updateArea(selectedArea.key, { humanInterventionMode: value as AutomationStageMode })}
                disabled={readOnly}
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
            <div className="space-y-1.5">
              <Label>Agente padrão</Label>
              <Select
                value={selectedArea.defaultAgentKey}
                onValueChange={(value) => updateArea(selectedArea.key, { defaultAgentKey: value })}
                disabled={readOnly}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {AGENT_OPTIONS.map((agent) => (
                    <SelectItem key={agent.value} value={agent.value}>{agent.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Faixa de modelo</Label>
              <Select
                value={selectedArea.modelTier}
                onValueChange={(value) => updateArea(selectedArea.key, { modelTier: value as PracticeAreaTemplate['modelTier'] })}
                disabled={readOnly}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="simple">Simples</SelectItem>
                  <SelectItem value="medium">Médio</SelectItem>
                  <SelectItem value="complex">Complexo</SelectItem>
                  <SelectItem value="premium">Premium</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5 lg:col-span-2">
              <Label>Checklist padrão</Label>
              <Textarea
                className="min-h-[120px]"
                value={selectedArea.checklist.join('\n')}
                onChange={(event) => updateChecklist(selectedArea.key, event.target.value)}
                disabled={readOnly}
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <Workflow className="h-4 w-4" />
              Flows permitidos nesta área
            </CardTitle>
            <CardDescription>
              O usuário final só poderá ativar templates liberados aqui. O admin pode abrir o editor técnico quando precisar.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-2">
            {selectedArea.availableFlows.map((flow) => {
              const enabled = selectedArea.allowedFlowTemplateIds.length === 0 || selectedArea.allowedFlowTemplateIds.includes(flow.id);
              return (
                <div key={flow.id} className="rounded-lg border p-3 space-y-2">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{flow.name}</p>
                      <p className="text-xs text-muted-foreground">{flow.description || 'Sem descrição adicional.'}</p>
                    </div>
                    <Button
                      type="button"
                      variant={enabled ? 'default' : 'outline'}
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => toggleFlow(selectedArea.key, flow.id)}
                      disabled={readOnly}
                    >
                      {enabled ? 'Liberado' : 'Liberar'}
                    </Button>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground tabular-nums">
                    <span>{flow.trigger}</span>
                    <span>•</span>
                    <span>{flow.nodeCount} nós</span>
                    <span>•</span>
                    <span>{flow.promptCount} prompts</span>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 px-0 text-xs"
                    onClick={() => navigate(`/automacoes/editor/${flow.id}`)}
                  >
                    <GitBranch className="mr-1 h-3 w-3" />
                    Abrir no FlowBuilder
                  </Button>
                </div>
              );
            })}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <MessageSquare className="h-4 w-4" />
              Prompts por etapa
            </CardTitle>
            <CardDescription>
              Ajuste rapidamente os prompts operacionais sem abrir o canvas do flow sempre que não precisar.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {selectedArea.stagePrompts.map((prompt) => (
              <div key={prompt.stageKey} className="space-y-1.5 rounded-lg border p-3">
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium">{prompt.title}</p>
                    <p className="text-xs text-muted-foreground">
                      {STAGE_OPTIONS.find((stage) => stage.value === prompt.stageKey)?.label ?? prompt.stageKey}
                    </p>
                  </div>
                </div>
                <Textarea
                  className="min-h-[120px]"
                  value={prompt.prompt}
                  onChange={(event) => updateStagePrompt(selectedArea.key, prompt.stageKey, event.target.value)}
                  disabled={readOnly}
                />
              </div>
            ))}
          </CardContent>
        </Card>

        <div className="flex justify-end">
          <Button onClick={handleSave} disabled={saving || readOnly}>
            {saving ? 'Salvando...' : 'Salvar biblioteca'}
          </Button>
        </div>
      </div>
    </div>
  );
}
