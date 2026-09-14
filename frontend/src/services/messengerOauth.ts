import { apiFetch } from '@/services/http';

export type MessengerConnectionSummary = {
  id: string;
  accountName?: string | null;
  pageId?: string | null;
  status: string;
};

export type MessengerAuthorizeUrlResult = {
  authorizationUrl: string;
  state: string;
  expiresAt: string;
  scopes: string[];
};

export type MessengerTokenStatus = {
  connectionId: string;
  state: 'configured' | 'expiring' | 'oauth_prepared' | 'not_connected';
  expiresInDays: number | null;
  oauthStatus: string | null;
  scopes: string[];
};

export type MetaOAuthCallbackResult = {
  status: 'connected' | 'oauth_prepared';
  connectionId: string | null;
  message: string;
};

export async function fetchMessengerConnections(): Promise<MessengerConnectionSummary[]> {
  return apiFetch('/api/v1/messenger/connections');
}

export async function fetchMessengerAuthorizeUrl(
  connectionId: string,
  redirectUri: string,
): Promise<MessengerAuthorizeUrlResult> {
  const params = new URLSearchParams({ connection_id: connectionId, redirect_uri: redirectUri });
  return apiFetch(`/api/v1/messenger/oauth/authorize-url?${params.toString()}`);
}

export async function fetchMessengerTokenStatus(connectionId: string): Promise<MessengerTokenStatus> {
  return apiFetch(`/api/v1/messenger/oauth/token-status/${connectionId}`);
}

export async function completeMessengerOAuth(
  code: string,
  state: string,
): Promise<MetaOAuthCallbackResult> {
  return apiFetch('/api/v1/messenger/oauth/callback', {
    method: 'POST',
    body: JSON.stringify({ code, state }),
  });
}

export async function createMessengerConnection(payload: {
  accountName?: string;
  pageId?: string;
}): Promise<MessengerConnectionSummary> {
  return apiFetch('/api/v1/messenger/connections', {
    method: 'POST',
    body: JSON.stringify({
      accountName: payload.accountName,
      pageId: payload.pageId,
      scopes: [],
      status: 'pending',
      webhookRegistered: false,
    }),
  });
}
