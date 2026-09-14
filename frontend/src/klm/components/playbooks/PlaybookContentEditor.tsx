import { useMemo, useState } from 'react';
import {
  Bot,
  BriefcaseBusiness,
  CircleDollarSign,
  ClipboardList,
  FileCheck2,
  HeartPulse,
  MessageSquareText,
  Plus,
  RefreshCw,
  Trash2,
  UsersRound,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { KlmPill } from '@/klm/components/Primitives';
import type {
  PlaybookAgentContract,
  PlaybookContent,
  PlaybookDiscoveryQuestion,
} from '@/services/playbooks';
import { cn } from '@/lib/utils';

type ContentSection =
  | 'voice'
  | 'audience'
  | 'discovery'
  | 'triage'
  | 'qualification'
  | 'commercial'
  | 'followup'
  | 'agents';

const sections: Array<{
  key: ContentSection;
  label: string;
  description: string;
  icon: typeof MessageSquareText;
}> = [
  { key: 'voice', label: 'Identidade e voz', description: 'Tom, claims e exemplos', icon: MessageSquareText },
  { key: 'audience', label: 'Público e intenção', description: 'Dores, sinais e exclusões', icon: UsersRound },
  { key: 'discovery', label: 'Descoberta', description: 'Perguntas e confirmação', icon: ClipboardList },
  { key: 'triage', label: 'Triagem jurídica', description: 'Subtemas, documentos e Jurídico', icon: HeartPulse },
  { key: 'qualification', label: 'Qualificação', description: 'Score, evidência e próxima ação', icon: FileCheck2 },
  { key: 'commercial', label: 'Oferta e honorários', description: 'Valores, êxito e concessões', icon: CircleDollarSign },
  { key: 'followup', label: 'Follow-up e handoff', description: 'Cadências, paradas e Hermes', icon: RefreshCw },
  { key: 'agents', label: 'Contratos dos agentes', description: 'Objetivos, tools e fallback', icon: Bot },
];

function listText(values: string[]): string {
  return values.join('\n');
}

function textList(value: string): string[] {
  return value
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean);
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-[11px]">{label}</Label>
      {children}
      {hint && <p className="text-[9px] leading-4 text-muted-foreground">{hint}</p>}
    </div>
  );
}

function ListField({
  label,
  value,
  onChange,
  hint = 'Um item por linha.',
  rows = 5,
}: {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
  hint?: string;
  rows?: number;
}) {
  return (
    <Field label={label} hint={hint}>
      <Textarea
        value={listText(value)}
        onChange={(event) => onChange(textList(event.target.value))}
        rows={rows}
        className="resize-y text-xs leading-5"
      />
    </Field>
  );
}

function VoiceEditor({ content, onChange }: EditorProps) {
  const voice = content.identityVoice;
  const update = <K extends keyof typeof voice>(key: K, value: (typeof voice)[K]) =>
    onChange({ ...content, identityVoice: { ...voice, [key]: value } });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Field label="Posicionamento"><Textarea value={voice.positioning} onChange={(event) => update('positioning', event.target.value)} className="min-h-24 text-xs" /></Field>
      <Field label="Tom"><Textarea value={voice.tone} onChange={(event) => update('tone', event.target.value)} className="min-h-24 text-xs" /></Field>
      <ListField label="Vocabulário preferido" value={voice.vocabulary} onChange={(value) => update('vocabulary', value)} />
      <ListField label="Vocabulário proibido" value={voice.forbiddenVocabulary} onChange={(value) => update('forbiddenVocabulary', value)} />
      <ListField label="Claims permitidos" value={voice.allowedClaims} onChange={(value) => update('allowedClaims', value)} hint="Afirmações que podem ser usadas quando a evidência aplicável estiver disponível." />
      <ListField label="Claims proibidos" value={voice.forbiddenClaims} onChange={(value) => update('forbiddenClaims', value)} hint="Inclua garantias de resultado e afirmações não sustentadas." />
      <Field label="Saudação"><Input value={voice.greeting} onChange={(event) => update('greeting', event.target.value)} /></Field>
      <Field label="Despedida"><Input value={voice.closing} onChange={(event) => update('closing', event.target.value)} /></Field>
      <Field label="Tamanho máximo por mensagem"><Input type="number" min={80} max={4096} value={voice.maxMessageCharacters} onChange={(event) => update('maxMessageCharacters', Number(event.target.value))} /></Field>
      <Field label="Formalidade">
        <div className="grid grid-cols-3 gap-2">
          {(['conversational', 'balanced', 'formal'] as const).map((mode) => (
            <Button key={mode} type="button" variant={voice.formality === mode ? 'default' : 'outline'} className="h-9 text-[10px]" onClick={() => update('formality', mode)}>{mode === 'conversational' ? 'Conversacional' : mode === 'balanced' ? 'Equilibrada' : 'Formal'}</Button>
          ))}
        </div>
      </Field>
    </div>
  );
}

function AudienceEditor({ content, onChange }: EditorProps) {
  const audience = content.audienceIntent;
  const update = <K extends keyof typeof audience>(key: K, value: (typeof audience)[K]) =>
    onChange({ ...content, audienceIntent: { ...audience, [key]: value } });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <ListField label="Personas" value={audience.personas} onChange={(value) => update('personas', value)} />
      <ListField label="Dores percebidas" value={audience.pains} onChange={(value) => update('pains', value)} />
      <ListField label="Sinais de urgência" value={audience.urgencySignals} onChange={(value) => update('urgencySignals', value)} />
      <ListField label="Sinais de intenção" value={audience.intentSignals} onChange={(value) => update('intentSignals', value)} />
      <ListField label="Sinais de elegibilidade" value={audience.eligibilitySignals} onChange={(value) => update('eligibilitySignals', value)} />
      <ListField label="Sinais de exclusão" value={audience.exclusionSignals} onChange={(value) => update('exclusionSignals', value)} />
    </div>
  );
}

function DiscoveryEditor({ content, onChange }: EditorProps) {
  const questions = content.discovery;
  const updateQuestion = (id: string, patch: Partial<PlaybookDiscoveryQuestion>) => {
    onChange({ ...content, discovery: questions.map((question) => question.id === id ? { ...question, ...patch } : question) });
  };
  const addQuestion = () => {
    const id = `question-${crypto.randomUUID()}`;
    onChange({ ...content, discovery: [...questions, { id, prompt: '', required: true, condition: null, doNotRepeatWhen: 'A informação já estiver confirmada no Card ou na conversa.', confirmation: 'Confirme o entendimento antes de avançar.' }] });
  };
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between"><p className="text-[10px] text-muted-foreground">A ordem abaixo é a ordem de descoberta do agente.</p><Button type="button" size="sm" variant="outline" onClick={addQuestion}><Plus className="mr-1.5 h-3.5 w-3.5" />Pergunta</Button></div>
      {questions.map((question, index) => (
        <div key={question.id} className="grid gap-3 border-b py-4 first:pt-0 lg:grid-cols-[32px_minmax(0,1fr)_minmax(180px,.55fr)_auto]">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-secondary text-[10px] font-semibold">{index + 1}</span>
          <div className="space-y-3"><Field label="Pergunta"><Input value={question.prompt} onChange={(event) => updateQuestion(question.id, { prompt: event.target.value })} /></Field><Field label="Quando não repetir"><Input value={question.doNotRepeatWhen} onChange={(event) => updateQuestion(question.id, { doNotRepeatWhen: event.target.value })} /></Field></div>
          <div className="space-y-3"><Field label="Condição"><Input value={question.condition ?? ''} onChange={(event) => updateQuestion(question.id, { condition: event.target.value || null })} placeholder="Sempre" /></Field><Field label="Confirmação"><Input value={question.confirmation} onChange={(event) => updateQuestion(question.id, { confirmation: event.target.value })} /></Field></div>
          <div className="flex items-start gap-2 pt-6"><label className="flex items-center gap-2 text-[10px]"><Switch checked={question.required} onCheckedChange={(checked) => updateQuestion(question.id, { required: checked })} />Obrigatória</label><Button type="button" size="icon" variant="ghost" aria-label="Remover pergunta" onClick={() => onChange({ ...content, discovery: questions.filter((item) => item.id !== question.id) })}><Trash2 className="h-4 w-4" /></Button></div>
        </div>
      ))}
    </div>
  );
}

function TriageEditor({ content, onChange }: EditorProps) {
  const triage = content.legalTriage;
  const update = <K extends keyof typeof triage>(key: K, value: (typeof triage)[K]) =>
    onChange({ ...content, legalTriage: { ...triage, [key]: value } });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <ListField label="Subtemas" value={triage.subthemes} onChange={(value) => update('subthemes', value)} hint="Medicamento de alto custo, tratamento, Home Care, cirurgia, exame e demais subtemas." />
      <ListField label="Documentos obrigatórios" value={triage.requiredDocuments} onChange={(value) => update('requiredDocuments', value)} />
      <ListField label="Evidências da negativa" value={triage.denialEvidence} onChange={(value) => update('denialEvidence', value)} />
      <ListField label="Campos de medicamento e ciclo" value={triage.medicationFields} onChange={(value) => update('medicationFields', value)} />
      <ListField label="Gatilhos para consultar o Jurídico" value={triage.legalConsultTriggers} onChange={(value) => update('legalConsultTriggers', value)} />
      <ListField label="Regras de urgência" value={triage.urgencyRules} onChange={(value) => update('urgencyRules', value)} />
      <div className="lg:col-span-2"><Field label="Regra de suficiência documental"><Textarea value={triage.sufficiencyRule} onChange={(event) => update('sufficiencyRule', event.target.value)} className="min-h-24 text-xs" /></Field></div>
    </div>
  );
}

function QualificationEditor({ content, onChange }: EditorProps) {
  const qualification = content.qualification;
  const update = <K extends keyof typeof qualification>(key: K, value: (typeof qualification)[K]) =>
    onChange({ ...content, qualification: { ...qualification, [key]: value } });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Field label="Score mínimo para qualificação" hint="De 0 a 100, sempre acompanhado das evidências usadas."><Input type="number" min={0} max={100} value={qualification.scoreThreshold} onChange={(event) => update('scoreThreshold', Number(event.target.value))} /></Field>
      <ListField label="Campos obrigatórios" value={qualification.requiredFields} onChange={(value) => update('requiredFields', value)} />
      <ListField label="Regras de próxima ação" value={qualification.nextActionRules} onChange={(value) => update('nextActionRules', value)} />
      <ListField label="Motivos de desqualificação" value={qualification.disqualificationReasons} onChange={(value) => update('disqualificationReasons', value)} />
      <div className="lg:col-span-2 space-y-2"><Label className="text-[11px]">Regras de pontuação</Label>{qualification.scoreRules.map((rule, index) => <div key={`${rule.label}-${index}`} className="grid gap-2 md:grid-cols-[1fr_100px_1.4fr_auto]"><Input value={rule.label} onChange={(event) => update('scoreRules', qualification.scoreRules.map((item, itemIndex) => itemIndex === index ? { ...item, label: event.target.value } : item))} placeholder="Sinal" /><Input type="number" value={rule.points} onChange={(event) => update('scoreRules', qualification.scoreRules.map((item, itemIndex) => itemIndex === index ? { ...item, points: Number(event.target.value) } : item))} /><Input value={rule.evidence} onChange={(event) => update('scoreRules', qualification.scoreRules.map((item, itemIndex) => itemIndex === index ? { ...item, evidence: event.target.value } : item))} placeholder="Evidência exigida" /><Button type="button" size="icon" variant="ghost" aria-label="Remover regra" onClick={() => update('scoreRules', qualification.scoreRules.filter((_, itemIndex) => itemIndex !== index))}><Trash2 className="h-4 w-4" /></Button></div>)}<Button type="button" size="sm" variant="outline" onClick={() => update('scoreRules', [...qualification.scoreRules, { label: '', points: 0, evidence: '' }])}><Plus className="mr-1.5 h-3.5 w-3.5" />Regra</Button></div>
    </div>
  );
}

export function PlaybookCommercialEditor({ content, onChange }: EditorProps) {
  const commercial = content.commercial;
  const update = <K extends keyof typeof commercial>(key: K, value: (typeof commercial)[K]) =>
    onChange({ ...content, commercial: { ...commercial, [key]: value } });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Field label="Honorários iniciais mínimos (R$)"><Input type="number" min={4000} step={100} value={commercial.minimumInitialFeeBrl} onChange={(event) => update('minimumInitialFeeBrl', Number(event.target.value))} /></Field>
      <Field label="Honorários iniciais máximos (R$)" hint="Vazio significa sem teto."><Input type="number" min={0} step={100} value={commercial.maximumInitialFeeBrl ?? ''} onChange={(event) => update('maximumInitialFeeBrl', event.target.value ? Number(event.target.value) : null)} /></Field>
      <Field label="Aprovação humana acima de (R$)"><Input type="number" min={0} step={100} value={commercial.humanApprovalAboveBrl} onChange={(event) => update('humanApprovalAboveBrl', Number(event.target.value))} /></Field>
      <Field label="Dano moral destinado ao êxito"><div className="flex items-center gap-2"><Input type="number" min={0} max={100} value={commercial.successOnlyMoralDamagesPercentage} onChange={(event) => update('successOnlyMoralDamagesPercentage', Number(event.target.value))} /><span className="text-xs">%</span></div></Field>
      <Field label="Limite de desconto"><div className="flex items-center gap-2"><Input type="number" min={0} max={100} value={commercial.discountLimitPercent} onChange={(event) => update('discountLimitPercent', Number(event.target.value))} /><span className="text-xs">%</span></div></Field>
      <Field label="Validade da proposta"><div className="flex items-center gap-2"><Input type="number" min={1} max={90} value={commercial.proposalValidityDays} onChange={(event) => update('proposalValidityDays', Number(event.target.value))} /><span className="text-xs">dias</span></div></Field>
      <Field label="Obrigação de fazer oncológica mínima (R$)"><Input type="number" min={0} step={1000} value={commercial.oncologyObligationFeeMinimumBrl} onChange={(event) => update('oncologyObligationFeeMinimumBrl', Number(event.target.value))} /></Field>
      <Field label="Obrigação de fazer oncológica máxima (R$)"><Input type="number" min={0} step={1000} value={commercial.oncologyObligationFeeMaximumBrl} onChange={(event) => update('oncologyObligationFeeMaximumBrl', Number(event.target.value))} /></Field>
      <ListField label="Formas de pagamento" value={commercial.paymentOptions} onChange={(value) => update('paymentOptions', value)} rows={4} />
      <div className="lg:col-span-2 grid gap-3 md:grid-cols-2"><label className="flex items-center justify-between rounded-md border px-3 py-2 text-[11px]"><span><strong className="block">Tudo no êxito</strong><span className="text-[9px] text-muted-foreground">Permitido quando não houver entrada</span></span><Switch checked={commercial.successOnlyAllowed} onCheckedChange={(checked) => update('successOnlyAllowed', checked)} /></label><label className="flex items-center justify-between rounded-md border px-3 py-2 text-[11px]"><span><strong className="block">Honorário intermediário</strong><span className="text-[9px] text-muted-foreground">Cobrança na concessão da liminar</span></span><Switch checked={commercial.intermediateFeeAllowed} onCheckedChange={(checked) => update('intermediateFeeAllowed', checked)} /></label><label className="flex items-center justify-between rounded-md border px-3 py-2 text-[11px]"><span><strong className="block">Exigir ciclo de tratamento</strong><span className="text-[9px] text-muted-foreground">Não valorar medicamento por uma única apresentação</span></span><Switch checked={commercial.requireTreatmentCycleForMedicationValue} onCheckedChange={(checked) => update('requireTreatmentCycleForMedicationValue', checked)} /></label><label className="flex items-center justify-between rounded-md border px-3 py-2 text-[11px]"><span><strong className="block">Revisão humana obrigatória</strong><span className="text-[9px] text-muted-foreground">Nenhuma projeção vira proposta automaticamente</span></span><Switch checked={commercial.requireHumanReview} onCheckedChange={(checked) => update('requireHumanReview', checked)} /></label></div>
      <Field label="Regra para pagamento no êxito"><Textarea value={commercial.successOnlyRule} onChange={(event) => update('successOnlyRule', event.target.value)} className="min-h-24 text-xs" /></Field>
      <Field label="Obrigação de fazer e viés econômico"><Textarea value={commercial.obligationToDoFeeRule} onChange={(event) => update('obligationToDoFeeRule', event.target.value)} className="min-h-24 text-xs" /></Field>
      <div className="lg:col-span-2"><Field label="Nota interna da política"><Textarea value={commercial.policyNote} onChange={(event) => update('policyNote', event.target.value)} className="min-h-20 text-xs" /></Field></div>
    </div>
  );
}

function FollowUpEditor({ content, onChange }: EditorProps) {
  const followUp = content.followUp;
  const handoff = content.handoff;
  const updateFollowUp = <K extends keyof typeof followUp>(key: K, value: (typeof followUp)[K]) => onChange({ ...content, followUp: { ...followUp, [key]: value } });
  const updateHandoff = <K extends keyof typeof handoff>(key: K, value: (typeof handoff)[K]) => onChange({ ...content, handoff: { ...handoff, [key]: value } });
  return (
    <div className="space-y-6">
      <div className="grid gap-4 lg:grid-cols-2">
        <label className="flex items-center justify-between rounded-md border px-3 py-3 text-[11px]"><span><strong className="block">Lead nunca fica sem resposta</strong><span className="text-[9px] text-muted-foreground">Para por resposta, conversão, inelegibilidade, opt-out, bloqueio ou intervenção humana.</span></span><Switch checked={followUp.leadNeverUnanswered} onCheckedChange={(checked) => updateFollowUp('leadNeverUnanswered', checked)} /></label>
        <Field label="Horário permitido"><Input value={followUp.businessHours} onChange={(event) => updateFollowUp('businessHours', event.target.value)} /></Field>
        <Field label="Máximo de mensagens por dia"><Input type="number" min={1} max={20} value={followUp.maxMessagesPerDay} onChange={(event) => updateFollowUp('maxMessagesPerDay', Number(event.target.value))} /></Field>
        <Field label="Janela anti-spam (minutos)"><Input type="number" min={1} value={followUp.antiSpamWindowMinutes} onChange={(event) => updateFollowUp('antiSpamWindowMinutes', Number(event.target.value))} /></Field>
      </div>
      <div className="border-t pt-5"><h3 className="text-[12px] font-semibold">Handoff operacional</h3><p className="mt-1 text-[10px] text-muted-foreground">O Orquestrador coordena os handoffs e coloca pedidos humanos na fila do atendente. Hermes apenas informa o dono, sem assumir a conversa.</p><div className="mt-4 grid gap-4 lg:grid-cols-2"><Field label="Fila de fallback"><Input value={handoff.fallbackQueue} onChange={(event) => updateHandoff('fallbackQueue', event.target.value)} /></Field><Field label="Dispatcher normal"><Input value="Orquestrador" disabled /></Field><ListField label="Campos obrigatórios do resumo" value={handoff.requiredSummaryFields} onChange={(value) => updateHandoff('requiredSummaryFields', value)} /></div></div>
    </div>
  );
}

function AgentsEditor({ content, onChange }: EditorProps) {
  const [selectedKey, setSelectedKey] = useState(content.agents[0]?.key ?? '');
  const selected = useMemo(() => content.agents.find((agent) => agent.key === selectedKey) ?? content.agents[0], [content.agents, selectedKey]);
  const updateAgent = (patch: Partial<PlaybookAgentContract>) => {
    if (!selected) return;
    onChange({ ...content, agents: content.agents.map((agent) => agent.key === selected.key ? { ...agent, ...patch } : agent) });
  };
  if (!selected) return <p className="text-xs text-muted-foreground">Nenhum contrato de agente configurado.</p>;
  return (
    <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
      <div className="space-y-1 border-r pr-4">{content.agents.map((agent) => <button key={agent.key} type="button" onClick={() => setSelectedKey(agent.key)} className={cn('flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-[11px]', selected.key === agent.key ? 'bg-[#031121] text-white' : 'hover:bg-secondary')}><span><strong className="block">{agent.label}</strong><span className={cn('text-[9px]', selected.key === agent.key ? 'text-white/60' : 'text-muted-foreground')}>{agent.objective.slice(0, 44)}</span></span><KlmPill tone={selected.key === agent.key ? 'gold' : 'muted'}>{agent.slaMinutes} min</KlmPill></button>)}</div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="lg:col-span-2"><Field label="Objetivo"><Textarea value={selected.objective} onChange={(event) => updateAgent({ objective: event.target.value })} className="min-h-20 text-xs" /></Field></div>
        <Field label="Hierarquia de autoridade"><Input value={selected.independentControl ? 'Controle independente · iniciado por humano ou sistema' : selected.parentAgentKey ? `Reporta a ${selected.parentAgentKey}` : 'Raiz de coordenação'} disabled /></Field>
        <Field label="Delegação direta"><Input value={selected.handoffs.length ? selected.handoffs.join(', ') : 'Nenhuma delegação direta'} disabled /></Field>
        <ListField label="Entradas autorizadas" value={selected.inputs} onChange={(value) => updateAgent({ inputs: value })} />
        <ListField label="Ferramentas" value={selected.tools} onChange={(value) => updateAgent({ tools: value })} />
        <ListField label="Permissões" value={selected.permissions} onChange={(value) => updateAgent({ permissions: value })} />
        <ListField label="Proibições" value={selected.prohibitions} onChange={(value) => updateAgent({ prohibitions: value })} />
        <ListField label="Critérios de sucesso" value={selected.successCriteria} onChange={(value) => updateAgent({ successCriteria: value })} />
        <Field label="Prazo de resposta (minutos)"><Input type="number" min={1} value={selected.slaMinutes} onChange={(event) => updateAgent({ slaMinutes: Number(event.target.value) })} /></Field>
        <Field label="Fallback"><Input value={selected.fallback} onChange={(event) => updateAgent({ fallback: event.target.value })} /></Field>
      </div>
    </div>
  );
}

interface EditorProps {
  content: PlaybookContent;
  onChange: (content: PlaybookContent) => void;
}

export function PlaybookContentEditor({ content, onChange }: EditorProps) {
  const [section, setSection] = useState<ContentSection>('voice');
  return (
    <div className="grid min-h-[620px] gap-0 lg:grid-cols-[220px_minmax(0,1fr)]">
      <aside className="border-b p-3 lg:border-b-0 lg:border-r">
        <p className="px-2 pb-2 text-[9px] font-semibold uppercase text-muted-foreground">Contrato do playbook</p>
        <nav className="grid grid-cols-2 gap-1 sm:grid-cols-4 lg:grid-cols-1" aria-label="Blocos do playbook">
          {sections.map((item) => {
            const Icon = item.icon;
            return <button key={item.key} type="button" onClick={() => setSection(item.key)} className={cn('flex min-w-0 items-start gap-2 rounded-md px-2.5 py-2 text-left transition', section === item.key ? 'bg-[#031121] text-white' : 'hover:bg-secondary')}><Icon className={cn('mt-0.5 h-3.5 w-3.5 shrink-0', section === item.key ? 'text-primary' : 'text-muted-foreground')} /><span className="min-w-0"><strong className="block truncate text-[10px]">{item.label}</strong><span className={cn('mt-0.5 hidden truncate text-[8px] lg:block', section === item.key ? 'text-white/55' : 'text-muted-foreground')}>{item.description}</span></span></button>;
          })}
        </nav>
      </aside>
      <section className="min-w-0 p-4 md:p-5">
        <div className="mb-5 flex items-start gap-3"><BriefcaseBusiness className="mt-0.5 h-4 w-4 text-primary" /><div><h2 className="text-[14px] font-semibold">{sections.find((item) => item.key === section)?.label}</h2><p className="mt-0.5 text-[10px] text-muted-foreground">{sections.find((item) => item.key === section)?.description}</p></div></div>
        {section === 'voice' && <VoiceEditor content={content} onChange={onChange} />}
        {section === 'audience' && <AudienceEditor content={content} onChange={onChange} />}
        {section === 'discovery' && <DiscoveryEditor content={content} onChange={onChange} />}
        {section === 'triage' && <TriageEditor content={content} onChange={onChange} />}
        {section === 'qualification' && <QualificationEditor content={content} onChange={onChange} />}
        {section === 'commercial' && <PlaybookCommercialEditor content={content} onChange={onChange} />}
        {section === 'followup' && <FollowUpEditor content={content} onChange={onChange} />}
        {section === 'agents' && <AgentsEditor content={content} onChange={onChange} />}
      </section>
    </div>
  );
}
