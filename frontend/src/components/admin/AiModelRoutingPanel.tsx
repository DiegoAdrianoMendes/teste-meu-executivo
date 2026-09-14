import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Cpu, KeyRound, Loader2, RotateCw, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import type {
  AiModelOption,
  WorkspaceAiCredentialStatus,
  WorkspaceAiModelRouting,
  WorkspaceAiModelRoutingUpdate,
} from '@/types';

// Ordem e rótulos de negócio das classes de tarefa do gateway.
export const AI_TASK_CLASS_LABELS: Record<string, string> = {
  premium: 'Petições',
  complex: 'Análise jurídica',
  simple: 'Conversas',
  medium: 'Leitura de documentos',
};
export const AI_TASK_CLASS_ORDER = ['premium', 'complex', 'simple', 'medium'];

export const AI_PROVIDER_LABELS: Record<string, string> = {
  anthropic: 'Anthropic (Claude)',
  openai: 'OpenAI',
  gemini: 'Google Gemini',
  gemini_api: 'Google Gemini',
  vertex: 'Google Vertex',
  vertex_gemini: 'Google Vertex',
  maritaca: 'Maritaca (Sabiá)',
  fake: 'Simulador local',
};

const PLATFORM_VALUE = '__platform__';

function providerLabel(provider: string): string {
  return AI_PROVIDER_LABELS[provider] ?? provider;
}

function optionKey(option: Pick<AiModelOption, 'provider' | 'model'>): string {
  return `${option.provider}:${option.model}`;
}

function formatUsdPerMillion(value: number): string {
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value || 0);
}

const SELECT_CLASS =
  'h-9 w-full rounded-md border border-input bg-background px-3 text-sm ' +
  'focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50';

// ── Modelos por tarefa ────────────────────────────────────────────────────

interface AiModelRoutingPanelProps {
  value: WorkspaceAiModelRouting | null;
  loading?: boolean;
  error?: string | null;
  readOnly?: boolean;
  onRetry?: () => void;
  onSave?: (payload: WorkspaceAiModelRoutingUpdate) => Promise<void>;
}

export function AiModelRoutingPanel({
  value,
  loading = false,
  error = null,
  readOnly = false,
  onRetry,
  onSave,
}: AiModelRoutingPanelProps) {
  const [selection, setSelection] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const baseline = useMemo(() => {
    const map: Record<string, string> = {};
    for (const item of value?.classes ?? []) {
      map[item.taskClass] = item.source === 'workspace' && item.provider && item.model
        ? `${item.provider}:${item.model}`
        : PLATFORM_VALUE;
    }
    return map;
  }, [value?.classes]);

  useEffect(() => {
    setSelection(baseline);
  }, [baseline]);

  const optionByKey = useMemo(() => {
    const map = new Map<string, AiModelOption>();
    for (const option of value?.options ?? []) map.set(optionKey(option), option);
    return map;
  }, [value?.options]);

  const orderedClasses = useMemo(() => {
    const items = value?.classes ?? [];
    return [...items].sort(
      (a, b) => AI_TASK_CLASS_ORDER.indexOf(a.taskClass) - AI_TASK_CLASS_ORDER.indexOf(b.taskClass),
    );
  }, [value?.classes]);

  const changes = useMemo(() => {
    const routes: WorkspaceAiModelRoutingUpdate['routes'] = {};
    for (const item of value?.classes ?? []) {
      const current = selection[item.taskClass] ?? PLATFORM_VALUE;
      const original = baseline[item.taskClass] ?? PLATFORM_VALUE;
      if (current === original) continue;
      if (current === PLATFORM_VALUE) {
        routes[item.taskClass] = null;
        continue;
      }
      const option = optionByKey.get(current);
      if (option) routes[item.taskClass] = { provider: option.provider, model: option.model };
    }
    return routes;
  }, [baseline, optionByKey, selection, value?.classes]);

  const hasChanges = Object.keys(changes).length > 0;

  const save = async () => {
    if (!onSave || readOnly || !hasChanges) return;
    setSaving(true);
    try {
      await onSave({ routes: changes });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card data-testid="ai-model-routing-panel">
      <CardHeader className="gap-1">
        <CardTitle className="flex items-center gap-2 text-base">
          <Cpu className="h-4 w-4 text-primary" />Modelos por tarefa
        </CardTitle>
        <CardDescription>
          Escolha o provedor e o modelo usados em cada tipo de trabalho. Sem escolha, vale o
          padrão da plataforma — sempre dentro do catálogo homologado, sem abrir nenhum gate externo.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading && !value ? (
          <div className="flex min-h-28 items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />Carregando catálogo de modelos
          </div>
        ) : error ? (
          <div className="flex min-h-28 flex-col items-center justify-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-5 text-center">
            <AlertTriangle className="h-5 w-5 text-destructive" />
            <div>
              <p className="text-sm font-semibold">Não foi possível carregar o roteamento de modelos</p>
              <p className="mt-1 text-xs text-muted-foreground">{error}</p>
            </div>
            {onRetry && (
              <Button type="button" variant="outline" size="sm" onClick={onRetry}>
                <RotateCw className="mr-2 h-3.5 w-3.5" />Tentar novamente
              </Button>
            )}
          </div>
        ) : !value ? (
          <div className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground">
            Nenhuma configuração de roteamento disponível para este escopo.
          </div>
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              Catálogo <span className="font-medium">{value.catalogVersion}</span> · custos por 1M de tokens (entrada / saída).
            </p>
            <div className="space-y-3">
              {orderedClasses.map((item) => {
                const selected = selection[item.taskClass] ?? PLATFORM_VALUE;
                const isPlatform = selected === PLATFORM_VALUE;
                const effectiveOption = isPlatform
                  ? (item.platformProvider && item.platformModel
                    ? optionByKey.get(`${item.platformProvider}:${item.platformModel}`) ?? null
                    : null)
                  : optionByKey.get(selected) ?? null;
                const selectId = `ai-route-${item.taskClass}`;
                return (
                  <div key={item.taskClass} className="rounded-lg border p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <Label htmlFor={selectId} className="text-sm font-medium">
                        {AI_TASK_CLASS_LABELS[item.taskClass] ?? item.taskClass}
                      </Label>
                      {isPlatform && (
                        <Badge variant="outline" className="text-2xs font-medium text-muted-foreground">
                          Padrão da plataforma
                        </Badge>
                      )}
                    </div>
                    <div className="mt-2 grid gap-2 sm:grid-cols-[1.4fr,1fr] sm:items-center">
                      <select
                        id={selectId}
                        className={SELECT_CLASS}
                        value={selected}
                        disabled={readOnly || saving}
                        onChange={(event) =>
                          setSelection((current) => ({ ...current, [item.taskClass]: event.target.value }))}
                      >
                        <option value={PLATFORM_VALUE}>
                          {item.platformModel
                            ? `Padrão da plataforma — ${providerLabel(item.platformProvider ?? '')} · ${item.platformModel}`
                            : 'Padrão da plataforma (ainda não configurado)'}
                        </option>
                        {(value.options ?? []).map((option) => (
                          <option key={optionKey(option)} value={optionKey(option)}>
                            {providerLabel(option.provider)} · {option.model}
                          </option>
                        ))}
                      </select>
                      <p className="text-xs text-muted-foreground tabular-nums">
                        {effectiveOption?.price
                          ? `${formatUsdPerMillion(effectiveOption.price.inputPerMillionUsd)} entrada · ${formatUsdPerMillion(effectiveOption.price.outputPerMillionUsd)} saída por 1M tokens`
                          : 'Custo por 1M tokens indisponível no catálogo.'}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
            {!readOnly && onSave && (
              <Button type="button" onClick={save} disabled={saving || !hasChanges}>
                {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Salvar roteamento
              </Button>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── Suas chaves de API (BYOK) ─────────────────────────────────────────────

interface AiByokKeysPanelProps {
  value: WorkspaceAiCredentialStatus[] | null;
  loading?: boolean;
  error?: string | null;
  readOnly?: boolean;
  busyProvider?: string | null;
  onRetry?: () => void;
  onSaveKey?: (provider: string, apiKey: string) => Promise<void>;
  onRemoveKey?: (provider: string) => Promise<void>;
}

export function AiByokKeysPanel({
  value,
  loading = false,
  error = null,
  readOnly = false,
  busyProvider = null,
  onRetry,
  onSaveKey,
  onRemoveKey,
}: AiByokKeysPanelProps) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const saveKey = async (provider: string) => {
    const draft = (drafts[provider] ?? '').trim();
    if (!onSaveKey || readOnly || draft.length < 8) return;
    await onSaveKey(provider, draft);
    setDrafts((current) => ({ ...current, [provider]: '' }));
  };

  return (
    <Card data-testid="ai-byok-keys-panel">
      <CardHeader className="gap-1">
        <CardTitle className="flex items-center gap-2 text-base">
          <KeyRound className="h-4 w-4 text-primary" />Suas chaves de API (BYOK)
        </CardTitle>
        <CardDescription>
          Traga sua própria chave por provedor. A chave é cifrada, write-only e nunca é reexibida —
          apenas os últimos 4 caracteres aparecem como confirmação.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <p>
            <strong>Aviso de responsabilidade.</strong> Com chave própria, o consumo passa a ser
            cobrado diretamente na sua conta do provedor, com os limites e políticas dele. Remover a
            chave volta a operação para a chave da plataforma.
          </p>
        </div>

        {loading && !value ? (
          <div className="flex min-h-24 items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />Carregando estado das credenciais
          </div>
        ) : error ? (
          <div className="flex min-h-24 flex-col items-center justify-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-center">
            <AlertTriangle className="h-5 w-5 text-destructive" />
            <p className="text-sm font-semibold">Não foi possível carregar as credenciais</p>
            <p className="text-xs text-muted-foreground">{error}</p>
            {onRetry && (
              <Button type="button" variant="outline" size="sm" onClick={onRetry}>
                <RotateCw className="mr-2 h-3.5 w-3.5" />Tentar novamente
              </Button>
            )}
          </div>
        ) : !value?.length ? (
          <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
            O estado das credenciais fica visível para quem pode ver integrações.
          </div>
        ) : (
          <div className="space-y-3">
            {value.map((credential) => {
              const active = credential.configured && credential.isActive;
              const inputId = `byok-key-${credential.provider}`;
              const draft = drafts[credential.provider] ?? '';
              const busy = busyProvider === credential.provider;
              return (
                <div key={credential.provider} className="rounded-lg border p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm font-medium">{providerLabel(credential.provider)}</p>
                    {active ? (
                      <Badge variant="outline" className="border-emerald-300 bg-emerald-50 text-2xs font-medium text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300">
                        Chave própria · {credential.apiKeyMasked}
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="text-2xs font-medium text-muted-foreground">
                        Chave da plataforma
                      </Badge>
                    )}
                  </div>
                  <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center">
                    <div className="flex-1">
                      <Label htmlFor={inputId} className="sr-only">
                        Nova chave de API de {providerLabel(credential.provider)}
                      </Label>
                      <Input
                        id={inputId}
                        type="password"
                        autoComplete="off"
                        placeholder="Colar nova chave (nunca será reexibida)"
                        value={draft}
                        disabled={readOnly || busy}
                        onChange={(event) =>
                          setDrafts((current) => ({ ...current, [credential.provider]: event.target.value }))}
                      />
                    </div>
                    {!readOnly && (
                      <div className="flex shrink-0 gap-2">
                        <Button
                          type="button"
                          size="sm"
                          disabled={busy || draft.trim().length < 8 || !onSaveKey}
                          onClick={() => { void saveKey(credential.provider); }}
                        >
                          {busy && <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />}
                          {active ? 'Rotacionar' : 'Salvar chave'}
                        </Button>
                        {active && onRemoveKey && (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={busy}
                            aria-label={`Remover chave de ${providerLabel(credential.provider)}`}
                            onClick={() => { void onRemoveKey(credential.provider); }}
                          >
                            <Trash2 className="mr-1.5 h-3.5 w-3.5" />Remover
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
