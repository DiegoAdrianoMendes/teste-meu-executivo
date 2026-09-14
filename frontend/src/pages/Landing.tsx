import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ArrowRight, CheckCircle2, ChevronDown, ChevronUp, Eye, EyeOff, MoveUpRight, Sparkles } from 'lucide-react';

import { PublicMediaFrame } from '@/components/public/PublicMediaFrame';
import { DesbLogo } from '@/components/ui/DesbLogo';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { BRAND } from '@/lib/brand';
import { fetchPublicHome } from '@/services/public-site';
import type { PublicFaqItem, PublicHomeSection } from '@/types';
import { usePageMetadata } from '@/hooks/use-page-metadata';

const PLAN_CARDS = [
  {
    name: 'Basic',
    description: 'Para escritórios que querem responder mais rápido, organizar intake e começar com uma operação enxuta.',
    features: ['3 membros', '1 workspace', '1 WhatsApp conectado', '6 agentes de IA governados pelo admin', 'Automações por status', 'Mensagens agendadas'],
  },
  {
    name: 'Pro',
    description: 'Para times em crescimento que precisam de mais volume, governança, integrações e mais contexto operacional.',
    features: ['6 membros', '3 workspaces', '3 WhatsApps conectados', '12 agentes IA', 'Automações ilimitadas', 'Integração com contratos e calendário'],
    highlight: true,
  },
  {
    name: 'Scale',
    description: 'Para operações com múltiplos atendentes, mais canais, mais governança e acompanhamento próximo da plataforma.',
    features: ['Membros ilimitados', '4 workspaces', '6 WhatsApps conectados', 'Agentes ilimitados', 'Webhooks', 'Gerente de sucesso'],
  },
];

const LANDING_HEADER_OFFSET = 112;

function scrollToSection(targetId: string) {
  const element = document.getElementById(targetId);
  if (element) {
    const top = element.getBoundingClientRect().top + window.scrollY - LANDING_HEADER_OFFSET;
    window.scrollTo({ top: Math.max(top, 0), behavior: 'smooth' });
  }
}

function Navbar({
  sections,
  whatsappUrl,
  isScrolled,
}: {
  sections: PublicHomeSection[];
  whatsappUrl: string;
  isScrolled: boolean;
}) {
  return (
    <header className="landing-navbar-wrap">
      <div className={`landing-navbar-shell ${isScrolled ? 'is-scrolled' : ''}`}>
        <Link to="/" className="flex min-w-0 items-center gap-3">
          <DesbLogo variant="full" size={56} />
          <div className="hidden xl:block">
            <p className="text-[10px] font-semibold uppercase tracking-[0.26em] text-primary/80">Atendimento, intake e fechamento</p>
            <p className="text-xs text-muted-foreground">Uma operação jurídica mais organizada do primeiro oi ao contrato.</p>
          </div>
        </Link>
        <nav className="hidden items-center gap-5 text-sm text-muted-foreground xl:flex">
          <Link to="/blog" className="landing-navbar-link">
            Blog
          </Link>
          {sections.slice(0, 6).map((section) => (
            <button key={section.key} type="button" onClick={() => scrollToSection(section.key)} className="landing-navbar-link">
              {section.navLabel}
            </button>
          ))}
        </nav>
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" asChild>
            <Link to="/login">Entrar</Link>
          </Button>
          <Button size="sm" className="h-10 rounded-full bg-brand-gradient px-5 text-white hover:opacity-90 shadow-glow" asChild>
            <a href={whatsappUrl} target="_blank" rel="noreferrer">Agendar demonstração</a>
          </Button>
        </div>
      </div>
    </header>
  );
}

function ProgressBar({ progress }: { progress: number }) {
  return (
    <div className="landing-progress-track">
      <div className="landing-progress-bar" style={{ transform: `scaleX(${Math.max(progress, 0.02)})` }} />
    </div>
  );
}

function SideNav({
  sections,
  activeSection,
}: {
  sections: PublicHomeSection[];
  activeSection: string | null;
}) {
  return (
    <>
      <aside className="landing-side-nav hidden 2xl:block">
        <div className="rounded-2xl border border-border/60 bg-background/80 p-3 shadow-elevated backdrop-blur-xl">
          <p className="mb-3 px-2 text-[10px] font-semibold uppercase tracking-[0.24em] text-muted-foreground">Navegação</p>
          <div className="space-y-1">
            {sections.map((section) => (
              <button
                key={section.key}
                type="button"
                onClick={() => scrollToSection(section.key)}
                className={`landing-side-link ${activeSection === section.key ? 'is-active' : ''}`}
              >
                <span className="landing-side-dot" />
                <span>{section.navLabel}</span>
              </button>
            ))}
          </div>
        </div>
      </aside>

      <div className="landing-mobile-nav 2xl:hidden">
        <div className="flex gap-2 overflow-x-auto px-4 pb-2 scrollbar-thin">
          {sections.map((section) => (
            <button
              key={section.key}
              type="button"
              onClick={() => scrollToSection(section.key)}
              className={`landing-mobile-chip ${activeSection === section.key ? 'is-active' : ''}`}
            >
              {section.navLabel}
            </button>
          ))}
        </div>
      </div>
    </>
  );
}

function HeroSection({
  section,
  whatsappUrl,
  secondaryTarget,
}: {
  section: PublicHomeSection;
  whatsappUrl: string;
  secondaryTarget: string;
}) {
  return (
    <section id={section.key} className="landing-section hero">
      <div className="landing-hero-backdrop">
        <div className="landing-hero-orb orb-one" />
        <div className="landing-hero-orb orb-two" />
        <div className="landing-hero-orb orb-three" />
        <div className="landing-hero-gridlines" />
      </div>
      <div className="landing-hero-grid">
        <div className="space-y-6 landing-hero-copy">
          <div className="landing-hero-pill landing-reveal">
            <Sparkles className="h-3.5 w-3.5" />
            WhatsApp, IA humanizada, documentos e CRM no mesmo fluxo
          </div>
          <h1 className="landing-display landing-reveal-delay-1">{section.title}</h1>
          {section.subtitle ? (
            <p className="max-w-2xl text-lg leading-relaxed text-muted-foreground landing-reveal-delay-2">{section.subtitle}</p>
          ) : null}
          {section.body ? <p className="max-w-xl text-sm leading-relaxed text-muted-foreground landing-reveal-delay-3">{section.body}</p> : null}
          <div className="flex flex-col gap-3 sm:flex-row landing-reveal-delay-3">
            <Button size="lg" className="h-12 bg-brand-gradient px-8 text-base text-white hover:opacity-90 shadow-glow" asChild>
              <a href={whatsappUrl} target="_blank" rel="noreferrer">
                Agendar demonstração
                <ArrowRight className="ml-2 h-4 w-4" />
              </a>
            </Button>
            <Button size="lg" variant="outline" className="h-12 px-8 text-base" onClick={() => scrollToSection(secondaryTarget)}>
              Ver como funciona
            </Button>
          </div>
          <div className="grid max-w-2xl gap-3 pt-4 sm:grid-cols-3 landing-reveal-delay-3">
            {section.bullets.map((bullet) => (
              <div key={bullet} className="landing-mini-stat">
                <CheckCircle2 className="h-4 w-4 text-primary" />
                <span>{bullet}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="landing-hero-stage landing-reveal-delay-2">
          <div className="landing-hero-floating-card top-left">
            <span className="landing-hero-floating-label">Resposta inicial</span>
            <strong>2m 07s</strong>
            <p>Triagem ativa com histórico já estruturado para a equipe.</p>
          </div>
          <div className="landing-hero-floating-card top-right">
            <span className="landing-hero-floating-label">Documentos</span>
            <strong>5 de 6 recebidos</strong>
            <p>Pendências visíveis antes do follow-up comercial.</p>
          </div>
          <div className="landing-hero-floating-card bottom-right">
            <span className="landing-hero-floating-label">Próximo passo</span>
            <strong>Agendar análise humana</strong>
            <p>Lead qualificado com contexto e checklist documental.</p>
          </div>
          <div className="landing-hero-visual">
            <PublicMediaFrame media={section.media} priority />
          </div>
        </div>
      </div>
    </section>
  );
}

function TextSection({ section }: { section: PublicHomeSection }) {
  return (
    <section id={section.key} className="landing-section">
      <div className="mx-auto max-w-5xl">
        <div className="landing-section-heading">
          <span className="landing-kicker">{section.navLabel}</span>
          <h2 className="landing-title">{section.title}</h2>
          {section.subtitle ? <p className="landing-subtitle">{section.subtitle}</p> : null}
          {section.body ? <p className="landing-body">{section.body}</p> : null}
        </div>
        {!!section.bullets.length && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {section.bullets.map((bullet) => (
              <div key={bullet} className="landing-chip-card">
                <CheckCircle2 className="h-4 w-4 text-primary" />
                <span>{bullet}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function ProposalSection({ section }: { section: PublicHomeSection }) {
  return (
    <section id={section.key} className="landing-section">
      <div className="mx-auto grid max-w-7xl gap-10 xl:grid-cols-[0.95fr,1.05fr]">
        <div className="space-y-4">
          <span className="landing-kicker">{section.navLabel}</span>
          <h2 className="landing-title">{section.title}</h2>
          {section.subtitle ? <p className="landing-subtitle">{section.subtitle}</p> : null}
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {section.items.map((item) => (
            <article key={item.title} className="landing-feature-card">
              {item.badge ? <Badge className="landing-badge">{item.badge}</Badge> : null}
              <h3 className="text-lg font-semibold">{item.title}</h3>
              {item.description ? <p className="text-sm leading-relaxed text-muted-foreground">{item.description}</p> : null}
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function ProcessSection({ section }: { section: PublicHomeSection }) {
  return (
    <section id={section.key} className="landing-section landing-surface">
      <div className="mx-auto grid max-w-7xl gap-10 lg:grid-cols-[0.9fr,1.1fr]">
        <div className="space-y-4">
          <span className="landing-kicker">{section.navLabel}</span>
          <h2 className="landing-title">{section.title}</h2>
        </div>
        <div className="grid gap-4 lg:grid-cols-[0.9fr,1.1fr]">
          <div className="space-y-3">
            {section.items.map((item, index) => (
              <div key={item.title} className="landing-step-card">
                <div className="landing-step-index">{String(index + 1).padStart(2, '0')}</div>
                <div>
                  <p className="text-sm font-semibold">{item.title}</p>
                  {item.description ? <p className="mt-1 text-sm text-muted-foreground">{item.description}</p> : null}
                </div>
              </div>
            ))}
          </div>
          <PublicMediaFrame media={section.media} />
        </div>
      </div>
    </section>
  );
}

function SplitSection({ section }: { section: PublicHomeSection }) {
  return (
    <section id={section.key} className="landing-section">
      <div className="mx-auto grid max-w-7xl gap-10 lg:grid-cols-[1.05fr,0.95fr]">
        <div className="space-y-4">
          <span className="landing-kicker">{section.navLabel}</span>
          <h2 className="landing-title">{section.title}</h2>
          {section.subtitle ? <p className="landing-subtitle">{section.subtitle}</p> : null}
          {section.body ? <p className="landing-body">{section.body}</p> : null}
          {!!section.bullets.length && (
            <div className="grid gap-3 sm:grid-cols-2">
              {section.bullets.map((bullet) => (
                <div key={bullet} className="landing-chip-card">
                  <CheckCircle2 className="h-4 w-4 text-primary" />
                  <span>{bullet}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <PublicMediaFrame media={section.media} />
      </div>
    </section>
  );
}

function TestimonialsSection({ section }: { section: PublicHomeSection }) {
  return (
    <section id={section.key} className="landing-section">
      <div className="mx-auto max-w-7xl space-y-8">
        <div className="landing-section-heading text-center">
          <span className="landing-kicker">{section.navLabel}</span>
          <h2 className="landing-title">{section.title}</h2>
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          {section.items.map((item) => (
            <article key={item.title} className="landing-testimonial-card">
              <div className="mb-4 flex gap-1 text-primary">
                {Array.from({ length: 5 }).map((_, index) => <Sparkles key={index} className="h-4 w-4" />)}
              </div>
              {item.description ? <p className="text-sm leading-relaxed text-muted-foreground">“{item.description}”</p> : null}
              <p className="mt-4 text-sm font-semibold">{item.title}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function PricingSection({
  section,
  whatsappUrl,
}: {
  section: PublicHomeSection;
  whatsappUrl: string;
}) {
  return (
    <section id={section.key} className="landing-section landing-surface">
      <div className="mx-auto max-w-7xl space-y-8">
        <div className="landing-section-heading text-center">
          <span className="landing-kicker">{section.navLabel}</span>
          <h2 className="landing-title">{section.title}</h2>
          {section.subtitle ? <p className="landing-subtitle mx-auto max-w-3xl">{section.subtitle}</p> : null}
        </div>
        <div className="grid gap-4 xl:grid-cols-3">
          {PLAN_CARDS.map((plan) => (
            <article key={plan.name} className={`landing-plan-card ${plan.highlight ? 'is-highlight' : ''}`}>
              {plan.highlight ? <Badge className="landing-badge">Mais escolhido</Badge> : null}
              <h3 className="mt-4 text-2xl font-bold">{plan.name}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{plan.description}</p>
              <div className="mt-6 space-y-2">
                {plan.features.map((feature) => (
                  <div key={feature} className="flex items-center gap-2 text-sm">
                    <CheckCircle2 className="h-4 w-4 text-primary" />
                    <span>{feature}</span>
                  </div>
                ))}
              </div>
              <Button className="mt-6 w-full" variant={plan.highlight ? 'default' : 'outline'} asChild>
                <a href={whatsappUrl} target="_blank" rel="noreferrer">
                  Falar com o time
                </a>
              </Button>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function FinalCtaSection({
  section,
  whatsappUrl,
}: {
  section: PublicHomeSection;
  whatsappUrl: string;
}) {
  return (
    <section id={section.key} className="landing-section">
      <div className="landing-final-cta">
        <div className="mx-auto max-w-3xl text-center">
          <span className="landing-kicker justify-center">Contato</span>
          <h2 className="landing-title">{section.title}</h2>
          {section.subtitle ? <p className="landing-subtitle mx-auto max-w-2xl">{section.subtitle}</p> : null}
          <div className="mt-8 flex flex-col justify-center gap-3 sm:flex-row">
            <Button size="lg" className="h-12 bg-brand-gradient px-8 text-base text-white hover:opacity-90 shadow-glow" asChild>
              <a href={whatsappUrl} target="_blank" rel="noreferrer">
                {section.cta?.primaryLabel ?? 'Agendar demonstração'}
                <MoveUpRight className="ml-2 h-4 w-4" />
              </a>
            </Button>
            <Button size="lg" variant="outline" className="h-12 px-8 text-base" asChild>
              <a href={whatsappUrl} target="_blank" rel="noreferrer">{section.cta?.secondaryLabel ?? 'Falar com o time'}</a>
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}

function FaqSection({ items }: { items: PublicFaqItem[] }) {
  const [openIndex, setOpenIndex] = useState<number | null>(0);

  return (
    <section id="faq" className="landing-section landing-surface">
      <div className="mx-auto max-w-4xl space-y-8">
        <div className="landing-section-heading text-center">
          <span className="landing-kicker">FAQ</span>
          <h2 className="landing-title">Perguntas frequentes</h2>
        </div>
        <div className="space-y-3">
          {items.map((item, index) => {
            const isOpen = openIndex === index;
            return (
              <article key={item.question} className="landing-faq-card">
                <button
                  type="button"
                  className="flex w-full items-center justify-between gap-4 px-5 py-5 text-left"
                  onClick={() => setOpenIndex(isOpen ? null : index)}
                >
                  <span className="text-sm font-semibold">{item.question}</span>
                  {isOpen ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
                </button>
                {isOpen ? <div className="px-5 pb-5 text-sm leading-relaxed text-muted-foreground">{item.answer}</div> : null}
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="border-t border-border/60 bg-background py-10">
      <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 sm:px-6 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <DesbLogo variant="full" size={34} />
          <p className="mt-2 text-xs text-muted-foreground">{BRAND.tagline}</p>
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
          <Link to="/termos" className="hover:text-foreground">Termos</Link>
          <Link to="/privacidade" className="hover:text-foreground">Privacidade</Link>
          <Link to="/checkout" className="hover:text-foreground">Criar conta</Link>
          <Link to="/login" className="hover:text-foreground">Entrar</Link>
        </div>
      </div>
    </footer>
  );
}

function FloatingButtons({
  whatsappUrl,
  floatingLabel,
  showBackToTop,
}: {
  whatsappUrl: string;
  floatingLabel: string;
  showBackToTop: boolean;
}) {
  return (
    <div className="landing-floating-actions">
      {showBackToTop ? (
        <button type="button" className="landing-floating-secondary" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
          <MoveUpRight className="h-4 w-4 rotate-[-45deg]" />
        </button>
      ) : null}
      <a href={whatsappUrl} target="_blank" rel="noreferrer" className="landing-floating-whatsapp">
        <img src="/whatsapp-icon.svg" alt="" className="landing-whatsapp-logo" />
        <span>{floatingLabel}</span>
      </a>
    </div>
  );
}

function renderSection(section: PublicHomeSection, whatsappUrl: string) {
  switch (section.key) {
    case 'hero':
      return <HeroSection key={section.key} section={section} whatsappUrl={whatsappUrl} secondaryTarget={section.cta?.secondaryTarget ?? 'como-funciona'} />;
    case 'proposta-valor':
      return <ProposalSection key={section.key} section={section} />;
    case 'como-funciona':
      return <ProcessSection key={section.key} section={section} />;
    case 'crm-vivo':
    case 'ia':
    case 'kpis':
      return <SplitSection key={section.key} section={section} />;
    case 'pricing':
      return <PricingSection key={section.key} section={section} whatsappUrl={whatsappUrl} />;
    case 'prova-social':
      return <TestimonialsSection key={section.key} section={section} />;
    case 'cta-final':
      return <FinalCtaSection key={section.key} section={section} whatsappUrl={whatsappUrl} />;
    default:
      return <TextSection key={section.key} section={section} />;
  }
}

export default function Landing() {
  const [activeSection, setActiveSection] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [scrollY, setScrollY] = useState(0);
  const homeQuery = useQuery({
    queryKey: ['public-home'],
    queryFn: fetchPublicHome,
    staleTime: 60_000,
  });

  const payload = homeQuery.data;
  usePageMetadata(payload?.home.seo ?? payload?.settings.seo);

  const visibleSections = useMemo(
    () => [...(payload?.home.sections ?? [])].filter((section) => section.isVisible).sort((a, b) => a.order - b.order),
    [payload?.home.sections],
  );

  useEffect(() => {
    if (!visibleSections.length) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (visible?.target.id) {
          setActiveSection(visible.target.id);
        }
      },
      { rootMargin: '-20% 0px -50% 0px', threshold: [0.2, 0.35, 0.6] },
    );

    visibleSections.forEach((section) => {
      const element = document.getElementById(section.key);
      if (element) observer.observe(element);
    });

    return () => observer.disconnect();
  }, [visibleSections]);

  useEffect(() => {
    const onScroll = () => {
      const total = document.documentElement.scrollHeight - window.innerHeight;
      const nextProgress = total > 0 ? window.scrollY / total : 0;
      setProgress(nextProgress);
      setScrollY(window.scrollY);
    };

    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  const landingStyle = useMemo(
    () => ({ ['--landing-scroll' as string]: `${Math.min(scrollY, 360)}px` }) as CSSProperties,
    [scrollY],
  );

  if (homeQuery.isLoading || !payload) {
    return (
      <div className="min-h-screen bg-background">
        <div className="mx-auto max-w-6xl px-4 py-32 sm:px-6">
          <div className="h-8 w-40 animate-pulse rounded bg-muted" />
          <div className="mt-8 h-24 max-w-4xl animate-pulse rounded bg-muted" />
          <div className="mt-6 h-12 max-w-2xl animate-pulse rounded bg-muted" />
          <div className="mt-10 grid gap-6 lg:grid-cols-2">
            <div className="h-[420px] animate-pulse rounded-3xl bg-muted" />
            <div className="h-[420px] animate-pulse rounded-3xl bg-muted" />
          </div>
        </div>
      </div>
    );
  }

  if (homeQuery.error instanceof Error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4 text-center">
        <div className="max-w-lg space-y-3">
          <p className="text-lg font-semibold">Não foi possível carregar o site agora.</p>
          <p className="text-sm text-muted-foreground">{homeQuery.error.message}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <ProgressBar progress={progress} />
      <Navbar sections={visibleSections} whatsappUrl={payload.settings.whatsappUrl} isScrolled={scrollY > 16} />
      <SideNav sections={visibleSections} activeSection={activeSection} />

      <main className="landing-page" style={landingStyle}>
        {visibleSections.map((section) => renderSection(section, payload.settings.whatsappUrl))}
        <FaqSection items={payload.faq.items} />
      </main>

      <Footer />
      <FloatingButtons
        whatsappUrl={payload.settings.whatsappUrl}
        floatingLabel={payload.settings.floatingWhatsappLabel}
        showBackToTop={progress > 0.12}
      />
    </div>
  );
}
