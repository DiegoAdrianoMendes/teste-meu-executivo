import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Banknote, CheckCircle2, Loader2, Save, Scale } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { klmApi, type KlmLegalPricingPolicy } from '@/klm/api';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { formatBRL } from '@/lib/formatters';

function MoneyField({ label, value, nullable = false, onChange }: { label: string; value: number | null; nullable?: boolean; onChange: (value: number | null) => void }) {
  return <div><Label className="text-2xs">{label}</Label><div className="relative mt-1"><span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-2xs text-muted-foreground">R$</span><Input aria-label={label} type="number" min={0} value={value ?? ''} placeholder={nullable ? 'Sem teto' : '0,00'} onChange={(event) => onChange(event.target.value ? Math.max(0, Number(event.target.value)) : nullable ? null : 0)} className="h-9 pl-9 text-2xs" /></div></div>;
}

function PolicyToggle({ label, description, checked, onChange }: { label: string; description: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <div className="flex items-start justify-between gap-4 border-b border-border py-3 last:border-0"><div><p className="text-2xs font-semibold">{label}</p><p className="mt-1 text-2xs leading-4 text-muted-foreground">{description}</p></div><Switch checked={checked} onCheckedChange={onChange} aria-label={label} /></div>;
}

export function LegalPricingPolicyPanel() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const policyQuery = useQuery({ queryKey: ['klm', 'legal-pricing-policy'], queryFn: klmApi.legalPricingPolicy });
  const [draft, setDraft] = useState<KlmLegalPricingPolicy | null>(null);
  useEffect(() => { if (policyQuery.data) setDraft({ ...policyQuery.data }); }, [policyQuery.data]);

  const saveMutation = useMutation({
    mutationFn: (payload: KlmLegalPricingPolicy) => klmApi.updateLegalPricingPolicy(payload),
    onSuccess: (saved) => {
      setDraft({ ...saved });
      queryClient.setQueryData(['klm', 'legal-pricing-policy'], saved);
      toast({ title: 'Política comercial salva e auditada.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível salvar a política', description: error.message }),
  });

  if (policyQuery.isPending) return <KlmPanel className="flex min-h-40 items-center justify-center gap-2 p-5 text-2xs text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Carregando política comercial</KlmPanel>;
  if (policyQuery.isError) return <KlmPanel className="p-5"><p className="text-2xs font-semibold text-[#a53e3c]">Não foi possível carregar a política comercial.</p><Button variant="outline" size="sm" className="mt-3" onClick={() => void policyQuery.refetch()}>Tentar novamente</Button></KlmPanel>;
  if (!draft) return null;

  const update = <K extends keyof KlmLegalPricingPolicy>(key: K, value: KlmLegalPricingPolicy[K]) => setDraft((current) => current ? { ...current, [key]: value } : current);
  const rangeValid = draft.oncologyObligationFeeMaximum >= draft.oncologyObligationFeeMinimum && (draft.maximumInitialFee == null || draft.maximumInitialFee >= draft.minimumInitialFee);

  return <KlmPanel className="overflow-hidden" data-testid="legal-pricing-policy">
    <div className="border-b border-border p-4 sm:p-5"><KlmSectionTitle title="Política de honorários" description="Parâmetros internos usados pelas projeções e propostas, sempre sob aprovação humana." action={<KlmPill tone={rangeValid ? 'green' : 'red'}>{rangeValid ? 'Válida' : 'Revisar faixa'}</KlmPill>} /></div>
    <div className="grid lg:grid-cols-2">
      <section className="border-b border-border p-4 sm:p-5 lg:border-b-0 lg:border-r"><div className="mb-4 flex items-center gap-2"><Banknote className="h-4 w-4 text-[#0057d8]" /><h3 className="text-sm font-semibold">Entrada e êxito</h3></div><div className="grid gap-3 sm:grid-cols-2"><MoneyField label="Entrada mínima" value={draft.minimumInitialFee} onChange={(value) => update('minimumInitialFee', value ?? 0)} /><MoneyField label="Entrada máxima" value={draft.maximumInitialFee} nullable onChange={(value) => update('maximumInitialFee', value)} /><div><Label className="text-2xs">Dano moral no êxito integral</Label><div className="relative mt-1"><Input aria-label="Dano moral no êxito integral" type="number" min={0} max={100} value={draft.successOnlyMoralDamagesPercentage} onChange={(event) => update('successOnlyMoralDamagesPercentage', Math.max(0, Math.min(100, Number(event.target.value))))} className="h-9 pr-8 text-2xs" /><span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-2xs text-muted-foreground">%</span></div></div></div><div className="mt-3"><PolicyToggle label="Permitir contratação integralmente no êxito" description="Entrada zero; a base e o percentual precisam ficar explícitos na simulação e no contrato." checked={draft.allowSuccessOnly} onChange={(checked) => update('allowSuccessOnly', checked)} /><PolicyToggle label="Permitir honorários intermediários na liminar" description="A etapa intermediária fica separada da entrada, do êxito e da obrigação de fazer." checked={draft.allowInjunctionMilestoneFee} onChange={(checked) => update('allowInjunctionMilestoneFee', checked)} /></div></section>
      <section className="p-4 sm:p-5"><div className="mb-4 flex items-center gap-2"><Scale className="h-4 w-4 text-[#0b7435]" /><h3 className="text-sm font-semibold">Obrigação de fazer</h3></div><div className="grid gap-3 sm:grid-cols-2"><MoneyField label="Faixa oncológica mínima" value={draft.oncologyObligationFeeMinimum} onChange={(value) => update('oncologyObligationFeeMinimum', value ?? 0)} /><MoneyField label="Faixa oncológica máxima" value={draft.oncologyObligationFeeMaximum} onChange={(value) => update('oncologyObligationFeeMaximum', value ?? 0)} /></div><div className="mt-3"><PolicyToggle label="Exigir ciclo para valorar medicamento" description="O preço de uma apresentação CMED nunca será tratado como custo total sem ciclos e unidades informados." checked={draft.requireTreatmentCycleForMedicationValue} onChange={(checked) => update('requireTreatmentCycleForMedicationValue', checked)} /><PolicyToggle label="Exigir revisão humana" description="Nenhum valor econômico vira proposta ou contrato apenas por decisão automatizada." checked={draft.requireHumanReview} onChange={(checked) => update('requireHumanReview', checked)} /></div><div className="mt-3"><Label className="text-2xs">Nota da política</Label><Textarea aria-label="Nota da política de honorários" value={draft.policyNote} onChange={(event) => update('policyNote', event.target.value)} className="mt-1 min-h-20 text-2xs" /></div></section>
    </div>
    <div className="flex flex-col gap-3 border-t border-border bg-secondary/50 p-4 sm:flex-row sm:items-center sm:justify-between"><p className="flex items-start gap-2 text-2xs leading-4 text-muted-foreground"><CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0b7435]" />Entrada atual: {formatBRL(draft.minimumInitialFee)}{draft.maximumInitialFee == null ? ', sem teto' : ` a ${formatBRL(draft.maximumInitialFee)}`}. Faixa oncológica não é aplicada automaticamente.</p><Button disabled={!rangeValid || saveMutation.isPending} onClick={() => saveMutation.mutate(draft)}>{saveMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}Salvar política</Button></div>
  </KlmPanel>;
}

export default LegalPricingPolicyPanel;
