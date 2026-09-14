import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Activity, AlertTriangle, CheckCircle2, ChevronDown, ExternalLink, RefreshCw, Search, ShieldAlert } from 'lucide-react';
import { Link } from 'react-router-dom';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Input } from '@/components/ui/input';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import type { WhatsAppMetaOperations } from '@/services/whatsapp';

const FIELD_LABELS: Record<string, string> = {
  account_alerts: 'Alertas da conta', account_review_update: 'Análise da conta', account_settings_update: 'Configurações da conta',
  account_update: 'Atualizações da conta', automatic_events: 'Eventos automáticos', business_capability_update: 'Capacidades do negócio',
  business_status_update: 'Status do negócio', business_username_updates: 'Nome de usuário do negócio', calls: 'Chamadas', flows: 'WhatsApp Flows',
  group_lifecycle_update: 'Ciclo de vida de grupos', group_participants_update: 'Participantes de grupos', group_settings_update: 'Configurações de grupos',
  group_status_update: 'Status de grupos', history: 'Histórico de mensagens', message_echoes: 'Mensagens espelhadas',
  message_template_components_update: 'Componentes de templates', message_template_quality_update: 'Qualidade de templates',
  message_template_status_update: 'Status de templates', messages: 'Mensagens', messaging_handovers: 'Transferências de atendimento',
  partner_solutions: 'Soluções de parceiros', payment_configuration_update: 'Configuração de pagamentos', phone_number_name_update: 'Nome do número',
  phone_number_quality_update: 'Qualidade do número', security: 'Segurança', smb_app_state_sync: 'Sincronização do WhatsApp Business',
  smb_message_echoes: 'Mensagens espelhadas do WhatsApp Business', standby: 'Atendimentos em espera', template_category_update: 'Categoria de templates',
  template_correct_category_detection: 'Correção de categoria de templates', tracking_events: 'Rastreamento e atribuição', user_preferences: 'Preferências do usuário',
};

const PROVIDER_STATE_LABELS: Record<string, string> = {
  subscribed: 'Assinado na Meta', manual_required: 'Ação manual necessária', not_eligible: 'Não elegível',
  provider_rejected: 'Recusado pela Meta', unknown: 'Sem confirmação da Meta', error: 'Erro de consulta',
};

type CatalogFilter = 'all' | 'attention' | 'observed' | 'waiting';

const OPERATION_AREAS: Record<string, { order: number; title: string; description: string }> = {
  health: { order: 1, title: 'Conta, número e capacidades', description: 'Saúde da WABA, identidade, análise, qualidade e recursos liberados.' },
  security: { order: 2, title: 'Segurança e incidentes', description: 'Sinais críticos que exigem investigação e evidência preservada.' },
  messages: { order: 3, title: 'Conversas e mensagens', description: 'Entrada, entrega e conciliação do histórico canônico.' },
  routing: { order: 4, title: 'Handover e espera', description: 'Controle ativo do atendimento e prevenção de respostas concorrentes.' },
  calls: { order: 5, title: 'Chamadas WhatsApp', description: 'Ciclo operacional das chamadas associado ao atendimento.' },
  flows: { order: 6, title: 'WhatsApp Flows', description: 'Execuções e estados dos Flows oficiais, separados das automações internas.' },
  groups: { order: 7, title: 'Grupos oficiais', description: 'Ciclo de vida, participantes, configurações e disponibilidade.' },
  templates: { order: 8, title: 'Templates Meta', description: 'Revisão, categoria, componentes e qualidade observados na Meta.' },
  coexistence: { order: 9, title: 'Coexistência e preferências', description: 'Sincronização segura com o WhatsApp Business App e preferências suportadas.' },
  payments: { order: 10, title: 'Capacidade de pagamentos', description: 'Configuração do canal; nunca movimentação financeira automática.' },
  partner: { order: 11, title: 'Parceiros e integrações', description: 'Vínculos de soluções parceiras e capacidades concedidas.' },
  automation: { order: 12, title: 'Eventos automáticos', description: 'Sinais automáticos observáveis, sujeitos à política de aprovação.' },
  tracking: { order: 13, title: 'Rastreamento e atribuição', description: 'Eventos associados a métricas sem ampliar a fronteira de dados privados.' },
};

function needsAttention(item: WhatsAppMetaOperations['catalog'][number]) {
  const states = item.providerStates ?? ['unknown'];
  return item.severity === 'critical' || states.some((state) => ['manual_required', 'provider_rejected', 'error'].includes(state));
}

interface MetaWebhookSubscriptionPreview {
  fieldCount?: number;
  connectionCount?: number;
  changes?: Array<Record<string, unknown>>;
}

interface MetaWebhookOperationsPanelProps {
  queryKey: readonly unknown[];
  queryFn: () => Promise<WhatsAppMetaOperations>;
  title?: string;
  canManageSubscriptions?: boolean;
  canManagePolicies?: boolean;
  previewSubscriptions?: () => Promise<MetaWebhookSubscriptionPreview>;
  reconcileSubscriptions?: () => Promise<unknown>;
  retryReceipt?: (receiptId: string) => Promise<unknown>;
  updatePolicy?: (field: string, payload: {
    enabled: boolean;
    mode: 'observe_only' | 'suggest_action' | 'approval_each_time';
    minimumSeverity: 'info' | 'warning' | 'high' | 'critical';
    expectedVersion: number;
  }) => Promise<unknown>;
}

export function MetaWebhookOperationsPanel({
  queryKey,
  queryFn,
  title = 'Central de webhooks Meta',
  canManageSubscriptions = false,
  canManagePolicies = false,
  previewSubscriptions,
  reconcileSubscriptions,
  retryReceipt,
  updatePolicy,
}: MetaWebhookOperationsPanelProps) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<CatalogFilter>('all');
  const [confirmReconciliation, setConfirmReconciliation] = useState(false);
  const query = useQuery({ queryKey, queryFn, staleTime: 15_000, refetchInterval: 30_000 });
  const previewMutation = useMutation<MetaWebhookSubscriptionPreview>({
    mutationFn: async () => previewSubscriptions?.() ?? {},
  });
  const reconcileMutation = useMutation({
    mutationFn: async () => reconcileSubscriptions?.() ?? {},
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey }); },
  });
  const retryMutation = useMutation({
    mutationFn: async (receiptId: string) => retryReceipt?.(receiptId),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey }); },
  });
  const policyMutation = useMutation({
    mutationFn: async ({ field, enabled, version, severity }: { field: string; enabled: boolean; version: number; severity: string }) => updatePolicy?.(field, {
      enabled,
      mode: enabled ? (severity === 'critical' || severity === 'high' ? 'approval_each_time' : 'suggest_action') : 'observe_only',
      minimumSeverity: 'warning',
      expectedVersion: version,
    }),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey }); },
  });
  const catalog = useMemo(() => query.data?.catalog ?? [], [query.data?.catalog]);
  const nativeCount = catalog.filter((item) => item.implementation === 'native').length;
  const projectedCount = catalog.filter((item) => item.implementation === 'domain_projection').length;
  const observedCount = catalog.filter((item) => item.observationState === 'observed').length;
  const attentionCount = catalog.filter(needsAttention).length;
  const filteredCatalog = useMemo(() => {
    const term = search.trim().toLocaleLowerCase('pt-BR');
    return catalog.filter((item) => {
      const matchesSearch = !term || [FIELD_LABELS[item.field], item.field, item.destination, item.description]
        .some((value) => value?.toLocaleLowerCase('pt-BR').includes(term));
      const matchesFilter = filter === 'all'
        || (filter === 'attention' && needsAttention(item))
        || (filter === 'observed' && item.observationState === 'observed')
        || (filter === 'waiting' && item.observationState !== 'observed');
      return matchesSearch && matchesFilter;
    });
  }, [catalog, filter, search]);
  const operationAreas = Array.from(new Set(filteredCatalog.map((item) => item.category))).sort((a, b) => (
    (OPERATION_AREAS[a]?.order ?? 99) - (OPERATION_AREAS[b]?.order ?? 99)
  ));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base"><Activity className="h-4 w-4" />{title}</CardTitle>
        <CardDescription>
          Todos os campos suportados permanecem visíveis. “Observado” comprova recebimento; “ainda não observado” não permite inferir se a assinatura está ligada ou desligada no painel da Meta.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {query.isError ? (
          <div className="flex items-center gap-2 text-sm text-destructive"><AlertTriangle className="h-4 w-4" />Não foi possível carregar a cobertura agora.</div>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">{nativeCount} fluxos nativos</Badge>
          <Badge variant="outline">{projectedCount} projeções persistentes</Badge>
          <Badge variant="outline">{observedCount}/{catalog.length} observados</Badge>
          <Badge variant="outline">{query.data?.receiptCount ?? query.data?.eventCount ?? 0} recibos recentes</Badge>
          {attentionCount > 0 ? <Badge variant="destructive">{attentionCount} exigem atenção</Badge> : null}
        </div>
        {canManageSubscriptions && previewSubscriptions && reconcileSubscriptions ? (
          <div className="space-y-2 rounded-xl border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm font-medium">Assinaturas desejadas versus observadas</p>
                <p className="text-xs text-muted-foreground">A prévia não chama a Meta. A reconciliação só começa após confirmação humana explícita.</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={() => previewMutation.mutate()} disabled={previewMutation.isPending}>
                  {previewMutation.isPending ? 'Calculando…' : 'Gerar prévia'}
                </Button>
                {previewMutation.isSuccess ? (
                  <Button size="sm" onClick={() => setConfirmReconciliation(true)} disabled={reconcileMutation.isPending}>
                    <RefreshCw className="mr-2 h-3.5 w-3.5" />{reconcileMutation.isPending ? 'Enfileirando…' : 'Confirmar reconciliação'}
                  </Button>
                ) : null}
              </div>
            </div>
            {previewMutation.data ? <p className="text-xs" aria-live="polite">Prévia pronta: {String(previewMutation.data.fieldCount ?? 33)} campos em {String(previewMutation.data.connectionCount ?? 0)} conexão(ões).</p> : null}
            {reconcileMutation.isSuccess ? <p className="text-xs text-emerald-700" aria-live="polite">Reconciliação enfileirada. O estado será atualizado sem presumir assinatura individual.</p> : null}
            {previewMutation.isError || reconcileMutation.isError ? <p className="text-xs text-destructive" role="alert">Não foi possível preparar ou enfileirar a reconciliação.</p> : null}
          </div>
        ) : null}
        <AlertDialog open={confirmReconciliation} onOpenChange={setConfirmReconciliation}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Reconciliar assinaturas com a Meta?</AlertDialogTitle>
              <AlertDialogDescription>
                Esta ação inicia uma operação externa para as conexões elegíveis. A prévia não garante que todos os campos serão aceitos pela Meta.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancelar</AlertDialogCancel>
              <AlertDialogAction onClick={() => reconcileMutation.mutate()}>Iniciar reconciliação</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
        {query.isLoading ? <p className="text-sm text-muted-foreground" aria-live="polite">Carregando os 33 contratos e seus estados reais…</p> : null}
        {!query.isLoading && catalog.length > 0 ? (
          <div className="space-y-3 rounded-xl border bg-muted/20 p-3" aria-label="Filtros dos webhooks">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Buscar por capacidade, destino ou código"
                aria-label="Buscar webhooks"
                className="pl-9"
              />
            </div>
            <div className="flex flex-wrap gap-2" aria-label="Filtrar webhooks por estado">
              {([
                ['all', `Todos (${catalog.length})`],
                ['attention', `Exigem atenção (${attentionCount})`],
                ['observed', `Observados (${observedCount})`],
                ['waiting', `Aguardando evento (${catalog.length - observedCount})`],
              ] as const).map(([value, label]) => (
                <Button key={value} type="button" size="sm" variant={filter === value ? 'default' : 'outline'} onClick={() => setFilter(value)} aria-pressed={filter === value}>
                  {label}
                </Button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground" aria-live="polite">Exibindo {filteredCatalog.length} de {catalog.length} capacidades.</p>
          </div>
        ) : null}
        {!query.isLoading && catalog.length > 0 && filteredCatalog.length === 0 ? (
          <div className="rounded-xl border border-dashed p-6 text-center">
            <p className="text-sm font-medium">Nenhuma capacidade encontrada</p>
            <p className="mt-1 text-xs text-muted-foreground">Ajuste a busca ou selecione outro estado.</p>
            <Button className="mt-3" size="sm" variant="outline" onClick={() => { setSearch(''); setFilter('all'); }}>Limpar filtros</Button>
          </div>
        ) : null}
        {operationAreas.map((category) => {
          const area = OPERATION_AREAS[category] ?? { title: category, description: 'Capacidades operacionais relacionadas.' };
          const areaItems = filteredCatalog.filter((item) => item.category === category);
          const areaHref = areaItems.find((item) => item.appHref)?.appHref;
          return (
          <section key={category} className="space-y-3 rounded-xl border bg-card p-3 sm:p-4" aria-label={area.title}>
            <div className="flex flex-wrap items-start justify-between gap-3 border-b pb-3">
              <div>
                <p className="text-sm font-semibold">{area.title}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">{area.description}</p>
              </div>
              {areaHref ? (
                <Button asChild size="sm" variant="outline">
                  <Link to={areaHref}>Abrir tela <ExternalLink className="ml-1.5 h-3.5 w-3.5" /></Link>
                </Button>
              ) : null}
            </div>
            <div className="grid gap-2 lg:grid-cols-2">
              {areaItems.map((item) => (
                <div key={item.field} className="space-y-2 rounded-xl border p-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold">{FIELD_LABELS[item.field] ?? item.destination}</p>
                      <p className="text-xs text-muted-foreground">Destino: {item.destination}</p>
                    </div>
                    <div className="flex flex-wrap gap-1">
                      <Badge variant={item.implementation === 'native' ? 'secondary' : 'outline'}>
                        {item.implementation === 'native' ? 'nativo' : item.implementation === 'domain_projection' ? 'projeção persistente' : 'legado semântico'}
                      </Badge>
                      <Badge variant={item.observationState === 'observed' ? 'secondary' : 'outline'}>
                        {item.observationState === 'observed' ? 'Evento recebido' : 'Aguardando primeiro evento'}
                      </Badge>
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground">{item.description}</p>
                  <p className="text-xs"><span className="font-medium">Ao receber:</span> {item.operatorAction}</p>
                  <div className="flex flex-wrap gap-1" aria-label={`Estado de ${FIELD_LABELS[item.field] ?? item.field}`}>
                    <Badge variant={item.desiredSubscription ? 'secondary' : 'outline'}>
                      desejada: {item.desiredSubscription ? 'sim' : 'não'}
                    </Badge>
                    <Badge variant="outline">
                      {item.subscriptionCount ?? 0} conexão(ões)
                    </Badge>
                    {(item.providerStates ?? ['unknown']).map((providerState) => (
                      <Badge key={providerState} variant={providerState === 'subscribed' ? 'secondary' : 'outline'}>
                        {PROVIDER_STATE_LABELS[providerState] ?? `Meta: ${providerState.replaceAll('_', ' ')}`}
                      </Badge>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {item.eventCount > 0
                      ? `${item.eventCount} evento(s) no recorte${item.lastObservedAt ? ` · último em ${new Date(item.lastObservedAt).toLocaleString('pt-BR')}` : ''}`
                      : 'Nenhum evento observado no recorte atual.'}
                  </p>
                  <Collapsible>
                    <CollapsibleTrigger asChild>
                      <Button size="sm" variant="ghost" className="h-8 px-0 text-xs text-muted-foreground">
                        Ver detalhes técnicos <ChevronDown className="ml-1 h-3.5 w-3.5" />
                      </Button>
                    </CollapsibleTrigger>
                    <CollapsibleContent className="space-y-1 rounded-lg bg-muted/40 p-2 text-xs">
                      <p><span className="font-medium">Código:</span> <code>{item.field}</code></p>
                      <p><span className="font-medium">Aplicabilidade:</span> {item.applicability?.replaceAll('_', ' ') ?? 'waba level'}</p>
                      <p><span className="font-medium">Automação:</span> {item.automation?.mode?.replaceAll('_', ' ') ?? 'observe only'}</p>
                      {item.stateCode ? <p><span className="font-medium">Estado:</span> {item.stateCode}</p> : null}
                    </CollapsibleContent>
                  </Collapsible>
                  {item.appHref ? (
                    <Link className="inline-flex items-center gap-1 text-xs text-primary hover:underline" to={item.appHref}>
                      Abrir destino operacional <ExternalLink className="h-3 w-3" />
                    </Link>
                  ) : null}
                  {canManagePolicies && updatePolicy ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={policyMutation.isPending}
                      onClick={() => policyMutation.mutate({
                        field: item.field,
                        enabled: !(item.automation?.enabled ?? false),
                        version: item.automation?.version ?? 1,
                        severity: item.severity,
                      })}
                    >
                      {item.automation?.enabled ? 'Voltar a observar' : item.severity === 'critical' ? 'Requer aprovação por evento' : 'Ativar sugestões'}
                    </Button>
                  ) : null}
                </div>
              ))}
            </div>
          </section>
          );
        })}
        <div className="space-y-2">
          <p className="text-sm font-medium">Atividade recente</p>
          {(query.data?.receipts ?? []).slice(0, 8).map((receipt) => (
            <div key={receipt.id} className="flex flex-wrap items-start justify-between gap-2 rounded-xl border p-3 text-sm">
              <div>
                <p><span className="font-medium">{receipt.field}</span> · {receipt.status.replaceAll('_', ' ')}</p>
                <p className="text-xs text-muted-foreground">{new Date(receipt.observedAt).toLocaleString('pt-BR')}{receipt.sanitizedErrorCode ? ` · ${receipt.sanitizedErrorCode}` : ''}</p>
              </div>
              {canManageSubscriptions && retryReceipt && receipt.retryAvailable ? (
                <Button size="sm" variant="outline" disabled={retryMutation.isPending} onClick={() => retryMutation.mutate(receipt.id)}>Tentar novamente</Button>
              ) : null}
              {receipt.status === 'failed' && receipt.retryManagedBy === 'provider_envelope' ? (
                <span className="text-xs text-muted-foreground">Retry pelo processamento seguro do envelope</span>
              ) : null}
            </div>
          ))}
          {(query.data?.events ?? []).slice(0, 8).map((event) => (
            <div key={event.id} className="flex items-start gap-2 rounded-xl border p-3 text-sm">
              {event.severity === 'critical' ? <ShieldAlert className="mt-0.5 h-4 w-4 text-destructive" /> : <CheckCircle2 className="mt-0.5 h-4 w-4 text-emerald-600" />}
              <div><p>{event.summary}</p><p className="text-xs text-muted-foreground">{new Date(event.occurredAt).toLocaleString('pt-BR')}</p></div>
            </div>
          ))}
          {!query.isLoading && (query.data?.events ?? []).length === 0 && (query.data?.receipts ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">Nenhum sinal recebido ainda. Isso não confirma assinatura nem falha do provedor.</p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
