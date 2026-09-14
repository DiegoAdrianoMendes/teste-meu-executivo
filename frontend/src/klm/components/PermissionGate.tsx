import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Lock } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { useAppStore } from '@/store/app-context';
import type { PermissionAction, PermissionModule } from '@/types';
import { fetchMyAccessPackage } from '@/services/admin';
import { getActionLabel, getModuleLabel } from '@/klm/lib/permission-labels';

export type PermissionGateVariant = 'hide' | 'lock' | 'readonly';

interface PermissionGateProps {
  /** Módulo técnico de permissão (ex.: 'kanban', 'mensagens'). */
  module: PermissionModule;
  /** Ação exigida — padrão 'edit', pois este componente protege ações/edições. */
  action?: PermissionAction;
  /**
   * hide: some completamente quando não há acesso (só use quando o item realmente
   *   não deveria existir para o usuário — prefira 'lock' sempre que possível).
   * lock: renderiza o conteúdo desabilitado com cadeado + tooltip explicativo.
   * readonly: idem, mas sem cadeado visível — usado para blocos read-only inteiros,
   *   com um badge "Somente leitura" no título via `readonlyLabel`/`showReadonlyBadge`.
   */
  variant?: PermissionGateVariant;
  children: ReactNode;
  /** Rótulo legível da ação bloqueada, para o tooltip (ex.: "editar este card"). Se omitido, usa o rótulo padrão da ação. */
  actionLabel?: string;
  /** Mostra o badge "Somente leitura" junto ao children (útil em `readonly` no título de um bloco). */
  showReadonlyBadge?: boolean;
  className?: string;
}

function useMyPackageLabel(): string {
  const { data } = useQuery({
    queryKey: ['klm', 'my-access-package'],
    queryFn: fetchMyAccessPackage,
    staleTime: 5 * 60_000,
    retry: false,
  });
  return data?.label ?? 'atual';
}

function buildTooltipText(moduleLabel: string, actionLabel: string, packageLabel: string): string {
  return `Somente leitura — seu pacote (${packageLabel}) não inclui ${actionLabel} em ${moduleLabel}. Fale com o administrador.`;
}

/** Desabilita recursivamente o(s) elemento(s) filho — inputs, botões, links — sem removê-los da árvore. */
function disableChildren(node: ReactNode): ReactNode {
  if (!isValidElement(node)) return node;
  const element = node as ReactElement<{ disabled?: boolean; 'aria-disabled'?: boolean; tabIndex?: number; className?: string; onClick?: (...args: unknown[]) => void }>;
  return cloneElement(element, {
    disabled: true,
    'aria-disabled': true,
    tabIndex: -1,
    onClick: (event: { preventDefault?: () => void; stopPropagation?: () => void }) => {
      event?.preventDefault?.();
      event?.stopPropagation?.();
    },
    className: cn(element.props.className, 'pointer-events-none opacity-60 grayscale-[0.15]'),
  });
}

/**
 * Componente único que padroniza os 3 estados de UX de permissão em toda a plataforma:
 * `hide` | `lock` | `readonly`. Nunca deixa uma ação "sumir sem explicação" — o padrão
 * recomendado é `lock`, reservando `hide` para quando o item de fato não deve existir.
 */
export function PermissionGate({
  module,
  action = 'edit',
  variant = 'lock',
  children,
  actionLabel,
  showReadonlyBadge = false,
  className,
}: PermissionGateProps) {
  const { can } = useAppStore();
  const allowed = can(module, action);
  const packageLabel = useMyPackageLabel();

  if (allowed) return <>{children}</>;

  if (variant === 'hide') return null;

  const moduleLabel = getModuleLabel(module);
  const resolvedActionLabel = actionLabel ?? getActionLabel(action);
  const tooltipText = buildTooltipText(moduleLabel, resolvedActionLabel, packageLabel);

  if (variant === 'readonly') {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <div
            className={cn('relative', className)}
            role="group"
            aria-disabled="true"
            aria-label={tooltipText}
          >
            {showReadonlyBadge && (
              <Badge
                variant="outline"
                className="mb-2 gap-1 border-dashed text-2xs font-medium text-muted-foreground"
              >
                Somente leitura
              </Badge>
            )}
            <div className="pointer-events-none select-none opacity-90">{children}</div>
          </div>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs text-xs">
          {tooltipText}
        </TooltipContent>
      </Tooltip>
    );
  }

  // variant === 'lock'
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn('inline-flex items-center gap-1.5', className)}
          role="group"
          aria-disabled="true"
          aria-label={tooltipText}
        >
          {disableChildren(children)}
          <Lock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs text-xs">
        {tooltipText}
      </TooltipContent>
    </Tooltip>
  );
}

export default PermissionGate;
