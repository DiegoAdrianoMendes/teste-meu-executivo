import { useState } from 'react';
import { CheckCircle2, Loader2 } from 'lucide-react';
import { Link } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { DesbLogo } from '@/components/ui/DesbLogo';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { requestPasswordReset, type PublicIdentityRequestAccepted } from '@/services/identity';

export default function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [accepted, setAccepted] = useState<PublicIdentityRequestAccepted | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const result = await requestPasswordReset(email.trim());
      setAccepted(result);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Não foi possível registrar a solicitação.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <main className="w-full max-w-md rounded-lg border bg-card p-6 shadow-sm sm:p-8">
        <DesbLogo variant="full" size={44} className="mb-7" />
        {accepted ? (
          <div className="space-y-5">
            <div className="text-center">
              <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-600" />
              <h1 className="mt-3 text-2xl font-semibold">Solicitação aceita</h1>
              <p className="mt-2 text-sm text-muted-foreground">
                {accepted.deliveryMode === 'transactional_email'
                  ? 'Se a identidade existir e estiver apta, as instruções foram enviadas por e-mail.'
                  : 'Se a identidade existir e estiver apta, uma redefinição foi preparada no ambiente local autorizado.'}
              </p>
            </div>

            <Button asChild className="w-full" variant="outline"><Link to="/login">Voltar ao login</Link></Button>
          </div>
        ) : (
          <>
            <h1 className="text-2xl font-semibold">Redefinir senha</h1>
            <p className="mb-6 mt-2 text-sm text-muted-foreground">
              Informe o e-mail da identidade. A resposta é a mesma exista ou não uma conta correspondente.
            </p>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="reset-email">E-mail</Label>
                <Input
                  id="reset-email"
                  type="email"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  autoComplete="email"
                  placeholder="seu@email.com"
                />
              </div>
              {error && <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</p>}
              <Button type="submit" disabled={loading} className="w-full">
                {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Registrar solicitação
              </Button>
            </form>
            <Button asChild className="mt-3 w-full" variant="ghost"><Link to="/login">Voltar ao login</Link></Button>
          </>
        )}
      </main>
    </div>
  );
}
