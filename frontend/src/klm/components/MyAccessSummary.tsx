import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, Loader2, ShieldQuestion, XCircle } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { fetchMyAccessPackage } from '@/services/admin';
import { getActionLabel, getModuleLabel } from '@/klm/lib/permission-labels';
import type { Permission, PermissionModule } from '@/types';

const SENSITIVE_POLICY_LABEL: Record<string, string> = {
  hidden: 'Dados sensíveis de clientes ficam ocultos para você.',
  view: 'Você pode ver dados sensíveis de clientes, mas não editá-los.',
  edit: 'Você pode ver e editar dados sensíveis de clientes.',
};

function groupPermissionsByModule(permissions: Permission[]): Map<PermissionModule, Permission[]> {
  const map = new Map<PermissionModule, Permission[]>();
  for (const permission of permissions) {
    const list = map.get(permission.module) ?? [];
    list.push(permission);
    map.set(permission.module, list);
  }
  return map;
}

/**
 * Seção "O que meu acesso inclui" — gerada diretamente do catálogo de pacotes
 * de acesso (app/core/workspace_access_packages.py, via GET /workspaces/me/access-package),
 * traduzida para linguagem simples. Mostrada na página de preferências do usuário.
 */
export function MyAccessSummary() {
  const { data, isPending, isError } = useQuery({
    queryKey: ['klm', 'my-access-package'],
    queryFn: fetchMyAccessPackage,
    staleTime: 5 * 60_000,
  });

  return (
    <Card>
      <CardHeader className="border-b border-border/70">
        <CardTitle className="flex items-center gap-2 text-base">
          <ShieldQuestion className="h-4 w-4" />
          O que meu acesso inclui
        </CardTitle>
        <CardDescription>
          Resumo do seu pacote de acesso neste workspace, em linguagem simples.
        </CardDescription>
      </CardHeader>
      <CardContent className="pt-4">
        {isPending && (
          <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Carregando seu pacote de acesso...
          </div>
        )}
        {isError && (
          <p className="py-4 text-sm text-muted-foreground">
            Não foi possível carregar o resumo do seu acesso agora. Tente novamente mais tarde.
          </p>
        )}
        {!isPending && !isError && !data && (
          <p className="py-4 text-sm text-muted-foreground">
            Nenhum pacote de acesso identificado para o seu usuário neste workspace.
          </p>
        )}
        {data && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge className="text-xs font-medium">{data.label}</Badge>
              <span className="text-xs text-muted-foreground">{data.description}</span>
            </div>

            <p className="text-xs leading-5 text-muted-foreground">
              {SENSITIVE_POLICY_LABEL[data.sensitiveDataPolicy] ?? SENSITIVE_POLICY_LABEL.hidden}
            </p>

            <div className="grid gap-2 sm:grid-cols-2">
              {Array.from(groupPermissionsByModule(data.permissions).entries())
                .sort((a, b) => getModuleLabel(a[0]).localeCompare(getModuleLabel(b[0]), 'pt-BR'))
                .map(([module, permissions]) => (
                  <div key={module} className="rounded-md border border-border/70 p-3">
                    <p className="text-xs font-semibold">{getModuleLabel(module)}</p>
                    <ul className="mt-1.5 space-y-1">
                      {permissions
                        .slice()
                        .sort((a, b) => getActionLabel(a.action).localeCompare(getActionLabel(b.action), 'pt-BR'))
                        .map((permission) => (
                          <li
                            key={`${permission.module}:${permission.action}`}
                            className="flex items-center gap-1.5 text-2xs text-muted-foreground"
                          >
                            <CheckCircle2 className="h-3 w-3 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
                            Você pode {getActionLabel(permission.action)}
                          </li>
                        ))}
                    </ul>
                  </div>
                ))}
            </div>

            {data.permissionDenials.length > 0 && (
              <div className="rounded-md border border-dashed border-border/70 p-3">
                <p className="text-xs font-semibold">Bloqueios específicos do seu pacote</p>
                <ul className="mt-1.5 space-y-1">
                  {data.permissionDenials.map((permission) => (
                    <li
                      key={`deny-${permission.module}:${permission.action}`}
                      className="flex items-center gap-1.5 text-2xs text-muted-foreground"
                    >
                      <XCircle className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" />
                      Não é possível {getActionLabel(permission.action)} em {getModuleLabel(permission.module)}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <p className="text-2xs text-muted-foreground">
              Para mudar seu acesso, fale com o administrador do workspace.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default MyAccessSummary;
