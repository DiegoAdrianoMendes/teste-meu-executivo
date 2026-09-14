import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { Suspense, lazy, useEffect, useRef } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";

import { canOpenBlackboxPath } from "@/blackbox/access";
import { BlackboxManifestProvider, useBlackboxManifest } from "@/blackbox/session-manifest";
import { BlackboxShell } from "@/components/blackbox/BlackboxShell";
import { Button } from "@/components/ui/button";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { hasPlatformAccess } from "@/lib/platform-access";
import { requiresPasswordChange } from "@/services/auth";
import { AppProvider, useAppStore } from "@/store/app-context";

const BlackboxRoleHome = lazy(() => import("@/pages/BlackboxRoleHome"));
const BlackboxAdvancedReports = lazy(() => import("@/pages/BlackboxAdvancedReports"));
const BlackboxConfigurationStudio = lazy(() => import("@/pages/BlackboxConfigurationStudio"));
const BlackboxCreativeLibrary = lazy(() => import("@/pages/BlackboxCreativeLibrary"));
const BlackboxPerformanceStudio = lazy(() => import("@/pages/BlackboxPerformanceStudio"));
const BlackboxPlanStudio = lazy(() => import("@/pages/BlackboxPlanStudio"));
const BlackboxBenefitsStudio = lazy(() => import("@/pages/BlackboxBenefitsStudio"));
const BlackboxCouponStudio = lazy(() => import("@/pages/BlackboxCouponStudio"));
const BlackboxStaffContinuity = lazy(() => import("@/pages/BlackboxStaffContinuity"));
const BlackboxGlobalSettings = lazy(() => import("@/pages/BlackboxGlobalSettings"));
const BlackboxGovernanceCenter = lazy(() => import("@/pages/BlackboxGovernanceCenter"));
const BlackboxInbox = lazy(() => import("@/pages/BlackboxInbox"));
const BlackboxInfrastructure = lazy(() => import("@/pages/BlackboxInfrastructure"));
const BlackboxTasks = lazy(() => import("@/pages/BlackboxTasks"));
const BlackboxExports = lazy(() => import("@/pages/BlackboxExports"));
const BlackboxOperations = lazy(() => import("@/pages/BlackboxOperations"));
const BlackboxAgentOperations = lazy(() => import("@/pages/BlackboxAgentOperations"));
const BlackboxFinance = lazy(() => import("@/pages/BlackboxFinance"));
const Login = lazy(() => import("@/pages/Login"));
const PlatformDashboard = lazy(() => import("@/pages/PlatformDashboard"));
const SupportWorkspace = lazy(() =>
  import("@/support-mode/SupportWorkspace").then((module) => ({
    default: module.SupportWorkspace,
  })),
);
const ForcePasswordChange = lazy(() => import("@/pages/ForcePasswordChange"));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60 * 1000,
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

function RouteFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 text-sm text-slate-300">
      Carregando Blackbox...
    </div>
  );
}

function SurfaceAccessDenied() {
  const { logout } = useAppStore();
  return (
    <main className="grid min-h-screen place-items-center bg-slate-950 px-6 text-slate-50">
      <section className="w-full max-w-md rounded-lg border border-slate-800 bg-slate-900 p-6 text-center">
        <h1 className="text-lg font-semibold">Identidade sem acesso ao Blackbox</h1>
        <p className="mt-2 text-sm text-slate-300">
          Este ambiente aceita somente identidades próprias da equipe Meu Executivo.
        </p>
        <Button className="mt-5" onClick={() => void logout()}>
          Encerrar esta sessão
        </Button>
      </section>
    </main>
  );
}

function BlackboxBoundary({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  if (
    location.pathname !== "/"
    && location.pathname !== "/blackbox"
    && !location.pathname.startsWith("/blackbox/")
  ) {
    return (
      <main className="grid min-h-screen place-items-center bg-slate-950 px-6 text-slate-50">
        <section className="w-full max-w-lg rounded-lg border border-slate-800 bg-slate-900 p-7 text-center">
          <ShieldCheck className="mx-auto h-8 w-8 text-blue-400" />
          <h1 className="mt-4 text-lg font-semibold">Ambiente interno isolado</h1>
          <p className="mt-2 text-sm leading-6 text-slate-300">
            O aplicativo das contas contratantes não é servido neste endereço.
          </p>
        </section>
      </main>
    );
  }
  return <>{children}</>;
}

function BlackboxLogin() {
  const { state } = useAppStore();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from;
  if (!state.isAuthenticated) {
    return <Login audience="platform" />;
  }
  if (state.session?.surface !== "platform" || !hasPlatformAccess(state.currentUser)) {
    return <SurfaceAccessDenied />;
  }
  return <Navigate to={from?.startsWith("/blackbox") ? from : "/blackbox"} replace />;
}

function PlatformRoute({ children }: { children: React.ReactNode }) {
  const { state } = useAppStore();
  const location = useLocation();
  if (!state.isAuthenticated) {
    return (
      <Navigate
        to="/blackbox/login"
        state={{ from: location.pathname }}
        replace
      />
    );
  }
  if (state.session?.surface !== "platform" || !hasPlatformAccess(state.currentUser)) {
    return <SurfaceAccessDenied />;
  }
  if (requiresPasswordChange(state.currentUser, state.session)) {
    return <ForcePasswordChange />;
  }
  return <>{children}</>;
}

function SupportModeNavigationGuard() {
  const { supportMode, supportModeTeardownPending } = useAppStore();
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    if (
      (supportMode || supportModeTeardownPending)
      && location.pathname !== "/blackbox/support-session"
    ) {
      navigate("/blackbox/support-session", { replace: true });
    }
  }, [location.pathname, navigate, supportMode, supportModeTeardownPending]);
  return null;
}

function BlackboxSupportExperience() {
  const { state, supportMode, supportModeTeardownPending, exitSupportMode } = useAppStore();
  const navigate = useNavigate();
  const hadSupportMode = useRef(Boolean(supportMode || supportModeTeardownPending));
  const returnPath = useRef(
    supportMode?.sessionType === 'root_direct' ? '/blackbox' : '/blackbox/support-access',
  );

  useEffect(() => {
    if (supportMode || supportModeTeardownPending) {
      hadSupportMode.current = true;
      if (supportMode) {
        returnPath.current = supportMode.sessionType === 'root_direct'
          ? '/blackbox'
          : '/blackbox/support-access';
      }
      return;
    }
    if (hadSupportMode.current) {
      hadSupportMode.current = false;
      navigate(returnPath.current, { replace: true });
    }
  }, [navigate, supportMode, supportModeTeardownPending]);

  if (requiresPasswordChange(state.currentUser, state.session)) {
    return <ForcePasswordChange />;
  }

  if (supportModeTeardownPending) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-950 px-6 text-slate-50">
        <p className="text-sm text-slate-300" role="status" aria-live="polite">
          Encerrando o acesso temporário e limpando os dados protegidos...
        </p>
      </main>
    );
  }
  if (!supportMode) {
    return <Navigate to="/blackbox/support-access" replace />;
  }
  return (
    <SupportWorkspace
      actor={state.currentUser}
      session={supportMode}
      onExit={async () => {
        await exitSupportMode();
        navigate(returnPath.current, { replace: true });
      }}
    />
  );
}

function BlackboxPanelRoute() {
  return (
    <BlackboxAuthorizedPage>
      <PlatformDashboard />
    </BlackboxAuthorizedPage>
  );
}

function BlackboxAuthorizedPage({ children }: { children: React.ReactNode }) {
  const { effectiveUser } = useBlackboxManifest();
  const location = useLocation();
  if (!canOpenBlackboxPath(effectiveUser, location.pathname)) {
    return (
      <div className="grid min-h-[calc(100vh-64px)] place-items-center px-6">
        <section className="max-w-md rounded-lg border bg-white p-6 text-center">
          <h1 className="text-lg font-semibold">Módulo não autorizado</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Este painel não pertence às permissões da sua função na equipe do SaaS.
          </p>
        </section>
      </div>
    );
  }
  return <>{children}</>;
}

function BlackboxRoot() {
  const { state } = useAppStore();
  if (!state.isAuthenticated) {
    return <Navigate to="/blackbox/login" replace />;
  }
  return <Navigate to="/blackbox" replace />;
}

function BlackboxManifestShell() {
  const { state } = useAppStore();
  if (!state.currentUser) return <SurfaceAccessDenied />;
  return (
    <BlackboxManifestProvider user={state.currentUser}>
      <BlackboxShell />
    </BlackboxManifestProvider>
  );
}

const BlackboxApp = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <AppProvider>
        <BrowserRouter>
          <BlackboxBoundary>
            <SupportModeNavigationGuard />
            <Suspense fallback={<RouteFallback />}>
              <Routes>
                <Route path="/" element={<BlackboxRoot />} />
                <Route path="/blackbox/login" element={<BlackboxLogin />} />
                <Route path="/blackbox/support-session" element={<BlackboxSupportExperience />} />
                <Route
                  path="/blackbox"
                  element={(
                    <PlatformRoute>
                      <BlackboxManifestShell />
                    </PlatformRoute>
                  )}
                >
                  <Route index element={<BlackboxRoleHome />} />
                  <Route path="tasks" element={<BlackboxAuthorizedPage><BlackboxTasks /></BlackboxAuthorizedPage>} />
                  <Route path="exports" element={<BlackboxAuthorizedPage><BlackboxExports /></BlackboxAuthorizedPage>} />
                  <Route path="operations" element={<BlackboxAuthorizedPage><BlackboxOperations /></BlackboxAuthorizedPage>} />
                  <Route path="agent-operations" element={<BlackboxAuthorizedPage><BlackboxAgentOperations /></BlackboxAuthorizedPage>} />
                  <Route path="inbox" element={<BlackboxAuthorizedPage><BlackboxInbox /></BlackboxAuthorizedPage>} />
                  <Route path="infrastructure" element={<BlackboxAuthorizedPage><BlackboxInfrastructure /></BlackboxAuthorizedPage>} />
                  <Route path="finance" element={<BlackboxAuthorizedPage><BlackboxFinance /></BlackboxAuthorizedPage>} />
                  <Route
                    path="configuration-studio"
                    element={(
                      <BlackboxAuthorizedPage>
                        <BlackboxConfigurationStudio />
                      </BlackboxAuthorizedPage>
                    )}
                  />
                  <Route
                    path="plans"
                    element={(
                      <BlackboxAuthorizedPage>
                        <BlackboxPlanStudio />
                      </BlackboxAuthorizedPage>
                    )}
                  />
                  <Route
                    path="benefits"
                    element={(
                      <BlackboxAuthorizedPage>
                        <BlackboxBenefitsStudio />
                      </BlackboxAuthorizedPage>
                    )}
                  />
                  <Route
                    path="coupons"
                    element={(
                      <BlackboxAuthorizedPage>
                        <BlackboxCouponStudio />
                      </BlackboxAuthorizedPage>
                    )}
                  />
                  <Route
                    path="performance"
                    element={(
                      <BlackboxAuthorizedPage>
                        <BlackboxPerformanceStudio />
                      </BlackboxAuthorizedPage>
                    )}
                  />
                  <Route
                    path="creative-library"
                    element={(
                      <BlackboxAuthorizedPage>
                        <BlackboxCreativeLibrary />
                      </BlackboxAuthorizedPage>
                    )}
                  />
                  <Route
                    path="advanced-reports"
                    element={(
                      <BlackboxAuthorizedPage>
                        <BlackboxAdvancedReports />
                      </BlackboxAuthorizedPage>
                    )}
                  />
                  <Route
                    path="team/continuity"
                    element={(
                      <BlackboxAuthorizedPage>
                        <BlackboxStaffContinuity />
                      </BlackboxAuthorizedPage>
                    )}
                  />
                  <Route
                    path="settings"
                    element={(
                      <BlackboxAuthorizedPage>
                        <BlackboxGlobalSettings />
                      </BlackboxAuthorizedPage>
                    )}
                  />
                  <Route
                    path="governance/:module"
                    element={(
                      <BlackboxAuthorizedPage>
                        <BlackboxGovernanceCenter />
                      </BlackboxAuthorizedPage>
                    )}
                  />
                  <Route path="*" element={<BlackboxPanelRoute />} />
                </Route>
                <Route path="*" element={<BlackboxBoundary><span /></BlackboxBoundary>} />
              </Routes>
            </Suspense>
          </BlackboxBoundary>
        </BrowserRouter>
      </AppProvider>
    </TooltipProvider>
  </QueryClientProvider>
);

export default BlackboxApp;
