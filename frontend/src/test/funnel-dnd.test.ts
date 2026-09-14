import { describe, expect, it } from 'vitest';
import { reorderFunnelCards } from '@/klm/lib/funnel-dnd';

type Card = { id: string; stage: string; order: number; label: string };

const cards: Card[] = [
  { id: 'a', stage: 'novo', order: 0, label: 'A' },
  { id: 'b', stage: 'novo', order: 1, label: 'B' },
  { id: 'c', stage: 'triagem', order: 0, label: 'C' },
  { id: 'd', stage: 'triagem', order: 1, label: 'D' },
];

describe('KLM funnel drag and drop ordering', () => {
  it('moves a card between columns and normalizes the destination order', () => {
    const result = reorderFunnelCards(cards, 'b', {
      stage: 'triagem',
      index: 1,
      visibleCardIds: ['c', 'd'],
    });

    expect(result.find((card) => card.id === 'b')).toMatchObject({ stage: 'triagem', order: 1 });
    expect(result.filter((card) => card.stage === 'triagem').map((card) => [card.id, card.order])).toEqual([
      ['c', 0],
      ['b', 1],
      ['d', 2],
    ]);
  });

  it('reorders a card inside the same column', () => {
    const result = reorderFunnelCards(cards, 'b', {
      stage: 'novo',
      index: 0,
      visibleCardIds: ['a', 'b'],
    });

    expect(result.filter((card) => card.stage === 'novo').map((card) => [card.id, card.order])).toEqual([
      ['b', 0],
      ['a', 1],
    ]);
  });

  it('keeps filtered-out cards stable and inserts relative to visible anchors', () => {
    const filteredCards: Card[] = [
      { id: 'hidden', stage: 'triagem', order: 0, label: 'Oculto' },
      { id: 'visible', stage: 'triagem', order: 1, label: 'Visível' },
      { id: 'moving', stage: 'novo', order: 0, label: 'Movendo' },
    ];
    const result = reorderFunnelCards(filteredCards, 'moving', {
      stage: 'triagem',
      index: 0,
      visibleCardIds: ['visible'],
    });

    expect(result.filter((card) => card.stage === 'triagem').map((card) => card.id)).toEqual([
      'hidden',
      'moving',
      'visible',
    ]);
  });

  it('returns the original collection for an unknown card', () => {
    expect(reorderFunnelCards(cards, 'missing', { stage: 'novo', index: 0, visibleCardIds: [] })).toBe(cards);
  });
});
