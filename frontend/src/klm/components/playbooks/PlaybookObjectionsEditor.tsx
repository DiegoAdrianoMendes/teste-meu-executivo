import { useMemo, useState } from 'react';
import { CircleHelp, Plus, ShieldAlert, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { KlmPill } from '@/klm/components/Primitives';
import { cn } from '@/lib/utils';
import type { PlaybookContent, PlaybookObjection } from '@/services/playbooks';

interface Props {
  content: PlaybookContent;
  onChange: (content: PlaybookContent) => void;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label className="text-[11px]">{label}</Label>
      {children}
    </div>
  );
}

export function PlaybookObjectionsEditor({ content, onChange }: Props) {
  const [selectedId, setSelectedId] = useState(content.objections[0]?.id ?? '');
  const selected = useMemo(
    () => content.objections.find((item) => item.id === selectedId) ?? content.objections[0],
    [content.objections, selectedId],
  );

  const update = (patch: Partial<PlaybookObjection>) => {
    if (!selected) return;
    onChange({
      ...content,
      objections: content.objections.map((item) =>
        item.id === selected.id ? { ...item, ...patch } : item,
      ),
    });
  };

  const add = () => {
    const objection: PlaybookObjection = {
      id: `objection-${crypto.randomUUID()}`,
      label: 'Nova objeção',
      diagnosis: '',
      confirmationQuestion: '',
      allowedResponse: '',
      proof: '',
      nextBestAction: '',
      maxAttempts: 2,
      escalationCriteria: 'Devolver ao Orquestrador quando houver risco jurídico ou dúvida não resolvida; pedido expresso por humano entra na fila do atendente e o dono é informado pelo Hermes.',
    };
    onChange({ ...content, objections: [...content.objections, objection] });
    setSelectedId(objection.id);
  };

  const remove = () => {
    if (!selected) return;
    const objections = content.objections.filter((item) => item.id !== selected.id);
    onChange({ ...content, objections });
    setSelectedId(objections[0]?.id ?? '');
  };

  return (
    <div className="grid min-h-[620px] lg:grid-cols-[260px_minmax(0,1fr)]">
      <aside className="border-b p-3 lg:border-b-0 lg:border-r">
        <div className="mb-3 flex items-center justify-between gap-2">
          <div>
            <p className="text-[10px] font-semibold uppercase text-muted-foreground">Biblioteca</p>
            <p className="mt-1 text-[9px] text-muted-foreground">{content.objections.length} objeções cobertas</p>
          </div>
          <Button type="button" size="icon" variant="outline" onClick={add} title="Adicionar objeção" aria-label="Adicionar objeção">
            <Plus className="h-4 w-4" />
          </Button>
        </div>
        <nav className="space-y-1" aria-label="Objeções comerciais">
          {content.objections.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setSelectedId(item.id)}
              className={cn(
                'flex w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left transition',
                selected?.id === item.id ? 'bg-[#031121] text-white' : 'hover:bg-secondary',
              )}
            >
              <span className="min-w-0">
                <strong className="block truncate text-[11px]">{item.label}</strong>
                <span className={cn('block truncate text-[9px]', selected?.id === item.id ? 'text-white/60' : 'text-muted-foreground')}>
                  {item.nextBestAction || 'Próxima ação não definida'}
                </span>
              </span>
              <KlmPill tone={selected?.id === item.id ? 'gold' : 'muted'}>{item.maxAttempts}x</KlmPill>
            </button>
          ))}
        </nav>
      </aside>

      <section className="min-w-0 p-4 md:p-5">
        {!selected ? (
          <div className="flex min-h-80 flex-col items-center justify-center text-center">
            <CircleHelp className="h-7 w-7 text-primary" />
            <p className="mt-3 text-sm font-semibold">Nenhuma objeção cadastrada</p>
            <Button type="button" className="mt-4" variant="outline" onClick={add}><Plus className="mr-2 h-4 w-4" />Adicionar</Button>
          </div>
        ) : (
          <div className="space-y-5">
            <div className="flex flex-col gap-3 border-b pb-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="flex min-w-0 items-start gap-3">
                <CircleHelp className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                <div className="min-w-0">
                  <h2 className="text-[14px] font-semibold">Roteiro de contorno</h2>
                  <p className="mt-0.5 text-[10px] text-muted-foreground">Diagnosticar antes de responder e registrar a evidência usada.</p>
                </div>
              </div>
              <Button type="button" size="sm" variant="ghost" className="text-destructive" onClick={remove}>
                <Trash2 className="mr-1.5 h-3.5 w-3.5" />Remover
              </Button>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <Field label="Nome da objeção"><Input value={selected.label} onChange={(event) => update({ label: event.target.value })} /></Field>
              <Field label="Máximo de tentativas"><Input type="number" min={1} max={5} value={selected.maxAttempts} onChange={(event) => update({ maxAttempts: Number(event.target.value) })} /></Field>
              <Field label="Diagnóstico"><Textarea className="min-h-24 text-xs" value={selected.diagnosis} onChange={(event) => update({ diagnosis: event.target.value })} /></Field>
              <Field label="Pergunta de confirmação"><Textarea className="min-h-24 text-xs" value={selected.confirmationQuestion} onChange={(event) => update({ confirmationQuestion: event.target.value })} /></Field>
              <div className="lg:col-span-2"><Field label="Resposta permitida"><Textarea className="min-h-28 text-xs" value={selected.allowedResponse} onChange={(event) => update({ allowedResponse: event.target.value })} /></Field></div>
              <Field label="Prova ou fundamento exigido"><Textarea className="min-h-24 text-xs" value={selected.proof} onChange={(event) => update({ proof: event.target.value })} /></Field>
              <Field label="Próxima melhor ação"><Textarea className="min-h-24 text-xs" value={selected.nextBestAction} onChange={(event) => update({ nextBestAction: event.target.value })} /></Field>
              <div className="lg:col-span-2">
                <Field label="Critério de escalonamento"><Textarea className="min-h-24 border-amber-500/40 bg-amber-500/5 text-xs" value={selected.escalationCriteria} onChange={(event) => update({ escalationCriteria: event.target.value })} /></Field>
              </div>
            </div>

            <div className="flex items-start gap-2 border-l-2 border-[#a53e3c] px-3 py-2 text-[10px] text-muted-foreground">
              <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#a53e3c]" />
              <span>Ao atingir o limite, o agente não improvisa: registra a objeção, interrompe a persuasão e aplica o handoff definido.</span>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
