import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Layers, Loader2, Plus, ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { LegalArtifactsTab } from '@/klm/components/legal/LegalArtifactsTab';
import { LegalDocumentsTab } from '@/klm/components/legal/CaseDocumentsTab';
import { LegalProtocolTab } from '@/klm/components/legal/ProtocolFolderTab';
import { LegalBatchPanel } from '@/klm/components/legal/LegalBatchPanel';
import { useCreateLegalBatchFromContext } from '@/klm/api/useLegalBatches';
import {
  isLegalDemandAccessDeniedError,
  useAddCaseFact,
  useAddCaseParty,
  useAddCasePleading,
  useAddCaseProofLink,
  useCase360,
  useCaseParties,
  useCaseStrategyMatrix,
  useLegalDemand,
} from '@/klm/api/useLegalDemands';
import type { KlmCase360, KlmCaseFact, KlmCasePartyPole, KlmProofForce } from '@/klm/api';
import {
  LEGAL_DEMAND_NATURE_LABELS,
  LEGAL_DEMAND_PHASE_LABELS,
  LEGAL_DEMAND_PHASE_TONE,
  LEGAL_DEMAND_RISK_LABELS,
  LEGAL_DEMAND_RISK_TONE,
} from '@/klm/lib/legal-demand-labels';
import { useAppStore } from '@/store/app-context';
import { ApiError } from '@/services/http';

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail as { message?: string } | undefined;
    return detail?.message ?? error.message;
  }
  return error instanceof Error ? error.message : 'Tente novamente.';
}

function formatCurrency(value: number | null | undefined): string {
  if (value == null) return '—';
  return value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  return new Date(value).toLocaleDateString('pt-BR');
}

const CASE_PARTY_POLE_LABELS: Record<KlmCasePartyPole, string> = {
  ativo: 'Polo ativo',
  passivo: 'Polo passivo',
  terceiro_interessado: 'Terceiro interessado',
};

const PROOF_FORCE_LABELS: Record<KlmProofForce, string> = {
  plena: 'Plena',
  robusta: 'Robusta',
  mediana: 'Mediana',
  indiciaria: 'Indiciária',
  fragil: 'Frágil',
};

/**
 * Detalhe da demanda — container de abas (Onda 5, Sprint A UI). Consome o
 * Caso 360 (`GET /legal/demands/{id}/case-360`), as partes e a matriz de
 * estratégia via `klmApi`/hooks reais. As abas "Peças", "Documentos" e
 * "Protocolo" são placeholders nomeados e importáveis
 * (`LegalDemandPlaceholderTabs`) que a fase seguinte preenche.
 */
export default function DemandaDetailPage() {
  const { demandId } = useParams<{ demandId: string }>();
  const navigate = useNavigate();
  const { toast } = useToast();
  const { can } = useAppStore();
  const canManage = can('legal', 'manage');

  const demandQuery = useLegalDemand(demandId);
  const accessDenied = isLegalDemandAccessDeniedError(demandQuery.error);

  const [batchDialogOpen, setBatchDialogOpen] = useState(false);
  const [batchId, setBatchId] = useState<string | null>(null);
  const createBatch = useCreateLegalBatchFromContext();

  const handleProcessBatch = () => {
    if (!demandId || !demandQuery.data) return;
    createBatch.mutate(
      {
        name: `Lote — ${LEGAL_DEMAND_NATURE_LABELS[demandQuery.data.nature]}${demandQuery.data.area ? ` · ${demandQuery.data.area}` : ''}`,
        externalRef: `legal-batch:demanda:${demandId}`,
        legalDemandId: demandId,
      },
      {
        onSuccess: (batch) => { setBatchId(batch.id); setBatchDialogOpen(true); },
        onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível abrir o lote desta demanda', description: errorMessage(error) }),
      },
    );
  };

  return (
    <div className="space-y-4 p-4">
      <Button variant="ghost" size="sm" className="-ml-2 w-fit" onClick={() => navigate('/app/demandas')}>
        <ArrowLeft className="mr-1.5 h-3.5 w-3.5" />Demandas
      </Button>

      {demandQuery.isPending && (
        <div className="grid gap-3">
          <Skeleton className="h-10 w-2/3 rounded-lg" />
          <Skeleton className="h-40 rounded-lg" />
        </div>
      )}

      {!demandQuery.isPending && accessDenied && (
        <KlmPanel>
          <div className="flex items-start gap-3 p-4">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-secondary">
              <ShieldAlert className="h-4 w-4 text-muted-foreground" />
            </span>
            <div>
              <p className="text-sm font-semibold">Acesso ao módulo jurídico restrito</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                Seu papel atual não tem a permissão jurídica (módulo <code>legal</code>) necessária para ver esta demanda.
              </p>
            </div>
          </div>
        </KlmPanel>
      )}

      {!demandQuery.isPending && !accessDenied && demandQuery.isError && (
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          Não foi possível carregar esta demanda agora. Tente novamente em instantes.
        </p>
      )}

      {!demandQuery.isPending && !accessDenied && demandQuery.isSuccess && demandId && (
        <>
          <KlmSectionTitle
            title={`${LEGAL_DEMAND_NATURE_LABELS[demandQuery.data.nature]}${demandQuery.data.area ? ` · ${demandQuery.data.area}` : ''}`}
            description={demandQuery.data.subarea ?? undefined}
            action={
              <div className="flex items-center gap-2">
                <KlmPill tone={LEGAL_DEMAND_PHASE_TONE[demandQuery.data.phase]}>{LEGAL_DEMAND_PHASE_LABELS[demandQuery.data.phase]}</KlmPill>
                <KlmPill tone={LEGAL_DEMAND_RISK_TONE[demandQuery.data.risk]}>Risco {LEGAL_DEMAND_RISK_LABELS[demandQuery.data.risk]}</KlmPill>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!canManage || createBatch.isPending}
                  title={!canManage ? 'Permissão legal.manage necessária' : undefined}
                  onClick={handleProcessBatch}
                >
                  {createBatch.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Layers className="mr-1.5 h-3.5 w-3.5" />}
                  Processar em lote
                </Button>
              </div>
            }
          />

          <Dialog open={batchDialogOpen} onOpenChange={setBatchDialogOpen}>
            <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
              <DialogHeader>
                <DialogTitle>Lote desta demanda</DialogTitle>
                <DialogDescription>Recorte por contexto — só os itens desta demanda, nunca do cliente inteiro.</DialogDescription>
              </DialogHeader>
              {batchId && <LegalBatchPanel batchId={batchId} canManage={canManage} />}
            </DialogContent>
          </Dialog>

          <Tabs defaultValue="visao360" className="space-y-4">
            <TabsList className="h-9 flex-wrap bg-secondary p-1">
              <TabsTrigger value="visao360" className="text-xs">Visão 360</TabsTrigger>
              <TabsTrigger value="partes" className="text-xs">Partes</TabsTrigger>
              <TabsTrigger value="estrategia" className="text-xs">Estratégia</TabsTrigger>
              <TabsTrigger value="pecas" className="text-xs">Peças</TabsTrigger>
              <TabsTrigger value="documentos" className="text-xs">Documentos</TabsTrigger>
              <TabsTrigger value="protocolo" className="text-xs">Protocolo</TabsTrigger>
            </TabsList>

            <TabsContent value="visao360"><Case360Tab demandId={demandId} /></TabsContent>
            <TabsContent value="partes"><PartesTab demandId={demandId} canManage={canManage} /></TabsContent>
            <TabsContent value="estrategia"><EstrategiaTab demandId={demandId} canManage={canManage} /></TabsContent>
            <TabsContent value="pecas"><LegalArtifactsTab demandId={demandId} /></TabsContent>
            <TabsContent value="documentos"><LegalDocumentsTab demandId={demandId} /></TabsContent>
            <TabsContent value="protocolo"><LegalProtocolTab demandId={demandId} /></TabsContent>
          </Tabs>
        </>
      )}
    </div>
  );
}

// ── Aba Visão 360 ──────────────────────────────────────────────────────

function Case360Tab({ demandId }: { demandId: string }) {
  const case360Query = useCase360(demandId);

  if (case360Query.isPending) {
    return <div className="grid gap-3">{Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-24 rounded-lg" />)}</div>;
  }
  if (isLegalDemandAccessDeniedError(case360Query.error)) return null; // já mostrado no cabeçalho da página
  if (case360Query.isError || !case360Query.data) {
    return (
      <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
        Não foi possível montar o Caso 360 agora. Tente novamente em instantes.
      </p>
    );
  }

  const data: KlmCase360 = case360Query.data;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <KlmPanel className="p-4">
        <KlmSectionTitle title="Demanda contratada × oportunidade" description="Nunca confundidas — a contratada é a camada jurídica; a oportunidade é o card do funil comercial, quando existir." />
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div className="rounded-lg border border-border bg-secondary/40 p-3">
            <p className="text-2xs font-semibold uppercase text-muted-foreground">Demanda contratada</p>
            <p className="mt-1 text-sm font-medium">{LEGAL_DEMAND_NATURE_LABELS[data.demandaContratada.nature]}</p>
            <p className="text-xs text-muted-foreground">{data.demandaContratada.area ?? 'Área não informada'}</p>
            <p className="mt-1 text-xs tabular-nums text-muted-foreground">Valor: {formatCurrency(data.demandaContratada.value)}</p>
          </div>
          <div className="rounded-lg border border-border bg-secondary/40 p-3">
            <p className="text-2xs font-semibold uppercase text-muted-foreground">Oportunidade (funil)</p>
            {data.oportunidade ? (
              <>
                <p className="mt-1 text-sm font-medium">{data.oportunidade.caseNumber ?? data.oportunidade.matterId.slice(0, 8)}</p>
                <p className="text-xs text-muted-foreground">{data.oportunidade.microStatus} · {data.oportunidade.classification}</p>
                <p className="mt-1 text-xs tabular-nums text-muted-foreground">Estimativa: {formatCurrency(data.oportunidade.claimValue)}</p>
              </>
            ) : (
              <p className="mt-1 text-xs text-muted-foreground">Sem oportunidade de funil vinculada.</p>
            )}
          </div>
        </div>
      </KlmPanel>

      <KlmPanel className="p-4">
        <KlmSectionTitle title="Cliente e responsável" />
        <div className="mt-3 space-y-1 text-xs">
          <p className="text-sm font-medium">{data.cliente.maskedLabel ?? data.cliente.name}</p>
          {!data.cliente.sensitiveDataHidden && (
            <p className="text-muted-foreground">{data.cliente.email ?? '—'} · {data.cliente.phone ?? '—'}</p>
          )}
          <p className="mt-2 text-muted-foreground">
            Responsável: {data.responsavel ? `${data.responsavel.name}` : 'Ainda não distribuída'}
          </p>
        </div>
      </KlmPanel>

      <KlmPanel className="p-4">
        <KlmSectionTitle title="Partes do caso" description={`${data.partes.length} parte(s) cadastrada(s)`} />
        <div className="mt-3 space-y-2">
          {data.partes.length === 0 && <p className="text-xs text-muted-foreground">Nenhuma parte cadastrada — use a aba "Partes".</p>}
          {data.partes.map((party) => (
            <div key={party.id} className="flex items-center justify-between rounded-lg border border-border bg-secondary/40 px-3 py-2 text-xs">
              <span className="font-medium">{party.name}</span>
              <KlmPill tone="muted">{CASE_PARTY_POLE_LABELS[party.pole]}</KlmPill>
            </div>
          ))}
        </div>
      </KlmPanel>

      <KlmPanel className="p-4">
        <KlmSectionTitle title="Processo(s) e prazos" />
        <div className="mt-3 space-y-2">
          {data.processos.length === 0 && <p className="text-xs text-muted-foreground">Nenhum processo monitorado vinculado.</p>}
          {data.processos.map((processo) => (
            <div key={processo.id} className="rounded-lg border border-border bg-secondary/40 px-3 py-2 text-xs">
              <p className="font-medium tabular-nums">{processo.numeroCnj}</p>
              <p className="text-muted-foreground">{processo.tribunal ?? '—'} · {processo.status} · {processo.publicacoes.length} publicação(ões)</p>
            </div>
          ))}
          <div className="grid grid-cols-2 gap-2 pt-1 text-2xs text-muted-foreground">
            <span>Prazo fatal: <span className="tabular-nums text-foreground">{formatDate(data.prazos.prazoFatalAt)}</span></span>
            <span>Prazo legal: <span className="tabular-nums text-foreground">{formatDate(data.prazos.prazoLegalAt)}</span></span>
            <span>SLA efetivo: <span className="tabular-nums text-foreground">{formatDate(data.prazos.slaEffectiveAt)}</span></span>
            <span>Margem: <span className="tabular-nums text-foreground">{data.prazos.margemSegurancaDias ?? '—'} dia(s)</span></span>
          </div>
        </div>
      </KlmPanel>

      <KlmPanel className="p-4">
        <KlmSectionTitle title="Financeiro (subledger)" description="Sempre filtrado pelo caso — nunca o financeiro do workspace inteiro." />
        <div className="mt-3 grid grid-cols-2 gap-2 text-2xs">
          <span>A receber: <span className="tabular-nums font-medium text-foreground">{formatCurrency(data.financeiro.totals.totalReceivable)}</span></span>
          <span>Pago: <span className="tabular-nums font-medium text-foreground">{formatCurrency(data.financeiro.totals.totalPaid)}</span></span>
          <span>Pendente: <span className="tabular-nums font-medium text-foreground">{formatCurrency(data.financeiro.totals.totalPending)}</span></span>
          <span>Vencido: <span className="tabular-nums font-medium text-foreground">{formatCurrency(data.financeiro.totals.totalOverdue)}</span></span>
        </div>
        {data.financeiro.entries.length === 0 && <p className="mt-2 text-xs text-muted-foreground">Nenhum lançamento financeiro vinculado.</p>}
      </KlmPanel>

      <KlmPanel className="p-4">
        <KlmSectionTitle title="Comunicações e exceções abertas" />
        <p className="mt-2 text-xs text-muted-foreground">{data.comunicacoes.totalCount} mensagem(ns) no total.</p>
        <div className="mt-2 space-y-1.5">
          {data.excecoesAbertas.length === 0 && <p className="text-xs text-muted-foreground">Nenhuma exceção aberta.</p>}
          {data.excecoesAbertas.map((exception) => (
            <div key={exception.id} className="flex items-center justify-between rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs">
              <span>{exception.title}</span>
              <KlmPill tone="red">{exception.priority}</KlmPill>
            </div>
          ))}
        </div>
      </KlmPanel>
    </div>
  );
}

// ── Aba Partes ─────────────────────────────────────────────────────────

function PartesTab({ demandId, canManage }: { demandId: string; canManage: boolean }) {
  const { toast } = useToast();
  const partiesQuery = useCaseParties(demandId);
  const addParty = useAddCaseParty(demandId);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [pole, setPole] = useState<KlmCasePartyPole>('passivo');
  const [role, setRole] = useState('');

  const reset = () => { setName(''); setPole('passivo'); setRole(''); };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    addParty.mutate(
      { name: name.trim(), pole, role: role.trim() || null },
      {
        onSuccess: () => { toast({ title: 'Parte adicionada' }); setOpen(false); reset(); },
        onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível adicionar a parte', description: errorMessage(error) }),
      },
    );
  };

  if (isLegalDemandAccessDeniedError(partiesQuery.error)) return null;

  return (
    <KlmPanel className="p-4">
      <KlmSectionTitle
        title="Partes do caso"
        description="Polo processual, papel e qualificação — além do cliente titular."
        action={
          <Button size="sm" disabled={!canManage} onClick={() => setOpen(true)} title={!canManage ? 'Permissão legal.manage necessária' : undefined}>
            <Plus className="mr-1.5 h-4 w-4" />Adicionar parte
          </Button>
        }
      />

      {partiesQuery.isPending && <div className="mt-3 grid gap-2">{Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-10 rounded-lg" />)}</div>}

      {partiesQuery.isSuccess && (
        <div className="mt-3 space-y-2">
          {partiesQuery.data.items.length === 0 && <p className="text-xs text-muted-foreground">Nenhuma parte cadastrada ainda.</p>}
          {partiesQuery.data.items.map((party) => (
            <div key={party.id} className="flex items-center justify-between rounded-lg border border-border bg-secondary/40 px-3 py-2 text-xs">
              <div>
                <p className="font-medium">{party.name}</p>
                {party.role && <p className="text-muted-foreground">{party.role}</p>}
              </div>
              <KlmPill tone="muted">{CASE_PARTY_POLE_LABELS[party.pole]}</KlmPill>
            </div>
          ))}
        </div>
      )}

      <Dialog open={open} onOpenChange={(next) => { if (!addParty.isPending) { setOpen(next); if (!next) reset(); } }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Adicionar parte</DialogTitle>
            <DialogDescription>Parte do caso além do cliente titular.</DialogDescription>
          </DialogHeader>
          <form className="space-y-3" onSubmit={handleSubmit}>
            <div className="space-y-1">
              <Label htmlFor="parte-nome">Nome</Label>
              <Input id="parte-nome" value={name} onChange={(event) => setName(event.target.value)} required maxLength={240} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="parte-polo">Polo</Label>
              <Select value={pole} onValueChange={(value) => setPole(value as KlmCasePartyPole)}>
                <SelectTrigger id="parte-polo"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(Object.keys(CASE_PARTY_POLE_LABELS) as KlmCasePartyPole[]).map((value) => (
                    <SelectItem key={value} value={value}>{CASE_PARTY_POLE_LABELS[value]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="parte-papel">Papel (opcional)</Label>
              <Input id="parte-papel" value={role} onChange={(event) => setRole(event.target.value)} placeholder="Ex.: assistente técnico" maxLength={120} />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>Cancelar</Button>
              <Button type="submit" disabled={addParty.isPending}>
                {addParty.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Adicionar
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </KlmPanel>
  );
}

// ── Aba Estratégia (matriz fato-prova-pedido) ─────────────────────────

function EstrategiaTab({ demandId, canManage }: { demandId: string; canManage: boolean }) {
  const { toast } = useToast();
  const matrixQuery = useCaseStrategyMatrix(demandId);
  const addFact = useAddCaseFact(demandId);
  const addProofLink = useAddCaseProofLink(demandId);
  const addPleading = useAddCasePleading(demandId);

  const [factOpen, setFactOpen] = useState(false);
  const [factDescription, setFactDescription] = useState('');
  const [factControverted, setFactControverted] = useState(false);

  const [proofOpen, setProofOpen] = useState(false);
  const [proofFactId, setProofFactId] = useState<string>('');
  const [proofForce, setProofForce] = useState<KlmProofForce>('mediana');

  const [pleadingOpen, setPleadingOpen] = useState(false);
  const [pleadingText, setPleadingText] = useState('');
  const [pleadingBasis, setPleadingBasis] = useState('');
  const [pleadingFactIds, setPleadingFactIds] = useState<string[]>([]);

  const facts = matrixQuery.data?.facts ?? [];
  const factById = useMemo(() => new Map<string, KlmCaseFact>(facts.map((fact) => [fact.id, fact])), [facts]);

  if (isLegalDemandAccessDeniedError(matrixQuery.error)) return null;

  const handleAddFact = (event: React.FormEvent) => {
    event.preventDefault();
    if (!factDescription.trim()) return;
    addFact.mutate(
      { description: factDescription.trim(), isControverted: factControverted },
      {
        onSuccess: () => { toast({ title: 'Fato adicionado' }); setFactOpen(false); setFactDescription(''); setFactControverted(false); },
        onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível adicionar o fato', description: errorMessage(error) }),
      },
    );
  };

  const handleAddProof = (event: React.FormEvent) => {
    event.preventDefault();
    if (!proofFactId) return;
    addProofLink.mutate(
      { factId: proofFactId, payload: { force: proofForce } },
      {
        onSuccess: () => { toast({ title: 'Vínculo de prova registrado' }); setProofOpen(false); setProofFactId(''); },
        onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível vincular a prova', description: errorMessage(error) }),
      },
    );
  };

  const handleAddPleading = (event: React.FormEvent) => {
    event.preventDefault();
    if (!pleadingText.trim()) return;
    addPleading.mutate(
      { requestText: pleadingText.trim(), legalBasis: pleadingBasis.trim() || null, supportingFactIds: pleadingFactIds },
      {
        onSuccess: () => { toast({ title: 'Pedido adicionado' }); setPleadingOpen(false); setPleadingText(''); setPleadingBasis(''); setPleadingFactIds([]); },
        onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível adicionar o pedido', description: errorMessage(error) }),
      },
    );
  };

  return (
    <div className="space-y-4">
      {matrixQuery.data && matrixQuery.data.gaps.length > 0 && (
        <KlmPanel className="border-destructive/30 bg-destructive/5 p-4">
          <KlmSectionTitle title={`${matrixQuery.data.gaps.length} lacuna(s) na estratégia`} description="Fato controvertido sem prova, ou pedido sem fato de sustentação — nunca fabricamos a prova ou o fato que falta." />
          <div className="mt-2 space-y-1.5">
            {matrixQuery.data.gaps.map((gap, index) => (
              <div key={`${gap.kind}-${index}`} className="rounded-lg border border-destructive/30 bg-card px-3 py-2 text-xs">
                <KlmPill tone="red">{gap.kind === 'fato_sem_prova' ? 'Fato sem prova' : 'Pedido sem fato'}</KlmPill>
                <p className="mt-1 text-muted-foreground">{gap.description}</p>
              </div>
            ))}
          </div>
        </KlmPanel>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <KlmPanel className="p-4">
          <KlmSectionTitle
            title="Fatos"
            action={<Button size="sm" variant="outline" disabled={!canManage} onClick={() => setFactOpen(true)}><Plus className="h-3.5 w-3.5" /></Button>}
          />
          {matrixQuery.isPending && <Skeleton className="mt-3 h-20 rounded-lg" />}
          <div className="mt-3 space-y-2">
            {matrixQuery.isSuccess && facts.length === 0 && <p className="text-xs text-muted-foreground">Nenhum fato registrado.</p>}
            {facts.map((fact) => (
              <div key={fact.id} className="rounded-lg border border-border bg-secondary/40 px-3 py-2 text-xs">
                <p>{fact.description}</p>
                {fact.isControverted && <KlmPill tone="gold" className="mt-1">Controvertido</KlmPill>}
              </div>
            ))}
          </div>
        </KlmPanel>

        <KlmPanel className="p-4">
          <KlmSectionTitle
            title="Provas"
            action={<Button size="sm" variant="outline" disabled={!canManage || facts.length === 0} onClick={() => setProofOpen(true)}><Plus className="h-3.5 w-3.5" /></Button>}
          />
          <div className="mt-3 space-y-2">
            {matrixQuery.isSuccess && (matrixQuery.data?.proofLinks.length ?? 0) === 0 && <p className="text-xs text-muted-foreground">Nenhuma prova vinculada.</p>}
            {matrixQuery.data?.proofLinks.map((proof) => (
              <div key={proof.id} className="rounded-lg border border-border bg-secondary/40 px-3 py-2 text-xs">
                <p className="text-muted-foreground">{factById.get(proof.factId)?.description ?? proof.factId}</p>
                <div className="mt-1 flex items-center gap-1.5">
                  <KlmPill tone={proof.status === 'tem' ? 'green' : proof.status === 'solicitada' ? 'gold' : 'red'}>{proof.status}</KlmPill>
                  <KlmPill tone="muted">{PROOF_FORCE_LABELS[proof.force]}</KlmPill>
                </div>
              </div>
            ))}
          </div>
        </KlmPanel>

        <KlmPanel className="p-4">
          <KlmSectionTitle
            title="Pedidos"
            action={<Button size="sm" variant="outline" disabled={!canManage} onClick={() => setPleadingOpen(true)}><Plus className="h-3.5 w-3.5" /></Button>}
          />
          <div className="mt-3 space-y-2">
            {matrixQuery.isSuccess && (matrixQuery.data?.pleadings.length ?? 0) === 0 && <p className="text-xs text-muted-foreground">Nenhum pedido registrado.</p>}
            {matrixQuery.data?.pleadings.map((pleading) => (
              <div key={pleading.id} className="rounded-lg border border-border bg-secondary/40 px-3 py-2 text-xs">
                <p>{pleading.requestText}</p>
                {pleading.legalBasis && <p className="mt-1 text-muted-foreground">{pleading.legalBasis}</p>}
              </div>
            ))}
          </div>
        </KlmPanel>
      </div>

      <Dialog open={factOpen} onOpenChange={(next) => { if (!addFact.isPending) setFactOpen(next); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader><DialogTitle>Adicionar fato</DialogTitle></DialogHeader>
          <form className="space-y-3" onSubmit={handleAddFact}>
            <div className="space-y-1">
              <Label htmlFor="fato-descricao">Descrição</Label>
              <Textarea id="fato-descricao" value={factDescription} onChange={(event) => setFactDescription(event.target.value)} required rows={3} />
            </div>
            <div className="flex items-center gap-2">
              <Checkbox id="fato-controvertido" checked={factControverted} onCheckedChange={(checked) => setFactControverted(checked === true)} />
              <Label htmlFor="fato-controvertido" className="text-xs font-normal">Fato controvertido</Label>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setFactOpen(false)}>Cancelar</Button>
              <Button type="submit" disabled={addFact.isPending}>{addFact.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Adicionar</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={proofOpen} onOpenChange={(next) => { if (!addProofLink.isPending) setProofOpen(next); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Vincular prova a um fato</DialogTitle>
            <DialogDescription>Sem documento anexado ainda, o vínculo nasce como "falta" — resolvido depois com o documento.</DialogDescription>
          </DialogHeader>
          <form className="space-y-3" onSubmit={handleAddProof}>
            <div className="space-y-1">
              <Label htmlFor="prova-fato">Fato</Label>
              <Select value={proofFactId} onValueChange={setProofFactId}>
                <SelectTrigger id="prova-fato"><SelectValue placeholder="Selecione o fato" /></SelectTrigger>
                <SelectContent>
                  {facts.map((fact) => <SelectItem key={fact.id} value={fact.id}>{fact.description.slice(0, 60)}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="prova-forca">Força probatória</Label>
              <Select value={proofForce} onValueChange={(value) => setProofForce(value as KlmProofForce)}>
                <SelectTrigger id="prova-forca"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(Object.keys(PROOF_FORCE_LABELS) as KlmProofForce[]).map((value) => (
                    <SelectItem key={value} value={value}>{PROOF_FORCE_LABELS[value]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setProofOpen(false)}>Cancelar</Button>
              <Button type="submit" disabled={addProofLink.isPending || !proofFactId}>{addProofLink.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Vincular</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={pleadingOpen} onOpenChange={(next) => { if (!addPleading.isPending) setPleadingOpen(next); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Adicionar pedido</DialogTitle>
            <DialogDescription>Sem fato de sustentação marcado, o pedido sinaliza lacuna automaticamente.</DialogDescription>
          </DialogHeader>
          <form className="space-y-3" onSubmit={handleAddPleading}>
            <div className="space-y-1">
              <Label htmlFor="pedido-texto">Pedido</Label>
              <Textarea id="pedido-texto" value={pleadingText} onChange={(event) => setPleadingText(event.target.value)} required rows={3} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="pedido-fundamento">Fundamento legal (opcional)</Label>
              <Input id="pedido-fundamento" value={pleadingBasis} onChange={(event) => setPleadingBasis(event.target.value)} maxLength={240} />
            </div>
            {facts.length > 0 && (
              <div className="space-y-1">
                <Label>Fatos de sustentação (opcional)</Label>
                <div className="max-h-32 space-y-1.5 overflow-y-auto rounded-lg border border-border p-2">
                  {facts.map((fact) => (
                    <label key={fact.id} className="flex items-center gap-2 text-xs">
                      <Checkbox
                        checked={pleadingFactIds.includes(fact.id)}
                        onCheckedChange={(checked) => setPleadingFactIds((current) => (
                          checked === true ? [...current, fact.id] : current.filter((id) => id !== fact.id)
                        ))}
                      />
                      <span className="truncate">{fact.description}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setPleadingOpen(false)}>Cancelar</Button>
              <Button type="submit" disabled={addPleading.isPending}>{addPleading.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Adicionar</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
