import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, Clock, Loader2, MailWarning, Save, ShieldAlert, UserCheck } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { formatBRL } from '@/lib/formatters';
import { klmApi, type KlmFinanceDunningLog, type KlmFinanceDunningSettings, type KlmFinanceDunningStepKey } from '@/klm/api';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { useFinanceDunningConfig, useFinanceDunningLog } from '@/klm/api/useFinanceDunning';

const STEP_ORDER: KlmFinanceDunningStepKey[] = ['d1_lembrete', 'd7_segunda_via', 'd15_formal'];

const STEP_LABEL: Record<KlmFinanceDunningStepKey, string> = {
  d1_lembrete: 'D+1 · Lembrete',
  d7_segunda_via: 'D+7 · Segunda via',
  d15_formal: 'D+15 · Formal',
};

const STATUS_LABEL: Record<string, string> = {
  sent: 'Enviado',
  local_draft: 'Rascunho local',
  pending_approval: 'Aguardando aprovação',
  blocked_gate_closed: 'Bloqueado (gate fechado)',
};

const statusTone = (status: string) => (
  status === 'sent' ? 'green' : status === 'pending_approval' ? 'gold' : status === 'blocked_gate_closed' ? 'red' : 'muted'
);

function LogRow({ log, showApprove, onApprove, approving }: { log: KlmFinanceDunningLog; showApprove: boolean; onApprove: () => void; approving: boolean }) {
  return (
    <div className="grid gap-2 border-b border-border p-3 last:border-b-0 sm:grid-cols-[1fr_auto] sm:items-center">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <KlmPill tone="black">{STEP_LABEL[log.step] ?? log.step}</KlmPill>
          <strong className="truncate text-2xs">{log.entryTitle ?? `Cobrança ${log.chargeId.slice(0, 8)}`}</strong>
          {log.amount !== null && <span className="text-2xs text-muted-foreground">{formatBRL(log.amount)}</span>}
        </div>
        {log.messagePreview && <p className="mt-1 line-clamp-2 text-2xs leading-4 text-muted-foreground">{log.messagePreview}</p>}
        <span className="mt-1 block text-2xs text-muted-foreground">{new Date(log.createdAt).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}</span>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <KlmPill tone={statusTone(log.status)}>{STATUS_LABEL[log.status] ?? log.status}</KlmPill>
        {showApprove && (
          <Button size="sm" className="h-7 text-2xs" disabled={approving} onClick={onApprove}>
            {approving ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <UserCheck className="mr-1.5 h-3.5 w-3.5" />}Aprovar
          </Button>
        )}
      </div>
    </div>
  );
}

export function FinanceDunningCard({ canManage }: { canManage: boolean }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const configQuery = useFinanceDunningConfig();
  const recentLogQuery = useFinanceDunningLog({ limit: 20 });
  const pendingLogQuery = useFinanceDunningLog({ status: 'pending_approval', limit: 50 });
  const [draft, setDraft] = useState<KlmFinanceDunningSettings | null>(null);

  useEffect(() => {
    if (configQuery.data) setDraft({ steps: { ...configQuery.data.steps } });
  }, [configQuery.data]);

  const refreshLogs = () => {
    void queryClient.invalidateQueries({ queryKey: ['klm', 'finance-dunning-log'] });
  };

  const saveMutation = useMutation({
    mutationFn: (payload: KlmFinanceDunningSettings) => klmApi.updateFinanceDunningConfig(payload.steps),
    onSuccess: (saved) => {
      setDraft({ steps: { ...saved.steps } });
      queryClient.setQueryData(['klm', 'finance-dunning-config'], saved);
      toast({ title: 'Régua de cobrança salva.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível salvar a régua', description: error.message }),
  });

  const approveMutation = useMutation({
    mutationFn: (logId: string) => klmApi.approveFinanceDunningLog(logId),
    onSuccess: () => {
      refreshLogs();
      toast({ title: 'Degrau aprovado e liberado para disparo.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível aprovar', description: error.message }),
  });

  const updateStep = (key: KlmFinanceDunningStepKey, patch: Partial<KlmFinanceDunningSettings['steps'][KlmFinanceDunningStepKey]>) => {
    setDraft((current) => current ? { steps: { ...current.steps, [key]: { ...current.steps[key], ...patch } } } : current);
  };

  const pending = pendingLogQuery.data ?? [];

  if (configQuery.isPending) {
    return <KlmPanel className="flex min-h-44 items-center justify-center gap-2 p-5 text-2xs text-muted-foreground" data-testid="finance-dunning-card"><Loader2 className="h-4 w-4 animate-spin" />Carregando régua de cobrança</KlmPanel>;
  }
  if (configQuery.isError || !draft) {
    return <KlmPanel className="p-5" data-testid="finance-dunning-card"><p className="text-xs font-semibold text-[#a53e3c]">Não foi possível carregar a régua de cobrança.</p><Button variant="outline" size="sm" className="mt-3" onClick={() => void configQuery.refetch()}>Tentar novamente</Button></KlmPanel>;
  }

  return (
    <div className="space-y-4" data-testid="finance-dunning-card">
      <KlmPanel className="overflow-hidden">
        <div className="border-b border-border p-4 sm:p-5">
          <KlmSectionTitle
            title="Régua de cobrança"
            description="Três degraus automáticos por cobrança vencida. O degrau formal (D+15) sempre exige aprovação humana antes do disparo."
            action={<KlmPill tone="gold"><ShieldAlert className="mr-1 h-3 w-3" />D+15 exige aprovação humana</KlmPill>}
          />
        </div>
        <div className="divide-y divide-border">
          {STEP_ORDER.map((key) => {
            const step = draft.steps[key];
            if (!step) return null;
            const isFinal = key === 'd15_formal';
            return (
              <div key={key} className="grid gap-3 p-4 sm:p-5 lg:grid-cols-[200px_120px_1fr]">
                <div className="flex items-start justify-between gap-3 lg:flex-col lg:items-start">
                  <div>
                    <p className="text-xs font-semibold">{STEP_LABEL[key]}</p>
                    <p className="mt-1 text-2xs text-muted-foreground">{isFinal ? 'Cobrança formal' : 'Cobrança amistosa'}</p>
                  </div>
                  <Switch checked={step.enabled} disabled={!canManage} onCheckedChange={(checked) => updateStep(key, { enabled: checked })} aria-label={`Ativar ${STEP_LABEL[key]}`} />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-2xs">Disparo em</Label>
                  <div className="relative">
                    <Input aria-label={`Offset em dias de ${STEP_LABEL[key]}`} type="number" min={0} max={120} disabled={!canManage} value={step.offsetDays} onChange={(event) => updateStep(key, { offsetDays: Math.max(0, Math.min(120, Number(event.target.value) || 0)) })} className="pr-14" />
                    <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-2xs text-muted-foreground">dias venc.</span>
                  </div>
                  <label className="mt-2 flex items-center gap-2 text-2xs">
                    <input type="checkbox" className="h-3.5 w-3.5 accent-primary" checked={step.requiresApproval} disabled={!canManage || isFinal} onChange={(event) => updateStep(key, { requiresApproval: event.target.checked })} />
                    Exige aprovação humana{isFinal ? ' (fixo)' : ''}
                  </label>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-2xs">Modelo da mensagem</Label>
                  <Textarea value={step.messageTemplate} disabled={!canManage} onChange={(event) => updateStep(key, { messageTemplate: event.target.value })} className="min-h-20 text-2xs" aria-label={`Modelo de mensagem de ${STEP_LABEL[key]}`} />
                </div>
              </div>
            );
          })}
        </div>
        {canManage && (
          <div className="flex flex-col gap-3 border-t border-border bg-secondary/50 p-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="flex items-start gap-2 text-2xs leading-4 text-muted-foreground"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0b7435]" />Alterações valem para as próximas cobranças vencidas; disparos já enfileirados não são reabertos.</p>
            <Button className="shrink-0" disabled={saveMutation.isPending} onClick={() => saveMutation.mutate({ steps: { ...draft.steps, d15_formal: { ...draft.steps.d15_formal, requiresApproval: true } } })}>{saveMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}Salvar régua</Button>
          </div>
        )}
      </KlmPanel>

      <div className="grid gap-4 lg:grid-cols-2">
        <KlmPanel className="overflow-hidden">
          <div className="border-b border-border p-4"><KlmSectionTitle title="Fila de aprovação" description="Degraus aguardando aprovação humana antes do disparo" action={<KlmPill tone={pending.length ? 'red' : 'green'}>{pending.length} pendente{pending.length === 1 ? '' : 's'}</KlmPill>} /></div>
          <div className="max-h-[420px] overflow-y-auto">
            {pendingLogQuery.isPending ? (
              <div className="flex items-center justify-center gap-2 p-8 text-2xs text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Carregando fila</div>
            ) : pending.length ? (
              pending.map((log) => (
                <LogRow key={log.id} log={log} showApprove={canManage} approving={approveMutation.isPending && approveMutation.variables === log.id} onApprove={() => approveMutation.mutate(log.id)} />
              ))
            ) : (
              <div className="p-8 text-center text-2xs text-muted-foreground">Nenhum degrau aguardando aprovação.</div>
            )}
          </div>
        </KlmPanel>

        <KlmPanel className="overflow-hidden">
          <div className="border-b border-border p-4"><KlmSectionTitle title="Últimos disparos" description="Histórico recente da régua no workspace" action={<KlmPill tone="black"><Clock className="mr-1 h-3 w-3" />{recentLogQuery.data?.length ?? 0} registros</KlmPill>} /></div>
          <div className="max-h-[420px] overflow-y-auto">
            {recentLogQuery.isPending ? (
              <div className="flex items-center justify-center gap-2 p-8 text-2xs text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Carregando histórico</div>
            ) : recentLogQuery.data?.length ? (
              recentLogQuery.data.map((log) => <LogRow key={log.id} log={log} showApprove={false} approving={false} onApprove={() => undefined} />)
            ) : (
              <div className="p-8 text-center text-2xs text-muted-foreground"><MailWarning className="mx-auto mb-2 h-4 w-4" />Nenhum disparo registrado ainda.</div>
            )}
          </div>
        </KlmPanel>
      </div>
    </div>
  );
}

export default FinanceDunningCard;
