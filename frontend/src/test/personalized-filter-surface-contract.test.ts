import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = resolve(import.meta.dirname, '..');

const personalizedSurfaces = [
  'pages/Kanban.tsx',
  'pages/Lista.tsx',
  'pages/Automations.tsx',
  'pages/Campaigns.tsx',
  'klm/pages/AgendaPage.tsx',
  'klm/pages/ConversationsPage.tsx',
  'klm/pages/FunnelPage.tsx',
  'klm/pages/KpisPage.tsx',
  'klm/pages/OperationalModulePage.tsx',
  'klm/pages/PlaybooksPage.tsx',
  'klm/pages/SocialCommentsPage.tsx',
  'klm/pages/ToolsPage.tsx',
  'klm/pages/TrackingPage.tsx',
];

describe('personalized filter surface contract', () => {
  it.each(personalizedSurfaces)('%s exposes the reusable personalized filter', (file) => {
    const source = readFileSync(resolve(root, file), 'utf8');
    expect(source).toContain('PersonalizedFilterBuilder');
  });

  it('allows custom periods wherever period presets drive KPI queries', () => {
    for (const file of ['klm/pages/HomePage.tsx', 'klm/pages/KpisPage.tsx', 'klm/pages/TrackingPage.tsx']) {
      const source = readFileSync(resolve(root, file), 'utf8');
      expect(source).toContain('CustomPeriodButton');
    }
  });
});
