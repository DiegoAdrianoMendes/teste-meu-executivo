/** @vitest-environment jsdom */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { AgentRunDrawer } from '@/klm/components/AgentRunDrawer';
import { HumanDecisionDrawer } from '@/klm/components/HumanDecisionDrawer';

describe('agent and human drawers', () => {
  it('renders running agent state and closes from overlay', () => {
    const onOpenChange = vi.fn();
    render(<AgentRunDrawer open onOpenChange={onOpenChange} agentName="Hermes" status="running" summary="processando" />);
    expect(screen.getByText('Executando')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('keeps a human checkpoint read-only instead of creating a manual linked decision', () => {
    render(<AgentRunDrawer open onOpenChange={vi.fn()} agentName="Controladoria" status="waiting_human" />);
    expect(screen.getByText('Aguardando humano')).toBeInTheDocument();
    expect(screen.getByText(/Central de Decisões/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /chamar humano/i })).not.toBeInTheDocument();
  });

  it('exposes one approve and one reject action with loading disabled', () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(<HumanDecisionDrawer open onOpenChange={vi.fn()} title="Revisar publicação" disabled onApprove={onApprove} onReject={onReject} />);
    expect(screen.getByRole('button', { name: /aprovar/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /rejeitar/i })).toBeDisabled();
  });

  it('explains why a decision cannot be taken', () => {
    render(<HumanDecisionDrawer open onOpenChange={vi.fn()} title="Revisar publicação" disabled disabledReason="Esta decisão pertence a outro responsável." />);
    expect(screen.getByText('Esta decisão pertence a outro responsável.')).toBeInTheDocument();
  });
});
