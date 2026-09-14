import { useCallback, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2, PlugZap } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/use-toast';
import { requireMetaGraphApiVersion } from '@/lib/meta-graph-version';
import { fetchMetaEmbeddedSignupConfig } from '@/services/whatsapp';

declare global {
  interface Window {
    FB?: {
      init: (opts: object) => void;
      login: (cb: (response: { authResponse?: { code: string } }) => void, opts: object) => void;
    };
    fbAsyncInit?: () => void;
  }
}

export interface MetaEmbeddedSignupResult {
  code: string;
  wabaId?: string;
  phoneNumberId?: string;
  transportMode: 'coexistence';
}

interface Props {
  onComplete: (result: MetaEmbeddedSignupResult) => Promise<void> | void;
  disabled?: boolean;
  label?: string;
}

function isFacebookOrigin(origin: string): boolean {
  try {
    const url = new URL(origin);
    return url.protocol === 'https:' && (url.hostname === 'facebook.com' || url.hostname.endsWith('.facebook.com'));
  } catch {
    return false;
  }
}

function loadFacebookSdk(appId: string, version: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const initialize = () => {
      if (!window.FB) {
        reject(new Error('O SDK da Meta não ficou disponível.'));
        return;
      }
      window.FB.init({ appId, cookie: true, xfbml: false, version });
      resolve();
    };
    if (window.FB) {
      initialize();
      return;
    }
    window.fbAsyncInit = initialize;
    if (!document.getElementById('facebook-jssdk')) {
      const script = document.createElement('script');
      script.id = 'facebook-jssdk';
      script.src = 'https://connect.facebook.net/pt_BR/sdk.js';
      script.async = true;
      script.defer = true;
      script.onerror = () => reject(new Error('Não foi possível carregar o SDK da Meta.'));
      document.body.appendChild(script);
    }
  });
}

export function MetaEmbeddedSignupButton({ onComplete, disabled, label = 'Conectar WhatsApp existente' }: Props) {
  const { toast } = useToast();
  const [loading, setLoading] = useState(false);
  const listenerRef = useRef<((event: MessageEvent) => void) | null>(null);
  const configQuery = useQuery({
    queryKey: ['meta-embedded-signup-config'],
    queryFn: fetchMetaEmbeddedSignupConfig,
    staleTime: 60_000,
  });

  const start = useCallback(async () => {
    const config = configQuery.data;
    if (!config?.available || !config.appId || !config.configId) {
      toast({
        variant: 'destructive',
        title: 'Embedded Signup indisponível',
        description: 'A configuração pública da Meta ainda não está completa neste ambiente.',
      });
      return;
    }
    setLoading(true);
    let wabaId: string | undefined;
    let phoneNumberId: string | undefined;
    try {
      await loadFacebookSdk(config.appId, requireMetaGraphApiVersion(config.graphApiVersion));
      if (listenerRef.current) window.removeEventListener('message', listenerRef.current);
      const listener = (event: MessageEvent) => {
        if (!isFacebookOrigin(event.origin)) return;
        try {
          const data = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
          if (data?.type !== 'WA_EMBEDDED_SIGNUP') return;
          if (data.event === 'FINISH' || data.event === 'FINISH_ONLY_WABA') {
            wabaId = data.data?.waba_id || undefined;
            phoneNumberId = data.data?.phone_number_id || undefined;
          }
        } catch {
          // Mensagens de outras superfícies do SDK não pertencem a este fluxo.
        }
      };
      listenerRef.current = listener;
      window.addEventListener('message', listener);
      const extras: Record<string, unknown> = {
        featureType: config.featureType,
        sessionInfoVersion: '3',
      };
      if (config.solutionId) extras.setup = { solutionID: config.solutionId };
      window.FB!.login(async (response) => {
        window.removeEventListener('message', listener);
        listenerRef.current = null;
        if (!response.authResponse?.code) {
          setLoading(false);
          toast({ variant: 'destructive', title: 'Conexão cancelada ou não autorizada.' });
          return;
        }
        try {
          await onComplete({
            code: response.authResponse.code,
            wabaId,
            phoneNumberId,
            transportMode: 'coexistence',
          });
        } finally {
          setLoading(false);
        }
      }, {
        config_id: config.configId,
        response_type: 'code',
        override_default_response_type: true,
        extras,
      });
    } catch (error) {
      setLoading(false);
      toast({
        variant: 'destructive',
        title: 'Não foi possível abrir a Meta',
        description: error instanceof Error ? error.message : 'Falha inesperada.',
      });
    }
  }, [configQuery.data, onComplete, toast]);

  const unavailable = configQuery.isError || (configQuery.data && !configQuery.data.available);
  return (
    <div className="space-y-2">
      <Button type="button" onClick={start} disabled={disabled || loading || configQuery.isLoading || unavailable}>
        {loading || configQuery.isLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <PlugZap className="mr-2 h-4 w-4" />}
        {label}
      </Button>
      {unavailable && (
        <p className="text-xs text-destructive">
          Embedded Signup não configurado neste ambiente. A conexão manual continua disponível.
        </p>
      )}
    </div>
  );
}
