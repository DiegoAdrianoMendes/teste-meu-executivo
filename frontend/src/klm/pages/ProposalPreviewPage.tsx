import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  Clock3,
  HeartPulse,
  LockKeyhole,
  MessageCircle,
  Printer,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import { useParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { KlmPanel, KlmPill } from '@/klm/components/Primitives';
import { EffectivePlaybookLink } from '@/klm/components/playbooks/EffectivePlaybookLink';
import { klmApi } from '@/klm/api';
import { formatBRL } from '@/lib/formatters';
import { type ContextualPageProps, useContextualNavigate, useContextualSearchParams } from '@/klm/hooks/useContextualRouting';

type ProposalContent = {
  label: string;
  value: string;
};

type ProposalSnapshot = {
  clientName?: string;
  demandLabel?: string;
  objectName?: string | null;
  denialReason?: string | null;
  executiveSummary?: string | null;
  strategyNotes?: string | null;
  nextAction?: string | null;
  financialConditions?: {
    fixedFee?: number | null;
    successPercentage?: number | null;
    projectedRevenue?: number | null;
    reviewStatus?: string;
    paymentMode?: 'initial_plus_success' | 'success_only' | null;
    successBaseType?: 'economic_obligation' | 'moral_damages' | null;
    economicObligation?: boolean | null;
    injunctionMilestoneFee?: number | null;
    obligationFee?: number | null;
  };
  institutionalEvidence?: {
    source?: string;
    casesCompleted?: string;
    casesInProgress?: string;
    injunctionAverage?: string;
    googleRating?: string;
    media?: string[];
  };
};

function EmptyProposal({
  title,
  description,
  retry,
  onNavigate,
}: {
  title: string;
  description: string;
  retry?: () => void;
  onNavigate: (path: string) => void;
}) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-[#f3f1ec] p-6 dark:bg-background">
      <KlmPanel className="max-w-md p-6 text-center">
        <h1 className="text-2xl font-semibold">{title}</h1>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">{description}</p>
        <div className="mt-5 flex justify-center gap-2">
          {retry ? <Button variant="outline" onClick={retry}><RefreshCw className="mr-2 h-4 w-4" />Tentar novamente</Button> : null}
          <Button variant="outline" onClick={() => onNavigate('/app/conversas')}><ArrowLeft className="mr-2 h-4 w-4" />Atendimento</Button>
        </div>
      </KlmPanel>
    </main>
  );
}

function SectionNumber({ children }: { children: ReactNode }) {
  return <span className="flex h-7 w-7 items-center justify-center rounded-full border border-current text-2xs font-semibold">{children}</span>;
}

export default function ProposalPreviewPage({ embedded = false, onNavigate, contextPath }: ContextualPageProps) {
  const queryClient = useQueryClient();
  const navigate = useContextualNavigate({ embedded, onNavigate });
  const routeParams = useParams();
  const contextualPathname = embedded && contextPath
    ? new URL(contextPath, 'http://klm.local').pathname
    : '';
  const matterId = embedded
    ? contextualPathname.split('/').filter(Boolean).at(-1) ?? ''
    : routeParams.matterId ?? '';
  const [searchParams] = useContextualSearchParams({ embedded, contextPath });
  const artifactId = searchParams.get('artifact');
  const matterQuery = useQuery({
    queryKey: ['klm', 'matter', matterId],
    queryFn: () => klmApi.matter(matterId),
    enabled: Boolean(matterId),
  });
  const artifactQuery = useQuery({
    queryKey: ['klm', 'proposal-artifact', matterId, artifactId ?? 'latest'],
    queryFn: async () => {
      if (artifactId) return klmApi.legalArtifact(artifactId);
      const versions = await klmApi.legalArtifacts({ matterId, kind: 'proposal' });
      return versions.sort((left, right) => right.version - left.version)[0] ?? null;
    },
    enabled: Boolean(matterId),
  });
  const submitMutation = useMutation({
    mutationFn: (id: string) => klmApi.submitLegalArtifact(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['klm', 'proposal-artifact', matterId] });
      await queryClient.invalidateQueries({ queryKey: ['klm', 'approvals'] });
    },
  });

  if (!matterId) {
    return <EmptyProposal title="Proposta sem caso vinculado" description="Abra a proposta a partir de um card ou de uma conversa para preservar o contexto." onNavigate={navigate} />;
  }
  if (matterQuery.isPending || artifactQuery.isPending) {
    return <main className="flex min-h-screen items-center justify-center bg-[#f3f1ec] text-sm text-muted-foreground dark:bg-background">Carregando dados persistidos da proposta...</main>;
  }
  if (matterQuery.isError || artifactQuery.isError) {
    return <EmptyProposal title="Não foi possível carregar a proposta" description="Os dados do caso não foram substituídos por conteúdo de exemplo." retry={() => void matterQuery.refetch()} onNavigate={navigate} />;
  }
  if (!matterQuery.data) {
    return <EmptyProposal title="Proposta indisponível" description="Nenhum dossiê persistido foi encontrado para este endereço." onNavigate={navigate} />;
  }
  if (!artifactQuery.data || artifactQuery.data.kind !== 'proposal' || artifactQuery.data.matterId !== matterId) {
    return <EmptyProposal title="Versão da proposta indisponível" description="Nenhuma versão persistida e vinculada a este caso foi encontrada. Gere a proposta pela conversa ou pelo card." retry={() => void artifactQuery.refetch()} onNavigate={navigate} />;
  }

  const matter = matterQuery.data;
  const artifact = artifactQuery.data;
  const snapshot = artifact.content as ProposalSnapshot;
  const conditions = snapshot.financialConditions ?? {};
  const initialFee = conditions.fixedFee;
  const successPercentage = conditions.successPercentage;
  const projectedRevenue = conditions.projectedRevenue;
  const demandLabel = snapshot.demandLabel?.trim() ?? '';
  const objectName = snapshot.objectName?.trim() || 'o tratamento indicado';
  const institutional = snapshot.institutionalEvidence ?? {};
  const proposalContent: ProposalContent[] = [
    { label: 'O que aconteceu', value: snapshot.denialReason?.trim() ?? '' },
    { label: 'Leitura inicial do caso', value: snapshot.executiveSummary?.trim() ?? '' },
    { label: 'Estratégia registrada', value: snapshot.strategyNotes?.trim() ?? '' },
    { label: 'Próximo passo', value: snapshot.nextAction?.trim() ?? '' },
  ].filter((item) => item.value);
  const hasCompleteConditions = initialFee != null && successPercentage != null;
  const reviewStatus = conditions.reviewStatus ?? 'rascunho';
  const isComplete = Boolean(
    demandLabel
    && proposalContent.length
    && hasCompleteConditions
    && ['revisado', 'aprovado'].includes(reviewStatus)
    && artifact.status === 'approved',
  );

  const timeline = [
    { label: 'Análise imediata', detail: 'Conferimos a negativa, a prescrição, o relatório médico e a urgência do caso.' },
    { label: 'Protocolo estratégico', detail: 'A ação é preparada com o pedido urgente e as provas organizadas para decisão.' },
    { label: 'Pedido de liminar', detail: 'Com o protocolo e a nossa atuação proativa, geralmente obtemos decisões liminares em média inferior a 24 horas, conforme as particularidades do juízo e do caso.' },
    { label: 'Cumprimento da decisão', detail: `Acompanhamos a operadora para buscar a efetivação de ${objectName}.` },
    { label: 'Depois da decisão', detail: 'Seguimos no processo, avaliamos eventual recurso e mantemos atualizações frequentes pelo WhatsApp.' },
  ];

  return (
    <main data-testid="proposal-preview" className="min-h-screen bg-[#ebe8e1] py-5 text-[#111318] dark:bg-[#090b0e] print:bg-white print:py-0">
      <div className="mx-auto mb-4 flex w-full max-w-[520px] flex-wrap items-center justify-between gap-3 px-3 print:hidden">
        <Button variant="outline" className="bg-white/90 dark:bg-card" onClick={() => navigate(`/app/leads?matter=${matter.id}`)}><ArrowLeft className="mr-2 h-4 w-4" />Dossiê</Button>
        <div className="flex items-center gap-2">
          <KlmPill tone={isComplete ? 'green' : 'gold'}>{isComplete ? `v${artifact.version} aprovada` : `v${artifact.version} · ${artifact.status.replaceAll('_', ' ')}`}</KlmPill>
          {artifact.status === 'draft' ? <Button variant="outline" className="bg-white/90 dark:bg-card" disabled={submitMutation.isPending} onClick={() => submitMutation.mutate(artifact.id)}><ShieldCheck className="mr-2 h-4 w-4" />Aprovação</Button> : null}
          <Button className="bg-[#050505] text-white hover:bg-[#1c3254]" disabled={!isComplete} title={isComplete ? 'Salvar a proposta revisada em PDF' : 'Revise o dossiê e as condições antes de salvar o PDF'} onClick={() => window.print()}><Printer className="mr-2 h-4 w-4" />Salvar PDF</Button>
        </div>
        <EffectivePlaybookLink
          selectors={{
            area: 'negativas_plano_saude',
            stage: 'proposta',
            offer: 'servicos_advocaticios',
          }}
          className="w-full justify-start bg-white/90 dark:bg-card"
          onNavigate={embedded ? navigate : undefined}
        />
      </div>

      <article className="mx-auto w-full max-w-[480px] overflow-hidden bg-white shadow-[0_22px_70px_rgba(10,12,16,.18)] dark:bg-[#101216] print:max-w-none print:overflow-visible print:shadow-none">
        <section className="relative flex min-h-[760px] flex-col overflow-hidden bg-[#050505] px-8 py-9 text-white print:min-h-[100vh] print:break-after-page">
          <div className="relative z-10 flex items-center justify-between gap-4">
            <div>
              <p className="text-2xl font-semibold text-primary">Meu Executivo</p>
              <p className="mt-1 text-2xs uppercase text-white/55">Direito à saúde em todo o Brasil</p>
            </div>
            <SectionNumber>01</SectionNumber>
          </div>

          <div className="relative z-10 mt-20 max-w-[340px]">
            <div className="flex h-11 w-11 items-center justify-center rounded-full border border-primary/70 bg-primary/10"><HeartPulse className="h-5 w-5 text-primary" /></div>
            <p className="mt-7 text-2xs font-semibold uppercase text-primary">Proposta personalizada para {snapshot.clientName || 'cliente identificado no card'}</p>
            <h1 className="mt-4 text-4xl font-medium">Seu tratamento não pode esperar.</h1>
            <p className="mt-5 text-sm leading-6 text-white/72">Estratégia jurídica para buscar o acesso a <strong className="font-semibold text-white">{objectName}</strong> diante da negativa do plano de saúde.</p>
          </div>

          <img src="/proposal-media/brasil-outline.svg" alt="Contorno do mapa do Brasil" className="pointer-events-none absolute -bottom-5 -right-14 h-[330px] w-[330px] opacity-[.12] invert" />
          <div className="relative z-10 mt-auto flex items-end justify-between gap-5 border-t border-white/15 pt-5">
            <div><p className="text-2xs text-white/45">Atuação</p><p className="mt-1 text-xs font-semibold">Nacional e 100% on-line</p></div>
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary text-primary-foreground"><ArrowRight className="h-4 w-4" /></div>
          </div>
        </section>

        <section className="flex min-h-[760px] flex-col bg-white px-8 py-9 text-[#111318] print:min-h-[100vh] print:break-after-page">
          <div className="flex items-center justify-between text-primary"><p className="text-2xs font-semibold uppercase">Por que a Meu Executivo</p><SectionNumber>02</SectionNumber></div>
          <h2 className="mt-10 text-3xl font-medium">Experiência para agir com rapidez quando a saúde é urgente.</h2>
          <p className="mt-5 text-sm leading-6 text-[#5f6670]">Somos referência nacional em negativas de plano de saúde, com atuação focada, atendimento próximo e estratégia construída a partir dos documentos do seu caso.</p>

          <div className="mt-9 grid grid-cols-2 border-y border-[#ded9cf]">
            {[
              [institutional.casesCompleted || 'Não registrado', 'Casos concluídos'],
              [institutional.casesInProgress || 'Não registrado', 'Casos em andamento'],
              [institutional.injunctionAverage || 'Não registrado', 'Média para liminares'],
              [institutional.googleRating || 'Não registrado', 'Estrelas no Google'],
            ].map(([value, label], index) => (
              <div key={label} className={`py-5 ${index % 2 === 0 ? 'border-r border-[#ded9cf] pr-4' : 'pl-4'} ${index < 2 ? 'border-b border-[#ded9cf]' : ''}`}>
                <strong className="block text-2xl font-semibold tabular-nums text-[#0c1016]">{value}</strong>
                <span className="mt-1 block text-2xs uppercase text-[#737984]">{label}</span>
              </div>
            ))}
          </div>

          <div className="mt-8">
            <p className="text-2xs font-semibold uppercase text-primary">Meu Executivo na imprensa</p>
            <div className="mt-4 grid grid-cols-3 items-center gap-3 border-y border-[#ded9cf] py-5">
              <div className="flex h-12 items-center justify-center"><img src="/proposal-media/g1.png" alt="G1" className="h-10 w-10 object-contain" /></div>
              <div className="flex h-12 items-center justify-center border-x border-[#ded9cf]"><img src="/proposal-media/r7.svg" alt="R7" className="h-10 w-10 object-contain" /></div>
              <div className="flex h-12 items-center justify-center"><img src="/proposal-media/estadao.png" alt="Estadão" className="h-10 w-[100px] object-contain" /></div>
            </div>
            <p className="mt-3 text-2xs leading-4 text-[#8a9098]">Logos identificam veículos nos quais o escritório informa ter aparecido; não representam endosso.</p>
          </div>

          <div className="mt-auto flex items-center gap-3 border-t border-[#ded9cf] pt-5"><ShieldCheck className="h-4 w-4 text-[#0b7435]" /><p className="text-2xs leading-4 text-[#5f6670]">Cada caso é revisado individualmente. Velocidade não substitui precisão jurídica.</p></div>
        </section>

        <section className="flex min-h-[760px] flex-col bg-[#f4f1eb] px-8 py-9 text-[#111318] print:min-h-[100vh] print:break-after-page">
          <div className="flex items-center justify-between text-primary"><p className="text-2xs font-semibold uppercase">Seu caso e o caminho</p><SectionNumber>03</SectionNumber></div>
          <h2 className="mt-10 text-3xl font-medium">Da negativa à busca pela efetivação do tratamento.</h2>
          <div className="mt-6 border-l-2 border-[#a53e3c] pl-4">
            <p className="text-2xs font-semibold uppercase text-[#a53e3c]">A dor que precisa ser enfrentada agora</p>
            <p className="mt-2 text-sm leading-6">{snapshot.denialReason?.trim() || `O plano negou ou não autorizou ${objectName}; o motivo formal ainda precisa ser confirmado nos documentos.`}</p>
            {typeof artifact.sourceSnapshot.medicalUrgencyHours === 'number' ? <p className="mt-2 text-2xs font-semibold text-[#a53e3c]">Urgência médica registrada na versão: {artifact.sourceSnapshot.medicalUrgencyHours} horas.</p> : null}
          </div>

          <div className="mt-8 space-y-0">
            {timeline.map((step, index) => (
              <div key={step.label} className="grid grid-cols-[30px_1fr] gap-3">
                <div className="flex flex-col items-center"><span className={`flex h-7 w-7 items-center justify-center rounded-full border text-2xs font-semibold ${index < 3 ? 'border-primary bg-primary text-primary-foreground' : 'border-[#c9c3b8] bg-white text-[#6c7179]'}`}>{index + 1}</span>{index < timeline.length - 1 ? <span className="h-full min-h-12 w-px bg-[#d6d0c5]" /> : null}</div>
                <div className="pb-5"><p className="text-xs font-semibold">{step.label}</p><p className="mt-1 text-2xs leading-4 text-[#676d76]">{step.detail}</p></div>
              </div>
            ))}
          </div>

          {proposalContent.length ? (
            <div className="mt-auto border-t border-[#d6d0c5] pt-5">
              <p className="text-2xs font-semibold uppercase text-primary">Leitura registrada no dossiê</p>
              <div className="mt-3 grid gap-2">
                {proposalContent.map((item) => (
                  <div key={item.label} className="grid grid-cols-[105px_1fr] gap-3 border-t border-[#d6d0c5] pt-2 first:border-t-0 first:pt-0">
                    <p className="text-2xs font-semibold uppercase text-primary">{item.label}</p>
                    <p className="text-2xs leading-4 text-[#676d76]">{item.value}</p>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </section>

        <section className="flex min-h-[760px] flex-col bg-white px-8 py-9 text-[#111318] print:min-h-[100vh] print:break-after-page">
          <div className="flex items-center justify-between text-primary"><p className="text-2xs font-semibold uppercase">Investimento e condições</p><SectionNumber>04</SectionNumber></div>
          <h2 className="mt-10 text-3xl font-medium">Condições transparentes, vinculadas ao seu caso.</h2>

          {hasCompleteConditions ? (
            <div className="mt-8 divide-y divide-[#ded9cf] border-y border-[#ded9cf]">
              <div className="flex items-end justify-between gap-5 py-5"><div><p className="text-2xs uppercase text-[#777d85]">Honorários iniciais</p><p className="mt-1 text-2xs text-[#777d85]">Entrada para início da atuação</p></div><strong className="text-2xl font-semibold tabular-nums">{formatBRL(initialFee)}</strong></div>
              {conditions.injunctionMilestoneFee != null && conditions.injunctionMilestoneFee > 0 ? <div className="flex items-end justify-between gap-5 py-5"><div><p className="text-2xs uppercase text-[#777d85]">Marco da liminar</p><p className="mt-1 text-2xs text-[#777d85]">Honorários intermediários</p></div><strong className="text-xl font-semibold tabular-nums">{formatBRL(conditions.injunctionMilestoneFee)}</strong></div> : null}
              {conditions.obligationFee != null && conditions.obligationFee > 0 ? <div className="flex items-end justify-between gap-5 py-5"><div><p className="text-2xs uppercase text-[#777d85]">Obrigação de fazer</p><p className="mt-1 text-2xs text-[#777d85]">Componente econômico revisado</p></div><strong className="text-xl font-semibold tabular-nums">{formatBRL(conditions.obligationFee)}</strong></div> : null}
              <div className="flex items-end justify-between gap-5 py-5"><div><p className="text-2xs uppercase text-[#777d85]">Honorários de êxito</p><p className="mt-1 text-2xs text-[#777d85]">{conditions.successBaseType === 'moral_damages' ? `${successPercentage}% sobre o dano moral definido no contrato` : conditions.economicObligation ? `${successPercentage}% sobre o conteúdo econômico da obrigação` : `${successPercentage}% sobre a base contratual revisada`}</p></div><strong className="text-2xl font-semibold tabular-nums">{successPercentage}%</strong></div>
            </div>
          ) : (
            <div className="mt-8 border-y border-[#ded9cf] py-7">
              <p className="text-sm font-semibold">Condições ainda não concluídas</p>
              <p className="mt-2 text-2xs leading-5 text-[#6c7179]">Honorários ainda não definidos no dossiê. Nenhum valor padrão foi aplicado.</p>
              <Button variant="outline" size="sm" className="mt-4" onClick={() => navigate(`/app/leads?matter=${matter.id}`)}>Revisar dossiê</Button>
            </div>
          )}

          <div className="mt-7 grid grid-cols-2 gap-px overflow-hidden rounded-lg bg-[#ded9cf] border border-[#ded9cf]">
            <div className="bg-[#f6f4ef] p-4"><p className="text-2xs uppercase text-[#777d85]">Forma de contratação</p><p className="mt-2 text-xs font-semibold">{conditions.paymentMode === 'success_only' ? 'Integralmente no êxito' : conditions.paymentMode === 'initial_plus_success' ? 'Entrada + êxito' : 'A definir'}</p></div>
            <div className="bg-[#f6f4ef] p-4"><p className="text-2xs uppercase text-[#777d85]">Estado da revisão</p><p className="mt-2 text-xs font-semibold">{reviewStatus.replaceAll('_', ' ')} · versão {artifact.version} {artifact.status.replaceAll('_', ' ')}</p></div>
          </div>

          {projectedRevenue != null ? <div className="mt-5 flex items-start gap-3 rounded-lg bg-[#0d121a] p-4 text-white print:hidden"><LockKeyhole className="mt-0.5 h-4 w-4 shrink-0 text-primary" /><div><p className="text-2xs font-semibold uppercase text-primary">Controle interno, não incluído no PDF</p><strong className="mt-1 block text-sm">{formatBRL(projectedRevenue)}</strong><p className="mt-1 text-2xs leading-4 text-white/65">Projeção operacional; não substitui as condições contratuais acima.</p></div></div> : null}

          <div className="mt-auto border-t border-[#ded9cf] pt-5"><p className="text-2xs leading-4 text-[#777d85]">A contratação e o envio para assinatura somente avançam após sua concordância e a revisão humana do escritório.</p></div>
        </section>

        <section className="flex min-h-[760px] flex-col bg-[#07100c] px-8 py-9 text-white print:min-h-[100vh]">
          <div className="flex items-center justify-between text-primary"><p className="text-2xs font-semibold uppercase">Seguimos ao seu lado</p><SectionNumber>05</SectionNumber></div>
          <div className="mt-16 flex h-12 w-12 items-center justify-center rounded-full bg-primary text-primary-foreground"><MessageCircle className="h-5 w-5" /></div>
          <h2 className="mt-7 text-4xl font-medium">O WhatsApp continuará sempre disponível para você.</h2>
          <p className="mt-5 text-sm leading-6 text-white/65">Nossa atuação não termina com a primeira decisão. Você terá acompanhamento do processo, orientação sobre cada etapa e um canal direto para dúvidas e documentos.</p>

          <div className="mt-9 divide-y divide-white/12 border-y border-white/12">
            {[
              ['Acompanhamento contínuo', 'Monitoramento do cumprimento e das movimentações relevantes.'],
              ['Eventual recurso', 'Análise da medida adequada se houver decisão desfavorável ou impugnação.'],
              ['Atualizações frequentes', 'Comunicação objetiva pelo WhatsApp sobre o que aconteceu e o próximo passo.'],
            ].map(([title, description]) => <div key={title} className="flex gap-3 py-4"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" /><div><p className="text-xs font-semibold">{title}</p><p className="mt-1 text-2xs leading-4 text-white/55">{description}</p></div></div>)}
          </div>

          <div className="mt-auto">
            <div className="flex items-center gap-2 text-primary"><Sparkles className="h-4 w-4" /><p className="text-2xs font-semibold">Próximo passo</p></div>
            <p className="mt-3 text-xs leading-5 text-white/72">Revise as condições, tire suas dúvidas e confirme com o atendimento quando estiver confortável para avançar.</p>
            <div className="mt-6 flex items-center justify-between border-t border-white/15 pt-5"><div><p className="text-xl font-semibold text-primary">Meu Executivo</p><p className="mt-1 text-2xs text-white/45">Atendimento on-line em todo o Brasil</p></div><div className="flex h-9 w-9 items-center justify-center rounded-full border border-white/20"><Check className="h-4 w-4" /></div></div>
          </div>
        </section>
      </article>

      <div className="mx-auto mt-4 flex w-full max-w-[520px] items-center justify-between gap-3 px-3 text-2xs text-muted-foreground print:hidden">
        <span><Clock3 className="mr-1 inline h-3 w-3" />Prévia gerada com o dossiê local</span>
        <button type="button" className="inline-flex items-center gap-1 hover:text-foreground" onClick={() => navigate('/app/conversas')}>Voltar ao atendimento<ArrowRight className="h-3 w-3" /></button>
      </div>
    </main>
  );
}
