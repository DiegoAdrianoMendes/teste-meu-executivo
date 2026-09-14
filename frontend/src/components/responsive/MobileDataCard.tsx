import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export function MobileDataCard({ title, subtitle, children, onClick, className }: {
  title: string;
  subtitle?: string;
  children?: ReactNode;
  onClick?: () => void;
  className?: string;
}) {
  const content = <div className={cn('min-w-0 rounded-lg border bg-card p-3 text-left', onClick && 'transition hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary', className)}>
    <strong className="block truncate text-sm">{title}</strong>
    {subtitle ? <span className="mt-1 block truncate text-xs text-muted-foreground">{subtitle}</span> : null}
    {children ? <div className="mt-3 grid gap-2">{children}</div> : null}
  </div>;
  return onClick ? <button type="button" className="block w-full min-h-11 text-left" onClick={onClick}>{content}</button> : content;
}
