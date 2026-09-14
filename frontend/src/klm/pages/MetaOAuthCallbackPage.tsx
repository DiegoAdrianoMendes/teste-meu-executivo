import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { KlmPanel } from '@/klm/components/Primitives';
import { completeInstagramOAuth } from '@/services/instagramOauth';
import { completeMessengerOAuth } from '@/services/messengerOauth';

type CallbackState =
  | { kind: 'loading'; message: string }
  | { kind: 'success'; message: string }
  | { kind: 'prepared'; message: string }
  | { kind: 'error'; message: string };

export default function MetaOAuthCallbackPage() {
  const { channel } = useParams<{ channel: string }>();
  const [params] = useSearchParams();
  const started = useRef(false);
  const [state, setState] = useState<CallbackState>({
    kind: 'loading',
    message: 'Concluindo a autorização com a Meta...',
  });

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    const providerError = params.get('error_description') || params.get('error');
    const code = params.get('code')?.trim() || '';
    const signedState = params.get('state')?.trim() || '';
    if (providerError) {
      setState({ kind: 'error', message: `A Meta não autorizou a conexão: ${providerError}` });
      return;
    }
    if (!code || !signedState || (channel !== 'instagram' && channel !== 'messenger')) {
      setState({
        kind: 'error',
        message: 'O retorno da Meta está incompleto ou não corresponde a um canal suportado.',
      });
      return;
    }

    const complete = channel === 'instagram' ? completeInstagramOAuth : completeMessengerOAuth;
    void complete(code, signedState)
      .then((result) => {
        setState({
          kind: result.status === 'connected' ? 'success' : 'prepared',
          message: result.message,
        });
      })
      .catch((error: unknown) => {
        setState({
          kind: 'error',
          message: error instanceof Error ? error.message : 'Não foi possível concluir a conexão Meta.',
        });
      });
  }, [channel, params]);

  const title = channel === 'instagram' ? 'Instagram' : 'Página do Facebook';
  return (
    <div className="mx-auto w-full max-w-2xl p-4 sm:p-8">
      <KlmPanel className="space-y-5 p-6">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Conexões</p>
          <h1 className="text-xl font-semibold text-foreground">Autorização {title}</h1>
        </div>

        {state.kind === 'loading' && (
          <div className="flex items-center gap-3 text-sm text-muted-foreground" role="status">
            <Loader2 className="h-5 w-5 animate-spin" />
            {state.message}
          </div>
        )}
        {state.kind === 'success' && (
          <Alert>
            <CheckCircle2 className="h-4 w-4" />
            <AlertTitle>Conexão concluída</AlertTitle>
            <AlertDescription>{state.message}</AlertDescription>
          </Alert>
        )}
        {state.kind === 'prepared' && (
          <Alert>
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Autorização preparada</AlertTitle>
            <AlertDescription>{state.message}</AlertDescription>
          </Alert>
        )}
        {state.kind === 'error' && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Não foi possível conectar</AlertTitle>
            <AlertDescription>{state.message}</AlertDescription>
          </Alert>
        )}

        {state.kind !== 'loading' && (
          <Button asChild>
            <Link to="/app/conexoes">Voltar para Conexões</Link>
          </Button>
        )}
      </KlmPanel>
    </div>
  );
}
