import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

const source = fs.readFileSync(
  path.resolve(process.cwd(), 'src', 'pages', 'PlatformSupportTicketsPanel.tsx'),
  'utf8',
);

describe('vínculos de evidência no SAC do Blackbox', () => {
  it('oferece seletor pesquisável quando existe registro canônico de release', () => {
    expect(source).not.toContain('id="linked-release"');
    expect(source).not.toContain('placeholder="UUID da publicação"');
    expect(source).toContain('entityType="releases"');
    expect(source).toContain('label="Selecionar release relacionada"');
  });

  it('salva os vínculos tipados sem exigir identificador manual', () => {
    const handlerStart = source.indexOf('const saveLinkedRecords = () => {');
    const handlerEnd = source.indexOf('const metrics =', handlerStart);
    const saveHandler = source.slice(handlerStart, handlerEnd);

    expect(handlerStart).toBeGreaterThan(-1);
    expect(handlerEnd).toBeGreaterThan(handlerStart);
    expect(saveHandler).toContain('Object.keys(linkedRecords)');
    expect(saveHandler).not.toContain('UUID');
  });

  it('usa lookups pesquisáveis para conta e solicitante sem coleções fixas de 200 itens', () => {
    expect(source).toContain('entityType="accounts" purpose="support_ticket_create"');
    expect(source).toContain('entityType="users" purpose="support_ticket_reporter"');
    expect(source).toContain('entityType="accounts" purpose="support_ticket_filter"');
    expect(source).not.toContain('/api/v1/platform/workspaces?limit=200');
    expect(source).not.toContain('fetchPlatformPeople({ workspaceId: createDraft.workspaceId, limit: 200 })');
    expect(source).not.toContain('workspace?.name ?? ticket.workspaceId');
  });
});
