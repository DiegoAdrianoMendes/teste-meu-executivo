/**
 * Painel de configuração de nó dirigido por schema (registro v2).
 * =====================================================================
 *
 * Spike Tarefa C — nasce AO LADO de `NodeConfigPanel.tsx` (bespoke, por tipo),
 * sem removê-lo nem alterá-lo. Escopo: apenas o nó `condition`, cujo contrato
 * `{schema, uischema, outputSchema}` a Tarefa A passou a expor no catálogo do
 * runtime (`GET /api/v1/flows/catalog`).
 *
 * O formulário é renderizado por `@jsonforms/react` a partir do schema do
 * catálogo. Os controles são renderers próprios do Meu Executivo, construídos sobre os
 * componentes do design system (Input, Select, Label) — NÃO se arrasta a
 * design-language do Workflow Builder. Os `@jsonforms/vanilla-renderers` entram
 * só como fallback de baixa prioridade para qualquer elemento não coberto.
 *
 * CSP: a política de produção não tem `unsafe-eval`
 * (`script-src 'self' https://connect.facebook.net`). O JsonForms embute o Ajv,
 * que usa `new Function()` em `compile()`/`validate()` — bloqueado por essa CSP.
 * Isolamos o risco em duas frentes:
 *   1. `validationMode="NoValidation"` — o JsonForms nunca chama `ajv.compile`
 *      no schema de dados (a validação autoritativa é fail-closed no backend).
 *   2. Passamos um adaptador de Ajv CSP-safe (sem `eval`) que delega ao
 *      validador vendored (`@/vendor/workflowbuilder`, Apache-2.0). Ele é o único
 *      caminho de `ajv` que o JsonForms exercita para o nó condition: a
 *      avaliação da regra `HIDE` do campo "value" (condição baseada em `enum`).
 */
import { useMemo, type ComponentProps, type ReactNode } from 'react';
import {
  and,
  isBooleanControl,
  isEnumControl,
  isIntegerControl,
  isNumberControl,
  or,
  isStringControl,
  rankWith,
  uiTypeIs,
  type ControlProps,
  type JsonSchema,
  type Layout,
  type LayoutProps,
  type RankedTester,
  type UISchemaElement,
} from '@jsonforms/core';
import {
  JsonForms,
  JsonFormsDispatch,
  withJsonFormsControlProps,
  withJsonFormsLayoutProps,
} from '@jsonforms/react';
import { vanillaCells, vanillaRenderers } from '@jsonforms/vanilla-renderers';
import { Sparkles, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import {
  createCspSafeValidator,
  type JsonSchema as CspJsonSchema,
} from '@/vendor/workflowbuilder/csp-safe-schema-validator';

// ---------------------------------------------------------------------------
// Escala tipográfica canônica do Meu Executivo: label = text-sm, hint/erro = text-xs.
// ---------------------------------------------------------------------------
function KlmFieldShell({
  id,
  label,
  description,
  error,
  required,
  children,
}: {
  id?: string;
  label?: string;
  description?: string;
  error?: string;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      {label ? (
        <Label htmlFor={id} className="text-sm font-medium text-foreground">
          {label}
          {required ? <span className="ml-0.5 text-destructive">*</span> : null}
        </Label>
      ) : null}
      {children}
      {error ? (
        <p className="text-xs leading-relaxed text-destructive">{error}</p>
      ) : description ? (
        <p className="text-xs leading-relaxed text-muted-foreground">{description}</p>
      ) : null}
    </div>
  );
}

function enumOptionLabel(value: string, uischema: UISchemaElement | undefined): string {
  const options = (uischema as { options?: { enumLabels?: Record<string, unknown> } } | undefined)?.options;
  const labels = options?.enumLabels;
  if (labels && typeof labels === 'object' && typeof labels[value] === 'string') {
    return labels[value] as string;
  }
  return value;
}

function placeholderFor(uischema: UISchemaElement | undefined): string | undefined {
  const options = (uischema as { options?: { placeholder?: unknown } } | undefined)?.options;
  return typeof options?.placeholder === 'string' ? options.placeholder : undefined;
}

// ---------------------------------------------------------------------------
// Renderer de string simples (sem enum) -> Input do design system.
// ---------------------------------------------------------------------------
function KlmTextControlBase(props: ControlProps) {
  const { id, label, description, errors, required, visible, enabled, data, handleChange, path, uischema } = props;
  if (visible === false) {
    return null;
  }
  return (
    <KlmFieldShell id={id} label={label} description={description} error={errors} required={required}>
      <Input
        id={id}
        data-testid={`schema-field-${path}`}
        className="h-9 text-sm"
        value={typeof data === 'string' ? data : ''}
        placeholder={placeholderFor(uischema)}
        disabled={enabled === false}
        onChange={(event) => handleChange(path, event.target.value)}
      />
    </KlmFieldShell>
  );
}

const klmTextControlTester: RankedTester = rankWith(4, isStringControl);
const KlmTextControl = withJsonFormsControlProps(KlmTextControlBase);

// ---------------------------------------------------------------------------
// Números e booleanos são muito comuns nos novos nós de runtime (limites,
// timeout, prioridade de retry, gates e flags). Não delegamos esses valores a
// um textarea/JSON genérico: isso evita que o operador transforme `false` em
// texto ou envie um número inválido. Objetos e arrays continuam sob contrato
// específico do nó ou renderer vanilla até terem um seletor de domínio.
// ---------------------------------------------------------------------------
function KlmNumberControlBase(props: ControlProps) {
  const { id, label, description, errors, required, visible, enabled, data, handleChange, path, schema } = props;
  if (visible === false) return null;
  const numericSchema = schema as { minimum?: unknown; maximum?: unknown; multipleOf?: unknown };
  const min = typeof numericSchema.minimum === 'number' ? numericSchema.minimum : undefined;
  const max = typeof numericSchema.maximum === 'number' ? numericSchema.maximum : undefined;
  const step = typeof numericSchema.multipleOf === 'number' ? numericSchema.multipleOf : 'any';
  return (
    <KlmFieldShell id={id} label={label} description={description} error={errors} required={required}>
      <Input
        id={id}
        data-testid={`schema-field-${path}`}
        type="number"
        min={min}
        max={max}
        step={step}
        className="h-9 text-sm"
        value={typeof data === 'number' && Number.isFinite(data) ? String(data) : ''}
        disabled={enabled === false}
        onChange={(event) => {
          const next = event.target.value;
          handleChange(path, next === '' ? undefined : Number(next));
        }}
      />
    </KlmFieldShell>
  );
}

const klmNumberControlTester: RankedTester = rankWith(10, or(isNumberControl, isIntegerControl));
const KlmNumberControl = withJsonFormsControlProps(KlmNumberControlBase);

function KlmBooleanControlBase(props: ControlProps) {
  const { id, label, description, errors, required, visible, enabled, data, handleChange, path } = props;
  if (visible === false) return null;
  return (
    <KlmFieldShell id={id} label={label} description={description} error={errors} required={required}>
      <div className="flex min-h-9 items-center justify-between rounded-md border px-3">
        <span className="text-xs text-muted-foreground">{data === true ? 'Ativado' : 'Desativado'}</span>
        <Switch
          id={id}
          data-testid={`schema-field-${path}`}
          aria-label={label || path}
          checked={data === true}
          disabled={enabled === false}
          onCheckedChange={(next) => handleChange(path, next)}
        />
      </div>
    </KlmFieldShell>
  );
}

const klmBooleanControlTester: RankedTester = rankWith(6, isBooleanControl);
const KlmBooleanControl = withJsonFormsControlProps(KlmBooleanControlBase);

// ---------------------------------------------------------------------------
// Renderer de enum de string -> Select do design system.
// ---------------------------------------------------------------------------
function KlmEnumControlBase(props: ControlProps) {
  const { id, label, description, errors, required, visible, enabled, data, handleChange, path, schema, uischema } = props;
  if (visible === false) {
    return null;
  }
  const options = Array.isArray(schema.enum)
    ? (schema.enum.filter((value) => typeof value === 'string') as string[])
    : [];
  return (
    <KlmFieldShell id={id} label={label} description={description} error={errors} required={required}>
      <Select
        value={typeof data === 'string' ? data : ''}
        disabled={enabled === false}
        onValueChange={(value) => handleChange(path, value)}
      >
        <SelectTrigger id={id} data-testid={`schema-field-${path}`} className="h-9 text-sm">
          <SelectValue placeholder="Selecionar" />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option} value={option} className="text-sm">
              {enumOptionLabel(option, uischema)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </KlmFieldShell>
  );
}

const klmEnumControlTester: RankedTester = rankWith(6, and(isStringControl, isEnumControl));
const KlmEnumControl = withJsonFormsControlProps(KlmEnumControlBase);

// ---------------------------------------------------------------------------
// Layout vertical -> pilha com espaçamento do design system.
// ---------------------------------------------------------------------------
function KlmVerticalLayoutBase(props: LayoutProps) {
  const { uischema, schema, path, enabled, renderers, cells, visible } = props;
  if (visible === false) {
    return null;
  }
  const elements = (uischema as Layout).elements ?? [];
  return (
    <div className="space-y-4">
      {elements.map((child, index) => (
        <JsonFormsDispatch
          key={`${path ?? 'root'}-${index}`}
          uischema={child}
          schema={schema}
          path={path ?? ''}
          enabled={enabled}
          renderers={renderers}
          cells={cells}
        />
      ))}
    </div>
  );
}

const klmVerticalLayoutTester: RankedTester = rankWith(5, uiTypeIs('VerticalLayout'));
const KlmVerticalLayout = withJsonFormsLayoutProps(KlmVerticalLayoutBase);

// Renderers do Meu Executivo têm prioridade (rank 4-6) sobre os vanilla (rank 1-3), que
// ficam apenas como fallback dependency-light para elementos não cobertos.
const klmSchemaRenderers = [
  ...vanillaRenderers,
  { tester: klmVerticalLayoutTester, renderer: KlmVerticalLayout },
  { tester: klmEnumControlTester, renderer: KlmEnumControl },
  { tester: klmBooleanControlTester, renderer: KlmBooleanControl },
  { tester: klmNumberControlTester, renderer: KlmNumberControl },
  { tester: klmTextControlTester, renderer: KlmTextControl },
];

// ---------------------------------------------------------------------------
// Adaptador de Ajv CSP-safe. Substitui o Ajv (eval) do JsonForms pelo validador
// vendored (sem `new Function`). Combinado a validationMode="NoValidation",
// mantém o painel funcional sob a CSP de produção (sem `unsafe-eval`).
// ---------------------------------------------------------------------------
type JsonFormsAjv = NonNullable<ComponentProps<typeof JsonForms>['ajv']>;

function createCspSafeAjv(): JsonFormsAjv {
  const validator = createCspSafeValidator();
  const adapter: Record<string, unknown> = {
    compile: (schema: unknown) => validator.compile(schema as CspJsonSchema),
    validate: (schema: unknown, data: unknown) => validator.validate(schema as CspJsonSchema, data),
    // Superfície residual do Ajv que o JsonForms pode tocar: no-ops CSP-safe.
    addSchema: () => adapter,
    addMetaSchema: () => adapter,
    removeSchema: () => adapter,
    getSchema: () => undefined,
    addFormat: () => adapter,
    addKeyword: () => adapter,
    removeKeyword: () => adapter,
    getKeyword: () => false,
    errorsText: () => '',
    errors: null,
  };
  return adapter as unknown as JsonFormsAjv;
}

interface SchemaDrivenNodePanelProps {
  /** JSON Schema do nó vindo do catálogo do runtime (`nodes[].schema`). */
  schema: JsonSchema;
  /** UI Schema do catálogo (`nodes[].uischema`); ausente => layout gerado. */
  uischema?: UISchemaElement;
  /** Dados atuais governados pelo schema (subconjunto da config do nó). */
  data: Record<string, unknown>;
  /**
   * Chamado a cada edição com os dados do formulário. Quem monta o painel liga
   * este callback ao MESMO caminho de save do editor atual (sem backend novo).
   */
  onChange: (data: Record<string, unknown>) => void;
  title?: string;
  description?: string;
  badges?: ReactNode;
  onClose?: () => void;
}

export function SchemaDrivenNodePanel({
  schema,
  uischema,
  data,
  onChange,
  title = 'Configuração schema-driven',
  description,
  badges,
  onClose,
}: SchemaDrivenNodePanelProps) {
  const renderers = useMemo(() => klmSchemaRenderers, []);
  const ajv = useMemo(() => createCspSafeAjv(), []);

  return (
    <div data-testid="schema-driven-node-panel" className="flex h-full flex-col">
      <div className="flex items-start justify-between gap-3 border-b px-4 py-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            <span className="text-base font-medium">{title}</span>
          </div>
          <p className="text-xs text-muted-foreground leading-relaxed">
            Formulário derivado do catálogo do runtime (registro v2). Validação autoritativa fail-closed no backend.
          </p>
          {badges ? <div className="flex flex-wrap items-center gap-2 pt-1">{badges}</div> : null}
        </div>
        {onClose ? (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0"
            aria-label="Fechar configuração do nó"
            title="Fechar configuração do nó"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </Button>
        ) : null}
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {description ? (
          <div className="rounded-lg border border-dashed px-3 py-2 text-xs leading-relaxed text-muted-foreground">
            {description}
          </div>
        ) : null}
        <JsonForms
          schema={schema}
          uischema={uischema}
          data={data}
          renderers={renderers}
          cells={vanillaCells}
          ajv={ajv}
          validationMode="NoValidation"
          onChange={({ data: nextData }) => {
            const normalized = (nextData ?? {}) as Record<string, unknown>;
            if (JSON.stringify(normalized) === JSON.stringify(data)) return;
            onChange(normalized);
          }}
        />
      </div>
    </div>
  );
}
