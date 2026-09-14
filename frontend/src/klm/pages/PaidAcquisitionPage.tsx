import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BadgeCheck,
  CircleDashed,
  Clock3,
  Instagram,
  MessageCircleMore,
  Rocket,
  ShieldAlert,
  Smartphone,
  Sparkles,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { KlmMetricCard, KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import {
  klmApi,
  type KlmCampaignDraft,
  type KlmPaidAcquisitionChannel,
} from '@/klm/api';
import { formatBRL, formatDecimal } from '@/lib/formatters';
import { cn } from '@/lib/utils';
import { type ContextualPageProps } from '@/klm/hooks/useContextualRouting';

const CHANNELS: Array<{
  value: KlmPaidAcquisitionChannel;
  label: string;
  connectionChannel: 'whatsapp' | 'messenger' | 'instagram';
  icon: typeof Smartphone;
}> = [
  { value: 'ctwa', label: 'Click-to-WhatsApp', connectionChannel: 'whatsapp', icon: Smartphone },
  { value: 'ctm', label: 'Click-to-Messenger', connectionChannel: 'messenger', icon: MessageCircleMore },
  { value: 'ctd', label: 'Click-to-Instagram-Direct', connectionChannel: 'instagram', icon: Instagram },
];

const SPEND_ORIGIN_LABEL: Record<string, { label: string; tone: 'gold' | 'green' | 'blue' | 'muted' }> = {
  oficial: { label: 'Origem: sync oficial', tone: 'green' },
  manual: { label: 'Origem: snapshot manual', tone: 'blue' },
  demonstracao: { label: 'Origem: demonstração', tone: 'gold' },
  sem_dado: { label: 'Sem dado de investimento', tone: 'muted' },
};

const DRAFT_STATUS_LABEL: Record<string, { label: string; tone: 'gold' | 'green' | 'blue' | 'red' | 'muted' }> = {
  draft: { label: 'Rascunho', tone: 'muted' },
  awaiting_approval: { label: 'Aguardando aprovação', tone: 'gold' },
  approved_local: { label: 'Aprovada (simulação local)', tone: 'blue' },
  blocked_external: { label: 'Bloqueada (anúncios reais não ativados)', tone: 'red' },
  cancelled: { label: 'Rejeitada', tone: 'muted' },
};

type BuilderState = {
  channel: KlmPaidAcquisitionChannel;
  campaignName: string;
  dailyBudget: string;
  headline: string;
  primaryText: string;
  welcomeMessage: string;
  iceBreaker: string;
  connectionId: string;
};

const EMPTY_BUILDER: BuilderState = {
  channel: 'ctwa',
  campaignName: '',
  dailyBudget: '',
  headline: '',
  primaryText: '',
  welcomeMessage: '',
  iceBreaker: '',
  connectionId: '',
};

export default function PaidAcquisitionPage({ embedded = false }: ContextualPageProps) {
  const queryClient = useQueryClient();
  const [builderOpen, setBuilderOpen] = useState(false);
  const [builder, setBuilder] = useState<BuilderState>(EMPTY_BUILDER);
  const [formError, setFormError] = useState<string | null>(null);

  const syncStatusQuery = useQuery({
    queryKey: ['klm', 'paid-acquisition', 'sync-status'],
    queryFn: () => klmApi.paidAcquisitionSyncStatus(),
  });
  const draftsQuery = useQuery({
    queryKey: ['klm', 'paid-acquisition', 'campaigns'],
    queryFn: () => klmApi.paidAcquisitionCampaigns(),
  });
  const funnelQuery = useQuery({
    queryKey: ['klm', 'paid-acquisition', 'funnel-summary'],
    queryFn: () => klmApi.paidAcquisitionFunnelSummary(),
  });

  const selectedChannelMeta = CHANNELS.find((item) => item.value === builder.channel) ?? CHANNELS[0];
  const connectionsQuery = useQuery({
    queryKey: ['klm', 'omnichannel', 'connections', selectedChannelMeta.connectionChannel],
    queryFn: () => klmApi.omnichannelConnections(selectedChannelMeta.connectionChannel),
    enabled: builderOpen,
  });
  const activeConnections = useMemo(
    () => (connectionsQuery.data ?? []).filter((item) => item.status !== 'paused' && item.status !== 'disconnected_local'),
    [connectionsQuery.data],
  );

  const prepareMutation = useMutation({
    mutationFn: () => {
      const trimmedIceBreaker = builder.iceBreaker.trim();
      return klmApi.prepareCampaignDraft({
        idempotencyKey: `spa-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        channel: builder.channel,
        campaignName: builder.campaignName.trim(),
        dailyBudget: builder.dailyBudget,
        currency: 'BRL',
        creative: {
          headline: builder.headline.trim(),
          primaryText: builder.primaryText.trim(),
        },
        welcomeMessage: builder.welcomeMessage.trim(),
        iceBreakers: trimmedIceBreaker ? [trimmedIceBreaker] : [],
        connectionId: builder.connectionId || undefined,
      });
    },
    onSuccess: () => {
      setBuilderOpen(false);
      setBuilder(EMPTY_BUILDER);
      setFormError(null);
      queryClient.invalidateQueries({ queryKey: ['klm', 'paid-acquisition', 'campaigns'] });
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Não foi possível preparar o rascunho.';
      setFormError(message);
    },
  });

  const dailyBudgetNumber = Number(builder.dailyBudget.replace(',', '.'));
  const estimatedMonthly = Number.isFinite(dailyBudgetNumber) && dailyBudgetNumber > 0
    ? dailyBudgetNumber * 30
    : null;

  const canSubmit =
    builder.campaignName.trim().length > 0 &&
    dailyBudgetNumber > 0 &&
    builder.headline.trim().length > 0 &&
    builder.primaryText.trim().length > 0 &&
    builder.welcomeMessage.trim().length > 0;

  const syncStatus = syncStatusQuery.data;
  const rows = funnelQuery.data?.rows ?? [];
  const totals = funnelQuery.data?.totals;

  return (
    <div className={cn('flex min-w-0 flex-col gap-6', !embedded && 'p-6')}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-2xl font-semibold text-foreground">
            <Rocket className="h-5 w-5 text-primary" /> Aquisição Paga
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Funil de campanhas CTWA/CTM/CTD ponta a ponta: investimento, conversas atribuídas, qualificados,
            contratos e receita. Criar campanha sempre passa por aprovação humana antes de qualquer
            execução externa.
          </p>
        </div>
        <Button onClick={() => setBuilderOpen(true)} className="gap-2">
          <Sparkles className="h-4 w-4" /> Nova campanha
        </Button>
      </div>

      <KlmPanel className="p-4">
        <KlmSectionTitle
          title="Status de sincronização"
          description="Os anúncios reais são ativados após a aprovação; use o cenário de demonstração para explorar agora."
        />
        {syncStatusQuery.isLoading ? (
          <p className="mt-3 text-sm text-muted-foreground">Carregando status…</p>
        ) : syncStatus ? (
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <KlmPill tone={syncStatus.connected ? 'green' : 'muted'}>
              {syncStatus.connected ? <BadgeCheck className="mr-1 h-3 w-3" /> : <CircleDashed className="mr-1 h-3 w-3" />}
              {syncStatus.connected ? `${syncStatus.syncedCampaigns} campanhas sincronizadas` : 'Nenhuma sincronização ainda'}
            </KlmPill>
            <KlmPill tone={syncStatus.gateOpen ? 'green' : 'gold'}>
              {syncStatus.gateOpen ? 'Anúncios reais ativados' : 'Anúncios reais ainda não ativados'}
            </KlmPill>
            {syncStatus.lastSyncedAt && (
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
                <Clock3 className="h-3 w-3" /> Última leitura: {new Date(syncStatus.lastSyncedAt).toLocaleString('pt-BR')}
              </span>
            )}
            <p className="w-full text-xs text-muted-foreground">{syncStatus.message}</p>
          </div>
        ) : null}
      </KlmPanel>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KlmMetricCard label="Investimento total" value={formatBRL(totals?.spend ?? 0)} delta="Últimos 30 dias" trend="neutral" />
        <KlmMetricCard label="Conversas atribuídas" value={formatDecimal(totals?.conversations ?? 0, 0)} delta="CTWA/CTM/CTD" trend="neutral" />
        <KlmMetricCard label="CAC" value={totals?.cac != null ? formatBRL(totals.cac) : '—'} delta="Investimento / contratos" trend="neutral" />
        <KlmMetricCard label="ROAS" value={totals?.roas != null ? `${formatDecimal(totals.roas, 2)}x` : '—'} delta="Receita / investimento" trend="neutral" />
      </div>

      <KlmPanel className="p-4">
        <KlmSectionTitle title="Funil por campanha" description="Investimento (Ads Manager) × conversas, qualificados, contratos e receita atribuídos aos eventos de conversão." />
        <div className="mt-4 overflow-x-auto">
          {funnelQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">Carregando funil…</p>
          ) : rows.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border py-10 text-center">
              <ShieldAlert className="h-6 w-6 text-muted-foreground" />
              <p className="text-sm font-medium text-foreground">Nenhuma campanha com dado no período</p>
              <p className="max-w-sm text-xs text-muted-foreground">
                Assim que houver investimento sincronizado ou conversas atribuídas (adId/ctwa_clid), o funil aparece aqui automaticamente.
              </p>
            </div>
          ) : (
            <table className="w-full min-w-[720px] border-collapse text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="py-2 pr-3">Campanha</th>
                  <th className="py-2 pr-3">Origem do investimento</th>
                  <th className="py-2 pr-3 text-right">Investimento</th>
                  <th className="py-2 pr-3 text-right">Conversas</th>
                  <th className="py-2 pr-3 text-right">Qualificados</th>
                  <th className="py-2 pr-3 text-right">Contratos</th>
                  <th className="py-2 pr-3 text-right">Receita</th>
                  <th className="py-2 pr-3 text-right">CPL</th>
                  <th className="py-2 pr-3 text-right">CAC</th>
                  <th className="py-2 pr-3 text-right">ROAS</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const originMeta = SPEND_ORIGIN_LABEL[row.spendOrigin] ?? SPEND_ORIGIN_LABEL.sem_dado;
                  return (
                    <tr key={row.campaignKey} className="border-b border-border/60">
                      <td className="py-2 pr-3 font-medium text-foreground">{row.campaignName}</td>
                      <td className="py-2 pr-3"><KlmPill tone={originMeta.tone}>{originMeta.label}</KlmPill></td>
                      <td className="py-2 pr-3 text-right">{formatBRL(row.spend)}</td>
                      <td className="py-2 pr-3 text-right">{formatDecimal(row.conversations, 0)}</td>
                      <td className="py-2 pr-3 text-right">{formatDecimal(row.qualifiedLeads, 0)}</td>
                      <td className="py-2 pr-3 text-right">{formatDecimal(row.contracts, 0)}</td>
                      <td className="py-2 pr-3 text-right">{formatBRL(row.revenue)}</td>
                      <td className="py-2 pr-3 text-right">{row.cpl != null ? formatBRL(row.cpl) : '—'}</td>
                      <td className="py-2 pr-3 text-right">{row.cac != null ? formatBRL(row.cac) : '—'}</td>
                      <td className="py-2 pr-3 text-right">{row.roas != null ? `${formatDecimal(row.roas, 2)}x` : '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </KlmPanel>

      <KlmPanel className="p-4">
        <KlmSectionTitle title="Rascunhos de campanha" description="Toda campanha nasce aguardando aprovação humana; aprove ou rejeite na Central de Aprovações." />
        <div className="mt-3 flex flex-col gap-2">
          {draftsQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">Carregando rascunhos…</p>
          ) : (draftsQuery.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">Nenhum rascunho ainda. Crie uma campanha para começar.</p>
          ) : (
            (draftsQuery.data as KlmCampaignDraft[]).map((draft) => {
              const statusMeta = DRAFT_STATUS_LABEL[draft.status] ?? DRAFT_STATUS_LABEL.draft;
              return (
                <div key={draft.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border p-3">
                  <div>
                    <p className="text-sm font-medium text-foreground">
                      {(draft.marketingApiPayload as { campaign?: { name?: string } })?.campaign?.name ?? draft.channelLabel}
                    </p>
                    <p className="text-xs text-muted-foreground">{draft.channelLabel} · criado em {new Date(draft.createdAt).toLocaleString('pt-BR')}</p>
                  </div>
                  <KlmPill tone={statusMeta.tone}>{statusMeta.label}</KlmPill>
                </div>
              );
            })
          )}
        </div>
      </KlmPanel>

      <Dialog open={builderOpen} onOpenChange={(open) => { setBuilderOpen(open); if (!open) setFormError(null); }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Nova campanha de aquisição paga</DialogTitle>
            <DialogDescription>
              Isto cria um rascunho aguardando aprovação humana. Nenhuma chamada é feita à Marketing API.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3">
            <div>
              <Label>Canal</Label>
              <Select
                value={builder.channel}
                onValueChange={(value) => setBuilder((prev) => ({ ...prev, channel: value as KlmPaidAcquisitionChannel, connectionId: '' }))}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {CHANNELS.map((item) => (
                    <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {connectionsQuery.isLoading ? (
              <p className="text-xs text-muted-foreground">Verificando conexões do canal…</p>
            ) : activeConnections.length === 0 ? (
              <p className="rounded-md border border-dashed border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
                Nenhuma conexão ativa para {selectedChannelMeta.label}. Conecte o canal em Central de Conexão antes de preparar esta campanha.
              </p>
            ) : (
              <div>
                <Label>Conexão de destino</Label>
                <Select
                  value={builder.connectionId || activeConnections[0]?.id}
                  onValueChange={(value) => setBuilder((prev) => ({ ...prev, connectionId: value }))}
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {activeConnections.map((connection) => (
                      <SelectItem key={connection.id} value={connection.id}>{connection.displayName}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            <div>
              <Label>Nome da campanha</Label>
              <Input value={builder.campaignName} onChange={(event) => setBuilder((prev) => ({ ...prev, campaignName: event.target.value }))} placeholder="Ex.: CTWA — Direito de Família" />
            </div>
            <div>
              <Label>Orçamento diário (BRL)</Label>
              <Input inputMode="decimal" value={builder.dailyBudget} onChange={(event) => setBuilder((prev) => ({ ...prev, dailyBudget: event.target.value }))} placeholder="150.00" />
              {estimatedMonthly != null && (
                <p className="mt-1 text-xs text-muted-foreground">Estimativa mensal: {formatBRL(estimatedMonthly)}</p>
              )}
            </div>
            <div>
              <Label>Título do criativo</Label>
              <Input value={builder.headline} onChange={(event) => setBuilder((prev) => ({ ...prev, headline: event.target.value }))} placeholder="Fale com um advogado agora" />
            </div>
            <div>
              <Label>Texto principal</Label>
              <Textarea value={builder.primaryText} onChange={(event) => setBuilder((prev) => ({ ...prev, primaryText: event.target.value }))} rows={2} placeholder="Atendimento imediato via WhatsApp." />
            </div>
            <div>
              <Label>Mensagem de boas-vindas</Label>
              <Textarea value={builder.welcomeMessage} onChange={(event) => setBuilder((prev) => ({ ...prev, welcomeMessage: event.target.value }))} rows={2} placeholder="Olá! Vi seu anúncio, posso ajudar?" />
            </div>
            <div>
              <Label>Ice breaker (opcional)</Label>
              <Input value={builder.iceBreaker} onChange={(event) => setBuilder((prev) => ({ ...prev, iceBreaker: event.target.value }))} placeholder="Quero saber mais" />
            </div>

            {formError && <p className="text-xs text-destructive">{formError}</p>}

            <div className="flex items-center justify-between gap-2 rounded-md border border-primary/25 bg-primary/5 p-2 text-xs text-muted-foreground">
              <ShieldAlert className="h-4 w-4 shrink-0 text-primary" />
              Isto envia o rascunho para aprovação humana. Nenhuma campanha real é criada agora.
            </div>

            <Button
              className="gap-2"
              disabled={!canSubmit || activeConnections.length === 0 || prepareMutation.isPending}
              onClick={() => prepareMutation.mutate()}
            >
              <Sparkles className="h-4 w-4" />
              {prepareMutation.isPending ? 'Enviando…' : 'Enviar para aprovação'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
