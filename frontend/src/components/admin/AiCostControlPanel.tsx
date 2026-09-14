import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ArrowRight, Bot, CheckCircle2, Clock3, Coins, Cpu, Headphones, Loader2, LockKeyhole, PackagePlus, RotateCw } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Switch } from '@/components/ui/switch';
import type {
  AiCostAlertLevel,
  AiCostModelBreakdown,
  AiCostTaskBreakdown,
  AiQuotaState,
  AiTokenPackageCatalog,
  AiTokenModelBreakdown,
  AiTokenTaskBreakdown,
  PlatformAiCostSummary,
  WorkspaceAiCostPolicyUpdate,
  WorkspaceAiTokenSummary,
} from '@/types';

type AiUsageSummary = WorkspaceAiTokenSummary | PlatformAiCostSummary;

interface AiCostControlPanelProps {
  value: AiUsageSummary | null;
  loading?: boolean;
  error?: string | null;
  readOnly?: boolean;
  title?: string;
  description?: string;
  showMonetaryCost?: boolean;
  onRetry?: () => void;
  onSave?: (payload: WorkspaceAiCostPolicyUpdate) => Promise<void>;
  tokenPackages?: AiTokenPackageCatalog | null;
  tokenPackagesLoading?: boolean;
  requestingPackageId?: string | null;
  onRequestPackage?: (packageId: string) => Promise<void>;
  showTokenPackages?: boolean;
}

interface AiCostUsageSummaryProps {
  value: AiUsageSummary | null;
  loading?: boolean;
  error?: boolean;
  onOpen: () => void;
  onRetry?: () => void;
}

function formatInteger(value: number): string {
  return new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 }).format(value || 0);
}

function formatUsd(value: number): string {
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(value || 0);
}

function formatCommercialPrice(value: number, currency: string): string {
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
  }).format(value / 100);
}

function hasMonetaryCost(value: AiUsageSummary): value is PlatformAiCostSummary {
  return 'costUsd' in value;
}

function modelCost(item: AiTokenModelBreakdown | AiCostModelBreakdown): number | null {
  return 'costUsd' in item ? item.costUsd : null;
}

function taskCost(item: AiTokenTaskBreakdown | AiCostTaskBreakdown): number | null {
  return 'costUsd' in item ? item.costUsd : null;
}

function quotaCopy(state: AiQuotaState): { label: string; detail: string } {
  if (state === 'configured') return { label: 'Franquia configurada', detail: 'Teto mensal contratual ativo' };
  if (state === 'local_legacy') return { label: 'Limite local de simulação', detail: 'APIs externas continuam bloqueadas sem plano tipado' };
  if (state === 'not_applicable') return { label: 'Visão global', detail: 'Consolidado de todas as contas' };
  return { label: 'Franquia pendente', detail: 'Chamadas externas bloqueadas até a configuração' };
}

function alertCopy(level: AiCostAlertLevel, quotaState: AiQuotaState): { label: string; className: string } {
  if (quotaState === 'missing') return { label: 'Franquia pendente', className: 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200' };
  if (quotaState === 'local_legacy') return { label: 'Somente simulação local', className: 'border-blue-300 bg-blue-50 text-blue-800 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-200' };
  if (quotaState === 'not_applicable') return { label: 'Visão global', className: 'border-border bg-muted text-muted-foreground' };
  if (level === 'blocked') return { label: 'Limite atingido', className: 'border-red-300 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300' };
  if (level === 'critical') return { label: 'Uso crítico', className: 'border-red-300 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300' };
  if (level === 'warning') return { label: 'Atenção ao consumo', className: 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200' };
  if (level === 'none') return { label: 'Sem consumo medido', className: 'border-border bg-muted text-muted-foreground' };
  return { label: 'Consumo saudável', className: 'border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300' };
}

export function AiCostUsageSummary({
  value,
  loading = false,
  error = false,
  onOpen,
  onRetry,
}: AiCostUsageSummaryProps) {
  const percent = Math.max(0, Math.min(100, Number(value?.usedPercent ?? 0)));
  const hasLimit = value?.monthlyLimit != null;
  const quota = quotaCopy(value?.quotaState ?? 'missing');

  return (
    <Card data-testid="ai-cost-usage-summary" className="border-primary/25">
      <CardContent className="space-y-4 py-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="flex items-center gap-2 text-sm font-semibold"><Coins className="h-4 w-4 text-primary" />Consumo de IA neste mês</p>
            <p className="mt-1 text-xs text-muted-foreground">Tokens usados, saldo e limite mensal da conta em uma leitura direta.</p>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={error && onRetry ? onRetry : onOpen}>
            {error ? <RotateCw className="mr-2 h-3.5 w-3.5" /> : <ArrowRight className="mr-2 h-3.5 w-3.5" />}
            {error ? 'Tentar novamente' : 'Abrir uso de IA'}
          </Button>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3 text-xs tabular-nums">
            <span>{loading ? 'Carregando consumo persistido' : error ? 'Consumo temporariamente indisponível' : hasLimit ? `${formatInteger(value?.totalTokens ?? 0)} de ${formatInteger(value?.monthlyLimit ?? 0)} tokens` : `${formatInteger(value?.totalTokens ?? 0)} tokens usados · ${quota.label.toLocaleLowerCase('pt-BR')}`}</span>
            <strong>{loading ? '...' : error ? 'Erro' : hasLimit ? `${percent.toFixed(1).replace('.', ',')}%` : quota.label}</strong>
          </div>
          <Progress value={hasLimit ? percent : 0} className="h-2.5" aria-label="Uso mensal de IA" />
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground tabular-nums">
            <span>{value ? `${formatInteger(value.totalRuns)} execuções registradas` : error ? 'Os dados não foram substituídos por estimativas.' : 'Aguardando a medição local.'}</span>
            {!hasLimit && !loading && !error && <span className="font-medium text-amber-700 dark:text-amber-300">{quota.detail}</span>}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export function AiCostControlPanel({
  value,
  loading = false,
  error = null,
  readOnly = false,
  title,
  description,
  showMonetaryCost = false,
  onRetry,
  onSave,
  tokenPackages = null,
  tokenPackagesLoading = false,
  requestingPackageId = null,
  onRequestPackage,
  showTokenPackages = true,
}: AiCostControlPanelProps) {
  const [monthlyLimit, setMonthlyLimit] = useState('');
  const [alertAtPct, setAlertAtPct] = useState('80');
  const [hardLimit, setHardLimit] = useState(false);
  const [saving, setSaving] = useState(false);
  const resolvedTitle = title ?? (showMonetaryCost ? 'Custo e orçamento de IA' : 'Uso e limite de tokens de IA');
  const resolvedDescription = description ?? (showMonetaryCost
    ? 'Consumo real do mês, custos internos, limites da conta e bloqueios antes de qualquer chamada externa.'
    : 'Consumo real do mês, saldo, limite da conta e bloqueios antes de qualquer chamada externa.');

  useEffect(() => {
    setMonthlyLimit(value?.policyMonthlyLimit == null ? '' : String(value.policyMonthlyLimit));
    setAlertAtPct(String(value?.alertAtPct ?? 80));
    setHardLimit(Boolean(value?.hardLimit));
  }, [value?.alertAtPct, value?.hardLimit, value?.policyMonthlyLimit]);

  const percent = Math.max(0, Math.min(100, Number(value?.usedPercent ?? 0)));
  const quota = quotaCopy(value?.quotaState ?? 'missing');
  const alert = alertCopy(value?.alertLevel ?? 'none', value?.quotaState ?? 'missing');
  const allExternalGatesClosed = value
    ? !value.gates.modelApisEnabled && !value.gates.onlineApproved && !value.gates.externalActionsEnabled
    : false;
  const topModels = useMemo(() => (value?.byModel ?? []).slice(0, 6), [value?.byModel]);
  const topTasks = useMemo(() => (value?.byTask ?? []).slice(0, 6), [value?.byTask]);
  const policyValidation = useMemo(() => {
    const parsedLimit = monthlyLimit.trim() === '' ? null : Number(monthlyLimit);
    const parsedAlert = Number(alertAtPct);
    if (parsedLimit != null && (!Number.isInteger(parsedLimit) || parsedLimit < 0 || parsedLimit > 2_147_483_647)) {
      return 'O limite deve ser um número inteiro entre 0 e 2.147.483.647 tokens.';
    }
    if (!Number.isInteger(parsedAlert) || parsedAlert < 1 || parsedAlert > 100) {
      return 'O alerta deve ficar entre 1% e 100%.';
    }
    return null;
  }, [alertAtPct, monthlyLimit]);

  const save = async () => {
    if (!onSave || readOnly || policyValidation) return;
    const parsedLimit = monthlyLimit.trim() === '' ? null : Number(monthlyLimit);
    const parsedAlert = Number(alertAtPct);
    setSaving(true);
    try {
      await onSave({ monthlyLimit: parsedLimit, alertAtPct: parsedAlert, hardLimit });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card data-testid="ai-cost-control-panel">
      <CardHeader className="gap-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <CardTitle className="flex items-center gap-2 text-base"><Coins className="h-4 w-4 text-primary" />{resolvedTitle}</CardTitle>
            <CardDescription className="mt-1">{resolvedDescription}</CardDescription>
          </div>
          <Badge variant="outline" className={alert.className}>{alert.label}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {loading && !value ? (
          <div className="flex min-h-36 items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />Carregando consumo persistido
          </div>
        ) : error ? (
          <div className="flex min-h-36 flex-col items-center justify-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-5 text-center">
            <AlertTriangle className="h-5 w-5 text-destructive" />
            <div><p className="text-sm font-semibold">Não foi possível carregar o consumo de IA</p><p className="mt-1 text-xs text-muted-foreground">{error}</p></div>
            {onRetry && <Button type="button" variant="outline" size="sm" onClick={onRetry}><RotateCw className="mr-2 h-3.5 w-3.5" />Tentar novamente</Button>}
          </div>
        ) : !value ? (
          <div className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground">Nenhuma medição de IA disponível para este escopo.</div>
        ) : (
          <>
            <div className={showMonetaryCost ? 'grid gap-3 md:grid-cols-2 xl:grid-cols-4' : 'grid gap-3 md:grid-cols-3'}>
              <div className="rounded-lg border p-3"><p className="text-xs text-muted-foreground">Tokens no período</p><p className="mt-1 text-2xl font-bold tabular-nums">{formatInteger(value.totalTokens)}</p><p className="mt-1 text-xs text-muted-foreground tabular-nums">{formatInteger(value.inputTokens)} entrada · {formatInteger(value.outputTokens)} saída</p></div>
              {showMonetaryCost && hasMonetaryCost(value) && <div className="rounded-lg border p-3"><p className="text-xs text-muted-foreground">Custo interno medido</p><p className="mt-1 text-2xl font-bold tabular-nums">{formatUsd(value.costUsd)}</p><p className="mt-1 text-xs text-muted-foreground">Visível somente ao titular do SaaS</p></div>}
              <div className="rounded-lg border p-3"><p className="text-xs text-muted-foreground">Execuções</p><p className="mt-1 text-2xl font-bold tabular-nums">{formatInteger(value.totalRuns)}</p><p className="mt-1 text-xs text-muted-foreground">Agentes e copilotos registrados</p></div>
              <div className="rounded-lg border p-3"><p className="text-xs text-muted-foreground">Saldo de tokens</p><p className="mt-1 text-2xl font-bold tabular-nums">{value.remainingTokens == null ? quota.label : formatInteger(value.remainingTokens)}</p><p className="mt-1 text-xs text-muted-foreground">{value.remainingTokens == null ? quota.detail : value.hardLimit ? 'Bloqueio automático ativo' : 'Somente alerta'}</p></div>
            </div>

            {!showMonetaryCost && value.quotaState === 'configured' && (
              <div className="flex items-start gap-2 rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-xs text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /><p><strong>Franquia de IA incluída até o limite de tokens.</strong> Não há custo variável de modelo para esta conta dentro da franquia; somente pacotes extras publicados podem exibir preço comercial e sempre dependem da liberação do suporte.</p></div>
            )}
            {!showMonetaryCost && value.quotaState !== 'configured' && (
              <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><p><strong>{quota.label}.</strong> {quota.detail}. Nenhuma chamada de modelo externo é permitida enquanto esse gate estiver pendente.</p></div>
            )}

            <div className="space-y-2 rounded-lg border p-4">
              <div className="flex items-center justify-between gap-3 text-sm"><span>Uso do limite mensal de tokens</span><strong>{value.monthlyLimit == null ? quota.label : `${percent.toFixed(1).replace('.', ',')}%`}</strong></div>
              <Progress value={value.monthlyLimit == null ? 0 : percent} className="h-2" />
              <p className="text-xs text-muted-foreground">Período {new Date(value.periodStart).toLocaleDateString('pt-BR')} a {new Date(value.periodEnd).toLocaleDateString('pt-BR')}.</p>
            </div>

            <div className="grid gap-4 xl:grid-cols-[0.9fr,1.1fr]">
              <div className="space-y-3 rounded-lg border p-4">
                <div><p className="text-sm font-semibold">Política da conta</p><p className="text-xs text-muted-foreground">O plano mantém o teto contratual em tokens. O limite local restringe o uso quando o bloqueio rígido está ativo; sem ele, funciona como alerta.</p></div>
                <div className="space-y-1.5"><Label htmlFor="ai-monthly-limit">Limite mensal de tokens</Label><Input id="ai-monthly-limit" inputMode="numeric" type="number" min={0} max={2_147_483_647} step={1} value={monthlyLimit} onChange={(event) => setMonthlyLimit(event.target.value)} placeholder="Usar a franquia do plano" disabled={readOnly || saving} aria-invalid={Boolean(policyValidation)} /></div>
                <div className="space-y-1.5"><Label htmlFor="ai-alert-percent">Alertar em (%)</Label><Input id="ai-alert-percent" inputMode="numeric" type="number" min={1} max={100} step={1} value={alertAtPct} onChange={(event) => setAlertAtPct(event.target.value)} disabled={readOnly || saving} aria-invalid={Boolean(policyValidation)} /></div>
                <label className="flex items-center justify-between gap-3 rounded-lg border p-3"><span><span className="block text-sm font-medium">Bloqueio rígido</span><span className="block text-xs text-muted-foreground">Impede novas reservas quando o teto efetivo acaba.</span></span><Switch checked={hardLimit} onCheckedChange={setHardLimit} disabled={readOnly || saving} /></label>
                {policyValidation && <p role="alert" className="text-xs text-destructive">{policyValidation}</p>}
                {!readOnly && onSave && <Button type="button" onClick={save} disabled={saving || Boolean(policyValidation)}>{saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Salvar política</Button>}
              </div>

              <div className="space-y-3">
                <div className="rounded-lg border p-4">
                  <div className="flex items-center gap-2"><LockKeyhole className="h-4 w-4 text-emerald-600" /><p className="text-sm font-semibold">Gates externos</p></div>
                  <div className="mt-3 grid gap-2 sm:grid-cols-3">
                    {[
                      ['APIs de modelos', value.gates.modelApisEnabled],
                      ['Operação online', value.gates.onlineApproved],
                      ['Ações externas', value.gates.externalActionsEnabled],
                    ].map(([label, enabled]) => <div key={String(label)} className="flex items-center gap-2 rounded-lg bg-muted px-3 py-2 text-xs">{enabled ? <AlertTriangle className="h-3.5 w-3.5 text-red-600" /> : <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />}<span>{label}: {enabled ? 'aberto' : 'fechado'}</span></div>)}
                  </div>
                  <p className="mt-3 text-xs text-muted-foreground">{allExternalGatesClosed ? 'Ambiente local protegido: nenhuma chamada externa foi liberada.' : 'Há gate externo aberto. Revise antes de continuar.'}</p>
                </div>

                <div className="rounded-lg border p-4">
                  <div className="flex items-center gap-2"><Bot className="h-4 w-4 text-primary" /><p className="text-sm font-semibold">Consumo por tipo de tarefa</p></div>
                  <div className="mt-3 space-y-2">
                    {topTasks.length ? topTasks.map((item) => <div key={item.taskClass} className="flex items-center justify-between gap-3 border-b pb-2 text-xs last:border-0 last:pb-0"><span><span className="block font-medium capitalize">{item.taskClass}</span><span className="text-muted-foreground">{formatInteger(item.runs)} execuções</span></span><span className="shrink-0 text-right">{showMonetaryCost && taskCost(item) != null && <span className="block font-medium">{formatUsd(taskCost(item) ?? 0)}</span>}<span className="text-muted-foreground">{formatInteger(item.inputTokens + item.outputTokens)} tokens</span></span></div>) : <div className="flex items-center gap-2 text-xs text-muted-foreground"><Bot className="h-4 w-4" />Nenhuma tarefa de IA medida no período.</div>}
                  </div>
                </div>

                <div className="rounded-lg border p-4">
                  <div className="flex items-center gap-2"><Cpu className="h-4 w-4 text-primary" /><p className="text-sm font-semibold">Modelos e provedores</p></div>
                  <div className="mt-3 space-y-2">
                    {topModels.length ? topModels.map((item) => <div key={`${item.provider}:${item.model}`} className="flex items-center justify-between gap-3 border-b pb-2 text-xs last:border-0 last:pb-0"><span className="min-w-0"><span className="block truncate font-medium">{item.model}</span><span className="text-muted-foreground">{item.provider} · {formatInteger(item.runs)} execuções</span></span><span className="shrink-0 text-right">{showMonetaryCost && modelCost(item) != null && <span className="block font-medium">{formatUsd(modelCost(item) ?? 0)}</span>}<span className="text-muted-foreground">{formatInteger(item.inputTokens + item.outputTokens)} tokens</span></span></div>) : <div className="flex items-center gap-2 text-xs text-muted-foreground"><Bot className="h-4 w-4" />Nenhum modelo externo utilizado no período.</div>}
                  </div>
                </div>
                {!showMonetaryCost && showTokenPackages && (
                  <div data-testid="ai-token-package-support" className="rounded-lg border border-primary/25 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2"><PackagePlus className="h-4 w-4 text-primary" /><p className="text-sm font-semibold">Pacotes extras de tokens</p></div>
                      <Badge variant="outline">Liberação pelo suporte</Badge>
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">A solicitação abre um pedido persistente: somente o suporte confirma e libera os tokens. Esta tela não compra nem ativa nada automaticamente.</p>

                    {tokenPackagesLoading ? (
                      <div className="mt-3 flex items-center gap-2 rounded-lg bg-muted p-3 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Carregando catálogo publicado</div>
                    ) : tokenPackages?.packages.length ? (
                      <div className="mt-3 grid gap-2">
                        {tokenPackages.packages.map((item) => {
                          const activeRequest = tokenPackages.requests.find((request) => request.packageId === item.id && ['requested', 'approved'].includes(request.status));
                          const awaitingSupport = activeRequest?.status === 'requested';
                          const alreadyGranted = activeRequest?.status === 'approved';
                          return (
                            <div key={item.id} className="flex flex-col gap-3 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between">
                              <div className="min-w-0">
                                <p className="text-sm font-medium">{item.name}</p>
                                <p className="mt-0.5 text-xs text-muted-foreground">{formatInteger(item.tokenQuantity)} tokens{item.commercialPriceCents == null ? '' : ` · ${formatCommercialPrice(item.commercialPriceCents, item.currency)}`}</p>
                                {item.description && <p className="mt-1 text-xs text-muted-foreground">{item.description}</p>}
                              </div>
                              <Button
                                type="button"
                                size="sm"
                                variant={alreadyGranted ? 'outline' : 'default'}
                                disabled={Boolean(requestingPackageId) || awaitingSupport || alreadyGranted || !onRequestPackage}
                                onClick={() => { void onRequestPackage?.(item.id); }}
                              >
                                {requestingPackageId === item.id ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : awaitingSupport ? <Clock3 className="mr-2 h-3.5 w-3.5" /> : alreadyGranted ? <CheckCircle2 className="mr-2 h-3.5 w-3.5" /> : <Headphones className="mr-2 h-3.5 w-3.5" />}
                                {awaitingSupport ? 'Aguardando suporte' : alreadyGranted ? 'Liberado neste mês' : 'Solicitar ao suporte'}
                              </Button>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <div className="mt-3 rounded-lg border border-dashed p-3 text-xs text-muted-foreground">Nenhum pacote comercial foi publicado pelo superadministrador.</div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
