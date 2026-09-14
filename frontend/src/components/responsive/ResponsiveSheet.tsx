import type { ReactNode } from 'react';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';

export function ResponsiveSheet({ open, onOpenChange, title, description, children, side = 'right' }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  side?: 'top' | 'bottom' | 'left' | 'right';
}) {
  return <Sheet open={open} onOpenChange={onOpenChange}>
    <SheetContent side={side} className="max-sm:w-full max-sm:max-w-none max-sm:max-h-[92dvh] max-sm:rounded-t-2xl max-sm:pb-[calc(1rem+env(safe-area-inset-bottom))]">
      <SheetHeader className="text-left"><SheetTitle>{title}</SheetTitle>{description ? <SheetDescription>{description}</SheetDescription> : null}</SheetHeader>
      <div className="min-h-0 flex-1 overflow-y-auto pt-4">{children}</div>
    </SheetContent>
  </Sheet>;
}
