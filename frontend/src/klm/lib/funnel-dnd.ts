export type OrderedFunnelCard = {
  id: string;
  stage: string;
  order: number;
};

export type FunnelDropTarget = {
  stage: string;
  index: number;
  visibleCardIds: string[];
};

/**
 * Reorders the full card collection while respecting the subset currently visible
 * after search/source filters. This keeps hidden cards stable during a filtered drag.
 */
export function reorderFunnelCards<T extends OrderedFunnelCard>(
  cards: T[],
  draggedId: string,
  target: FunnelDropTarget,
): T[] {
  const dragged = cards.find((card) => card.id === draggedId);
  if (!dragged) return cards;

  const withoutDragged = cards.filter((card) => card.id !== draggedId);
  const targetCards = withoutDragged.filter((card) => card.stage === target.stage);
  const visibleTargets = target.visibleCardIds.filter((id) => id !== draggedId);
  const anchorId = visibleTargets[target.index];

  let insertionIndex = targetCards.length;
  if (anchorId) {
    const anchorIndex = targetCards.findIndex((card) => card.id === anchorId);
    if (anchorIndex >= 0) insertionIndex = anchorIndex;
  } else if (visibleTargets.length) {
    const lastVisibleIndex = targetCards.findIndex(
      (card) => card.id === visibleTargets[visibleTargets.length - 1],
    );
    if (lastVisibleIndex >= 0) insertionIndex = lastVisibleIndex + 1;
  }

  const reorderedTarget = [...targetCards];
  reorderedTarget.splice(insertionIndex, 0, { ...dragged, stage: target.stage });
  const stageOrder = Array.from(new Set([...cards.map((card) => card.stage), target.stage]));
  return stageOrder.flatMap((stage) => {
    const stageCards = stage === target.stage
      ? reorderedTarget
      : withoutDragged.filter((card) => card.stage === stage);
    return stageCards.map((card, index) => ({ ...card, order: index }));
  });
}
