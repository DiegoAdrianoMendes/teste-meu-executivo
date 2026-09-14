import { Check, ShieldCheck, X } from 'lucide-react';
import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from '@/components/ui/sheet';

export interface HumanDecisionDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  context?: Array<{ label: string; value: string }>;
  onApprove?: () => void;
  onReject?: () => void;
  approveLabel?: string;
  rejectLabel?: string;
  disabled?: boolean;
  disabledReason?: string;
  children?: ReactNode;
}

export function HumanDecisionDrawer({ open, onOpenChange, title, description, context = [], onApprove, onReject, approveLabel = 'Aprovar', rejectLabel = 'Rejeitar', disabled, disabledReason, children }: HumanDecisionDrawerProps) {
  return <Sheet open={open} onOpenChange={onOpenChange}><SheetContent side="bottom" className="max-h-[92dvh] rounded-t-2xl sm:mx-auto sm:max-w-2xl sm:rounded-lg sm:bottom-4">
    <SheetHeader className="text-left"><SheetTitle className="flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-primary" />{title}</SheetTitle>{description && <SheetDescription>{description}</SheetDescription>}</SheetHeader>
    <div className="mt-5 grid gap-2 sm:grid-cols-2">{context.map((item) => <div key={item.label} className="rounded-lg border p-3"><span className="block text-[10px] uppercase text-muted-foreground">{item.label}</span><strong className="mt-1 block break-words text-sm">{item.value}</strong></div>)}</div>
    {children}
    {disabled && disabledReason ? <p className="mt-4 rounded-md border bg-muted/40 p-2 text-xs text-muted-foreground" role="status">{disabledReason}</p> : null}
    <SheetFooter className="mt-6 grid grid-cols-2 gap-2 sm:flex"><Button variant="outline" className="min-h-11" disabled={disabled} onClick={onReject}><X className="mr-2 h-4 w-4" />{rejectLabel}</Button><Button className="min-h-11" disabled={disabled} onClick={onApprove}><Check className="mr-2 h-4 w-4" />{approveLabel}</Button></SheetFooter>
  </SheetContent></Sheet>;
}
