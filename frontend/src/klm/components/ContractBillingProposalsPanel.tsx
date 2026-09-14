import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Check, FileText, Loader2, Pencil, ShieldAlert, Sparkles, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { Input } from '@/components/ui/input';
import { useToast } from '@/hooks/use-toast';
import { KlmPanel, KlmPill } from '@/klm/components/Primitives';
import {
  klmApi,
  type KlmContractBillingApproveResult,
  type KlmContractBillingItemKind,
  type KlmContractBillingProposal,
  type KlmContractBillingProposalStatus,
} from '@/klm/api';
import { formatBRL } from '@/lib/formatters';

const STATUS_LABEL: Record<KlmContractBillingProposalStatus, string> = {
  draft: 'Rascunho',
  pending_approval: 'Aguardando aprovação',
  approved: 'Aprovada',
  rejected: 'Rejeitada',
};

const STATUS_TONE: Record<KlmContractBillingProposalStatus, 'gold' | 'green' | 'red' | 'blue' | 'muted'> = {
  draft: 'muted',
  pending_approval: 'gold',
  approved: 'green',
  rejected: 'red',
};

const ITEM_KIND_LABEL: Record<KlmContractBillingItemKind, string> = {
  fixo: 'Valor fixo',
  entrada: 'Entrada',
  parcela: 'Parcela',
  mensalidade: 'Mensalidade',
  exito: 'Êxito',
  por_hora: 'Por hora',
};

function itemValueLabel(item: KlmContractBillingProposal['items'][number]): string {
  const parts: string[] = [];
  if (item.valor !== null) parts.push(formatBRL(item.valor));
  if (item.percentual !== null) parts.push(`${item.percentual}%`);
  if (item.vencimento) parts.push(`vence ${new Date(item.vencimento).toLocaleDateString('pt-BR')}`);
  if (item.ciclo) parts.push(item.ciclo);
  return parts.length > 0 ? parts.join(' · ') : '—';
}

/**
 * Sub-seção "Propostas de contrato" (F11) — cola/envia o texto de um contrato,
 * a extração vira proposta em DRAFT com itens marcados needs_review, revisão
 * humana, envio para aprovação e aprovação/rejeição (finance.manage) que
 * materializa lançamentos/recorrências/arranjos. Consome
 * app/api/v1/endpoints/contract_billing.py. Sem endpoint de listagem no
 * backend — as propostas extraídas nesta sessão ficam neste painel até a
 * página ser recarregada; cada uma pode ser reconsultada por id.
 */
export function ContractBillingProposalsPanel({ canManage }: { canManage: boolean }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [matterId, setMatterId] = useState('');
  const [contractText, setContractText] = useState('');
  const [sourceDocumentName, setSourceDocumentName] = useState('');

  const [proposals, setProposals] = useState<KlmContractBillingProposal[]>([]);
  const [materialized, setMaterialized] = useState<Record<string, KlmContractBillingApproveResult>>({});

  const [rejectTarget, setRejectTarget] = useState<KlmContractBillingProposal | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [editTarget, setEditTarget] = useState<KlmContractBillingProposal | null>(null);
  const [editSourceDocumentName, setEditSourceDocumentName] = useState('');

  const matterFunnelQuery = useQuery({ queryKey: ['klm', 'contract-billing-matters-funnel'], queryFn: klmApi.funnel });
  const matterOptions = useMemo(
    () => matterFunnelQuery.data?.cards ?? [],
    [matterFunnelQuery.data?.cards],
  );
  const matterById = useMemo(() => new Map(matterOptions.map((card) => [card.id, card])), [matterOptions]);

  const upsertProposal = (proposal: KlmContractBillingProposal) => {
    setProposals((prev) => {
      const others = prev.filter((item) => item.id !== proposal.id);
      return [proposal, ...others];
    });
  };

  const invalidateFinance = () => {
    void queryClient.invalidateQueries({ queryKey: ['klm', 'fee-arrangements'] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'finance'] });
  };

  const extractMutation = useMutation({
    mutationFn: () => klmApi.extractContractBillingPlan({
      matterId,
      contractText: contractText.trim(),
      sourceDocumentName: sourceDocumentName.trim() || null,
      requestedBy: null,
    }),
    onSuccess: (proposal) => {
      upsertProposal(proposal);
      setContractText('');
      setSourceDocumentName('');
      const needsReviewCount = proposal.items.filter((item) => item.needsReview).length;
      toast({
        title: 'Plano de cobrança extraído',
        description: needsReviewCount > 0
          ? `${proposal.items.length} item(ns) — ${needsReviewCount} precisam de revisão antes de enviar para aprovação.`
          : `${proposal.items.length} item(ns) extraído(s) em rascunho.`,
      });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível extrair o plano de cobrança', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const submitMutation = useMutation({
    mutationFn: (proposal: KlmContractBillingProposal) =>
      klmApi.submitContractBillingProposal(proposal.id, { requestedBy: null }),
    onSuccess: async (result, proposal) => {
      const refreshed = await klmApi.getContractBillingProposal(proposal.id);
      upsertProposal(refreshed);
      toast({ title: 'Proposta enviada para aprovação', description: `Status da aprovação: ${result.status}.` });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível enviar a proposta', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const approveMutation = useMutation({
    mutationFn: (proposal: KlmContractBillingProposal) => {
      if (!proposal.approvalId) throw new Error('Proposta sem aprovação pendente.');
      return klmApi.approveContractBillingProposal(proposal.approvalId);
    },
    onSuccess: async (result, proposal) => {
      const refreshed = await klmApi.getContractBillingProposal(proposal.id);
      upsertProposal(refreshed);
      setMaterialized((prev) => ({ ...prev, [proposal.id]: result }));
      invalidateFinance();
      toast({
        title: 'Proposta aprovada',
        description: `${result.fixedEntries.length} lançamento(s) fixo(s), ${result.recurrences.length} recorrência(s) e ${result.arrangements.length} arranjo(s) criados.`,
      });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível aprovar a proposta', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: () => {
      if (!rejectTarget?.approvalId) throw new Error('Proposta sem aprovação pendente.');
      return klmApi.rejectContractBillingProposal(rejectTarget.approvalId, {
        resolutionReason: rejectReason.trim() || null,
      });
    },
    onSuccess: (proposal) => {
      upsertProposal(proposal);
      setRejectTarget(null);
      setRejectReason('');
      toast({ title: 'Proposta rejeitada', description: 'Nada foi materializado — nenhum lançamento, recorrência ou arranjo foi criado.' });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível rejeitar a proposta', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const updateMutation = useMutation({
    mutationFn: () => {
      if (!editTarget) throw new Error('Selecione uma proposta em rascunho.');
      return klmApi.updateContractBillingProposal(editTarget.id, {
        sourceDocumentName: editSourceDocumentName.trim() || null,
        expectedUpdatedAt: editTarget.updatedAt,
      }, `contract-panel-${editTarget.id}-${editTarget.updatedAt}`);
    },
    onSuccess: (proposal) => {
      upsertProposal(proposal);
      setEditTarget(null);
      toast({ title: 'Rascunho atualizado', description: 'A alteração foi registrada no histórico antes do envio para aprovação.' });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível atualizar o rascunho', description: error instanceof Error ? error.message : 'Atualize a proposta e tente novamente.', variant: 'destructive' });
    },
  });

  return (
    <div className="space-y-4">
      <KlmPanel className="p-4">
        <h3 className="text-lg font-semibold leading-6 text-foreground">Propostas de contrato</h3>
        <p className="mt-0.5 text-xs leading-4 text-muted-foreground">
          Cole o texto do contrato de honorários — a extração vira uma proposta em rascunho, com itens marcados para revisão antes de enviar para aprovação.
        </p>
        <form
          className="mt-4 space-y-3"
          onSubmit={(event) => { event.preventDefault(); extractMutation.mutate(); }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="contract-billing-matter">Caso</Label>
              <Select value={matterId} onValueChange={setMatterId}>
                <SelectTrigger id="contract-billing-matter" className="h-10 text-sm"><SelectValue placeholder="Selecione um caso" /></SelectTrigger>
                <SelectContent>
                  {matterOptions.map((card) => (
                    <SelectItem key={card.id} value={card.id}>{card.name} · {card.object}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="contract-billing-source">Nome do documento (opcional)</Label>
              <Input id="contract-billing-source" value={sourceDocumentName} onChange={(event) => setSourceDocumentName(event.target.value)} placeholder="Ex.: Contrato_Honorarios_Caso.pdf" />
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="contract-billing-text">Texto do contrato</Label>
            <Textarea
              id="contract-billing-text"
              value={contractText}
              onChange={(event) => setContractText(event.target.value)}
              rows={6}
              placeholder="Cole aqui a cláusula de honorários do contrato..."
              required
            />
          </div>
          <div className="flex justify-end">
            <Button type="submit" disabled={extractMutation.isPending || !canManage || !matterId || !contractText.trim()}>
              {extractMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
              Extrair plano de cobrança
            </Button>
          </div>
        </form>
      </KlmPanel>

      {proposals.length > 0 && (
        <div className="space-y-4">
          {proposals.map((proposal) => {
            const matter = matterById.get(proposal.matterId);
            const needsReviewCount = proposal.items.filter((item) => item.needsReview).length;
            const proposalMaterialized = materialized[proposal.id];
            return (
              <KlmPanel key={proposal.id} className="p-4">
                <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <FileText className="h-4 w-4 text-primary" />
                      <h4 className="text-base font-semibold leading-5 text-foreground">
                        {matter?.name ?? `Caso ${proposal.matterId.slice(0, 8)}`}
                      </h4>
                      <KlmPill tone={STATUS_TONE[proposal.status]}>{STATUS_LABEL[proposal.status]}</KlmPill>
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {proposal.sourceDocumentName ?? 'Sem nome de documento'}
                      {proposal.extractionModel ? ` · modelo ${proposal.extractionModel}` : ''}
                      {needsReviewCount > 0 ? ` · ${needsReviewCount} item(ns) precisam de revisão` : ''}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {proposal.status === 'draft' && (
                      <>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={!canManage}
                          onClick={() => { setEditTarget(proposal); setEditSourceDocumentName(proposal.sourceDocumentName ?? ''); }}
                        >
                          <Pencil className="mr-1.5 h-3.5 w-3.5" />Editar rascunho
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          disabled={submitMutation.isPending || !canManage}
                          onClick={() => submitMutation.mutate(proposal)}
                        >
                          {submitMutation.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                          Enviar para aprovação
                        </Button>
                      </>
                    )}
                    {proposal.status === 'pending_approval' && (
                      <>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          disabled={rejectMutation.isPending || !canManage}
                          onClick={() => { setRejectTarget(proposal); setRejectReason(''); }}
                        >
                          <X className="mr-1.5 h-3.5 w-3.5" />Rejeitar
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          disabled={approveMutation.isPending || !canManage}
                          onClick={() => approveMutation.mutate(proposal)}
                        >
                          {approveMutation.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Check className="mr-1.5 h-3.5 w-3.5" />}
                          Aprovar proposta
                        </Button>
                      </>
                    )}
                  </div>
                </div>

                {proposal.status === 'pending_approval' && (
                  <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-2.5 text-xs text-amber-700 dark:text-amber-400">
                    <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    Aprovar materializa lançamentos, recorrências e arranjos de honorários — a ação não pode ser desfeita, só renegociada depois.
                  </div>
                )}

                <div className="mt-3 overflow-x-auto rounded-lg border border-border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Tipo</TableHead>
                        <TableHead>Valores</TableHead>
                        <TableHead>Descrição</TableHead>
                        <TableHead>Confiança</TableHead>
                        <TableHead>Revisão</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {proposal.items.map((item, index) => (
                        <TableRow key={`${proposal.id}-${index}`}>
                          <TableCell className="text-sm font-medium">{ITEM_KIND_LABEL[item.kind]}</TableCell>
                          <TableCell className="text-sm tabular-nums">{itemValueLabel(item)}</TableCell>
                          <TableCell className="text-xs text-muted-foreground">{item.descricao ?? '—'}</TableCell>
                          <TableCell className="text-xs tabular-nums text-muted-foreground">
                            {item.confidence !== null ? `${Math.round(item.confidence * 100)}%` : '—'}
                          </TableCell>
                          <TableCell>
                            {item.needsReview ? (
                              <KlmPill tone="gold" className="gap-1"><AlertTriangle className="h-3 w-3" />Revisar</KlmPill>
                            ) : (
                              <KlmPill tone="muted">OK</KlmPill>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>

                {proposal.status === 'rejected' && (
                  <p className="mt-3 text-xs text-muted-foreground">Proposta rejeitada — nenhum lançamento foi criado.</p>
                )}

                {proposalMaterialized && (
                  <div className="mt-3 rounded-lg border border-emerald-600/20 bg-emerald-500/[0.08] p-3 text-xs text-emerald-800 dark:text-emerald-300">
                    <strong className="block text-sm">Materializado:</strong>
                    {proposalMaterialized.fixedEntries.length > 0 && (
                      <p>{proposalMaterialized.fixedEntries.length} lançamento(s) fixo(s) — {proposalMaterialized.fixedEntries.map((item) => formatBRL(item.entry.amount)).join(', ')}</p>
                    )}
                    {proposalMaterialized.installments && (
                      <p>{proposalMaterialized.installments.entries.length} parcela(s) de entrada/êxito criadas.</p>
                    )}
                    {proposalMaterialized.recurrences.length > 0 && (
                      <p>{proposalMaterialized.recurrences.length} recorrência(s) de mensalidade programada(s).</p>
                    )}
                    {proposalMaterialized.arrangements.length > 0 && (
                      <p>{proposalMaterialized.arrangements.length} arranjo(s) de honorários criado(s) automaticamente.</p>
                    )}
                  </div>
                )}
              </KlmPanel>
            );
          })}
        </div>
      )}

      <Dialog open={Boolean(rejectTarget)} onOpenChange={(open) => { if (!open) { setRejectTarget(null); setRejectReason(''); } }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Rejeitar proposta de cobrança</DialogTitle>
            <DialogDescription>Nada é materializado — nenhum lançamento, recorrência ou arranjo é criado.</DialogDescription>
          </DialogHeader>
          <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); rejectMutation.mutate(); }}>
            <div className="space-y-1">
              <Label htmlFor="contract-billing-reject-reason">Motivo (opcional)</Label>
              <Textarea id="contract-billing-reject-reason" value={rejectReason} onChange={(event) => setRejectReason(event.target.value)} rows={3} placeholder="Ex.: valores desalinhados com o combinado" />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setRejectTarget(null)}>Cancelar</Button>
              <Button type="submit" variant="destructive" disabled={rejectMutation.isPending || !canManage}>
                {rejectMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Rejeitar proposta
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog open={Boolean(editTarget)} onOpenChange={(open) => { if (!open) setEditTarget(null); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Editar rascunho</DialogTitle>
            <DialogDescription>Somente propostas em rascunho podem ser alteradas. A versão é conferida antes de salvar.</DialogDescription>
          </DialogHeader>
          <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); updateMutation.mutate(); }}>
            <div className="space-y-1">
              <Label htmlFor="contract-billing-edit-source">Nome do documento</Label>
              <Input id="contract-billing-edit-source" value={editSourceDocumentName} onChange={(event) => setEditSourceDocumentName(event.target.value)} />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setEditTarget(null)}>Cancelar</Button>
              <Button type="submit" disabled={updateMutation.isPending || !canManage}>
                {updateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Salvar rascunho
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
