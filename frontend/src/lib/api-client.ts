/**
 * API client simples para páginas públicas (checkout, landing).
 * Wraps fetch nativo com interface axios-compatible.
 */
const API_BASE = import.meta.env.VITE_API_URL || '';

type ApiClientError = Error & {
  response?: {
    data: unknown;
    status: number;
  };
};

export const apiClient = {
  async post<T = unknown>(path: string, body: unknown): Promise<{ data: T }> {
    const response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    const data = await response.json().catch(() => null);

    if (!response.ok) {
      const err = new Error(
        data?.detail?.message ?? data?.detail ?? 'Erro na requisição.'
      ) as ApiClientError;
      err.response = { data, status: response.status };
      throw err;
    }

    return { data: data as T };
  },
};
