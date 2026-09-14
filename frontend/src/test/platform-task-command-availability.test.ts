import { describe, expect, it } from 'vitest';

import { availablePlatformTaskCommands } from '@/components/platform/platform-task-commands';
import type { PlatformTask, PlatformTaskCommand } from '@/services/platform-tasks';
import type { AuthUser } from '@/types';

function user(input: {
  id: string;
  role?: string;
  root?: boolean;
}): AuthUser {
  return {
    id: input.id,
    name: input.id,
    isPlatformRoot: input.root ?? false,
    platformAccessActive: true,
    platformRolePackage: input.role ?? 'support',
    platformPermissions: [
      { module: 'platform_tasks', action: 'view' },
      { module: 'platform_tasks', action: 'review' },
    ],
  } as AuthUser;
}

function task(
  submittedByUserId: string,
  allowedCommands?: PlatformTaskCommand[],
): PlatformTask {
  return {
    id: 'task-1',
    title: 'Revisão segregada',
    status: 'in_progress',
    reviewRequired: true,
    reviewState: 'pending',
    reviewerUserId: 'reviewer-1',
    participants: [],
    reviewRounds: [
      {
        id: 'round-1',
        state: 'pending',
        submittedByUserId,
      },
    ],
    ...(allowedCommands ? { allowedCommands } : {}),
  } as PlatformTask;
}

describe('PlatformTask command segregation', () => {
  it('hides all review decisions from the non-root employee who submitted the round', () => {
    expect(availablePlatformTaskCommands(
      task('reviewer-1', ['approve', 'request-changes', 'reject']),
      user({ id: 'reviewer-1' }),
    ))
      .not.toEqual(expect.arrayContaining(['approve', 'request-changes', 'reject']));
  });

  it('offers review decisions to the distinct assigned reviewer', () => {
    expect(availablePlatformTaskCommands(
      task('executor-1', ['approve', 'request-changes', 'reject']),
      user({ id: 'reviewer-1' }),
    ))
      .toEqual(expect.arrayContaining(['approve', 'request-changes', 'reject']));
  });

  it('does not infer commands from an unrelated reviewer permission', () => {
    expect(availablePlatformTaskCommands(
      task('executor-1', []),
      user({ id: 'reviewer-2' }),
    )).toEqual([]);
  });

  it('never offers self-review, including to platform_owner', () => {
    const admin = user({ id: 'admin-1', role: 'platform_admin' });
    const root = user({ id: 'root-1', role: 'platform_owner', root: true });

    expect(availablePlatformTaskCommands(
      task('admin-1', ['approve', 'request-changes', 'reject']),
      admin,
    ))
      .not.toEqual(expect.arrayContaining(['approve', 'request-changes', 'reject']));
    expect(availablePlatformTaskCommands(task('executor-1', []), admin))
      .not.toEqual(expect.arrayContaining(['approve', 'request-changes', 'reject']));
    expect(availablePlatformTaskCommands(
      task('root-1', ['approve', 'request-changes', 'reject']),
      root,
    ))
      .not.toEqual(expect.arrayContaining(['approve', 'request-changes', 'reject']));
  });

  it('fails closed when allowedCommands is absent even for an owner with broad capabilities', () => {
    const owner = {
      ...user({ id: 'owner-1' }),
      platformPermissions: [
        { module: 'platform_tasks', action: 'view' },
        { module: 'platform_tasks', action: 'edit' },
        { module: 'platform_tasks', action: 'review' },
        { module: 'platform_tasks', action: 'complete' },
      ],
    } as AuthUser;
    const legacyPayloadWithoutManifest = {
      ...task('executor-1'),
      reviewRequired: false,
      reviewState: 'not_required',
      ownerUserId: 'owner-1',
      participants: [],
      reviewRounds: [],
      status: 'pending',
    } as PlatformTask;

    expect(availablePlatformTaskCommands(legacyPayloadWithoutManifest, owner)).toEqual([]);
  });

  it('uses the backend command manifest for team-manager authority', () => {
    const manager = {
      ...user({ id: 'manager-1', role: 'commercial_manager' }),
      platformPermissions: [
        { module: 'platform_tasks', action: 'view' },
        { module: 'platform_tasks', action: 'complete' },
      ],
    } as AuthUser;
    const managed = {
      ...task('executor-1'),
      allowedCommands: ['cancel'],
    } satisfies PlatformTask;

    expect(availablePlatformTaskCommands(managed, manager)).toEqual(['cancel']);
  });

  it('filters a stale backend review manifest when the current user submitted the round', () => {
    const stale = {
      ...task('reviewer-1'),
      allowedCommands: ['approve', 'request-changes', 'reject'],
    } satisfies PlatformTask;

    expect(availablePlatformTaskCommands(stale, user({ id: 'reviewer-1', root: true })))
      .toEqual([]);
  });
});
