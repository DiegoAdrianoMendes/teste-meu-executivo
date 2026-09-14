import { useEffect, useState } from 'react';
import { cn } from '@/lib/utils';

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Celebração discreta ao mover um card para a etapa de ganho. Autodescarta-se e
 * respeita prefers-reduced-motion (sem burst animado, só aparece/some). */
export function WinCelebration({ label, onDone }: { label: string | null; onDone: () => void }) {
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    if (!label) return undefined;
    setReduceMotion(prefersReducedMotion());
    const timer = window.setTimeout(onDone, prefersReducedMotion() ? 1200 : 1400);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [label]);

  if (!label) return null;

  return (
    <div
      className="pointer-events-none fixed inset-x-0 top-16 z-[80] flex justify-center px-4"
      data-testid="win-celebration"
      aria-live="polite"
      role="status"
    >
      <div
        className={cn(
          'flex items-center gap-3 rounded-2xl border border-emerald-500/30 bg-[#031121] px-5 py-3 text-white shadow-2xl',
          !reduceMotion && 'klm-win-pop',
        )}
      >
        <svg width="36" height="36" viewBox="0 0 64 64" aria-hidden="true">
          <circle
            cx="32"
            cy="32"
            r="30"
            fill="none"
            stroke="#1a9948"
            strokeWidth="3"
            className={cn(!reduceMotion && 'klm-win-ring')}
          />
          <path
            d="M18 33 L27 42 L46 21"
            fill="none"
            stroke="#1a9948"
            strokeWidth="4"
            strokeLinecap="round"
            strokeLinejoin="round"
            className={cn(!reduceMotion && 'klm-win-check')}
          />
        </svg>
        <p className="text-xs font-semibold leading-tight">
          Caso ganho
          <span className="mt-0.5 block max-w-[220px] truncate font-normal text-white/75">{label}</span>
        </p>
      </div>
    </div>
  );
}
