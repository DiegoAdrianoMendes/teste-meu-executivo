import {
  klmApi,
  type KlmSupportMessage,
  type KlmSupportTicket,
  type KlmSupportTicketCreate,
} from '@/klm/api';

const DATABASE_NAME = 'klm-support-center';
const DATABASE_VERSION = 2;
const STORE_NAME = 'offline-operations';

export type SupportQueueScope = {
  workspaceId: string;
  userId: string;
};

type QueuedAttachment = {
  id: string;
  blob: Blob;
  name: string;
  type: string;
  lastModified: number;
};

type QueuedTicketOperation = {
  id: string;
  kind: 'ticket';
  scopeKey: string;
  idempotencyKey: string;
  payload: KlmSupportTicketCreate;
  attachments: QueuedAttachment[];
  serverTicketId?: string;
  createdAt: string;
  lastAttemptAt?: string;
};

type QueuedMessageOperation = {
  id: string;
  kind: 'message';
  scopeKey: string;
  idempotencyKey: string;
  ticketId: string;
  body: string;
  attachments: QueuedAttachment[];
  serverMessageId?: string;
  createdAt: string;
  lastAttemptAt?: string;
};

type QueuedSupportOperation = QueuedTicketOperation | QueuedMessageOperation;

export type SupportSubmissionResult<T> = {
  state: 'confirmed' | 'queued';
  record?: T;
  pendingAttachments: number;
  failedAttachments: number;
};

export type SupportQueueFlushResult = {
  confirmedTicketIds: string[];
  confirmedMessageIds: string[];
  remaining: number;
};

const activeFlushes = new Map<string, Promise<SupportQueueFlushResult>>();

function supportScopeKey(scope: SupportQueueScope): string {
  const workspaceId = scope.workspaceId.trim();
  const userId = scope.userId.trim();
  if (!workspaceId || !userId) {
    throw new Error('A fila offline exige conta e usuário autenticados.');
  }
  return `${workspaceId}:${userId}`;
}

function createOperationId(prefix: string): string {
  return `${prefix}:${crypto.randomUUID()}`;
}

function toQueuedAttachments(files: File[]): QueuedAttachment[] {
  return files.map((file) => ({
    id: crypto.randomUUID(),
    blob: file,
    name: file.name,
    type: file.type || 'application/octet-stream',
    lastModified: file.lastModified,
  }));
}

function toFile(attachment: QueuedAttachment): File {
  return new File([attachment.blob], attachment.name, {
    type: attachment.type,
    lastModified: attachment.lastModified,
  });
}

function isTransientNetworkError(error: unknown): boolean {
  if (typeof navigator !== 'undefined' && !navigator.onLine) return true;
  if (error instanceof TypeError) return true;
  if (error instanceof DOMException && ['NetworkError', 'TimeoutError'].includes(error.name)) return true;
  return error instanceof Error && error.name === 'ApiRequestTimeoutError';
}

function openDatabase(): Promise<IDBDatabase> {
  if (!('indexedDB' in globalThis)) {
    return Promise.reject(new Error('A fila offline não está disponível neste navegador.'));
  }
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE_NAME)) {
        database.createObjectStore(STORE_NAME, { keyPath: 'id' });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error('Não foi possível abrir a fila offline.'));
  });
}

async function withStore<T>(
  mode: IDBTransactionMode,
  operation: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, mode);
    const request = operation(transaction.objectStore(STORE_NAME));
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error('Não foi possível atualizar a fila offline.'));
    transaction.oncomplete = () => database.close();
    transaction.onerror = () => {
      database.close();
      reject(new Error('Não foi possível concluir a operação offline.'));
    };
  });
}

function putOperation(operation: QueuedSupportOperation): Promise<IDBValidKey> {
  return withStore('readwrite', (store) => store.put(operation));
}

function deleteOperation(operationId: string): Promise<undefined> {
  return withStore('readwrite', (store) => store.delete(operationId));
}

export async function listQueuedSupportOperations(scope: SupportQueueScope): Promise<QueuedSupportOperation[]> {
  const scopeKey = supportScopeKey(scope);
  const rows = await withStore<QueuedSupportOperation[]>('readonly', (store) => store.getAll());
  return rows
    .filter((operation) => operation.scopeKey === scopeKey)
    .sort((left, right) => left.createdAt.localeCompare(right.createdAt));
}

export async function countQueuedSupportOperations(scope: SupportQueueScope): Promise<number> {
  return (await listQueuedSupportOperations(scope)).length;
}

async function uploadAttachments(
  operation: QueuedSupportOperation,
  ticketId: string,
  messageId?: string,
  persistProgress = true,
): Promise<void> {
  while (operation.attachments.length) {
    const [attachment] = operation.attachments;
    await klmApi.uploadSupportAttachment(
      ticketId,
      toFile(attachment),
      messageId,
      `support-attachment:${attachment.id}`,
    );
    operation.attachments = operation.attachments.filter((item) => item.id !== attachment.id);
    if (persistProgress) await putOperation(operation);
  }
}

async function queueTicketOperation(
  scope: SupportQueueScope,
  payload: KlmSupportTicketCreate,
  files: File[],
  idempotencyKey: string,
  serverTicketId?: string,
): Promise<void> {
  await putOperation({
    id: createOperationId('support-ticket'),
    kind: 'ticket',
    scopeKey: supportScopeKey(scope),
    idempotencyKey,
    payload,
    attachments: toQueuedAttachments(files),
    serverTicketId,
    createdAt: new Date().toISOString(),
  });
}

async function queueMessageOperation(
  scope: SupportQueueScope,
  ticketId: string,
  body: string,
  files: File[],
  idempotencyKey: string,
  serverMessageId?: string,
): Promise<void> {
  await putOperation({
    id: createOperationId('support-message'),
    kind: 'message',
    scopeKey: supportScopeKey(scope),
    idempotencyKey,
    ticketId,
    body,
    attachments: toQueuedAttachments(files),
    serverMessageId,
    createdAt: new Date().toISOString(),
  });
}

export async function submitSupportTicket(
  scope: SupportQueueScope,
  payload: KlmSupportTicketCreate,
  files: File[],
): Promise<SupportSubmissionResult<KlmSupportTicket>> {
  const idempotencyKey = createOperationId('support-ticket-create');
  if (!navigator.onLine) {
    await queueTicketOperation(scope, payload, files, idempotencyKey);
    return { state: 'queued', pendingAttachments: files.length, failedAttachments: 0 };
  }

  let ticket: KlmSupportTicket;
  try {
    ticket = await klmApi.createSupportTicket(payload, idempotencyKey);
  } catch (error) {
    if (!isTransientNetworkError(error)) throw error;
    await queueTicketOperation(scope, payload, files, idempotencyKey);
    return { state: 'queued', pendingAttachments: files.length, failedAttachments: 0 };
  }

  const operation: QueuedTicketOperation = {
    id: createOperationId('support-ticket'),
    kind: 'ticket',
    scopeKey: supportScopeKey(scope),
    idempotencyKey,
    payload,
    attachments: toQueuedAttachments(files),
    serverTicketId: ticket.id,
    createdAt: new Date().toISOString(),
  };
  if (operation.attachments.length) {
    try {
      await uploadAttachments(operation, ticket.id, undefined, false);
    } catch (error) {
      const remaining = operation.attachments.length;
      if (isTransientNetworkError(error)) {
        try {
          await putOperation(operation);
          return {
            state: 'confirmed',
            record: ticket,
            pendingAttachments: remaining,
            failedAttachments: 0,
          };
        } catch {
          return {
            state: 'confirmed',
            record: ticket,
            pendingAttachments: 0,
            failedAttachments: remaining,
          };
        }
      }
      return {
        state: 'confirmed',
        record: ticket,
        pendingAttachments: 0,
        failedAttachments: remaining,
      };
    }
  }
  return { state: 'confirmed', record: ticket, pendingAttachments: 0, failedAttachments: 0 };
}

export async function submitSupportMessage(
  scope: SupportQueueScope,
  ticketId: string,
  body: string,
  files: File[],
): Promise<SupportSubmissionResult<KlmSupportMessage>> {
  const idempotencyKey = createOperationId('support-message-create');
  if (!navigator.onLine) {
    await queueMessageOperation(scope, ticketId, body, files, idempotencyKey);
    return { state: 'queued', pendingAttachments: files.length, failedAttachments: 0 };
  }

  let message: KlmSupportMessage;
  try {
    message = await klmApi.addSupportMessage(ticketId, body, idempotencyKey);
  } catch (error) {
    if (!isTransientNetworkError(error)) throw error;
    await queueMessageOperation(scope, ticketId, body, files, idempotencyKey);
    return { state: 'queued', pendingAttachments: files.length, failedAttachments: 0 };
  }

  const operation: QueuedMessageOperation = {
    id: createOperationId('support-message'),
    kind: 'message',
    scopeKey: supportScopeKey(scope),
    idempotencyKey,
    ticketId,
    body,
    attachments: toQueuedAttachments(files),
    serverMessageId: message.id,
    createdAt: new Date().toISOString(),
  };
  if (operation.attachments.length) {
    try {
      await uploadAttachments(operation, ticketId, message.id, false);
    } catch (error) {
      const remaining = operation.attachments.length;
      if (isTransientNetworkError(error)) {
        try {
          await putOperation(operation);
          return {
            state: 'confirmed',
            record: message,
            pendingAttachments: remaining,
            failedAttachments: 0,
          };
        } catch {
          return {
            state: 'confirmed',
            record: message,
            pendingAttachments: 0,
            failedAttachments: remaining,
          };
        }
      }
      return {
        state: 'confirmed',
        record: message,
        pendingAttachments: 0,
        failedAttachments: remaining,
      };
    }
  }
  return { state: 'confirmed', record: message, pendingAttachments: 0, failedAttachments: 0 };
}

async function flushQueue(scope: SupportQueueScope): Promise<SupportQueueFlushResult> {
  if (!navigator.onLine) {
    return {
      confirmedTicketIds: [],
      confirmedMessageIds: [],
      remaining: await countQueuedSupportOperations(scope),
    };
  }
  const confirmedTicketIds: string[] = [];
  const confirmedMessageIds: string[] = [];
  const operations = await listQueuedSupportOperations(scope);

  for (const operation of operations) {
    try {
      operation.lastAttemptAt = new Date().toISOString();
      await putOperation(operation);
      if (operation.kind === 'ticket') {
        if (!operation.serverTicketId) {
          const ticket = await klmApi.createSupportTicket(operation.payload, operation.idempotencyKey);
          operation.serverTicketId = ticket.id;
          confirmedTicketIds.push(ticket.id);
          await putOperation(operation);
        }
        await uploadAttachments(operation, operation.serverTicketId);
      } else {
        if (!operation.serverMessageId) {
          const message = await klmApi.addSupportMessage(
            operation.ticketId,
            operation.body,
            operation.idempotencyKey,
          );
          operation.serverMessageId = message.id;
          confirmedMessageIds.push(message.id);
          await putOperation(operation);
        }
        await uploadAttachments(operation, operation.ticketId, operation.serverMessageId);
      }
      if (
        operation.kind === 'ticket'
        && !confirmedTicketIds.includes(operation.serverTicketId)
      ) {
        confirmedTicketIds.push(operation.serverTicketId);
      }
      if (
        operation.kind === 'message'
        && !confirmedMessageIds.includes(operation.serverMessageId)
      ) {
        confirmedMessageIds.push(operation.serverMessageId);
      }
      await deleteOperation(operation.id);
    } catch (error) {
      if (!isTransientNetworkError(error)) {
        operation.lastAttemptAt = new Date().toISOString();
        await putOperation(operation);
      }
      break;
    }
  }

  return {
    confirmedTicketIds,
    confirmedMessageIds,
    remaining: await countQueuedSupportOperations(scope),
  };
}

export function flushQueuedSupportOperations(scope: SupportQueueScope): Promise<SupportQueueFlushResult> {
  const scopeKey = supportScopeKey(scope);
  const activeFlush = activeFlushes.get(scopeKey);
  if (activeFlush) return activeFlush;
  const nextFlush = flushQueue(scope).finally(() => {
    activeFlushes.delete(scopeKey);
  });
  activeFlushes.set(scopeKey, nextFlush);
  return nextFlush;
}
