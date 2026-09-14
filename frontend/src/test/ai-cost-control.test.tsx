import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AiCostControlPanel, AiCostUsageSummary } from '@/components/admin/AiCostControlPanel';
import type { PlatformAiCostSummary } from '@/types';

const summary: PlatformAiCostSummary = {
  workspaceId: 'workspace-1',
  periodStart: '2026-07-01T00:00:00Z',
  periodEnd: '2026-08-01T00:00:00Z',
  totalRuns: 12,
  inputTokens: 30_000,
  outputTokens: 20_000,
  totalTokens: 50_000,
  costUsd: 1.2345,
  monthlyLimit: 100_000,
  alertAtPct: 80,
  hardLimit: true,
  usedPercent: 50,
  remainingTokens: 50_000,
  alertLevel: 'ok',
  quotaState: 'configured',
  byModel: [{
    model: 'modelo-local',
    provider: 'local',
    runs: 12,
    inputTokens: 30_000,
    outputTokens: 20_000,
    costUsd: 1.2345,
  }],
  byTask: [{
    taskClass: 'simple',
    runs: 12,
    inputTokens: 30_000,
    outputTokens: 20_000,
    costUsd: 1.2345,
  }],
  gates: {
    modelApisEnabled: false,
    onlineApproved: false,
    externalActionsEnabled: false,
  },
};

describe('AiCostControlPanel', () => {
  it('mostra consumo persistido e confirma os gates externos fechados', () => {
    render(<AiCostControlPanel value={summary} />);

    expect(screen.getAllByText('50.000')).toHaveLength(2);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
    expect(screen.getByText('Ambiente local protegido: nenhuma chamada externa foi liberada.')).toBeInTheDocument();
    expect(screen.getByText('modelo-local')).toBeInTheDocument();
    expect(screen.queryByText(/US\$/)).not.toBeInTheDocument();
    expect(screen.queryByText('Custo interno medido')).not.toBeInTheDocument();
    expect(screen.getByTestId('ai-token-package-support')).toHaveTextContent('somente o suporte confirma e libera os tokens');
  });

  it('mostra custo monetário somente quando o painel do super admin solicita', () => {
    render(<AiCostControlPanel value={summary} showMonetaryCost />);

    expect(screen.getByText('Custo interno medido')).toBeInTheDocument();
    expect(screen.getAllByText(/US\$/).length).toBeGreaterThan(0);
  });

  it('persiste uma política válida quando o usuário tem permissão', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<AiCostControlPanel value={summary} onSave={onSave} />);

    fireEvent.change(screen.getByLabelText('Limite mensal de tokens'), { target: { value: '200000' } });
    fireEvent.change(screen.getByLabelText('Alertar em (%)'), { target: { value: '75' } });
    fireEvent.click(screen.getByRole('switch', { name: /Bloqueio rígido/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Salvar política' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledWith({
      monthlyLimit: 200_000,
      alertAtPct: 75,
      hardLimit: false,
    }));
  });

  it('bloqueia edição quando a política é somente leitura', () => {
    render(<AiCostControlPanel value={summary} readOnly onSave={vi.fn()} />);

    expect(screen.getByLabelText('Limite mensal de tokens')).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Salvar política' })).not.toBeInTheDocument();
  });

  it('explica uma política inválida e não tenta persistir', () => {
    const onSave = vi.fn();
    render(<AiCostControlPanel value={summary} onSave={onSave} />);

    fireEvent.change(screen.getByLabelText('Alertar em (%)'), { target: { value: '101' } });

    expect(screen.getByRole('alert')).toHaveTextContent('O alerta deve ficar entre 1% e 100%.');
    expect(screen.getByRole('button', { name: 'Salvar política' })).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('mantém um resumo explícito e clicável nas configurações', () => {
    const onOpen = vi.fn();
    render(<AiCostUsageSummary value={summary} onOpen={onOpen} />);

    expect(screen.getByTestId('ai-cost-usage-summary')).toHaveTextContent('Consumo de IA neste mês');
    expect(screen.getByRole('progressbar', { name: 'Uso mensal de IA' })).toHaveAttribute('aria-valuenow', '50');
    fireEvent.click(screen.getByRole('button', { name: 'Abrir uso de IA' }));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it('não chama a ausência de franquia de ilimitada', () => {
    render(<AiCostUsageSummary value={{ ...summary, monthlyLimit: null, remainingTokens: null, usedPercent: 0, alertLevel: 'none', quotaState: 'missing' }} onOpen={vi.fn()} />);

    expect(screen.getByRole('progressbar', { name: 'Uso mensal de IA' })).toHaveAttribute('aria-valuenow', '0');
    expect(screen.getByText('Franquia pendente')).toBeInTheDocument();
    expect(screen.queryByText(/Ilimitado/i)).not.toBeInTheDocument();
  });

  it('expõe a falha de leitura e permite tentar novamente', () => {
    const onRetry = vi.fn();
    render(<AiCostControlPanel value={null} error="Serviço local indisponível." onRetry={onRetry} />);

    expect(screen.getByText('Não foi possível carregar o consumo de IA')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Tentar novamente' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
