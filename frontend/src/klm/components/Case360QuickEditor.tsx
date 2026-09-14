import { useEffect, useMemo, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Check, PencilLine, Plus, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { klmApi, type KlmMatterDetail } from '@/klm/api';
import { useAppStore } from '@/store/app-context';

const SOURCES = [
  ['google_ads', 'Google Ads'],
  ['meta_ads', 'Meta Ads'],
  ['instagram', 'Instagram'],
  ['tiktok_ads', 'TikTok Ads'],
  ['indicacao', 'Indicação'],
  ['organico', 'Orgânico'],
  ['whatsapp', 'WhatsApp direto'],
] as const;

const DEMANDS = [
  ['negativa_plano_saude', 'Negativa de plano de saúde'],
  ['cobertura_medicamento', 'Cobertura de medicamento'],
  ['home_care', 'Home care'],
  ['cirurgia', 'Cirurgia'],
  ['exame', 'Exame'],
  ['tratamento', 'Tratamento'],
] as const;

const SUBTHEMES = [
  ['medicamento_alto_custo', 'Medicamento de alto custo'],
  ['tratamento', 'Tratamento'],
  ['home_care', 'Home care'],
  ['cirurgia', 'Cirurgia'],
  ['exame', 'Exame'],
  ['opme', 'OPME'],
  ['terapia', 'Terapia'],
] as const;

type EditorForm = {
  name: string;
  phone: string;
  email: string;
  sourceMode: string;
  sourceCustom: string;
  demandMode: string;
  demandCustom: string;
  subthemeMode: string;
  subthemeCustom: string;
  objectName: string;
  insurer: string;
  denialReason: string;
  urgencyHours: string;
  priority: string;
  urgency: string;
  classification: string;
  leadScore: string;
  nextAction: string;
  tags: string[];
};

function optionMode(value: string | null | undefined, options: ReadonlyArray<readonly [string, string]>) {
  return value && options.some(([candidate]) => candidate === value) ? value : 'custom';
}

function formFromMatter(matter: KlmMatterDetail): EditorForm {
  const effectiveSubtheme = matter.healthSubthemeCustom || matter.healthSubtheme;
  return {
    name: matter.client.name,
    phone: matter.client.phone ?? '',
    email: matter.client.email ?? '',
    sourceMode: optionMode(matter.leadSource, SOURCES),
    sourceCustom: optionMode(matter.leadSource, SOURCES) === 'custom' ? matter.leadSource ?? '' : '',
    demandMode: optionMode(matter.demandType, DEMANDS),
    demandCustom: optionMode(matter.demandType, DEMANDS) === 'custom' ? matter.demandType : '',
    subthemeMode: matter.healthSubthemeCustom ? 'custom' : optionMode(matter.healthSubtheme, SUBTHEMES),
    subthemeCustom: effectiveSubtheme && (matter.healthSubthemeCustom || optionMode(matter.healthSubtheme, SUBTHEMES) === 'custom') ? effectiveSubtheme : '',
    objectName: matter.healthObjectName ?? '',
    insurer: matter.healthInsurer ?? '',
    denialReason: matter.healthDenialReason ?? '',
    urgencyHours: matter.medicalUrgencyHours == null ? '' : String(matter.medicalUrgencyHours),
    priority: matter.priority ?? 'media',
    urgency: matter.urgency ?? 'normal',
    classification: matter.classification ?? 'Bom',
    leadScore: String(matter.leadScore),
    nextAction: matter.nextAction ?? '',
    tags: matter.tags ?? [],
  };
}

function OptionalCustomSelect({
  id,
  label,
  value,
  customValue,
  options,
  onValue,
  onCustom,
}: {
  id: string;
  label: string;
  value: string;
  customValue: string;
  options: ReadonlyArray<readonly [string, string]>;
  onValue: (value: string) => void;
  onCustom: (value: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Select value={value} onValueChange={onValue}>
        <SelectTrigger id={id}><SelectValue /></SelectTrigger>
        <SelectContent>
          {options.map(([optionValue, optionLabel]) => <SelectItem key={optionValue} value={optionValue}>{optionLabel}</SelectItem>)}
          <SelectItem value="custom">Personalizado</SelectItem>
        </SelectContent>
      </Select>
      {value === 'custom' && <Input value={customValue} onChange={(event) => onCustom(event.target.value)} placeholder={`Informe ${label.toLocaleLowerCase('pt-BR')}`} maxLength={300} />}
    </div>
  );
}

export function Case360QuickEditor({
  matter,
  onSaved,
}: {
  matter: KlmMatterDetail;
  onSaved: (matter: KlmMatterDetail) => void;
}) {
  const { can } = useAppStore();
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<EditorForm>(() => formFromMatter(matter));
  const [newTag, setNewTag] = useState('');
  const canEdit = can('kanban', 'edit') && can('lead_data', 'edit') && !matter.client.sensitiveDataHidden;

  useEffect(() => setForm(formFromMatter(matter)), [matter]);

  const resolved = useMemo(() => ({
    leadSource: form.sourceMode === 'custom' ? form.sourceCustom.trim() : form.sourceMode,
    demandType: form.demandMode === 'custom' ? form.demandCustom.trim() : form.demandMode,
    healthSubtheme: form.subthemeMode === 'custom' ? 'outro' : form.subthemeMode,
    healthSubthemeCustom: form.subthemeMode === 'custom' ? form.subthemeCustom.trim() : null,
  }), [form.demandCustom, form.demandMode, form.sourceCustom, form.sourceMode, form.subthemeCustom, form.subthemeMode]);

  const saveMutation = useMutation({
    mutationFn: () => klmApi.updateCase360(matter.id, {
      client: {
        name: form.name.trim(),
        phone: form.phone.trim() || null,
        email: form.email.trim() || null,
      },
      matter: {
        demandType: resolved.demandType,
        leadSource: resolved.leadSource || null,
        tags: form.tags,
        healthSubtheme: resolved.healthSubtheme,
        healthSubthemeCustom: resolved.healthSubthemeCustom,
        healthObjectName: form.objectName.trim() || null,
        healthInsurer: form.insurer.trim() || null,
        healthDenialReason: form.denialReason.trim() || null,
        medicalUrgencyHours: form.urgencyHours ? Number(form.urgencyHours) : null,
        priority: form.priority,
        urgency: form.urgency,
        classification: form.classification,
        leadScore: Math.max(0, Math.min(100, Number(form.leadScore) || 0)),
        nextAction: form.nextAction.trim() || null,
      },
    }),
    onSuccess: (updated) => {
      onSaved(updated);
      setOpen(false);
      toast({
        title: 'Caso 360 atualizado',
        description: `Revisão manual ${updated.manualContextRevision} registrada para os agentes.`,
      });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Não foi possível salvar o Caso 360',
      description: error instanceof Error ? error.message : 'Revise os campos e tente novamente.',
    }),
  });

  const addTag = () => {
    const tag = newTag.trim().slice(0, 60);
    if (!tag || form.tags.some((item) => item.toLocaleLowerCase('pt-BR') === tag.toLocaleLowerCase('pt-BR'))) return;
    setForm((current) => ({ ...current, tags: [...current.tags, tag].slice(0, 30) }));
    setNewTag('');
  };

  const valid = Boolean(form.name.trim() && resolved.demandType && (form.subthemeMode !== 'custom' || resolved.healthSubthemeCustom));

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button type="button" variant="outline" size="sm" className="h-7 gap-1 px-2 text-2xs" disabled={!canEdit} title={canEdit ? 'Editar dados sem sair da conversa' : 'Seu perfil não pode editar dados protegidos'}>
          <PencilLine className="h-3 w-3" />Editar
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[92vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Editar Caso 360</DialogTitle>
          <DialogDescription>Alterações humanas são auditadas e passam a prevalecer no contexto dos agentes.</DialogDescription>
        </DialogHeader>

        <section className="space-y-3 border-b border-border pb-5">
          <h3 className="text-sm font-semibold">Cliente</h3>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1.5"><Label htmlFor="case360-name">Nome</Label><Input id="case360-name" value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} /></div>
            <div className="space-y-1.5"><Label htmlFor="case360-phone">WhatsApp</Label><Input id="case360-phone" value={form.phone} onChange={(event) => setForm((current) => ({ ...current, phone: event.target.value }))} /></div>
            <div className="space-y-1.5"><Label htmlFor="case360-email">E-mail</Label><Input id="case360-email" type="email" value={form.email} onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))} /></div>
          </div>
        </section>

        <section className="space-y-3 border-b border-border pb-5">
          <h3 className="text-sm font-semibold">Qualificação e origem</h3>
          <div className="grid gap-3 sm:grid-cols-3">
            <OptionalCustomSelect id="case360-source" label="Origem" value={form.sourceMode} customValue={form.sourceCustom} options={SOURCES} onValue={(sourceMode) => setForm((current) => ({ ...current, sourceMode }))} onCustom={(sourceCustom) => setForm((current) => ({ ...current, sourceCustom }))} />
            <OptionalCustomSelect id="case360-demand" label="Demanda" value={form.demandMode} customValue={form.demandCustom} options={DEMANDS} onValue={(demandMode) => setForm((current) => ({ ...current, demandMode }))} onCustom={(demandCustom) => setForm((current) => ({ ...current, demandCustom }))} />
            <OptionalCustomSelect id="case360-subtheme" label="Subtema" value={form.subthemeMode} customValue={form.subthemeCustom} options={SUBTHEMES} onValue={(subthemeMode) => setForm((current) => ({ ...current, subthemeMode }))} onCustom={(subthemeCustom) => setForm((current) => ({ ...current, subthemeCustom }))} />
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1.5"><Label>Prioridade</Label><Select value={form.priority} onValueChange={(priority) => setForm((current) => ({ ...current, priority }))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="alta">Alta</SelectItem><SelectItem value="media">Média</SelectItem><SelectItem value="baixa">Baixa</SelectItem></SelectContent></Select></div>
            <div className="space-y-1.5"><Label>Urgência</Label><Select value={form.urgency} onValueChange={(urgency) => setForm((current) => ({ ...current, urgency }))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="urgente">Urgente</SelectItem><SelectItem value="normal">Normal</SelectItem><SelectItem value="baixa">Baixa</SelectItem></SelectContent></Select></div>
            <div className="space-y-1.5"><Label>Classificação</Label><Select value={form.classification} onValueChange={(classification) => setForm((current) => ({ ...current, classification }))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="Premium">Premium</SelectItem><SelectItem value="Forte">Forte</SelectItem><SelectItem value="Bom">Bom</SelectItem><SelectItem value="Frio">Frio</SelectItem></SelectContent></Select></div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="case360-new-tag">Tags</Label>
            <div className="flex gap-2"><Input id="case360-new-tag" value={newTag} onChange={(event) => setNewTag(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); addTag(); } }} placeholder="Digite e pressione Enter" /><Button type="button" variant="outline" size="icon" onClick={addTag} aria-label="Adicionar tag"><Plus className="h-4 w-4" /></Button></div>
            <div className="flex min-h-8 flex-wrap gap-1.5">{form.tags.map((tag) => <span key={tag} className="inline-flex items-center gap-1 rounded-full border border-primary/35 bg-primary/10 px-2 py-1 text-2xs font-medium">{tag}<button type="button" aria-label={`Remover tag ${tag}`} onClick={() => setForm((current) => ({ ...current, tags: current.tags.filter((item) => item !== tag) }))}><X className="h-3 w-3" /></button></span>)}</div>
          </div>
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold">Caso e estratégia</h3>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1.5"><Label htmlFor="case360-object">Medicamento / procedimento</Label><Input id="case360-object" value={form.objectName} onChange={(event) => setForm((current) => ({ ...current, objectName: event.target.value }))} /></div>
            <div className="space-y-1.5"><Label htmlFor="case360-insurer">Operadora</Label><Input id="case360-insurer" value={form.insurer} onChange={(event) => setForm((current) => ({ ...current, insurer: event.target.value }))} /></div>
            <div className="space-y-1.5"><Label htmlFor="case360-hours">Urgência médica (horas)</Label><Input id="case360-hours" type="number" min={1} max={8760} value={form.urgencyHours} onChange={(event) => setForm((current) => ({ ...current, urgencyHours: event.target.value }))} /></div>
          </div>
          <div className="space-y-1.5"><Label htmlFor="case360-denial">Motivo da negativa</Label><Textarea id="case360-denial" value={form.denialReason} onChange={(event) => setForm((current) => ({ ...current, denialReason: event.target.value }))} rows={3} /></div>
          <div className="grid gap-3 sm:grid-cols-[120px_1fr]">
            <div className="space-y-1.5"><Label htmlFor="case360-score">Nota</Label><Input id="case360-score" type="number" min={0} max={100} value={form.leadScore} onChange={(event) => setForm((current) => ({ ...current, leadScore: event.target.value }))} /></div>
            <div className="space-y-1.5"><Label htmlFor="case360-next">Próxima ação</Label><Input id="case360-next" value={form.nextAction} onChange={(event) => setForm((current) => ({ ...current, nextAction: event.target.value }))} /></div>
          </div>
        </section>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>Cancelar</Button>
          <Button type="button" disabled={!valid || saveMutation.isPending} onClick={() => saveMutation.mutate()}><Check className="mr-2 h-4 w-4" />{saveMutation.isPending ? 'Salvando...' : 'Salvar e avisar agentes'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
