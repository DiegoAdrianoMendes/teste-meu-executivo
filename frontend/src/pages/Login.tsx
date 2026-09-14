import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAppStore } from '@/store/app-context';
import { ArrowLeft, Eye, EyeOff, AlertCircle, Bot, KeyRound, Sparkles } from 'lucide-react';
import { DesbLogo } from '@/components/ui/DesbLogo';
import { useNavigate, Link } from 'react-router-dom';
import { BRAND, runtimeEnvironmentStatus } from '@/lib/brand';
import { forgotPasswordHref } from '@/runtime-surface';
import {
  extractRefreshToken,
  extractToken,
  bootstrapLocalSession,
  consumeSessionExpiredNotice,
  isMfaLoginChallenge,
  loginWithCredentials,
  saveRefreshToken,
  saveSession,
  saveToken,
  saveUser,
  verifyMfaLogin,
  type LoginResponse,
  type AuthAudience,
  type MfaLoginChallengeResponse,
} from '@/services/auth';

export default function Login({ audience = 'workspace' }: { audience?: AuthAudience }) {
  const { login } = useAppStore();
  const navigate = useNavigate();
  const forgotHref = forgotPasswordHref();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [mfaChallenge, setMfaChallenge] = useState<MfaLoginChallengeResponse | null>(null);
  const [mfaCode, setMfaCode] = useState('');
  const [useRecoveryCode, setUseRecoveryCode] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessionNotice] = useState(() => consumeSessionExpiredNotice());

  const completeAuthentication = useCallback((data: LoginResponse) => {
    const token = extractToken(data);
    if (!token) throw new Error('A autenticação não retornou uma sessão válida.');
    saveToken(token);
    saveRefreshToken(extractRefreshToken(data));
    saveUser(data.user);
    saveSession(data.session);
    login({
      id: data.user.id,
      name: data.user.name,
      email: data.user.email,
      role: data.user.role,
      initials: data.user.initials,
      isInternalStaff: data.user.isInternalStaff,
      isPlatformAdmin: data.user.isPlatformAdmin,
      platformRolePackage: data.user.platformRolePackage,
      platformAccessActive: data.user.platformAccessActive,
      platformPermissions: data.user.platformPermissions,
      requiresPasswordSetup: data.user.requiresPasswordSetup,
      requiresPasswordChange: data.user.requiresPasswordChange,
      mustChangePassword: data.user.mustChangePassword,
    }, data.session);
    navigate(data.session.surface === 'platform' ? '/blackbox' : (data.session.activeWorkspace ? '/app' : '/workspace/select'));
  }, [login, navigate]);

  useEffect(() => {
    const isLocalRuntime = import.meta.env.VITE_KLM_RUNTIME_MODE === 'local_simulation';
    if (!isLocalRuntime || import.meta.env.MODE === 'test') return;

    let active = true;
    setLoading(true);
    void bootstrapLocalSession(audience)
      .then((data) => {
        if (active) completeAuthentication(data);
      })
      .catch((err: unknown) => {
        if (active) {
          setError(err instanceof Error ? err.message : 'A sessão local não pôde ser iniciada.');
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [audience, completeAuthentication]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!email || !password) { setError('Preencha e-mail e senha.'); return; }
    setLoading(true);
    try {
      const data = await loginWithCredentials(email, password, audience);
      if (isMfaLoginChallenge(data)) {
        setMfaChallenge(data);
        setMfaCode(data.simulatedCode ?? '');
        return;
      }
      completeAuthentication(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Erro ao fazer login');
    } finally {
      setLoading(false);
    }
  };

  const handleMfaSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!mfaChallenge || !mfaCode.trim()) {
      setError(useRecoveryCode ? 'Informe um código de recuperação.' : 'Informe o código de verificação.');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await verifyMfaLogin(
        mfaChallenge.challengeId,
        useRecoveryCode ? { recoveryCode: mfaCode.trim() } : { code: mfaCode.trim() },
      );
      completeAuthentication(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Código de verificação inválido.');
    } finally {
      setLoading(false);
    }
  };

  const resetMfa = () => {
    setMfaChallenge(null);
    setMfaCode('');
    setUseRecoveryCode(false);
    setError(null);
  };

  return (
    <div className="min-h-screen flex bg-background">
      {/* Left — Branding */}
      <div className="hidden lg:flex flex-1 items-center justify-center bg-[hsl(var(--sidebar-background))] relative overflow-hidden">
        <div className="absolute inset-0 opacity-20">
          <div className="absolute inset-0 bg-[linear-gradient(rgba(208,232,245,0.08)_1px,transparent_1px),linear-gradient(90deg,rgba(208,232,245,0.08)_1px,transparent_1px)] bg-[size:34px_34px]" />
          <div className="absolute left-1/2 top-1/2 h-[520px] w-[520px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(circle,rgba(57,208,200,0.34),transparent_62%)]" />
        </div>
        <div className="relative z-10 text-center space-y-6 px-12 max-w-lg">
          <div className="flex items-center justify-center gap-3 mb-8">
            <DesbLogo variant="icon" size={64} dark />
          </div>
          <div className="inline-flex items-center gap-2 rounded-full border border-primary-foreground/15 bg-primary-foreground/10 px-3 py-1.5 text-xs font-medium text-primary-foreground/75">
            <Bot className="h-3.5 w-3.5 text-[hsl(var(--ai))]" />
            {runtimeEnvironmentStatus()}
          </div>
          <h1 className="text-2xl font-semibold text-primary-foreground tracking-tight">
            {BRAND.name} <span className="text-primary">{BRAND.tagline}</span>
          </h1>
          <p className="text-sm text-primary-foreground/60 leading-relaxed">
            {BRAND.description}
          </p>
          <div className="flex items-center justify-center gap-6 pt-4 text-primary-foreground/45 text-xs">
            <span className="inline-flex items-center gap-1.5">
              <Sparkles className="h-3 w-3 text-[hsl(var(--ai))]" />
              Co-piloto IA
            </span>
            <span className="w-1 h-1 rounded-full bg-primary-foreground/30" />
            <span>WhatsApp oficial</span>
            <span className="w-1 h-1 rounded-full bg-primary-foreground/30" />
            <span>CRM assistido</span>
          </div>
        </div>
      </div>

      {/* Right — Login Form */}
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-sm space-y-8">
          <div className="lg:hidden flex items-center gap-3 justify-center mb-4">
            <DesbLogo variant="icon" size={40} />
            <div>
              <span className="text-lg font-semibold text-primary">{BRAND.name}</span>
              <span className="text-xs text-muted-foreground block -mt-0.5">{BRAND.tagline}</span>
            </div>
          </div>

          <div className="space-y-2 text-center lg:text-left">
            <h2 className="text-lg font-semibold tracking-tight">
            {mfaChallenge ? 'Verificação em duas etapas' : audience === 'platform' ? 'Entrar na Blackbox' : 'Entrar'}
            </h2>
            <p className="text-sm text-muted-foreground">
              {mfaChallenge
                ? 'Confirme sua identidade para concluir o acesso.'
                : audience === 'platform'
                  ? 'Acesso exclusivo da equipe Meu Executivo'
                  : 'Acesse sua conta para continuar'}
            </p>
          </div>

          <form onSubmit={mfaChallenge ? handleMfaSubmit : handleSubmit} className="space-y-4">
            {(error || sessionNotice) && (
              <div role="alert" className="flex items-center gap-2 p-3 rounded-lg bg-destructive/10 text-destructive text-sm">
                <AlertCircle className="h-4 w-4 shrink-0" />
                <span>{error || sessionNotice}</span>
              </div>
            )}
            {!mfaChallenge ? (
              <>
                <div className="space-y-2">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">E-mail</label>
                  <Input
                    type="email"
                    placeholder="seu@email.com"
                    value={email}
                    onChange={e => setEmail(e.target.value)}
                    className="h-11"
                    autoFocus
                    disabled={loading}
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Senha</label>
                  <div className="relative">
                    <Input
                      type={showPassword ? 'text' : 'password'}
                      placeholder="••••••••"
                      value={password}
                      onChange={e => setPassword(e.target.value)}
                      className="h-11 pr-10"
                      disabled={loading}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                      aria-label={showPassword ? 'Ocultar senha' : 'Mostrar senha'}
                    >
                      {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>
              </>
            ) : (
              <div className="space-y-4">
                <div className="flex items-start gap-3 rounded-md border bg-muted/30 p-3">
                  <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                  <div className="min-w-0 text-xs text-muted-foreground">
                    <p className="font-medium text-foreground">{email}</p>
                    <p className="mt-1">
                      {mfaChallenge.attemptsRemaining} tentativa(s) restante(s) · expira em {Math.ceil(mfaChallenge.expiresIn / 60)} min
                    </p>
                  </div>
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                    {useRecoveryCode ? 'Código de recuperação' : 'Código de verificação'}
                  </label>
                  <Input
                    value={mfaCode}
                    onChange={(event) => setMfaCode(event.target.value)}
                    placeholder={useRecoveryCode ? 'XXXX-XXXX-XXXX' : '000000'}
                    autoComplete="one-time-code"
                    inputMode={useRecoveryCode ? 'text' : 'numeric'}
                    className="h-11 font-mono"
                    autoFocus
                    disabled={loading}
                  />
                </div>
                {mfaChallenge.simulatedCode && !useRecoveryCode && (
                  <div className="rounded-md border border-primary/20 bg-primary/5 px-3 py-2 text-xs">
                    Código do simulador local: <code className="font-semibold">{mfaChallenge.simulatedCode}</code>
                  </div>
                )}
              </div>
            )}

            <Button type="submit" className="w-full h-11 font-semibold" disabled={loading}>
              {loading ? (
                <span className="flex items-center gap-2">
                  <span className="h-4 w-4 border-2 border-primary-foreground/30 border-t-primary-foreground rounded-full animate-spin" />
                  {mfaChallenge ? 'Verificando...' : 'Entrando...'}
                </span>
              ) : (
                mfaChallenge ? 'Confirmar código' : 'Entrar'
              )}
            </Button>
            {!mfaChallenge ? (
              <div className="text-center">
                {forgotHref.startsWith('/') ? (
                  <Link to={forgotHref} className="text-xs text-muted-foreground hover:text-foreground transition-colors">
                    Esqueceu a senha?
                  </Link>
                ) : (
                  // Blackbox: a recuperação vive no app das contas (V43)
                  <a href={forgotHref} className="text-xs text-muted-foreground hover:text-foreground transition-colors">
                    Esqueceu a senha?
                  </a>
                )}
              </div>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Button type="button" variant="ghost" size="sm" onClick={resetMfa}>
                  <ArrowLeft className="mr-1.5 h-3.5 w-3.5" />Voltar
                </Button>
                {mfaChallenge.recoveryAvailable && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => { setUseRecoveryCode((current) => !current); setMfaCode(''); setError(null); }}
                  >
                    {useRecoveryCode ? 'Usar código temporário' : 'Usar recuperação'}
                  </Button>
                )}
              </div>
            )}
          </form>

          <p className="text-center text-2xs text-muted-foreground/50">
            v{BRAND.version} — {BRAND.full}
          </p>
        </div>
      </div>
    </div>
  );
}
