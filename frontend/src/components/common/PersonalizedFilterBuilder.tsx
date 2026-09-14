import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookmarkPlus, FilterX, Plus, Save, SlidersHorizontal, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { cn } from '@/lib/utils';
import {
  clonePersonalizedFilter,
  createPersonalizedFilterCondition,
  EMPTY_PERSONALIZED_FILTER,
  FILTER_OPERATOR_LABELS,
  isPersonalizedFilterDefinition,
  operatorsForField,
  type PersonalizedFilterCondition,
  type PersonalizedFilterDefinition,
  type PersonalizedFilterField,
  type PersonalizedFilterOperator,
  type PersonalizedFilterView,
} from '@/lib/personalized-filters';
import { fetchMyConfiguration, saveMyConfiguration, type ConfigurationDocument } from '@/services/configurations';

const CONFIG_NAMESPACE = 'ui.filters';

interface PersonalizedFilterBuilderProps<T> {
  scope: string;
  fields: PersonalizedFilterField<T>[];
  value: PersonalizedFilterDefinition;
  onChange: (definition: PersonalizedFilterDefinition) => void;
  title?: string;
  description?: string;
  triggerLabel?: string;
  disabled?: boolean;
  className?: string;
  compact?: boolean;
}

function savedViewsFromDocument(
  document: ConfigurationDocument | null | undefined,
  scope: string,
): PersonalizedFilterView[] {
  const allViews = document?.payload?.views;
  if (!allViews || typeof allViews !== 'object' || Array.isArray(allViews)) return [];
  const candidate = (allViews as Record<string, unknown>)[scope];
  if (!Array.isArray(candidate)) return [];
  return candidate.filter((item): item is PersonalizedFilterView => {
    if (!item || typeof item !== 'object') return false;
    const view = item as Partial<PersonalizedFilterView>;
    return typeof view.id === 'string'
      && typeof view.name === 'string'
      && isPersonalizedFilterDefinition(view.definition);
  });
}

function payloadWithViews(
  document: ConfigurationDocument | null,
  scope: string,
  views: PersonalizedFilterView[],
): Record<string, unknown> {
  const currentPayload = document?.payload ?? {};
  const currentViews = currentPayload.views && typeof currentPayload.views === 'object' && !Array.isArray(currentPayload.views)
    ? currentPayload.views as Record<string, unknown>
    : {};
  return {
    ...currentPayload,
    schemaVersion: 1,
    views: { ...currentViews, [scope]: views },
  };
}

function conditionValue(
  condition: PersonalizedFilterCondition,
  fields: PersonalizedFilterField<unknown>[],
  onChange: (value: string | number | boolean | null) => void,
) {
  const field = fields.find((candidate) => candidate.key === condition.field);
  if (!field || condition.operator === 'is_empty' || condition.operator === 'is_not_empty') return null;
  if (field.type === 'select') {
    return (
      <Select value={String(condition.value ?? '')} onValueChange={onChange}>
        <SelectTrigger className="h-9 min-w-36 text-xs"><SelectValue placeholder="Selecione" /></SelectTrigger>
        <SelectContent>
          {(field.options ?? []).map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
        </SelectContent>
      </Select>
    );
  }
  if (field.type === 'boolean') {
    return (
      <Select value={String(condition.value ?? true)} onValueChange={(value) => onChange(value === 'true')}>
        <SelectTrigger className="h-9 min-w-28 text-xs"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value="true">Sim</SelectItem><SelectItem value="false">Não</SelectItem></SelectContent>
      </Select>
    );
  }
  return (
    <Input
      type={field.type === 'number' ? 'number' : field.type === 'date' ? 'date' : 'text'}
      value={String(condition.value ?? '')}
      onChange={(event) => onChange(field.type === 'number' ? Number(event.target.value) : event.target.value)}
      className="h-9 min-w-36 text-xs"
      placeholder={field.type === 'number' ? '0' : 'Valor'}
    />
  );
}

export function PersonalizedFilterBuilder<T>({
  scope,
  fields,
  value,
  onChange,
  title = 'Personalizar filtros',
  description = 'Combine campos para criar uma visão precisa e salve-a para reutilizar.',
  triggerLabel = 'Personalizar',
  disabled = false,
  className,
  compact = false,
}: PersonalizedFilterBuilderProps<T>) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<PersonalizedFilterDefinition>(() => clonePersonalizedFilter(value));
  const [viewName, setViewName] = useState('');
  const [saveError, setSaveError] = useState<string | null>(null);
  const configurationQuery = useQuery({
    queryKey: ['configuration', CONFIG_NAMESPACE, 'me'],
    queryFn: () => fetchMyConfiguration(CONFIG_NAMESPACE),
    staleTime: 60_000,
  });
  const views = useMemo(
    () => savedViewsFromDocument(configurationQuery.data, scope),
    [configurationQuery.data, scope],
  );

  useEffect(() => {
    if (open) setDraft(clonePersonalizedFilter(value));
  }, [open, value]);

  const persistViews = useMutation({
    mutationFn: async (nextViews: PersonalizedFilterView[]) => {
      const latest = await queryClient.fetchQuery({
        queryKey: ['configuration', CONFIG_NAMESPACE, 'me'],
        queryFn: () => fetchMyConfiguration(CONFIG_NAMESPACE),
        staleTime: 0,
      });
      return saveMyConfiguration(
        CONFIG_NAMESPACE,
        payloadWithViews(latest, scope, nextViews),
        { etag: latest?.etag, reason: `Filtros personalizados atualizados em ${scope}` },
      );
    },
    onSuccess: (document) => {
      queryClient.setQueryData(['configuration', CONFIG_NAMESPACE, 'me'], document);
      setSaveError(null);
    },
    onError: () => {
      setSaveError('Não foi possível salvar a visualização. Recarregue e tente novamente.');
      void queryClient.invalidateQueries({ queryKey: ['configuration', CONFIG_NAMESPACE, 'me'] });
    },
  });

  const setCondition = (id: string, patch: Partial<PersonalizedFilterCondition>) => {
    setDraft((current) => ({
      ...current,
      conditions: current.conditions.map((condition) => condition.id === id ? { ...condition, ...patch } : condition),
    }));
  };

  const chooseField = (condition: PersonalizedFilterCondition, fieldKey: string) => {
    const field = fields.find((candidate) => candidate.key === fieldKey);
    if (!field) return;
    setCondition(condition.id, {
      field: fieldKey,
      operator: operatorsForField(field.type)[0],
      value: field.type === 'boolean' ? true : '',
    });
  };

  const addCondition = () => {
    if (!fields.length) return;
    setDraft((current) => ({
      ...current,
      conditions: [...current.conditions, createPersonalizedFilterCondition(fields[0] as PersonalizedFilterField)],
    }));
  };

  const saveView = () => {
    const name = viewName.trim();
    if (!name || !draft.conditions.length) return;
    const now = new Date().toISOString();
    const id = globalThis.crypto?.randomUUID?.() ?? `view-${Date.now()}`;
    persistViews.mutate([
      ...views,
      { id, name, definition: clonePersonalizedFilter(draft), createdAt: now, updatedAt: now },
    ]);
    setViewName('');
  };

  const apply = (definition = draft) => {
    onChange(clonePersonalizedFilter(definition));
    setOpen(false);
  };

  const activeCount = value.conditions.length;
  const genericFields = fields as PersonalizedFilterField<unknown>[];

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          type="button"
          variant={activeCount ? 'default' : 'outline'}
          size="sm"
          disabled={disabled}
          className={cn('h-8 shrink-0 gap-1.5 text-xs', compact && 'w-8 px-0', className)}
          aria-label={activeCount ? `${triggerLabel}, ${activeCount} ativos` : triggerLabel}
          title={activeCount ? `${triggerLabel}: ${activeCount} ativos` : triggerLabel}
        >
          <SlidersHorizontal className="h-3.5 w-3.5" />
          {!compact && <span>{triggerLabel}{activeCount ? ` (${activeCount})` : ''}</span>}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[88vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        {views.length > 0 && (
          <section aria-label="Visualizações salvas" className="border-b border-border pb-4">
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold"><BookmarkPlus className="h-4 w-4 text-primary" />Visualizações salvas</div>
            <div className="flex flex-wrap gap-2">
              {views.map((view) => (
                <div key={view.id} className="flex items-center rounded-md border border-border bg-secondary">
                  <button type="button" className="px-3 py-2 text-left text-xs font-medium hover:text-primary" onClick={() => apply(view.definition)}>{view.name}</button>
                  <Button type="button" variant="ghost" size="icon" className="h-8 w-8" aria-label={`Excluir ${view.name}`} onClick={() => persistViews.mutate(views.filter((item) => item.id !== view.id))}><Trash2 className="h-3.5 w-3.5" /></Button>
                </div>
              ))}
            </div>
          </section>
        )}

        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div className="space-y-1.5">
            <Label htmlFor={`${scope}-match`}>Aplicar quando</Label>
            <Select value={draft.match} onValueChange={(match: 'all' | 'any') => setDraft((current) => ({ ...current, match }))}>
              <SelectTrigger id={`${scope}-match`} className="h-9 w-56 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value="all">Todas as condições</SelectItem><SelectItem value="any">Qualquer condição</SelectItem></SelectContent>
            </Select>
          </div>
          <Button type="button" variant="outline" size="sm" className="h-9 gap-1.5" onClick={addCondition} disabled={!fields.length}><Plus className="h-4 w-4" />Adicionar condição</Button>
        </div>

        <div className="space-y-2">
          {draft.conditions.length === 0 && (
            <div className="rounded-md border border-dashed border-border px-4 py-8 text-center text-xs text-muted-foreground">Nenhuma condição. Adicione um campo ou escolha uma visualização salva.</div>
          )}
          {draft.conditions.map((condition) => {
            const field = fields.find((candidate) => candidate.key === condition.field) ?? fields[0];
            const operators = field ? operatorsForField(field.type) : [];
            return (
              <div key={condition.id} className="grid gap-2 rounded-md border border-border bg-secondary/45 p-2 sm:grid-cols-[1fr_1fr_1.2fr_auto] sm:items-center">
                <Select value={condition.field} onValueChange={(fieldKey) => chooseField(condition, fieldKey)}>
                  <SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>{fields.map((candidate) => <SelectItem key={candidate.key} value={candidate.key}>{candidate.label}</SelectItem>)}</SelectContent>
                </Select>
                <Select value={condition.operator} onValueChange={(operator: PersonalizedFilterOperator) => setCondition(condition.id, { operator })}>
                  <SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>{operators.map((operator) => <SelectItem key={operator} value={operator}>{FILTER_OPERATOR_LABELS[operator]}</SelectItem>)}</SelectContent>
                </Select>
                <div>{conditionValue(condition, genericFields, (nextValue) => setCondition(condition.id, { value: nextValue }))}</div>
                <Button type="button" variant="ghost" size="icon" className="h-9 w-9" aria-label="Remover condição" onClick={() => setDraft((current) => ({ ...current, conditions: current.conditions.filter((item) => item.id !== condition.id) }))}><Trash2 className="h-4 w-4" /></Button>
              </div>
            );
          })}
        </div>

        <div className="border-t border-border pt-4">
          <Label htmlFor={`${scope}-view-name`}>Salvar esta combinação</Label>
          <div className="mt-2 flex flex-col gap-2 sm:flex-row">
            <Input id={`${scope}-view-name`} value={viewName} onChange={(event) => setViewName(event.target.value)} className="h-9 text-xs" maxLength={80} placeholder="Ex.: Urgentes sem resposta" />
            <Button type="button" variant="outline" size="sm" className="h-9 gap-1.5" disabled={!viewName.trim() || !draft.conditions.length || persistViews.isPending} onClick={saveView}><Save className="h-4 w-4" />Salvar visualização</Button>
          </div>
          {saveError && <p className="mt-2 text-xs text-destructive">{saveError}</p>}
        </div>

        <DialogFooter className="gap-2 sm:justify-between">
          <Button type="button" variant="ghost" className="gap-1.5" onClick={() => { setDraft(EMPTY_PERSONALIZED_FILTER); onChange(EMPTY_PERSONALIZED_FILTER); setOpen(false); }}><FilterX className="h-4 w-4" />Limpar</Button>
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>Cancelar</Button>
            <Button type="button" onClick={() => apply()}>Aplicar filtros</Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
