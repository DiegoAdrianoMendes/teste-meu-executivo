import { AlertTriangle, CheckCircle2, ExternalLink, Loader2, Megaphone, RefreshCw } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import type { MetaCommercialChargeCurrencyTotal, WhatsAppCommercialChargeSummary } from '@/types';

function formatCommercialCharge(value: string, currency: string): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return `${currency} —`;
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 8,
  }).format(parsed);
}

function ChargeTotal({ item }: { item: MetaCommercialChargeCurrencyTotal }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-muted-foreground">API oficial · {item.currency}</p>
        <Badge variant="outline">por entrega</Badge>
      </div>
      <p className="mt-2 text-2xl font-bold tabular-nums">{formatCommercialCharge(item.confirmedCommercialCharge, item.currency)}</p>
      <p className="mt-1 text-xs text-muted-foreground tabular-nums">
        Confirmado em {item.confirmedRecipientCount.toLocaleString('pt-BR')} mensagens entregues.
      </p>
      <div className="mt-2 border-t pt-2 text-xs text-muted-foreground tabular-nums">
        Projeção da audiência: <strong className="text-foreground">{formatCommercialCharge(item.estimatedCommercialCharge, item.currency)}</strong>
        {' '}({item.estimatedRecipientCount.toLocaleString('pt-BR')} elegíveis)
      </div>
    </div>
  );
}

export function MetaCommercialChargePanel({
  value,
  loading = false,
  error = null,
  onRetry,
  title = 'Cobrança da API oficial da Meta',
  compact = false,
}: {
  value: WhatsAppCommercialChargeSummary | null;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  title?: string;
  compact?: boolean;
}) {
  const missingRecipientCount = (value?.missingRates ?? []).reduce((sum, item) => sum + item.recipientCount, 0);
  const hasMissingRates = missingRecipientCount > 0;

  return (
    <Card data-testid="meta-commercial-charge-panel" className="border-primary/25">
      <CardHeader className="gap-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <CardTitle className="flex items-center gap-2 text-base"><Megaphone className="h-4 w-4 text-primary" />{title}</CardTitle>
            <CardDescription className="mt-1">
              Valor comercial integral das mensagens oficiais, separado da franquia de tokens de IA e de qualquer custo interno da plataforma.
            </CardDescription>
          </div>
          <Badge variant="outline" className={hasMissingRates ? 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200' : 'border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300'}>
            {hasMissingRates ? 'Tarifa pendente' : 'Tabela conferida'}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading && !value ? (
          <div className="flex min-h-28 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />Calculando mensagens elegíveis e entregues</div>
        ) : error ? (
          <div className="flex min-h-28 flex-col items-center justify-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-5 text-center">
            <AlertTriangle className="h-5 w-5 text-destructive" />
            <div><p className="text-sm font-semibold">Cobrança oficial temporariamente indisponível</p><p className="mt-1 text-xs text-muted-foreground">{error}</p></div>
            {onRetry && <Button type="button" size="sm" variant="outline" onClick={onRetry}><RefreshCw className="mr-2 h-3.5 w-3.5" />Tentar novamente</Button>}
          </div>
        ) : value ? (
          <>
            <div className={`grid gap-3 ${compact ? 'md:grid-cols-2' : 'md:grid-cols-2 xl:grid-cols-3'}`}>
              {value.totalsByCurrency.map((item) => <ChargeTotal key={item.currency} item={item} />)}
              {!value.totalsByCurrency.length && (
                <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                  {value.campaigns.length ? 'Ainda não há total calculável: cadastre a tarifa oficial aplicável.' : 'Nenhum disparo oficial registrado neste workspace.'}
                </div>
              )}
            </div>

            {hasMissingRates && (
              <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <p><strong>{missingRecipientCount.toLocaleString('pt-BR')} mensagens sem tarifa aplicável.</strong> O sistema não estimou nem confirmou valor para elas: nenhum valor será inventado. O superadmin precisa cadastrar a versão oficial por categoria, país, moeda e vigência.</p>
              </div>
            )}

            {!hasMissingRates && value.campaigns.length > 0 && (
              <div className="flex items-start gap-2 rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-xs text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                <p>Todos os destinatários avaliados encontraram uma tarifa vigente. O confirmado usa somente mensagens entregues ou lidas; a projeção usa a audiência elegível.</p>
              </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3 text-xs text-muted-foreground">
              <span>Base de cobrança: mensagens entregues · atualizado em {new Date(value.generatedAt).toLocaleString('pt-BR')}</span>
              <a className="inline-flex items-center gap-1 font-medium text-primary hover:underline" href="https://developers.facebook.com/docs/whatsapp/pricing" target="_blank" rel="noreferrer">Tabela oficial <ExternalLink className="h-3 w-3" /></a>
            </div>
          </>
        ) : (
          <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">Nenhum resumo disponível.</div>
        )}
      </CardContent>
    </Card>
  );
}
