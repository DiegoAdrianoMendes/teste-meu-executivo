export type ApplicationSurface = 'workspace' | 'platform';

const configuredSurface = String(import.meta.env.VITE_APP_SURFACE ?? 'workspace')
  .trim()
  .toLowerCase();

if (configuredSurface !== 'workspace' && configuredSurface !== 'platform') {
  throw new Error(
    'VITE_APP_SURFACE deve ser "workspace" ou "platform".',
  );
}

export const APP_SURFACE = configuredSurface as ApplicationSurface;
export const isPlatformRuntime = APP_SURFACE === 'platform';

export function pathnameBelongsToRuntime(pathname: string): boolean {
  const isBlackboxPath = (
    pathname === '/blackbox'
    || pathname.startsWith('/blackbox/')
  );
  return isPlatformRuntime ? isBlackboxPath || pathname === '/' : !isBlackboxPath;
}

/**
 * V43 (11/09/2026): a recuperação de senha (`/forgot-password`, `/reset-password`)
 * só existe na superfície workspace. A tela de login é compartilhada, e na
 * Blackbox o link relativo caía em "Ambiente interno isolado". Aqui o link da
 * plataforma aponta para o app: por `VITE_WORKSPACE_APP_URL` quando o build
 * informa, senão pela convenção de host `blackbox.<domínio>` -> `app.<domínio>`.
 * Sem nenhum dos dois (dev local), continua relativo, sem inventar host.
 */
export function forgotPasswordHref(
  location: { host: string; protocol: string } = window.location,
): string {
  if (!isPlatformRuntime) return '/forgot-password';
  const configured = String(import.meta.env.VITE_WORKSPACE_APP_URL ?? '')
    .trim()
    .replace(/\/+$/, '');
  if (configured) return `${configured}/forgot-password`;
  if (/^blackbox\./i.test(location.host)) {
    const appHost = location.host.replace(/^blackbox\./i, 'app.');
    return `${location.protocol}//${appHost}/forgot-password`;
  }
  return '/forgot-password';
}
