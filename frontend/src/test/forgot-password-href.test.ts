import { afterEach, describe, expect, it, vi } from 'vitest';

/**
 * V43: "Esqueceu a senha?" da Blackbox caía em "Ambiente interno isolado",
 * porque a recuperação só existe no app das contas e o link era relativo.
 */
async function loadSurface(surface: 'workspace' | 'platform', workspaceUrl = '') {
  vi.resetModules();
  vi.stubEnv('VITE_APP_SURFACE', surface);
  vi.stubEnv('VITE_WORKSPACE_APP_URL', workspaceUrl);
  return import('@/runtime-surface');
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('forgotPasswordHref (V43)', () => {
  it('no app das contas o link continua relativo', async () => {
    const surface = await loadSurface('workspace');
    expect(
      surface.forgotPasswordHref({ host: 'app.meuexecutivo.com', protocol: 'https:' }),
    ).toBe('/forgot-password');
  });

  it('na Blackbox aponta para o app pela convenção de host', async () => {
    const surface = await loadSurface('platform');
    expect(
      surface.forgotPasswordHref({ host: 'blackbox.meuexecutivo.com', protocol: 'https:' }),
    ).toBe('https://app.meuexecutivo.com/forgot-password');
  });

  it('na Blackbox a variável de build vence a convenção de host', async () => {
    const surface = await loadSurface('platform', 'https://app.staging.meuexecutivo.com/');
    expect(
      surface.forgotPasswordHref({ host: 'localhost:4174', protocol: 'http:' }),
    ).toBe('https://app.staging.meuexecutivo.com/forgot-password');
  });

  it('na Blackbox sem variável nem convenção fica relativo, sem inventar host', async () => {
    const surface = await loadSurface('platform');
    expect(
      surface.forgotPasswordHref({ host: 'localhost:4174', protocol: 'http:' }),
    ).toBe('/forgot-password');
  });
});
