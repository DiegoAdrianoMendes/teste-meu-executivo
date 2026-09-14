import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, KeyRound, Loader2, UserPlus } from 'lucide-react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { DesbLogo } from '@/components/ui/DesbLogo';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  acceptInvitation,
  createIdempotencyKey,
  passwordPolicyError,
  previewInvitation,
  readLocalOutbox,
  type InvitationAccepted,
  type InvitationPreview,
} from '@/services/identity';

export default function AcceptInvitation() {
  const { invitationId = '' } = useParams();
  const [params] = useSearchParams();
  const deliveryId = params.get('delivery') ?? params.get('deliveryId') ?? '';
  const [token, setToken] = useState(params.get('token') ?? '');
  const [preview, setPreview] = useState<InvitationPreview | null>(null);
  const [accepted, setAccepted] = useState<InvitationAccepted | null>(null);
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const idempotencyKey = useRef(createIdempotencyKey());

  useEffect(() => {
    let active = true;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        let invitationToken = token;
        if (!invitationToken && deliveryId) {
          const delivery = await readLocalOutbox(deliveryId);
          const link = new URL(delivery.link, window.location.origin);
          invitationToken = link.searchParams.get('token') ?? '';
          if (active) setToken(invitationToken);
        }
        if (!invitationId || !invitationToken) throw new Error('Convite inválido ou incompleto.');
        const result = await previewInvitation(invitationId, invitationToken);
        if (active) setPreview(result);
      } catch (loadError) {
        if (active) setError(loadError instanceof Error ? loadError.message : 'Não foi possível abrir o convite.');
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    return () => { active = false; };
  }, [deliveryId, invitationId, token]);

  const updateIdentity = (setter: (value: string) => void) => (event: React.ChangeEvent<HTMLInputElement>) => {
    idempotencyKey.current = createIdempotencyKey();
    setter(event.target.value);
  };

  const handleAccept = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!preview || !token) return;
    if (preview.requiresPassword) {
      const policyError = passwordPolicyError(password);
      if (policyError) {
        setError(policyError);
        return;
      }
      if (password !== confirmPassword) {
        setError('As senhas não coincidem.');
        return;
      }
    }
    setSubmitting(true);
    setError(null);
    try {
      const result = await acceptInvitation(
        invitationId,
        {
          token,
          ...(preview.requiresPassword ? { name: name.trim() || undefined, password } : {}),
        },
        idempotencyKey.current,
      );
      setAccepted(result);
    } catch (acceptError) {
      setError(acceptError instanceof Error ? acceptError.message : 'Não foi possível aceitar o convite.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <main className="w-full max-w-md rounded-lg border bg-card p-6 shadow-sm sm:p-8">
        <DesbLogo variant="full" size={44} className="mb-7" />
        {loading ? (
          <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />Validando convite...
          </div>
        ) : !preview ? (
          <div className="space-y-4 text-center">
            <p role="alert" className="text-sm text-destructive">{error ?? 'Não foi possível abrir o convite.'}</p>
            <Button asChild className="w-full" variant="outline"><Link to="/login">Voltar ao login</Link></Button>
          </div>
        ) : accepted || preview?.status === 'accepted' ? (
          <div className="space-y-5 text-center">
            <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-600" />
            <div>
              <h1 className="text-2xl font-semibold">Convite aceito</h1>
              <p className="mt-2 text-sm text-muted-foreground">
                Sua membership está ativa. Entre com sua identidade para acessar o workspace.
              </p>
            </div>
            <Button asChild className="w-full"><Link to="/login">Entrar</Link></Button>
          </div>
        ) : preview?.status !== 'pending' ? (
          <div className="space-y-5 text-center">
            <KeyRound className="mx-auto h-9 w-9 text-muted-foreground" />
            <div>
              <h1 className="text-2xl font-semibold">Convite indisponível</h1>
              <p className="mt-2 text-sm text-muted-foreground">
                Este convite foi revogado ou expirou. Solicite um novo envio ao administrador do workspace.
              </p>
            </div>
            <Button asChild className="w-full" variant="outline"><Link to="/login">Voltar ao login</Link></Button>
          </div>
        ) : (
          <form className="space-y-5" onSubmit={handleAccept}>
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/10">
                <UserPlus className="h-4 w-4 text-primary" />
              </div>
              <div>
                <h1 className="min-w-0 text-2xl font-semibold">Entrar em {preview.workspaceName}</h1>
                <p className="mt-1 text-sm text-muted-foreground">Convite destinado a {preview.recipientHint}.</p>
              </div>
            </div>

            {preview.requiresPassword && (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="invitation-name">Nome</Label>
                  <Input id="invitation-name" value={name} onChange={updateIdentity(setName)} autoComplete="name" />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="invitation-password">Defina sua senha</Label>
                  <Input
                    id="invitation-password"
                    type="password"
                    value={password}
                    onChange={updateIdentity(setPassword)}
                    autoComplete="new-password"
                    placeholder="10 a 72 bytes em UTF-8"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="invitation-password-confirmation">Confirmar senha</Label>
                  <Input
                    id="invitation-password-confirmation"
                    type="password"
                    value={confirmPassword}
                    onChange={updateIdentity(setConfirmPassword)}
                    autoComplete="new-password"
                  />
                </div>
              </>
            )}

            {!preview.requiresPassword && (
              <p className="rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground">
                O convite será vinculado à sua identidade existente. Sua senha e seus dados globais não serão alterados.
              </p>
            )}

            {error && <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</p>}
            <Button className="w-full" type="submit" disabled={submitting}>
              {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Aceitar convite
            </Button>
          </form>
        )}

      </main>
    </div>
  );
}
