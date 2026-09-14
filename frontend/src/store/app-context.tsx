import React, { createContext, useContext, useReducer, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { AppState, Filters, MicroStatus, KanbanColumnId, Priority, ModuleId, MatterCard, ClientClassification, AuthUser, AiMode, MessageStatus, WhatsAppConnectionSummary, WhatsAppMessage, Permission, PermissionModule, SessionContext, WorkspaceSummary } from '@/types';
import { KLM_KANBAN_COLUMNS } from '@/klm/kanban-columns';
import {
  CLIENT_SESSION_EXPIRED_EVENT,
  expireClientSession,
  fetchCurrentSession,
  getAccessTokenExpiresAt,
  getSavedSession,
  getSavedUser,
  getToken,
  removeToken,
  refreshAccessToken,
  requiresPasswordChange,
  saveSession,
  SessionAccessError,
} from '@/services/auth';
import {
  addMatterNote,
  fetchMatterDetail,
  fetchMatters,
  overrideAgentRun,
  retryAgentRun,
  toggleMatterTask,
  updateMatterDetail,
} from '@/services/matters';
import type { Task as MatterTask } from '@/types';
import { fetchReportsSummary } from '@/services/reports';
import { fetchApprovalItems, resolveApprovalItem } from '@/services/api';
import {
  handoffConversation,
  fetchWhatsAppConnections,
  fetchWhatsAppContacts,
  fetchWhatsAppConversation,
  markWhatsAppConversationRead,
  sendWhatsAppMessage,
} from '@/services/whatsapp';
import { fetchWebSocketTicket } from '@/services/http';
import { resolveWebSocketBase } from '@/services/api-origin';
import type { SupportAccessActivation } from '@/services/support-access';
import { supportPermissions } from '@/support-mode/scopes';
import {
  abortAllSupportModeRequests,
  activateStagedSupportMode,
  clearSupportModeCredential,
  completeSupportModeEnding,
  discardStagedSupportMode,
  getSupportModePhase,
  subscribeToSupportMode,
  type SupportModeSession,
} from '@/support-mode/session';
import { filterMatterCards } from '@/lib/matter-card-filters';
import type {
  CommercialEntitlementKey,
  EffectiveCapability,
  EffectiveCapabilityManifest,
} from '@/services/platform-commercial-catalog';
import {
  classifyWorkspaceCapabilityManifestError,
  fetchWorkspaceCapabilityManifest,
  getWorkspaceCapability,
  isWorkspaceCapabilityUsable,
  isWorkspaceCapabilityVisible,
  type WorkspaceCapabilityManifestStatus,
} from '@/services/workspace-capabilities';

const REALTIME_RETRY_BASE_MS = 1_000;
const REALTIME_RETRY_MAX_MS = 30_000;
const REALTIME_STABLE_CONNECTION_MS = 30_000;
const REALTIME_RETRY_JITTER_RATIO = 0.2;
const REALTIME_HEARTBEAT_MS = 25_000;
const REALTIME_CONNECT_TIMEOUT_MS = 10_000;
const REALTIME_REFRESH_DEBOUNCE_MS = 500;
const ACCESS_TOKEN_REFRESH_LEAD_MS = 60_000;
const ACCESS_TOKEN_REFRESH_FALLBACK_MS = 20 * 60_000;

function calculateRealtimeRetryDelay(
  attempt: number,
  random: () => number = Math.random,
): number {
  const maxExponent = Math.ceil(Math.log2(REALTIME_RETRY_MAX_MS / REALTIME_RETRY_BASE_MS));
  const exponent = Math.min(Math.max(0, Math.floor(attempt)), maxExponent);
  const exponentialDelay = Math.min(
    REALTIME_RETRY_MAX_MS,
    REALTIME_RETRY_BASE_MS * (2 ** exponent),
  );
  const randomValue = Math.min(1, Math.max(0, random()));
  const jitterMultiplier = 1 + ((randomValue * 2) - 1) * REALTIME_RETRY_JITTER_RATIO;
  return Math.min(REALTIME_RETRY_MAX_MS, Math.round(exponentialDelay * jitterMultiplier));
}

// ── Initial State ──

const initialFilters: Filters = {
  search: '',
  status: [],
  priority: [],
  urgency: [],
  agentId: [],
  columnId: [],
  classification: [],
  leadSource: [],
  closeIntent: [],
  documentReadiness: [],
  isBlocked: null,
  needsApproval: null,
  hasUnreadMessages: null,
  hasFinancialPending: null,
  docScoreBelow: null,
  updatedWithinDays: null,
  minClaimValue: null,
  maxClaimValue: null,
};

const MODULE_PERMISSION_MAP: Record<ModuleId, PermissionModule> = {
  dashboard: 'dashboard',
  kanban: 'kanban',
  lista: 'lista',
  relatorios: 'relatorios',
  aprovacoes: 'aprovacoes',
  configuracoes: 'configuracoes',
  mensagens: 'mensagens',
  campanhas: 'mensagens',
  admin: 'admin',
  automacoes: 'automations',
  platform: 'admin',
  'whatsapp-inbox': 'mensagens',
  'whatsapp-campaigns': 'mensagens',
  'whatsapp-templates': 'mensagens',
  'whatsapp-meta-templates': 'mensagens',
  'whatsapp-automations': 'automations',
  'whatsapp-connections': 'integrations',
  'instagram-inbox': 'mensagens',
  'instagram-comments': 'mensagens',
  'instagram-automations': 'automations',
  'instagram-approved': 'mensagens',
  'instagram-connections': 'integrations',
  'instagram-entrypoints': 'integrations',
  'messenger-inbox': 'mensagens',
  'messenger-comments': 'mensagens',
  'messenger-automations': 'automations',
  'messenger-approved': 'mensagens',
  'messenger-connections': 'integrations',
  'messenger-entrypoints': 'integrations',
};

const savedUser = getSavedUser();
const savedSession = getSavedSession();
const savedToken = getToken();

const initialState: AppState = {
  cards: [],
  columns: KLM_KANBAN_COLUMNS,
  filters: initialFilters,
  selectedCardId: null,
  drawerOpen: false,
  approvalItems: [],
  metrics: [],
  notifications: [],
  loading: false,
  practiceArea: 'trabalhista',
  activeModule: 'dashboard',
  isAuthenticated: Boolean(savedUser && savedToken),
  currentUser: savedUser ? {
    ...savedUser,
    isInternalStaff: savedUser.isInternalStaff,
  } : null,
  session: savedSession,
  activeWorkspace: savedSession?.activeWorkspace ?? null,
  conversations: [],
};

// ── Actions ──

type Action =
  | { type: 'SET_CARDS'; payload: AppState['cards'] }
  | { type: 'SET_CONVERSATIONS'; payload: AppState['conversations'] }
  | { type: 'SELECT_CARD'; payload: string | null }
  | { type: 'TOGGLE_DRAWER'; payload?: boolean }
  | { type: 'UPDATE_FILTERS'; payload: Partial<Filters> }
  | { type: 'RESET_FILTERS' }
  | { type: 'UPDATE_CARD_STATUS'; payload: { cardId: string; microStatus: MicroStatus; columnId?: KanbanColumnId } }
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'UPDATE_METRICS'; payload: AppState['metrics'] }
  | { type: 'SET_APPROVALS'; payload: AppState['approvalItems'] }
  | { type: 'RESOLVE_APPROVAL'; payload: { id: string; status: 'approved' | 'rejected' | 'changes_requested' } }
  | { type: 'MARK_NOTIFICATION_READ'; payload: string }
  | { type: 'SET_PRACTICE_AREA'; payload: AppState['practiceArea'] }
  | { type: 'SET_ACTIVE_MODULE'; payload: ModuleId }
  | { type: 'MOVE_CARD'; payload: { cardId: string; newColumnId: KanbanColumnId } }
  | { type: 'UPDATE_CARD_FIELD'; payload: { cardId: string; field: keyof MatterCard; value: unknown } }
  | { type: 'TOGGLE_TASK_STATUS'; payload: { cardId: string; taskId: string } }
  | { type: 'UPSERT_CARD_TASK'; payload: { cardId: string; task: Pick<MatterTask, 'id'> & Partial<MatterTask> } }
  | { type: 'ADD_CARD_NOTE'; payload: { cardId: string; note: string } }
  | { type: 'LOGIN'; payload: { user: AuthUser; session: SessionContext } }
  | { type: 'UPDATE_CURRENT_USER'; payload: Partial<AuthUser> }
  | { type: 'SET_SESSION'; payload: SessionContext | null }
  | { type: 'PURGE_PROTECTED_DATA' }
  | { type: 'UPSERT_CARD'; payload: MatterCard }
  | { type: 'UPSERT_CONVERSATION'; payload: AppState['conversations'][0] }
  | { type: 'LOGOUT' }
  | { type: 'TOGGLE_AI_MODE'; payload: { conversationId: string; mode: AiMode } }
  | { type: 'MARK_CONVERSATION_READ'; payload: string }
  | { type: 'SEND_MESSAGE'; payload: { conversationId: string; message: WhatsAppMessage } }
  | { type: 'UPDATE_MESSAGE_STATUS'; payload: { conversationId: string; messageId: string; status: MessageStatus } };

function appReducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'SET_CARDS':
      return { ...state, cards: action.payload };
    case 'SET_CONVERSATIONS':
      return {
        ...state,
        conversations: action.payload
          .map((incoming) => {
            const existing = state.conversations.find((conversation) => conversation.id === incoming.id);
            return existing
              ? {
                  ...incoming,
                  messages: existing.messages.length > 0 ? existing.messages : incoming.messages,
                }
              : incoming;
          })
          .sort((left, right) => new Date(right.lastMessageTime).getTime() - new Date(left.lastMessageTime).getTime()),
      };
    case 'SELECT_CARD':
      return { ...state, selectedCardId: action.payload, drawerOpen: action.payload !== null };
    case 'TOGGLE_DRAWER':
      return { ...state, drawerOpen: action.payload ?? !state.drawerOpen, selectedCardId: action.payload === false ? null : state.selectedCardId };
    case 'UPDATE_FILTERS':
      return { ...state, filters: { ...state.filters, ...action.payload } };
    case 'RESET_FILTERS':
      return { ...state, filters: initialFilters };
    case 'UPDATE_CARD_STATUS':
      return {
        ...state,
        cards: state.cards.map(c =>
          c.id === action.payload.cardId
            ? { ...c, microStatus: action.payload.microStatus, columnId: action.payload.columnId ?? c.columnId, updatedAt: new Date().toISOString() }
            : c
        ),
      };
    case 'SET_LOADING':
      return { ...state, loading: action.payload };
    case 'UPDATE_METRICS':
      return { ...state, metrics: action.payload };
    case 'SET_APPROVALS':
      return { ...state, approvalItems: action.payload };
    case 'RESOLVE_APPROVAL': {
      const approval = state.approvalItems.find(a => a.id === action.payload.id);
      const isApproved = action.payload.status === 'approved';
      return {
        ...state,
        approvalItems: state.approvalItems.map(item =>
          item.id === action.payload.id
            ? {
                ...item,
                status: action.payload.status,
                resolvedAt: new Date().toISOString(),
                resolvedBy: state.currentUser?.id,
              }
            : item
        ),
        cards: approval ? state.cards.map(c => {
          if (c.id !== approval.cardId) return c;
          if (isApproved) {
            return { ...c, needsApproval: false, updatedAt: new Date().toISOString() };
          } else {
            return { ...c, microStatus: 'ajustes_solicitados' as MicroStatus, updatedAt: new Date().toISOString() };
          }
        }) : state.cards,
      };
    }
    case 'MARK_NOTIFICATION_READ':
      return {
        ...state,
        notifications: state.notifications.map(n =>
          n.id === action.payload ? { ...n, read: true } : n
        ),
      };
    case 'SET_PRACTICE_AREA':
      return { ...state, practiceArea: action.payload };
    case 'SET_ACTIVE_MODULE':
      return { ...state, activeModule: action.payload };
    case 'MOVE_CARD':
      return {
        ...state,
        cards: state.cards.map(c =>
          c.id === action.payload.cardId
            ? { ...c, columnId: action.payload.newColumnId, updatedAt: new Date().toISOString() }
            : c
        ),
      };
    case 'UPDATE_CARD_FIELD':
      return {
        ...state,
        cards: state.cards.map(c =>
          c.id === action.payload.cardId
            ? { ...c, [action.payload.field]: action.payload.value, updatedAt: new Date().toISOString() }
            : c
        ),
      };
    case 'TOGGLE_TASK_STATUS':
      return {
        ...state,
        cards: state.cards.map(c => {
          if (c.id !== action.payload.cardId) return c;
          const tasks = c.tasks.map(t => {
            if (t.id !== action.payload.taskId) return t;
            const newStatus = t.status === 'done' ? 'pending' : 'done';
            return { ...t, status: newStatus as typeof t.status };
          });
          const openTasks = tasks.filter(t => t.status !== 'done').length;
          return { ...c, tasks, openTasks, updatedAt: new Date().toISOString() };
        }),
      };
    case 'UPSERT_CARD_TASK':
      return {
        ...state,
        cards: state.cards.map(c => {
          if (c.id !== action.payload.cardId) return c;
          const tasks = c.tasks.map(t => (t.id === action.payload.task.id ? { ...t, ...action.payload.task } : t));
          const openTasks = tasks.filter(t => t.status !== 'done').length;
          return { ...c, tasks, openTasks, updatedAt: new Date().toISOString() };
        }),
      };
    case 'ADD_CARD_NOTE':
      return {
        ...state,
        cards: state.cards.map(c => {
          if (c.id !== action.payload.cardId) return c;
          const newEvent = {
            id: `tl-${Date.now()}`,
            type: 'comment' as const,
            title: 'Observação adicionada',
            description: action.payload.note,
            timestamp: new Date().toISOString(),
            agentId: state.currentUser?.id ?? 'system:local',
          };
          return { ...c, timeline: [newEvent, ...c.timeline], updatedAt: new Date().toISOString() };
        }),
      };
    case 'LOGIN':
      return {
        ...state,
        isAuthenticated: true,
        currentUser: action.payload.user,
        session: action.payload.session,
        activeWorkspace: action.payload.session.activeWorkspace,
      };
    case 'UPDATE_CURRENT_USER':
      return {
        ...state,
        currentUser: state.currentUser ? { ...state.currentUser, ...action.payload } : state.currentUser,
      };
    case 'SET_SESSION':
      return {
        ...state,
        session: action.payload,
        activeWorkspace: action.payload?.activeWorkspace ?? null,
      };
    case 'PURGE_PROTECTED_DATA':
      return {
        ...state,
        cards: [],
        conversations: [],
        approvalItems: [],
        metrics: [],
        notifications: [],
        selectedCardId: null,
        drawerOpen: false,
      };
    case 'UPSERT_CARD': {
      const exists = state.cards.some((card) => card.id === action.payload.id);
      return {
        ...state,
        cards: exists
          ? state.cards.map((card) => (card.id === action.payload.id ? action.payload : card))
          : [action.payload, ...state.cards],
      };
    }
    case 'UPSERT_CONVERSATION': {
      const existing = state.conversations.find((conversation) => conversation.id === action.payload.id);
      const nextConversation = existing
        ? {
            ...existing,
            ...action.payload,
            aiMode: action.payload.aiMode ?? existing.aiMode,
          }
        : action.payload;
      const conversations = existing
        ? state.conversations.map((conversation) => (conversation.id === action.payload.id ? nextConversation : conversation))
        : [nextConversation, ...state.conversations];
      return {
        ...state,
        conversations: conversations.sort((left, right) => new Date(right.lastMessageTime).getTime() - new Date(left.lastMessageTime).getTime()),
      };
    }
    case 'LOGOUT':
      return {
        ...state,
        isAuthenticated: false,
        currentUser: null,
        session: null,
        activeWorkspace: null,
        activeModule: 'dashboard',
        conversations: [],
      };
    case 'TOGGLE_AI_MODE':
      return {
        ...state,
        conversations: state.conversations.map(conv =>
          conv.id === action.payload.conversationId
            ? { ...conv, aiMode: action.payload.mode }
            : conv
        ),
      };
    case 'MARK_CONVERSATION_READ':
      return {
        ...state,
        conversations: state.conversations.map(conv =>
          conv.id === action.payload
            ? { ...conv, unreadCount: 0, messages: conv.messages.map(m => m.isFromClient ? { ...m, status: 'read' as const } : m) }
            : conv
        ),
      };
    case 'SEND_MESSAGE':
      return {
        ...state,
        conversations: state.conversations.map(conv => {
          if (conv.id !== action.payload.conversationId) return conv;
          return {
            ...conv,
            messages: [...conv.messages, action.payload.message],
            lastMessage: action.payload.message.text,
            lastMessageTime: action.payload.message.timestamp,
          };
        }),
      };
    case 'UPDATE_MESSAGE_STATUS':
      return {
        ...state,
        conversations: state.conversations.map((conv) => {
          if (conv.id !== action.payload.conversationId) return conv;
          return {
            ...conv,
            messages: conv.messages.map((message) =>
              message.id === action.payload.messageId
                ? { ...message, status: action.payload.status }
                : message
            ),
          };
        }),
      };
    default:
      return state;
  }
}

// ── Context ──

/** Campos que `updateCardField` sabe persistir: o `MatterUpdate` do backend descarta campo
 *  desconhecido em silêncio e recusa estágio/coluna; a allowlist evita o "200 que não mudou nada". */
export type EditableMatterField =
  | 'starsRating'
  | 'strategyNotes'
  | 'nextAction'
  | 'executiveSummary'
  | 'isBlocked'
  | 'needsApproval'
  | 'caseNumber'
  | 'claimValue'
  | 'responsibleHuman'
  | 'currentAgent';
const MATTER_FIELD_TO_API: Record<EditableMatterField, string> = {
  starsRating: 'starsRating',
  strategyNotes: 'strategyNotes',
  nextAction: 'nextAction',
  executiveSummary: 'executiveSummary',
  isBlocked: 'isBlocked',
  needsApproval: 'needsApproval',
  caseNumber: 'caseNumber',
  claimValue: 'claimValue',
  responsibleHuman: 'responsibleHumanId',
  currentAgent: 'currentAgentId',
};

interface AppContextValue {
  state: AppState;
  capabilityManifest: EffectiveCapabilityManifest | null;
  capabilityManifestStatus: WorkspaceCapabilityManifestStatus;
  supportMode: SupportModeSession | null;
  supportModeTeardownPending: boolean;
  dispatch: React.Dispatch<Action>;
  selectCard: (id: string | null) => void;
  closeDrawer: () => void;
  updateFilters: (filters: Partial<Filters>) => void;
  resolveApproval: (id: string, status: 'approved' | 'rejected' | 'changes_requested', resolutionReason?: string) => Promise<void>;
  getSelectedCard: () => AppState['cards'][0] | undefined;
  getFilteredCards: () => AppState['cards'];
  getPendingApprovals: () => AppState['approvalItems'];
  getUnreadNotifications: () => number;
  setActiveModule: (module: ModuleId) => void;
  navigateWithFilter: (filters: Partial<Filters>, module?: ModuleId) => void;
  moveCard: (cardId: string, newColumnId: KanbanColumnId) => void;
  updateCardField: (cardId: string, field: EditableMatterField, value: unknown) => Promise<void>;
  toggleTask: (cardId: string, taskId: string) => Promise<void>;
  addNote: (cardId: string, note: string) => Promise<void>;
  refreshCards: () => Promise<void>;
  refreshCard: (cardId: string) => Promise<void>;
  refreshConversations: () => Promise<void>;
  refreshConversation: (conversationId: string) => Promise<void>;
  overrideRun: (matterId: string, runId: string, decision: 'accepted' | 'corrected' | 'rejected', notes?: string, correctedPayload?: Record<string, unknown>) => Promise<void>;
  retryRun: (matterId: string, runId: string) => Promise<void>;
  login: (user: AuthUser, session: SessionContext) => void;
  selectWorkspace: (session: SessionContext) => void;
  enterSupportMode: (
    activation: SupportAccessActivation,
    workspace: { id: string; name: string; slug?: string | null },
  ) => Promise<void>;
  exitSupportMode: () => Promise<void>;
  logout: () => Promise<void>;
  sendMessage: (conversationId: string, text: string, connectionId?: string | null) => Promise<void>;
  refreshWhatsAppConnections: () => Promise<WhatsAppConnectionSummary[]>;
  toggleAiMode: (conversationId: string, mode: AiMode) => Promise<void>;
  markConversationRead: (conversationId: string) => Promise<void>;
  getTotalUnreadMessages: () => number;
  getCapability: (key: CommercialEntitlementKey) => EffectiveCapability | null;
  isCapabilityVisible: (key: CommercialEntitlementKey) => boolean;
  canUseCapability: (key: CommercialEntitlementKey) => boolean;
  canAccessModule: (module: ModuleId) => boolean;
  can: (module: Permission['module'], action?: Permission['action']) => boolean;
}

const AppContext = createContext<AppContextValue | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(appReducer, initialState);
  // Leitura síncrona dos cards dentro de callbacks assíncronos (mesmo padrão de realtimeHandlersRef).
  const cardsRef = useRef(state.cards);
  cardsRef.current = state.cards;
  const [supportMode, setSupportMode] = useState<SupportModeSession | null>(null);
  const [supportModeTeardownPending, setSupportModeTeardownPending] = useState(() => (
    getSupportModePhase() === 'bootstrap' || getSupportModePhase() === 'ending'
  ));
  const queryClient = useQueryClient();
  const activeWorkspaceId = state.activeWorkspace?.id ?? null;
  const capabilityManifestQuery = useQuery({
    queryKey: ['workspace-capability-manifest', activeWorkspaceId],
    queryFn: ({ signal }) => fetchWorkspaceCapabilityManifest(signal),
    enabled: Boolean(
      state.isAuthenticated
      && activeWorkspaceId
      && state.session?.surface !== 'platform'
    ),
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  });
  const capabilityManifest = capabilityManifestQuery.data ?? null;
  const capabilityManifestStatus: WorkspaceCapabilityManifestStatus = !activeWorkspaceId
    || !state.isAuthenticated
    || state.session?.surface === 'platform'
    ? 'idle'
    : capabilityManifestQuery.isPending
      ? 'loading'
      : capabilityManifestQuery.isSuccess
        ? 'ready'
        : classifyWorkspaceCapabilityManifestError(capabilityManifestQuery.error);
  const sessionRef = useRef<SessionContext | null>(state.session);
  const supportModeRef = useRef<SupportModeSession | null>(null);
  const supportReturnSessionRef = useRef<SessionContext | null>(null);
  const supportCleanupPromiseRef = useRef<Promise<void> | null>(null);

  useEffect(() => {
    const handleSessionExpired = () => {
      queryClient.clear();
      dispatch({ type: 'LOGOUT' });
    };
    window.addEventListener(CLIENT_SESSION_EXPIRED_EVENT, handleSessionExpired);
    return () => window.removeEventListener(CLIENT_SESSION_EXPIRED_EVENT, handleSessionExpired);
  }, [queryClient]);

  const finishSupportMode = useCallback((): Promise<void> => {
    if (supportCleanupPromiseRef.current) return supportCleanupPromiseRef.current;
    setSupportModeTeardownPending(true);
    abortAllSupportModeRequests();
    supportCleanupPromiseRef.current = (async () => {
      await queryClient.cancelQueries();
      queryClient.clear();
      dispatch({ type: 'PURGE_PROTECTED_DATA' });
      const returnSession = supportReturnSessionRef.current ?? sessionRef.current;
      const completed = completeSupportModeEnding(() => {
        supportReturnSessionRef.current = null;
        sessionRef.current = returnSession;
        dispatch({ type: 'SET_SESSION', payload: returnSession });
        setSupportMode(null);
        supportModeRef.current = null;
        setSupportModeTeardownPending(false);
      });
      if (!completed) {
        throw new Error('O modo suporte permanece bloqueado porque o tombstone seguro não pôde ser removido.');
      }
    })().finally(() => {
      supportCleanupPromiseRef.current = null;
    });
    return supportCleanupPromiseRef.current;
  }, [queryClient]);

  const enterSupportMode = useCallback(async (
    activation: SupportAccessActivation,
    workspace: { id: string; name: string; slug?: string | null },
  ) => {
    if (
      activation.grant.workspaceId !== workspace.id
      || !activation.grant.expiresAt
      || activation.grant.state !== 'active'
    ) {
      throw new Error('O grant ativo não corresponde ao workspace selecionado.');
    }
    const returnSession = state.session;
    supportReturnSessionRef.current = returnSession;
    setSupportModeTeardownPending(true);
    abortAllSupportModeRequests();
    try {
      // See finishSupportMode: cancellation is signalled synchronously and
      // the protected cache must be purged without depending on queryFns.
      void queryClient.cancelQueries();
      queryClient.clear();
      dispatch({ type: 'PURGE_PROTECTED_DATA' });
      const activeSupportMode = activateStagedSupportMode({
        grantId: activation.grant.id,
        workspaceId: workspace.id,
        workspaceName: workspace.name,
        workspaceSlug: workspace.slug,
        sessionType: activation.grant.ticketReference.startsWith('ROOT-')
          ? 'root_direct'
          : 'support_approved',
      });
      const workspaceSummary: WorkspaceSummary = {
        id: workspace.id,
        name: workspace.name,
        slug: workspace.slug ?? 'support-mode',
        status: 'active',
        isActive: true,
        onboardingCompleted: true,
      };
      const supportSession: SessionContext = {
        surface: 'support',
        activeWorkspace: workspaceSummary,
        availableWorkspaces: [],
        permissions: supportPermissions(activeSupportMode.scopes),
        isInternalStaff: true,
        activeRole: 'support_access',
      };
      supportModeRef.current = activeSupportMode;
      sessionRef.current = supportSession;
      setSupportMode(activeSupportMode);
      dispatch({ type: 'SET_SESSION', payload: supportSession });
      setSupportModeTeardownPending(false);
    } catch (error) {
      discardStagedSupportMode();
      supportReturnSessionRef.current = null;
      supportModeRef.current = null;
      sessionRef.current = returnSession;
      setSupportMode(null);
      dispatch({ type: 'SET_SESSION', payload: returnSession });
      setSupportModeTeardownPending(getSupportModePhase() === 'bootstrap');
      throw error;
    }
  }, [queryClient, state.session]);

  const exitSupportMode = useCallback(async () => {
    clearSupportModeCredential('manual');
    await finishSupportMode();
  }, [finishSupportMode]);

  useEffect(() => subscribeToSupportMode((session, reason) => {
    if (session !== null) return;
    setSupportModeTeardownPending(true);
    if (reason === 'pagehide') {
      abortAllSupportModeRequests();
      void queryClient.cancelQueries();
      queryClient.clear();
      dispatch({ type: 'PURGE_PROTECTED_DATA' });
      const returnSession = supportReturnSessionRef.current ?? sessionRef.current;
      supportReturnSessionRef.current = null;
      supportModeRef.current = null;
      sessionRef.current = returnSession;
      setSupportMode(null);
      dispatch({ type: 'SET_SESSION', payload: returnSession });
      return;
    }
    if (supportModeRef.current || getSupportModePhase() === 'bootstrap' || getSupportModePhase() === 'ending') {
      void finishSupportMode().catch(() => undefined);
    }
  }), [finishSupportMode, queryClient]);

  useEffect(() => {
    if (!supportModeRef.current && !supportModeTeardownPending) {
      sessionRef.current = state.session;
    }
  }, [state.session, supportModeTeardownPending]);

  useEffect(() => {
    if (getSupportModePhase() !== 'bootstrap') return;
    setSupportModeTeardownPending(true);
    void finishSupportMode().catch(() => undefined);
  }, [finishSupportMode]);

  const refreshCards = useCallback(async () => {
    if (!state.isAuthenticated || !state.activeWorkspace) return;
    const cards = await fetchMatters(state.filters);
    dispatch({ type: 'SET_CARDS', payload: cards });
  }, [state.activeWorkspace, state.filters, state.isAuthenticated]);

  const loadCard = useCallback(async (cardId: string): Promise<MatterCard | null> => {
    if (!state.isAuthenticated || !state.activeWorkspace) return null;
    const card = await fetchMatterDetail(cardId);
    dispatch({ type: 'UPSERT_CARD', payload: card });
    return card;
  }, [state.isAuthenticated, state.activeWorkspace]);

  const refreshCard = useCallback(async (cardId: string) => {
    await loadCard(cardId);
  }, [loadCard]);

  const refreshMetrics = useCallback(async () => {
    if (!state.isAuthenticated || !state.activeWorkspace) return;
    const summary = await fetchReportsSummary();
    dispatch({ type: 'UPDATE_METRICS', payload: summary.kpis });
  }, [state.isAuthenticated, state.activeWorkspace]);

  const refreshApprovals = useCallback(async () => {
    if (!state.isAuthenticated || !state.activeWorkspace) return;
    const approvals = await fetchApprovalItems();
    dispatch({ type: 'SET_APPROVALS', payload: approvals });
  }, [state.isAuthenticated, state.activeWorkspace]);

  const refreshConversations = useCallback(async () => {
    if (!state.isAuthenticated || !state.activeWorkspace) return;
    const conversations = await fetchWhatsAppContacts();
    dispatch({ type: 'SET_CONVERSATIONS', payload: conversations });
  }, [state.activeWorkspace, state.isAuthenticated]);

  const refreshWhatsAppConnections = useCallback(async () => {
    if (!state.isAuthenticated || !state.activeWorkspace) return [];
    return fetchWhatsAppConnections();
  }, [state.activeWorkspace, state.isAuthenticated]);

  const refreshConversation = useCallback(async (conversationId: string) => {
    if (!state.isAuthenticated || !state.activeWorkspace) return;
    const conversation = await fetchWhatsAppConversation(conversationId);
    dispatch({ type: 'UPSERT_CONVERSATION', payload: conversation });
  }, [state.activeWorkspace, state.isAuthenticated]);

  const selectCard = useCallback((id: string | null) => {
    dispatch({ type: 'SELECT_CARD', payload: id });
  }, []);

  const closeDrawer = useCallback(() => {
    dispatch({ type: 'TOGGLE_DRAWER', payload: false });
  }, []);

  const updateFilters = useCallback((filters: Partial<Filters>) => {
    dispatch({ type: 'UPDATE_FILTERS', payload: filters });
  }, []);

  const resolveApproval = useCallback(async (
    id: string,
    status: 'approved' | 'rejected' | 'changes_requested',
    resolutionReason?: string,
  ) => {
    await resolveApprovalItem(id, status, resolutionReason);
    dispatch({ type: 'RESOLVE_APPROVAL', payload: { id, status } });
    await refreshApprovals();
    await refreshCards();
  }, [refreshApprovals, refreshCards]);

  const getSelectedCard = useCallback(() => {
    return state.cards.find(c => c.id === state.selectedCardId);
  }, [state.cards, state.selectedCardId]);

  const getFilteredCards = useCallback(() => {
    return filterMatterCards(state.cards, state.filters);
  }, [state.cards, state.filters]);

  const getPendingApprovals = useCallback(() => {
    return state.approvalItems.filter(a => a.status === 'pending');
  }, [state.approvalItems]);

  const getUnreadNotifications = useCallback(() => {
    return state.notifications.filter(n => !n.read).length;
  }, [state.notifications]);

  const setActiveModule = useCallback((module: ModuleId) => {
    dispatch({ type: 'SET_ACTIVE_MODULE', payload: module });
  }, []);

  const navigateWithFilter = useCallback((filters: Partial<Filters>, module: ModuleId = 'lista') => {
    dispatch({ type: 'RESET_FILTERS' });
    dispatch({ type: 'UPDATE_FILTERS', payload: filters });
    dispatch({ type: 'SET_ACTIVE_MODULE', payload: module });
  }, []);

  const moveCard = useCallback((cardId: string, newColumnId: KanbanColumnId) => {
    dispatch({ type: 'MOVE_CARD', payload: { cardId, newColumnId } });
  }, []);

  // V9-b: os dois callbacks abaixo eram só `dispatch` e a mudança sumia no refetch seguinte.
  // Otimista → API → resposta do servidor. Duas operações concorrentes no mesmo card deixam a
  // última resposta do servidor vencer (UPSERT_CARD substitui o card inteiro).
  const updateCardField = useCallback(async (cardId: string, field: EditableMatterField, value: unknown) => {
    const apiField = MATTER_FIELD_TO_API[field];
    if (!apiField) throw new Error(`O campo ${String(field)} não é editável por aqui.`);
    const previous = cardsRef.current.find((c) => c.id === cardId);
    dispatch({ type: 'UPDATE_CARD_FIELD', payload: { cardId, field, value } });
    try {
      const updated = await updateMatterDetail(cardId, { [apiField]: value });
      dispatch({ type: 'UPSERT_CARD', payload: updated });
    } catch (error) {
      // Restaura pelo servidor; se a releitura também falhar, volta ao snapshot local.
      const restored = await loadCard(cardId).catch(() => null);
      if (!restored && previous) dispatch({ type: 'UPSERT_CARD', payload: previous });
      throw error;
    }
  }, [loadCard]);

  const toggleTask = useCallback(async (cardId: string, taskId: string) => {
    const findTask = (cards: MatterCard[]) => cards.find((c) => c.id === cardId)?.tasks.find((t) => t.id === taskId);
    let snapshot = cardsRef.current.find((c) => c.id === cardId);
    let task = findTask(cardsRef.current);
    if (!task || typeof task.version !== 'number') {
      // A lista do Kanban vem sem tarefas (e sem versão): recarrega o detalhe antes de agir.
      const loaded = await loadCard(cardId);
      snapshot = loaded ?? snapshot;
      task = loaded ? findTask([loaded]) : undefined;
    }
    if (!task) throw new Error('A tarefa não foi encontrada neste caso.');
    if (typeof task.version !== 'number') {
      throw new Error('A tarefa veio sem versão; recarregue o caso e tente de novo.');
    }
    if (task.status === 'done') {
      // O backend não reabre tarefa concluída pelo toggle; não fingir na tela.
      throw new Error('Tarefa concluída não reabre por aqui; use a página de tarefas.');
    }
    const expectedVersion = task.version;
    dispatch({ type: 'TOGGLE_TASK_STATUS', payload: { cardId, taskId } });
    try {
      const updated = await toggleMatterTask(taskId, expectedVersion);
      dispatch({
        type: 'UPSERT_CARD_TASK',
        payload: {
          cardId,
          task: {
            id: updated.id,
            status: updated.status,
            ...(typeof updated.version === 'number' ? { version: updated.version } : {}),
          },
        },
      });
    } catch (error) {
      // Restaura pelo servidor; se a releitura também falhar, volta ao snapshot local.
      const restored = await loadCard(cardId).catch(() => null);
      if (!restored && snapshot) dispatch({ type: 'UPSERT_CARD', payload: snapshot });
      throw error;
    }
  }, [loadCard]);

  const addNote = useCallback(async (cardId: string, note: string) => {
    await addMatterNote(cardId, note);
    dispatch({ type: 'ADD_CARD_NOTE', payload: { cardId, note } });
    void refreshCard(cardId).catch(() => undefined);
  }, [refreshCard]);

  const overrideRun = useCallback(async (
    matterId: string,
    runId: string,
    decision: 'accepted' | 'corrected' | 'rejected',
    notes?: string,
    correctedPayload?: Record<string, unknown>,
  ) => {
    await overrideAgentRun(matterId, runId, { decision, notes, correctedPayload });
    await refreshCard(matterId);
    await refreshCards();
  }, [refreshCard, refreshCards]);

  const retryRun = useCallback(async (matterId: string, runId: string) => {
    await retryAgentRun(matterId, runId);
    await refreshCard(matterId);
    await refreshCards();
  }, [refreshCard, refreshCards]);

  const login = useCallback((user: AuthUser, session: SessionContext) => {
    dispatch({ type: 'LOGIN', payload: { user, session } });
  }, []);

  const selectWorkspace = useCallback((session: SessionContext) => {
    void queryClient.cancelQueries();
    queryClient.clear();
    dispatch({ type: 'PURGE_PROTECTED_DATA' });
    dispatch({ type: 'SET_SESSION', payload: session });
  }, [queryClient]);

  const logout = useCallback(async () => {
    clearSupportModeCredential('manual');
    if (getSupportModePhase() === 'ending' || getSupportModePhase() === 'bootstrap') {
      await finishSupportMode();
    }
    abortAllSupportModeRequests();
    supportModeRef.current = null;
    supportReturnSessionRef.current = null;
    setSupportMode(null);
    setSupportModeTeardownPending(false);
    await removeToken();
    queryClient.clear();
    dispatch({ type: 'LOGOUT' });
  }, [finishSupportMode, queryClient]);

  const sendMessage = useCallback(async (conversationId: string, text: string, connectionId?: string | null) => {
    const pendingId = `pending-${Date.now()}`;
    const optimisticMessage: WhatsAppMessage = {
      id: pendingId,
      conversationId,
      senderId: state.currentUser?.id ?? 'current-user',
      text,
      timestamp: new Date().toISOString(),
      status: 'pending',
      isFromClient: false,
    };
    dispatch({ type: 'SEND_MESSAGE', payload: { conversationId, message: optimisticMessage } });
    try {
      await sendWhatsAppMessage(conversationId, text, connectionId);
      await Promise.all([
        refreshConversation(conversationId),
        refreshConversations(),
        refreshCards(),
      ]);
    } catch (error) {
      dispatch({
        type: 'UPDATE_MESSAGE_STATUS',
        payload: { conversationId, messageId: pendingId, status: 'failed' },
      });
      throw error;
    }
  }, [refreshCards, refreshConversation, refreshConversations, state.currentUser?.id]);

  const toggleAiMode = useCallback(async (conversationId: string, mode: AiMode) => {
    const payload = mode === 'active'
      ? { queueStatus: 'ai', humanControlState: 'copilot' }
      : mode === 'paused'
        ? { queueStatus: 'human', humanControlState: 'manual_pause' }
        : { queueStatus: 'human', humanControlState: 'manual_off' };
    const conversation = await handoffConversation(conversationId, payload);
    dispatch({ type: 'UPSERT_CONVERSATION', payload: conversation });
    await Promise.all([
      refreshConversation(conversationId),
      refreshConversations(),
      refreshCards(),
    ]);
  }, [refreshCards, refreshConversation, refreshConversations]);

  const markConversationRead = useCallback(async (conversationId: string) => {
    dispatch({ type: 'MARK_CONVERSATION_READ', payload: conversationId });
    await markWhatsAppConversationRead(conversationId);
    await Promise.all([
      refreshConversation(conversationId),
      refreshConversations(),
      refreshCards(),
    ]);
  }, [refreshCards, refreshConversation, refreshConversations]);

  const getTotalUnreadMessages = useCallback(() => {
    return state.conversations.reduce((sum, c) => sum + c.unreadCount, 0);
  }, [state.conversations]);

  const getCapability = useCallback((key: CommercialEntitlementKey) => (
    capabilityManifestStatus === 'ready'
      ? getWorkspaceCapability(capabilityManifest, key)
      : null
  ), [capabilityManifest, capabilityManifestStatus]);

  const isCapabilityVisible = useCallback((key: CommercialEntitlementKey) => (
    isWorkspaceCapabilityVisible(getCapability(key))
  ), [getCapability]);

  const canUseCapability = useCallback((key: CommercialEntitlementKey) => (
    isWorkspaceCapabilityUsable(getCapability(key))
  ), [getCapability]);

  const can = useCallback((module: Permission['module'], action: Permission['action'] = 'view') => {
    return state.session?.permissions.some(permission => permission.module === module && permission.action === action)
      ?? false;
  }, [state.session]);

  const canAccessModule = useCallback((module: ModuleId) => {
    return can(MODULE_PERMISSION_MAP[module], 'view');
  }, [can]);

  const selectedCardId = state.selectedCardId;
  const permissionSignature = (state.session?.permissions ?? [])
    .map((permission) => `${permission.module}:${permission.action}:${permission.scope}`)
    .sort()
    .join('|');

  const realtimeHandlersRef = useRef({
    refreshApprovals,
    refreshCard,
    refreshCards,
    refreshConversations,
    refreshMetrics,
    selectedCardId,
  });
  realtimeHandlersRef.current = {
    refreshApprovals,
    refreshCard,
    refreshCards,
    refreshConversations,
    refreshMetrics,
    selectedCardId,
  };
  const passwordChangePending = requiresPasswordChange(state.currentUser, state.session);

  const refreshSessionFromServer = useCallback(async () => {
    if (supportModeRef.current || supportModeTeardownPending) return sessionRef.current;
    if (passwordChangePending) return sessionRef.current;
    try {
      const session = await fetchCurrentSession();
      const previousSignature = (sessionRef.current?.permissions ?? [])
        .map((permission) => `${permission.module}:${permission.action}:${permission.scope}`)
        .sort()
        .join('|');
      const nextSignature = session.permissions
        .map((permission) => `${permission.module}:${permission.action}:${permission.scope}`)
        .sort()
        .join('|');
      if (previousSignature !== nextSignature) {
        await queryClient.cancelQueries();
        queryClient.removeQueries({ type: 'inactive' });
        dispatch({ type: 'PURGE_PROTECTED_DATA' });
        // Install the reduced authority before any protected query is
        // restarted. Waiting for every active query here left the SPA in a
        // blank transitional state and kept the stale permission manifest in
        // memory while the refetch fan-out was still running.
        saveSession(session);
        sessionRef.current = session;
        dispatch({ type: 'SET_SESSION', payload: session });
        await queryClient.resetQueries({ type: 'active' });
        return session;
      }
      saveSession(session);
      sessionRef.current = session;
      dispatch({ type: 'SET_SESSION', payload: session });
      return session;
    } catch (error) {
      if (error instanceof SessionAccessError && [401, 403].includes(error.status)) {
        expireClientSession();
      }
      throw error;
    }
  }, [passwordChangePending, queryClient, supportModeTeardownPending]);

  useEffect(() => {
    if (!state.isAuthenticated || supportMode || supportModeTeardownPending) return;
    const handleFocus = () => { void refreshSessionFromServer().catch(() => undefined); };
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        void refreshSessionFromServer().catch(() => undefined);
      }
    };
    void refreshSessionFromServer().catch(() => undefined);
    window.addEventListener('focus', handleFocus);
    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      window.removeEventListener('focus', handleFocus);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [refreshSessionFromServer, state.isAuthenticated, supportMode, supportModeTeardownPending]);

  useEffect(() => {
    if (!state.isAuthenticated || supportMode || supportModeTeardownPending) return;
    let cancelled = false;
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;

    const scheduleRefresh = () => {
      const expiresAt = getAccessTokenExpiresAt();
      const delay = expiresAt === null
        ? ACCESS_TOKEN_REFRESH_FALLBACK_MS
        : Math.max(1_000, expiresAt - Date.now() - ACCESS_TOKEN_REFRESH_LEAD_MS);
      refreshTimer = setTimeout(() => {
        void refreshAccessToken()
          .then(async (token) => {
            if (cancelled) return;
            if (!token) {
              expireClientSession();
              return;
            }
            await refreshSessionFromServer();
            if (!cancelled) scheduleRefresh();
          })
          .catch(() => {
            if (!cancelled) expireClientSession();
          });
      }, delay);
    };

    scheduleRefresh();
    return () => {
      cancelled = true;
      if (refreshTimer) clearTimeout(refreshTimer);
    };
  }, [refreshSessionFromServer, state.isAuthenticated, supportMode, supportModeTeardownPending]);

  useEffect(() => {
    if (supportMode || supportModeTeardownPending || !state.isAuthenticated || !activeWorkspaceId) {
      return;
    }

    void refreshCards().catch(() => undefined);
  }, [activeWorkspaceId, permissionSignature, refreshCards, state.filters, state.isAuthenticated, supportMode, supportModeTeardownPending]);

  useEffect(() => {
    if (supportMode || supportModeTeardownPending || !state.isAuthenticated || !activeWorkspaceId) {
      return;
    }

    void refreshMetrics().catch(() => undefined);
  }, [activeWorkspaceId, permissionSignature, refreshMetrics, state.isAuthenticated, supportMode, supportModeTeardownPending]);

  useEffect(() => {
    if (supportMode || supportModeTeardownPending || !state.isAuthenticated || !activeWorkspaceId) {
      return;
    }

    void refreshApprovals().catch(() => undefined);
  }, [activeWorkspaceId, permissionSignature, refreshApprovals, state.isAuthenticated, supportMode, supportModeTeardownPending]);

  useEffect(() => {
    if (supportMode || supportModeTeardownPending || !state.isAuthenticated || !activeWorkspaceId) {
      return;
    }

    void refreshConversations().catch(() => undefined);
  }, [activeWorkspaceId, permissionSignature, refreshConversations, state.isAuthenticated, supportMode, supportModeTeardownPending]);

  useEffect(() => {
    if (supportMode || supportModeTeardownPending || !selectedCardId || !state.isAuthenticated || !activeWorkspaceId) {
      return;
    }

    void refreshCard(selectedCardId).catch(() => undefined);
  }, [activeWorkspaceId, permissionSignature, refreshCard, selectedCardId, state.isAuthenticated, supportMode, supportModeTeardownPending]);

  useEffect(() => {
    if (supportMode || supportModeTeardownPending || !state.isAuthenticated || !activeWorkspaceId) {
      return;
    }

    let disposed = false;
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
    let stableConnectionTimer: ReturnType<typeof setTimeout> | null = null;
    let connectionTimeoutTimer: ReturnType<typeof setTimeout> | null = null;
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    const pendingMatterRefreshes = new Set<string>();
    let retryAttempt = 0;
    let connecting = false;
    let hasConnected = false;
    const apiBase = resolveWebSocketBase(window.location.origin);
    const publishStatus = (status: 'connecting' | 'connected' | 'reconnecting' | 'offline') => {
      document.documentElement.dataset.klmRealtimeStatus = status;
      window.dispatchEvent(new CustomEvent('klm:realtime-status', { detail: { status } }));
    };

    const clearRetryTimer = () => {
      if (retryTimer === null) return;
      clearTimeout(retryTimer);
      retryTimer = null;
    };
    const clearHeartbeatTimer = () => {
      if (heartbeatTimer === null) return;
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    };
    const clearStableConnectionTimer = () => {
      if (stableConnectionTimer === null) return;
      clearTimeout(stableConnectionTimer);
      stableConnectionTimer = null;
    };
    const clearConnectionTimeoutTimer = () => {
      if (connectionTimeoutTimer === null) return;
      clearTimeout(connectionTimeoutTimer);
      connectionTimeoutTimer = null;
    };
    const clearRefreshTimer = () => {
      if (refreshTimer === null) return;
      clearTimeout(refreshTimer);
      refreshTimer = null;
    };
    const flushRealtimeRefreshes = () => {
      refreshTimer = null;
      const handlers = realtimeHandlersRef.current;
      const matterIds = [...pendingMatterRefreshes];
      pendingMatterRefreshes.clear();
      void handlers.refreshCards().catch(() => undefined);
      void handlers.refreshMetrics().catch(() => undefined);
      void handlers.refreshApprovals().catch(() => undefined);
      void handlers.refreshConversations().catch(() => undefined);
      matterIds.forEach((matterId) => {
        void handlers.refreshCard(matterId).catch(() => undefined);
      });
    };
    const scheduleRealtimeRefresh = (matterId: unknown) => {
      const handlers = realtimeHandlersRef.current;
      if (typeof matterId === 'string' && handlers.selectedCardId === matterId) {
        pendingMatterRefreshes.add(matterId);
      }
      clearRefreshTimer();
      refreshTimer = setTimeout(flushRealtimeRefreshes, REALTIME_REFRESH_DEBOUNCE_MS);
    };

    const scheduleReconnect = () => {
      if (disposed || retryTimer !== null || ws !== null) return;
      const delay = calculateRealtimeRetryDelay(retryAttempt);
      retryAttempt += 1;
      retryTimer = setTimeout(() => {
        retryTimer = null;
        void connect();
      }, delay);
    };

    const abandonSocket = (socket: WebSocket) => {
      if (ws !== socket) return;
      ws = null;
      clearHeartbeatTimer();
      clearStableConnectionTimer();
      clearConnectionTimeoutTimer();
      if (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN) {
        socket.close();
      }
      if (!disposed) {
        publishStatus('reconnecting');
        void refreshSessionFromServer().catch(() => undefined);
        scheduleReconnect();
      }
    };

    const connect = async () => {
      if (disposed || connecting || ws !== null) return;
      connecting = true;
      publishStatus(hasConnected || retryAttempt > 0 ? 'reconnecting' : 'connecting');
      try {
        const { ticket } = await fetchWebSocketTicket();
        if (disposed || ws !== null) return;
        const socket = new WebSocket(
          `${apiBase}/api/v1/ws`,
          ['klm.realtime.v1', `ticket.${ticket}`],
        );
        ws = socket;
        connectionTimeoutTimer = setTimeout(() => {
          connectionTimeoutTimer = null;
          if (!disposed && ws === socket && socket.readyState !== WebSocket.OPEN) {
            abandonSocket(socket);
          }
        }, REALTIME_CONNECT_TIMEOUT_MS);
        socket.onopen = () => {
          if (disposed || ws !== socket) {
            socket.close();
            return;
          }
          hasConnected = true;
          clearRetryTimer();
          clearHeartbeatTimer();
          clearStableConnectionTimer();
          clearConnectionTimeoutTimer();
          publishStatus('connected');
          stableConnectionTimer = setTimeout(() => {
            stableConnectionTimer = null;
            if (!disposed && ws === socket && socket.readyState === WebSocket.OPEN) {
              retryAttempt = 0;
            }
          }, REALTIME_STABLE_CONNECTION_MS);
          heartbeatTimer = setInterval(() => {
            if (socket.readyState === WebSocket.OPEN) socket.send('ping');
          }, REALTIME_HEARTBEAT_MS);
        };
        socket.onmessage = (event) => {
          if (disposed || ws !== socket) return;
          try {
            const payload = JSON.parse(event.data);
            const matterId = payload?.data?.matter_id;
            const eventName = payload?.event;

            if (eventName === 'CONNECTED' || eventName === 'PONG') {
              publishStatus('connected');
            }

            window.dispatchEvent(new CustomEvent('klm:realtime', { detail: payload }));

            if (
              eventName?.startsWith('CARD_')
              || eventName?.startsWith('AGENT_RUN_')
              || eventName?.startsWith('APPROVAL_')
              || eventName?.startsWith('OMNICHANNEL_')
            ) {
              scheduleRealtimeRefresh(matterId);
            }
          } catch {
            // ignora eventos inválidos
          }
        };
        socket.onerror = () => {
          abandonSocket(socket);
        };
        socket.onclose = () => {
          if (ws !== socket) return;
          ws = null;
          clearHeartbeatTimer();
          clearStableConnectionTimer();
          clearConnectionTimeoutTimer();
          if (!disposed) {
            publishStatus('reconnecting');
            void refreshSessionFromServer().catch(() => undefined);
            scheduleReconnect();
          }
        };
      } catch {
        if (!disposed) {
          publishStatus('reconnecting');
          scheduleReconnect();
        }
      } finally {
        connecting = false;
      }
    };
    void connect();

    return () => {
      disposed = true;
      clearRetryTimer();
      clearHeartbeatTimer();
      clearStableConnectionTimer();
      clearConnectionTimeoutTimer();
      clearRefreshTimer();
      pendingMatterRefreshes.clear();
      const socket = ws;
      ws = null;
      if (socket) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onclose = null;
        socket.close();
      }
      publishStatus('offline');
    };
  }, [activeWorkspaceId, permissionSignature, refreshSessionFromServer, state.isAuthenticated, supportMode, supportModeTeardownPending]);

  const contextValue = useMemo<AppContextValue>(() => ({
    state,
    capabilityManifest,
    capabilityManifestStatus,
    supportMode,
    supportModeTeardownPending,
    dispatch,
    selectCard,
    closeDrawer,
    updateFilters,
    resolveApproval,
    getSelectedCard,
    getFilteredCards,
    getPendingApprovals,
    getUnreadNotifications,
    setActiveModule,
    navigateWithFilter,
    moveCard,
    updateCardField,
    toggleTask,
    addNote,
    refreshCards,
    refreshCard,
    refreshConversations,
    refreshConversation,
    refreshWhatsAppConnections,
    overrideRun,
    retryRun,
    login,
    selectWorkspace,
    enterSupportMode,
    exitSupportMode,
    logout,
    sendMessage,
    toggleAiMode,
    markConversationRead,
    getTotalUnreadMessages,
    getCapability,
    isCapabilityVisible,
    canUseCapability,
    canAccessModule,
    can,
  }), [
    addNote,
    can,
    canUseCapability,
    canAccessModule,
    capabilityManifest,
    capabilityManifestStatus,
    closeDrawer,
    enterSupportMode,
    exitSupportMode,
    getFilteredCards,
    getCapability,
    getPendingApprovals,
    getSelectedCard,
    getTotalUnreadMessages,
    getUnreadNotifications,
    isCapabilityVisible,
    login,
    logout,
    markConversationRead,
    moveCard,
    navigateWithFilter,
    overrideRun,
    refreshCard,
    refreshCards,
    refreshConversation,
    refreshConversations,
    refreshWhatsAppConnections,
    resolveApproval,
    retryRun,
    selectCard,
    selectWorkspace,
    sendMessage,
    setActiveModule,
    state,
    supportMode,
    supportModeTeardownPending,
    toggleAiMode,
    toggleTask,
    updateCardField,
    updateFilters,
  ]);

  return (
    <AppContext.Provider value={contextValue}>
      {children}
    </AppContext.Provider>
  );
}

export function useAppStore() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useAppStore must be used within AppProvider');
  return ctx;
}

export function useOptionalAppStore() {
  return useContext(AppContext);
}
