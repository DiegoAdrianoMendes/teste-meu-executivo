import { Search, Bell, AlertTriangle, Info, CheckCircle2, LogOut, ArrowRightLeft, Bot, MessageSquare, Sparkles, Settings2 } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { SidebarTrigger } from '@/components/ui/sidebar';
import { useAppStore } from '@/store/app-context';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import type { Notification } from '@/types';
import { useNavigate } from 'react-router-dom';
import { BRAND, runtimeEnvironmentStatus } from '@/lib/brand';
import { roleLabel } from '@/lib/role-labels';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '@/services/notifications';
import {
  fetchPlatformNoticeInbox,
  markPlatformNoticeRead,
} from '@/services/platform-notices';

const iconMap: Record<Notification['type'], React.ReactNode> = {
  urgent: <AlertTriangle className="h-3.5 w-3.5 text-destructive shrink-0" />,
  warning: <MessageSquare className="h-3.5 w-3.5 text-amber-500 shrink-0" />,
  info: <Info className="h-3.5 w-3.5 text-primary shrink-0" />,
  success: <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />,
};

function timeAgo(ts: string) {
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}min`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

export function Topbar() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { updateFilters, state, dispatch, selectCard, logout } = useAppStore();
  const notificationQueryKey = ['notifications', state.activeWorkspace?.id ?? 'none'] as const;
  const notificationsQuery = useQuery({
    queryKey: notificationQueryKey,
    queryFn: () => fetchNotifications(50),
    enabled: Boolean(state.currentUser && state.activeWorkspace?.id),
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
    staleTime: 10_000,
    retry: 1,
  });
  const noticeInboxQuery = useQuery({
    queryKey: ['platform-notice-inbox', state.activeWorkspace?.id ?? 'none'],
    queryFn: () => fetchPlatformNoticeInbox(50),
    enabled: Boolean(state.currentUser && state.activeWorkspace?.id),
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
    staleTime: 10_000,
    retry: 1,
  });
  const platformNotifications: Notification[] = (noticeInboxQuery.data?.items ?? []).map((notice) => ({
    id: `platform-notice:${notice.receiptId}`,
    title: notice.title,
    message: notice.body,
    timestamp: notice.publishedAt,
    read: Boolean(notice.readAt),
    type: notice.requiresAcknowledgement && !notice.acknowledgedAt ? 'urgent' : 'info',
  }));
  const notifications = [...platformNotifications, ...(notificationsQuery.data ?? state.notifications)];
  const unread = notifications.reduce((count, item) => count + (item.read ? 0 : 1), 0);

  const readMutation = useMutation({
    mutationFn: markNotificationRead,
    onMutate: async (notificationId) => {
      await queryClient.cancelQueries({ queryKey: notificationQueryKey });
      queryClient.setQueryData<Notification[]>(notificationQueryKey, (current) =>
        current?.map((item) => item.id === notificationId ? { ...item, read: true } : item),
      );
      dispatch({ type: 'MARK_NOTIFICATION_READ', payload: notificationId });
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: notificationQueryKey }),
  });

  const readAllMutation = useMutation({
    mutationFn: markAllNotificationsRead,
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: notificationQueryKey });
      queryClient.setQueryData<Notification[]>(notificationQueryKey, (current) =>
        current?.map((item) => ({ ...item, read: true })),
      );
      state.notifications.forEach((item) => {
        if (!item.read) dispatch({ type: 'MARK_NOTIFICATION_READ', payload: item.id });
      });
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: notificationQueryKey }),
  });

  const platformReadMutation = useMutation({
    mutationFn: markPlatformNoticeRead,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['platform-notice-inbox', state.activeWorkspace?.id ?? 'none'] }),
  });

  const markAllRead = () => {
    if (unread === 0 || readAllMutation.isPending || platformReadMutation.isPending) return;
    if (notificationsQuery.data?.some((item) => !item.read) || state.notifications.some((item) => !item.read)) {
      readAllMutation.mutate();
    }
    const unreadPlatform = noticeInboxQuery.data?.items.filter((item) => !item.readAt) ?? [];
    if (unreadPlatform.length > 0) {
      void Promise.all(unreadPlatform.map((item) => markPlatformNoticeRead(item.receiptId)))
        .finally(() => queryClient.invalidateQueries({ queryKey: ['platform-notice-inbox', state.activeWorkspace?.id ?? 'none'] }));
    }
  };

  return (
    <header className="h-14 border-b bg-card/95 backdrop-blur flex items-center px-3 sm:px-4 gap-2 sm:gap-3 shrink-0 sticky top-0 z-30" style={{ boxShadow: 'var(--shadow-card)' }}>
      <SidebarTrigger className="text-muted-foreground hover:text-foreground" />

      <div className="flex-1 flex items-center max-w-md">
        <div className="relative w-full">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            placeholder="Buscar..."
            className="h-8 pl-8 text-sm bg-muted/50 border-0 focus-visible:ring-1"
            value={state.filters.search}
            onChange={e => updateFilters({ search: e.target.value })}
          />
        </div>
      </div>

      <div className="flex items-center gap-1.5 sm:gap-2">
        <div className="hidden xl:flex items-center gap-2 rounded-full border border-[hsl(var(--ai))]/25 bg-[hsl(var(--ai-surface))]/75 px-3 py-1.5 text-xs text-foreground shadow-sm">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-ai-gradient text-primary-foreground">
            <Bot className="h-3.5 w-3.5" />
          </span>
          <div className="leading-tight">
            <div className="flex items-center gap-1 font-semibold">
              {BRAND.ai.label}
              <Sparkles className="h-3 w-3 text-[hsl(var(--ai))]" />
            </div>
            <div className="text-2xs text-muted-foreground">{runtimeEnvironmentStatus()}</div>
          </div>
        </div>

        <Popover>
          <PopoverTrigger asChild>
            <Button variant="ghost" size="icon" className="relative h-8 w-8">
              <Bell className="h-4 w-4 text-muted-foreground" />
              {unread > 0 && (
                <span className="absolute -top-0.5 -right-0.5 h-4 min-w-[16px] rounded-full bg-destructive text-destructive-foreground text-2xs tabular-nums flex items-center justify-center font-bold px-1 animate-pulse">
                  {unread}
                </span>
              )}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-72 sm:w-80 p-0" align="end" sideOffset={8}>
            <div className="flex items-center justify-between px-3 py-2.5 border-b">
              <span className="text-sm font-semibold">Notificações</span>
              {unread > 0 && (
                <Button variant="ghost" size="sm" className="h-6 shrink-0 text-xs text-primary" onClick={markAllRead}>
                  Marcar lidas
                </Button>
              )}
            </div>
            <div className="max-h-[320px] overflow-y-auto">
              {notifications.length === 0 ? (
                <p className="text-xs text-muted-foreground text-center py-8">Nenhuma notificação</p>
              ) : (
                notifications.map(n => (
                  <button
                    key={n.id}
                    className={`w-full text-left px-3 py-2.5 flex items-start gap-2.5 hover:bg-muted/50 transition-colors border-b last:border-0 ${!n.read ? 'bg-primary/5' : ''}`}
                    onClick={() => {
                      if (!n.read && n.id.startsWith('platform-notice:')) {
                        platformReadMutation.mutate(n.id.replace('platform-notice:', ''));
                      } else if (!n.read) {
                        readMutation.mutate(n.id);
                      }
                      if (n.cardId) selectCard(n.cardId);
                    }}
                  >
                    <div className="mt-0.5">{iconMap[n.type]}</div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-2">
                        <span className={`min-w-0 text-sm font-medium truncate ${!n.read ? 'text-foreground' : 'text-muted-foreground'}`}>
                          {n.title}
                        </span>
                        <span className="text-2xs tabular-nums text-muted-foreground shrink-0">{timeAgo(n.timestamp)}</span>
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{n.message}</p>
                    </div>
                    {!n.read && <div className="h-2 w-2 rounded-full bg-primary shrink-0 mt-1.5" />}
                  </button>
                ))
              )}
            </div>
          </PopoverContent>
        </Popover>

        <div className="h-6 w-px bg-border hidden sm:block" />

        {state.activeWorkspace && (
          <div className="hidden lg:flex items-center gap-2 rounded-full border bg-muted/50 px-3 py-1.5 text-xs">
            <span className="max-w-[180px] truncate font-medium">{state.activeWorkspace.name}</span>
            {(state.session?.availableWorkspaces.length ?? 0) > 1 && (
              <Button
                variant="ghost"
                size="icon"
                className="h-5 w-5"
                onClick={() => navigate('/workspace/select')}
                title="Trocar workspace"
              >
                <ArrowRightLeft className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        )}

        <Popover>
          <PopoverTrigger asChild>
            <Button variant="ghost" className="h-9 gap-2 px-1.5 sm:px-2" aria-label="Abrir menu da conta">
              <Avatar className="h-7 w-7">
                <AvatarFallback className="bg-primary text-xs font-semibold text-primary-foreground">
                  {state.currentUser?.initials ?? 'US'}
                </AvatarFallback>
              </Avatar>
              <div className="hidden min-w-0 flex-col items-start md:flex">
                <span className="max-w-[160px] truncate text-sm font-semibold leading-none">{state.currentUser?.name?.split(' ').slice(0, 2).join(' ') ?? 'Usuário'}</span>
                <span className="mt-0.5 max-w-[160px] truncate text-2xs leading-none text-muted-foreground">{roleLabel(state.currentUser?.role)}</span>
              </div>
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-64 p-1.5" align="end" sideOffset={8}>
            <div className="border-b px-2.5 py-2.5">
              <p className="truncate text-sm font-medium">{state.currentUser?.name ?? 'Usuário'}</p>
              <p className="mt-0.5 truncate text-xs text-muted-foreground">{state.currentUser?.email ?? state.activeWorkspace?.name}</p>
            </div>
            <div className="py-1">
              <Button
                variant="ghost"
                className="h-9 w-full justify-start text-sm"
                onClick={() => navigate('/app/preferencias')}
              >
                <Settings2 className="mr-2 h-4 w-4" />
                Minhas preferências
              </Button>
              {(state.session?.availableWorkspaces.length ?? 0) > 1 && (
                <Button
                  variant="ghost"
                  className="h-9 w-full justify-start text-sm"
                  onClick={() => navigate('/workspace/select')}
                >
                  <ArrowRightLeft className="mr-2 h-4 w-4" />
                  Trocar workspace
                </Button>
              )}
            </div>
            <div className="border-t pt-1">
              <Button
                variant="ghost"
                className="h-9 w-full justify-start text-sm text-destructive hover:text-destructive"
                onClick={async () => { await logout(); }}
              >
                <LogOut className="mr-2 h-4 w-4" />
                Sair
              </Button>
            </div>
          </PopoverContent>
        </Popover>
      </div>
    </header>
  );
}
