import { useEffect, useState } from 'react';
import {
  Bell,
  Clock3,
  Contrast,
  Eye,
  Loader2,
  Monitor,
  Moon,
  Save,
  Sun,
  UserRoundCog,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { useToast } from '@/hooks/use-toast';
import { useUserPreferences } from '@/providers/user-preferences-provider';
import type { UserPreferences } from '@/types/frontend-contracts';

type PreferencesDraft = Omit<
  UserPreferences,
  'id' | 'workspaceId' | 'userId' | 'version' | 'updatedAt'
>;

const timezoneLabel: Record<string, string> = {
  'America/Sao_Paulo': 'São Paulo',
  'America/Manaus': 'Manaus',
  'America/Recife': 'Recife',
};

const homePathLabel: Record<string, string> = {
  '/app': 'Visão geral',
  '/app/conversas': 'Conversas',
  '/app/funil': 'Funil',
  '/app/agenda': 'Agenda',
};

function toDraft(preferences: UserPreferences): PreferencesDraft {
  const {
    id: _id,
    workspaceId: _workspaceId,
    userId: _userId,
    version: _version,
    updatedAt: _updatedAt,
    ...draft
  } = preferences;
  return draft;
}

function SettingRow({
  icon,
  label,
  description,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-3 border-b border-border/70 py-4 last:border-b-0 md:grid-cols-[minmax(0,1fr)_minmax(240px,0.8fr)] md:items-center">
      <div className="flex min-w-0 gap-3">
        <span className="mt-0.5 text-muted-foreground">{icon}</span>
        <div className="min-w-0">
          <Label className="text-sm font-medium">{label}</Label>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
        </div>
      </div>
      <div className="flex min-w-0 justify-start md:justify-end">{children}</div>
    </div>
  );
}

export function PersonalPreferencesPanel() {
  const { toast } = useToast();
  const { preferences, status, persistenceError, updatePreferences } = useUserPreferences();
  const [draft, setDraft] = useState<PreferencesDraft | null>(
    preferences ? toDraft(preferences) : null,
  );
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (preferences) setDraft(toDraft(preferences));
  }, [preferences]);

  if (status === 'loading' || !draft) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Carregando preferências pessoais...
        </CardContent>
      </Card>
    );
  }

  const save = async () => {
    setSaving(true);
    try {
      await updatePreferences(draft);
      toast({ title: 'Preferências pessoais atualizadas.' });
    } catch (error) {
      toast({
        variant: 'destructive',
        title: 'Não foi possível salvar as preferências',
        description: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader className="border-b border-border/70">
        <CardTitle className="flex items-center gap-2 text-base">
          <UserRoundCog className="h-4 w-4" />
          Preferências pessoais
        </CardTitle>
        <CardDescription>
          Aplicadas somente ao seu usuário neste workspace.
        </CardDescription>
      </CardHeader>
      <CardContent className="pt-1">
        <SettingRow
          icon={<Monitor className="h-4 w-4" />}
          label="Aparência"
          description="Tema e densidade da interface."
        >
          <div className="flex flex-wrap items-center gap-2">
            <ToggleGroup
              type="single"
              value={draft.theme}
              onValueChange={(theme) => {
                if (theme) setDraft((current) => current && ({ ...current, theme: theme as PreferencesDraft['theme'] }));
              }}
              aria-label="Tema da interface"
            >
              <ToggleGroupItem value="light" aria-label="Tema claro" className="gap-1.5">
                <Sun className="h-4 w-4" /> Claro
              </ToggleGroupItem>
              <ToggleGroupItem value="dark" aria-label="Tema escuro" className="gap-1.5">
                <Moon className="h-4 w-4" /> Escuro
              </ToggleGroupItem>
              <ToggleGroupItem value="system" aria-label="Tema do sistema" className="gap-1.5">
                <Monitor className="h-4 w-4" /> Sistema
              </ToggleGroupItem>
            </ToggleGroup>
            <Select
              value={draft.density}
              onValueChange={(density) => setDraft((current) => current && ({
                ...current,
                density: density as PreferencesDraft['density'],
              }))}
            >
              <SelectTrigger className="w-36" aria-label="Densidade da interface">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="compact">Compacta</SelectItem>
                <SelectItem value="comfortable">Confortável</SelectItem>
                <SelectItem value="spacious">Ampla</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </SettingRow>

        <SettingRow
          icon={<Clock3 className="h-4 w-4" />}
          label="Localização"
          description="Idioma, fuso, moeda e formatos usados nas telas."
        >
          <div className="grid w-full grid-cols-2 gap-2 lg:grid-cols-3">
            <Select value={draft.locale} onValueChange={(locale) => setDraft((current) => current && ({ ...current, locale }))}>
              <SelectTrigger aria-label="Idioma" title="Português (Brasil)" className="min-w-[172px]"><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value="pt-BR">Português (Brasil)</SelectItem></SelectContent>
            </Select>
            <Select value={draft.timezone} onValueChange={(timezone) => setDraft((current) => current && ({ ...current, timezone }))}>
              <SelectTrigger aria-label="Fuso horário" title={timezoneLabel[draft.timezone] ?? draft.timezone}><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="America/Sao_Paulo">São Paulo</SelectItem>
                <SelectItem value="America/Manaus">Manaus</SelectItem>
                <SelectItem value="America/Recife">Recife</SelectItem>
              </SelectContent>
            </Select>
            <Select value={draft.currency} onValueChange={(currency) => setDraft((current) => current && ({ ...current, currency }))}>
              <SelectTrigger aria-label="Moeda" title="Real brasileiro" className="min-w-[140px]"><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value="BRL">Real brasileiro</SelectItem></SelectContent>
            </Select>
            <Select value={draft.dateFormat} onValueChange={(dateFormat) => setDraft((current) => current && ({ ...current, dateFormat }))}>
              <SelectTrigger aria-label="Formato de data" title={draft.dateFormat === 'dd/MM/yyyy' ? '31/12/2026' : '2026-12-31'}><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="dd/MM/yyyy">31/12/2026</SelectItem>
                <SelectItem value="yyyy-MM-dd">2026-12-31</SelectItem>
              </SelectContent>
            </Select>
            <Select value={draft.timeFormat} onValueChange={(timeFormat) => setDraft((current) => current && ({ ...current, timeFormat: timeFormat as '12h' | '24h' }))}>
              <SelectTrigger aria-label="Formato de hora" title={draft.timeFormat === '24h' ? '24 horas' : '12 horas'}><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="24h">24 horas</SelectItem>
                <SelectItem value="12h">12 horas</SelectItem>
              </SelectContent>
            </Select>
            <Select value={draft.homePath} onValueChange={(homePath) => setDraft((current) => current && ({ ...current, homePath }))}>
              <SelectTrigger aria-label="Página inicial" title={homePathLabel[draft.homePath] ?? draft.homePath}><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="/app">Visão geral</SelectItem>
                <SelectItem value="/app/conversas">Conversas</SelectItem>
                <SelectItem value="/app/funil">Funil</SelectItem>
                <SelectItem value="/app/agenda">Agenda</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </SettingRow>

        <SettingRow
          icon={<Bell className="h-4 w-4" />}
          label="Notificações"
          description="Canais e alertas que você recebe."
        >
          <div className="grid w-full grid-cols-2 gap-x-5 gap-y-3 text-sm">
            {([
              ['inApp', 'No aplicativo'],
              ['email', 'E-mail'],
              ['whatsapp', 'WhatsApp'],
              ['approvalRequests', 'Aprovações'],
              ['slaWarnings', 'Alertas de prazo de resposta'],
            ] as const).map(([key, label]) => (
              <label key={key} className="flex items-center justify-between gap-3">
                <span>{label}</span>
                <Switch
                  checked={draft.notifications[key]}
                  onCheckedChange={(checked) => setDraft((current) => current && ({
                    ...current,
                    notifications: { ...current.notifications, [key]: checked },
                  }))}
                  aria-label={label}
                />
              </label>
            ))}
          </div>
        </SettingRow>

        <SettingRow
          icon={<Eye className="h-4 w-4" />}
          label="Resumo diário"
          description="Horário local do seu resumo operacional."
        >
          <div className="flex w-full items-center gap-4 md:max-w-sm">
            <Switch
              checked={draft.dailyDigest.enabled}
              onCheckedChange={(enabled) => setDraft((current) => current && ({
                ...current,
                dailyDigest: { ...current.dailyDigest, enabled },
              }))}
              aria-label="Ativar resumo diário"
            />
            <Slider
              min={0}
              max={23}
              step={1}
              value={[draft.dailyDigest.hour]}
              onValueChange={([hour]) => setDraft((current) => current && ({
                ...current,
                dailyDigest: { ...current.dailyDigest, hour },
              }))}
              disabled={!draft.dailyDigest.enabled}
              aria-label="Hora do resumo diário"
            />
            <span className="w-12 text-right font-mono text-sm tabular-nums">
              {String(draft.dailyDigest.hour).padStart(2, '0')}:00
            </span>
          </div>
        </SettingRow>

        <SettingRow
          icon={<Contrast className="h-4 w-4" />}
          label="Acessibilidade"
          description="Movimento, contraste e escala de leitura."
        >
          <div className="w-full space-y-3 md:max-w-md">
            <label className="flex items-center justify-between gap-3 text-sm">
              <span>Reduzir movimento</span>
              <Switch
                checked={draft.accessibility.reducedMotion}
                onCheckedChange={(reducedMotion) => setDraft((current) => current && ({
                  ...current,
                  accessibility: { ...current.accessibility, reducedMotion },
                }))}
              />
            </label>
            <label className="flex items-center justify-between gap-3 text-sm">
              <span>Alto contraste</span>
              <Switch
                checked={draft.accessibility.highContrast}
                onCheckedChange={(highContrast) => setDraft((current) => current && ({
                  ...current,
                  accessibility: { ...current.accessibility, highContrast },
                }))}
              />
            </label>
            <div className="flex items-center gap-3 text-sm">
              <span className="shrink-0">Texto</span>
              <Slider
                min={0.8}
                max={1.5}
                step={0.05}
                value={[draft.accessibility.fontScale]}
                onValueChange={([fontScale]) => setDraft((current) => current && ({
                  ...current,
                  accessibility: { ...current.accessibility, fontScale },
                }))}
                aria-label="Escala do texto"
              />
              <span className="w-12 text-right tabular-nums">
                {Math.round(draft.accessibility.fontScale * 100)}%
              </span>
            </div>
          </div>
        </SettingRow>

        {persistenceError && (
          <p className="mt-4 text-sm text-destructive" role="alert">
            {persistenceError.message}
          </p>
        )}

        <div className="flex justify-end pt-5">
          <Button onClick={save} disabled={saving}>
            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
            Salvar preferências
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
