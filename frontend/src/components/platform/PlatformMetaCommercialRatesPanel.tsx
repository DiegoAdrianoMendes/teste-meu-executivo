import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Loader2, Megaphone, RefreshCw } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { useToast } from '@/hooks/use-toast';
import { fetchPlatformMetaCommercialRateCards, savePlatformMetaCommercialRateCard } from '@/services/platform';
import type { MetaCommercialRateCard, MetaCommercialRateCardUpsert } from '@/types';

const META_PRICING_SOURCE = 'https://developers.facebook.com/docs/whatsapp/pricing';

function localDateTimeInput(date = new Date()): string {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}

const emptyDraft = () => ({
  rateCardKey: '',
  category: 'marketing',
  countryCode: 'BR',
  currency: 'BRL',
  commercialRate: '',
  effectiveFrom: localDateTimeInput(),
  effectiveTo: '',
  checkedAt: localDateTimeInput(),
  isActive: true,
});

function toDraft(item: MetaCommercialRateCard) {
  return {
    rateCardKey: item.rateCardKey,
    category: item.category,
    countryCode: item.countryCode,
    currency: item.currency,
    commercialRate: item.commercialRate,
    effectiveFrom: localDateTimeInput(new Date(item.effectiveFrom)),
    effectiveTo: item.effectiveTo ? localDateTimeInput(new Date(item.effectiveTo)) : '',
    checkedAt: localDateTimeInput(new Date(item.checkedAt)),
    isActive: item.isActive,
  };
}

function formatRate(value: string, currency: string): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return `${currency} —`;
  return `${currency} ${parsed.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 8 })}`;
}

export function PlatformMetaCommercialRatesPanel() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(emptyDraft);
  const ratesQuery = useQuery({
    queryKey: ['platform', 'meta-commercial-rate-cards'],
    queryFn: () => fetchPlatformMetaCommercialRateCards(true),
    staleTime: 30_000,
  });
  const saveMutation = useMutation({
    mutationFn: ({ rateCardKey, payload }: { rateCardKey: string; payload: MetaCommercialRateCardUpsert }) => savePlatformMetaCommercialRateCard(rateCardKey, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'meta-commercial-rate-cards'] });
      void queryClient.invalidateQueries({ queryKey: ['whatsapp-commercial-charges'] });
      setDraft(emptyDraft());
      toast({ title: 'Versão da tarifa comercial salva.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Tarifa não salva', description: error.message }),
  });

  const parsedRate = Number(draft.commercialRate);
  const canSave = draft.rateCardKey.trim().length >= 3
    && draft.category.trim().length > 0
    && (draft.countryCode.trim() === '*' || /^[A-Za-z]{2}$/.test(draft.countryCode.trim()))
    && /^[A-Za-z]{3}$/.test(draft.currency.trim())
    && draft.commercialRate.trim().length > 0
    && Number.isFinite(parsedRate)
    && parsedRate >= 0
    && Boolean(draft.effectiveFrom)
    && Boolean(draft.checkedAt);

  const save = () => {
    if (!canSave) return;
    saveMutation.mutate({
      rateCardKey: draft.rateCardKey.trim().toLowerCase(),
      payload: {
        category: draft.category.trim().toLowerCase(),
        countryCode: draft.countryCode.trim().toUpperCase(),
        currency: draft.currency.trim().toUpperCase(),
        commercialRate: draft.commercialRate.trim(),
        effectiveFrom: new Date(draft.effectiveFrom).toISOString(),
        effectiveTo: draft.effectiveTo ? new Date(draft.effectiveTo).toISOString() : null,
        sourceUrl: META_PRICING_SOURCE,
        checkedAt: new Date(draft.checkedAt).toISOString(),
        isActive: draft.isActive,
      },
    });
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[1.05fr,0.95fr]">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base"><Megaphone className="h-4 w-4 text-primary" />Tarifas comerciais da API oficial</CardTitle>
          <CardDescription>Cada versão define o valor integral cobrado do cliente por mensagem entregue. Nenhuma tarifa é inferida ou inserida automaticamente.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {ratesQuery.isLoading && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Carregando histórico versionado</p>}
          {ratesQuery.isError && <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 p-3 text-sm"><span>Não foi possível carregar as tarifas.</span><Button type="button" size="sm" variant="outline" onClick={() => { void ratesQuery.refetch(); }}><RefreshCw className="mr-2 h-3.5 w-3.5" />Tentar novamente</Button></div>}
          {(ratesQuery.data ?? []).map((item) => (
            <div key={item.id} className="rounded-lg border p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2"><p className="text-sm font-semibold">{item.rateCardKey}</p><Badge variant="outline">{item.category}</Badge><Badge variant="outline">{item.countryCode}</Badge></div>
                  <p className="mt-1 text-sm tabular-nums">{formatRate(item.commercialRate, item.currency)} por entrega</p>
                  <p className="mt-1 text-xs text-muted-foreground">Vigência: {new Date(item.effectiveFrom).toLocaleString('pt-BR')} até {item.effectiveTo ? new Date(item.effectiveTo).toLocaleString('pt-BR') : 'sem término'}</p>
                </div>
                <div className="flex items-center gap-2"><Badge variant={item.isActive ? 'secondary' : 'outline'}>{item.isActive ? 'Ativa' : 'Inativa'}</Badge><Button type="button" size="sm" variant="outline" onClick={() => setDraft(toDraft(item))}>Editar</Button></div>
              </div>
              <p className="mt-2 text-2xs text-muted-foreground">Fonte conferida em {new Date(item.checkedAt).toLocaleString('pt-BR')}.</p>
            </div>
          ))}
          {!ratesQuery.isLoading && !ratesQuery.isError && !(ratesQuery.data ?? []).length && <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">Nenhuma tarifa cadastrada. Os workspaces verão “tarifa pendente” e nenhum valor será inventado.</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Cadastrar versão</CardTitle><CardDescription>Use a categoria, o país e a moeda publicados pela Meta. Vigências sobrepostas são bloqueadas.</CardDescription></CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2"><Label htmlFor="meta-rate-key">Chave da versão</Label><Input id="meta-rate-key" value={draft.rateCardKey} onChange={(event) => setDraft((current) => ({ ...current, rateCardKey: event.target.value }))} placeholder="meta-marketing-br-brl-2026-07" /></div>
          <div className="space-y-1.5"><Label htmlFor="meta-rate-category">Categoria</Label><Input id="meta-rate-category" value={draft.category} onChange={(event) => setDraft((current) => ({ ...current, category: event.target.value }))} placeholder="marketing" /></div>
          <div className="space-y-1.5"><Label htmlFor="meta-rate-country">País</Label><Input id="meta-rate-country" maxLength={2} value={draft.countryCode} onChange={(event) => setDraft((current) => ({ ...current, countryCode: event.target.value }))} placeholder="BR ou *" /></div>
          <div className="space-y-1.5"><Label htmlFor="meta-rate-currency">Moeda</Label><Input id="meta-rate-currency" maxLength={3} value={draft.currency} onChange={(event) => setDraft((current) => ({ ...current, currency: event.target.value }))} placeholder="BRL" /></div>
          <div className="space-y-1.5"><Label htmlFor="meta-rate-value">Valor por entrega</Label><Input id="meta-rate-value" inputMode="decimal" value={draft.commercialRate} onChange={(event) => setDraft((current) => ({ ...current, commercialRate: event.target.value.replace(',', '.') }))} placeholder="Sem valor padrão" /></div>
          <div className="space-y-1.5"><Label htmlFor="meta-rate-from">Início da vigência</Label><Input id="meta-rate-from" type="datetime-local" value={draft.effectiveFrom} onChange={(event) => setDraft((current) => ({ ...current, effectiveFrom: event.target.value }))} /></div>
          <div className="space-y-1.5"><Label htmlFor="meta-rate-to">Fim da vigência</Label><Input id="meta-rate-to" type="datetime-local" value={draft.effectiveTo} onChange={(event) => setDraft((current) => ({ ...current, effectiveTo: event.target.value }))} /></div>
          <div className="space-y-1.5 sm:col-span-2"><Label htmlFor="meta-rate-checked">Tabela conferida em</Label><Input id="meta-rate-checked" type="datetime-local" value={draft.checkedAt} onChange={(event) => setDraft((current) => ({ ...current, checkedAt: event.target.value }))} /></div>
          <label className="flex items-center justify-between gap-3 rounded-lg border p-3 sm:col-span-2"><span><span className="block text-sm font-medium">Versão ativa</span><span className="block text-xs text-muted-foreground">Versões inativas permanecem no histórico.</span></span><Switch checked={draft.isActive} onCheckedChange={(isActive) => setDraft((current) => ({ ...current, isActive }))} /></label>
          <a className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline sm:col-span-2" href={META_PRICING_SOURCE} target="_blank" rel="noreferrer">Conferir documentação oficial <ExternalLink className="h-3 w-3" /></a>
          <div className="flex gap-2 sm:col-span-2"><Button type="button" disabled={!canSave || saveMutation.isPending} onClick={save}>{saveMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Salvar versão</Button><Button type="button" variant="outline" onClick={() => setDraft(emptyDraft())}>Limpar</Button></div>
        </CardContent>
      </Card>
    </div>
  );
}
