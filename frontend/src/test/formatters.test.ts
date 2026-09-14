import { describe, expect, it } from 'vitest';
import { formatBRL, formatDecimal } from '@/lib/formatters';

describe('Brazilian numeric formatters', () => {
  it('renders full BRL values with cents and never compact notation', () => {
    expect(formatBRL(1_000_000)).toBe('R$ 1.000.000,00');
    expect(formatBRL(84_250.5)).toBe('R$ 84.250,50');
    expect(formatBRL(0)).toBe('R$ 0,00');
    expect(formatBRL(-1.01)).toBe('-R$ 1,01');
    expect(formatBRL(1.005)).toBe('R$ 1,01');
    expect(formatBRL(Number.MAX_SAFE_INTEGER)).toBe('R$ 9.007.199.254.740.991,00');
    expect(formatBRL(null)).toBe('—');
    expect(formatBRL(undefined)).toBe('—');
    expect(formatBRL(Number.NaN)).toBe('—');
    expect(formatBRL(Number.POSITIVE_INFINITY)).toBe('—');
  });

  it('renders decimal metrics with a stable number of places', () => {
    expect(formatDecimal(4.8)).toBe('4,80');
    expect(formatDecimal(12.345, 1)).toBe('12,3');
    expect(formatDecimal(undefined)).toBe('—');
    expect(formatDecimal(Number.NEGATIVE_INFINITY)).toBe('—');
  });
});
