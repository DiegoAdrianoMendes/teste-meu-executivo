const brlFormatter = new Intl.NumberFormat('pt-BR', {
  style: 'currency',
  currency: 'BRL',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function normalizedNumber(value: number | null | undefined): number | null {
  if (value == null) return null;
  return Number.isFinite(value) ? value : null;
}

export function formatBRL(value: number | null | undefined): string {
  const normalized = normalizedNumber(value);
  if (normalized === null) return '—';
  return brlFormatter.format(normalized).replace(/\u00a0/g, ' ');
}

export function formatDecimal(
  value: number | null | undefined,
  fractionDigits = 2,
): string {
  const normalized = normalizedNumber(value);
  if (normalized === null) return '—';
  return new Intl.NumberFormat('pt-BR', {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(normalized);
}
