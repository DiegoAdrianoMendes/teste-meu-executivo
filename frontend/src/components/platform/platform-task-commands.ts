import type { PlatformTask, PlatformTaskCommand } from '@/services/platform-tasks';
import type { AuthUser } from '@/types';

export function availablePlatformTaskCommands(
  task: PlatformTask,
  user: AuthUser,
): PlatformTaskCommand[] {
  const pendingRound = [...task.reviewRounds]
    .reverse()
    .find((round) => round.state === 'pending');
  const submittedByCurrentUser = pendingRound?.submittedByUserId === user.id;
  if (!task.allowedCommands) return [];

  // Defense in depth for cached responses created before a concurrent review
  // submission: the submitter never receives a self-review action.
  return task.allowedCommands.filter((command) => (
    !submittedByCurrentUser
    || !['approve', 'request-changes', 'reject'].includes(command)
  ));
}
