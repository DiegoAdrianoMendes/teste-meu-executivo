export const KLM_THEME_STORAGE_KEY = 'klm-theme';

export function initializeKlmTheme(
  root: Pick<HTMLElement, 'classList'> = document.documentElement,
  storage: Pick<Storage, 'getItem'> = localStorage,
) {
  try {
    const storedTheme = storage.getItem(KLM_THEME_STORAGE_KEY);
    if (storedTheme === 'dark' || storedTheme === 'light') {
      root.classList.toggle('dark', storedTheme === 'dark');
    }
  } catch {
    // Keep the current document theme when storage is unavailable.
  }
  return root.classList.contains('dark');
}
