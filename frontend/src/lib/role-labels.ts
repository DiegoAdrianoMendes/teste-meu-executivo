const ROLE_LABELS: Record<string, string> = {
  workspace_owner: 'Titular do escritório',
  workspace_admin: 'Administrador do escritório',
  legal_manager: 'Gestor jurídico',
  lawyer: 'Advogado',
  paralegal: 'Assistente jurídico',
  legal_assistant: 'Assistente jurídico',
  workspace_member: 'Membro do escritório',
  operator: 'Operador',
  observer: 'Observador',
  platform_owner: 'Titular da plataforma',
  platform_admin: 'Administrador da plataforma',
  internal_staff: 'Equipe Meu Executivo',
};

export function roleLabel(role?: string | null) {
  if (!role) return 'Membro do escritório';
  return ROLE_LABELS[role] ?? 'Função personalizada';
}

export function roleListLabel(roles?: string[] | null) {
  return roles?.length ? roles.map(roleLabel).join(', ') : roleLabel(null);
}
