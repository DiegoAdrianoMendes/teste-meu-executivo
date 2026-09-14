import { useEffect } from 'react';
import type { SeoMetadata } from '@/types';

function upsertMeta(selector: string, attrs: Record<string, string>) {
  let element = document.head.querySelector(selector) as HTMLMetaElement | null;
  if (!element) {
    element = document.createElement('meta');
    Object.entries(attrs)
      .filter(([key]) => key !== 'content')
      .forEach(([key, value]) => element?.setAttribute(key, value));
    document.head.appendChild(element);
  }
  if (attrs.content) {
    element.setAttribute('content', attrs.content);
  }
}

export function usePageMetadata(meta?: SeoMetadata | null) {
  useEffect(() => {
    if (!meta) return;

    document.title = meta.title;
    upsertMeta('meta[name="description"]', { name: 'description', content: meta.description });
    upsertMeta('meta[property="og:title"]', { property: 'og:title', content: meta.title });
    upsertMeta('meta[property="og:description"]', { property: 'og:description', content: meta.description });
    upsertMeta('meta[name="twitter:title"]', { name: 'twitter:title', content: meta.title });
    upsertMeta('meta[name="twitter:description"]', { name: 'twitter:description', content: meta.description });

    if (meta.ogImageUrl) {
      upsertMeta('meta[property="og:image"]', { property: 'og:image', content: meta.ogImageUrl });
      upsertMeta('meta[name="twitter:image"]', { name: 'twitter:image', content: meta.ogImageUrl });
    }

    if (meta.canonicalUrl) {
      let canonical = document.head.querySelector('link[rel="canonical"]') as HTMLLinkElement | null;
      if (!canonical) {
        canonical = document.createElement('link');
        canonical.setAttribute('rel', 'canonical');
        document.head.appendChild(canonical);
      }
      canonical.setAttribute('href', meta.canonicalUrl);
    }
  }, [meta]);
}
