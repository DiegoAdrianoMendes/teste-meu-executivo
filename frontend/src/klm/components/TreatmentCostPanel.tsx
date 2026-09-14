import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Calculator, CheckCircle2, ExternalLink, Loader2, Pill, Search, ShieldCheck, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { klmApi, type KlmMedicationReference, type KlmTreatmentEstimate } from '@/klm/api';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { formatBRL } from '@/lib/formatters';

type TreatmentDraft = {
  medicationReferenceId: string;
  presentationsPerCycle: number;
  cycleCount: number;
  cycleIntervalDays: number | null;
  treatmentDurationDays: number | null;
  prescriptionReference: string;
  notes: string;
  reviewStatus: KlmTreatmentEstimate['reviewStatus'];
  applyToMatterEconomicValue: boolean;
};

const emptyDraft = (): TreatmentDraft => ({
  medicationReferenceId: '',
  presentationsPerCycle: 1,
  cycleCount: 1,
  cycleIntervalDays: null,
  treatmentDurationDays: null,
  prescriptionReference: '',
  notes: '',
  reviewStatus: 'rascunho',
  applyToMatterEconomicValue: false,
});

function fromEstimate(estimate: KlmTreatmentEstimate): TreatmentDraft {
  return {
    medicationReferenceId: estimate.medicationReferenceId,
    presentationsPerCycle: estimate.presentationsPerCycle,
    cycleCount: estimate.cycleCount,
    cycleIntervalDays: estimate.cycleIntervalDays,
    treatmentDurationDays: estimate.treatmentDurationDays,
    prescriptionReference: estimate.prescriptionReference ?? '',
    notes: estimate.notes ?? '',
    reviewStatus: estimate.reviewStatus,
    applyToMatterEconomicValue: estimate.appliedToMatterEconomicValue,
  };
}

function OptionalNumber({ label, value, suffix, onChange }: { label: string; value: number | null; suffix: string; onChange: (value: number | null) => void }) {
  return <div><Label className="text-2xs">{label}</Label><div className="relative mt-1"><Input aria-label={label} type="number" min={1} value={value ?? ''} onChange={(event) => onChange(event.target.value ? Math.max(1, Number(event.target.value)) : null)} className="h-9 pr-12 text-2xs" /><span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-2xs text-muted-foreground">{suffix}</span></div></div>;
}

export function TreatmentCostPanel({ matterId, initialProduct }: { matterId: string; initialProduct?: string | null }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [search, setSearch] = useState(initialProduct ?? '');
  const [oncologyOnly, setOncologyOnly] = useState(false);
  const [draft, setDraft] = useState<TreatmentDraft>(emptyDraft);
  const [selected, setSelected] = useState<KlmMedicationReference | null>(null);
  const currentMatterRef = useRef(matterId);
  const searchEditedRef = useRef(false);

  useEffect(() => {
    if (currentMatterRef.current !== matterId) {
      currentMatterRef.current = matterId;
      searchEditedRef.current = false;
      setSearch(initialProduct ?? '');
      setSelected(null);
      setDraft(emptyDraft());
      return;
    }
    if (!searchEditedRef.current && initialProduct) setSearch(initialProduct);
  }, [initialProduct, matterId]);

  const statusQuery = useQuery({ queryKey: ['klm', 'medication-catalog-status'], queryFn: klmApi.medicationCatalogStatus });
  const policyQuery = useQuery({ queryKey: ['klm', 'legal-pricing-policy'], queryFn: klmApi.legalPricingPolicy });
  const estimatesQuery = useQuery({ queryKey: ['klm', 'treatment-estimates', matterId], queryFn: () => klmApi.treatmentEstimates(matterId), enabled: Boolean(matterId) });
  const medicationQuery = useQuery({
    queryKey: ['klm', 'medications', search.trim(), oncologyOnly],
    queryFn: () => klmApi.medications(search, oncologyOnly ? true : undefined),
    enabled: search.trim().length >= 2,
  });

  useEffect(() => {
    const existing = estimatesQuery.data?.[0];
    if (!existing || selected) return;
    setSelected(existing.medication);
    setDraft(fromEstimate(existing));
  }, [estimatesQuery.data, selected]);

  const existingEstimate = estimatesQuery.data?.find((item) => item.medicationReferenceId === draft.medicationReferenceId) ?? null;
  const total = useMemo(() => selected ? selected.pmvgWithoutTax * draft.presentationsPerCycle * draft.cycleCount : 0, [draft.cycleCount, draft.presentationsPerCycle, selected]);
  const canSave = Boolean(selected && draft.presentationsPerCycle > 0 && draft.cycleCount > 0 && draft.prescriptionReference.trim());

  const saveMutation = useMutation({
    mutationFn: () => klmApi.upsertTreatmentEstimate(matterId, {
      medicationReferenceId: draft.medicationReferenceId,
      presentationsPerCycle: draft.presentationsPerCycle,
      cycleCount: draft.cycleCount,
      cycleIntervalDays: draft.cycleIntervalDays,
      treatmentDurationDays: draft.treatmentDurationDays,
      prescriptionReference: draft.prescriptionReference.trim(),
      notes: draft.notes.trim() || null,
      reviewStatus: draft.reviewStatus,
      applyToMatterEconomicValue: draft.applyToMatterEconomicValue,
    }),
    onSuccess: (saved) => {
      queryClient.setQueryData<KlmTreatmentEstimate[]>(['klm', 'treatment-estimates', matterId], (current = []) => [saved, ...current.filter((item) => item.id !== saved.id)]);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'matter', matterId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
      toast({ title: 'Custo do tratamento salvo no dossiê.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível salvar o cálculo', description: error.message }),
  });

  const deleteMutation = useMutation({
    mutationFn: (estimateId: string) => klmApi.deleteTreatmentEstimate(matterId, estimateId),
    onSuccess: () => {
      setSelected(null);
      setDraft(emptyDraft());
      void queryClient.invalidateQueries({ queryKey: ['klm', 'treatment-estimates', matterId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'matter', matterId] });
      toast({ title: 'Estimativa removida do caso.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível remover', description: error.message }),
  });

  const chooseMedication = (medication: KlmMedicationReference) => {
    const existing = estimatesQuery.data?.find((item) => item.medicationReferenceId === medication.id);
    setSelected(medication);
    setDraft(existing ? fromEstimate(existing) : { ...emptyDraft(), medicationReferenceId: medication.id });
  };

  return (
    <KlmPanel className="overflow-hidden" data-testid="treatment-cost-panel">
      <div className="border-b border-border p-4 sm:p-5">
        <KlmSectionTitle title="Medicamento, ciclo e custo do tratamento" description="Base CMED versionada. O sistema nunca infere dose, frascos, ciclos ou duração." action={<KlmPill tone={statusQuery.data?.recordCount ? 'green' : 'gold'}>{statusQuery.data?.recordCount ?? '—'} referências</KlmPill>} />
      </div>
      <div className="grid lg:grid-cols-[0.82fr_1.18fr]">
        <section className="border-b border-border p-4 sm:p-5 lg:border-b-0 lg:border-r">
          <div className="relative"><Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" /><Input aria-label="Buscar medicamento no catálogo CMED" value={search} onChange={(event) => { searchEditedRef.current = true; setSearch(event.target.value); }} placeholder="Medicamento, princípio ativo, GGREM ou Anvisa" className="h-9 pl-9 text-2xs" /></div>
          <div className="mt-3 flex items-center justify-between"><Label className="text-2xs">Somente oncologia</Label><Switch checked={oncologyOnly} onCheckedChange={setOncologyOnly} aria-label="Somente medicamentos oncológicos" /></div>
          <div className="mt-4 max-h-72 space-y-1.5 overflow-y-auto pr-1">
            {medicationQuery.isPending ? <p className="flex items-center gap-2 py-4 text-2xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Consultando o catálogo local</p> : null}
            {(medicationQuery.data ?? []).map((medication) => <button type="button" key={medication.id} onClick={() => chooseMedication(medication)} className={`w-full border-l-2 px-3 py-2 text-left transition-colors ${selected?.id === medication.id ? 'border-primary bg-primary/10' : 'border-border hover:bg-secondary'}`}><span className="flex items-center justify-between gap-2"><strong className="truncate text-2xs">{medication.productName}</strong>{medication.oncology ? <KlmPill tone="red">Oncologia</KlmPill> : null}</span><span className="mt-1 block truncate text-2xs text-muted-foreground">{medication.activeIngredient}</span><span className="mt-1 block text-2xs font-semibold tabular-nums text-[#0057d8] dark:text-primary">{formatBRL(medication.pmvgWithoutTax)} / apresentação</span></button>)}
            {search.trim().length < 2 ? <p className="py-5 text-center text-2xs text-muted-foreground">Digite ao menos dois caracteres.</p> : null}
            {medicationQuery.data?.length === 0 ? <p className="py-5 text-center text-2xs text-muted-foreground">Nenhuma apresentação encontrada.</p> : null}
          </div>
        </section>

        <section className="p-4 sm:p-5">
          {!selected ? <div className="flex min-h-64 flex-col items-center justify-center text-center"><Pill className="h-6 w-6 text-[#0057d8]" /><p className="mt-3 text-2xs font-semibold">Selecione a apresentação prescrita</p><p className="mt-1 max-w-sm text-2xs leading-4 text-muted-foreground">A apresentação precisa coincidir com a prescrição. O preço exibido não é o custo do tratamento.</p></div> : <>
            <div className="flex flex-col gap-3 border-b border-border pb-4 sm:flex-row sm:items-start sm:justify-between"><div className="min-w-0"><p className="text-sm font-semibold">{selected.productName}</p><p className="mt-1 text-2xs leading-4 text-muted-foreground">{selected.cmedPresentation}</p><p className="mt-2 text-2xs text-muted-foreground">GGREM {selected.ggremCode ?? 'não informado'} · {selected.datasetVersion}</p></div><a href={selected.sourceUrl} target="_blank" rel="noreferrer" className="inline-flex shrink-0 items-center gap-1 text-2xs font-semibold text-[#0057d8] hover:underline dark:text-primary">Fonte CMED<ExternalLink className="h-3 w-3" /></a></div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <div><Label className="text-2xs">Apresentações por ciclo</Label><Input aria-label="Apresentações por ciclo" type="number" min={0.0001} step={0.0001} value={draft.presentationsPerCycle} onChange={(event) => setDraft((current) => ({ ...current, presentationsPerCycle: Math.max(0.0001, Number(event.target.value)) }))} className="mt-1 h-9 text-2xs" /></div>
              <div><Label className="text-2xs">Número de ciclos</Label><Input aria-label="Número de ciclos" type="number" min={1} value={draft.cycleCount} onChange={(event) => setDraft((current) => ({ ...current, cycleCount: Math.max(1, Number(event.target.value)) }))} className="mt-1 h-9 text-2xs" /></div>
              <OptionalNumber label="Intervalo entre ciclos" value={draft.cycleIntervalDays} suffix="dias" onChange={(value) => setDraft((current) => ({ ...current, cycleIntervalDays: value }))} />
              <OptionalNumber label="Duração total prescrita" value={draft.treatmentDurationDays} suffix="dias" onChange={(value) => setDraft((current) => ({ ...current, treatmentDurationDays: value }))} />
              <div className="sm:col-span-2"><Label className="text-2xs">Referência da prescrição</Label><Input aria-label="Referência da prescrição" value={draft.prescriptionReference} onChange={(event) => setDraft((current) => ({ ...current, prescriptionReference: event.target.value }))} placeholder="Documento, data ou página que comprova os ciclos" className="mt-1 h-9 text-2xs" /></div>
              <div className="sm:col-span-2"><Label className="text-2xs">Notas internas</Label><Textarea aria-label="Notas internas do tratamento" value={draft.notes} onChange={(event) => setDraft((current) => ({ ...current, notes: event.target.value }))} className="mt-1 min-h-16 text-2xs" /></div>
              <div><Label className="text-2xs">Revisão</Label><Select value={draft.reviewStatus} onValueChange={(value: TreatmentDraft['reviewStatus']) => setDraft((current) => ({ ...current, reviewStatus: value }))}><SelectTrigger aria-label="Status da revisão do tratamento" className="mt-1 h-9 text-2xs"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="rascunho">Rascunho</SelectItem><SelectItem value="revisado">Revisado</SelectItem><SelectItem value="aprovado">Aprovado</SelectItem><SelectItem value="bloqueado">Bloqueado</SelectItem></SelectContent></Select></div>
              <div className="flex items-center justify-between gap-3 border-l-2 border-primary bg-secondary px-3 py-2"><div><p className="text-2xs font-semibold">Usar no valor econômico</p><p className="mt-1 text-2xs text-muted-foreground">Atualiza o card após salvar.</p></div><Switch checked={draft.applyToMatterEconomicValue} onCheckedChange={(checked) => setDraft((current) => ({ ...current, applyToMatterEconomicValue: checked }))} aria-label="Aplicar custo ao valor econômico do card" /></div>
            </div>
            <div className="mt-4 grid gap-3 border-y border-border py-4 sm:grid-cols-[1fr_auto]"><div><p className="text-2xs uppercase text-muted-foreground">Cálculo auditável</p><p className="mt-1 text-2xs">{formatBRL(selected.pmvgWithoutTax)} × {draft.presentationsPerCycle.toLocaleString('pt-BR')} × {draft.cycleCount.toLocaleString('pt-BR')}</p></div><div className="sm:text-right"><p className="text-2xs uppercase text-muted-foreground">Custo do tratamento</p><p className="mt-1 text-lg font-semibold tabular-nums">{formatBRL(total)}</p></div></div>
            {selected.oncology && policyQuery.data ? <div className="mt-4 flex items-start gap-2 bg-secondary px-3 py-2.5 text-2xs leading-4"><ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0b7435]" /><span>Parâmetro interno para honorários pela obrigação oncológica: {formatBRL(policyQuery.data.oncologyObligationFeeMinimum)} a {formatBRL(policyQuery.data.oncologyObligationFeeMaximum)}. Não é aplicado automaticamente.</span></div> : null}
            <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-between"><div>{existingEstimate ? <Button variant="ghost" size="sm" className="h-8 text-2xs text-[#a53e3c]" disabled={deleteMutation.isPending} onClick={() => deleteMutation.mutate(existingEstimate.id)}><Trash2 className="mr-1.5 h-3.5 w-3.5" />Remover cálculo</Button> : null}</div><Button className="h-9 bg-[#031121] text-2xs text-white hover:bg-[#031121]" disabled={!canSave || saveMutation.isPending} onClick={() => saveMutation.mutate()}>{saveMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : existingEstimate ? <CheckCircle2 className="mr-2 h-4 w-4" /> : <Calculator className="mr-2 h-4 w-4" />}{existingEstimate ? 'Atualizar cálculo' : 'Salvar no dossiê'}</Button></div>
          </>}
        </section>
      </div>
    </KlmPanel>
  );
}

export default TreatmentCostPanel;
