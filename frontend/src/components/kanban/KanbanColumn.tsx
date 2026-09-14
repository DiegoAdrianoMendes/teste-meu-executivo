import { useState } from 'react';
import type { KanbanColumn as KanbanColumnType, MatterCard as MatterCardType } from '@/types';
import { MatterCard } from '@/components/cards/MatterCard';
import { AddMatterDialog } from '@/components/cards/AddMatterDialog';
import { Droppable, Draggable } from '@hello-pangea/dnd';
import { Button } from '@/components/ui/button';
import { Plus } from 'lucide-react';

interface Props {
  column: KanbanColumnType;
  cards: MatterCardType[];
  onCardCreated?: () => void;
}

export function KanbanColumn({ column, cards, onCardCreated }: Props) {
  const [addOpen, setAddOpen] = useState(false);

  return (
    <div className="flex flex-col min-w-[220px] sm:min-w-[260px] max-w-[280px] w-full shrink-0">
      {/* Column Header */}
      <div className="flex items-center gap-2 mb-2 px-1">
        <div className="h-2.5 w-2.5 rounded-full shrink-0" style={{ backgroundColor: column.color }} />
        <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground truncate flex-1">
          {column.title}
        </h3>
        <span className="text-xs text-muted-foreground font-medium bg-muted rounded-full px-1.5 py-0.5 min-w-[20px] text-center tabular-nums shrink-0">
          {cards.length}
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="h-5 w-5 text-muted-foreground hover:text-primary shrink-0"
          onClick={() => setAddOpen(true)}
        >
          <Plus className="h-3.5 w-3.5" />
        </Button>
      </div>

      {/* Droppable area */}
      <Droppable droppableId={column.id}>
        {(provided, snapshot) => (
          <div
            ref={provided.innerRef}
            {...provided.droppableProps}
            className={`flex-1 space-y-2 min-h-[120px] p-1.5 rounded-lg transition-colors ${
              snapshot.isDraggingOver ? 'bg-primary/5 ring-1 ring-primary/20' : 'bg-muted/30'
            }`}
          >
            {cards.map((card, index) => (
              <Draggable key={card.id} draggableId={card.id} index={index}>
                {(dragProvided, dragSnapshot) => (
                  <div
                    ref={dragProvided.innerRef}
                    {...dragProvided.draggableProps}
                    {...dragProvided.dragHandleProps}
                    className={`transition-shadow ${dragSnapshot.isDragging ? 'shadow-lg rotate-1' : ''}`}
                  >
                    <MatterCard card={card} />
                  </div>
                )}
              </Draggable>
            ))}
            {provided.placeholder}
            {cards.length === 0 && !snapshot.isDraggingOver && (
              <button
                className="flex items-center justify-center w-full h-20 text-xs text-muted-foreground/60 italic hover:text-primary/60 hover:bg-primary/5 rounded-md transition-colors"
                onClick={() => setAddOpen(true)}
              >
                + Adicionar caso
              </button>
            )}
          </div>
        )}
      </Droppable>

      <AddMatterDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        defaultColumnId={column.id as import('@/types').KanbanColumnId}
        onCreated={() => {
          setAddOpen(false);
          onCardCreated?.();
        }}
      />
    </div>
  );
}
