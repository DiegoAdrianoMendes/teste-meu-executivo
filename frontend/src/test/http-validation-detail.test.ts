/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, apiFetch } from '@/services/http';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('apiFetch: erro de validação do FastAPI (V32)', () => {
  it('transforma a lista de erros do pydantic em mensagem com o campo recusado', async () => {
    const body = {
      detail: [
        { type: 'literal_error', loc: ['body', 'category'], msg: "Input should be 'utility', 'marketing' or 'authentication'" },
      ],
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body), {
      status: 422,
      headers: { 'Content-Type': 'application/json' },
    })));

    const failure = await apiFetch('/api/v1/whatsapp/meta-template-drafts/x', { method: 'PATCH', body: '{}' })
      .then(() => null, (error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(422);
    expect((failure as ApiError).message).toBe("category: Input should be 'utility', 'marketing' or 'authentication'");
  });

  it('mantém a mensagem do detail quando ele é um objeto com message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { code: 'WA-TPL-VALIDATION', message: 'Rascunho inválido.' } }), {
      status: 422,
      headers: { 'Content-Type': 'application/json' },
    })));

    const failure = await apiFetch('/api/v1/whatsapp/meta-template-drafts/x', { method: 'PATCH', body: '{}' })
      .then(() => null, (error: unknown) => error);

    expect((failure as ApiError).message).toBe('Rascunho inválido.');
  });
});
