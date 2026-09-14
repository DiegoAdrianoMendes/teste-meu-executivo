import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, HandCoins, Loader2, Plus, UserPlus, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { KlmMetricCard, KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import {
  klmApi,
  type KlmReferralCommissionType,
  type KlmReferralPartner,
  type KlmReferralPartnerType,
} from '@/klm/api';
import { formatBRL } from '@/lib/formatters';

const PARTNER_TYPE_LABEL: Record<KlmReferralPartnerType, string> = {
  contato: 'Contato',
  cliente: 'Cliente',
  parceiro: 'Parceiro',
  advogado: 'Advogado',
};

const COMMISSION_TYPE_LABEL: Record<KlmReferralCommissionType, string> = {
  percentual: 'Percentual',
  fixo: 'Valor fixo',
};

function formatCommission(partner: Pick<KlmReferralPartner, 'comissaoTipo' | 'comissaoValor'>): string {
  return partner.comissaoTipo === 'percentual'
    ? `${partner.comissaoValor.toLocaleString('pt-BR', { maximumFractionDigits: 2 })}%`
    : formatBRL(partner.comissaoValor);
}

type PartnerDraft = {
  nome: string;
  tipo: KlmReferralPartnerType;
  contatoRef: string;
  comissaoTipo: KlmReferralCommissionType;
  comissaoValor: string;
  vigenciaInicio: string;
  vigenciaFim: string;
  ativo: boolean;
  notas: string;
};

const EMPTY_PARTNER_DRAFT: PartnerDraft = {
  nome: '',
  tipo: 'contato',
  contatoRef: '',
  comissaoTipo: 'percentual',
  comissaoValor: '',
  vigenciaInicio: new Date().toISOString().slice(0, 10),
  vigenciaFim: '',
  ativo: true,
  notas: '',
};

function partnerToDraft(partner: KlmReferralPartner): PartnerDraft {
  return {
    nome: partner.nome,
    tipo: partner.tipo,
    contatoRef: partner.contatoRef ?? '',
    comissaoTipo: partner.comissaoTipo,
    comissaoValor: String(partner.comissaoValor),
    vigenciaInicio: partner.vigenciaInicio.slice(0, 10),
    vigenciaFim: partner.vigenciaFim ? partner.vigenciaFim.slice(0, 10) : '',
    ativo: partner.ativo,
    notas: partner.notas ?? '',
  };
}

/**
 * Painel "Indicações" (F13) — parceiros indicadores, vínculo indicação→caso
 * e comissões acumuladas com aprovação de repasse. Consome
 * GET/POST/PATCH /referrals/partners, /referrals/matters/{id} e
 * GET /referrals/commissions, cruzando com GET /finance/expenses
 * (categoria comissao_indicacao) para o status de aprovação/pagamento.
 */
export function ReferralsPanel({ canManage }: { canManage: boolean }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [partnerDialogOpen, setPartnerDialogOpen] = useState(false);
  const [editingPartnerId, setEditingPartnerId] = useState<string | null>(null);
  const [draft, setDraft] = useState<PartnerDraft>(EMPTY_PARTNER_DRAFT);

  const [linkMatterId, setLinkMatterId] = useState('');
  const [linkPartnerId, setLinkPartnerId] = useState('');
  const [commissionPartnerFilter, setCommissionPartnerFilter] = useState('todos');

  const partnersQuery = useQuery({
    queryKey: ['klm', 'referral-partners'],
    queryFn: () => klmApi.referralPartners(),
  });
  const partners = partnersQuery.data ?? [];
  const activePartners = useMemo(() => partners.filter((partner) => partner.ativo), [partners]);
  const partnerById = useMemo(() => new Map(partners.map((partner) => [partner.id, partner])), [partners]);

  const matterFunnelQuery = useQuery({
    queryKey: ['klm', 'referral-matters-funnel'],
    queryFn: klmApi.funnel,
  });
  const matterOptions = matterFunnelQuery.data?.cards ?? [];
  const matterById = useMemo(() => new Map(matterOptions.map((card) => [card.id, card])), [matterOptions]);

  const matterReferralQuery = useQuery({
    queryKey: ['klm', 'referral-matter-referral', linkMatterId],
    queryFn: () => klmApi.matterReferral(linkMatterId),
    enabled: Boolean(linkMatterId),
  });

  const commissionsQuery = useQuery({
    queryKey: ['klm', 'referral-commissions', commissionPartnerFilter],
    queryFn: () => klmApi.referralCommissions(commissionPartnerFilter === 'todos' ? undefined : commissionPartnerFilter),
  });
  const commissions = commissionsQuery.data ?? [];

  const commissionExpensesQuery = useQuery({
    queryKey: ['klm', 'referral-commission-expenses'],
    queryFn: () => klmApi.listFinanceExpenses({ category: 'comissao_indicacao', limit: 300 }),
  });
  const expenseById = useMemo(
    () => new Map((commissionExpensesQuery.data ?? []).map((expense) => [expense.id, expense])),
    [commissionExpensesQuery.data],
  );

  const pendingApprovalCount = useMemo(
    () => commissions.filter((commission) => expenseById.get(commission.expenseEntryId)?.approvalStatus === 'pending').length,
    [commissions, expenseById],
  );
  const pendingApprovalTotal = useMemo(
    () => commissions
      .filter((commission) => expenseById.get(commission.expenseEntryId)?.approvalStatus === 'pending')
      .reduce((sum, commission) => sum + commission.comissaoValorCalculado, 0),
    [commissions, expenseById],
  );
  const paidTotal = useMemo(
    () => commissions
      .filter((commission) => expenseById.get(commission.expenseEntryId)?.status === 'paid')
      .reduce((sum, commission) => sum + commission.comissaoValorCalculado, 0),
    [commissions, expenseById],
  );

  const savePartnerMutation = useMutation({
    mutationFn: () => {
      const payload = {
        nome: draft.nome.trim(),
        tipo: draft.tipo,
        contatoRef: draft.contatoRef.trim() || null,
        comissaoTipo: draft.comissaoTipo,
        comissaoValor: Number(draft.comissaoValor.replace(',', '.')) || 0,
        vigenciaInicio: draft.vigenciaInicio,
        vigenciaFim: draft.vigenciaFim || null,
        ativo: draft.ativo,
        notas: draft.notas.trim() || null,
      };
      return editingPartnerId
        ? klmApi.updateReferralPartner(editingPartnerId, payload)
        : klmApi.createReferralPartner(payload);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'referral-partners'] });
      setPartnerDialogOpen(false);
      setEditingPartnerId(null);
      setDraft(EMPTY_PARTNER_DRAFT);
      toast({ title: editingPartnerId ? 'Parceiro atualizado' : 'Parceiro cadastrado', description: 'As comissões futuras já usam os dados atuais.' });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível salvar o parceiro', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const toggleActiveMutation = useMutation({
    mutationFn: (partner: KlmReferralPartner) => klmApi.updateReferralPartner(partner.id, { ativo: !partner.ativo }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'referral-partners'] });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível alterar o status', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const linkMutation = useMutation({
    mutationFn: () => klmApi.registerMatterReferral(linkMatterId, { partnerId: linkPartnerId }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'referral-matter-referral', linkMatterId] });
      toast({ title: 'Indicação vinculada ao caso', description: 'A próxima cobrança paga deste caso já gera a comissão automaticamente.' });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível vincular a indicação', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const approveMutation = useMutation({
    mutationFn: ({ expenseEntryId, decision }: { expenseEntryId: string; decision: 'approved' | 'rejected' }) =>
      klmApi.approveFinanceExpense(expenseEntryId, { decision }),
    onSuccess: (_, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'referral-commission-expenses'] });
      toast({
        title: variables.decision === 'approved' ? 'Repasse aprovado' : 'Repasse rejeitado',
        description: variables.decision === 'approved'
          ? 'A comissão segue para pagamento conforme o fluxo financeiro do escritório.'
          : 'A comissão não será paga; o motivo fica registrado na auditoria.',
      });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível registrar a decisão', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const openCreateDialog = () => {
    setEditingPartnerId(null);
    setDraft(EMPTY_PARTNER_DRAFT);
    setPartnerDialogOpen(true);
  };
  const openEditDialog = (partner: KlmReferralPartner) => {
    setEditingPartnerId(partner.id);
    setDraft(partnerToDraft(partner));
    setPartnerDialogOpen(true);
  };

  const linkedReferral = matterReferralQuery.data;
  const linkedPartnerName = linkedReferral ? partnerById.get(linkedReferral.partnerId)?.nome ?? 'Parceiro não encontrado' : null;

  return (
    <div className="space-y-4">
      <KlmPanel className="p-4">
        <KlmSectionTitle
          title="Indicações"
          description="Parceiros que indicam casos ao escritório, o vínculo de cada indicação a um caso e as comissões geradas automaticamente quando o caso é pago."
        />
        <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <KlmMetricCard label="Parceiros ativos" value={String(activePartners.length)} delta={`${partners.length} cadastrado(s)`} trend="neutral" />
          <KlmMetricCard label="Comissões pendentes" value={String(pendingApprovalCount)} delta="aguardando aprovação" trend={pendingApprovalCount > 0 ? 'down' : 'neutral'} />
          <KlmMetricCard label="Valor pendente" value={formatBRL(pendingApprovalTotal)} delta="a aprovar" trend="neutral" />
          <KlmMetricCard label="Já pago a parceiros" value={formatBRL(paidTotal)} delta="repasses concluídos" trend="up" />
        </div>
      </KlmPanel>

      <KlmPanel className="p-4">
        <KlmSectionTitle
          title="Parceiros indicadores"
          description="Cadastro dos indicadores e a regra de comissão vigente de cada um."
          action={(
            <Button type="button" size="sm" onClick={openCreateDialog} disabled={!canManage}>
              <Plus className="mr-2 h-3.5 w-3.5" />Novo parceiro
            </Button>
          )}
        />
        <div className="mt-4 overflow-x-auto rounded-lg border border-border">
          {partnersQuery.isPending ? (
            <div className="flex min-h-24 items-center justify-center gap-2 p-6 text-xs text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />Carregando parceiros...
            </div>
          ) : partnersQuery.isError ? (
            <p className="p-6 text-xs text-muted-foreground">Não foi possível carregar os parceiros de indicação.</p>
          ) : partners.length === 0 ? (
            <p className="p-6 text-xs text-muted-foreground">Nenhum parceiro cadastrado ainda.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Nome</TableHead>
                  <TableHead>Tipo</TableHead>
                  <TableHead>Comissão</TableHead>
                  <TableHead>Vigência</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Ações</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {partners.map((partner) => (
                  <TableRow key={partner.id}>
                    <TableCell className="text-sm font-medium">{partner.nome}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{PARTNER_TYPE_LABEL[partner.tipo]}</TableCell>
                    <TableCell className="text-sm tabular-nums">{formatCommission(partner)} · {COMMISSION_TYPE_LABEL[partner.comissaoTipo]}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(partner.vigenciaInicio).toLocaleDateString('pt-BR')}
                      {partner.vigenciaFim ? ` a ${new Date(partner.vigenciaFim).toLocaleDateString('pt-BR')}` : ' · sem término'}
                    </TableCell>
                    <TableCell>
                      <KlmPill tone={partner.ativo ? 'green' : 'muted'}>{partner.ativo ? 'Ativo' : 'Inativo'}</KlmPill>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button type="button" size="sm" variant="outline" onClick={() => openEditDialog(partner)} disabled={!canManage}>Editar</Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          disabled={!canManage || toggleActiveMutation.isPending}
                          onClick={() => toggleActiveMutation.mutate(partner)}
                        >
                          {partner.ativo ? 'Desativar' : 'Ativar'}
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </KlmPanel>

      <KlmPanel className="p-4">
        <KlmSectionTitle
          title="Vincular indicação a um caso"
          description="Associe um parceiro a um caso: a comissão é lançada sozinha na primeira cobrança paga desse caso."
        />
        <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <div className="space-y-1">
            <Label htmlFor="referral-matter">Caso</Label>
            <Select value={linkMatterId} onValueChange={setLinkMatterId}>
              <SelectTrigger id="referral-matter" className="h-10 text-sm"><SelectValue placeholder="Selecione um caso" /></SelectTrigger>
              <SelectContent>
                {matterOptions.map((card) => (
                  <SelectItem key={card.id} value={card.id}>{card.name} · {card.object}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="referral-partner">Parceiro indicador</Label>
            <Select value={linkPartnerId} onValueChange={setLinkPartnerId}>
              <SelectTrigger id="referral-partner" className="h-10 text-sm"><SelectValue placeholder="Selecione um parceiro ativo" /></SelectTrigger>
              <SelectContent>
                {activePartners.map((partner) => (
                  <SelectItem key={partner.id} value={partner.id}>{partner.nome}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button
            type="button"
            onClick={() => linkMutation.mutate()}
            disabled={!canManage || !linkMatterId || !linkPartnerId || linkMutation.isPending}
          >
            {linkMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <UserPlus className="mr-2 h-4 w-4" />}
            Vincular
          </Button>
        </div>
        {linkMatterId && (
          <p className="mt-3 text-xs text-muted-foreground">
            {matterReferralQuery.isPending
              ? 'Consultando indicação atual deste caso...'
              : linkedReferral
                ? `Este caso já está vinculado a ${linkedPartnerName}, desde ${new Date(linkedReferral.data).toLocaleDateString('pt-BR')}. Vincular de novo substitui o parceiro.`
                : 'Este caso ainda não tem indicação registrada.'}
          </p>
        )}
      </KlmPanel>

      <KlmPanel className="p-4">
        <KlmSectionTitle
          title="Comissões acumuladas"
          description="Uma linha por cobrança paga que gerou comissão — pendente de aprovação, aprovada ou já paga."
          action={(
            <Select value={commissionPartnerFilter} onValueChange={setCommissionPartnerFilter}>
              <SelectTrigger className="h-9 w-48 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="todos">Todos os parceiros</SelectItem>
                {partners.map((partner) => (
                  <SelectItem key={partner.id} value={partner.id}>{partner.nome}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        />
        <div className="mt-4 overflow-x-auto rounded-lg border border-border">
          {commissionsQuery.isPending || commissionExpensesQuery.isPending ? (
            <div className="flex min-h-24 items-center justify-center gap-2 p-6 text-xs text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />Carregando comissões...
            </div>
          ) : commissionsQuery.isError || commissionExpensesQuery.isError ? (
            <p className="p-6 text-xs text-muted-foreground">Não foi possível carregar as comissões.</p>
          ) : commissions.length === 0 ? (
            <p className="p-6 text-xs text-muted-foreground">Nenhuma comissão registrada ainda para este filtro.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Parceiro</TableHead>
                  <TableHead>Caso</TableHead>
                  <TableHead className="text-right">Valor base</TableHead>
                  <TableHead className="text-right">Comissão</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Ações</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {commissions.map((commission) => {
                  const expense = expenseById.get(commission.expenseEntryId);
                  const isPaid = expense?.status === 'paid';
                  const approvalStatus = expense?.approvalStatus ?? 'pending';
                  const statusLabel = isPaid
                    ? 'Paga'
                    : approvalStatus === 'approved'
                      ? 'Aprovada'
                      : approvalStatus === 'rejected'
                        ? 'Rejeitada'
                        : approvalStatus === 'not_required'
                          ? 'Sem aprovação necessária'
                          : 'Pendente de aprovação';
                  const statusTone = isPaid ? 'green' : approvalStatus === 'approved' ? 'blue' : approvalStatus === 'rejected' ? 'red' : 'gold';
                  const canApprove = canManage && !isPaid && approvalStatus === 'pending';
                  return (
                    <TableRow key={commission.id}>
                      <TableCell className="text-sm font-medium">{partnerById.get(commission.partnerId)?.nome ?? 'Parceiro'}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{matterById.get(commission.matterId)?.name ?? `Caso ${commission.matterId.slice(0, 8)}`}</TableCell>
                      <TableCell className="text-right text-sm tabular-nums">{formatBRL(commission.valorBase)}</TableCell>
                      <TableCell className="text-right text-sm font-semibold tabular-nums">{formatBRL(commission.comissaoValorCalculado)}</TableCell>
                      <TableCell><KlmPill tone={statusTone}>{statusLabel}</KlmPill></TableCell>
                      <TableCell className="text-right">
                        {canApprove ? (
                          <div className="flex justify-end gap-2">
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              disabled={approveMutation.isPending}
                              onClick={() => approveMutation.mutate({ expenseEntryId: commission.expenseEntryId, decision: 'rejected' })}
                            >
                              <X className="mr-1 h-3.5 w-3.5" />Rejeitar
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              disabled={approveMutation.isPending}
                              onClick={() => approveMutation.mutate({ expenseEntryId: commission.expenseEntryId, decision: 'approved' })}
                            >
                              {approveMutation.isPending ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Check className="mr-1 h-3.5 w-3.5" />}
                              Aprovar repasse
                            </Button>
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </div>
      </KlmPanel>

      <Dialog open={partnerDialogOpen} onOpenChange={(open) => { setPartnerDialogOpen(open); if (!open) { setEditingPartnerId(null); setDraft(EMPTY_PARTNER_DRAFT); } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2"><HandCoins className="h-4 w-4 text-primary" />{editingPartnerId ? 'Editar parceiro' : 'Novo parceiro de indicação'}</DialogTitle>
            <DialogDescription>A regra de comissão se aplica a partir de agora — indicações e comissões já lançadas não mudam retroativamente.</DialogDescription>
          </DialogHeader>
          <form
            className="grid gap-3 sm:grid-cols-2"
            onSubmit={(event) => {
              event.preventDefault();
              savePartnerMutation.mutate();
            }}
          >
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="partner-nome">Nome</Label>
              <Input id="partner-nome" value={draft.nome} onChange={(event) => setDraft((prev) => ({ ...prev, nome: event.target.value }))} required />
            </div>
            <div className="space-y-1">
              <Label htmlFor="partner-tipo">Tipo</Label>
              <Select value={draft.tipo} onValueChange={(value) => setDraft((prev) => ({ ...prev, tipo: value as KlmReferralPartnerType }))}>
                <SelectTrigger id="partner-tipo" className="h-10 text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(Object.keys(PARTNER_TYPE_LABEL) as KlmReferralPartnerType[]).map((value) => (
                    <SelectItem key={value} value={value}>{PARTNER_TYPE_LABEL[value]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="partner-contato">Contato (telefone/e-mail, opcional)</Label>
              <Input id="partner-contato" value={draft.contatoRef} onChange={(event) => setDraft((prev) => ({ ...prev, contatoRef: event.target.value }))} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="partner-comissao-tipo">Tipo de comissão</Label>
              <Select value={draft.comissaoTipo} onValueChange={(value) => setDraft((prev) => ({ ...prev, comissaoTipo: value as KlmReferralCommissionType }))}>
                <SelectTrigger id="partner-comissao-tipo" className="h-10 text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(Object.keys(COMMISSION_TYPE_LABEL) as KlmReferralCommissionType[]).map((value) => (
                    <SelectItem key={value} value={value}>{COMMISSION_TYPE_LABEL[value]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="partner-comissao-valor">{draft.comissaoTipo === 'percentual' ? 'Percentual (%)' : 'Valor fixo (R$)'}</Label>
              <Input
                id="partner-comissao-valor"
                inputMode="decimal"
                value={draft.comissaoValor}
                onChange={(event) => setDraft((prev) => ({ ...prev, comissaoValor: event.target.value }))}
                placeholder={draft.comissaoTipo === 'percentual' ? 'Ex.: 10' : 'Ex.: 500'}
                required
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="partner-vigencia-inicio">Vigência início</Label>
              <Input id="partner-vigencia-inicio" type="date" value={draft.vigenciaInicio} onChange={(event) => setDraft((prev) => ({ ...prev, vigenciaInicio: event.target.value }))} required />
            </div>
            <div className="space-y-1">
              <Label htmlFor="partner-vigencia-fim">Vigência fim (opcional)</Label>
              <Input id="partner-vigencia-fim" type="date" value={draft.vigenciaFim} onChange={(event) => setDraft((prev) => ({ ...prev, vigenciaFim: event.target.value }))} />
            </div>
            <div className="flex items-center justify-between gap-3 rounded-md border border-border p-3 sm:col-span-2">
              <div>
                <p className="text-sm font-medium text-foreground">Parceiro ativo</p>
                <p className="text-xs text-muted-foreground">Só parceiros ativos aparecem para vincular a novos casos.</p>
              </div>
              <Switch checked={draft.ativo} onCheckedChange={(checked) => setDraft((prev) => ({ ...prev, ativo: checked }))} aria-label="Parceiro ativo" />
            </div>
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="partner-notas">Notas (opcional)</Label>
              <Textarea id="partner-notas" value={draft.notas} onChange={(event) => setDraft((prev) => ({ ...prev, notas: event.target.value }))} rows={2} />
            </div>
            <DialogFooter className="sm:col-span-2">
              <Button type="button" variant="outline" onClick={() => setPartnerDialogOpen(false)}>Cancelar</Button>
              <Button type="submit" disabled={savePartnerMutation.isPending || !canManage}>
                {savePartnerMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {editingPartnerId ? 'Salvar alterações' : 'Cadastrar parceiro'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
