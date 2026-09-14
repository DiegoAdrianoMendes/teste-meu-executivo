import { describe, expect, it } from 'vitest';
import { runtimeEnvironmentStatus } from '@/lib/brand';

describe('runtimeEnvironmentStatus', () => {
  it('distinguishes local, staging and production hosts without a misleading fallback', () => {
    expect(runtimeEnvironmentStatus('localhost')).toBe('Simulação local ativa');
    expect(runtimeEnvironmentStatus('staging-app.meuexecutivo.com')).toBe('Ambiente de homologação');
    expect(runtimeEnvironmentStatus('app.meuexecutivo.com')).toBe('Ambiente de produção');
  });
});
