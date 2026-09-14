import { useMemo, useState } from 'react';
import { Cable, Clipboard, ExternalLink, Landmark, Network, PlugZap, Workflow } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { PublicApiTokensPanel } from '@/components/admin/PublicApiTokensPanel';
import { FinanceUpgradePauseNotice } from '@/components/finance/FinanceUpgradePauseNotice';
import { useToast } from '@/hooks/use-toast';
import type {
  IntegrationCatalogProvider,
  IntegrationConnection,
  OpenApiSurface,
  PublicApiRequestLog,
  PublicApiScope,
  WorkspacePublicApiToken,
} from '@/types';

const CATALOG_CATEGORY_LABELS: Record<string, string> = {
  finance: 'Financeiro',
  signature: 'Assinatura eletrônica',
  open_platform: 'API aberta / Custom',
  automation: 'Automação (no-code)',
  ads: 'Ads e mensuração',
  tracking: 'Tracking e analytics',
  channels: 'Canais de mensagem',
};

function catalogCategoryLabel(category: string) {
  return CATALOG_CATEGORY_LABELS[category] ?? category.replaceAll('_', ' ');
}

type CatalogConnectionStatus = 'connected' | 'pending' | 'not_configured';

function catalogConnectionStatus(
  providerKey: string,
  integrations: IntegrationConnection[],
): CatalogConnectionStatus {
  const match = integrations.find((item) => item.provider === providerKey);
  if (!match) return 'not_configured';
  if (match.status === 'connected') return 'connected';
  return 'pending';
}

const catalogStatusLabel: Record<CatalogConnectionStatus, string> = {
  connected: 'Conectado',
  pending: 'Pendente',
  not_configured: 'Não configurado',
};

const catalogStatusVariant: Record<CatalogConnectionStatus, 'default' | 'secondary' | 'outline'> = {
  connected: 'default',
  pending: 'secondary',
  not_configured: 'outline',
};

interface FinanceIntegrationsPanelProps {
  integrations: IntegrationConnection[];
  catalog: IntegrationCatalogProvider[];
  openApiSurface: OpenApiSurface | null;
  publicApiTokens?: WorkspacePublicApiToken[];
  publicApiLogs?: PublicApiRequestLog[];
  loading?: boolean;
  readOnly?: boolean;
  onSave: (payload: Partial<IntegrationConnection> & { id?: string }) => Promise<void>;
  onCreatePublicApiToken?: (payload: {
    label: string;
    scopes: PublicApiScope[];
    rateLimitPerMinute: number;
    expiresAt?: string | null;
  }) => Promise<{ plainTextToken: string }>;
  onRevokePublicApiToken?: (tokenId: string) => Promise<void>;
}

type IntegrationDraft = {
  provider: IntegrationConnection['provider'];
  label: string;
  baseUrl: string;
  webhookUrl: string;
  externalAccountId: string;
  apiVersion: string;
  capabilities: string;
  notes: string;
};

const defaultDraft: IntegrationDraft = {
  provider: 'asaas',
  label: '',
  baseUrl: '',
  webhookUrl: '',
  externalAccountId: '',
  apiVersion: '',
  capabilities: 'cobranca, webhooks, conciliacao',
  notes: '',
};

export function FinanceIntegrationsPanel({
  integrations,
  catalog,
  openApiSurface,
  publicApiTokens = [],
  publicApiLogs = [],
  loading,
  readOnly = false,
  onSave,
  onCreatePublicApiToken,
  onRevokePublicApiToken,
}: FinanceIntegrationsPanelProps) {
  const [draft, setDraft] = useState<IntegrationDraft>(defaultDraft);
  const [saving, setSaving] = useState(false);
  const [howToConnectProvider, setHowToConnectProvider] = useState<IntegrationCatalogProvider | null>(null);
  const { toast } = useToast();

  const selectedProvider = useMemo(
    () => catalog.find((provider) => provider.key === draft.provider),
    [catalog, draft.provider],
  );

  const catalogByCategory = useMemo(() => {
    const groups = new Map<string, IntegrationCatalogProvider[]>();
    for (const provider of catalog) {
      const list = groups.get(provider.category) ?? [];
      list.push(provider);
      groups.set(provider.category, list);
    }
    return [...groups.entries()].sort(([a], [b]) => catalogCategoryLabel(a).localeCompare(catalogCategoryLabel(b), 'pt-BR'));
  }, [catalog]);

  const copyWebhookUrl = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url);
      toast({ title: 'URL do webhook copiada.' });
    } catch {
      toast({ variant: 'destructive', title: 'Não foi possível copiar', description: 'Copie manualmente a URL exibida.' });
    }
  };

  const grouped = useMemo(() => ({
    asaas: integrations.filter((item) => item.provider === 'asaas'),
    contaAzul: integrations.filter((item) => item.provider === 'conta_azul'),
    zapsign: integrations.filter((item) => item.provider === 'zapsign'),
    docusign: integrations.filter((item) => item.provider === 'docusign'),
    custom: integrations.filter((item) => item.provider === 'custom'),
  }), [integrations]);

  const handleSave = async () => {
    if (!draft.label) return;
    setSaving(true);
    try {
      await onSave({
        provider: draft.provider,
        label: draft.label,
        status: 'pending',
        baseUrl: draft.baseUrl || null,
        webhookUrl: draft.webhookUrl || null,
        externalAccountId: draft.externalAccountId || null,
        apiVersion: draft.apiVersion || null,
        capabilities: draft.capabilities.split(',').map((item) => item.trim()).filter(Boolean),
        notes: draft.notes || null,
      });
      setDraft(defaultDraft);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          Carregando integrações financeiras...
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <FinanceUpgradePauseNotice showLaunchPaths />

      <div className="grid gap-3 md:grid-cols-5">
        <Card>
          <CardContent className="flex items-center gap-3 py-5">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
              <Landmark className="h-4 w-4 text-primary" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-1.5">
                <p className="text-xs uppercase tracking-wide text-muted-foreground">Asaas</p>
                <Badge variant="secondary" className="text-xs">Em breve</Badge>
              </div>
              <p className="text-2xl font-bold tabular-nums">{grouped.asaas.length}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 py-5">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
              <PlugZap className="h-4 w-4 text-primary" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-1.5">
                <p className="text-xs uppercase tracking-wide text-muted-foreground">Conta Azul</p>
                <Badge variant="secondary" className="text-xs">Em breve</Badge>
              </div>
              <p className="text-2xl font-bold tabular-nums">{grouped.contaAzul.length}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 py-5">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
              <Cable className="h-4 w-4 text-primary" />
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">ZapSign</p>
              <p className="text-2xl font-bold tabular-nums">{grouped.zapsign.length}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 py-5">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
              <ExternalLink className="h-4 w-4 text-primary" />
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">DocuSign</p>
              <p className="text-2xl font-bold tabular-nums">{grouped.docusign.length}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 py-5">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
              <Network className="h-4 w-4 text-primary" />
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">API aberta/custom</p>
              <p className="text-2xl font-bold tabular-nums">{grouped.custom.length}</p>
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.05fr,0.95fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <Cable className="h-4 w-4" />
              Conexões financeiras
            </CardTitle>
            <CardDescription>
              Asaas e Conta Azul permanecem planejados. ZapSign, DocuSign e a conexão custom continuam preservados; um registro local não comprova conexão externa.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Conexão</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Conta</TableHead>
                  <TableHead>Webhook</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {integrations.map((integration) => {
                  const isPausedFinancialProvider = ['asaas', 'conta_azul'].includes(integration.provider);
                  return (
                    <TableRow key={integration.id}>
                      <TableCell>
                        <div>
                          <p className="text-sm font-medium">{integration.label}</p>
                          <p className="text-xs text-muted-foreground">{integration.provider}</p>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={isPausedFinancialProvider ? 'secondary' : integration.status === 'connected' ? 'default' : integration.status === 'error' ? 'destructive' : 'secondary'}>
                          {isPausedFinancialProvider ? 'Em breve' : integration.status}
                        </Badge>
                        {isPausedFinancialProvider && (
                          <p className="mt-1 text-2xs text-muted-foreground">estado local preservado: {integration.status}</p>
                        )}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{integration.externalAccountId ?? '—'}</TableCell>
                      <TableCell className="max-w-[220px] truncate text-xs text-muted-foreground">{integration.webhookUrl ?? '—'}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <ExternalLink className="h-4 w-4" />
              Novo plano local de integração
            </CardTitle>
            <CardDescription>
              Registre a rota pretendida sem credenciais, teste de conta ou chamada ao provedor.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Fornecedor</Label>
                <Select value={draft.provider} onValueChange={(value) => setDraft((current) => ({ ...current, provider: value as IntegrationConnection['provider'] }))} disabled={readOnly}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="asaas">Asaas</SelectItem>
                    <SelectItem value="conta_azul">Conta Azul</SelectItem>
                    <SelectItem value="zapsign">ZapSign</SelectItem>
                    <SelectItem value="docusign">DocuSign</SelectItem>
                    <SelectItem value="custom">Pluga / Zapier / API aberta</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Estado do registro</Label>
                <div className="flex min-h-10 items-center justify-between gap-3 rounded-md border bg-muted/30 px-3">
                  <span className="text-sm">Planejamento local</span>
                  <Badge variant="secondary">Pendente</Badge>
                </div>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>Nome do plano local</Label>
              <Input value={draft.label} onChange={(event) => setDraft((current) => ({ ...current, label: event.target.value }))} placeholder="Ex.: Asaas direto - sandbox futuro" disabled={readOnly} />
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Base URL planejada</Label>
                <Input
                  value={draft.baseUrl}
                  onChange={(event) => setDraft((current) => ({ ...current, baseUrl: event.target.value }))}
                  placeholder={selectedProvider?.setupHint.recommendedBaseUrl ?? 'https://api...'}
                  disabled={readOnly}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Webhook planejado</Label>
                <Input value={draft.webhookUrl} onChange={(event) => setDraft((current) => ({ ...current, webhookUrl: event.target.value }))} placeholder="https://..." disabled={readOnly} />
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Conta externa</Label>
                <Input value={draft.externalAccountId} onChange={(event) => setDraft((current) => ({ ...current, externalAccountId: event.target.value }))} disabled={readOnly} />
              </div>
              <div className="space-y-1.5">
                <Label>Versão da API</Label>
                <Input
                  value={draft.apiVersion}
                  onChange={(event) => setDraft((current) => ({ ...current, apiVersion: event.target.value }))}
                  placeholder={selectedProvider?.setupHint.recommendedApiVersion ?? 'v1'}
                  disabled={readOnly}
                />
              </div>
            </div>
            {selectedProvider && (
              <div className="rounded-md border border-dashed p-3 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-medium">{selectedProvider.label}: referência da fase futura</p>
                  <Badge variant="secondary">Em breve</Badge>
                </div>
                <div className="mt-2 grid gap-2 md:grid-cols-2">
                  <div>
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">Autenticação</p>
                    <p>{selectedProvider.setupHint.authType}</p>
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">Segurança do webhook</p>
                    <p>{selectedProvider.setupHint.webhookSecurity}</p>
                  </div>
                </div>
                <div className="mt-3">
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Campos mínimos em settings</p>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {selectedProvider.setupHint.requiredSettings.length ? (
                      selectedProvider.setupHint.requiredSettings.map((item) => (
                        <Badge key={item} variant="outline">{item}</Badge>
                      ))
                    ) : (
                      <Badge variant="outline">sem obrigatórios</Badge>
                    )}
                  </div>
                </div>
                <div className="mt-3 space-y-1">
                  {selectedProvider.setupHint.setupSteps.map((step, index) => (
                    <p key={step} className="text-xs text-muted-foreground">{index + 1}. {step}</p>
                  ))}
                </div>
              </div>
            )}
            <div className="space-y-1.5">
              <Label>Capacidades planejadas</Label>
              <Input value={draft.capabilities} onChange={(event) => setDraft((current) => ({ ...current, capabilities: event.target.value }))} placeholder="cobranca, webhooks, conciliacao" disabled={readOnly} />
            </div>
            <div className="space-y-1.5">
              <Label>Notas operacionais</Label>
              <Textarea value={draft.notes} onChange={(event) => setDraft((current) => ({ ...current, notes: event.target.value }))} disabled={readOnly} />
            </div>
            <div className="flex justify-end">
              <Button onClick={handleSave} disabled={saving || !draft.label || readOnly}>
                {saving ? 'Salvando plano...' : readOnly ? 'Somente leitura' : 'Salvar plano local'}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Workflow className="h-4 w-4" />
            Catálogo completo de integrações
          </CardTitle>
          <CardDescription>
            Todos os providers suportados, agrupados por categoria, com o passo a passo real de conexão. Nenhum clique aqui chama o provedor externo.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {catalogByCategory.map(([category, providers]) => (
            <div key={category}>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{catalogCategoryLabel(category)}</p>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {providers.map((provider) => {
                  const status = catalogConnectionStatus(provider.key, integrations);
                  return (
                    <div key={provider.key} className="flex flex-col justify-between rounded-md border p-3">
                      <div>
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-sm font-medium">{provider.label}</p>
                          <Badge variant={catalogStatusVariant[status]}>{catalogStatusLabel[status]}</Badge>
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground">{provider.description}</p>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => setHowToConnectProvider(provider)}
                        >
                          Como conectar
                        </Button>
                        {provider.docsUrl && (
                          <Button type="button" variant="ghost" size="sm" asChild>
                            <a href={provider.docsUrl} target="_blank" rel="noreferrer">
                              Documentação oficial
                              <ExternalLink className="ml-1.5 h-3 w-3" />
                            </a>
                          </Button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <Sheet open={Boolean(howToConnectProvider)} onOpenChange={(open) => { if (!open) setHowToConnectProvider(null); }}>
        <SheetContent className="overflow-y-auto sm:max-w-lg">
          {howToConnectProvider && (
            <>
              <SheetHeader>
                <SheetTitle>Como conectar · {howToConnectProvider.label}</SheetTitle>
                <SheetDescription>{howToConnectProvider.description}</SheetDescription>
              </SheetHeader>
              <div className="mt-5 space-y-5">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Passo a passo</p>
                  <ol className="mt-2 space-y-2">
                    {howToConnectProvider.setupHint.setupSteps.map((step, index) => (
                      <li key={step} className="grid grid-cols-[24px_1fr] gap-2 text-sm">
                        <span className="grid h-6 w-6 place-items-center rounded-full bg-secondary text-xs font-semibold">{index + 1}</span>
                        <span>{step}</span>
                      </li>
                    ))}
                  </ol>
                </div>

                {howToConnectProvider.setupHint.requiredSettings.length > 0 && (
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Campos obrigatórios</p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {howToConnectProvider.setupHint.requiredSettings.map((item) => (
                        <Badge key={item} variant="outline">{item}</Badge>
                      ))}
                    </div>
                  </div>
                )}

                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">URL do webhook</p>
                  <div className="mt-2 flex min-w-0 items-center gap-2 rounded-md border bg-muted/30 p-2">
                    <code className="min-w-0 flex-1 truncate text-xs">{howToConnectProvider.defaultWebhookPath}</code>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 shrink-0"
                      aria-label="Copiar URL do webhook"
                      onClick={() => void copyWebhookUrl(howToConnectProvider.defaultWebhookPath)}
                    >
                      <Clipboard className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>

                <div className="flex flex-wrap gap-2">
                  {howToConnectProvider.docsUrl && (
                    <Button asChild variant="outline" size="sm">
                      <a href={howToConnectProvider.docsUrl} target="_blank" rel="noreferrer">
                        Documentação oficial
                        <ExternalLink className="ml-1.5 h-3 w-3" />
                      </a>
                    </Button>
                  )}
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>

      <div className="grid gap-4 xl:grid-cols-[0.95fr,1.05fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Catálogo preservado para o upgrade</CardTitle>
            <CardDescription>
              Providers previstos pelo produto, mantidos como referência sem ativação financeira externa nesta fase.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {catalog.map((provider) => (
              <div key={provider.key} className="rounded-md border p-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium">{provider.label}</p>
                    <p className="text-xs text-muted-foreground">{provider.description}</p>
                  </div>
                  <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
                    <Badge variant="secondary">{provider.category}</Badge>
                    {['asaas', 'conta_azul'].includes(provider.key) && <Badge variant="outline">Em breve</Badge>}
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {provider.capabilities.map((capability) => (
                    <Badge key={capability} variant="outline">{capability}</Badge>
                  ))}
                </div>
                <div className="mt-3 space-y-1 text-xs text-muted-foreground">
                  <p><span className="font-medium text-foreground">Webhook:</span> {provider.defaultWebhookPath}</p>
                  <p><span className="font-medium text-foreground">Auth:</span> {provider.setupHint.authType}</p>
                  <p><span className="font-medium text-foreground">Segurança:</span> {provider.setupHint.webhookSecurity}</p>
                  {provider.setupHint.requiredSettings.length > 0 && (
                    <p><span className="font-medium text-foreground">Settings:</span> {provider.setupHint.requiredSettings.join(', ')}</p>
                  )}
                  {provider.docsUrl && (
                    <p><span className="font-medium text-foreground">Docs:</span> {provider.docsUrl}</p>
                  )}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Contrato local da API do workspace</CardTitle>
            <CardDescription>
              Superfície local preservada para a futura conexão direta, Pluga, Zapier e demais sistemas, sem chamadas externas automáticas.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {openApiSurface ? (
              <>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-md border p-3">
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">Base URL</p>
                    <p className="mt-1 break-all text-sm font-medium">{openApiSurface.baseUrl}</p>
                  </div>
                  <div className="rounded-md border p-3">
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">Webhook base</p>
                    <p className="mt-1 break-all text-sm font-medium">{openApiSurface.webhookBaseUrl}</p>
                  </div>
                  <div className="rounded-md border p-3">
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">OpenAPI</p>
                    <p className="mt-1 break-all text-sm font-medium">{openApiSurface.openapiUrl}</p>
                  </div>
                  <div className="rounded-md border p-3">
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">Docs</p>
                    <p className="mt-1 break-all text-sm font-medium">{openApiSurface.docsUrl ?? 'Indisponível em produção'}</p>
                  </div>
                </div>

                <div className="space-y-2">
                  {openApiSurface.resources.map((resource) => (
                    <div key={resource.key} className="rounded-md border p-3">
                      <div className="flex items-center gap-2">
                        <Badge>{resource.method}</Badge>
                        <p className="text-sm font-medium">{resource.label}</p>
                      </div>
                      <p className="mt-1 break-all text-xs text-muted-foreground">{resource.path}</p>
                      <p className="mt-2 text-xs text-muted-foreground">{resource.description}</p>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">A superfície aberta da API ainda não foi carregada.</p>
            )}
          </CardContent>
        </Card>
      </div>

      {onCreatePublicApiToken && onRevokePublicApiToken && (
        <PublicApiTokensPanel
          baseUrl={openApiSurface?.baseUrl ?? null}
          tokens={publicApiTokens}
          logs={publicApiLogs}
          loading={loading}
          readOnly={readOnly}
          onCreate={onCreatePublicApiToken}
          onRevoke={onRevokePublicApiToken}
        />
      )}
    </div>
  );
}
