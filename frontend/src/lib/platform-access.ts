import type { AuthUser, PlatformPermission } from '@/types';

export function hasPlatformAccess(user: AuthUser | null | undefined): boolean {
  return Boolean(user?.isPlatformRoot || user?.platformAccessActive);
}

export function isPlatformRoot(user: AuthUser | null | undefined): boolean {
  return Boolean(user?.isPlatformRoot);
}

export function canPlatform(
  user: AuthUser | null | undefined,
  module: string,
  action = 'view',
): boolean {
  if (isPlatformRoot(user)) return true;
  return Boolean(
    user?.platformAccessActive
    && user.platformPermissions?.some(
      (permission: PlatformPermission) => permission.module === module && permission.action === action,
    ),
  );
}
