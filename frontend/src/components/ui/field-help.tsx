import * as React from 'react';
import { Info } from 'lucide-react';

import { Label } from '@/components/ui/label';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';

type FieldHelpCopy = {
  label: string;
  description: React.ReactNode;
  whereToFind: React.ReactNode;
};

type FieldHelpProps = FieldHelpCopy & {
  className?: string;
};

type FieldHelpLabelProps = FieldHelpCopy & {
  htmlFor?: string;
  className?: string;
};

/**
 * Ajuda sob demanda para termos e configurações que não devem ocupar o texto
 * principal da tela. O botão funciona com mouse, toque e teclado.
 */
export function FieldHelp({ label, description, whereToFind, className }: FieldHelpProps) {
  const titleId = React.useId();
  const descriptionId = React.useId();

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`Ajuda sobre ${label}`}
          className={cn(
            'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
            className,
          )}
          data-field-help={label}
          title={`Ajuda sobre ${label}`}
        >
          <Info className="h-4 w-4" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        role="dialog"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        align="start"
        className="w-[min(22rem,calc(100vw-2rem))] space-y-3 text-sm"
      >
        <div className="flex items-start gap-2">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
          <div>
            <p id={titleId} className="font-semibold text-foreground">{label}</p>
            <p id={descriptionId} className="mt-1 leading-5 text-muted-foreground">
              <span className="font-medium text-foreground">O que é: </span>
              {description}
            </p>
          </div>
        </div>
        <p className="border-t pt-3 leading-5 text-muted-foreground">
          <span className="font-medium text-foreground">Onde encontrar: </span>
          {whereToFind}
        </p>
      </PopoverContent>
    </Popover>
  );
}

export function FieldHelpLabel({
  label,
  description,
  whereToFind,
  htmlFor,
  className,
}: FieldHelpLabelProps) {
  return (
    <div className={cn('flex min-h-7 items-center gap-1', className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      <FieldHelp label={label} description={description} whereToFind={whereToFind} />
    </div>
  );
}

export type { FieldHelpCopy, FieldHelpLabelProps, FieldHelpProps };
