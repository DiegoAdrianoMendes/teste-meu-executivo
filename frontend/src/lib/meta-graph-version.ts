const META_GRAPH_API_VERSION_PATTERN = /^v[1-9][0-9]*\.0$/;

export function requireMetaGraphApiVersion(value: string | undefined): string {
  const normalized = String(value ?? '').trim();
  if (!META_GRAPH_API_VERSION_PATTERN.test(normalized)) {
    throw new Error('VITE_META_GRAPH_API_VERSION ausente ou inválida. Esperado: v<major>.0.');
  }
  return normalized;
}

export function metaGraphApiVersionDiagnostic(value: string | undefined): string {
  try {
    return requireMetaGraphApiVersion(value);
  } catch {
    return 'não configurada';
  }
}
