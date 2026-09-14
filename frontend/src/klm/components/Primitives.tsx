import { type ComponentPropsWithoutRef, type ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react';
import { Area, AreaChart, ResponsiveContainer } from 'recharts';
import { cn } from '@/lib/utils';
import { Skeleton } from '@/components/ui/skeleton';

/** Reconhece um valor formatado como "R$ 12.345,67", "1.234" ou "87%" e devolve
 * as peças necessárias para reconstruí-lo a cada quadro da contagem animada. */
function parseAnimatableValue(raw: string): { prefix: string; suffix: string; target: number; decimals: number } | null {
  const match = /^([^\d-]*)(-?[\d.,]+)([^\d]*)$/.exec(raw.trim());
  if (!match) return null;
  const [, prefix, numeric, suffix] = match;
  const hasComma = numeric.includes(',');
  const decimals = hasComma ? (numeric.split(',')[1]?.length ?? 0) : 0;
  const normalized = hasComma ? numeric.replaceAll('.', '').replace(',', '.') : numeric.replaceAll('.', '');
  const target = Number(normalized);
  if (!Number.isFinite(target)) return null;
  return { prefix, suffix, target, decimals };
}

function formatAnimatableValue(parsed: { prefix: string; suffix: string; decimals: number }, current: number): string {
  const body = current.toLocaleString('pt-BR', { minimumFractionDigits: parsed.decimals, maximumFractionDigits: parsed.decimals });
  return `${parsed.prefix}${body}${parsed.suffix}`;
}

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Janela após a montagem em que trocas de valor (ex.: placeholder "0" → dado real
 * chegando da API) assumem o valor final direto, sem animação — evita contar do
 * zero só porque a consulta acabou de resolver. Trocas depois disso (atualização
 * ao vivo de um número já exibido) contam normalmente. */
const COUNT_UP_MOUNT_GRACE_MS = 900;

/** Contagem animada ease-out (~600ms) para números de métrica. Cai de volta ao
 * valor final imediatamente quando o valor não é numérico, reduced-motion está
 * ativo, ou a troca aconteceu logo após a montagem (primeiro carregamento). */
export function useAnimatedValue(rawValue: string, durationMs = 600): string {
  const parsed = useMemo(() => parseAnimatableValue(rawValue), [rawValue]);
  const [display, setDisplay] = useState(rawValue);
  const previousTarget = useRef<number | null>(null);
  const mountedAt = useRef<number | null>(null);

  useEffect(() => {
    if (mountedAt.current === null) mountedAt.current = performance.now();
    if (!parsed) {
      previousTarget.current = null;
      setDisplay(rawValue);
      return;
    }
    const withinMountGrace = performance.now() - mountedAt.current < COUNT_UP_MOUNT_GRACE_MS;
    if (previousTarget.current === null || withinMountGrace || prefersReducedMotion()) {
      previousTarget.current = parsed.target;
      setDisplay(rawValue);
      return;
    }
    const start = previousTarget.current;
    const { target } = parsed;
    previousTarget.current = target;
    if (start === target) {
      setDisplay(rawValue);
      return;
    }
    let frame = 0;
    const startedAt = performance.now();
    const tick = (now: number) => {
      const elapsed = now - startedAt;
      const t = Math.min(1, elapsed / durationMs);
      const eased = 1 - (1 - t) ** 3;
      const current = start + (target - start) * eased;
      setDisplay(t >= 1 ? rawValue : formatAnimatableValue(parsed, current));
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [rawValue, parsed, durationMs]);

  return display;
}

export function KlmSparkline({ data, tone = 'neutral' }: { data: number[]; tone?: 'up' | 'down' | 'neutral' }) {
  if (!data || data.length < 2) return null;
  const points = data.map((value, index) => ({ index, value }));
  const strokeColor = tone === 'up' ? '#0b7435' : tone === 'down' ? '#a53e3c' : '#0057d8';
  return (
    <div className="mt-2 h-8 w-full" aria-hidden="true" data-testid="klm-sparkline">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={`klm-sparkline-fill-${tone}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={strokeColor} stopOpacity={0.35} />
              <stop offset="100%" stopColor={strokeColor} stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area
            type="monotone"
            dataKey="value"
            stroke={strokeColor}
            strokeWidth={1.5}
            fill={`url(#klm-sparkline-fill-${tone})`}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function KlmPanel({
  children,
  className,
  tone = 'default',
  ...sectionProps
}: ComponentPropsWithoutRef<'section'> & {
  tone?: 'default' | 'soft' | 'black';
}) {
  return (
    <section
      {...sectionProps}
      className={cn(
        'min-w-0 rounded-lg border shadow-[var(--shadow-card)]',
        tone === 'default' && 'border-border bg-card text-card-foreground',
        tone === 'soft' && 'border-border bg-secondary text-secondary-foreground',
        tone === 'black' && 'border-sidebar-border bg-sidebar text-sidebar-foreground dark:border-white/10',
        className,
      )}
    >
      {children}
    </section>
  );
}

export function KlmSectionTitle({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="flex min-w-0 items-start justify-between gap-4">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold leading-5 text-foreground">{title}</h2>
        {description && <p className="mt-0.5 text-2xs leading-4 text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function KlmMetricCard({
  label,
  value,
  delta,
  trend = 'neutral',
  onClick,
  loading = false,
  sparkline,
}: {
  label: string;
  value: string;
  delta: string;
  trend?: 'up' | 'down' | 'neutral';
  onClick?: () => void;
  loading?: boolean;
  sparkline?: number[];
}) {
  const Icon = trend === 'up' ? ArrowUpRight : trend === 'down' ? ArrowDownRight : Minus;
  const animatedValue = useAnimatedValue(value);
  const content = loading ? (
    <>
      <span className="absolute inset-y-0 left-0 w-1 bg-primary/40" />
      <span className="block text-xs font-semibold text-foreground">{label}</span>
      <Skeleton className="mt-2 h-7 w-20" />
      <Skeleton className="mt-3 h-3 w-24" />
    </>
  ) : (
    <>
      <span className="absolute inset-y-0 left-0 w-1 bg-primary transition-colors group-hover:bg-[#1478ff]" />
      <span className="block text-xs font-semibold text-foreground">{label}</span>
      <span className="mt-1 block text-2xl font-bold leading-8 text-foreground tabular-nums" data-testid="klm-metric-value">{animatedValue}</span>
      <span className={cn(
        'mt-2 flex items-center gap-1 text-2xs font-semibold',
        trend === 'up' && 'text-[#0b7435] dark:text-emerald-400',
        trend === 'down' && 'text-[#a53e3c] dark:text-red-400',
        trend === 'neutral' && 'text-muted-foreground',
      )}>
        <Icon className="h-3 w-3" />
        {delta}
      </span>
      {sparkline && sparkline.length >= 2 && <KlmSparkline data={sparkline} tone={trend} />}
    </>
  );
  const className = cn(
    'group relative min-h-[102px] w-full overflow-hidden rounded-lg border border-border bg-card p-4 text-left shadow-[var(--shadow-card)]',
    onClick && 'transition duration-200 hover:-translate-y-0.5 hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
  );
  return onClick
    ? <button type="button" onClick={onClick} className={className}>{content}</button>
    : <article className={className}>{content}</article>;
}

export function KlmPill({
  children,
  tone = 'gold',
  className,
  ...spanProps
}: ComponentPropsWithoutRef<'span'> & {
  tone?: 'gold' | 'green' | 'red' | 'blue' | 'black' | 'muted';
}) {
  return (
    <span {...spanProps} className={cn(
      'inline-flex min-h-5 min-w-0 items-center rounded-full border px-2 py-0.5 text-2xs font-medium leading-4',
      tone === 'gold' && 'border-primary/25 bg-primary/10 text-[#0057d8] dark:text-[#8fc1ff]',
      tone === 'green' && 'border-emerald-600/20 bg-emerald-500/[0.12] text-[#0b7435] dark:text-emerald-400',
      tone === 'red' && 'border-red-700/20 bg-red-500/[0.12] text-[#a53e3c] dark:text-red-400',
      tone === 'blue' && 'border-primary/25 bg-primary/10 text-[#0057d8] dark:text-[#8fc1ff]',
      tone === 'black' && 'border-sidebar-border bg-sidebar text-sidebar-foreground dark:border-white/20',
      tone === 'muted' && 'border-border bg-secondary text-muted-foreground',
      className,
    )}>
      {children}
    </span>
  );
}

export function KlmProgress({ value, tone = 'gold' }: { value: number; tone?: 'gold' | 'green' | 'black' | 'red' }) {
  const normalizedValue = Math.max(0, Math.min(100, value));
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-[#dce7f2] dark:bg-white/10" role="progressbar" aria-label={`${Math.round(normalizedValue)}%`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(normalizedValue)}>
      <div
        className={cn(
          'h-full rounded-full transition-[width] duration-500',
          tone === 'gold' && 'bg-primary',
          tone === 'green' && 'bg-[#1a9948]',
          tone === 'black' && 'bg-sidebar dark:bg-white',
          tone === 'red' && 'bg-[#b94a48]',
        )}
        style={{ width: `${normalizedValue}%` }}
      />
    </div>
  );
}
