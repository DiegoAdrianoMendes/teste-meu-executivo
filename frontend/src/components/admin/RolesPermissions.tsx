import { useEffect, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { PermissionMatrix } from './PermissionMatrix';
import { Users, ChevronRight } from 'lucide-react';
import type { AdminRole, AdminUser } from '@/types';

interface RolesPermissionsProps {
  roles: AdminRole[];
  users: AdminUser[];
}

export function RolesPermissions({ roles, users }: RolesPermissionsProps) {
  const [selectedRoleId, setSelectedRoleId] = useState<string | null>(roles[0]?.id ?? null);
  const selectedRole = roles.find((role) => role.id === selectedRoleId) ?? roles[0] ?? null;

  useEffect(() => {
    setSelectedRoleId((current) => {
      if (current && roles.some((role) => role.id === current)) {
        return current;
      }
      return roles[0]?.id ?? null;
    });
  }, [roles]);

  const getUserCount = (roleId: string) => users.filter(u => u.roles.includes(roleId)).length;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="space-y-2 lg:col-span-1">
          {roles.map(role => (
            <button
              key={role.id}
              type="button"
              onClick={() => setSelectedRoleId(role.id)}
              className={`w-full text-left rounded-lg border p-3 ${selectedRole?.id === role.id ? 'border-primary bg-primary/5' : ''}`}
            >
              <div className="flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: role.color }} />
                    <span className="text-sm font-semibold">{role.name}</span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1 line-clamp-1">{role.description}</p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant="outline" className="text-xs gap-1 tabular-nums shrink-0">
                    <Users className="h-2.5 w-2.5" /> {getUserCount(role.id)}
                  </Badge>
                  {selectedRole?.id === role.id && <ChevronRight className="h-4 w-4 text-muted-foreground" />}
                </div>
              </div>
            </button>
          ))}
        </div>

        <div className="lg:col-span-2">
          {selectedRole ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-medium">{selectedRole.name}</h3>
                  <p className="text-xs text-muted-foreground">{selectedRole.description}</p>
                </div>
                <Badge variant="outline">Somente leitura</Badge>
              </div>
              <PermissionMatrix permissions={selectedRole.permissions} onChange={() => {}} readOnly />
            </div>
          ) : (
            <div className="flex items-center justify-center h-48 text-sm text-muted-foreground border rounded-lg border-dashed">
              Selecione um papel para ver suas permissões
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
