import { describe, expect, it } from 'vitest';

import { estadoOab, normalizarOab, rotuloTribunal } from '@/klm/lib/oab';

/** Espelho de tests/services/test_oab_numero.py: as mesmas formas sujas e as mesmas recusas. */
describe('normalizarOab (paridade com app/services/oab_numero.py)', () => {
  it.each([
    ['68812/DF', '68812/DF'],
    ['OAB/DF 123.456', '123456/DF'],
    ['123456-df', '123456/DF'],
    ['123456 DF', '123456/DF'],
    ['DF 123456', '123456/DF'],
    ['SP123456', '123456/SP'],
    ['123456SP', '123456/SP'],
    ['OAB/DF nº 123.456', '123456/DF'],
    ['OAB/DF n. 123456', '123456/DF'],
    ['068812/DF', '68812/DF'],
    ['OAB/SP 123456 SP', '123456/SP'],
    ['OAB/DF n.º 123.456', '123456/DF'],
    ['OAB/DF n 123456', '123456/DF'],
    ['n123456 DF', '123456/DF'],
    ['OAB/DF' + ' '.repeat(20) + 'nº' + ' '.repeat(10) + '123.456', '123456/DF'],
  ])('canoniza %s → %s', (entrada, canonica) => {
    expect(normalizarOab(entrada)).toEqual({ ok: true, canonica });
  });

  it.each([
    ['123456A', 'letra'],
    ['123456/DF-A', 'letra'],
    ['123456 A/DF', 'letra'],
    ['OAB/SP 123456 RJ', 'uma só'],
    ['0000000/DF', 'inválido'],
    ['12345 6/DF', 'único número'],
    ['12345678/DF', '7 dígitos'],
    ['', 'formato'],
    ['123456', 'UF'],
    ['123456/XX', 'UF desconhecida'],
    ['123456N/DF', 'letra'],
    ['123456 N DF', 'letra'],
    ['123456NO/DF', 'letra'],
    ['123456/D', 'incompleta'],
    ['123456/DFF', 'incompleta'],
    ['１２３４５６/DF', 'não reconhecidos'],
    ['OAB/DF ' + 'x'.repeat(40), 'longa demais'],
  ])('recusa %s (%s)', (entrada, trecho) => {
    const resultado = normalizarOab(entrada);
    expect(resultado.ok).toBe(false);
    if (resultado.ok === false) expect(resultado.erro).toContain(trecho);
  });

  it('letra nunca é descartada em silêncio', () => {
    // 123456A/DF não pode virar 123456/DF: seria a OAB de outro advogado
    expect(normalizarOab('123456A/DF').ok).toBe(false);
  });
});

describe('estadoOab e rotuloTribunal', () => {
  it('distingue ausente, canônica e inválida (legado sem UF)', () => {
    expect(estadoOab(null)).toBe('ausente');
    expect(estadoOab('68812/DF')).toBe('canonica');
    expect(estadoOab('68812')).toBe('invalida');
    expect(estadoOab('068812/DF')).toBe('invalida'); // fora da canônica: o worker pula e grava last_error
  });

  it('grita só o alias; texto livre legado fica como está', () => {
    expect(rotuloTribunal('tjdft')).toBe('TJDFT');
    expect(rotuloTribunal('tre-dft')).toBe('TRE-DFT');
    expect(rotuloTribunal('Tribunal de Justiça do DF')).toBe('Tribunal de Justiça do DF');
    expect(rotuloTribunal(null)).toBe('—');
  });
});
