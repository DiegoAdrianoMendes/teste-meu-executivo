import { ShieldAlert } from 'lucide-react';
import { KlmPill } from '@/klm/components/Primitives';

/**
 * Metadado de honestidade (Plano Mestre §15) — obrigatório em toda aba do
 * BI jurídico e da capacidade (Onda 8, Sprint C UI). NUNCA esconder: corte
 * temporal, amostra, origem e campos ausentes vêm literalmente do que o
 * backend devolveu em `honesty` — nenhum texto aqui infere causalidade
 * onde o serviço só contou correlação.
 */
export type BiHonestyBannerProps = {
  generatedAt: string;
  periodFrom?: string | null;
  periodTo?: string | null;
  sampleSize: number;
  origin: string;
  missingFields: readonly string[];
};

function formatDateTime(value: string | null | undefined): string {
  if (!value) return 'não informado';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('pt-BR');
}

export function BiHonestyBanner({ generatedAt, periodFrom, periodTo, sampleSize, origin, missingFields }: BiHonestyBannerProps) {
  return (
    <div className="rounded-lg border border-border bg-secondary p-3 text-2xs" data-testid="bi-honesty-banner">
      <div className="grid gap-2 sm:grid-cols-4">
        <span><strong className="block text-foreground">Corte temporal</strong>{periodFrom || periodTo ? `${formatDateTime(periodFrom)} até ${formatDateTime(periodTo)}` : 'recorte não informado pelo backend'}</span>
        <span><strong className="block text-foreground">Amostra</strong>{sampleSize} registro{sampleSize === 1 ? '' : 's'}</span>
        <span><strong className="block text-foreground">Origem</strong>{origin}</span>
        <span><strong className="block text-foreground">Gerado em</strong>{formatDateTime(generatedAt)}</span>
      </div>
      {missingFields.length > 0 && (
        <div className="mt-2 flex items-start gap-2 border-t border-dashed border-border pt-2 text-muted-foreground">
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#a53e3c]" />
          <div>
            <strong className="block text-foreground">Campos que este endpoint declara indisponíveis</strong>
            <ul className="mt-1 list-disc space-y-1 pl-4">
              {missingFields.map((field) => <li key={field}>{field}</li>)}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

/** Célula de indicador que respeita a regra §15: quando o valor é `null`
 * (o backend não calculou por não existir de forma confiável, ex.: "tempo
 * economizado"), mostra "Indisponível" — nunca 0 nem "—" disfarçado. */
export function BiMetricValue({ value, suffix }: { value: number | null | undefined; suffix?: string }) {
  if (value == null || !Number.isFinite(value)) {
    return <KlmPill tone="gold">Indisponível</KlmPill>;
  }
  return <span className="tabular-nums">{value.toLocaleString('pt-BR')}{suffix ?? ''}</span>;
}
