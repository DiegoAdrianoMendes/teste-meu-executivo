/** @vitest-environment jsdom */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { WhatsAppAutomationRulesPanel } from '@/components/admin/WhatsAppAutomationRulesPanel';
import type { WhatsAppAutomationSettings } from '@/types';

const unconfirmedSettings: WhatsAppAutomationSettings = {
  enabled: false,
  onboardingCompleted: false,
  timezone: 'America/Sao_Paulo',
  businessHoursStart: 8,
  businessHoursEnd: 19,
  rules: [
    {
      id: 'welcome-default',
      label: 'Boas-vindas imediatas',
      enabled: false,
      triggerType: 'welcome',
      keyword: null,
      replyText: 'Texto sugerido e editável.',
      flowTemplateId: null,
      targetQueueStatus: null,
      targetColumnId: null,
      escalateToHuman: false,
      onlyFirstMessage: true,
      silenceWindowHours: 0,
    },
    {
      id: 'off-hours-default',
      label: 'Resposta fora do horário',
      enabled: false,
      triggerType: 'off_hours',
      keyword: null,
      replyText: 'Retornaremos no próximo expediente.',
      flowTemplateId: null,
      targetQueueStatus: 'follow_up',
      targetColumnId: null,
      escalateToHuman: false,
      onlyFirstMessage: false,
      silenceWindowHours: 12,
    },
  ],
};

describe('WhatsApp automation explicit onboarding choice', () => {
  it('keeps suggestions disabled until the office confirms and persists its silence window', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<WhatsAppAutomationRulesPanel value={unconfirmedSettings} onSave={onSave} />);

    expect(screen.getByText('Escolha obrigatória do escritório')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Texto sugerido e editável.')).toBeInTheDocument();

    const silenceInput = screen.getByLabelText('Silêncio após responder (horas)');
    fireEvent.change(silenceInput, { target: { value: '18' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar escolha de automação' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      enabled: false,
      onboardingCompleted: true,
      rules: expect.arrayContaining([
        expect.objectContaining({ id: 'off-hours-default', enabled: false, silenceWindowHours: 18 }),
      ]),
    }));
  });
});
