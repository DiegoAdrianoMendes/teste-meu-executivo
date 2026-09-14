import {
  expireClientSession,
  getToken,
  recoverAccessToken,
  saveAdministrativeSessionToken,
  SESSION_EXPIRED_MESSAGE,
} from '@/services/auth';
import { API_BASE } from '@/services/api-origin';
import {
  clearSupportModeCredential,
  registerSupportModeRequest,
  resolveSupportRequest,
  type SupportModeRequestLease,
  type SupportRequestDecision,
} from '@/support-mode/session';

interface ApiErrorEnvelope {
  detail?: unknown;
}

const HTTP_REQUEST_TIMEOUT_MS = 15_000;
const HTTP_MAX_ATTEMPTS = 3;
const HTTP_RETRY_BASE_MS = 250;
const IDEMPOTENT_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'PUT', 'DELETE']);
const TRANSIENT_HTTP_STATUSES = new Set([408, 425, 429, 500, 502, 503, 504]);
const ADMINISTRATIVE_STEP_UP_ERROR_CODES = new Set([
  'AUTH-018',
  'SUPPORT-ROOT-STEP-UP-REQUIRED',
  'SUPPORT-ROOT-STEP-UP-STALE',
]);

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

export class SupportModeRouteDeniedError extends ApiError {
  constructor(requiredScope: string | null) {
    super(
      403,
      requiredScope
        ? `O acesso temporário não inclui o escopo ${requiredScope}.`
        : 'Esta rota não pode ser usada durante o acesso temporário de suporte.',
      { code: 'SUPPORT-CLIENT-ROUTE-DENIED', requiredScope },
    );
    this.name = 'SupportModeRouteDeniedError';
  }
}

export class ApiRequestTimeoutError extends Error {
  readonly timeoutMs: number;

  constructor(timeoutMs: number) {
    super(`A requisição excedeu o limite de ${timeoutMs} ms.`);
    this.name = 'ApiRequestTimeoutError';
    this.timeoutMs = timeoutMs;
  }
}

function detailCode(detail: unknown): string | null {
  if (!detail || typeof detail !== 'object' || !('code' in detail)) return null;
  return typeof detail.code === 'string' ? detail.code : null;
}

function isAdministrativeStepUpError(detail: unknown): boolean {
  const code = detailCode(detail);
  return code !== null && ADMINISTRATIVE_STEP_UP_ERROR_CODES.has(code);
}

function detailMessage(detail: unknown, fallback: string): string {
  if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') {
    return detail.message;
  }
  // V32: erro de validação do FastAPI chega como lista [{ loc, msg }]; antes virava
  // "Erro ao processar requisição" e o usuário não sabia qual campo recusou.
  if (Array.isArray(detail) && detail.length) {
    const parts = detail
      .map((item) => {
        if (!item || typeof item !== 'object') return null;
        const record = item as { loc?: unknown; msg?: unknown };
        const msg = typeof record.msg === 'string' ? record.msg : null;
        if (!msg) return null;
        const loc = Array.isArray(record.loc)
          ? record.loc.filter((part) => typeof part === 'string' && part !== 'body' && part !== 'query').join('.')
          : '';
        return loc ? `${loc}: ${msg}` : msg;
      })
      .filter((part): part is string => Boolean(part));
    if (parts.length) return parts.join('; ');
  }
  return typeof detail === 'string' && detail ? detail : fallback;
}

async function readError(response: Response): Promise<ApiErrorEnvelope> {
  return response.json().catch(() => ({})) as Promise<ApiErrorEnvelope>;
}

function requestHeaders(
  init: RequestInit | undefined,
  token: string | null,
  includeJsonContentType: boolean,
): Headers {
  const headers = new Headers(init?.headers);
  headers.delete('Authorization');
  if (includeJsonContentType && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (token) headers.set('Authorization', `Bearer ${token}`);
  return headers;
}

interface ActiveResponse {
  response: Response;
  assertActive: () => void;
  release: () => void;
}

interface ResponseGuard {
  assertActive: () => void;
  release: () => void;
}

function abortReason(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException('A requisição foi cancelada.', 'AbortError');
}

function throwIfAborted(signal?: AbortSignal | null): void {
  if (signal?.aborted) throw abortReason(signal);
}

function createAttemptSignal(parentSignal?: AbortSignal | null): ResponseGuard & { signal: AbortSignal } {
  const controller = new AbortController();
  let timeoutError: ApiRequestTimeoutError | null = null;
  let released = false;
  const abortFromParent = () => {
    if (!controller.signal.aborted) controller.abort(abortReason(parentSignal!));
  };

  if (parentSignal?.aborted) abortFromParent();
  else parentSignal?.addEventListener('abort', abortFromParent, { once: true });

  const timeout = setTimeout(() => {
    if (controller.signal.aborted) return;
    timeoutError = new ApiRequestTimeoutError(HTTP_REQUEST_TIMEOUT_MS);
    controller.abort(timeoutError);
  }, HTTP_REQUEST_TIMEOUT_MS);

  return {
    signal: controller.signal,
    assertActive: () => {
      throwIfAborted(parentSignal);
      if (timeoutError) throw timeoutError;
    },
    release: () => {
      if (released) return;
      released = true;
      clearTimeout(timeout);
      parentSignal?.removeEventListener('abort', abortFromParent);
    },
  };
}

function passiveResponseGuard(signal?: AbortSignal | null): ResponseGuard {
  return {
    assertActive: () => throwIfAborted(signal),
    release: () => undefined,
  };
}

function isRetryableRequest(init?: RequestInit): boolean {
  if (typeof ReadableStream !== 'undefined' && init?.body instanceof ReadableStream) return false;
  const method = (init?.method ?? 'GET').toUpperCase();
  return IDEMPOTENT_METHODS.has(method) || new Headers(init?.headers).has('Idempotency-Key');
}

function isTransientFetchError(error: unknown): boolean {
  return error instanceof ApiRequestTimeoutError
    || error instanceof TypeError
    || (error instanceof DOMException && error.name === 'NetworkError');
}

function retryDelay(attempt: number): number {
  return HTTP_RETRY_BASE_MS * (2 ** attempt);
}

function waitForRetry(delay: number, signal?: AbortSignal | null): Promise<void> {
  throwIfAborted(signal);
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      signal?.removeEventListener('abort', handleAbort);
      resolve();
    }, delay);
    const handleAbort = () => {
      clearTimeout(timeout);
      signal?.removeEventListener('abort', handleAbort);
      reject(abortReason(signal!));
    };
    signal?.addEventListener('abort', handleAbort, { once: true });
  });
}

function awaitWithSignal<T>(promise: Promise<T>, signal?: AbortSignal | null): Promise<T> {
  throwIfAborted(signal);
  if (!signal) return promise;
  return new Promise((resolve, reject) => {
    const handleAbort = () => {
      signal.removeEventListener('abort', handleAbort);
      reject(abortReason(signal));
    };
    signal.addEventListener('abort', handleAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener('abort', handleAbort);
        resolve(value);
      },
      (error) => {
        signal.removeEventListener('abort', handleAbort);
        reject(error);
      },
    );
  });
}

function discardResponse(response: Response): void {
  try {
    void response.body?.cancel().catch(() => undefined);
  } catch {
    // Some fetch implementations expose an already-consumed body.
  }
}

async function requestWithRetry(
  path: string,
  init: RequestInit | undefined,
  headers: Headers,
  signal?: AbortSignal | null,
): Promise<ActiveResponse> {
  const retryable = isRetryableRequest(init);
  const maxAttempts = retryable ? HTTP_MAX_ATTEMPTS : 1;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const attemptGuard = createAttemptSignal(signal);
    try {
      const response = await fetch(`${API_BASE}${path}`, {
        ...init,
        credentials: init?.credentials ?? 'include',
        headers,
        signal: attemptGuard.signal,
      });
      attemptGuard.assertActive();
      const shouldRetry = TRANSIENT_HTTP_STATUSES.has(response.status) && attempt + 1 < maxAttempts;
      if (!shouldRetry) {
        return { response, assertActive: attemptGuard.assertActive, release: attemptGuard.release };
      }
      discardResponse(response);
    } catch (error) {
      let requestError = error;
      try {
        attemptGuard.assertActive();
      } catch (guardError) {
        requestError = guardError;
      }
      attemptGuard.release();
      throwIfAborted(signal);
      if (!isTransientFetchError(requestError) || attempt + 1 >= maxAttempts) throw requestError;
      await waitForRetry(retryDelay(attempt), signal);
      continue;
    }

    attemptGuard.release();
    await waitForRetry(retryDelay(attempt), signal);
  }

  throw new Error('A requisição não produziu resposta.');
}

async function authenticatedRequest(
  path: string,
  init: RequestInit | undefined,
  includeJsonContentType: boolean,
): Promise<{
  response: Response;
  decision: SupportRequestDecision;
  error: ApiErrorEnvelope | null;
  supportLease: SupportModeRequestLease | null;
  responseGuard: ResponseGuard;
}> {
  const method = init?.method ?? 'GET';
  const decision = resolveSupportRequest(path, method);
  if (decision.kind === 'blocked') {
    throw new SupportModeRouteDeniedError(decision.requiredScope);
  }
  const supportRequest = decision.kind === 'support';
  const supportLease = decision.kind === 'support'
    ? registerSupportModeRequest(decision.identity, init?.signal)
    : null;
  const requestSignal = supportLease?.signal ?? init?.signal;
  const doRequest = (token: string | null) => requestWithRetry(
    path,
    init,
    requestHeaders(init, token, includeJsonContentType),
    requestSignal,
  );
  let activeResponse: ActiveResponse | null = null;

  try {
    activeResponse = await doRequest(supportRequest ? decision.token : getToken());
    let response = activeResponse.response;
    supportLease?.assertCurrent();
    let error = response.ok ? null : await readError(response);
    activeResponse.assertActive();
    supportLease?.assertCurrent();
    if (!response.ok) {
      activeResponse.release();
      activeResponse = null;
    }

    if (response.status === 401 && supportRequest) {
      clearSupportModeCredential('unauthorized');
      return {
        response,
        decision,
        error,
        supportLease,
        responseGuard: passiveResponseGuard(requestSignal),
      };
    }

    if (
      (response.status === 401 || response.status === 403)
      && isAdministrativeStepUpError(error?.detail)
    ) {
      saveAdministrativeSessionToken(null);
      return {
        response,
        decision,
        error,
        supportLease,
        responseGuard: passiveResponseGuard(requestSignal),
      };
    }

    if (response.status === 401) {
      const recoveredToken = await awaitWithSignal(
        recoverAccessToken({ forceRefresh: true }),
        requestSignal,
      );
      if (recoveredToken) {
        activeResponse = await doRequest(recoveredToken);
        response = activeResponse.response;
        error = response.ok ? null : await readError(response);
        activeResponse.assertActive();
        if (!response.ok) {
          activeResponse.release();
          activeResponse = null;
        }
        if (
          (response.status === 401 || response.status === 403)
          && isAdministrativeStepUpError(error?.detail)
        ) {
          saveAdministrativeSessionToken(null);
        }
      }
    }

    return {
      response,
      decision,
      error,
      supportLease,
      responseGuard: activeResponse ?? passiveResponseGuard(requestSignal),
    };
  } catch (error) {
    activeResponse?.release();
    try {
      supportLease?.assertCurrent();
    } finally {
      supportLease?.release();
    }
    throw error;
  }
}

function throwResponseError(
  response: Response,
  error: ApiErrorEnvelope | null,
  fallback: string,
  decision: SupportRequestDecision,
): never {
  const detail = error?.detail;
  const isStepUpFailure = isAdministrativeStepUpError(detail);
  if (response.status === 401 && decision.kind !== 'support' && !isStepUpFailure) {
    expireClientSession();
    throw new ApiError(response.status, SESSION_EXPIRED_MESSAGE, detail);
  }
  throw new ApiError(response.status, detailMessage(detail, fallback), detail);
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const { response, decision, error, supportLease, responseGuard } = await authenticatedRequest(path, init, true);
  try {
    supportLease?.assertCurrent();
    responseGuard.assertActive();
    if (!response.ok) {
      throwResponseError(response, error, 'Erro ao processar requisição.', decision);
    }
    if (response.status === 204) return undefined as T;
    const result = await response.json() as T;
    supportLease?.assertCurrent();
    responseGuard.assertActive();
    return result;
  } finally {
    responseGuard.release();
    supportLease?.release();
  }
}

/**
 * Upload multipart (`FormData`) autenticado — usado por importações de
 * arquivo (ex.: extrato OFX/CSV da conciliação bancária, F7). Nunca define
 * `Content-Type` manualmente: o navegador precisa gerar o boundary do
 * multipart sozinho, por isso `includeJsonContentType=false` aqui.
 */
export async function apiUpload<T>(path: string, formData: FormData): Promise<T> {
  const { response, decision, error, supportLease, responseGuard } = await authenticatedRequest(
    path,
    { method: 'POST', body: formData },
    false,
  );
  try {
    supportLease?.assertCurrent();
    responseGuard.assertActive();
    if (!response.ok) {
      throwResponseError(response, error, 'Erro ao enviar arquivo.', decision);
    }
    if (response.status === 204) return undefined as T;
    const result = await response.json() as T;
    supportLease?.assertCurrent();
    responseGuard.assertActive();
    return result;
  } finally {
    responseGuard.release();
    supportLease?.release();
  }
}

export interface ApiBlobResult {
  blob: Blob;
  filename: string | null;
  truncated: boolean;
}

export async function apiBlob(path: string, init?: RequestInit): Promise<ApiBlobResult> {
  const { response, decision, error, supportLease, responseGuard } = await authenticatedRequest(
    path,
    init,
    false,
  );
  try {
    supportLease?.assertCurrent();
    responseGuard.assertActive();
    if (!response.ok) {
      throwResponseError(response, error, 'Erro ao baixar arquivo.', decision);
    }
    const disposition = response.headers.get('Content-Disposition') ?? '';
    const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? null;
    const blob = await response.blob();
    supportLease?.assertCurrent();
    responseGuard.assertActive();
    return {
      blob,
      filename,
      truncated: response.headers.get('X-Result-Truncated') === 'true',
    };
  } finally {
    responseGuard.release();
    supportLease?.release();
  }
}

export async function apiDownload(path: string, init?: RequestInit): Promise<Blob> {
  const { response, decision, error, supportLease, responseGuard } = await authenticatedRequest(path, init, false);
  try {
    supportLease?.assertCurrent();
    responseGuard.assertActive();
    if (!response.ok) {
      throwResponseError(response, error, 'Erro ao baixar arquivo.', decision);
    }
    const result = await response.blob();
    supportLease?.assertCurrent();
    responseGuard.assertActive();
    return result;
  } finally {
    responseGuard.release();
    supportLease?.release();
  }
}

export async function fetchWebSocketTicket(): Promise<{ ticket: string; expiresIn: number }> {
  return apiFetch<{ ticket: string; expiresIn: number }>('/api/v1/auth/ws-ticket', {
    method: 'POST',
  });
}

export { API_BASE };
