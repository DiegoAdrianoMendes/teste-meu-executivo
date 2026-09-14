import type { Brand, UserPreferences, UserThemePreference } from '@/types/frontend-contracts';

type Cleanup = () => void;

function setAttributeWithCleanup(
  element: Element,
  name: string,
  value: string,
): Cleanup {
  const previous = element.getAttribute(name);
  element.setAttribute(name, value);
  return () => {
    if (element.getAttribute(name) !== value) return;
    if (previous === null) element.removeAttribute(name);
    else element.setAttribute(name, previous);
  };
}

function setStyleWithCleanup(
  element: HTMLElement,
  name: string,
  value: string,
): Cleanup {
  const previous = element.style.getPropertyValue(name);
  const previousPriority = element.style.getPropertyPriority(name);
  element.style.setProperty(name, value);
  return () => {
    if (element.style.getPropertyValue(name) !== value) return;
    if (previous) element.style.setProperty(name, previous, previousPriority);
    else element.style.removeProperty(name);
  };
}

function upsertMetaWithCleanup(
  documentRef: Document,
  selector: string,
  identity: Record<string, string>,
  content: string,
): Cleanup {
  let element = documentRef.head.querySelector(selector) as HTMLMetaElement | null;
  const created = !element;
  if (!element) {
    element = documentRef.createElement('meta');
    Object.entries(identity).forEach(([name, value]) => element?.setAttribute(name, value));
    documentRef.head.appendChild(element);
  }
  const previous = element.getAttribute('content');
  element.setAttribute('content', content);
  return () => {
    if (!element || element.getAttribute('content') !== content) return;
    if (created) element.remove();
    else if (previous === null) element.removeAttribute('content');
    else element.setAttribute('content', previous);
  };
}

function updateLinksWithCleanup(
  documentRef: Document,
  selector: string,
  identity: Record<string, string>,
  href: string,
): Cleanup[] {
  const existing = Array.from(documentRef.head.querySelectorAll(selector)) as HTMLLinkElement[];
  const links = existing.length ? existing : [documentRef.createElement('link')];
  if (!existing.length) {
    Object.entries(identity).forEach(([name, value]) => links[0].setAttribute(name, value));
    documentRef.head.appendChild(links[0]);
  }

  return links.map((link) => {
    const previous = link.getAttribute('href');
    link.setAttribute('href', href);
    return () => {
      if (link.getAttribute('href') !== href) return;
      if (!existing.length) link.remove();
      else if (previous === null) link.removeAttribute('href');
      else link.setAttribute('href', previous);
    };
  });
}

export function applyWorkspaceBrandToDocument(
  brand: Brand,
  documentRef: Document = document,
): Cleanup {
  const cleanups: Cleanup[] = [];
  const root = documentRef.documentElement;

  for (const [name, value] of Object.entries(brand.cssVariables)) {
    if (!name.startsWith('--') || !value.trim()) continue;
    cleanups.push(setStyleWithCleanup(root, name, value));
  }

  cleanups.push(setStyleWithCleanup(root, '--brand-logo-light-url', `url("${brand.assets.logoLightUrl}")`));
  cleanups.push(setStyleWithCleanup(root, '--brand-logo-dark-url', `url("${brand.assets.logoDarkUrl}")`));
  cleanups.push(setAttributeWithCleanup(root, 'data-brand-id', brand.id));
  cleanups.push(setAttributeWithCleanup(root, 'data-brand-workspace-id', brand.workspaceId));

  const previousTitle = documentRef.title;
  documentRef.title = brand.metadata.defaultTitle;
  cleanups.push(() => {
    if (documentRef.title === brand.metadata.defaultTitle) documentRef.title = previousTitle;
  });

  const metadata: Array<[string, Record<string, string>, string]> = [
    ['meta[name="description"]', { name: 'description' }, brand.metadata.description],
    ['meta[name="author"]', { name: 'author' }, brand.metadata.author],
    ['meta[name="theme-color"]', { name: 'theme-color' }, brand.metadata.themeColor],
    ['meta[name="application-name"]', { name: 'application-name' }, brand.metadata.applicationName],
    ['meta[name="apple-mobile-web-app-title"]', { name: 'apple-mobile-web-app-title' }, brand.metadata.applicationName],
    ['meta[property="og:title"]', { property: 'og:title' }, brand.metadata.defaultTitle],
    ['meta[property="og:description"]', { property: 'og:description' }, brand.metadata.description],
    ['meta[property="og:site_name"]', { property: 'og:site_name' }, brand.name],
    ['meta[name="twitter:title"]', { name: 'twitter:title' }, brand.metadata.defaultTitle],
    ['meta[name="twitter:description"]', { name: 'twitter:description' }, brand.metadata.description],
  ];
  metadata.forEach(([selector, identity, content]) => {
    cleanups.push(upsertMetaWithCleanup(documentRef, selector, identity, content));
  });

  cleanups.push(...updateLinksWithCleanup(
    documentRef,
    'link[rel~="icon"]',
    { rel: 'icon' },
    brand.assets.faviconUrl,
  ));
  if (brand.assets.appleTouchIconUrl) {
    cleanups.push(...updateLinksWithCleanup(
      documentRef,
      'link[rel="apple-touch-icon"]',
      { rel: 'apple-touch-icon' },
      brand.assets.appleTouchIconUrl,
    ));
  }

  return () => {
    cleanups.reverse().forEach((cleanup) => cleanup());
  };
}

export function resolveUserTheme(
  preference: UserThemePreference,
  systemPrefersDark: boolean,
): 'light' | 'dark' {
  return preference === 'system' ? (systemPrefersDark ? 'dark' : 'light') : preference;
}

const DENSITY_SCALE: Record<UserPreferences['density'], string> = {
  compact: '0.875',
  comfortable: '1',
  spacious: '1.125',
};

export function applyUserPreferencesToDocument(
  preferences: UserPreferences,
  systemPrefersDark: boolean,
  documentRef: Document = document,
): Cleanup {
  const root = documentRef.documentElement;
  const cleanups: Cleanup[] = [];
  const resolvedTheme = resolveUserTheme(preferences.theme, systemPrefersDark);
  const previousDark = root.classList.contains('dark');
  const previousHighContrast = root.classList.contains('high-contrast');
  const previousReducedMotion = root.classList.contains('reduce-motion');

  root.classList.toggle('dark', resolvedTheme === 'dark');
  root.classList.toggle('high-contrast', preferences.accessibility.highContrast);
  root.classList.toggle('reduce-motion', preferences.accessibility.reducedMotion);

  cleanups.push(() => {
    if (root.classList.contains('dark') === (resolvedTheme === 'dark')) {
      root.classList.toggle('dark', previousDark);
    }
    if (root.classList.contains('high-contrast') === preferences.accessibility.highContrast) {
      root.classList.toggle('high-contrast', previousHighContrast);
    }
    if (root.classList.contains('reduce-motion') === preferences.accessibility.reducedMotion) {
      root.classList.toggle('reduce-motion', previousReducedMotion);
    }
  });

  cleanups.push(setAttributeWithCleanup(root, 'data-theme', resolvedTheme));
  cleanups.push(setAttributeWithCleanup(root, 'data-density', preferences.density));
  cleanups.push(setAttributeWithCleanup(
    root,
    'data-user-preferences-scope',
    `${preferences.workspaceId}:${preferences.userId}`,
  ));
  cleanups.push(setAttributeWithCleanup(root, 'lang', preferences.locale));
  cleanups.push(setStyleWithCleanup(root, '--app-density-scale', DENSITY_SCALE[preferences.density]));
  cleanups.push(setStyleWithCleanup(root, '--app-font-scale', String(preferences.accessibility.fontScale)));
  cleanups.push(setStyleWithCleanup(
    root,
    '--app-motion-duration-scale',
    preferences.accessibility.reducedMotion ? '0' : '1',
  ));

  return () => {
    cleanups.reverse().forEach((cleanup) => cleanup());
  };
}
