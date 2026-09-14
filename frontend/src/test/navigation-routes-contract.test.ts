import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import { klmNavigation } from '@/klm/navigation';

// Impede link morto no menu: todo item.path declarado em klm/navigation.ts precisa
// corresponder a uma rota REGISTRADA em App.tsx dentro do grupo "/app" protegido
// (rota real + página que consome API real), nunca só um item de menu "decorativo".
function readAppSource() {
  return fs.readFileSync(path.resolve(process.cwd(), 'src', 'App.tsx'), 'utf8');
}

function extractAppChildRoutes(appSource: string): Set<string> {
  const startMarker = '<Route path="/app" element={<ProtectedRoute><WorkspaceExperience /></ProtectedRoute>}>';
  const startIndex = appSource.indexOf(startMarker);
  if (startIndex === -1) {
    throw new Error('Bloco de rotas "/app" não encontrado em App.tsx — contrato de navegação não pôde ser verificado.');
  }
  const endIndex = appSource.indexOf('<Route path="*"', startIndex);
  const block = appSource.slice(startIndex, endIndex === -1 ? undefined : endIndex);

  const paths = new Set<string>(['/app']); // <Route index .../> cobre o path "/app"
  const routePathPattern = /<Route\s+path="([^"]+)"/g;
  let match: RegExpExecArray | null;
  while ((match = routePathPattern.exec(block)) !== null) {
    const relativePath = match[1].split('/')[0]; // ignora sub-rotas com params (ex.: "playbooks/:playbookId")
    paths.add(`/app/${relativePath}`);
  }
  return paths;
}

describe('KLM navigation → App.tsx route contract', () => {
  const registeredRoutes = extractAppChildRoutes(readAppSource());

  it.each(klmNavigation.map((item) => [item.path, item.label] as const))(
    'menu item %s (%s) resolve para uma rota registrada em App.tsx',
    (itemPath) => {
      const normalized = itemPath.split('?')[0];
      expect(registeredRoutes.has(normalized)).toBe(true);
    },
  );

  it('não possui nenhum path de menu fora do prefixo protegido /app', () => {
    for (const item of klmNavigation) {
      expect(item.path === '/app' || item.path.startsWith('/app/')).toBe(true);
    }
  });
});
