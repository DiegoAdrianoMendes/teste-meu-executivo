/**
 * Inscrição na OAB na forma canônica `N/UF`, espelho de `app/services/oab_numero.py`.
 *
 * Só para feedback imediato no formulário; a validação de verdade é a do
 * backend (`POST`/`PATCH /processos` devolvem 422 `PROC-MON-006`). Regra que
 * importa: letra NUNCA é descartada por máscara. `123456A/DF` digitado não pode
 * virar `123456/DF` em silêncio, porque isso cadastra a OAB de outro advogado;
 * a letra aparece como erro sobre o valor cru.
 */

export const UFS = new Set(
  'AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SE SP TO'.split(' '),
);
const TOKENS_IGNORADOS = new Set(['OAB', 'º', '°', 'INSCRICAO', 'INSCRIÇÃO', 'INSC']);
// "nº", "n.º", "no", "n." só valem como prefixo do número; fora dessa posição, N é letra de inscrição.
const PREFIXO_NUMERO = /\bN(?:O\b|\.?[º°]|\.)?\s*(?=[0-9])/g;

export type OabNormalizada = { ok: true; canonica: string } | { ok: false; erro: string };

export function normalizarOab(valor: string): OabNormalizada {
  if (!valor || !valor.trim()) return { ok: false, erro: 'Informe a OAB no formato 123456/UF.' };
  let bruto = valor.split(/\s+/).filter(Boolean).join(' ');
  if (bruto.length > 40) return { ok: false, erro: 'OAB longa demais. Informe só o número e a UF, no formato 123456/UF.' };
  bruto = bruto.toUpperCase();
  // ponto de milhar dentro do número ("123.456") não separa; espaço separa
  bruto = bruto.replace(/(?<=[0-9])\.(?=[0-9])/g, '');
  bruto = bruto.replace(PREFIXO_NUMERO, ' ');
  // uma letra colada DEPOIS do número ("123456A") é letra de inscrição, não UF
  const letraColada = /[0-9][A-Z](?![A-Z])/.test(bruto);
  // separa dígitos de letras coladas ("SP123456", "123456SP")
  bruto = bruto.replace(/(?<=[0-9])(?=[A-ZÇº°])|(?<=[A-ZÇº°])(?=[0-9])/g, ' ');
  const tokens = bruto.split(/[\s/.\-,:;()]+/).filter(Boolean).filter((t) => !TOKENS_IGNORADOS.has(t));
  const eNumero = (t: string) => /^[0-9]+$/.test(t);
  const numeros = tokens.filter(eNumero);
  const ufs = tokens.filter((t) => UFS.has(t));
  const sobras = tokens.filter((t) => !eNumero(t) && !UFS.has(t));

  if (sobras.length > 0 && ufs.length === 0 && !letraColada && sobras.every((s) => /^[A-Z]{1,3}$/.test(s))) {
    return { ok: false, erro: `UF desconhecida ou incompleta: ${sobras[0]}. Informe a sigla da seccional.` };
  }
  if (sobras.some((s) => !/^[A-Z]+$/.test(s))) {
    return { ok: false, erro: 'Caracteres não reconhecidos na OAB. Informe só o número e a UF, no formato 123456/UF.' };
  }
  if (sobras.length > 0) {
    return {
      ok: false,
      erro:
        'OAB com letra não é aceita (inscrição suplementar ou de estagiário): o DJEN responde vazio ' +
        'a essa consulta e o processo ficaria sem monitoramento. Informe só o número e a UF, no formato 123456/UF.',
    };
  }
  if (numeros.length !== 1) return { ok: false, erro: 'Informe um único número de inscrição, no formato 123456/UF.' };
  if (new Set(ufs).size !== 1) return { ok: false, erro: 'Informe a UF da seccional (uma só), no formato 123456/UF.' };
  const numero = numeros[0].replace(/^0+/, '');
  if (!numero) return { ok: false, erro: 'Número de inscrição inválido.' };
  if (numero.length > 7) return { ok: false, erro: 'Número de inscrição com mais de 7 dígitos não existe na OAB.' };
  return { ok: true, canonica: `${numero}/${ufs[0]}` };
}

/** Estado da OAB gravada, como a listagem mostra: o worker só consulta a canônica. */
export function estadoOab(oab: string | null | undefined): 'ausente' | 'canonica' | 'invalida' {
  if (!oab) return 'ausente';
  const resultado = normalizarOab(oab);
  return resultado.ok && resultado.canonica === oab ? 'canonica' : 'invalida';
}

/** Rótulo do tribunal: o alias do DataJud em maiúsculas (`tjdft` → `TJDFT`,
 * `tre-dft` → `TRE-DFT`). Texto livre legado, que o worker ainda vai
 * recalcular, aparece como está, e não gritado. */
export function rotuloTribunal(tribunal: string | null | undefined): string {
  if (!tribunal) return '—';
  return /^[a-z0-9-]+$/.test(tribunal) ? tribunal.toUpperCase() : tribunal;
}
