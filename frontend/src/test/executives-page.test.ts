import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

function source(relativePath: string) {
  return fs.readFileSync(path.resolve(process.cwd(), 'src', relativePath), 'utf8');
}

describe('Meus Executivos product contract', () => {
  it('places the owner council immediately after CRM with a dedicated permission', () => {
    const navigation = source('klm/navigation.ts');
    const crm = navigation.indexOf("label: 'CRM', shortLabel: 'CRM', path: '/app/funil'");
    const executives = navigation.indexOf("label: 'Meus Executivos', shortLabel: 'Executivos', path: '/app/executivos'");

    expect(crm).toBeGreaterThan(-1);
    expect(executives).toBeGreaterThan(crm);
    expect(navigation.slice(crm, executives).match(/label:/g)).toHaveLength(1);
    expect(navigation).toContain("permission: { module: 'executives', action: 'view' }");
    expect(navigation).not.toContain("path: '/app/executivos', icon: Crown, group: 'operacao', permission: { module: 'agents'");
  });

  it('registers a native protected route independent from operational agents', () => {
    const app = source('App.tsx');

    expect(app).toContain('const KlmExecutivesPage = lazy(() => import("@/klm/pages/ExecutivesPage"))');
    expect(app).toContain('<Route path="executivos" element={<PermissionRoute module="executives"><KlmExecutivesPage /></PermissionRoute>} />');
    expect(app).toContain('<Route path="agentes" element={<PermissionRoute module="agents"><KlmAgentsPage /></PermissionRoute>} />');
  });

  it('keeps CEO mandatory, account context read-only, and refuses simulated responses', () => {
    const page = source('klm/pages/ExecutivesPage.tsx');

    expect(page).toContain("useState<ExecutiveKey[]>(['ceo'])");
    expect(page).toContain("if (key === 'ceo') return");
    expect(page).toContain('Conta inteira autorizada');
    expect(page).toContain('sem ações automáticas');
    expect(page).toContain('Nenhuma resposta simulada foi inserida');
    expect(page).toContain("['workspace_owner', 'workspace_admin'].includes(activeRole)");
  });

  it('uses backend persistence for chats, rounds, sources and reports', () => {
    const api = source('klm/api.ts');

    expect(api).toContain("'/api/v1/executives/profiles'");
    expect(api).toContain("'/api/v1/executives/threads'");
    expect(api).toContain('/api/v1/executives/reports');
    expect(api).toContain('requestedExecutives?: ExecutiveKey[]');
    expect(api).toContain('preventAdditionalInvites?: boolean');
  });
});
