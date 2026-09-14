/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { NativeCapabilityWorkspace } from '@/klm/components/NativeCapabilityWorkspace';
import NativeCapabilityPage from '@/klm/pages/NativeCapabilityPage';
import { API_BASE } from '@/services/api-origin';
import {
  buildNativeCapabilityPath,
  executeNativeCapabilityAction,
  fetchNativeCapability,
  NativeCapabilityError,
  humanizeNativeKey,
  normalizeNativeCapabilityDocument,
  type NativeActionSchema,
  type NativeCapabilityDocument,
  type NativeCapabilityTransport,
} from '@/klm/native-capabilities';

it('localizes legacy technical keys for the native interface', () => {
  expect(humanizeNativeKey('records')).toBe('Registros');
  expect(humanizeNativeKey('threadId')).toBe('ID da conversa');
  expect(humanizeNativeKey('workspace_id')).toBe('ID do ambiente');
  expect(humanizeNativeKey('notification_rule')).toBe('Regra de notificação');
  expect(humanizeNativeKey('Contacts')).toBe('Contatos');
  expect(humanizeNativeKey('Opportunities')).toBe('Oportunidades');
  expect(humanizeNativeKey('avatar_url')).toBe('Foto');
  expect(humanizeNativeKey('channel_type')).toBe('Tipo de canal');
  expect(humanizeNativeKey('greeting_enabled')).toBe('Saudação ativa');
  expect(humanizeNativeKey('remoteResources')).toBe('Recursos integrados');
});

it('preserves zero and boolean values when details declare fields separately', () => {
  const input = documentFixture();
  input.tabs = [{
    id: 'overview',
    label: 'Visão geral',
    sections: [{
      id: 'summary',
      type: 'details',
      title: 'Resumo',
      data: { nativeRecords: 0, tenantScoped: true },
      fields: [
        { key: 'nativeRecords', label: 'Registros locais' },
        { key: 'tenantScoped', label: 'Isolado por ambiente', format: 'boolean' },
      ],
    }],
  }];

  const normalized = normalizeNativeCapabilityDocument(input, 'crm', 'external-integrations');
  const section = normalized.tabs[0].sections[0];
  expect(section.type).toBe('details');
  if (section.type !== 'details') throw new Error('Expected details section.');
  expect(section.data).toEqual({ nativeRecords: 0, tenantScoped: true });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function auditEntry(id = 'audit-1') {
  return {
    id,
    title: 'Sincronização concluída',
    description: 'O bridge reconciliou os registros sem duplicação.',
    timestamp: '2026-07-18T12:30:00Z',
    actor: 'Hermes',
    status: 'success',
    metadata: { records: 2 },
  } as const;
}

function documentFixture(): NativeCapabilityDocument {
  return {
    schemaVersion: '1.0',
    domain: { id: 'communications', label: 'Comunicações' },
    capability: {
      id: 'labels-status-assignment',
      label: 'Roteamento de conversas',
      description: 'Operação nativa de status, prioridade, labels e responsáveis.',
      category: 'Atendimento',
    },
    health: {
      state: 'healthy',
      label: 'Íntegra',
      lastSyncAt: '2026-07-18T12:30:00Z',
      latencyMs: 42,
    },
    access: { state: 'allowed' },
    version: 'v7',
    updatedAt: '2026-07-18T12:31:00Z',
    kpis: [
      { id: 'open', label: 'Conversas abertas', value: 18, format: 'integer', delta: '+12%', trend: 'up' },
      { id: 'sla', label: 'Dentro do SLA', value: 94.5, format: 'percent', delta: '-1,2 p.p.', trend: 'down' },
      { id: 'value', label: 'Valor potencial', value: 185000, format: 'currency', trend: 'neutral' },
      { id: 'healthy', label: 'Bridge saudável', value: true, format: 'boolean', trend: 'neutral' },
    ],
    tabs: [
      {
        id: 'overview',
        label: 'Operação',
        sections: [
          {
            id: 'leads',
            type: 'table',
            title: 'Leads ativos',
            description: 'Registros pesquisáveis e vinculados ao contexto KLM.',
            columns: [
              { key: 'name', label: 'Nome' },
              { key: 'status', label: 'Status', format: 'status' },
              { key: 'amount', label: 'Potencial', format: 'currency' },
              { key: 'updatedAt', label: 'Atualizado', format: 'datetime', hiddenOnMobile: true },
            ],
            rows: [
              { id: 'lead-1', name: 'Ana Lima', status: 'open', amount: 12500, updatedAt: '2026-07-18T11:00:00Z' },
              { id: 'lead-2', name: 'Bruno Alves', status: 'pending', amount: 8700, updatedAt: '2026-07-18T10:00:00Z' },
            ],
            idField: 'id',
            searchable: true,
            searchKeys: ['name', 'status'],
            pageSize: 12,
            rowActionIds: ['update-lead'],
          },
          {
            id: 'details',
            type: 'details',
            title: 'Resumo operacional',
            actionIds: ['reset-routing'],
            data: { owner: 'Equipe Saúde', inbox: 'WhatsApp principal', active: true },
            fields: [
              { key: 'owner', label: 'Responsável' },
              { key: 'inbox', label: 'Caixa' },
              { key: 'active', label: 'Ativa', format: 'boolean' },
            ],
          },
          {
            id: 'queue',
            type: 'list',
            title: 'Fila de revisão',
            items: [
              { id: 'q1', title: 'Revisar documento', description: 'Negativa anexada ao caso.', status: 'pending', priority: 'high' },
              { id: 'q2', title: 'Confirmar medicamento', description: 'Validar nome comercial.', status: 'ready' },
            ],
            idField: 'id',
            titleField: 'title',
            descriptionField: 'description',
            statusField: 'status',
          },
          {
            id: 'distribution',
            type: 'chart',
            title: 'Distribuição por status',
            points: [
              { id: 'open', label: 'Abertas', value: 18, format: 'integer' },
              { id: 'pending', label: 'Pendentes', value: 7, format: 'integer', detail: 'Aguardando cliente ou jurídico.' },
            ],
          },
          {
            id: 'notice',
            type: 'notice',
            title: 'Canal externo protegido',
            tone: 'warning',
            body: 'Entregas externas exigem gate explícito.',
          },
          {
            id: 'raw',
            type: 'structured',
            title: 'Contexto adicional',
            data: { account: { id: 17, region: 'Brasil' }, tags: ['saúde', 'urgente'] },
          },
          {
            id: 'history',
            type: 'timeline',
            title: 'Histórico recente',
            items: [auditEntry('timeline-1')],
          },
        ],
      },
      {
        id: 'empty-tab',
        label: 'Sem registros',
        sections: [],
      },
    ],
    actions: [
      {
        id: 'configure-routing',
        label: 'Configurar roteamento',
        description: 'Atualiza parâmetros locais do bridge.',
        intent: 'primary',
        execution: 'local',
        supportsDryRun: true,
        permission: { allowed: true },
        fields: [
          { name: 'name', label: 'Nome da configuração', type: 'text', required: true, defaultValue: 'Saúde premium' },
          { name: 'email', label: 'E-mail de aviso', type: 'email', defaultValue: 'ops@klm.test' },
          { name: 'endpoint', label: 'URL de callback', type: 'url', defaultValue: 'https://example.test/callback' },
          { name: 'secret', label: 'Segredo local', type: 'password', defaultValue: 'segredo-seguro' },
          { name: 'notes', label: 'Observações', type: 'textarea', defaultValue: 'Aplicar somente no workspace atual.' },
          { name: 'limit', label: 'Limite', type: 'number', min: 1, max: 100, defaultValue: 20 },
          { name: 'retries', label: 'Tentativas', type: 'integer', min: 0, max: 5, defaultValue: 2 },
          { name: 'effectiveDate', label: 'Data de vigência', type: 'date', defaultValue: '2026-07-18' },
          { name: 'runAt', label: 'Executar em', type: 'datetime', defaultValue: '2026-07-18T12:30' },
          { name: 'team', label: 'Equipe', type: 'select', required: true, defaultValue: 'health', options: [{ value: 'health', label: 'Saúde' }, { value: 'legal', label: 'Jurídico' }] },
          { name: 'channels', label: 'Canais', type: 'multiselect', defaultValue: ['whatsapp'], options: [{ value: 'whatsapp', label: 'WhatsApp' }, { value: 'email', label: 'E-mail' }] },
          { name: 'notify', label: 'Notificar responsáveis', type: 'checkbox', defaultValue: true },
          { name: 'enabled', label: 'Configuração ativa', type: 'switch', defaultValue: true },
          { name: 'payload', label: 'Configuração JSON', type: 'json', defaultValue: { priority: 'high' } },
          { name: 'context', label: 'Contexto', type: 'hidden', defaultValue: 'workspace' },
          { name: 'advanced', label: 'Modo avançado', type: 'switch', defaultValue: false },
          { name: 'advancedNote', label: 'Nota avançada', type: 'text', visibleWhen: { field: 'advanced', equals: true } },
        ],
        confirmation: { title: 'Aplicar configuração?', confirmLabel: 'Confirmar configuração' },
      },
      {
        id: 'external-send',
        label: 'Enviar atualização',
        description: 'Preparar uma atualização para o canal do cliente.',
        intent: 'secondary',
        execution: 'external',
        permission: { allowed: true },
        gate: { state: 'gated', reason: 'Credenciais externas ainda não foram aprovadas.', allowDryRun: true, allowConfigure: true },
        supportsDryRun: true,
        fields: [{ name: 'destination', label: 'Destino', type: 'text', required: true, defaultValue: '+55 11 99999-0000' }],
      },
      {
        id: 'restricted-export',
        label: 'Exportar dados',
        intent: 'secondary',
        execution: 'local',
        permission: { allowed: false, reason: 'Exige permissão de auditoria.' },
        fields: [],
      },
      {
        id: 'update-lead',
        label: 'Atualizar lead',
        intent: 'secondary',
        execution: 'local',
        permission: { allowed: true },
        fields: [
          { name: 'leadId', label: 'Lead', type: 'hidden', sourceKey: 'id' },
          { name: 'status', label: 'Novo status', type: 'select', sourceKey: 'status', required: true, options: [{ value: 'open', label: 'Aberto' }, { value: 'resolved', label: 'Resolvido' }] },
        ],
        optimisticPatch: [{ op: 'replace', path: '/tabs/0/sections/0/rows/0/status', valueFromField: 'status' }],
      },
      {
        id: 'reset-routing',
        label: 'Limpar roteamento',
        intent: 'danger',
        execution: 'local',
        permission: { allowed: true },
        fields: [],
      },
    ],
    audit: [auditEntry()],
  };
}

function transportFixture(document = documentFixture()): NativeCapabilityTransport & {
  load: ReturnType<typeof vi.fn>;
  execute: ReturnType<typeof vi.fn>;
} {
  return {
    load: vi.fn().mockResolvedValue(document),
    execute: vi.fn().mockResolvedValue({
      status: 'ok',
      version: 'v8',
      message: 'Alteração local aplicada.',
      auditEntry: auditEntry('action-audit'),
    }),
  };
}

describe('NativeCapabilityWorkspace schema-driven renderer', () => {
  it('renders every native section, source health, KPIs, tabs and audit without an iframe', async () => {
    const { container } = render(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" initialDocument={documentFixture()} />);

    expect(screen.getByRole('heading', { name: 'Roteamento de conversas' })).toBeInTheDocument();
    expect(screen.getByText('Comunicações')).toBeInTheDocument();
    expect(screen.getByText('Íntegra')).toBeInTheDocument();
    expect(screen.getByText(/185\.000,00/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Leads ativos' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Resumo operacional' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Fila de revisão' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Distribuição por status' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Canal externo protegido' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Contexto adicional' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Histórico recente' })).toBeInTheDocument();
    expect(container.querySelector('iframe')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /Auditoria/ }));
    expect(screen.getByRole('heading', { name: 'Trilha de auditoria' })).toBeInTheDocument();
    expect(screen.getByText('O bridge reconciliou os registros sem duplicação.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: 'Sem registros' }));
    expect(screen.getByText('Sem dados em sem registros')).toBeInTheDocument();
  });

  it('links tabs to the active panel and supports roving keyboard navigation', () => {
    render(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" initialDocument={documentFixture()} />);

    const tablist = screen.getByRole('tablist', { name: 'Visões da capacidade' });
    const [operationTab, emptyTab, auditTab] = within(tablist).getAllByRole('tab');
    const operationPanel = screen.getByRole('tabpanel', { name: 'Operação' });

    expect(tablist).toHaveAttribute('aria-orientation', 'horizontal');
    expect(operationTab).toHaveAttribute('aria-selected', 'true');
    expect(operationTab).toHaveAttribute('tabindex', '0');
    expect(emptyTab).toHaveAttribute('tabindex', '-1');
    expect(auditTab).toHaveAttribute('tabindex', '-1');
    expect(operationTab).toHaveAttribute('aria-controls', operationPanel.id);
    expect(operationPanel).toHaveAttribute('aria-labelledby', operationTab.id);
    expect(screen.getAllByRole('tabpanel', { hidden: true })).toHaveLength(3);
    for (const tab of [operationTab, emptyTab, auditTab]) {
      const controlledPanel = document.getElementById(tab.getAttribute('aria-controls') ?? '');
      expect(controlledPanel).toHaveAttribute('role', 'tabpanel');
      expect(controlledPanel).toHaveAttribute('aria-labelledby', tab.id);
    }

    operationTab.focus();
    fireEvent.keyDown(operationTab, { key: 'ArrowRight' });
    expect(emptyTab).toHaveFocus();
    expect(emptyTab).toHaveAttribute('aria-selected', 'true');
    expect(emptyTab).toHaveAttribute('tabindex', '0');
    expect(operationTab).toHaveAttribute('tabindex', '-1');
    const emptyPanel = screen.getByRole('tabpanel', { name: 'Sem registros' });
    expect(emptyTab).toHaveAttribute('aria-controls', emptyPanel.id);
    expect(emptyPanel).toHaveAttribute('aria-labelledby', emptyTab.id);
    expect(operationPanel).toHaveAttribute('hidden');

    fireEvent.keyDown(emptyTab, { key: 'End' });
    expect(auditTab).toHaveFocus();
    expect(auditTab).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(auditTab, { key: 'Home' });
    expect(operationTab).toHaveFocus();
    expect(operationTab).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(operationTab, { key: 'ArrowLeft' });
    expect(auditTab).toHaveFocus();
    expect(auditTab).toHaveAttribute('aria-selected', 'true');
  });

  it('searches table rows and opens complete row details with contextual actions', () => {
    render(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" initialDocument={documentFixture()} />);
    const desktop = screen.getByTestId('desktop-table-leads');
    expect(within(desktop).getByText('Ana Lima')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('Pesquisar em leads ativos'), { target: { value: 'Bruno' } });
    expect(within(desktop).queryByText('Ana Lima')).not.toBeInTheDocument();
    expect(within(desktop).getByText('Bruno Alves')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('Pesquisar em leads ativos'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ver detalhes de Ana Lima' }));
    const details = screen.getByTestId('native-detail-pane');
    expect(within(details).getAllByText('Ana Lima')).not.toHaveLength(0);
    expect(within(details).getByText(/12\.500,00/)).toBeInTheDocument();
    expect(within(details).getByRole('button', { name: 'Atualizar lead' })).toBeInTheDocument();
  });

  it('keeps separate mobile and desktop table layouts for 320px and 393px logic', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 320 });
    const { rerender } = render(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" initialDocument={documentFixture()} />);
    const desktop = screen.getByTestId('desktop-table-leads');
    const mobile = screen.getByTestId('mobile-table-leads');
    expect(desktop).toHaveAttribute('data-layout', 'desktop-table');
    expect(desktop.className).toContain('hidden');
    expect(desktop.className).toContain('sm:block');
    expect(mobile).toHaveAttribute('data-layout', 'mobile-stacked');
    expect(mobile.className).toContain('sm:hidden');

    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 393 });
    rerender(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" initialDocument={documentFixture()} />);
    expect(screen.getByTestId('native-capability-workspace')).toHaveClass('min-w-0');
  });

  it('renders every action field type, validates and confirms with version and idempotency', async () => {
    const transport = transportFixture();
    render(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" transport={transport} initialDocument={documentFixture()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Configurar roteamento' }));
    expect(screen.getByLabelText('Nome da configuração')).toHaveValue('Saúde premium');
    expect(screen.getByLabelText('E-mail de aviso')).toHaveAttribute('type', 'email');
    expect(screen.getByLabelText('URL de callback')).toHaveAttribute('type', 'url');
    expect(screen.getByLabelText('Segredo local')).toHaveAttribute('type', 'password');
    expect(screen.getByLabelText('Observações')).toBeInstanceOf(HTMLTextAreaElement);
    expect(screen.getByLabelText('Limite')).toHaveAttribute('type', 'number');
    expect(screen.getByLabelText('Data de vigência')).toHaveAttribute('type', 'date');
    expect(screen.getByLabelText('Executar em')).toHaveAttribute('type', 'datetime-local');
    expect(screen.getByLabelText('Equipe')).toBeInstanceOf(HTMLSelectElement);
    expect(screen.getByText('Canais')).toBeInTheDocument();
    expect(screen.getByLabelText('Configuração JSON')).toBeInstanceOf(HTMLTextAreaElement);
    expect(screen.getAllByRole('switch')).toHaveLength(3);
    expect(screen.queryByLabelText('Nota avançada')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('switch', { name: 'Modo avançado' }));
    expect(screen.getByLabelText('Nota avançada')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Revisar ação/ }));
    expect(screen.getByRole('heading', { name: 'Aplicar configuração?' })).toBeInTheDocument();
    expect(screen.getByText('v7')).toBeInTheDocument();
    expect(screen.getByText(/klm-native:communications:labels-status-assignment:configure-routing:/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Confirmar configuração' }));
    await waitFor(() => expect(transport.execute).toHaveBeenCalledTimes(1));
    expect(transport.execute.mock.calls[0][3]).toMatchObject({
      actionId: 'configure-routing',
      mode: 'execute',
      version: 'v7',
      values: expect.objectContaining({ limit: 20, retries: 2, payload: { priority: 'high' }, advanced: true }),
      transientSecrets: { secret: 'segredo-seguro' },
    });
    expect(transport.execute.mock.calls[0][3].values).not.toHaveProperty('secret');
    expect(transport.execute.mock.calls[0][3].idempotencyKey).toMatch(/^klm-native:communications:/);
    expect(await screen.findByText('Alteração local aplicada.')).toBeInTheDocument();
  });

  it('requires one of the alternative fields without exposing upstream pipe syntax', async () => {
    const document = documentFixture();
    document.actions = [{
      id: 'send-direct-message',
      label: 'Enviar mensagem direta',
      execution: 'local',
      permission: { allowed: true },
      fields: [
        { name: 'username', label: 'Nome de usuário', type: 'text', oneOfGroup: 'username|roomId', description: 'Preencha ao menos um: Nome de usuário ou ID da sala.' },
        { name: 'roomId', label: 'ID da sala', type: 'text', oneOfGroup: 'username|roomId', description: 'Preencha ao menos um: Nome de usuário ou ID da sala.' },
        { name: 'text', label: 'Mensagem', type: 'textarea', required: true },
      ],
    }];
    const transport = transportFixture();
    render(<NativeCapabilityWorkspace domain="collaboration" capability="direct-messages" transport={transport} initialDocument={document} />);

    fireEvent.click(screen.getByRole('button', { name: 'Enviar mensagem direta' }));
    expect(screen.queryByText(/username\|roomId/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Mensagem'), { target: { value: 'Olá' } });
    fireEvent.click(screen.getByRole('button', { name: /Revisar ação/ }));
    expect(screen.getByRole('alert')).toHaveTextContent('Preencha Nome de usuário ou ID da sala.');

    fireEvent.change(screen.getByLabelText('ID da sala'), { target: { value: 'room-42' } });
    fireEvent.click(screen.getByRole('button', { name: /Revisar ação/ }));
    expect(screen.getByRole('heading', { name: 'Confirmar enviar mensagem direta?' })).toBeInTheDocument();
  });

  it('expands alternative required fields in legacy adapter snapshots', () => {
    const normalized = normalizeNativeCapabilityDocument({
      provider: 'rocketchat',
      capabilityKey: 'direct-messages',
      actions: [{ command: 'send-direct-message', required: ['username|roomId', 'text'] }],
      content: { items: [] },
    }, 'collaboration', 'direct-messages');
    const action = normalized.actions.find((item) => item.id === 'send-direct-message');

    expect(action?.fields.map((field) => field.name)).toEqual(['username', 'roomId', 'text']);
    expect(action?.fields.slice(0, 2).every((field) => field.oneOfGroup === 'username|roomId')).toBe(true);
    expect(action?.fields[2].required).toBe(true);
    expect(action?.fields[2].type).toBe('textarea');
  });

  it('allows a gated external action only as configure or dry-run and never claims delivery', async () => {
    const transport = transportFixture();
    transport.execute.mockResolvedValue({ status: 'sent', message: 'Mensagem enviada pelo provider.' });
    render(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" transport={transport} initialDocument={documentFixture()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Enviar atualização (simulação)' }));
    expect(screen.queryByRole('button', { name: 'Execução' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Simulação' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Configuração' })).toBeInTheDocument();
    expect(screen.getByText(/não envia, publica, cobra nem altera/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Revisar ação/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Executar simulação' }));
    await waitFor(() => expect(transport.execute).toHaveBeenCalledTimes(1));
    expect(transport.execute.mock.calls[0][3].mode).toBe('dry-run');
    expect(await screen.findByText('Simulação concluída. Nenhum efeito externo foi executado.')).toBeInTheDocument();
    expect(screen.queryByText('Mensagem enviada pelo provider.')).not.toBeInTheDocument();
  });

  it('applies an optimistic row patch and rolls it back after a conflict', async () => {
    let rejectMutation: (error: Error) => void = () => undefined;
    const pending = new Promise<never>((_resolve, reject) => { rejectMutation = reject; });
    const transport = transportFixture();
    transport.execute.mockReturnValue(pending);
    render(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" transport={transport} initialDocument={documentFixture()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Ver detalhes de Ana Lima' }));
    fireEvent.click(within(screen.getByTestId('native-detail-pane')).getByRole('button', { name: 'Atualizar lead' }));
    fireEvent.change(screen.getByLabelText('Novo status'), { target: { value: 'resolved' } });
    fireEvent.click(screen.getByRole('button', { name: /Revisar ação/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar operação' }));

    await waitFor(() => expect(within(screen.getByTestId('desktop-table-leads')).getByText('Resolvido')).toBeInTheDocument());
    rejectMutation(new NativeCapabilityError(409, 'Versão divergente.', 'version_conflict'));
    expect(await screen.findByText(/dados mudaram desde a abertura/i)).toBeInTheDocument();
    expect(within(screen.getByTestId('desktop-table-leads')).getByText('Aberto')).toBeInTheDocument();
  });

  it('disables actions without permission and makes loaded data read-only while offline', () => {
    const { rerender } = render(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" initialDocument={documentFixture()} online={false} />);
    expect(screen.getByText(/Modo offline: os dados já carregados/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Configurar roteamento' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Exportar dados' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Exportar dados' })).toHaveAttribute('title', 'Exige permissão de auditoria.');

    rerender(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" initialDocument={documentFixture()} online />);
    expect(screen.getByRole('button', { name: 'Configurar roteamento' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Exportar dados' })).toBeDisabled();
  });

  it('preserves a fetched snapshot and disables every action surface after connectivity drops', async () => {
    const transport = transportFixture();
    const view = render(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" transport={transport} online />);
    expect(await screen.findByRole('heading', { name: 'Roteamento de conversas' })).toBeInTheDocument();

    view.rerender(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" transport={transport} online={false} />);
    expect(screen.getByRole('heading', { name: 'Roteamento de conversas' })).toBeInTheDocument();
    expect(screen.getByText(/Modo offline: os dados já carregados/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Configurar roteamento' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Limpar roteamento' })).toBeDisabled();
  });

  it('renders a dedicated 403 state without exposing capability content', async () => {
    const transport = transportFixture();
    transport.load.mockRejectedValue(new NativeCapabilityError(403, 'Acesso restrito ao administrador.', 'forbidden'));
    render(<NativeCapabilityWorkspace domain="crm" capability="audit" transport={transport} />);

    expect(await screen.findByRole('heading', { name: 'Acesso não autorizado' })).toBeInTheDocument();
    expect(screen.getByText('Acesso restrito ao administrador.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Tentar novamente' })).not.toBeInTheDocument();
    expect(screen.queryByText('Roteamento de conversas')).not.toBeInTheDocument();
  });

  it('recovers from a typed backend error through retry', async () => {
    const transport = transportFixture();
    transport.load
      .mockRejectedValueOnce(new NativeCapabilityError(503, 'Bridge em inicialização.', 'bridge_starting'))
      .mockResolvedValueOnce(documentFixture());
    render(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" transport={transport} />);

    expect(await screen.findByRole('heading', { name: 'Capacidade indisponível' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Tentar novamente' }));
    expect(await screen.findByRole('heading', { name: 'Roteamento de conversas' })).toBeInTheDocument();
    expect(transport.load).toHaveBeenCalledTimes(2);
  });

  it('shows loading and honest empty states', async () => {
    let resolveLoad: (document: NativeCapabilityDocument) => void = () => undefined;
    const transport = transportFixture();
    transport.load.mockReturnValue(new Promise((resolve) => { resolveLoad = resolve; }));
    const view = render(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" transport={transport} />);
    expect(screen.getByRole('status', { name: 'Carregando capacidade nativa' })).toBeInTheDocument();
    resolveLoad(documentFixture());
    expect(await screen.findByRole('heading', { name: 'Roteamento de conversas' })).toBeInTheDocument();
    expect(transport.load).toHaveBeenCalledTimes(1);

    const empty = documentFixture();
    empty.kpis = [];
    empty.tabs = [];
    empty.actions = [];
    empty.audit = [];
    view.rerender(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" initialDocument={empty} />);
    expect(await screen.findByText('Nenhum dado disponível')).toBeInTheDocument();
  });

  it('keeps the native workspace free of launch controls and embedded upstream applications', () => {
    const { container } = render(<NativeCapabilityWorkspace domain="communications" capability="labels-status-assignment" initialDocument={documentFixture()} />);
    expect(screen.queryByText(/Abrir motor|Chatwoot|Rocket\.Chat|ChatterMate|BottleCRM/)).not.toBeInTheDocument();
    expect(container.querySelector('iframe')).toBeNull();
  });
});

describe('native API-boundary snapshot compatibility', () => {
  it('normalizes only the canonical KLM document fields', () => {
    const payload = documentFixture();
    const normalized = normalizeNativeCapabilityDocument(
      payload,
      'communications',
      'labels-status-assignment',
    );

    expect(normalized.domain.id).toBe('communications');
    expect(normalized.capability.id).toBe('labels-status-assignment');
    expect(normalized.tabs[0].sections.some((item) => item.title === 'Leads ativos')).toBe(true);
    expect(normalized.actions.some((item) => item.label === 'Configurar roteamento')).toBe(true);
    expect(normalized.tabs[0].sections.some((item) => item.type === 'table')).toBe(true);
    expect(normalized.version).toBe('v7');
  });
});

describe('native capability HTTP contract', () => {
  it('uses the scoped endpoint and sends optimistic version plus idempotency headers', async () => {
    const document = documentFixture();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(document), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok', version: 'v8' }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    expect(buildNativeCapabilityPath('communications', 'labels-status-assignment')).toBe('/api/v1/capabilities/domains/communications/labels-status-assignment/document');
    await fetchNativeCapability('communications', 'labels-status-assignment');
    const action = document.actions.find((item) => item.id === 'configure-routing') as NativeActionSchema;
    await executeNativeCapabilityAction('communications', 'labels-status-assignment', action, {
      actionId: action.id,
      values: { name: 'Saúde premium' },
      transientSecrets: { secret: 'only-in-flight' },
      mode: 'execute',
      version: 'v7',
      idempotencyKey: 'native-test-key',
    });

    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE}/api/v1/capabilities/domains/communications/labels-status-assignment/document`);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'include' });
    expect(fetchMock.mock.calls[1][0]).toBe(`${API_BASE}/api/v1/capabilities/domains/communications/labels-status-assignment/actions`);
    expect(fetchMock.mock.calls[1][1]).toMatchObject({
      method: 'POST',
      credentials: 'include',
      headers: expect.objectContaining({ 'If-Match': 'v7', 'Idempotency-Key': 'native-test-key', 'X-KLM-Action-Mode': 'execute' }),
    });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({
      actionId: 'configure-routing',
      values: { name: 'Saúde premium' },
      transientSecrets: { secret: 'only-in-flight' },
      version: 'v7',
      idempotencyKey: 'native-test-key',
    });
  });

  it('rejects unsafe route segments before issuing a request', () => {
    expect(() => buildNativeCapabilityPath('../communications' as never, 'labels')).toThrow('Domínio ou capacidade inválidos.');
  });

  it('loads domain and capability from the canonical route contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(documentFixture()), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    render(
      <MemoryRouter initialEntries={['/app/capacidades/communications/labels-status-assignment']}>
        <Routes>
          <Route path="/app/capacidades/:domain/:capability" element={<NativeCapabilityPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByRole('heading', { name: 'Roteamento de conversas' })).toBeInTheDocument();
    expect(fetchMock.mock.calls[0][0]).toContain('/api/v1/capabilities/domains/communications/labels-status-assignment/document');
  });
});
