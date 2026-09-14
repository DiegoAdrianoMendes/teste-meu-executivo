import { useEffect } from 'react';

export function useStructuredData(data: Record<string, unknown> | null | undefined, key = 'default') {
  useEffect(() => {
    const scriptId = `structured-data-${key}`;
    const previous = document.getElementById(scriptId);
    if (previous) {
      previous.remove();
    }

    if (!data) {
      return;
    }

    const script = document.createElement('script');
    script.id = scriptId;
    script.type = 'application/ld+json';
    script.text = JSON.stringify(data);
    document.head.appendChild(script);

    return () => {
      script.remove();
    };
  }, [data, key]);
}
