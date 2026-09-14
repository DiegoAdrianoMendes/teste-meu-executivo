import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Save, ShieldAlert } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useToast } from '@/hooks/use-toast';
import { klmApi, type KlmAgent, type KlmAgentAutonomyMode, type KlmAgentAutonomySetting } from '@/klm/api';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';

const MODE_LABEL: Record<KlmAgentAutonomyMode, string> = {
  hil_total: 'HIL total',
  hil_marcos: 'HIL por marcos',
  autonomo: 'Autônomo (banda)',
};

const MODE_DESCRIPTION: Record<KlmAgentAutonomyMode, string> = {
  hil_total: 'Aprova cada resposta externa antes do envio ao cliente.',
  hil_marcos: 'Segue sozinho na conversa e só pede aprovação nos marcos: proposta, contrato e desconto.',
  autonomo: 'Conduz a negociação sozinho até o aceite do contrato, dentro do piso e da banda abaixo.',
};

type AutonomyDraft = { autonomyMode: KlmAgentAutonomyMode; negotiationFloorPct: number | null; negotiationBandPct: number | null };

const toDraft = (setting: KlmAgentAutonomySetting): AutonomyDraft => ({
  autonomyMode: setting.autonomyMode,
  negotiationFloorPct: setting.negotiationFloorPct,
  negotiationBandPct: setting.negotiationBandPct,
});

const pctToUi = (value: number | null) => (value === null || value === undefined ? '' : String(Math.round(value * 1000) / 10));
const pctFromUi = (value: string) => {
  if (!value.trim()) return null;
  const parsed = Number(value);
  if (Number.isNaN(parsed)) return null;
  return Math.max(0, Math.min(100, parsed)) / 100;
};

function AgentAutonomyRow({ agent, setting, canManage }: { agent: KlmAgent; setting: KlmAgentAutonomySetting; canManage: boolean }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<AutonomyDraft>(() => toDraft(setting));

  useEffect(() => setDraft(toDraft(setting)), [setting]);

  const saveMutation = useMutation({
    mutationFn: (payload: AutonomyDraft) => klmApi.setAgentAutonomy(agent.key, {
      autonomyMode: payload.autonomyMode,
      negotiationFloorPct: payload.autonomyMode === 'autonomo' ? payload.negotiationFloorPct : null,
      negotiationBandPct: payload.autonomyMode === 'autonomo' ? payload.negotiationBandPct : null,
      reason: 'Autonomia ajustada no painel de administração do workspace.',
      expectedEtag: setting.etag,
    }),
    onSuccess: (updated) => {
      queryClient.setQueryData<KlmAgentAutonomySetting[]>(['klm', 'agent-autonomy'], (current) =>
        (current ?? []).map((item) => (item.agentKey === updated.agentKey ? updated : item)));
      setDraft(toDraft(updated));
      toast({ title: `Autonomia de ${agent.name} salva.` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível salvar a autonomia', description: error.message }),
  });

  const dirty = draft.autonomyMode !== setting.autonomyMode
    || draft.negotiationFloorPct !== setting.negotiationFloorPct
    || draft.negotiationBandPct !== setting.negotiationBandPct;

  return (
    <div className="grid gap-3 border-b border-border p-4 last:border-b-0 lg:grid-cols-[220px_220px_1fr_auto] lg:items-start">
      <div className="min-w-0">
        <p className="text-xs font-semibold">{agent.name}</p>
        <p className="mt-0.5 text-2xs text-muted-foreground">{agent.role}</p>
      </div>
      <div>
        <Select value={draft.autonomyMode} disabled={!canManage} onValueChange={(value) => setDraft((current) => ({ ...current, autonomyMode: value as KlmAgentAutonomyMode }))}>
          <SelectTrigger className="h-9 text-2xs" aria-label={`Modo de autonomia de ${agent.name}`}><SelectValue /></SelectTrigger>
          <SelectContent>
            {(Object.keys(MODE_LABEL) as KlmAgentAutonomyMode[]).map((mode) => (
              <SelectItem key={mode} value={mode} className="text-2xs">{MODE_LABEL[mode]}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="mt-1.5 text-2xs leading-4 text-muted-foreground">{MODE_DESCRIPTION[draft.autonomyMode]}</p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <Label className="text-2xs">Piso de negociação</Label>
          <div className="relative">
            <Input type="number" min={0} max={100} step={1} disabled={!canManage || draft.autonomyMode !== 'autonomo'} value={pctToUi(draft.negotiationFloorPct)} onChange={(event) => setDraft((current) => ({ ...current, negotiationFloorPct: pctFromUi(event.target.value) }))} className="h-9 pr-7 text-2xs" aria-label={`Piso de negociação de ${agent.name}`} />
            <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-2xs text-muted-foreground">%</span>
          </div>
        </div>
        <div className="space-y-1">
          <Label className="text-2xs">Banda</Label>
          <div className="relative">
            <Input type="number" min={0} max={100} step={1} disabled={!canManage || draft.autonomyMode !== 'autonomo'} value={pctToUi(draft.negotiationBandPct)} onChange={(event) => setDraft((current) => ({ ...current, negotiationBandPct: pctFromUi(event.target.value) }))} className="h-9 pr-7 text-2xs" aria-label={`Banda de negociação de ${agent.name}`} />
            <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-2xs text-muted-foreground">%</span>
          </div>
        </div>
      </div>
      {canManage ? (
        <Button size="sm" className="h-9 shrink-0 text-2xs" disabled={!dirty || saveMutation.isPending} onClick={() => saveMutation.mutate(draft)}>
          {saveMutation.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Save className="mr-1.5 h-3.5 w-3.5" />}Salvar
        </Button>
      ) : <KlmPill tone="muted">Somente leitura</KlmPill>}
    </div>
  );
}

export function AgentAutonomyCard({ agents, canManage }: { agents: KlmAgent[]; canManage: boolean }) {
  const autonomyQuery = useQuery({ queryKey: ['klm', 'agent-autonomy'], queryFn: klmApi.agentAutonomySettings });
  const settingsByKey = new Map((autonomyQuery.data ?? []).map((item) => [item.agentKey, item]));

  return (
    <KlmPanel className="overflow-hidden" data-testid="agent-autonomy-card">
      <div className="border-b border-border p-4 sm:p-5">
        <KlmSectionTitle
          title="Autonomia por agente"
          description="Define, por agente de vendas, o nível de human-in-the-loop na negociação com o cliente."
          action={<KlmPill tone="red"><ShieldAlert className="mr-1 h-3 w-3" />Fora da banda sempre exige humano</KlmPill>}
        />
        <div className="mt-3 rounded-lg border border-red-700/25 bg-red-500/[0.08] p-3 text-2xs leading-4 text-[#a53e3c] dark:text-red-300">
          Assinatura, protocolo e desconto fora da banda pré-aprovada <strong>SEMPRE</strong> exigem decisão humana, mesmo em modo autônomo.
        </div>
      </div>
      {autonomyQuery.isPending ? (
        <div className="flex items-center justify-center gap-2 p-8 text-2xs text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Carregando configuração de autonomia</div>
      ) : autonomyQuery.isError ? (
        <div className="p-5"><p className="text-xs font-semibold text-[#a53e3c]">Não foi possível carregar a autonomia dos agentes.</p><Button variant="outline" size="sm" className="mt-3" onClick={() => void autonomyQuery.refetch()}>Tentar novamente</Button></div>
      ) : (
        <div className="max-h-[560px] overflow-y-auto">
          {agents.map((agent) => {
            const setting = settingsByKey.get(agent.key);
            if (!setting) return null;
            return <AgentAutonomyRow key={agent.key} agent={agent} setting={setting} canManage={canManage} />;
          })}
        </div>
      )}
    </KlmPanel>
  );
}

export default AgentAutonomyCard;
