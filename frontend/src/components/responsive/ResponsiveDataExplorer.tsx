import type { ReactNode } from 'react';

export function ResponsiveDataExplorer({ children, mobile, label = 'Dados' }: { children: ReactNode; mobile?: ReactNode; label?: string }) {
  return <section aria-label={label} className="min-w-0">
    <div className="hidden min-w-0 md:block">{children}</div>
    <div className="min-w-0 md:hidden">{mobile ?? children}</div>
  </section>;
}
