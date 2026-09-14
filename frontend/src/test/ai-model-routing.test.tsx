import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AiByokKeysPanel, AiModelRoutingPanel } from '@/components/admin/AiModelRoutingPanel';
import type { WorkspaceAiCredentialStatus, WorkspaceAiModelRouting } from '@/types';

const routing: WorkspaceAiModelRouting = {
  catalogVersion: 'catalog-test-1',
  classes: [
    {
      taskClass: 'simple',
      provider: 'openai',
      model: 'modelo-simples',
      source: 'platform',
      platformProvider: 'openai',
      platformModel: 'modelo-simples',
      price: { inputPerMillionUsd: 0.1, outputPerMillionUsd: 0.4 },
    },
    {
      taskClass: 'medium',
      provider: 'openai',
      model: 'modelo-medio',
      source: 'platform',
      platformProvider: 'openai',
      platformModel: 'modelo-medio',
      price: { inputPerMillionUsd: 0.5, outputPerMillionUsd: 1.5 },
    },
    {
      taskClass: 'complex',
      provider: 'anthropic',
      model: 'modelo-complexo',
      source: 'platform',
      platformProvider: 'anthropic',
      platformModel: 'modelo-complexo',
      price: { inputPerMillionUsd: 3, outputPerMillionUsd: 15 },
    },
    {
      taskClass: 'premium',
      provider: 'anthropic',
      model: 'modelo-custom',
      source: 'workspace',
      platformProvider: 'anthropic',
      platformModel: 'modelo-premium',
      price: { inputPerMillionUsd: 5, outputPerMillionUsd: 25 },
    },
  ],
  options: [
    { provider: 'openai', model: 'modelo-simples', price: { inputPerMillionUsd: 0.1, outputPerMillionUsd: 0.4 } },
    { provider: 'openai', model: 'modelo-medio', price: { inputPerMillionUsd: 0.5, outputPerMillionUsd: 1.5 } },
    { provider: 'anthropic', model: 'modelo-complexo', price: { inputPerMillionUsd: 3, outputPerMillionUsd: 15 } },
    { provider: 'anthropic', model: 'modelo-custom', price: { inputPerMillionUsd: 5, outputPerMillionUsd: 25 } },
    { provider: 'anthropic', model: 'modelo-premium', price: { inputPerMillionUsd: 5, outputPerMillionUsd: 25 } },
  ],
};

const credentials: WorkspaceAiCredentialStatus[] = [
  {
    provider: 'anthropic',
    configured: true,
    isActive: true,
    apiKeyMasked: '****abcd',
    updatedAt: '2026-07-24T00:00:00Z',
  },
  {
    provider: 'openai',
    configured: false,
    isActive: false,
    apiKeyMasked: null,
    updatedAt: null,
  },
];

describe('AiModelRoutingPanel', () => {
  it('mostra as quatro classes de negócio com custo por 1M e badge de padrão', () => {
    render(<AiModelRoutingPanel value={routing} />);

    expect(screen.getByLabelText('Petições')).toBeInTheDocument();
    expect(screen.getByLabelText('Análise jurídica')).toBeInTheDocument();
    expect(screen.getByLabelText('Conversas')).toBeInTheDocument();
    expect(screen.getByLabelText('Leitura de documentos')).toBeInTheDocument();

    // Três classes seguem o padrão da plataforma; a premium é customizada.
    expect(screen.getAllByText('Padrão da plataforma')).toHaveLength(3);
    expect(screen.getByText(/US\$\s*3,00 entrada · US\$\s*15,00 saída por 1M tokens/)).toBeInTheDocument();
    expect((screen.getByLabelText('Petições') as HTMLSelectElement).value).toBe('anthropic:modelo-custom');
  });

  it('persiste somente as classes alteradas, com null para voltar ao padrão', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<AiModelRoutingPanel value={routing} onSave={onSave} />);

    const saveButton = screen.getByRole('button', { name: 'Salvar roteamento' });
    expect(saveButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Conversas'), {
      target: { value: 'openai:modelo-medio' },
    });
    fireEvent.change(screen.getByLabelText('Petições'), {
      target: { value: '__platform__' },
    });
    expect(saveButton).toBeEnabled();
    fireEvent.click(saveButton);

    await waitFor(() => expect(onSave).toHaveBeenCalledWith({
      routes: {
        simple: { provider: 'openai', model: 'modelo-medio' },
        premium: null,
      },
    }));
  });

  it('congela a edição em modo somente leitura', () => {
    render(<AiModelRoutingPanel value={routing} readOnly onSave={vi.fn()} />);

    expect(screen.getByLabelText('Petições')).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Salvar roteamento' })).not.toBeInTheDocument();
  });

  it('expõe a falha de leitura e permite tentar novamente', () => {
    const onRetry = vi.fn();
    render(<AiModelRoutingPanel value={null} error="Serviço local indisponível." onRetry={onRetry} />);

    expect(screen.getByText('Não foi possível carregar o roteamento de modelos')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Tentar novamente' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});

describe('AiByokKeysPanel', () => {
  it('mostra estado por provedor sempre redigido e o aviso de responsabilidade', () => {
    render(<AiByokKeysPanel value={credentials} />);

    expect(screen.getByText('Chave própria · ****abcd')).toBeInTheDocument();
    expect(screen.getByText('Chave da plataforma')).toBeInTheDocument();
    expect(screen.getByText('Aviso de responsabilidade.')).toBeInTheDocument();
    // Input write-only: tipo password, nunca reexibe a chave.
    expect(screen.getByLabelText('Nova chave de API de OpenAI')).toHaveAttribute('type', 'password');
  });

  it('só habilita salvar com chave plausível e envia para o provedor certo', async () => {
    const onSaveKey = vi.fn().mockResolvedValue(undefined);
    render(<AiByokKeysPanel value={credentials} onSaveKey={onSaveKey} />);

    const input = screen.getByLabelText('Nova chave de API de OpenAI');
    const save = screen.getByRole('button', { name: 'Salvar chave' });
    expect(save).toBeDisabled();

    fireEvent.change(input, { target: { value: 'curta' } });
    expect(save).toBeDisabled();

    fireEvent.change(input, { target: { value: 'sk-proj-nova-chave-0123456789' } });
    expect(save).toBeEnabled();
    fireEvent.click(save);

    await waitFor(() => expect(onSaveKey).toHaveBeenCalledWith('openai', 'sk-proj-nova-chave-0123456789'));
    await waitFor(() => expect((input as HTMLInputElement).value).toBe(''));
  });

  it('remove a chave própria e volta para a chave da plataforma', async () => {
    const onRemoveKey = vi.fn().mockResolvedValue(undefined);
    render(<AiByokKeysPanel value={credentials} onRemoveKey={onRemoveKey} />);

    fireEvent.click(screen.getByRole('button', { name: 'Remover chave de Anthropic (Claude)' }));
    await waitFor(() => expect(onRemoveKey).toHaveBeenCalledWith('anthropic'));
  });

  it('congela tudo em modo somente leitura', () => {
    render(<AiByokKeysPanel value={credentials} readOnly onSaveKey={vi.fn()} onRemoveKey={vi.fn()} />);

    expect(screen.getByLabelText('Nova chave de API de OpenAI')).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Salvar chave' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Remover/ })).not.toBeInTheDocument();
  });
});
