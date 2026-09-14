import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import type { Permission, PermissionModule, PermissionAction, PermissionScope } from '@/types';
import { PERMISSION_MODULES_LABELS, PERMISSION_ACTIONS_LABELS } from '@/constants/permissions';
import { useIsMobile } from '@/hooks/use-mobile';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

const moduleOrder = Object.keys(PERMISSION_MODULES_LABELS) as PermissionModule[];
const actionOrder = Object.keys(PERMISSION_ACTIONS_LABELS) as PermissionAction[];

interface PermissionMatrixProps {
  permissions: Permission[];
  onChange: (perms: Permission[]) => void;
  readOnly?: boolean;
  availablePermissions?: Permission[];
  inheritedPermissions?: Permission[];
  directPermissions?: Permission[];
  deniedPermissions?: Permission[];
}

const scopeLabels: Record<PermissionScope, string> = {
  workspace: 'Workspace',
  all: 'Todos',
  team: 'Equipe',
  own: 'Próprio',
};

export function PermissionMatrix({ permissions, onChange, readOnly, availablePermissions, inheritedPermissions = [], directPermissions = [], deniedPermissions = [] }: PermissionMatrixProps) {
  const isMobile = useIsMobile();
  const catalog = availablePermissions?.length ? availablePermissions : permissions;
  const modules = moduleOrder.filter((module) => catalog.some((permission) => permission.module === module));
  const actions = actionOrder.filter((action) => catalog.some((permission) => permission.action === action));

  const hasPerm = (mod: PermissionModule, act: PermissionAction) =>
    permissions.some(p => p.module === mod && p.action === act);

  const getScope = (mod: PermissionModule, act: PermissionAction): PermissionScope =>
    permissions.find(p => p.module === mod && p.action === act)?.scope
      ?? catalog.find(p => p.module === mod && p.action === act)?.scope
      ?? 'workspace';

  const isAvailable = (mod: PermissionModule, act: PermissionAction) =>
    catalog.some(p => p.module === mod && p.action === act);

  const permissionState = (mod: PermissionModule, act: PermissionAction) => {
    const matches = (permission: Permission) => permission.module === mod && permission.action === act;
    if (deniedPermissions.some(matches)) return 'blocked';
    if (directPermissions.some(matches)) return 'granted';
    if (inheritedPermissions.some(matches)) return 'inherited';
    return 'off';
  };

  const togglePerm = (mod: PermissionModule, act: PermissionAction) => {
    if (readOnly) return;
    if (hasPerm(mod, act)) {
      onChange(permissions.filter(p => !(p.module === mod && p.action === act)));
    } else {
      onChange([...permissions, { module: mod, action: act, scope: getScope(mod, act) }]);
    }
  };

  const setScope = (mod: PermissionModule, act: PermissionAction, scope: PermissionScope) => {
    if (readOnly) return;
    onChange(permissions.map(p => p.module === mod && p.action === act ? { ...p, scope } : p));
  };

  return (
    <TooltipProvider delayDuration={250}>
    <div className="overflow-hidden rounded-md border border-border bg-background">
      <div className="flex flex-wrap gap-x-4 gap-y-2 border-b bg-muted/20 px-3 py-2 text-2xs text-muted-foreground" aria-label="Legenda das permissões">
        <span><span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm border border-emerald-700 bg-emerald-600" />Permitida</span>
        <span><span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm border border-border bg-background" />Não permitida</span>
        <span>Clique na caixa para permitir ou bloquear.</span>
      </div>
      <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b bg-muted/50">
            <th className="text-left p-2 font-medium text-muted-foreground sticky left-0 bg-muted/50 min-w-[100px]">Módulo</th>
            {actions.map(a => (
              <th key={a} className="p-2 font-medium text-muted-foreground text-center min-w-[70px]">
                {isMobile ? PERMISSION_ACTIONS_LABELS[a].slice(0, 3) : PERMISSION_ACTIONS_LABELS[a]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {modules.map(mod => (
            <tr key={mod} className="border-b last:border-b-0 hover:bg-muted/30">
              <td className="p-2 font-medium sticky left-0 bg-card">{PERMISSION_MODULES_LABELS[mod]}</td>
              {actions.map(act => {
                if (!isAvailable(mod, act)) {
                  return <td key={act} className="p-2 text-center text-muted-foreground/40">-</td>;
                }
                const checked = hasPerm(mod, act);
                const state = permissionState(mod, act);
                const moduleLabel = PERMISSION_MODULES_LABELS[mod];
                const actionLabel = PERMISSION_ACTIONS_LABELS[act];
                const stateLabel = state === 'blocked' ? 'bloqueada individualmente' : state === 'granted' ? 'concedida individualmente' : state === 'inherited' ? 'herdada do papel base' : 'não permitida';
                return (
                  <td key={act} className="p-2 text-center">
                    <div className="flex flex-col items-center gap-1">
                      <Tooltip>
                        <TooltipTrigger asChild>
                      <Checkbox
                        checked={checked}
                        onCheckedChange={() => togglePerm(mod, act)}
                        disabled={readOnly}
                        aria-label={`${actionLabel} em ${moduleLabel}: ${stateLabel}`}
                        className="h-4 w-4 border-border data-[state=checked]:border-emerald-700 data-[state=checked]:bg-emerald-700 data-[state=checked]:text-white"
                      />
                        </TooltipTrigger>
                        <TooltipContent className="max-w-64 text-xs">
                          <strong>{moduleLabel}: {actionLabel}</strong>
                          <p className="mt-1">Estado atual: {stateLabel}. {readOnly ? 'O acesso integral do titular não aceita exceções.' : checked ? 'Clique para bloquear esta ação.' : 'Clique para conceder esta ação.'}</p>
                        </TooltipContent>
                      </Tooltip>
                      {checked && !readOnly && (
                        <Select value={getScope(mod, act)} onValueChange={(v) => setScope(mod, act, v as PermissionScope)}>
                          <SelectTrigger className="h-5 w-14 text-2xs px-1 border-dashed">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="all" className="text-xs">Todos</SelectItem>
                            <SelectItem value="workspace" className="text-xs">Workspace</SelectItem>
                          </SelectContent>
                        </Select>
                      )}
                      {checked && readOnly && (
                        <span className="text-2xs text-muted-foreground">
                          {scopeLabels[getScope(mod, act)]}
                        </span>
                      )}
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
    </TooltipProvider>
  );
}
