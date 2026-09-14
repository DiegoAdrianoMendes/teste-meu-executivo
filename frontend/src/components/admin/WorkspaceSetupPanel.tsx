import { useEffect, useMemo, useState } from 'react';
import { Building2, Mail, MessageCircle, Settings2, Sparkles, Wand2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import type { WorkspaceAutomationSettings, WorkspaceSettingsProfile } from '@/types';

interface WorkspaceSetupPanelProps {
  workspace: WorkspaceSettingsProfile | null;
  automation: WorkspaceAutomationSettings | null;
  loading?: boolean;
  readOnly?: boolean;
  onSave: (payload: { workspace: Partial<WorkspaceSettingsProfile>; automation: WorkspaceAutomationSettings }) => Promise<void>;
}

type DraftState = {
  name: string;
  slug: string;
  legalName: string;
  description: string;
  onboardingMode: string;
  preset: WorkspaceAutomationSettings['preset'];
  whatsappEnabled: boolean;
  emailEnabled: boolean;
  adminPanelEnabled: boolean;
  workspaceSelectionEnabled: boolean;
};

function buildDraft(
  workspace: WorkspaceSettingsProfile | null,
  automation: WorkspaceAutomationSettings | null,
): DraftState | null {
  if (!workspace || !automation) return null;
  const settings = workspace.settings ?? {};
  const channels = (settings.channels ?? {}) as Record<string, boolean>;
  const features = (settings.features ?? {}) as Record<string, boolean>;

  return {
    name: workspace.name ?? '',
    slug: workspace.slug ?? '',
    legalName: workspace.legalName ?? '',
    description: workspace.description ?? '',
    onboardingMode: automation.onboardingMode ?? workspace.setupMode ?? 'self_service',
    preset: automation.preset,
    whatsappEnabled: Boolean(channels.whatsapp),
    emailEnabled: Boolean(channels.email),
    adminPanelEnabled: features.adminPanel !== false,
    workspaceSelectionEnabled: features.workspaceSelection !== false,
  };
}

export function WorkspaceSetupPanel({ workspace, automation, loading, readOnly = false, onSave }: WorkspaceSetupPanelProps) {
  const [draft, setDraft] = useState<DraftState | null>(buildDraft(workspace, automation));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(buildDraft(workspace, automation));
  }, [workspace, automation]);

  const activePreset = useMemo(
    () => automation?.availablePresets.find((item) => item.key === draft?.preset),
    [automation, draft?.preset],
  );

  const updateDraft = <K extends keyof DraftState>(key: K, value: DraftState[K]) => {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  };

  const handleSave = async () => {
    if (!draft || !automation) return;
    setSaving(true);
    try {
      await onSave({
        workspace: {
          name: draft.name,
          slug: draft.slug,
          legalName: draft.legalName || null,
          description: draft.description || null,
          settings: {
            ...(workspace?.settings ?? {}),
            channels: {
              whatsapp: draft.whatsappEnabled,
              email: draft.emailEnabled,
            },
            features: {
              adminPanel: draft.adminPanelEnabled,
              workspaceSelection: draft.workspaceSelectionEnabled,
            },
          },
        },
        automation: {
          ...automation,
          onboardingMode: draft.onboardingMode,
          preset: draft.preset,
        },
      });
    } finally {
      setSaving(false);
    }
  };

  if (loading || !draft || !automation) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          Carregando setup guiado do workspace...
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Wand2 className="h-4 w-4" />
            Setup Guiado do Escritório
          </CardTitle>
          <CardDescription>
            Ajuste identidade, operação e automação do workspace por formulário simples, sem depender de configuração técnica.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          <div className="rounded-lg border p-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Modo de implantação</p>
            <p className="text-sm font-medium">{draft.onboardingMode === 'assisted' ? 'Assistido' : 'Self-service'}</p>
          </div>
          <div className="rounded-lg border p-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Preset ativo</p>
            <p className="text-sm font-medium">{activePreset?.label ?? 'Personalizado'}</p>
          </div>
          <div className="rounded-lg border p-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Etapas configuráveis</p>
            <p className="text-sm font-medium">{automation.stagePolicies.length}</p>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1.3fr,1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <Building2 className="h-4 w-4" />
              Identidade do Workspace
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Nome exibido</Label>
              <Input value={draft.name} onChange={(event) => updateDraft('name', event.target.value)} disabled={readOnly} />
            </div>
            <div className="space-y-1.5">
              <Label>Slug</Label>
              <Input value={draft.slug} onChange={(event) => updateDraft('slug', event.target.value)} disabled={readOnly} />
            </div>
            <div className="space-y-1.5 md:col-span-2">
              <Label>Razão social</Label>
              <Input value={draft.legalName} onChange={(event) => updateDraft('legalName', event.target.value)} disabled={readOnly} />
            </div>
            <div className="space-y-1.5 md:col-span-2">
              <Label>Descrição operacional</Label>
              <Textarea
                value={draft.description}
                onChange={(event) => updateDraft('description', event.target.value)}
                placeholder="Explique rapidamente como este escritório quer operar."
                disabled={readOnly}
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <Sparkles className="h-4 w-4" />
              Operação Guiada
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label>Tipo de onboarding</Label>
              <Select value={draft.onboardingMode} onValueChange={(value) => updateDraft('onboardingMode', value)} disabled={readOnly}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="self_service">Self-service</SelectItem>
                  <SelectItem value="assisted">Assistido</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label>Preset operacional</Label>
              <Select value={draft.preset} onValueChange={(value) => updateDraft('preset', value as WorkspaceAutomationSettings['preset'])} disabled={readOnly}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {automation.availablePresets.map((preset) => (
                    <SelectItem key={preset.key} value={preset.key}>
                      {preset.label}
                    </SelectItem>
                  ))}
                  <SelectItem value="custom">Personalizado</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                {activePreset?.description ?? 'Use personalizado se quiser manter ajustes manuais finos nas etapas.'}
              </p>
            </div>

            <div className="space-y-3">
              <label className="flex items-center justify-between rounded-md border p-3">
                <div className="space-y-0.5">
                  <span className="text-sm font-medium flex items-center gap-2"><MessageCircle className="h-3.5 w-3.5" /> WhatsApp ativo</span>
                  <p className="text-xs text-muted-foreground">Prepara o workspace para atendimento conversacional.</p>
                </div>
                <Switch checked={draft.whatsappEnabled} onCheckedChange={(checked) => updateDraft('whatsappEnabled', checked)} disabled={readOnly} />
              </label>
              <label className="flex items-center justify-between rounded-md border p-3">
                <div className="space-y-0.5">
                  <span className="text-sm font-medium flex items-center gap-2"><Mail className="h-3.5 w-3.5" /> Email ativo</span>
                  <p className="text-xs text-muted-foreground">Mantém comunicação complementar e notificações por email.</p>
                </div>
                <Switch checked={draft.emailEnabled} onCheckedChange={(checked) => updateDraft('emailEnabled', checked)} disabled={readOnly} />
              </label>
              <label className="flex items-center justify-between rounded-md border p-3">
                <div className="space-y-0.5">
                  <span className="text-sm font-medium flex items-center gap-2"><Settings2 className="h-3.5 w-3.5" /> Painel administrativo</span>
                  <p className="text-xs text-muted-foreground">Mantém a configuração do escritório acessível pelo próprio sistema.</p>
                </div>
                <Switch checked={draft.adminPanelEnabled} onCheckedChange={(checked) => updateDraft('adminPanelEnabled', checked)} disabled={readOnly} />
              </label>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Resumo do que este setup entrega</CardTitle>
          <CardDescription>
            Isso ajuda gestores leigos a entender o impacto prático antes de salvar.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          <div className="rounded-lg border p-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Comercial e atendimento</p>
            <p className="text-sm font-medium">SDR, closer, secretaria e pós-venda passam a seguir o preset escolhido.</p>
          </div>
          <div className="rounded-lg border p-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Onboarding e documentos</p>
            <p className="text-sm font-medium">Coleta de informações, checklist documental e apoio pontual ficam padronizados sem avançar para petições.</p>
          </div>
          <div className="rounded-lg border p-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Governança</p>
            <p className="text-sm font-medium">O painel segue como centro de configuração, sem precisar mexer em banco ou código.</p>
          </div>
        </CardContent>
      </Card>

      <div className="flex justify-end">
        <Button onClick={handleSave} disabled={saving || readOnly}>
          {readOnly ? 'Somente leitura' : saving ? 'Salvando setup...' : 'Salvar setup do workspace'}
        </Button>
      </div>
    </div>
  );
}
