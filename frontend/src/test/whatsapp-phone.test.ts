import { describe, expect, it } from 'vitest';

import {
  formatWhatsAppPhoneDisplay,
  normalizeWhatsAppPhone,
  requireWhatsAppPhone,
} from '@/lib/whatsapp-phone';

describe('WhatsApp phone contract', () => {
  it.each([
    ['(61) 99999-9999', '5561999999999'],
    ['+55 61 99999-9999', '5561999999999'],
    ['0055 61 99999-9999', '5561999999999'],
    ['5561999999999', '5561999999999'],
    ['+1 202-555-0123', '12025550123'],
  ])('normalizes %s to Meta digits %s', (input, expected) => {
    expect(normalizeWhatsAppPhone(input)).toBe(expected);
  });

  it.each(['', '99999999', 'telefone-invalido', '+55 61 99999-9999 ramal 2'])(
    'rejects invalid or ambiguous input %s',
    (input) => expect(normalizeWhatsAppPhone(input)).toBeNull(),
  );

  it('fails closed before an invalid phone can reach the API', () => {
    expect(() => requireWhatsAppPhone('99999999')).toThrow(/DDI, DDD/);
  });

  it('formats a canonical number only for display', () => {
    expect(formatWhatsAppPhoneDisplay('5561999999999')).toContain('+55');
  });
});
