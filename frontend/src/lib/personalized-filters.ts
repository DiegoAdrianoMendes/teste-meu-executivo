export type PersonalizedFilterFieldType = 'text' | 'number' | 'select' | 'boolean' | 'date';

export type PersonalizedFilterOperator =
  | 'eq'
  | 'neq'
  | 'contains'
  | 'not_contains'
  | 'gt'
  | 'gte'
  | 'lt'
  | 'lte'
  | 'is_empty'
  | 'is_not_empty';

export type PersonalizedFilterValue = string | number | boolean | null;

export interface PersonalizedFilterOption {
  label: string;
  value: string;
}

export interface PersonalizedFilterField<T = Record<string, unknown>> {
  key: string;
  label: string;
  type: PersonalizedFilterFieldType;
  options?: PersonalizedFilterOption[];
  getValue?: (record: T) => unknown;
}

export interface PersonalizedFilterCondition {
  id: string;
  field: string;
  operator: PersonalizedFilterOperator;
  value: PersonalizedFilterValue;
}

export interface PersonalizedFilterDefinition {
  match: 'all' | 'any';
  conditions: PersonalizedFilterCondition[];
}

export interface PersonalizedFilterView {
  id: string;
  name: string;
  definition: PersonalizedFilterDefinition;
  createdAt: string;
  updatedAt: string;
}

export const EMPTY_PERSONALIZED_FILTER: PersonalizedFilterDefinition = {
  match: 'all',
  conditions: [],
};

export const FILTER_OPERATOR_LABELS: Record<PersonalizedFilterOperator, string> = {
  eq: 'é igual a',
  neq: 'é diferente de',
  contains: 'contém',
  not_contains: 'não contém',
  gt: 'é maior que',
  gte: 'é maior ou igual a',
  lt: 'é menor que',
  lte: 'é menor ou igual a',
  is_empty: 'está vazio',
  is_not_empty: 'não está vazio',
};

export function operatorsForField(
  type: PersonalizedFilterFieldType,
): PersonalizedFilterOperator[] {
  if (type === 'number' || type === 'date') {
    return ['eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'is_empty', 'is_not_empty'];
  }
  if (type === 'select' || type === 'boolean') {
    return ['eq', 'neq', 'is_empty', 'is_not_empty'];
  }
  return ['contains', 'not_contains', 'eq', 'neq', 'is_empty', 'is_not_empty'];
}

export function createPersonalizedFilterCondition(
  field: PersonalizedFilterField,
): PersonalizedFilterCondition {
  return {
    id: globalThis.crypto?.randomUUID?.() ?? `filter-${Date.now()}-${Math.random()}`,
    field: field.key,
    operator: operatorsForField(field.type)[0],
    value: field.type === 'boolean' ? true : '',
  };
}

function nestedValue(record: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>((value, key) => {
    if (!value || typeof value !== 'object') return undefined;
    return (value as Record<string, unknown>)[key];
  }, record);
}

function isEmpty(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === 'string') return value.trim().length === 0;
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

function comparable(value: unknown, type: PersonalizedFilterFieldType): string | number | boolean {
  if (type === 'number') {
    const numeric = typeof value === 'number' ? value : Number(String(value).replace(',', '.'));
    return Number.isFinite(numeric) ? numeric : Number.NaN;
  }
  if (type === 'date') {
    const timestamp = value instanceof Date ? value.getTime() : new Date(String(value)).getTime();
    return Number.isFinite(timestamp) ? timestamp : Number.NaN;
  }
  if (type === 'boolean') {
    if (typeof value === 'boolean') return value;
    return String(value).toLocaleLowerCase('pt-BR') === 'true';
  }
  return String(value ?? '').trim().toLocaleLowerCase('pt-BR');
}

function conditionMatches<T>(
  record: T,
  condition: PersonalizedFilterCondition,
  fields: PersonalizedFilterField<T>[],
): boolean {
  const field = fields.find((candidate) => candidate.key === condition.field);
  if (!field) return false;
  const actual = field.getValue ? field.getValue(record) : nestedValue(record, field.key);
  if (condition.operator === 'is_empty') return isEmpty(actual);
  if (condition.operator === 'is_not_empty') return !isEmpty(actual);

  const expected = condition.value;
  if (condition.operator === 'contains' || condition.operator === 'not_contains') {
    const haystack = Array.isArray(actual) ? actual.join(' ') : String(actual ?? '');
    const matched = haystack
      .toLocaleLowerCase('pt-BR')
      .includes(String(expected ?? '').trim().toLocaleLowerCase('pt-BR'));
    return condition.operator === 'contains' ? matched : !matched;
  }

  const left = comparable(actual, field.type);
  const right = comparable(expected, field.type);
  if ((typeof left === 'number' && Number.isNaN(left)) || (typeof right === 'number' && Number.isNaN(right))) {
    return false;
  }
  if (condition.operator === 'eq') return left === right;
  if (condition.operator === 'neq') return left !== right;
  if (condition.operator === 'gt') return left > right;
  if (condition.operator === 'gte') return left >= right;
  if (condition.operator === 'lt') return left < right;
  if (condition.operator === 'lte') return left <= right;
  return false;
}

export function matchesPersonalizedFilter<T>(
  record: T,
  definition: PersonalizedFilterDefinition,
  fields: PersonalizedFilterField<T>[],
): boolean {
  if (!definition.conditions.length) return true;
  const matches = definition.conditions.map((condition) => conditionMatches(record, condition, fields));
  return definition.match === 'all' ? matches.every(Boolean) : matches.some(Boolean);
}

export function clonePersonalizedFilter(
  definition: PersonalizedFilterDefinition,
): PersonalizedFilterDefinition {
  return {
    match: definition.match,
    conditions: definition.conditions.map((condition) => ({ ...condition })),
  };
}

export function isPersonalizedFilterDefinition(
  value: unknown,
): value is PersonalizedFilterDefinition {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<PersonalizedFilterDefinition>;
  return (candidate.match === 'all' || candidate.match === 'any') && Array.isArray(candidate.conditions);
}
