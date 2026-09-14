import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  CalendarPlus,
  Check,
  CircleDollarSign,
  FileSearch,
  FileText,
  History,
  Layers,
  ListTodo,
  PencilLine,
  ShieldOff,
  Sparkles,
  UserRoundCheck,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { BookingLinkDialog } from '@/klm/components/BookingLinkDialog';
import { CrmViewNav } from '@/klm/components/CrmViewNav';
import { NativeCRMOperationsPanel } from '@/klm/components/NativeCRMOperationsPanel';
import { TaskCollaborationDrawer } from '@/klm/components/TaskCollaborationDrawer';
import { TreatmentCostPanel } from '@/klm/components/TreatmentCostPanel';
import { LegalBatchPanel } from '@/klm/components/legal/LegalBatchPanel';
import { useCreateLegalBatchFromContext } from '@/klm/api/useLegalBatches';
import { KlmMetricCard, KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { klmApi } from '@/klm/api';
import { buildTimelineRows } from '@/klm/timeline';
import { formatBRL } from '@/lib/formatters';
import { type ContextualPageProps, useContextualSearchParams } from '@/klm/hooks/useContextualRouting';
import { useOptionalAppStore } from '@/store/app-context';
import { classifyTaskError, taskErrorMessage } from '@/services/tasks';

const expectedDocuments = [
  { name: 'Negativa do plano', aliases: ['negativa', 'indeferimento'] },
  { name: 'Prescrição médica', aliases: ['prescricao', 'pedido medico', 'receita'] },
  { name: 'Relatório médico', aliases: ['relatorio medico', 'laudo medico'] },
  { name: 'Carteirinha do plano', aliases: ['carteirinha', 'cartao do plano'] },
  { name: 'Contrato do plano', aliases: ['contrato do plano', 'apolice'] },
] as const;

const money = formatBRL;

type ProjectionPremises = {
  initialFee: number | null;
  successPercent: number | null;
  objectAnnualCost: number | null;
  probability: number | null;
  economicObligation: boolean;
  paymentMode: 'initial_plus_success' | 'success_only';
  successBaseType: 'economic_obligation' | 'moral_damages';
  moralDamagesEstimate: number | null;
  injunctionMilestoneFee: number | null;
  obligationFee: number | null;
  reviewStatus: string;
};

type DocumentRow = {
  key: string;
  name: string;
  status: string;
  tone: 'green' | 'gold' | 'red';
  canRequest: boolean;
};

type RagScope = 'case_sources' | 'conversation' | 'documents' | 'internal' | 'all';

const conversationSourceTypes = new Set(['conversation_message', 'whatsapp_message']);
const documentSourceTypes = new Set(['document', 'email_attachment', 'local_document', 'whatsapp_media']);

function normalizedText(value: string): string {
  return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('pt-BR').trim();
}

function nullableNumber(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function documentStatus(status: string): Pick<DocumentRow, 'status' | 'tone'> {
  const normalized = normalizedText(status).replaceAll('_', ' ');
  if (['completo', 'concluido', 'aprovado', 'confirmed'].includes(normalized)) return { status: 'OK', tone: 'green' };
  if (['rejeitado', 'falhou', 'invalido'].includes(normalized)) return { status: status.replaceAll('_', ' '), tone: 'red' };
  return { status: status.replaceAll('_', ' ') || 'Status não informado', tone: 'gold' };
}

function ragSourceLabel(sourceType: string): string {
  if (conversationSourceTypes.has(sourceType)) return 'Conversa com o cliente';
  if (sourceType === 'internal_conversation') return 'Nota interna restrita';
  if (documentSourceTypes.has(sourceType)) return 'Documento ou anexo';
  if (sourceType === 'human_override') return 'Decisão humana';
  return 'Registro do caso';
}

export default function LeadPage({ embedded = false, onNavigate, contextPath }: ContextualPageProps) {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useContextualSearchParams({ embedded, contextPath });
  const funnelQuery = useQuery({ queryKey: ['klm', 'funnel'], queryFn: klmApi.funnel });
  const threadsQuery = useQuery({ queryKey: ['klm', 'threads'], queryFn: klmApi.threads });
  const [selectedMatterId, setSelectedMatterId] = useState(() => searchParams.get('matter') ?? '');
  const contractedClientsOnly = searchParams.get('scope') === 'clientes';
  const matterQuery = useQuery({ queryKey: ['klm', 'matter', selectedMatterId], queryFn: () => klmApi.matter(selectedMatterId), enabled: Boolean(selectedMatterId) });
  const selectedThread = threadsQuery.data?.find((thread) => thread.matterId === selectedMatterId);
  const messagesQuery = useQuery({ queryKey: ['klm', 'messages', selectedThread?.id], queryFn: () => klmApi.messages(selectedThread!.id), enabled: Boolean(selectedThread) });
  const pricingPolicyQuery = useQuery({ queryKey: ['klm', 'legal-pricing-policy'], queryFn: klmApi.legalPricingPolicy });
  const [notice, setNotice] = useState<string | null>(null);
  const [bookingDialogOpen, setBookingDialogOpen] = useState(false);
  const [batchDialogOpen, setBatchDialogOpen] = useState(false);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const appStore = useOptionalAppStore();
  const canManageLegal = appStore?.can('legal', 'manage') ?? true;
  const canUseTasks = appStore?.canUseCapability?.('active_tasks') ?? true;
  const createBatch = useCreateLegalBatchFromContext();
  const [ragOpen, setRagOpen] = useState(() => searchParams.get('panel') === 'rag');
  const [ragScope, setRagScope] = useState<RagScope>('case_sources');
  const [ragSearch, setRagSearch] = useState('');
  const [simulationOpen, setSimulationOpen] = useState(false);
  const [premises, setPremises] = useState<ProjectionPremises | null>(null);
  const matter = matterQuery.data;
  const selectedCard = funnelQuery.data?.cards.find((card) => card.id === selectedMatterId);
  const availableCards = useMemo(
    () => (funnelQuery.data?.cards ?? []).filter((card) => !contractedClientsOnly || card.stage === 'ganho'),
    [contractedClientsOnly, funnelQuery.data?.cards],
  );
  const conversationRagCount = matter?.knowledgeChunks.filter((chunk) => conversationSourceTypes.has(chunk.sourceType)).length ?? 0;
  const documentRagCount = matter?.knowledgeChunks.filter((chunk) => documentSourceTypes.has(chunk.sourceType)).length ?? 0;
  const internalRagCount = matter?.knowledgeChunks.filter((chunk) => chunk.sourceType === 'internal_conversation').length ?? 0;

  const filteredRagChunks = useMemo(() => {
    const normalizedSearch = normalizedText(ragSearch);
    return (matter?.knowledgeChunks ?? []).filter((chunk) => {
      const matchesScope = ragScope === 'all'
        || (ragScope === 'conversation' && conversationSourceTypes.has(chunk.sourceType))
        || (ragScope === 'documents' && documentSourceTypes.has(chunk.sourceType))
        || (ragScope === 'internal' && chunk.sourceType === 'internal_conversation')
        || (ragScope === 'case_sources' && (conversationSourceTypes.has(chunk.sourceType) || documentSourceTypes.has(chunk.sourceType)));
      if (!matchesScope) return false;
      if (!normalizedSearch) return true;
      return normalizedText(`${chunk.title ?? ''} ${chunk.summary ?? ''} ${chunk.contentText} ${ragSourceLabel(chunk.sourceType)}`).includes(normalizedSearch);
    });
  }, [matter?.knowledgeChunks, ragScope, ragSearch]);

  useEffect(() => {
    const selectedIsAvailable = availableCards.some((card) => card.id === selectedMatterId);
    if (selectedIsAvailable || !availableCards[0]) return;
    const matterId = availableCards[0].id;
    setSelectedMatterId(matterId);
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('matter', matterId);
    setSearchParams(nextParams);
  }, [availableCards, searchParams, selectedMatterId, setSearchParams]);

  useEffect(() => {
    const requestedMatter = searchParams.get('matter');
    if (requestedMatter && requestedMatter !== selectedMatterId) setSelectedMatterId(requestedMatter);
    if (searchParams.get('panel') === 'rag') setRagOpen(true);
  }, [searchParams, selectedMatterId]);

  const changeRagOpen = (open: boolean) => {
    setRagOpen(open);
    const nextParams = new URLSearchParams(searchParams);
    if (open) nextParams.set('panel', 'rag');
    else nextParams.delete('panel');
    setSearchParams(nextParams);
  };

  useEffect(() => {
    if (!matter) return;
    const storedPremises = matter.revenueProjection?.projectionPremises ?? {};
    const persistedProbability = matter.revenueProjection?.successProbability;
    setPremises({
      initialFee: storedPremises.fixedFee ?? matter.honorariumEstimate?.fixedFee ?? null,
      successPercent: storedPremises.successPercentage ?? matter.honorariumEstimate?.quotaLitisPercentage ?? null,
      objectAnnualCost: storedPremises.economicValueAnnual ?? matter.economicValueAnnual ?? matter.revenueProjection?.estimatedClaimValue ?? null,
      probability: storedPremises.probability ?? (persistedProbability == null ? null : Math.round(persistedProbability * 100)),
      economicObligation: storedPremises.economicObligation ?? (matter.economicValueAnnual != null),
      paymentMode: storedPremises.paymentMode ?? (matter.honorariumEstimate?.contractType === 'success_only' ? 'success_only' : 'initial_plus_success'),
      successBaseType: storedPremises.successBaseType ?? 'economic_obligation',
      moralDamagesEstimate: storedPremises.moralDamagesEstimate ?? null,
      injunctionMilestoneFee: storedPremises.injunctionMilestoneFee ?? null,
      obligationFee: storedPremises.obligationFee ?? null,
      reviewStatus: storedPremises.reviewStatus ?? 'rascunho',
    });
  }, [matter]);

  const documentRows = useMemo(() => {
    if (!matter) return [];
    const matchedDocumentIds = new Set<string>();
    const requirements: DocumentRow[] = expectedDocuments.map((requirement) => {
      const aliases = [requirement.name, ...requirement.aliases].map(normalizedText);
      const document = matter.documents.find((item) => {
        const haystack = normalizedText(`${item.name} ${item.type ?? ''}`);
        return aliases.some((alias) => haystack.includes(alias));
      });
      if (!document) return { key: `missing:${requirement.name}`, name: requirement.name, status: 'Pedir', tone: 'red', canRequest: true };
      matchedDocumentIds.add(document.id);
      return { key: document.id, name: requirement.name, ...documentStatus(document.status), canRequest: false };
    });
    const additionalDocuments = matter.documents
      .filter((document) => !matchedDocumentIds.has(document.id))
      .map((document): DocumentRow => ({ key: document.id, name: document.name, ...documentStatus(document.status), canRequest: false }));
    return [...requirements, ...additionalDocuments];
  }, [matter]);

  const timelineRows = buildTimelineRows(messagesQuery.data, matter?.timeline).map((row) => ({
    ...row,
    detail: row.detail?.replace(/(?:^|\s)Fonte:\s*[^\n]+/gi, '').trim() || undefined,
    agent: /^(?:agent action|klm:|local[_-]deterministic)/i.test(row.agent) ? 'Automação do caso' : row.agent,
  }));

  const closerMutation = useMutation({
    mutationFn: () => {
      if (!funnelQuery.data || !selectedCard) throw new Error('Funil local ainda não carregado');
      return klmApi.moveFunnel(selectedMatterId, 'proposta', {
        expectedBoardVersion: funnelQuery.data.boardVersion,
        sourceColumnId: selectedCard.stage,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'matter', selectedMatterId] });
      setNotice('Caso movido para Proposta/Closer no funil local, sem dispatch de agente.');
    },
    onError: () => setNotice('Não foi possível mover o caso no funil local.'),
  });

  const taskMutation = useMutation({
    mutationFn: (payload: { title: string; description: string; priority: 'alta' | 'media' | 'baixa' }) => {
      if (!selectedMatterId) throw new Error('Caso local indisponível');
      if (!canUseTasks) throw new Error('Novas tarefas estão bloqueadas pela capacidade efetiva da conta.');
      return klmApi.createMatterTask(selectedMatterId, {
        ...payload,
        isAiGenerated: false,
      });
    },
    onSuccess: (task) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'matter', selectedMatterId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
      setNotice(`Tarefa “${task.title}” criada no caso e registrada na timeline.`);
    },
    onError: (error) => setNotice(
      classifyTaskError(error) === 'quota'
        ? taskErrorMessage(error, 'O limite de tarefas ativas desta conta foi atingido.')
        : taskErrorMessage(error, 'Não foi possível criar a tarefa no caso.'),
    ),
  });

  const createMatterTask = (title: string, description: string, priority: 'alta' | 'media' | 'baixa' = 'media') => {
    taskMutation.mutate({ title, description, priority });
  };

  const handleProcessBatch = () => {
    if (!matter?.client.id) return;
    createBatch.mutate(
      {
        name: `Lote — ${matter.client.name}`,
        externalRef: `legal-batch:cliente:${matter.client.id}`,
        clientId: matter.client.id,
      },
      {
        onSuccess: (batch) => { setBatchId(batch.id); setBatchDialogOpen(true); },
        onError: () => setNotice('Não foi possível abrir o lote deste cliente.'),
      },
    );
  };

  const projectionMutation = useMutation({
    mutationFn: () => {
      if (!selectedMatterId) throw new Error('Caso local indisponível');
      if (!premises) throw new Error('Premissas ainda não carregadas');
      if (premises.initialFee == null || premises.successPercent == null || premises.objectAnnualCost == null || premises.probability == null) {
        throw new Error('Preencha todas as premissas obrigatórias antes de salvar');
      }
      if (premises.successBaseType === 'moral_damages' && premises.moralDamagesEstimate == null) {
        throw new Error('Informe a estimativa de dano moral antes de salvar');
      }
      return klmApi.updateFinancialProjection(selectedMatterId, {
        fixedFee: premises.initialFee,
        successPercentage: premises.successPercent,
        economicValueAnnual: premises.objectAnnualCost,
        probability: premises.probability,
        economicObligation: premises.economicObligation,
        paymentMode: premises.paymentMode,
        successBaseType: premises.successBaseType,
        moralDamagesEstimate: premises.moralDamagesEstimate,
        injunctionMilestoneFee: premises.injunctionMilestoneFee ?? 0,
        obligationFee: premises.obligationFee ?? 0,
        reviewStatus: premises.reviewStatus,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'matter', selectedMatterId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
      setSimulationOpen(false);
      setNotice('Premissas econômicas versionadas no dossiê local e registradas na auditoria.');
    },
    onError: () => setNotice('Não foi possível versionar as premissas no dossiê local.'),
  });

  const projectionValidationMessage = useMemo(() => {
    if (!premises) return 'Aguardando as condições persistidas do caso.';
    if (premises.initialFee == null) return 'Informe os honorários iniciais.';
    if (premises.successPercent == null) return 'Informe o percentual de êxito.';
    if (premises.objectAnnualCost == null) return 'Informe o valor econômico anual do objeto.';
    if (premises.probability == null) return 'Informe a probabilidade comercial.';
    if (premises.successBaseType === 'moral_damages' && premises.moralDamagesEstimate == null) return 'Informe a estimativa de dano moral.';
    if (premises.successBaseType === 'economic_obligation' && !premises.economicObligation) return 'Ative a obrigação econômica ou selecione dano moral como base do êxito.';
    if (premises.paymentMode === 'success_only' && premises.initialFee !== 0) return 'No modo integralmente no êxito, a entrada deve ser zero.';
    if (premises.paymentMode === 'initial_plus_success' && pricingPolicyQuery.data && premises.initialFee < pricingPolicyQuery.data.minimumInitialFee) {
      return `A entrada mínima configurada é ${formatBRL(pricingPolicyQuery.data.minimumInitialFee)}.`;
    }
    return null;
  }, [premises, pricingPolicyQuery.data]);

  const projections = useMemo(() => {
    if (!premises || projectionValidationMessage) return null;
    const probability = premises.probability! / 100;
    const successBase = premises.successBaseType === 'moral_damages' ? premises.moralDamagesEstimate! : premises.objectAnnualCost!;
    const fixedComponents = premises.initialFee! + (premises.injunctionMilestoneFee ?? 0) + (premises.obligationFee ?? 0);
    const likely = fixedComponents + (successBase * premises.successPercent! / 100);
    return {
      conservative: Math.round((fixedComponents + successBase * 0.12) * probability),
      likely: Math.round(likely * probability),
      aggressive: Math.round((fixedComponents + successBase * 0.3) * Math.min(0.9, probability + 0.12)),
      fixedOnly: Math.round(fixedComponents * probability),
    };
  }, [premises, projectionValidationMessage]);

  if (funnelQuery.isPending && !funnelQuery.data) {
    return (
      <KlmPanel className="mx-auto max-w-xl space-y-2 p-6">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-4 w-3/4" />
      </KlmPanel>
    );
  }

  if (funnelQuery.isError && !funnelQuery.data) {
    return (
      <KlmPanel className="mx-auto max-w-xl p-6 text-center">
        <h2 className="text-lg font-semibold">Não foi possível carregar os casos</h2>
        <p className="mt-2 text-xs text-muted-foreground">Nenhum card de exemplo foi exibido no lugar dos dados locais.</p>
        <Button variant="outline" className="mt-4" onClick={() => void funnelQuery.refetch()}>Tentar novamente</Button>
      </KlmPanel>
    );
  }

  if (!availableCards.length) {
    return (
      <KlmPanel className="mx-auto max-w-xl p-6 text-center">
        <h2 className="text-lg font-semibold">{contractedClientsOnly ? 'Nenhum cliente contratado' : 'Nenhum caso disponível'}</h2>
        <p className="mt-2 text-xs text-muted-foreground">{contractedClientsOnly ? 'Os casos aparecerão aqui quando forem marcados como ganhos no CRM.' : 'O funil ainda não tem casos para abrir no Caso 360.'}</p>
        <Button asChild variant="outline" className="mt-4"><Link to="/app/conversas">Iniciar uma conversa</Link></Button>
      </KlmPanel>
    );
  }

  if (!selectedMatterId || matterQuery.isPending) {
    return (
      <KlmPanel className="mx-auto max-w-xl space-y-2 p-6">
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-1/3" />
      </KlmPanel>
    );
  }

  if (matterQuery.isError || !matter) {
    return (
      <KlmPanel className="mx-auto max-w-xl p-6 text-center">
        <h2 className="text-lg font-semibold">Dossiê indisponível</h2>
        <p className="mt-2 text-xs text-muted-foreground">Não foi possível carregar este caso. A interface não substituiu os dados por conteúdo de exemplo.</p>
        <Button variant="outline" className="mt-4" onClick={() => void matterQuery.refetch()}>Tentar novamente</Button>
      </KlmPanel>
    );
  }

  return (
    <div className="mx-auto max-w-[1220px] space-y-5">
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Meu Executivo / Operação jurídica</p>
          <h2 className="mt-2 text-2xl font-semibold">Caso 360</h2>
          <p className="mt-1 text-xs text-muted-foreground">{contractedClientsOnly ? 'Clientes contratados com documentos, histórico, responsáveis e próximos passos jurídicos.' : 'Ficha operacional por subtema, documentos, CRM, RAG, economia e próximos passos.'}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Select value={selectedMatterId} onValueChange={(value) => { const nextParams = new URLSearchParams(searchParams); nextParams.set('matter', value); setSelectedMatterId(value); setSearchParams(nextParams); }}><SelectTrigger className="h-9 w-full text-xs sm:w-[230px]"><SelectValue placeholder="Selecionar caso" /></SelectTrigger><SelectContent>{availableCards.map((card) => <SelectItem key={card.id} value={card.id}>{card.name} · {card.object}</SelectItem>)}</SelectContent></Select>
          <Button variant="outline" className="h-9 text-xs" disabled={!matter} onClick={() => setBookingDialogOpen(true)}><CalendarPlus className="mr-2 h-3.5 w-3.5" />Enviar agenda</Button>
          <Button
            variant="outline"
            className="h-9 text-xs"
            disabled={!matter || !canManageLegal || createBatch.isPending}
            title={!canManageLegal ? 'Permissão legal.manage necessária' : undefined}
            onClick={handleProcessBatch}
          >
            <Layers className="mr-2 h-3.5 w-3.5" />{createBatch.isPending ? 'Abrindo lote' : 'Processar em lote'}
          </Button>
          <Button className="h-9 bg-[#031121] text-xs text-white hover:bg-[#031121]" disabled={!selectedMatterId || closerMutation.isPending} onClick={() => closerMutation.mutate()}><UserRoundCheck className="mr-2 h-3.5 w-3.5" />{closerMutation.isPending ? 'Movendo' : 'Mover para Closer'}</Button>
        </div>
      </div>

      <Dialog open={batchDialogOpen} onOpenChange={setBatchDialogOpen}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Lote deste cliente</DialogTitle>
            <DialogDescription>Recorte por contexto — todas as demandas jurídicas de {matter?.client.name ?? 'este cliente'} num único lote.</DialogDescription>
          </DialogHeader>
          {batchId && <LegalBatchPanel batchId={batchId} canManage={canManageLegal} />}
        </DialogContent>
      </Dialog>

      <CrmViewNav active="case" matterId={selectedMatterId} />

      {notice && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-primary/35 bg-primary/10 px-3 py-2 text-2xs">
          <span>{notice}</span><Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setNotice(null)}>Fechar</Button>
        </div>
      )}

      <NativeCRMOperationsPanel view="matter" matterId={selectedMatterId} />

      <KlmPanel className="p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <KlmSectionTitle
            title="Tarefas do caso e do cliente"
            description="Responsáveis, executores, revisão e prazos preservam o vínculo com este Caso 360."
          />
          <Button asChild size="sm" variant="outline" className="h-8 text-2xs">
            <Link to={`/app/tarefas?matter=${encodeURIComponent(selectedMatterId)}`}>
              <ListTodo className="mr-1.5 h-3.5 w-3.5" />Central de tarefas
            </Link>
          </Button>
        </div>
        {matter.tasks?.length ? (
          <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {matter.tasks.map((task) => (
              <button
                key={task.id}
                type="button"
                className="rounded-lg border bg-card p-3 text-left transition hover:border-primary hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => setSelectedTaskId(task.id)}
              >
                <span className="flex items-start justify-between gap-2">
                  <strong className="line-clamp-2 text-xs">{task.title}</strong>
                  <KlmPill tone={task.status === 'done' ? 'green' : task.status === 'blocked' ? 'red' : 'gold'}>
                    {task.status.replaceAll('_', ' ')}
                  </KlmPill>
                </span>
                <span className="mt-2 block text-2xs text-muted-foreground">
                  {task.dueDate ? `Prazo ${new Date(task.dueDate).toLocaleString('pt-BR')}` : 'Sem prazo geral definido'}
                </span>
              </button>
            ))}
          </div>
        ) : (
          <p className="mt-4 rounded-lg border border-dashed p-5 text-center text-xs text-muted-foreground">
            Nenhuma tarefa foi registrada neste caso.
          </p>
        )}
        {!canUseTasks && (
          <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">
            A consulta do histórico continua disponível, mas novas tarefas estão bloqueadas pela capacidade efetiva da conta.
          </p>
        )}
      </KlmPanel>

      <div className="grid gap-4 xl:grid-cols-[0.9fr_0.95fr_1.05fr]">
        <KlmPanel className="p-4">
          <KlmSectionTitle title="Identificação" description="Dados que alimentam CRM, RAG e petição" />
          <div className="mt-4 flex flex-wrap gap-2">
            <KlmPill>{matter.healthSubtheme?.replaceAll('_', ' ') ?? selectedCard?.subject.replaceAll('_', ' ') ?? 'Subtema não informado'}</KlmPill>
            <KlmPill>{matter.healthObjectName ?? selectedCard?.object ?? 'Objeto não informado'}</KlmPill>
            <KlmPill tone="gold">{matter.medicalUrgencyHours != null ? `${matter.medicalUrgencyHours}h` : 'Prazo médico não informado'}</KlmPill>
            <KlmPill tone={matter.leadScore >= 80 ? 'red' : 'gold'}>{matter.leadScore >= 80 ? 'Alta prioridade' : `Score ${matter.leadScore}`}</KlmPill>
          </div>
          <div className="mt-6">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-semibold">{matter.client.name}</h3>
              {(matter.client.sensitiveDataHidden || selectedCard?.sensitiveDataHidden) && (
                <KlmPill tone="gold"><ShieldOff className="mr-1 h-3 w-3" />Dados ocultos</KlmPill>
              )}
            </div>
            <p className="mt-1 text-2xs text-muted-foreground">{matter.healthInsurer ?? 'Operadora não informada'} · {matter.healthDenialReason ?? 'Motivo da negativa não registrado'}</p>
            <p className="mt-4 text-2xs">{matter.leadSource ?? selectedCard?.source ?? 'Origem não atribuída'} / {matter.demandType}</p>
            <p className="mt-3 text-2xs font-semibold text-[#a53e3c]">Valor econômico anual: {matter.economicValueAnnual != null ? money(matter.economicValueAnnual) : 'não informado'}</p>
          </div>
          <div className="mt-6 border-t border-border pt-4">
            <p className="text-2xs font-semibold">Resumo jurídico inicial</p>
            <p className="mt-3 text-2xs leading-4 text-muted-foreground">{matter.executiveSummary ?? 'Nenhum resumo jurídico foi registrado neste dossiê.'}</p>
            <div className="mt-4 flex flex-wrap gap-2"><KlmPill tone={matter.medicalUrgencyHours != null && matter.medicalUrgencyHours <= 24 ? 'red' : 'gold'}>{matter.medicalUrgencyHours == null ? 'Urgência não informada' : matter.medicalUrgencyHours <= 24 ? 'Urgência alta' : `Urgência: ${matter.medicalUrgencyHours}h`}</KlmPill><KlmPill tone="gold">Docs {matter.documents.length}</KlmPill><KlmPill tone="green">Score {matter.leadScore}</KlmPill></div>
          </div>
          <div className="mt-6 grid grid-cols-2 gap-3 border-t border-border pt-4 text-2xs">
            {[
              ['Contato', matter.client.phone ?? matter.client.email ?? 'não informado'], ['Canal', selectedThread?.primaryChannel?.replaceAll('_', ' ') ?? 'não atribuído'], ['Campanha', matter.campaignAttribution?.campaign ?? selectedCard?.source ?? 'não atribuída'], ['Etapa', selectedCard?.stage.replaceAll('_', ' ') ?? matter.columnId.replaceAll('_', ' ')],
            ].map(([label, value]) => <div key={label}><span className="block text-muted-foreground">{label}</span><span className="mt-1 block font-semibold">{value}</span></div>)}
          </div>
        </KlmPanel>

        <KlmPanel className="p-4">
          <KlmSectionTitle title="Documentos e RAG" description="Documentos e conversa canônica, sempre isolados neste caso" />
          <div className="mt-5 space-y-3.5">
            {documentRows.map((document) => (
              <div key={document.key} className="flex items-center justify-between gap-3 text-2xs"><span>{document.name}</span><button disabled={!document.canRequest || !canUseTasks || taskMutation.isPending} onClick={() => createMatterTask(`Solicitar ${document.name}`, `Coletar ${document.name} para o dossiê deste caso; a indexação no RAG dependerá do processamento persistido do arquivo.`, 'alta')}><KlmPill tone={document.tone}>{document.status}</KlmPill></button></div>
            ))}
          </div>
          <div className="mt-6 border-t border-border pt-4">
            <div className="flex items-center justify-between"><p className="text-2xs font-semibold">Fonte mais recente do RAG</p><KlmPill tone={matter.knowledgeChunks.length ? 'green' : undefined}>{matter.knowledgeChunks.length} trechos</KlmPill></div>
            <div className="mt-2 flex flex-wrap gap-1.5"><KlmPill tone={documentRagCount ? 'green' : undefined}>{documentRagCount} de documentos</KlmPill><KlmPill tone={conversationRagCount ? 'green' : undefined}>{conversationRagCount} da conversa</KlmPill></div>
            {matter.knowledgeChunks[0] ? (
              <div className="mt-3">
                <p className="text-2xs text-muted-foreground">{matter.knowledgeChunks[0].title ?? ragSourceLabel(matter.knowledgeChunks[0].sourceType)}</p>
                <p className="mt-2 text-2xs leading-4">{matter.knowledgeChunks[0].summary ?? matter.knowledgeChunks[0].contentText}</p>
              </div>
            ) : <p className="mt-3 text-2xs leading-4 text-muted-foreground">Nenhuma fonte foi indexada para este caso.</p>}
            <div className="mt-4 flex gap-2"><Button size="sm" className="h-8 bg-[#031121] text-2xs text-white hover:bg-[#031121]" onClick={() => changeRagOpen(true)}><FileSearch className="mr-1.5 h-3.5 w-3.5" />Abrir fontes</Button><Button size="sm" variant="outline" className="h-8 text-2xs" disabled={!selectedMatterId || !canUseTasks || taskMutation.isPending} onClick={() => createMatterTask('Preparar petição com as fontes atuais', `Handoff para o time de Petição com ${matter.documents.length} documento(s) e ${matter.knowledgeChunks.length} trecho(s) atualmente persistidos no caso.`, 'alta')}><FileText className="mr-1.5 h-3.5 w-3.5" />Petição</Button></div>
          </div>
        </KlmPanel>

        <KlmPanel tone="soft" className="p-4">
          <KlmSectionTitle title="Timeline e agentes" description="Histórico auditável das decisões" />
          <div className="mt-5 space-y-4">
            {timelineRows.map(({ key, time, action, agent, detail }, index) => (
              <div key={key} className="grid grid-cols-[42px_10px_1fr] gap-2 text-2xs">
                <span className="text-muted-foreground">{time}</span>
                <span className="relative mt-1 h-2 w-2 rounded-full bg-primary after:absolute after:left-[3px] after:top-2 after:h-8 after:w-px after:bg-border last:after:hidden" />
                <span><span className="block text-2xs font-medium">{action}</span>{detail ? <span className="mt-1 block leading-4 text-muted-foreground">{detail}</span> : null}<span className={index === 1 ? 'text-[#0057d8]' : 'text-muted-foreground'}>{agent}</span></span>
              </div>
            ))}
            {!timelineRows.length && selectedThread && messagesQuery.isPending ? <p className="text-2xs text-muted-foreground">Carregando eventos persistidos...</p> : null}
            {!timelineRows.length && selectedThread && messagesQuery.isError ? <p className="text-2xs text-destructive">Não foi possível carregar as mensagens desta timeline.</p> : null}
            {!timelineRows.length && (!selectedThread || !messagesQuery.isPending) && !messagesQuery.isError ? <p className="text-2xs text-muted-foreground">Ainda não há eventos persistidos neste caso.</p> : null}
          </div>
          <div className="mt-6 border-t border-border pt-4">
            <p className="text-2xs font-semibold">Próxima melhor ação</p>
            <p className="mt-3 text-2xs leading-4 text-muted-foreground">{matter.nextAction ?? 'Nenhuma próxima ação foi registrada no dossiê.'}</p>
            <div className="mt-4 flex flex-wrap gap-2"><Button size="sm" className="h-8 bg-[#031121] text-2xs text-white hover:bg-[#031121]" disabled={!selectedMatterId || closerMutation.isPending} onClick={() => closerMutation.mutate()}><Sparkles className="mr-1.5 h-3.5 w-3.5" />Mover para Closer</Button><Button size="sm" variant="outline" className="h-8 text-2xs" disabled={!selectedMatterId || !canUseTasks || taskMutation.isPending} onClick={() => createMatterTask('Confirmar reunião online', 'Secretaria deve confirmar disponibilidade, link da reunião e lembrete com o lead.', 'alta')}><Check className="mr-1.5 h-3.5 w-3.5" />{taskMutation.isPending ? 'Criando' : 'Criar tarefa'}</Button></div>
          </div>
        </KlmPanel>
      </div>

      {selectedMatterId ? <TreatmentCostPanel matterId={selectedMatterId} initialProduct={matter.healthObjectName ?? selectedCard?.object} /> : null}

      <KlmPanel className="p-4">
        <div className="grid gap-5 lg:grid-cols-[1.1fr_0.9fr]">
          <div>
            <p className="text-2xs font-semibold uppercase text-[#0057d8]">Projeção interna de honorários</p>
            <h3 className="mt-2 text-base font-medium">Expectativa econômica do caso</h3>
            <p className="mt-1 max-w-2xl text-2xs leading-4 text-muted-foreground">A visão principal mostra somente condições e projeções persistidas. Novas premissas ficam identificadas como rascunho até o salvamento.</p>
            <div className="mt-4 flex flex-wrap gap-2"><KlmPill>Objeto: {matter.healthObjectName ?? selectedCard?.object ?? 'não informado'}</KlmPill><KlmPill>Status: {matter.honorariumEstimate ? 'condições persistidas' : 'condições não definidas'}</KlmPill><KlmPill>{pricingPolicyQuery.data ? `Entrada mínima ${formatBRL(pricingPolicyQuery.data.minimumInitialFee)}` : pricingPolicyQuery.isPending ? 'Carregando política de preços' : 'Política de preços indisponível'}</KlmPill></div>
            <div className="mt-4 flex flex-wrap gap-2"><Button size="sm" className="h-8 bg-[#031121] text-2xs text-white hover:bg-[#031121]" onClick={() => setSimulationOpen(true)}><CircleDollarSign className="mr-1.5 h-3.5 w-3.5" />Abrir simulação</Button><Button size="sm" variant="outline" className="h-8 text-2xs" onClick={() => setSimulationOpen(true)}><PencilLine className="mr-1.5 h-3.5 w-3.5" />Ajustar premissas</Button></div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <KlmMetricCard label="Honorários iniciais" value={matter.honorariumEstimate?.fixedFee != null ? formatBRL(matter.honorariumEstimate.fixedFee) : 'Não definido'} delta="valor persistido" />
            <KlmMetricCard label="Final / êxito" value={matter.honorariumEstimate?.quotaLitisPercentage != null ? `${matter.honorariumEstimate.quotaLitisPercentage}%` : 'Não definido'} delta="percentual persistido" />
            <KlmMetricCard label="Receita projetada" value={matter.revenueProjection?.projectedRevenue != null ? money(matter.revenueProjection.projectedRevenue) : 'Não calculada'} delta="projeção persistida" />
            <KlmMetricCard label="Probabilidade comercial" value={matter.revenueProjection?.successProbability != null ? `${Math.round(matter.revenueProjection.successProbability * 100)}%` : 'Não informada'} delta="premissa persistida" />
          </div>
        </div>
      </KlmPanel>

      <Dialog open={ragOpen} onOpenChange={changeRagOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>Fontes locais do RAG</DialogTitle><DialogDescription>Documentos e conversa canônica persistidos com proveniência e isolamento por caso.</DialogDescription></DialogHeader>
          <div className="space-y-3">
            <div role="tablist" aria-label="Filtrar fontes do RAG" className="flex flex-wrap items-center gap-1 rounded-md border bg-secondary p-1">
              {[
                ['case_sources', 'Caso', conversationRagCount + documentRagCount],
                ['conversation', 'Conversa', conversationRagCount],
                ['documents', 'Documentos', documentRagCount],
                ['internal', 'Notas internas', internalRagCount],
                ['all', 'Todas', matter.knowledgeChunks.length],
              ].map(([scope, label, count]) => (
                <button
                  key={scope as string}
                  type="button"
                  role="tab"
                  aria-selected={ragScope === scope}
                  className={`h-8 rounded px-2.5 text-2xs font-semibold transition ${ragScope === scope ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
                  onClick={() => setRagScope(scope as RagScope)}
                >
                  {label as string} · {count as number}
                </button>
              ))}
            </div>
            <Input aria-label="Pesquisar nas fontes do RAG" value={ragSearch} onChange={(event) => setRagSearch(event.target.value)} placeholder="Pesquisar em títulos, resumos e conteúdo..." className="h-9 text-2xs" />
            <div className="max-h-[56vh] space-y-3 overflow-y-auto pr-2 scrollbar-thin">
              {filteredRagChunks.map((chunk) => <div key={chunk.id} className={`border-l-2 pl-3 ${chunk.sourceType === 'internal_conversation' ? 'border-amber-500' : 'border-primary'}`}><div className="flex items-start justify-between gap-3"><p className="text-2xs font-semibold">{chunk.title ?? ragSourceLabel(chunk.sourceType)}</p><KlmPill tone={chunk.sourceType === 'internal_conversation' ? 'gold' : undefined}>{ragSourceLabel(chunk.sourceType)}</KlmPill></div><p className="mt-1 text-2xs leading-4 text-muted-foreground">{chunk.summary ?? chunk.contentText}</p></div>)}
              {!filteredRagChunks.length ? <p className="py-8 text-center text-2xs leading-4 text-muted-foreground">Nenhuma fonte corresponde a este filtro.</p> : null}
            </div>
          </div>
          <DialogFooter><Button onClick={() => changeRagOpen(false)}>Fechar</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={simulationOpen} onOpenChange={setSimulationOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>Simulação econômica do caso</DialogTitle><DialogDescription>Rascunho interno iniciado a partir das condições persistidas. Campos vazios não recebem valores padrão.</DialogDescription></DialogHeader>
          {premises ? (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="sm:col-span-2"><Label className="text-2xs">Forma de contratação</Label><Select value={premises.paymentMode} onValueChange={(value: ProjectionPremises['paymentMode']) => setPremises((current) => current ? ({ ...current, paymentMode: value, initialFee: value === 'success_only' ? 0 : current.initialFee, successBaseType: value === 'success_only' ? 'moral_damages' : current.successBaseType, successPercent: value === 'success_only' && pricingPolicyQuery.data ? pricingPolicyQuery.data.successOnlyMoralDamagesPercentage : current.successPercent }) : current)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="initial_plus_success">Entrada + êxito</SelectItem><SelectItem value="success_only" disabled={pricingPolicyQuery.data?.allowSuccessOnly === false}>Integralmente no êxito</SelectItem></SelectContent></Select></div>
                <div><Label className="text-2xs">Honorários iniciais</Label><Input type="number" min={0} value={premises.initialFee ?? ''} onChange={(event) => setPremises((current) => current ? ({ ...current, initialFee: nullableNumber(event.target.value) }) : current)} /></div>
                <div><Label className="text-2xs">Êxito (%)</Label><Input type="number" min={0} max={100} value={premises.successPercent ?? ''} onChange={(event) => setPremises((current) => current ? ({ ...current, successPercent: nullableNumber(event.target.value) }) : current)} /></div>
                <div><Label className="text-2xs">Base do êxito</Label><Select value={premises.successBaseType} onValueChange={(value: ProjectionPremises['successBaseType']) => setPremises((current) => current ? ({ ...current, successBaseType: value }) : current)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="economic_obligation">Obrigação de fazer</SelectItem><SelectItem value="moral_damages">Dano moral</SelectItem></SelectContent></Select></div>
                {premises.successBaseType === 'moral_damages' ? <div><Label className="text-2xs">Estimativa do dano moral</Label><Input type="number" min={0} value={premises.moralDamagesEstimate ?? ''} onChange={(event) => setPremises((current) => current ? ({ ...current, moralDamagesEstimate: nullableNumber(event.target.value) }) : current)} /></div> : <div><Label className="text-2xs">Honorários na obrigação</Label><Input type="number" min={0} value={premises.obligationFee ?? ''} onChange={(event) => setPremises((current) => current ? ({ ...current, obligationFee: nullableNumber(event.target.value) }) : current)} /></div>}
                <div><Label className="text-2xs">Honorários na liminar</Label><Input type="number" min={0} value={premises.injunctionMilestoneFee ?? ''} disabled={!pricingPolicyQuery.data?.allowInjunctionMilestoneFee} onChange={(event) => setPremises((current) => current ? ({ ...current, injunctionMilestoneFee: nullableNumber(event.target.value) }) : current)} /></div>
                <div><Label className="text-2xs">Custo anual estimado do objeto</Label><Input type="number" min={0} value={premises.objectAnnualCost ?? ''} onChange={(event) => setPremises((current) => current ? ({ ...current, objectAnnualCost: nullableNumber(event.target.value) }) : current)} /></div>
                <div><Label className="text-2xs">Probabilidade comercial (%)</Label><Input type="number" min={0} max={100} value={premises.probability ?? ''} onChange={(event) => setPremises((current) => current ? ({ ...current, probability: nullableNumber(event.target.value) }) : current)} /></div>
                <div className="flex items-center justify-between rounded-lg border border-border p-3 sm:col-span-2"><div><p className="text-2xs font-semibold">Usar obrigação de fazer como base econômica</p><p className="mt-1 text-2xs text-muted-foreground">Premissa interna sujeita a fonte e revisão humana.</p></div><Switch aria-label="Usar obrigação de fazer como base econômica" checked={premises.economicObligation} onCheckedChange={(checked) => setPremises((current) => current ? ({ ...current, economicObligation: checked }) : current)} /></div>
                <div className="sm:col-span-2"><Label className="text-2xs">Status da revisão</Label><Select value={premises.reviewStatus} onValueChange={(value) => setPremises((current) => current ? ({ ...current, reviewStatus: value }) : current)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="rascunho">Rascunho</SelectItem><SelectItem value="revisado">Revisado</SelectItem><SelectItem value="aprovado">Aprovado</SelectItem><SelectItem value="bloqueado">Bloqueado</SelectItem></SelectContent></Select></div>
              </div>
              {projections ? (
                <div className="grid grid-cols-2 gap-3 rounded-lg bg-secondary p-3 text-center sm:grid-cols-4">
                  {[
                    ['Conservador · 12% da base', projections.conservative], ['Provável · percentual informado', projections.likely], ['Agressivo · 30% da base', projections.aggressive], ['Somente componentes fixos', projections.fixedOnly],
                  ].map(([label, value]) => <div key={label as string}><p className="text-2xs text-muted-foreground">{label}</p><p className="mt-1 text-sm font-semibold">{money(value as number)}</p></div>)}
                </div>
              ) : <p className="rounded-lg bg-secondary p-3 text-2xs text-muted-foreground">{projectionValidationMessage}</p>}
              <DialogFooter><Button variant="outline" onClick={() => setSimulationOpen(false)}>Cancelar</Button><Button disabled={projectionMutation.isPending || !selectedMatterId || Boolean(projectionValidationMessage)} className="bg-[#031121] text-white hover:bg-[#031121]" onClick={() => projectionMutation.mutate()}><History className="mr-2 h-4 w-4" />{projectionMutation.isPending ? 'Versionando' : 'Salvar versão local'}</Button></DialogFooter>
            </>
          ) : <p className="text-sm text-muted-foreground">Carregando condições persistidas...</p>}
        </DialogContent>
      </Dialog>

      <BookingLinkDialog
        open={bookingDialogOpen}
        onOpenChange={setBookingDialogOpen}
        lockedMatterId={matter.id}
        lockedMatterLabel={`${matter.client.name} · ${matter.demandType}`}
        initialTitle={`Reunião inicial sobre ${matter.healthObjectName ?? matter.demandType}`}
        onCreated={(_, absoluteUrl) => setNotice(`Página de agendamento criada para este card: ${absoluteUrl}`)}
      />
      <TaskCollaborationDrawer
        taskId={selectedTaskId}
        contextLabels={{
          clientLabel: matter.client.name,
          matterLabel: matter.demandType,
        }}
        open={Boolean(selectedTaskId)}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) setSelectedTaskId(null);
        }}
      />
    </div>
  );
}
