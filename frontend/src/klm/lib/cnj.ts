/**
 * Número Único de Processo (CNJ, Resolução CNJ 65/2008 Anexo V) — máscara e
 * validação client-side. Espelha `app/services/cnj_numero.py` (dígito
 * verificador módulo 97 base 10 / ISO 7064 mod 97-10) só para feedback
 * imediato no formulário; a validação de verdade é sempre a do backend
 * (`POST /processos` devolve 422 `PROC-MON-002` se o dígito não confere).
 */

/** Remove tudo que não for dígito. */
export function onlyDigits(value: string): string {
  return value.replace(/\D/g, '');
}

/** Aplica a máscara NNNNNNN-DD.AAAA.J.TR.OOOO progressivamente, conforme o
 * usuário digita (funciona com string parcial). */
export function maskNumeroCnj(value: string): string {
  const digits = onlyDigits(value).slice(0, 20);
  let out = digits.slice(0, 7);
  if (digits.length > 7) out += `-${digits.slice(7, 9)}`;
  if (digits.length > 9) out += `.${digits.slice(9, 13)}`;
  if (digits.length > 13) out += `.${digits.slice(13, 14)}`;
  if (digits.length > 14) out += `.${digits.slice(14, 16)}`;
  if (digits.length > 16) out += `.${digits.slice(16, 20)}`;
  return out;
}

function digitoVerificador(sequencial: string, ano: string, justica: string, tribunal: string, origem: string): string {
  const numero = BigInt(`${sequencial}${ano}${justica}${tribunal}${origem}`);
  const resto = (numero * 100n) % 97n;
  return String(98n - resto).padStart(2, '0');
}

/** Valida formato completo (20 dígitos) + dígito verificador. Só retorna
 * `true` quando o número está completo e correto — números parciais (ainda
 * sendo digitados) retornam `false` sem serem tratados como erro pela UI. */
export function numeroCnjValido(value: string): boolean {
  const digits = onlyDigits(value);
  if (digits.length !== 20) return false;
  const sequencial = digits.slice(0, 7);
  const digitoInformado = digits.slice(7, 9);
  const ano = digits.slice(9, 13);
  const justica = digits.slice(13, 14);
  const tribunal = digits.slice(14, 16);
  const origem = digits.slice(16, 20);
  return digitoVerificador(sequencial, ano, justica, tribunal, origem) === digitoInformado;
}
