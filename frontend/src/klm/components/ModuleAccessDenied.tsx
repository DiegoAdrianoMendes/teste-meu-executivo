import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Loader2, ShieldOff, Send, CheckCircle2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/use-toast';
import { requestModuleAccess } from '@/services/notifications';
import type { PermissionModule } from '@/types';
import { getModuleLabel } from '@/klm/lib/permission-labels';

interface ModuleAccessDeniedProps {
  module: PermissionModule;
  /** Sobrescreve o título padrão, se necessário. */
  title?: string;
  /** Sobrescreve a explicação padrão. */
  description?: string;
  className?: string;
}

/**
 * Empty-state elegante mostrado quando o usuário acessa diretamente (via URL) um
 * módulo fora do seu pacote de acesso. Substitui o redirecionamento silencioso
 * anterior do `PermissionRoute` — nunca "some" sem explicação.
 */
export function ModuleAccessDenied({ module, title, description, className }: ModuleAccessDeniedProps) {
  const { toast } = useToast();
  const [requested, setRequested] = useState(false);
  const moduleLabel = getModuleLabel(module);

  const mutation = useMutation({
    mutationFn: () => requestModuleAccess({ module, moduleLabel }),
    onSuccess: (result) => {
      setRequested(true);
      toast({
        title: 'Pedido enviado',
        description: result.notifiedAdmins > 0
          ? `O(s) administrador(es) do workspace foram avisados.`
          : 'O pedido foi registrado.',
      });
    },
    onError: () => {
      toast({
        title: 'Não foi possível enviar o pedido',
        description: 'Tente novamente em instantes ou fale diretamente com o administrador do workspace.',
        variant: 'destructive',
      });
    },
  });

  return (
    <div
      className={`mx-auto flex w-full max-w-md flex-col items-center gap-4 rounded-2xl border border-dashed border-border/70 bg-muted/20 px-6 py-14 text-center ${className ?? ''}`}
      role="status"
    >
      <span className="flex h-14 w-14 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <ShieldOff className="h-6 w-6" aria-hidden="true" />
      </span>
      <div className="space-y-1.5">
        <h2 className="text-lg font-semibold">{title ?? `${moduleLabel} não faz parte do seu acesso`}</h2>
        <p className="text-sm leading-6 text-muted-foreground">
          {description
            ?? `Este módulo não está incluído no seu pacote de acesso atual. Se você precisa dele para o seu trabalho, solicite ao administrador do workspace.`}
        </p>
      </div>
      {requested ? (
        <div className="flex items-center gap-2 rounded-full bg-emerald-500/10 px-4 py-2 text-sm font-medium text-emerald-600 dark:text-emerald-400">
          <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
          Pedido enviado ao administrador
        </div>
      ) : (
        <Button onClick={() => mutation.mutate()} disabled={mutation.isPending} className="gap-2">
          {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          Solicitar acesso
        </Button>
      )}
    </div>
  );
}

export default ModuleAccessDenied;
