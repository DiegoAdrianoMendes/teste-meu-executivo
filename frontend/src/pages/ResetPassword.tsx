import { useState } from 'react';
import { CheckCircle2, KeyRound, Loader2 } from 'lucide-react';
import { Link, useSearchParams } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { DesbLogo } from '@/components/ui/DesbLogo';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { passwordPolicyError, resetPassword } from '@/services/identity';

export default function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get('token') ?? '';
  const deliveryId = params.get('delivery') ?? params.get('deliveryId') ?? '';
  const [code, setCode] = useState(params.get('code') ?? '');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (password !== confirm) {
      setError('As senhas não coincidem.');
      return;
    }
    const policyError = passwordPolicyError(password);
    if (policyError) {
      setError(policyError);
      return;
    }
    if (!token && (!deliveryId || !code.trim())) {
      setError('Informe o código da entrega simulada local.');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await resetPassword(token
        ? { token, newPassword: password }
        : { deliveryId, code: code.trim(), newPassword: password });
      setDone(true);
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : 'Não foi possível redefinir a senha.');
    } finally {
      setLoading(false);
    }
  };

  if (!token && !deliveryId) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-4">
        <main className="w-full max-w-md space-y-4 rounded-lg border bg-card p-8 text-center">
          <KeyRound className="mx-auto h-9 w-9 text-muted-foreground" />
          <h1 className="text-2xl font-semibold">Redefinição indisponível</h1>
          <p className="text-sm text-muted-foreground">Solicite um novo link ou código de uso único.</p>
          <Button asChild className="w-full"><Link to="/forgot-password">Nova solicitação</Link></Button>
        </main>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <main className="w-full max-w-md rounded-lg border bg-card p-6 shadow-sm sm:p-8">
        <DesbLogo variant="full" size={44} className="mb-7" />
        {done ? (
          <div className="space-y-5 text-center">
            <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-600" />
            <div>
              <h1 className="text-2xl font-semibold">Senha redefinida</h1>
              <p className="mt-2 text-sm text-muted-foreground">Sessões anteriores foram revogadas. Entre novamente.</p>
            </div>
            <Button asChild className="w-full"><Link to="/login">Entrar</Link></Button>
          </div>
        ) : (
          <>
            <h1 className="text-2xl font-semibold">Definir nova senha</h1>
            <p className="mb-6 mt-2 text-sm text-muted-foreground">Use uma senha entre 10 e 72 bytes em UTF-8.</p>
            <form onSubmit={handleSubmit} className="space-y-4">
              {!token && (
                <div className="space-y-1.5">
                  <Label htmlFor="reset-code">Código local</Label>
                  <Input
                    id="reset-code"
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    className="font-mono"
                    maxLength={12}
                  />
                </div>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="new-password">Nova senha</Label>
                <Input
                  id="new-password"
                  type="password"
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="new-password"
                  placeholder="10 a 72 bytes em UTF-8"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="confirm-password">Confirmar nova senha</Label>
                <Input
                  id="confirm-password"
                  type="password"
                  required
                  value={confirm}
                  onChange={(event) => setConfirm(event.target.value)}
                  autoComplete="new-password"
                />
              </div>
              {error && <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</p>}
              <Button type="submit" disabled={loading} className="w-full">
                {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Redefinir senha
              </Button>
            </form>
          </>
        )}
      </main>
    </div>
  );
}
