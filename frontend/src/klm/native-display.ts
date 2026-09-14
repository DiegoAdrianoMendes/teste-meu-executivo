import type { KlmNativeDomain } from '@/klm/native-domains';

export const nativeDomainLabels: Record<KlmNativeDomain, string> = {
  communications: 'Comunicações',
  collaboration: 'Colaboração',
  automation: 'Automação',
  crm: 'CRM jurídico',
};

const NATIVE_EXACT_LABELS: Record<string, string> = {
  account: 'Conta',
  accounts: 'Contas',
  contact: 'Contato',
  contacts: 'Contatos',
  lead: 'Lead',
  leads: 'Leads',
  opportunity: 'Oportunidade',
  opportunities: 'Oportunidades',
};

export function nativeDisplayText(value: string | null | undefined): string {
  const normalized = String(value ?? '').trim();
  return NATIVE_EXACT_LABELS[normalized.toLocaleLowerCase('en-US')] ?? normalized;
}

export function nativeCapabilityLabel(value: string | null | undefined): string {
  const normalized = String(value ?? '');
  const separator = normalized.indexOf('.');
  return nativeDisplayText(separator >= 0 ? normalized.slice(separator + 1) : normalized);
}

export function nativeFunctionReference(value: string): string {
  return value;
}
