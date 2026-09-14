import { describe, expect, it } from 'vitest';
import {
  matchesPersonalizedFilter,
  type PersonalizedFilterDefinition,
  type PersonalizedFilterField,
} from '@/lib/personalized-filters';

type RecordUnderTest = {
  name: string;
  score: number;
  urgent: boolean;
  status: string;
  createdAt: string;
};

const fields: PersonalizedFilterField<RecordUnderTest>[] = [
  { key: 'name', label: 'Nome', type: 'text' },
  { key: 'score', label: 'Score', type: 'number' },
  { key: 'urgent', label: 'Urgente', type: 'boolean' },
  { key: 'status', label: 'Status', type: 'select' },
  { key: 'createdAt', label: 'Criado em', type: 'date' },
];

const record: RecordUnderTest = {
  name: 'Marina Oliveira',
  score: 94,
  urgent: true,
  status: 'open',
  createdAt: '2026-07-20T10:00:00-03:00',
};

describe('personalized filters', () => {
  it('combines text, numeric and boolean conditions with all semantics', () => {
    const definition: PersonalizedFilterDefinition = {
      match: 'all',
      conditions: [
        { id: '1', field: 'name', operator: 'contains', value: 'marina' },
        { id: '2', field: 'score', operator: 'gte', value: 90 },
        { id: '3', field: 'urgent', operator: 'eq', value: true },
      ],
    };

    expect(matchesPersonalizedFilter(record, definition, fields)).toBe(true);
    expect(matchesPersonalizedFilter({ ...record, score: 89 }, definition, fields)).toBe(false);
  });

  it('supports any semantics, empty checks and dates', () => {
    const any: PersonalizedFilterDefinition = {
      match: 'any',
      conditions: [
        { id: '1', field: 'status', operator: 'eq', value: 'closed' },
        { id: '2', field: 'createdAt', operator: 'gte', value: '2026-07-20' },
      ],
    };
    const empty: PersonalizedFilterDefinition = {
      match: 'all',
      conditions: [{ id: '3', field: 'name', operator: 'is_empty', value: null }],
    };

    expect(matchesPersonalizedFilter(record, any, fields)).toBe(true);
    expect(matchesPersonalizedFilter({ ...record, name: '' }, empty, fields)).toBe(true);
  });
});
