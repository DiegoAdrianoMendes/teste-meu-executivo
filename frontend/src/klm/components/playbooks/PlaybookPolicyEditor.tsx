import { Gauge, Hand, Power, ShieldCheck, UserRoundCheck } from 'lucide-react';

import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { KlmPill } from '@/klm/components/Primitives';
import { autonomyDescription, autonomyLabel } from '@/klm/playbooks/presentation';
import { cn } from '@/lib/utils';
import type { PlaybookAutonomyMode, PlaybookContent } from '@/services/playbooks';

interface Props {
  content: PlaybookContent;
  onChange: (content: PlaybookContent) => void;
}

const modes = Object.keys(autonomyLabel) as PlaybookAutonomyMode[];
const defaultStages = ['triagem', 'qualificacao', 'proposta', 'fechamento', 'pos_venda'];

function listText(values: string[]) {
  return values.join('\n');
}

function textList(value: string) {
  return value.split('\n').map((item) => item.trim()).filter(Boolean);
}

export function PlaybookPolicyEditor({ content, onChange }: Props) {
  const policy = content.policy;
  const update = <K extends keyof typeof policy>(key: K, value: (typeof policy)[K]) =>
    onChange({ ...content, policy: { ...policy, [key]: value } });
  const stages = [...new Set([...defaultStages, ...Object.keys(policy.stageModes)])];

  return (
    <div className="space-y-5 p-4 md:p-5">
      <div>
        <div className="flex items-center gap-2"><Gauge className="h-4 w-4 text-primary" /><h2 className="text-[14px] font-semibold">Modo de autonomia padrão</h2></div>
        <div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
          {modes.map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => update('autonomyMode', mode)}
              className={cn(
                'min-h-28 rounded-md border p-3 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                policy.autonomyMode === mode ? 'border-primary bg-primary/10 shadow-sm' : 'hover:border-foreground/25 hover:bg-secondary',
              )}
            >
              <span className="flex items-center justify-between gap-2">
                <strong className="text-[11px]">{autonomyLabel[mode]}</strong>
                {policy.autonomyMode === mode && <ShieldCheck className="h-4 w-4 text-[#0b7435]" />}
              </span>
              <span className="mt-2 block text-[9px] leading-4 text-muted-foreground">{autonomyDescription[mode]}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-5 border-t pt-5 xl:grid-cols-[1.15fr_.85fr]">
        <section>
          <div className="flex items-center justify-between gap-3">
            <div><h3 className="text-[12px] font-semibold">Autonomia por etapa</h3><p className="mt-0.5 text-[10px] text-muted-foreground">A regra mais específica prevalece sobre o modo padrão.</p></div>
            <KlmPill tone="blue">{stages.length} etapas</KlmPill>
          </div>
          <div className="mt-3 divide-y border-y">
            {stages.map((stage) => (
              <div key={stage} className="grid gap-2 py-3 sm:grid-cols-[minmax(120px,1fr)_minmax(230px,1.4fr)] sm:items-center">
                <div><p className="text-[11px] font-semibold capitalize">{stage.replaceAll('_', ' ')}</p><p className="text-[9px] text-muted-foreground">Política efetiva nesta etapa</p></div>
                <Select
                  value={policy.stageModes[stage] ?? policy.autonomyMode}
                  onValueChange={(value) => update('stageModes', { ...policy.stageModes, [stage]: value as PlaybookAutonomyMode })}
                >
                  <SelectTrigger className="h-9 text-[10px]"><SelectValue /></SelectTrigger>
                  <SelectContent>{modes.map((mode) => <SelectItem key={mode} value={mode}>{autonomyLabel[mode]}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            ))}
          </div>
        </section>

        <section className="space-y-4">
          <div className="flex items-center gap-2"><UserRoundCheck className="h-4 w-4 text-primary" /><h3 className="text-[12px] font-semibold">Gates humanos obrigatórios</h3></div>
          <Textarea
            value={listText(policy.hardGates)}
            onChange={(event) => update('hardGates', textList(event.target.value))}
            className="min-h-44 text-xs leading-5"
            aria-label="Gates humanos obrigatórios"
          />
          <p className="text-[9px] leading-4 text-muted-foreground">Um gate por linha. O runtime deve falhar fechado quando não conseguir avaliá-lo.</p>
        </section>
      </div>

      <div className="grid gap-4 border-t pt-5 md:grid-cols-2">
        <label className="flex min-h-24 items-center justify-between gap-4 rounded-md border px-4 py-3">
          <span><span className="flex items-center gap-2 text-[11px] font-semibold"><Power className="h-4 w-4 text-[#a53e3c]" />Kill switch</span><span className="mt-1 block text-[9px] leading-4 text-muted-foreground">Desabilita decisões autônomas sem apagar a configuração.</span></span>
          <Switch checked={policy.killSwitchEnabled} onCheckedChange={(checked) => update('killSwitchEnabled', checked)} />
        </label>
        <div className="min-h-24 rounded-md border px-4 py-3">
          <div className="flex items-center justify-between"><Label className="text-[11px]">Confiança mínima</Label><strong className="text-[11px]">{Math.round(policy.minimumConfidence * 100)}%</strong></div>
          <Slider className="mt-5" min={0.5} max={1} step={0.01} value={[policy.minimumConfidence]} onValueChange={([value]) => update('minimumConfidence', value)} />
        </div>
      </div>

      <div className="flex items-start gap-2 border-l-2 border-primary px-3 py-2 text-[10px] text-muted-foreground">
        <Hand className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
        <span>Reunião com advogado, concessão fora da política, envio jurídico definitivo, contrato fora da alçada e qualquer ação irreversível permanecem sujeitos aos gates humanos.</span>
      </div>
    </div>
  );
}
