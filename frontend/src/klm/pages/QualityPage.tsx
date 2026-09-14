import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Check,
  CheckCircle2,
  ChevronRight,
  LoaderCircle,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  klmApi,
  type KlmOmnichannelAuditFinding,
} from '@/klm/api';
import { NativeAgentOperationsPanel } from '@/klm/components/NativeAgentOperationsPanel';
import { NativeModuleBar } from '@/klm/components/NativeModuleBar';
import {
  KlmMetricCard,
  KlmPanel,
  KlmPill,
  KlmProgress,
  KlmSectionTitle,
} from '@/klm/components/Primitives';
import { cn } from '@/lib/utils';
import { useAppStore } from '@/store/app-context';
import { type ContextualPageProps, useContextualNavigate } from '@/klm/hooks/useContextualRouting';

type Review = {
  id: string;
  threadId: string;
  matterId: string | null;
  reason: string;
  type: string;
  title: string;
  detail: string;
  severity: 'Alta' | 'Média';
  agent: string;
  score: number;
  auditFinding?: KlmOmnichannelAuditFinding;
};

type AuditFilters = {
  state: string;
  severity: string;
  findingType: string;
  query: string;
};

const EMPTY_AUDIT_FILTERS: AuditFilters = {
  state: '',
  severity: '',
  findingType: '',
  query: '',
};

export default function QualityPage({ embedded = false, onNavigate }: ContextualPageProps) {
  const { can, state } = useAppStore();
  const canSendMessages = can('mensagens', 'send_message');
  const canManageIntegrations = can('integrations', 'manage');
  const navigate = useContextualNavigate({ embedded, onNavigate });
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const [customizingAudit, setCustomizingAudit] = useState(false);
  const [auditFilters, setAuditFilters] = useState<AuditFilters>(EMPTY_AUDIT_FILTERS);

  const qualityQuery = useQuery({ queryKey: ['klm', 'quality'], queryFn: klmApi.quality });
  const auditQuery = useQuery({
    queryKey: ['klm', 'omnichannel-audit', 'quality', auditFilters],
    queryFn: () => klmApi.omnichannelAudit({
      state: auditFilters.state || undefined,
      severity: auditFilters.severity || undefined,
      findingType: auditFilters.findingType || undefined,
      query: auditFilters.query.trim() || undefined,
      limit: 300,
    }),
  });

  const reviews = useMemo<Review[]>(() => [
    ...(auditQuery.data?.findings ?? [])
      .filter((finding) => ['open', 'waiting_human', 'retry_scheduled'].includes(finding.state))
      .map((finding) => ({
        id: `audit:${finding.id}`,
        threadId: finding.threadId,
        matterId: finding.matterId,
        reason: finding.findingType,
        type: 'Auditoria',
        title: finding.title,
        detail: finding.summary,
        severity: finding.severity === 'critical' ? 'Alta' as const : 'Média' as const,
        agent: 'Auditor',
        score: finding.severity === 'critical' ? 35 : 70,
        auditFinding: finding,
      })),
    ...(qualityQuery.data?.reviewQueue ?? []).map((item) => ({
      id: `quality:${item.threadId}:${item.reason}`,
      threadId: item.threadId,
      matterId: item.matterId,
      reason: item.reason,
      type: item.reason === 'handoff_humano' ? 'Transferência' : 'Nota',
      title: item.reason === 'handoff_humano'
        ? 'Conversa aguardando humano'
        : 'Conversa abaixo do limiar',
      detail: item.matterId
        ? `Caso ${item.matterId.slice(0, 8)} vinculado à revisão.`
        : 'Conversa ainda sem vínculo com um caso.',
      severity: item.score < 50 ? 'Alta' as const : 'Média' as const,
      agent: 'Operação',
      score: item.score,
    })),
  ], [auditQuery.data?.findings, qualityQuery.data?.reviewQueue]);

  useEffect(() => {
    if (!reviews.length) {
      setSelectedId('');
      return;
    }
    if (!reviews.some((review) => review.id === selectedId)) setSelectedId(reviews[0].id);
  }, [reviews, selectedId]);

  useEffect(() => {
    const refresh = (event: Event) => {
      const detail = (event as CustomEvent<{ event?: string }>).detail;
      if (detail?.event !== 'OMNICHANNEL_AUDIT_UPDATED') return;
      void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-audit'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'quality'] });
    };
    window.addEventListener('klm:realtime', refresh);
    return () => window.removeEventListener('klm:realtime', refresh);
  }, [queryClient]);

  const selected = reviews.find((review) => review.id === selectedId) ?? null;
  const messagesQuery = useQuery({
    queryKey: ['klm', 'messages', selected?.threadId],
    queryFn: () => klmApi.messages(selected!.threadId),
    enabled: Boolean(selected),
  });
  const transcript = useMemo(() => [...(messagesQuery.data ?? [])]
    .sort((left, right) => left.occurredAt.localeCompare(right.occurredAt))
    .slice(-12), [messagesQuery.data]);
  const dimensions = qualityQuery.data?.dimensions ?? [];

  const decisionMutation = useMutation({
    mutationFn: (decision: 'registrar_sem_alteracao' | 'registrar_sugestao') => {
      if (!selected) throw new Error('Revisão indisponível');
      return klmApi.sendInternalMessage(
        selected.threadId,
        [
          `Nota de revisão de qualidade: ${decision === 'registrar_sugestao' ? 'sugestão de ajuste registrada' : 'sem alteração sugerida'}.`,
          `Motivo da fila: ${selected.reason}. Score observado: ${selected.score}.`,
          'Nota interna; nenhuma configuração de agente foi alterada ou publicada.',
        ].join('\n'),
        state?.currentUser?.name?.trim() || state?.currentUser?.email || 'Usuário do workspace',
        { source: 'quality_review' },
      );
    },
    onSuccess: (_, decision) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'messages', selected?.threadId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
      setNotice(decision === 'registrar_sugestao'
        ? 'Sugestão de ajuste registrada como nota interna. Nenhuma configuração foi alterada.'
        : 'Nota sem alteração registrada na conversa interna.');
    },
    onError: () => setNotice('Não foi possível persistir a decisão de qualidade.'),
  });

  const auditActionMutation = useMutation({
    mutationFn: (action: 'resolve' | 'dismiss' | 'retry') => {
      if (!selected?.auditFinding) throw new Error('Apontamento indisponível');
      return klmApi.actOnOmnichannelAuditFinding(selected.auditFinding.id, {
        action,
        expectedVersion: selected.auditFinding.version,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-audit'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'quality'] });
      setNotice('Ação registrada no apontamento canônico do Auditor.');
    },
    onError: () => setNotice('O apontamento mudou ou não admite esta ação com segurança.'),
  });

  const runAuditMutation = useMutation({
    mutationFn: () => klmApi.runOmnichannelAudit({ limit: 250 }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-audit'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'quality'] });
      setNotice('Auditoria local concluída e reconciliada com as evidências de entrega.');
    },
    onError: () => setNotice('A auditoria local não pôde ser concluída.'),
  });

  const publishMutation = useMutation({
    mutationFn: async () => {
      if (!selected) throw new Error('Revisão indisponível');
      const intent = await klmApi.createDomainExternalIntent({
        matterId: selected.matterId,
        actionType: 'agent_quality_version_publish',
        domain: 'automation',
        idempotencyKey: `quality-publish:${selected.threadId}:${selected.reason}:${selected.score}`,
        payload: {
          threadId: selected.threadId,
          reviewReason: selected.reason,
          observedScore: selected.score,
          qualitySnapshot: qualityQuery.data?.dimensions ?? [],
          externalActionsExecuted: 0,
        },
      });
      return klmApi.approveExternalIntentLocally(intent.id);
    },
    onSuccess: (intent) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'external-intents'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
      setNotice(`Intenção de versão registrada localmente (${intent.status}). Nenhum prompt foi enviado ao provedor.`);
    },
    onError: () => setNotice('Não foi possível registrar a intenção local de versão.'),
  });

  const setPreset = (preset: 'pending' | 'critical' | 'delivery' | 'unanswered') => {
    setAuditFilters({
      ...EMPTY_AUDIT_FILTERS,
      state: preset === 'pending' ? 'open' : '',
      severity: preset === 'critical' ? 'critical' : '',
      findingType: preset === 'delivery' ? 'delivery_failed' : preset === 'unanswered' ? 'unanswered_lead' : '',
    });
  };

  return (
    <div className="mx-auto max-w-[1220px] space-y-5">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Meu Executivo / AI Quality</p>
          <h1 className="mt-2 text-2xl font-semibold">Qualidade e integridade</h1>
          <p className="mt-1 max-w-3xl text-xs leading-4 text-muted-foreground">Auditoria independente, avaliações, entregas, respostas, transferências e decisões internas sob controle humano.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <KlmPill tone={reviews.length ? 'gold' : 'green'}>{reviews.length} revisões</KlmPill>
          <KlmPill tone={auditQuery.isError ? 'gold' : (auditQuery.data?.criticalCount ?? 0) ? 'red' : 'green'}>{auditQuery.isError ? 'críticas indisponíveis' : `${auditQuery.data?.criticalCount ?? 0} críticas`}</KlmPill>
          <Button variant="outline" className="h-9 text-xs" disabled={!canSendMessages || runAuditMutation.isPending} onClick={() => runAuditMutation.mutate()}>
            {runAuditMutation.isPending ? <LoaderCircle className="mr-2 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-2 h-3.5 w-3.5" />}
            Auditar agora
          </Button>
          <Button className="h-9 bg-[#031121] text-xs text-white hover:bg-[#031121]" onClick={() => navigate('/app/flows')}>
            <Sparkles className="mr-2 h-3.5 w-3.5" />Abrir ciclos em Fluxos
          </Button>
        </div>
      </header>

      {notice ? (
        <KlmPanel className="flex items-center justify-between gap-4 border-primary/30 bg-primary/10 p-3">
          <div className="flex items-center gap-2 text-2xs"><Check className="h-4 w-4 text-emerald-600" />{notice}</div>
          <Button variant="ghost" size="sm" onClick={() => setNotice(null)} className="h-7 text-2xs">Fechar</Button>
        </KlmPanel>
      ) : null}

      {qualityQuery.isError || auditQuery.isError ? (
        <KlmPanel className="flex flex-wrap items-center justify-between gap-4 border-destructive/40 bg-destructive/10 p-3" role="alert">
          <div className="text-2xs">
            Não foi possível carregar {qualityQuery.isError && auditQuery.isError ? 'as métricas de qualidade e a auditoria' : qualityQuery.isError ? 'as métricas de qualidade' : 'a auditoria'}. Os números abaixo não refletem o estado atual.
          </div>
          <Button variant="outline" size="sm" className="h-7 text-2xs" onClick={() => { if (qualityQuery.isError) void qualityQuery.refetch(); if (auditQuery.isError) void auditQuery.refetch(); }}>Tentar novamente</Button>
        </KlmPanel>
      ) : null}

      <NativeModuleBar domain="automation" surface="Qualidade dos agentes" returnTo="/app/qualidade" capabilityIds={['analytics', 'ratings-feedback', 'preview-execution', 'handoff-inbox-transfer']} onNavigate={embedded ? onNavigate : undefined} />
      <NativeAgentOperationsPanel view="quality" canManage={can('agents', 'manage')} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KlmMetricCard label="Cobertura auditada" loading={auditQuery.isPending} value={auditQuery.isError ? 'indisponível' : `${auditQuery.data?.coveragePercent ?? 0}%`} delta={auditQuery.isError ? 'consulta de auditoria falhou' : `${auditQuery.data?.healthyThreads ?? 0} conversas íntegras`} trend={!auditQuery.isError && (auditQuery.data?.coveragePercent ?? 0) >= 95 ? 'up' : 'neutral'} />
        <KlmMetricCard label="Respostas no prazo" loading={auditQuery.isPending} value={auditQuery.isError ? 'indisponível' : `${auditQuery.data?.responseCoveragePercent ?? 0}%`} delta={auditQuery.isError ? 'consulta de auditoria falhou' : 'comparação canônica'} trend={!auditQuery.isError && (auditQuery.data?.responseCoveragePercent ?? 0) >= 95 ? 'up' : 'neutral'} />
        <KlmMetricCard label="Integridade de entrega" loading={auditQuery.isPending} value={auditQuery.isError ? 'indisponível' : `${auditQuery.data?.deliveryIntegrityPercent ?? 0}%`} delta={auditQuery.isError ? 'consulta de auditoria falhou' : 'recibos e projeção'} trend={!auditQuery.isError && (auditQuery.data?.deliveryIntegrityPercent ?? 0) >= 95 ? 'up' : 'neutral'} />
        <KlmMetricCard label="Score dos agentes" loading={qualityQuery.isPending} value={qualityQuery.isError ? 'indisponível' : `${qualityQuery.data?.score ?? 0}%`} delta={qualityQuery.isError ? 'consulta de qualidade falhou' : 'fonte local atual'} trend={!qualityQuery.isError && (qualityQuery.data?.score ?? 0) >= 90 ? 'up' : 'neutral'} />
      </div>

      <div className="flex flex-wrap items-center gap-2 border-y border-border py-3">
        <span className="text-2xs font-semibold uppercase text-muted-foreground">Filtros rápidos</span>
        <Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setAuditFilters(EMPTY_AUDIT_FILTERS)}>Todos</Button>
        <Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setPreset('pending')}>Pendentes</Button>
        <Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setPreset('critical')}>Críticos</Button>
        <Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setPreset('delivery')}>Falha de entrega</Button>
        <Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setPreset('unanswered')}>Sem resposta</Button>
        <Button variant={customizingAudit ? 'secondary' : 'outline'} size="sm" className="h-7 text-2xs" onClick={() => setCustomizingAudit((current) => !current)}>
          <SlidersHorizontal className="mr-1.5 h-3 w-3" />Personalizar
        </Button>
      </div>

      {customizingAudit ? (
        <div className="grid gap-2 border-b border-border pb-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="quality-custom-filters">
          <input value={auditFilters.query} onChange={(event) => setAuditFilters((current) => ({ ...current, query: event.target.value }))} placeholder="Buscar nos apontamentos" className="h-9 border border-border bg-background px-3 text-2xs outline-none focus:border-primary" />
          <select value={auditFilters.state} onChange={(event) => setAuditFilters((current) => ({ ...current, state: event.target.value }))} className="h-9 border border-border bg-background px-3 text-2xs"><option value="">Todos os estados</option><option value="open">Aberto</option><option value="waiting_human">Revisão humana</option><option value="retry_scheduled">Reenvio agendado</option><option value="resolved">Resolvido</option><option value="dismissed">Descartado</option></select>
          <select value={auditFilters.severity} onChange={(event) => setAuditFilters((current) => ({ ...current, severity: event.target.value }))} className="h-9 border border-border bg-background px-3 text-2xs"><option value="">Toda severidade</option><option value="critical">Crítica</option><option value="warning">Atenção</option><option value="info">Informativa</option></select>
          <input value={auditFilters.findingType} onChange={(event) => setAuditFilters((current) => ({ ...current, findingType: event.target.value }))} placeholder="Tipo personalizado" className="h-9 border border-border bg-background px-3 text-2xs outline-none focus:border-primary" />
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[330px_minmax(0,1fr)_300px]">
        <KlmPanel className="overflow-hidden">
          <div className="border-b p-4"><KlmSectionTitle title="Fila de revisão" description="Evidências e métricas reais." action={<KlmPill tone={reviews.some((review) => review.severity === 'Alta') ? 'red' : 'green'}>{reviews.filter((review) => review.severity === 'Alta').length} alta</KlmPill>} /></div>
          <div className="max-h-[720px] divide-y overflow-y-auto">
            {reviews.map((review) => (
              <button key={review.id} onClick={() => setSelectedId(review.id)} className={cn('grid w-full grid-cols-[1fr_auto] gap-3 p-4 text-left transition hover:bg-secondary', selectedId === review.id && 'bg-primary/10')}>
                <span><span className="text-2xs font-semibold uppercase text-[#0057d8]">{review.type}</span><strong className="mt-1 block text-2xs">{review.title}</strong><span className="mt-1 block text-2xs leading-4 text-muted-foreground">{review.detail}</span><span className="mt-2 block text-2xs text-muted-foreground">{review.agent} · score {review.score}</span></span>
                <span className="flex flex-col items-end gap-2"><KlmPill tone={review.severity === 'Alta' ? 'red' : 'gold'}>{review.severity}</KlmPill><ChevronRight className="h-4 w-4 text-muted-foreground" /></span>
              </button>
            ))}
            {!qualityQuery.isPending && !auditQuery.isPending && !reviews.length ? <div className="p-6 text-center text-2xs text-muted-foreground">{qualityQuery.isError || auditQuery.isError ? 'Não foi possível carregar as revisões pendentes.' : 'Nenhuma conversa está pendente de revisão.'}</div> : null}
          </div>
        </KlmPanel>

        <KlmPanel className="overflow-hidden">
          <div className="border-b p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-base font-medium">Avaliação detalhada</h2><p className="mt-1 text-2xs text-muted-foreground">{selected ? `${selected.agent} · ${selected.type}` : 'Selecione uma revisão'}</p></div>{selected ? <KlmPill tone="gold">score {selected.score}</KlmPill> : null}</div></div>
          {selected ? (
            <div className="space-y-5 p-4 sm:p-5">
              <section><h3 className="text-2xs font-semibold uppercase text-muted-foreground">Conversa avaliada</h3><div className="mt-3 space-y-2 border-y border-border py-3">{messagesQuery.isPending ? <div className="flex items-center gap-2 text-2xs text-muted-foreground"><LoaderCircle className="h-3.5 w-3.5 animate-spin" />Carregando conversa</div> : null}{transcript.map((message) => <div key={message.id} className="grid grid-cols-[100px_1fr] gap-2 text-2xs"><strong className={message.authorType === 'client' ? 'text-foreground' : 'text-[#0057d8]'}>{message.authorLabel}</strong><p className="leading-4 text-foreground/75">{message.body}</p></div>)}{!messagesQuery.isPending && !transcript.length ? <p className="text-2xs text-muted-foreground">A conversa ainda não possui mensagens persistidas.</p> : null}</div></section>
              {selected.auditFinding ? (
                <section><h3 className="text-2xs font-semibold uppercase text-muted-foreground">Evidência do Auditor</h3><div className="mt-3 divide-y border-y border-border"><div className="grid grid-cols-[1fr_auto] gap-3 py-3"><span><strong className="block text-2xs">{selected.auditFinding.title}</strong><span className="mt-1 block text-2xs leading-4 text-muted-foreground">{selected.auditFinding.summary}</span></span><KlmPill tone={selected.auditFinding.severity === 'critical' ? 'red' : 'gold'}>{selected.auditFinding.state.replaceAll('_', ' ')}</KlmPill></div><div className="py-3 text-2xs text-muted-foreground">Última confirmação: {new Date(selected.auditFinding.lastSeenAt).toLocaleString('pt-BR')}</div></div></section>
              ) : (
                <section><h3 className="text-2xs font-semibold uppercase text-muted-foreground">Critérios calculados</h3><div className="mt-3 divide-y border-y border-border">{dimensions.map((dimension) => { const dimensionState = dimension.score >= 95 ? 'OK' : dimension.score >= 80 ? 'Revisar' : 'Ajustar'; return <div key={dimension.key} className="grid grid-cols-[1fr_auto] gap-3 py-3"><span><strong className="block text-2xs">{dimension.label}</strong><span className="mt-1 block text-2xs text-muted-foreground">Score atual: {dimension.score}%.</span></span><KlmPill tone={dimensionState === 'OK' ? 'green' : dimensionState === 'Ajustar' ? 'red' : 'gold'}>{dimensionState}</KlmPill></div>; })}</div></section>
              )}
              <div className="border-l-2 border-primary bg-primary/10 p-4"><div className="flex items-center gap-2 text-2xs font-semibold"><Sparkles className="h-3.5 w-3.5 text-[#0057d8]" />Próxima revisão</div><p className="mt-2 text-2xs leading-4 text-muted-foreground">A decisão fica interna. Reenvios são idempotentes e bloqueados quando a entrega externa estiver incerta.</p></div>
              {selected.auditFinding ? (
                <div className="grid grid-cols-2 gap-2">
                  {selected.auditFinding.remediation.automaticRetryEligible === true ? <Button variant="outline" disabled={!canSendMessages || auditActionMutation.isPending} onClick={() => auditActionMutation.mutate('retry')} className="text-xs"><RotateCcw className="mr-2 h-3.5 w-3.5" />Tentar com segurança</Button> : <Button variant="outline" disabled={!canSendMessages || auditActionMutation.isPending} onClick={() => auditActionMutation.mutate('dismiss')} className="text-xs"><ThumbsDown className="mr-2 h-3.5 w-3.5" />Descartar</Button>}
                  <Button disabled={!canSendMessages || auditActionMutation.isPending} onClick={() => auditActionMutation.mutate('resolve')} className="bg-[#031121] text-xs text-white hover:bg-[#031121]"><CheckCircle2 className="mr-2 h-3.5 w-3.5" />Resolver</Button>
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-2"><Button variant="outline" disabled={!canSendMessages || decisionMutation.isPending} onClick={() => decisionMutation.mutate('registrar_sem_alteracao')} className="text-xs"><ThumbsDown className="mr-2 h-3.5 w-3.5" />Registrar sem alteração</Button><Button disabled={!canSendMessages || decisionMutation.isPending} onClick={() => decisionMutation.mutate('registrar_sugestao')} className="bg-[#031121] text-xs text-white hover:bg-[#031121]"><ThumbsUp className="mr-2 h-3.5 w-3.5" />Registrar sugestão de ajuste</Button></div>
              )}
              <Button variant="ghost" className="w-full text-2xs" onClick={() => navigate(`/app/conversas?thread=${selected.threadId}${selected.matterId ? `&matter=${selected.matterId}` : ''}`)}>Abrir conversa e Caso 360<ChevronRight className="ml-2 h-3.5 w-3.5" /></Button>
            </div>
          ) : <div className="p-8 text-center text-2xs text-muted-foreground">{qualityQuery.isError || auditQuery.isError ? 'Não foi possível carregar a fila de avaliações.' : 'A fila está limpa; nenhuma avaliação detalhada pendente.'}</div>}
        </KlmPanel>

        <div className="space-y-4">
          <KlmPanel tone="black" className="p-5"><ShieldCheck className="h-5 w-5 text-primary" /><h2 className="mt-4 text-lg font-semibold">Intenção de versão</h2><p className="mt-2 text-2xs leading-5 text-white/60">Cria e aprova uma intenção de mudança de versão. Não publica versão nem envia prompt ao provedor.</p><div className="mt-4 space-y-2">{dimensions.slice(0, 4).map((dimension) => <div key={dimension.key} className="flex justify-between border-b border-white/10 py-2 text-2xs"><span className="text-white/45">{dimension.label}</span><strong>{dimension.score}%</strong></div>)}</div><Button disabled={!canManageIntegrations || !selected || publishMutation.isPending} title={!canManageIntegrations ? 'Permissão integrations.manage necessária' : 'Registrar apenas internamente'} onClick={() => publishMutation.mutate()} className="mt-5 w-full text-xs"><Play className="mr-2 h-3.5 w-3.5" />{publishMutation.isPending ? 'Registrando intenção' : 'Registrar intenção de versão'}</Button></KlmPanel>
          <KlmPanel className="p-4"><KlmSectionTitle title="Qualidade por etapa" description="Métricas calculadas sobre os dados atuais." /><div className="mt-4 space-y-4">{dimensions.map((item) => <div key={item.key}><div className="mb-1.5 flex justify-between text-2xs"><span>{item.label}</span><strong>{item.score}%</strong></div><KlmProgress value={item.score} tone={item.score >= 95 ? 'green' : item.score < 80 ? 'red' : 'gold'} /></div>)}</div></KlmPanel>
        </div>
      </div>
    </div>
  );
}
