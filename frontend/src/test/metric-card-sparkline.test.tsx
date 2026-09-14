/** @vitest-environment jsdom */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { KlmMetricCard } from '@/klm/components/Primitives';

afterEach(() => cleanup());

describe('KlmMetricCard sparkline', () => {
  it('renders a sparkline when a series is provided', () => {
    render(<KlmMetricCard label="Leads novos" value="12" delta="7 dias" sparkline={[1, 2, 3, 4, 5]} />);
    expect(screen.getByTestId('klm-sparkline')).toBeInTheDocument();
  });

  it('renders normally without breaking when no series is provided', () => {
    render(<KlmMetricCard label="Leads novos" value="12" delta="7 dias" />);
    expect(screen.queryByTestId('klm-sparkline')).not.toBeInTheDocument();
    expect(screen.getByText('Leads novos')).toBeInTheDocument();
  });

  it('does not render a sparkline for a series shorter than 2 points', () => {
    render(<KlmMetricCard label="Leads novos" value="12" delta="7 dias" sparkline={[1]} />);
    expect(screen.queryByTestId('klm-sparkline')).not.toBeInTheDocument();
  });
});
