import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Loader2, Scale as ScaleIcon } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { useToast } from '@/hooks/use-toast';
import {
  fetchPlatformPendingBillingReconciliations,
  reconcilePlatformAgentRunBilling,
} from '@/services/platform';
import type {
  PlatformAgentBillingReconciliationDecision,
  PlatformAgentBillingReconciliationRun,
} from '@/types';
import type { PlatformSearchResult } from '@/services/platform-search';

type DecisionValue = 'charged' | 'not_charged';

interface DraftState {
  decision: DecisionValue;
  actualCostUsd: string;
  evidenceReference: string;
  reason: string;
}

const emptyDraft: DraftState = {
  decision: 'charged',
  actualCostUsd: '',
  evidenceReference: '',
  reason: '',
};

function billingBlock(run: PlatformAgentBillingReconciliationRun): Record<string, unknown> {
  const output = run.outputPayload ?? {};
  const block = (output as Record<string, unknown>).billingReconciliation;
  return block && typeof block === 'object' ? (block as Record<string, unknown>) : {};
}

function textField(source: Record<string, unknown>, key: string): string {
  const value = source[key];
  return typeof value === 'string' ? value : '';
}

function formatDateTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('pt-BR');
}

export function PlatformAgentBillingReconciliationPanel({
  defaultWorkspaceId,
  enabled,
  canDecide,
}: {
  defaultWorkspaceId?: string | null;
  enabled: boolean;
  canDecide: boolean;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [pickedWorkspaceId, setPickedWorkspaceId] = useState<string>('');
  const [pickedWorkspace, setPickedWorkspace] = useState<PlatformSearchResult | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftState>(emptyDraft);

  const workspaceId = pickedWorkspaceId || defaultWorkspaceId || '';
  const workspaceName = pickedWorkspace?.id === workspaceId ? pickedWorkspace.label : null;

  const pendingQuery = useQuery({
    queryKey: ['platform', 'agent-billing-reconciliation', workspaceId],
    queryFn: () => fetchPlatformPendingBillingReconciliations(workspaceId),
    enabled: enabled && Boolean(workspaceId),
    staleTime: 15_000,
    retry: false,
  });

  const items = useMemo(() => pendingQuery.data?.items ?? [], [pendingQuery.data]);
  const selectedRun = useMemo(
    () => items.find((item) => item.id === selectedRunId) ?? null,
    [items, selectedRunId],
  );

  const reconcileMutation = useMutation({
    mutationFn: ({ runId, payload }: { runId: string; payload: PlatformAgentBillingReconciliationDecision }) =>
      reconcilePlatformAgentRunBilling(workspaceId, runId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'agent-billing-reconciliation', workspaceId] });
      void queryClient.invalidateQueries({ queryKey: ['platform', 'ai-costs', workspaceId] });
      setSelectedRunId(null);
      setDraft(emptyDraft);
      toast({ title: 'Cobrança conciliada e trilha de auditoria registrada.' });
    },
    onError: (error: Error) =>
      toast({ variant: 'destructive', title: 'Conciliação não aplicada', description: error.message }),
  });

  const startDecision = (run: PlatformAgentBillingReconciliationRun) => {
    const billing = billingBlock(run);
    setSelectedRunId(run.id);
    setDraft({
      decision: 'charged',
      actualCostUsd: textField(billing, 'estimatedCostUsd'),
      evidenceReference: '',
      reason: '',
    });
  };

  const submitDecision = () => {
    if (!selectedRun || !workspaceId) return;
    const billing = billingBlock(selectedRun);
    const providerRequestId = textField(billing, 'providerRequestId');
    reconcileMutation.mutate({
      runId: selectedRun.id,
      payload: {
        decision: draft.decision,
        actualCostUsd: draft.decision === 'charged' ? draft.actualCostUsd.trim() : '0',
        evidenceReference: draft.evidenceReference.trim(),
        reason: draft.reason.trim(),
        providerRequestId: providerRequestId || null,
      },
    });
  };

  const decisionReady =
    draft.evidenceReference.trim().length >= 3
    && draft.reason.trim().length >= 3
    && (draft.decision === 'not_charged' || draft.actualCostUsd.trim().length > 0);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Workspace em conciliação</CardTitle>
          <CardDescription>
            A conciliação é sempre feita dentro de um workspace. Nenhuma execução de outra conta aparece nesta lista.
          </CardDescription>
        </CardHeader>
        <CardContent className="max-w-xl">
          <Label>Conta</Label>
          <PlatformEntityCombobox
            className="mt-1.5"
            entityType="accounts"
            purpose="agent_billing_reconciliation"
            value={workspaceId || null}
            onChange={(value, item) => {
              setPickedWorkspaceId(value ?? '');
              setPickedWorkspace(item);
              setSelectedRunId(null);
              setDraft(emptyDraft);
            }}
            label="Selecionar conta para conciliação"
            placeholder="Pesquisar conta"
            allowClear
          />
        </CardContent>
      </Card>

      {!workspaceId && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Conciliação de cobrança</CardTitle>
            <CardDescription>
              Selecione um workspace para revisar execuções cuja cobrança pelo provider ficou incerta.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {workspaceId && (
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <ScaleIcon className="h-4 w-4 text-primary" />
            Cobranças incertas pendentes
          </CardTitle>
          <CardDescription>
            Execuções de agente em que a chamada ao provider começou mas o faturamento voltou desconhecido.
            {workspaceName ? ` Workspace: ${workspaceName}.` : ''} Nenhum valor é lançado sem decisão humana com evidência.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {pendingQuery.isLoading && (
            <p className="text-sm text-muted-foreground flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" />
              Carregando execuções pendentes...
            </p>
          )}

          {pendingQuery.isError && (
            <p className="text-sm text-destructive flex items-center gap-2">
              <AlertTriangle className="h-4 w-4" />
              Não foi possível carregar as execuções pendentes. Os dados não estão disponíveis agora.
            </p>
          )}

          {!pendingQuery.isLoading && !pendingQuery.isError && items.length === 0 && (
            <p className="text-sm text-muted-foreground" data-testid="billing-reconciliation-empty">
              Nenhuma execução com cobrança incerta pendente neste workspace.
            </p>
          )}

          {items.map((run) => {
            const billing = billingBlock(run);
            return (
              <div key={run.id} className="rounded-md border p-3 space-y-2" data-testid="billing-reconciliation-row">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="space-y-1">
                    <p className="text-sm font-medium">{run.agentName}</p>
                    <p className="text-xs text-muted-foreground">
                      {formatDateTime(run.createdAt)} · {textField(billing, 'provider') || run.aiProvider || 'provider não informado'}
                      {' · '}
                      {textField(billing, 'model') || run.modelUsed || 'modelo não informado'}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant="outline">
                      Estimado: {textField(billing, 'estimatedCostUsd') || 'não informado'}
                    </Badge>
                    <Button
                      size="sm"
                      variant={selectedRunId === run.id ? 'secondary' : 'outline'}
                      disabled={!canDecide}
                      onClick={() => startDecision(run)}
                    >
                      Conciliar
                    </Button>
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">
                  Request do provider: {textField(billing, 'providerRequestId') || 'não informado'}
                </p>
              </div>
            );
          })}
        </CardContent>
      </Card>
      )}

      {selectedRun && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Decisão de conciliação</CardTitle>
            <CardDescription>
              Execução {selectedRun.agentName}. Informe a evidência real do extrato do provider; o valor entra no
              orçamento do workspace.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 max-w-xl">
            <div>
              <Label htmlFor="billing-decision">Decisão</Label>
              <Select
                value={draft.decision}
                onValueChange={(value) => setDraft((current) => ({ ...current, decision: value as DecisionValue }))}
              >
                <SelectTrigger id="billing-decision" className="mt-1.5">
                  <SelectValue placeholder="Selecionar decisão" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="charged">O provider cobrou</SelectItem>
                  <SelectItem value="not_charged">O provider não cobrou</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {draft.decision === 'charged' && (
              <div>
                <Label htmlFor="billing-actual-cost">Custo efetivamente cobrado (USD)</Label>
                <Input
                  id="billing-actual-cost"
                  className="mt-1.5"
                  value={draft.actualCostUsd}
                  onChange={(event) => setDraft((current) => ({ ...current, actualCostUsd: event.target.value }))}
                  placeholder="0.0062"
                />
              </div>
            )}

            <div>
              <Label htmlFor="billing-evidence">Referência da evidência</Label>
              <Input
                id="billing-evidence"
                className="mt-1.5"
                value={draft.evidenceReference}
                onChange={(event) => setDraft((current) => ({ ...current, evidenceReference: event.target.value }))}
                placeholder="invoice-provider-2026-07"
              />
            </div>

            <div>
              <Label htmlFor="billing-reason">Justificativa</Label>
              <Textarea
                id="billing-reason"
                className="mt-1.5"
                value={draft.reason}
                onChange={(event) => setDraft((current) => ({ ...current, reason: event.target.value }))}
                placeholder="Cobrança confirmada no extrato do provider."
              />
            </div>

            <div className="flex items-center gap-2">
              <Button
                onClick={submitDecision}
                disabled={!canDecide || !decisionReady || reconcileMutation.isPending}
              >
                {reconcileMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Registrar decisão
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  setSelectedRunId(null);
                  setDraft(emptyDraft);
                }}
              >
                Cancelar
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export default PlatformAgentBillingReconciliationPanel;
