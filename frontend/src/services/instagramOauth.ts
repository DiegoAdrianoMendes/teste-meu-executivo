import { apiFetch } from '@/services/http';

export type InstagramConnectionSummary = {
  id: string;
  accountName?: string | null;
  username?: string | null;
  status: string;
};

export type InstagramAuthorizeUrlResult = {
  authorizationUrl: string;
  state: string;
  expiresAt: string;
  scopes: string[];
};

export type InstagramTokenStatus = {
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

export async function fetchInstagramConnections(): Promise<InstagramConnectionSummary[]> {
  return apiFetch('/api/v1/instagram/connections');
}

export async function fetchInstagramAuthorizeUrl(
  connectionId: string,
  redirectUri: string,
): Promise<InstagramAuthorizeUrlResult> {
  const params = new URLSearchParams({ connection_id: connectionId, redirect_uri: redirectUri });
  return apiFetch(`/api/v1/instagram/oauth/authorize-url?${params.toString()}`);
}

export async function fetchInstagramTokenStatus(connectionId: string): Promise<InstagramTokenStatus> {
  return apiFetch(`/api/v1/instagram/oauth/token-status/${connectionId}`);
}

export async function completeInstagramOAuth(
  code: string,
  state: string,
): Promise<MetaOAuthCallbackResult> {
  return apiFetch('/api/v1/instagram/oauth/callback', {
    method: 'POST',
    body: JSON.stringify({ code, state }),
  });
}

export async function createInstagramConnection(payload: {
  accountName?: string;
  username?: string;
}): Promise<InstagramConnectionSummary> {
  return apiFetch('/api/v1/instagram/connections', {
    method: 'POST',
    body: JSON.stringify({
      accountName: payload.accountName,
      username: payload.username,
      scopes: [],
      status: 'pending',
      webhookRegistered: false,
    }),
  });
}
