import { useEffect, useState } from 'react';
import {
  Building2,
  Image,
  Loader2,
  Palette,
  Plus,
  Save,
  ShieldCheck,
  Trash2,
  Type,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import type { BrandingSettingsProfile } from '@/types';

interface BrandingSettingsPanelProps {
  value: BrandingSettingsProfile | null;
  loading?: boolean;
  readOnly?: boolean;
  onSave: (value: BrandingSettingsProfile) => Promise<void>;
}

function Field({ label, children, className = '' }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`min-w-0 space-y-1.5 ${className}`}>
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function ColorField({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <Field label={label}>
      <div className="flex items-center gap-2">
        <label
          className="relative h-9 w-10 shrink-0 overflow-hidden border border-border"
          style={{ backgroundColor: value }}
          title={`Selecionar ${label.toLowerCase()}`}
        >
          <input
            type="color"
            value={value}
            onChange={(event) => onChange(event.target.value.toUpperCase())}
            className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
            disabled={disabled}
            aria-label={`Selecionar ${label.toLowerCase()}`}
          />
        </label>
        <Input
          value={value}
          onChange={(event) => onChange(event.target.value.toUpperCase())}
          disabled={disabled}
          maxLength={7}
          className="font-mono uppercase"
        />
      </div>
    </Field>
  );
}

export function BrandingSettingsPanel({
  value,
  loading,
  readOnly = false,
  onSave,
}: BrandingSettingsPanelProps) {
  const [draft, setDraft] = useState<BrandingSettingsProfile | null>(value);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  const update = <K extends keyof BrandingSettingsProfile>(
    key: K,
    nextValue: BrandingSettingsProfile[K],
  ) => {
    setDraft((current) => (current ? { ...current, [key]: nextValue } : current));
  };

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      await onSave(draft);
    } finally {
      setSaving(false);
    }
  };

  if (loading || !draft) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Carregando identidade do workspace...
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="border-b border-border/70">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <Palette className="h-4 w-4" />
              Marca do workspace
            </CardTitle>
            <CardDescription className="mt-1">
              Identidade usada no sistema, propostas e documentos.
            </CardDescription>
          </div>
          <div
            className="min-w-[260px] overflow-hidden border border-border bg-background"
            style={{ color: draft.foregroundColor, backgroundColor: draft.backgroundColor }}
          >
            <div className="h-2" style={{ backgroundColor: draft.primaryColor }} />
            <div className="flex items-center gap-3 p-3">
              <div
                className="flex h-9 w-9 items-center justify-center text-xs font-semibold"
                style={{ backgroundColor: draft.sidebarColor, color: draft.primaryColor }}
              >
                {draft.shortName.slice(0, 3).toUpperCase()}
              </div>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">{draft.brandName}</p>
                <p className="truncate text-xs opacity-70">{draft.tagline}</p>
              </div>
            </div>
          </div>
        </div>
      </CardHeader>
      <CardContent className="pt-5">
        <Tabs defaultValue="identidade">
          <TabsList className="h-auto flex-wrap justify-start">
            <TabsTrigger value="identidade" className="gap-1.5"><Building2 className="h-3.5 w-3.5" />Identidade</TabsTrigger>
            <TabsTrigger value="cores" className="gap-1.5"><Palette className="h-3.5 w-3.5" />Cores</TabsTrigger>
            <TabsTrigger value="arquivos" className="gap-1.5"><Image className="h-3.5 w-3.5" />Arquivos</TabsTrigger>
            <TabsTrigger value="tipografia" className="gap-1.5"><Type className="h-3.5 w-3.5" />Tipografia</TabsTrigger>
            <TabsTrigger value="institucional" className="gap-1.5"><ShieldCheck className="h-3.5 w-3.5" />Institucional</TabsTrigger>
          </TabsList>

          <TabsContent value="identidade" className="grid gap-4 pt-4 md:grid-cols-2">
            <Field label="Nome da marca">
              <Input value={draft.brandName} onChange={(event) => update('brandName', event.target.value)} disabled={readOnly} />
            </Field>
            <Field label="Nome curto">
              <Input value={draft.shortName} onChange={(event) => update('shortName', event.target.value)} disabled={readOnly} />
            </Field>
            <Field label="Assinatura da marca" className="md:col-span-2">
              <Input value={draft.tagline} onChange={(event) => update('tagline', event.target.value)} disabled={readOnly} />
            </Field>
            <Field label="Nome jurídico">
              <Input value={draft.legalName} onChange={(event) => update('legalName', event.target.value)} disabled={readOnly} />
            </Field>
            <Field label="Domínio">
              <Input value={draft.domain} onChange={(event) => update('domain', event.target.value)} disabled={readOnly} />
            </Field>
            <Field label="Idioma padrão">
              <Select value={draft.defaultLocale} onValueChange={(next) => update('defaultLocale', next)} disabled={readOnly}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="pt-BR">Português (Brasil)</SelectItem></SelectContent>
              </Select>
            </Field>
            <Field label="Densidade padrão">
              <Select value={draft.layoutDensity} onValueChange={(next) => update('layoutDensity', next as BrandingSettingsProfile['layoutDensity'])} disabled={readOnly}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="compact">Compacta</SelectItem>
                  <SelectItem value="comfortable">Confortável</SelectItem>
                  <SelectItem value="spacious">Ampla</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </TabsContent>

          <TabsContent value="cores" className="grid gap-4 pt-4 sm:grid-cols-2 lg:grid-cols-4">
            <ColorField label="Primária" value={draft.primaryColor} disabled={readOnly} onChange={(next) => update('primaryColor', next)} />
            <ColorField label="Apoio" value={draft.accentColor} disabled={readOnly} onChange={(next) => update('accentColor', next)} />
            <ColorField label="Texto" value={draft.foregroundColor} disabled={readOnly} onChange={(next) => update('foregroundColor', next)} />
            <ColorField label="Fundo" value={draft.backgroundColor} disabled={readOnly} onChange={(next) => update('backgroundColor', next)} />
            <ColorField label="Navegação" value={draft.sidebarColor} disabled={readOnly} onChange={(next) => update('sidebarColor', next)} />
            <ColorField label="Sucesso" value={draft.successColor} disabled={readOnly} onChange={(next) => update('successColor', next)} />
            <ColorField label="Alerta" value={draft.warningColor} disabled={readOnly} onChange={(next) => update('warningColor', next)} />
            <ColorField label="Crítico" value={draft.dangerColor} disabled={readOnly} onChange={(next) => update('dangerColor', next)} />
          </TabsContent>

          <TabsContent value="arquivos" className="grid gap-4 pt-4 md:grid-cols-2">
            {([
              ['logoUrl', 'Logo para fundo claro'],
              ['logoDarkUrl', 'Logo para fundo escuro'],
              ['iconUrl', 'Símbolo da marca'],
              ['faviconUrl', 'Favicon'],
              ['appleTouchIconUrl', 'Ícone para dispositivos'],
              ['letterheadUrl', 'Papel timbrado'],
              ['signatureUrl', 'Assinatura institucional'],
            ] as const).map(([key, label]) => (
              <Field key={key} label={label} className={key === 'signatureUrl' ? 'md:col-span-2' : ''}>
                <Input
                  value={draft[key] ?? ''}
                  onChange={(event) => update(key, event.target.value || null)}
                  disabled={readOnly}
                  placeholder="/arquivos/..."
                />
              </Field>
            ))}
          </TabsContent>

          <TabsContent value="tipografia" className="grid gap-4 pt-4 md:grid-cols-2">
            <Field label="Fonte da interface">
              <Input value={draft.sansFamily} onChange={(event) => update('sansFamily', event.target.value)} disabled={readOnly} />
            </Field>
            <Field label="Fonte jurídica">
              <Input value={draft.legalFamily} onChange={(event) => update('legalFamily', event.target.value)} disabled={readOnly} />
            </Field>
            <Field label="Título padrão">
              <Input value={draft.defaultTitle} onChange={(event) => update('defaultTitle', event.target.value)} disabled={readOnly} />
            </Field>
            <Field label="Modelo de título">
              <Input value={draft.titleTemplate} onChange={(event) => update('titleTemplate', event.target.value)} disabled={readOnly} />
            </Field>
            <Field label="Descrição" className="md:col-span-2">
              <Textarea value={draft.description} onChange={(event) => update('description', event.target.value)} disabled={readOnly} rows={3} />
            </Field>
            <Field label="Nome do aplicativo">
              <Input value={draft.applicationName} onChange={(event) => update('applicationName', event.target.value)} disabled={readOnly} />
            </Field>
            <Field label="Autor institucional">
              <Input value={draft.author} onChange={(event) => update('author', event.target.value)} disabled={readOnly} />
            </Field>
            <ColorField label="Cor do navegador" value={draft.themeColor} disabled={readOnly} onChange={(next) => update('themeColor', next)} />
          </TabsContent>

          <TabsContent value="institucional" className="space-y-6 pt-4">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="E-mail público"><Input value={draft.email} onChange={(event) => update('email', event.target.value)} disabled={readOnly} /></Field>
              <Field label="E-mail de suporte"><Input value={draft.supportEmail} onChange={(event) => update('supportEmail', event.target.value)} disabled={readOnly} /></Field>
              <Field label="Telefone"><Input value={draft.phone ?? ''} onChange={(event) => update('phone', event.target.value || null)} disabled={readOnly} /></Field>
              <Field label="Site"><Input value={draft.websiteUrl} onChange={(event) => update('websiteUrl', event.target.value)} disabled={readOnly} /></Field>
              <Field label="Razão social"><Input value={draft.registeredName} onChange={(event) => update('registeredName', event.target.value)} disabled={readOnly} /></Field>
              <Field label="Registro"><Input value={draft.registrationNumber ?? ''} onChange={(event) => update('registrationNumber', event.target.value || null)} disabled={readOnly} /></Field>
              <Field label="Registro na OAB"><Input value={draft.barRegistration ?? ''} onChange={(event) => update('barRegistration', event.target.value || null)} disabled={readOnly} /></Field>
              <Field label="Endereço"><Input value={draft.address ?? ''} onChange={(event) => update('address', event.target.value || null)} disabled={readOnly} /></Field>
            </div>

            <div className="border-t border-border pt-5">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium">Biblioteca de prova social</p>
                  <p className="mt-1 text-xs text-muted-foreground">Itens só aparecem externamente após aprovação.</p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={readOnly}
                  onClick={() => update('socialProof', [
                    ...draft.socialProof,
                    {
                      id: `proof-${Date.now()}`,
                      kind: 'metric',
                      label: '',
                      value: '',
                      approvedForExternalUse: false,
                    },
                  ])}
                >
                  <Plus className="mr-2 h-4 w-4" />Adicionar
                </Button>
              </div>
              <div className="mt-4 space-y-3">
                {draft.socialProof.length === 0 && (
                  <p className="border border-dashed border-border px-4 py-6 text-center text-xs text-muted-foreground">
                    Nenhum item cadastrado.
                  </p>
                )}
                {draft.socialProof.map((proof, index) => (
                  <div key={proof.id} className="grid gap-3 border border-border p-3 lg:grid-cols-[160px_minmax(0,1fr)_minmax(0,1fr)_auto_auto] lg:items-end">
                    <Field label="Tipo">
                      <Select
                        value={proof.kind}
                        onValueChange={(kind) => update('socialProof', draft.socialProof.map((item, itemIndex) => itemIndex === index ? { ...item, kind: kind as typeof proof.kind } : item))}
                        disabled={readOnly}
                      >
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="metric">Métrica</SelectItem>
                          <SelectItem value="credential">Credencial</SelectItem>
                          <SelectItem value="testimonial">Depoimento</SelectItem>
                          <SelectItem value="case_result">Resultado</SelectItem>
                        </SelectContent>
                      </Select>
                    </Field>
                    <Field label="Rótulo"><Input value={proof.label} onChange={(event) => update('socialProof', draft.socialProof.map((item, itemIndex) => itemIndex === index ? { ...item, label: event.target.value } : item))} disabled={readOnly} /></Field>
                    <Field label="Valor"><Input value={proof.value} onChange={(event) => update('socialProof', draft.socialProof.map((item, itemIndex) => itemIndex === index ? { ...item, value: event.target.value } : item))} disabled={readOnly} /></Field>
                    <label className="flex h-9 items-center gap-2 text-xs">
                      <Switch
                        checked={proof.approvedForExternalUse}
                        onCheckedChange={(approvedForExternalUse) => update('socialProof', draft.socialProof.map((item, itemIndex) => itemIndex === index ? { ...item, approvedForExternalUse } : item))}
                        disabled={readOnly}
                      />
                      Aprovado
                    </label>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      disabled={readOnly}
                      onClick={() => update('socialProof', draft.socialProof.filter((_, itemIndex) => itemIndex !== index))}
                      title="Remover item"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          </TabsContent>
        </Tabs>

        <div className="flex justify-end border-t border-border pt-5">
          <Button onClick={handleSave} disabled={saving || readOnly}>
            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
            Salvar marca
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
