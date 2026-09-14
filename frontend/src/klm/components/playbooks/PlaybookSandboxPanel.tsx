import { Beaker, Bot, CircleAlert, CircleCheck, LoaderCircle, ShieldCheck, UserRoundCheck } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { KlmPill } from '@/klm/components/Primitives';
import { autonomyLabel } from '@/klm/playbooks/presentation';
import type { PlaybookSimulation, PlaybookSimulationInput } from '@/services/playbooks';

interface Props {
  simulations: PlaybookSimulation[];
  pending: boolean;
  onSimulate: (input: PlaybookSimulationInput) => void;
}

function SimulationResult({ simulation }: { simulation: PlaybookSimulation }) {
  const passed = simulation.status === 'passed';
  return (
    <article className="border-t py-5 first:border-t-0 first:pt-0" data-testid="playbook-simulation-result">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">{passed ? <CircleCheck className="h-4 w-4 text-emerald-600" /> : <CircleAlert className="h-4 w-4 text-amber-600" />}<strong className="text-[11px]">{simulation.scenario}</strong><KlmPill tone={passed ? 'green' : 'gold'}>{simulation.status === 'needs_review' ? 'Revisão' : passed ? 'Aprovada' : 'Falhou'}</KlmPill></div>
        <div className="flex flex-wrap gap-1.5"><KlmPill tone="black">v{simulation.version}</KlmPill><KlmPill tone="blue">{autonomyLabel[simulation.autonomyMode]}</KlmPill></div>
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(280px,.8fr)]">
        <div className="space-y-3">
          <div className="rounded-md bg-[#f4f1eb] p-3 text-[11px] leading-5 text-[#202020] dark:bg-white/8 dark:text-white"><p className="mb-1 text-[9px] font-semibold uppercase text-muted-foreground">Resposta proposta</p>{simulation.response}</div>
          <div className="text-[10px]"><span className="font-semibold">Próxima ação: </span><span className="text-muted-foreground">{simulation.nextAction}</span></div>
          <div><p className="text-[9px] font-semibold uppercase text-muted-foreground">Fontes atribuídas</p><div className="mt-2 flex flex-wrap gap-1.5">{simulation.sources.map((source) => <KlmPill key={source} tone="muted">{source}</KlmPill>)}</div></div>
        </div>
        <div className="space-y-3">
          <div><p className="text-[9px] font-semibold uppercase text-muted-foreground">Trilha da política</p><div className="mt-2 space-y-2">{simulation.policyTrace.map((trace, index) => <div key={`${trace.rule}-${index}`} className="border-l-2 border-primary pl-2 text-[9px] leading-4"><strong>{trace.rule}</strong><span className="ml-1 text-muted-foreground">{trace.outcome}: {trace.reason}</span></div>)}</div></div>
          <div className="rounded-md border p-2 text-[9px]"><ShieldCheck className="mb-1 h-3.5 w-3.5 text-emerald-600" /><strong className="block">{simulation.externalActionsExecuted}</strong><span className="text-muted-foreground">ações externas</span></div>
          {simulation.humanApprovalRequired && <div className="flex items-center gap-2 rounded-md border border-amber-500/35 bg-amber-500/8 p-2 text-[9px]"><UserRoundCheck className="h-3.5 w-3.5 text-amber-700" />Aprovação humana obrigatória</div>}
        </div>
      </div>
    </article>
  );
}

export function PlaybookSandboxPanel({ simulations, pending, onSimulate }: Props) {
  const [scenario, setScenario] = useState('Lead com negativa de medicamento de alto custo');
  const [customerMessage, setCustomerMessage] = useState('Meu plano negou o medicamento prescrito e preciso começar o tratamento nesta semana. Como vocês podem me ajudar?');
  return (
    <div className="grid min-h-[640px] xl:grid-cols-[360px_minmax(0,1fr)]">
      <section className="border-b p-4 md:p-5 xl:border-b-0 xl:border-r">
        <div className="flex items-center gap-2"><Beaker className="h-4 w-4 text-primary" /><div><h2 className="text-[14px] font-semibold">Sandbox local</h2><p className="text-[10px] text-muted-foreground">Simulação determinística sem envio ou integração externa.</p></div></div>
        <div className="mt-5 space-y-4">
          <div className="space-y-1.5"><Label className="text-[11px]">Cenário</Label><Input value={scenario} onChange={(event) => setScenario(event.target.value)} /></div>
          <div className="space-y-1.5"><Label className="text-[11px]">Mensagem do lead</Label><Textarea className="min-h-40 text-xs leading-5" value={customerMessage} onChange={(event) => setCustomerMessage(event.target.value)} /></div>
          <Button type="button" className="w-full bg-[#031121] text-white hover:bg-[#031121]" disabled={pending || !scenario.trim() || !customerMessage.trim()} onClick={() => onSimulate({ scenario: scenario.trim(), customerMessage: customerMessage.trim(), context: { channel: 'whatsapp', stage: 'triagem', localOnly: true } })}>{pending ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Bot className="mr-2 h-4 w-4" />}Executar simulação</Button>
        </div>
        <div className="mt-5 border-t pt-4 text-[9px] leading-4 text-muted-foreground"><p className="font-semibold text-foreground">Contrato de segurança</p><p className="mt-1">Toda resposta deve informar versão, modo, trilha de regras, fontes e número de ações externas.</p></div>
      </section>
      <section className="min-w-0 p-4 md:p-5">
        <div className="mb-4 flex items-center justify-between"><div><h3 className="text-[12px] font-semibold">Execuções recentes</h3><p className="text-[9px] text-muted-foreground">Mais recente primeiro</p></div><KlmPill tone="green">0 ações externas</KlmPill></div>
        {simulations.length === 0 ? <div className="flex min-h-80 flex-col items-center justify-center text-center"><Beaker className="h-7 w-7 text-primary" /><p className="mt-3 text-sm font-semibold">Nenhuma simulação ainda</p><p className="mt-1 text-xs text-muted-foreground">Execute o cenário ao lado para validar este rascunho.</p></div> : <div>{simulations.map((simulation) => <SimulationResult key={simulation.id} simulation={simulation} />)}</div>}
      </section>
    </div>
  );
}
