import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Check, LoaderCircle, LockKeyhole, PencilLine, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuTrigger,
} from '@/components/ui/context-menu';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

export type InlineChoiceOption = { value: string; label: string };

type InlineEditorShellProps = {
  label: string;
  editing: boolean;
  disabled?: boolean;
  saving?: boolean;
  display: ReactNode;
  onEdit: () => void;
  onSave: () => void;
  onCancel: () => void;
  children: ReactNode;
};

function InlineEditorShell({
  label,
  editing,
  disabled = false,
  saving = false,
  display,
  onEdit,
  onSave,
  onCancel,
  children,
}: InlineEditorShellProps) {
  if (!editing) {
    return (
      <ContextMenu>
        <ContextMenuTrigger asChild>
          <div
            data-read-only={disabled ? 'true' : undefined}
            data-inline-editable={!disabled ? 'true' : undefined}
            onDoubleClick={(event) => {
              if (disabled) return;
              event.preventDefault();
              onEdit();
            }}
            className={cn(
              'group inline-flex min-h-6 w-full min-w-0 items-start gap-1.5 rounded px-1 py-0.5 text-left font-medium transition-colors',
              disabled ? 'cursor-not-allowed opacity-80' : 'cursor-text hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary',
            )}
            title={disabled ? `Somente leitura: sem permissão para editar ${label.toLocaleLowerCase('pt-BR')}` : `Clique duas vezes para editar ${label.toLocaleLowerCase('pt-BR')}`}
          >
            <button
              type="button"
              disabled={disabled}
              data-read-only={disabled ? 'true' : undefined}
              data-inline-editable={!disabled ? 'true' : undefined}
              className="min-w-0 flex-1 cursor-inherit break-words text-left focus-visible:outline-none"
              onKeyDown={(event) => {
                if (!disabled && (event.key === 'Enter' || event.key === 'F2')) {
                  event.preventDefault();
                  onEdit();
                }
              }}
              aria-label={disabled ? label : `${label}. Clique duas vezes para editar`}
              title={disabled ? `Somente leitura: sem permissão para editar ${label.toLocaleLowerCase('pt-BR')}` : `Clique duas vezes para editar ${label.toLocaleLowerCase('pt-BR')}`}
            >
              {display || '—'}
            </button>
            {disabled
              ? <LockKeyhole className="mt-0.5 h-3 w-3 shrink-0 opacity-55" aria-hidden="true" />
              : (
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onEdit();
                  }}
                  className="mt-0.5 shrink-0 rounded opacity-0 transition-opacity hover:text-primary focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary group-hover:opacity-70"
                  aria-label={`Editar ${label}`}
                  title={`Editar ${label}`}
                >
                  <PencilLine className="h-3 w-3" aria-hidden="true" />
                </button>
              )}
          </div>
        </ContextMenuTrigger>
        <ContextMenuContent className="w-48">
          <ContextMenuLabel className="truncate text-2xs">{label}</ContextMenuLabel>
          <ContextMenuItem className="text-xs" disabled={disabled} onSelect={onEdit}>
            <PencilLine className="mr-2 h-3.5 w-3.5" />
            Editar informação
          </ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>
    );
  }

  return (
    <div className="min-w-0 space-y-1.5" data-inline-editor={label} aria-busy={saving}>
      {children}
      <div className="flex items-center gap-1">
        <Button type="button" size="icon" className="h-6 w-6" onClick={onSave} disabled={saving} aria-label={`Salvar ${label}`} title={`Salvar ${label}`}>
          {saving ? <LoaderCircle className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
        </Button>
        <Button type="button" variant="ghost" size="icon" className="h-6 w-6" onClick={onCancel} disabled={saving} aria-label={`Cancelar edição de ${label}`} title="Cancelar">
          <X className="h-3 w-3" />
        </Button>
        {saving && <span role="status" className="sr-only">Salvando {label}</span>}
      </div>
    </div>
  );
}

export function InlineTextField({
  label,
  value,
  display,
  disabled = false,
  saving = false,
  multiline = false,
  type = 'text',
  placeholder,
  nullable = true,
  validate,
  onSave,
}: {
  label: string;
  value: string | number | null | undefined;
  display?: ReactNode;
  disabled?: boolean;
  saving?: boolean;
  multiline?: boolean;
  type?: 'text' | 'email' | 'tel' | 'number' | 'datetime-local';
  placeholder?: string;
  nullable?: boolean;
  validate?: (value: string) => string | null;
  onSave: (value: string | null) => Promise<void>;
}) {
  const normalizedValue = value == null ? '' : String(value);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(normalizedValue);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!editing) setDraft(normalizedValue);
  }, [editing, normalizedValue]);

  const cancel = () => {
    setDraft(normalizedValue);
    setError(null);
    setEditing(false);
  };
  const commit = async () => {
    if (saving) return;
    const next = draft.trim();
    const validationError = validate?.(next) ?? (!nullable && !next ? `${label} é obrigatório.` : null);
    if (validationError) {
      setError(validationError);
      return;
    }
    if (next === normalizedValue.trim()) {
      cancel();
      return;
    }
    try {
      await onSave(next || null);
      setError(null);
      setEditing(false);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar este dado.');
    }
  };
  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      cancel();
    }
    if (event.key === 'Enter' && (!multiline || event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      void commit();
    }
  };
  const common = {
    value: draft,
    onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      setDraft(event.target.value);
      setError(null);
    },
    onKeyDown: handleKeyDown,
    autoFocus: true,
    disabled: saving,
    placeholder,
    'aria-label': label,
    'aria-invalid': Boolean(error),
  };

  return (
    <InlineEditorShell
      label={label}
      editing={editing}
      disabled={disabled}
      saving={saving}
      display={display ?? (normalizedValue || '—')}
      onEdit={() => setEditing(true)}
      onSave={() => void commit()}
      onCancel={cancel}
    >
      {multiline
        ? <Textarea {...common} rows={4} className="min-h-20 resize-y text-2xs leading-4" />
        : <Input {...common} type={type} className="h-8 min-w-0 px-2 text-2xs" />}
      {error && <p className="text-2xs text-destructive">{error}</p>}
    </InlineEditorShell>
  );
}

export function InlineChoiceField({
  label,
  value,
  customValue,
  options,
  display,
  disabled = false,
  saving = false,
  allowCustom = true,
  onSave,
}: {
  label: string;
  value: string | null | undefined;
  customValue?: string | null;
  options: InlineChoiceOption[];
  display?: ReactNode;
  disabled?: boolean;
  saving?: boolean;
  allowCustom?: boolean;
  onSave: (value: string, customValue: string | null) => Promise<void>;
}) {
  const currentMode = options.some((option) => option.value === value) && !customValue ? value! : '__custom__';
  const currentCustom = customValue ?? (currentMode === '__custom__' ? value ?? '' : '');
  const [editing, setEditing] = useState(false);
  const [mode, setMode] = useState(currentMode);
  const [custom, setCustom] = useState(currentCustom);
  const [error, setError] = useState<string | null>(null);
  const optionLabel = options.find((option) => option.value === value)?.label;

  useEffect(() => {
    if (!editing) {
      setMode(currentMode);
      setCustom(currentCustom);
    }
  }, [currentCustom, currentMode, editing]);

  const cancel = () => {
    setMode(currentMode);
    setCustom(currentCustom);
    setError(null);
    setEditing(false);
  };
  const commit = async () => {
    if (saving) return;
    const nextCustom = mode === '__custom__' ? custom.trim() : null;
    if (mode === '__custom__' && !nextCustom) {
      setError(`Informe ${label.toLocaleLowerCase('pt-BR')}.`);
      return;
    }
    if (mode === currentMode && nextCustom === (currentMode === '__custom__' ? currentCustom.trim() : null)) {
      cancel();
      return;
    }
    try {
      await onSave(mode === '__custom__' ? '__custom__' : mode, nextCustom);
      setError(null);
      setEditing(false);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar este dado.');
    }
  };
  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement | HTMLSelectElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      cancel();
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      void commit();
    }
  };

  return (
    <InlineEditorShell
      label={label}
      editing={editing}
      disabled={disabled}
      saving={saving}
      display={display ?? customValue ?? optionLabel ?? value ?? '—'}
      onEdit={() => setEditing(true)}
      onSave={() => void commit()}
      onCancel={cancel}
    >
      <select
        value={mode}
        onChange={(event) => { setMode(event.target.value); setError(null); }}
        onKeyDown={onKeyDown}
        autoFocus
        disabled={saving}
        aria-label={label}
        className="h-8 w-full min-w-0 border border-input bg-background px-2 text-2xs outline-none focus:ring-1 focus:ring-primary"
      >
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        {allowCustom && <option value="__custom__">Personalizado</option>}
      </select>
      {mode === '__custom__' && (
        <Input value={custom} onChange={(event) => { setCustom(event.target.value); setError(null); }} onKeyDown={onKeyDown} autoFocus disabled={saving} className="h-8 px-2 text-2xs" placeholder={`Informe ${label.toLocaleLowerCase('pt-BR')}`} />
      )}
      {error && <p className="text-2xs text-destructive">{error}</p>}
    </InlineEditorShell>
  );
}

export function InlineCompoundChoiceField({
  label,
  firstValue,
  secondValue,
  firstOptions,
  secondOptions,
  display,
  disabled = false,
  saving = false,
  onSave,
}: {
  label: string;
  firstValue: string;
  secondValue: string;
  firstOptions: InlineChoiceOption[];
  secondOptions: InlineChoiceOption[];
  display: ReactNode;
  disabled?: boolean;
  saving?: boolean;
  onSave: (first: string, second: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [first, setFirst] = useState(firstValue);
  const [second, setSecond] = useState(secondValue);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!editing) {
      setFirst(firstValue);
      setSecond(secondValue);
    }
  }, [editing, firstValue, secondValue]);
  const cancel = () => {
    setFirst(firstValue);
    setSecond(secondValue);
    setError(null);
    setEditing(false);
  };
  const commit = async () => {
    if (saving) return;
    if (first === firstValue && second === secondValue) {
      cancel();
      return;
    }
    try {
      await onSave(first, second);
      setError(null);
      setEditing(false);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar este dado.');
    }
  };
  const onKeyDown = (event: React.KeyboardEvent<HTMLSelectElement>) => {
    if (event.key === 'Escape') cancel();
    if (event.key === 'Enter') void commit();
  };
  const selectClass = 'h-8 w-full min-w-0 border border-input bg-background px-2 text-2xs outline-none focus:ring-1 focus:ring-primary';

  return (
    <InlineEditorShell label={label} editing={editing} disabled={disabled} saving={saving} display={display} onEdit={() => setEditing(true)} onSave={() => void commit()} onCancel={cancel}>
      <div className="grid gap-1.5 sm:grid-cols-2">
        <select value={first} onChange={(event) => { setFirst(event.target.value); setError(null); }} onKeyDown={onKeyDown} autoFocus disabled={saving} className={selectClass} aria-label={`${label}: primeira opção`}>
          {firstOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        <select value={second} onChange={(event) => { setSecond(event.target.value); setError(null); }} onKeyDown={onKeyDown} disabled={saving} className={selectClass} aria-label={`${label}: segunda opção`}>
          {secondOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </div>
      {error && <p className="text-2xs text-destructive">{error}</p>}
    </InlineEditorShell>
  );
}

export function InlineTagsField({
  label,
  tags,
  disabled = false,
  saving = false,
  onSave,
}: {
  label: string;
  tags: string[];
  disabled?: boolean;
  saving?: boolean;
  onSave: (tags: string[]) => Promise<void>;
}) {
  const value = tags.join(', ');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (!editing) setDraft(value); }, [editing, value]);
  const parsed = useMemo(() => {
    const seen = new Set<string>();
    return draft.split(',').map((tag) => tag.trim()).filter((tag) => {
      const key = tag.toLocaleLowerCase('pt-BR');
      if (!tag || seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, 30);
  }, [draft]);
  const cancel = () => { setDraft(value); setError(null); setEditing(false); };
  const commit = async () => {
    if (saving) return;
    if (parsed.join('|') === tags.join('|')) {
      cancel();
      return;
    }
    try {
      await onSave(parsed);
      setError(null);
      setEditing(false);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Não foi possível salvar este dado.');
    }
  };
  const display = tags.length ? (
    <span className="flex flex-wrap gap-1">{tags.map((tag) => <span key={tag} className="rounded-full border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-2xs">{tag}</span>)}</span>
  ) : '—';

  return (
    <InlineEditorShell label={label} editing={editing} disabled={disabled} saving={saving} display={display} onEdit={() => setEditing(true)} onSave={() => void commit()} onCancel={cancel}>
      <Input
        value={draft}
        onChange={(event) => { setDraft(event.target.value); setError(null); }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') cancel();
          if (event.key === 'Enter') { event.preventDefault(); void commit(); }
        }}
        autoFocus
        disabled={saving}
        className="h-8 px-2 text-2xs"
        placeholder="Separe as tags por vírgulas"
        aria-label={label}
      />
      <p className="text-2xs text-muted-foreground">{parsed.length}/30 tags · valores repetidos serão unidos</p>
      {error && <p className="text-2xs text-destructive">{error}</p>}
    </InlineEditorShell>
  );
}
