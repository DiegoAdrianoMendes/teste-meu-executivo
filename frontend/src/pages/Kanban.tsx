import { useMemo, useState } from 'react';
import { SectionHeader } from '@/components/common/SectionHeader';
import { AdvancedFilterBar } from '@/components/common/AdvancedFilterBar';
import { SavedFilterPresets } from '@/components/common/SavedFilterPresets';
import { KanbanColumn } from '@/components/kanban/KanbanColumn';
import { AddMatterDialog } from '@/components/cards/AddMatterDialog';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { useAppStore } from '@/store/app-context';
import { DragDropContext, type DropResult } from '@hello-pangea/dnd';
import type { KanbanColumnId } from '@/types';
import { Plus } from 'lucide-react';
import { PersonalizedFilterBuilder } from '@/components/common/PersonalizedFilterBuilder';
import { EMPTY_PERSONALIZED_FILTER, matchesPersonalizedFilter, type PersonalizedFilterDefinition } from '@/lib/personalized-filters';
import { matterCardPersonalizedFields } from '@/lib/matter-card-filters';

export default function Kanban() {
  const { state, getFilteredCards, moveCard, refreshCards } = useAppStore();
  const standardFilteredCards = getFilteredCards();
  const [personalizedFilter, setPersonalizedFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);
  const personalizedFields = useMemo(() => matterCardPersonalizedFields(state.cards), [state.cards]);
  const filteredCards = useMemo(
    () => standardFilteredCards.filter((card) => matchesPersonalizedFilter(card, personalizedFilter, personalizedFields)),
    [personalizedFields, personalizedFilter, standardFilteredCards],
  );
  const [addOpen, setAddOpen] = useState(false);
  const unreadCount = filteredCards.filter((card) => card.unreadMessages > 0).length;
  const blockedCount = filteredCards.filter((card) => card.isBlocked).length;
  const financialPendingCount = filteredCards.filter((card) => card.microStatus === 'pagamento_pendente').length;
  const warmLeadsCount = filteredCards.filter((card) => card.columnId === 'proposta_fechamento' || card.columnId === 'coleta_dados').length;

  const handleDragEnd = (result: DropResult) => {
    if (!result.destination) return;
    const { draggableId, destination } = result;
    const newColumnId = destination.droppableId as KanbanColumnId;
    moveCard(draggableId, newColumnId);
  };

  return (
    <div className="space-y-3 h-full flex flex-col">
      <SectionHeader
        title="Kanban"
        description="Fluxo operacional de casos por fase"
        action={
          <Button size="sm" className="h-8 text-xs bg-brand-gradient text-white hover:opacity-90" onClick={() => setAddOpen(true)}>
            <Plus className="h-3.5 w-3.5 mr-1.5" />
            Novo caso
          </Button>
        }
      />

      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1"><SavedFilterPresets /></div>
        <PersonalizedFilterBuilder scope="kanban" fields={personalizedFields} value={personalizedFilter} onChange={setPersonalizedFilter} />
      </div>
      <AdvancedFilterBar />

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {[
          { label: 'Cards visíveis', value: filteredCards.length },
          { label: 'Mensagens não lidas', value: unreadCount },
          { label: 'Financeiro pendente', value: financialPendingCount },
          { label: 'Quentes p/ avanço', value: Math.max(warmLeadsCount - blockedCount, 0) },
        ].map((item) => (
          <Card key={item.label}>
            <CardContent className="py-4">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">{item.label}</p>
              <p className="mt-1 text-xl font-semibold">{item.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <DragDropContext onDragEnd={handleDragEnd}>
        <div className="flex-1 overflow-x-auto scrollbar-thin -mx-4 lg:-mx-6 px-4 lg:px-6">
          <div className="flex gap-3 pb-4 min-h-[400px] lg:min-h-[500px] px-1">
            {state.columns.map(col => {
              const colCards = filteredCards.filter(c => c.columnId === col.id);
              return (
                <KanbanColumn
                  key={col.id}
                  column={col}
                  cards={colCards}
                  onCardCreated={() => refreshCards?.()}
                />
              );
            })}
          </div>
        </div>
      </DragDropContext>

      <AddMatterDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        defaultColumnId="entrada"
        onCreated={() => { setAddOpen(false); refreshCards?.(); }}
      />
    </div>
  );
}
