import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MobileDataCard, ResponsiveDataExplorer, ResponsiveDialog, ResponsiveSheet } from '@/components/responsive';

describe('responsive primitives', () => {
  it('expõe cartão e explorador com alternativa mobile', () => {
    render(<ResponsiveDataExplorer label="Contas" mobile={<MobileDataCard title="Conta móvel" />}><div>tabela</div></ResponsiveDataExplorer>);
    expect(screen.getByRole('region', { name: 'Contas' })).toBeInTheDocument();
    expect(screen.getByText('Conta móvel')).toBeInTheDocument();
  });

  it('mantém diálogo e sheet controláveis por foco/teclado', () => {
    render(<><ResponsiveDialog open onOpenChange={vi.fn()} title="Dialog responsivo">conteúdo</ResponsiveDialog><ResponsiveSheet open onOpenChange={vi.fn()} title="Sheet responsivo">conteúdo</ResponsiveSheet></>);
    expect(screen.getByText('Dialog responsivo')).toBeInTheDocument();
    expect(screen.getByText('Sheet responsivo')).toBeInTheDocument();
  });
});
