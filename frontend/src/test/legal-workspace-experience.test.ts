import { describe, expect, it } from 'vitest';

import {
  legalNavigationGroups,
  klmNavigation,
  navigationGroupsForMode,
  navigationForWorkspaceMode,
  preservedNavigationGroups,
} from '@/klm/navigation';
import { workspaceExperienceForMode } from '@/klm/workspace-experience';

describe('legal workspace experience isolation', () => {
  it('preserves the existing shell and task experience for general and commercial modes', () => {
    for (const mode of ['general', 'commercial'] as const) {
      expect(workspaceExperienceForMode(mode)).toEqual({
        mode,
        navigationVariant: 'preserved',
        topbarVariant: 'preserved',
        taskExperience: 'standard',
      });
      expect(navigationGroupsForMode(mode)).toBe(preservedNavigationGroups);
    }
    const commercial = navigationForWorkspaceMode(klmNavigation, 'commercial', false);
    expect(commercial.find((item) => item.label === 'CRM')).toMatchObject({
      path: '/app/funil',
      group: 'clientes',
    });
    expect(commercial.find((item) => item.path === '/app')).toMatchObject({ label: 'Início' });
    expect(commercial.find((item) => item.path === '/app/conversas')).toMatchObject({ label: 'Conversas' });
    expect(klmNavigation.find((item) => item.path === '/app/atividade')).toMatchObject({ label: 'Atividade' });
  });

  it('activates the expanded hierarchy only in legal mode', () => {
    expect(workspaceExperienceForMode('legal')).toEqual({
      mode: 'legal',
      navigationVariant: 'legal_operations',
      topbarVariant: 'legal_operations',
      taskExperience: 'legal_360',
    });
    expect(navigationGroupsForMode('legal')).toBe(legalNavigationGroups);
    expect(legalNavigationGroups.map((group) => group.label)).toEqual([
      'Trabalho jurídico',
      'Clientes e casos',
      'Produção jurídica',
      'Gestão do escritório',
      'Sistema',
    ]);

    const legal = navigationForWorkspaceMode(klmNavigation, 'legal', false);
    expect(legal.find((item) => item.path === '/app')).toMatchObject({ label: 'Início jurídico' });
    expect(legal.find((item) => item.path === '/app/conversas')).toMatchObject({ label: 'Atendimentos e conversas' });
    expect(legal.find((item) => item.path === '/app/peticoes')).toMatchObject({ label: 'Peças e petições' });
    expect(legal.find((item) => item.path === '/app/atividade')).toMatchObject({ label: 'Atividade e auditoria' });
    expect(legal.find((item) => item.path === '/app/leads')).toMatchObject({
      label: 'Caso 360',
      group: 'clientes',
    });
    expect(legal.some((item) => item.path === '/app/funil')).toBe(false);
  });

  it('does not reuse or mutate the preserved group labels', () => {
    expect(preservedNavigationGroups.map((group) => group.label)).toEqual([
      'Área de trabalho',
      'Clientes',
      'Jurídico',
      'Gestão',
      'Sistema',
    ]);
    expect(legalNavigationGroups).not.toBe(preservedNavigationGroups);
  });
});
