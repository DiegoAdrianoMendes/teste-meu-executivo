import type { PublicSectionMedia } from '@/types';

const REAL_MEDIA_FALLBACKS: Record<string, Partial<PublicSectionMedia>> = {
  hero: {
    posterUrl: '/site-media/crm-system.png',
    gifUrl: '/site-media/hero-demo-real.gif',
    alt: 'Tela real do atendimento do Meu Executivo com mensagens, pendências e CRM',
  },
  crm: {
    posterUrl: '/site-media/crm-system.png',
    gifUrl: '/site-media/crm-demo-real.gif',
    alt: 'Tela real do CRM jurídico com cards, histórico e andamento de leads',
  },
  chat: {
    posterUrl: '/site-media/ia-system.png',
    gifUrl: '/site-media/ia-demo-real.gif',
    alt: 'Tela real da operação com IA, follow-up e supervisão humana',
  },
  kpi: {
    posterUrl: '/site-media/platform-system.png',
    gifUrl: '/site-media/kpi-demo-real.gif',
    alt: 'Tela real de dashboard e indicadores do Meu Executivo',
  },
  features: {
    posterUrl: '/site-media/ia-system.png',
    gifUrl: '/site-media/flow-demo-real.gif',
    alt: 'Tela real da plataforma com visão operacional do intake',
  },
  timeline: {
    posterUrl: '/site-media/crm-system.png',
    gifUrl: '/site-media/flow-demo-real.gif',
    alt: 'Tela real da plataforma com progresso do atendimento e etapas',
  },
};

function withFallbackMedia(media?: PublicSectionMedia | null): PublicSectionMedia | null {
  if (!media) return null;

  const fallback = REAL_MEDIA_FALLBACKS[media.layoutVariant] ?? {};
  const configuredEntries = Object.entries(media).filter(
    ([, value]) => value !== '' && value != null,
  );
  return {
    ...fallback,
    ...Object.fromEntries(configuredEntries),
  } as PublicSectionMedia;
}

function ScreenshotFrame({ media, priority }: { media: PublicSectionMedia; priority: boolean }) {
  return (
    <div className={`landing-media-stack variant-${media.layoutVariant ?? 'hero'}`}>
      <div className="landing-media-depth depth-back" />
      <div className="landing-media-depth depth-front" />
      <div className="landing-browser landing-browser-real">
        <div className="landing-browser-bar">
          <span className="landing-dot" />
          <span className="landing-dot" />
          <span className="landing-dot" />
          <span className="ml-2 text-[11px] font-medium text-muted-foreground">Meu Executivo</span>
        </div>
        <img
          src={media.posterUrl ?? ''}
          alt={media.alt ?? ''}
          className="landing-system-shot"
          loading={priority ? 'eager' : 'lazy'}
          decoding="async"
        />
      </div>
      {media.caption ? <div className="landing-media-caption">{media.caption}</div> : null}
    </div>
  );
}

export function PublicMediaFrame({
  media,
  priority = false,
}: {
  media?: PublicSectionMedia | null;
  priority?: boolean;
}) {
  const resolvedMedia = withFallbackMedia(media);
  if (!resolvedMedia) return null;

  if (resolvedMedia.mp4Url || resolvedMedia.webmUrl) {
    return (
      <div className="landing-media-shell">
        <video
          className="landing-video"
          autoPlay
          muted
          loop
          playsInline
          preload={priority ? 'auto' : 'metadata'}
          poster={resolvedMedia.posterUrl ?? undefined}
        >
          {resolvedMedia.webmUrl ? <source src={resolvedMedia.webmUrl} type="video/webm" /> : null}
          {resolvedMedia.mp4Url ? <source src={resolvedMedia.mp4Url} type="video/mp4" /> : null}
        </video>
      </div>
    );
  }

  if (resolvedMedia.gifUrl) {
    return (
      <div className="landing-media-shell">
        <img
          src={resolvedMedia.gifUrl}
          alt={resolvedMedia.alt ?? ''}
          className="landing-video object-cover"
          loading={priority ? 'eager' : 'lazy'}
          decoding="async"
        />
      </div>
    );
  }

  if (!resolvedMedia.posterUrl) return null;

  return (
    <div className="landing-media-shell">
      <ScreenshotFrame media={resolvedMedia} priority={priority} />
    </div>
  );
}
