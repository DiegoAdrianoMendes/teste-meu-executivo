import { useEffect, useState } from 'react';
import { Plus, Save, Sparkles, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import type { MessageTemplate, MessageTemplateSettings } from '@/types';

interface MessageTemplatesPanelProps {
  value: MessageTemplateSettings | null;
  loading?: boolean;
  readOnly?: boolean;
  onSave: (value: MessageTemplateSettings) => Promise<void>;
}

const EMPTY_TEMPLATE = (kind: string, label: string, format = 'text'): MessageTemplate => ({
  id: `template-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
  source: 'workspace',
  kind,
  category: 'geral',
  label,
  body: '',
  format,
  favorite: false,
  title: label,
  footer: '',
});

function TemplateEditor({
  title,
  description,
  items,
  readOnly,
  onChange,
  onAdd,
}: {
  title: string;
  description: string;
  items: MessageTemplate[];
  readOnly: boolean;
  onChange: (items: MessageTemplate[]) => void;
  onAdd: () => void;
}) {
  const updateItem = (index: number, patch: Partial<MessageTemplate>) => {
    onChange(items.map((item, currentIndex) => (currentIndex === index ? { ...item, ...patch } : item)));
  };

  const removeItem = (index: number) => {
    onChange(items.filter((_, currentIndex) => currentIndex !== index));
  };

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium">{title}</p>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
        <Button variant="outline" size="sm" onClick={onAdd} disabled={readOnly}>
          <Plus className="mr-2 h-4 w-4" />
          Adicionar
        </Button>
      </div>

      <div className="space-y-4">
        {items.map((template, index) => (
          <div key={template.id} className="rounded-xl border p-4 space-y-3">
            <div className="flex items-start justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">{template.kind}</Badge>
                <Badge variant="secondary">{template.format}</Badge>
                {template.favorite && <Badge variant="secondary">favorito</Badge>}
              </div>
              <Button variant="ghost" size="icon" onClick={() => removeItem(index)} disabled={readOnly}>
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Rótulo</Label>
                <Input value={template.label} onChange={(event) => updateItem(index, { label: event.target.value })} disabled={readOnly} />
              </div>
              <div className="space-y-1.5">
                <Label>Categoria</Label>
                <Input value={template.category} onChange={(event) => updateItem(index, { category: event.target.value })} disabled={readOnly} />
              </div>
              <div className="space-y-1.5">
                <Label>Título do documento</Label>
                <Input value={template.title ?? ''} onChange={(event) => updateItem(index, { title: event.target.value })} disabled={readOnly} />
              </div>
              <div className="space-y-1.5">
                <Label>Formato</Label>
                <Input value={template.format} onChange={(event) => updateItem(index, { format: event.target.value })} disabled={readOnly} />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>Corpo</Label>
              <Textarea
                value={template.body}
                onChange={(event) => updateItem(index, { body: event.target.value })}
                disabled={readOnly}
                className="min-h-[140px]"
              />
            </div>

            <div className="space-y-1.5">
              <Label>Rodapé</Label>
              <Textarea
                value={template.footer ?? ''}
                onChange={(event) => updateItem(index, { footer: event.target.value })}
                disabled={readOnly}
                className="min-h-[80px]"
              />
            </div>
          </div>
        ))}

        {items.length === 0 && (
          <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
            Nenhum template cadastrado nesta categoria ainda.
          </div>
        )}
      </div>
    </div>
  );
}

export function MessageTemplatesPanel({
  value,
  loading,
  readOnly = false,
  onSave,
}: MessageTemplatesPanelProps) {
  const [draft, setDraft] = useState<MessageTemplateSettings | null>(value);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(value);
  }, [value]);

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
        <CardContent className="py-6 text-sm text-muted-foreground">Carregando templates...</CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <Sparkles className="h-4 w-4" />
          Respostas rápidas internas (mensagens, proposta e contrato)
        </CardTitle>
        <CardDescription>
          Biblioteca interna do chat, para uso dentro da janela de 24h de conversas já abertas. Não são templates oficiais da Meta — para criar e submeter templates que abrem conversa, use "Templates WhatsApp Meta". Variáveis úteis: {'{client_name}'}, {'{workspace_name}'}, {'{contract_link}'}, {'{responsible_name}'}.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="rounded-xl border bg-muted/20 p-4">
          <p className="text-sm font-medium">Biblioteca base da plataforma</p>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            {draft.systemTemplates.map((template) => (
              <div key={template.id} className="rounded-lg border bg-background p-3">
                <div className="flex items-center gap-2">
                  <Badge variant="outline">{template.category}</Badge>
                  {template.favorite && <Badge variant="secondary">favorito</Badge>}
                </div>
                <p className="mt-2 text-sm font-medium">{template.label}</p>
                <p className="mt-1 text-xs text-muted-foreground whitespace-pre-wrap">{template.body}</p>
              </div>
            ))}
          </div>
        </div>

        <Tabs defaultValue="workspace">
          <TabsList className="flex h-auto flex-wrap justify-start gap-2 bg-transparent p-0">
            <TabsTrigger value="workspace">Mensagens do workspace</TabsTrigger>
            <TabsTrigger value="proposal">Propostas</TabsTrigger>
            <TabsTrigger value="contract">Contratos</TabsTrigger>
          </TabsList>

          <TabsContent value="workspace" className="pt-4">
            <TemplateEditor
              title="Mensagens padrão do inbox"
              description="Entram junto com os templates do sistema e aparecem na busca rápida do chat."
              items={draft.workspaceTemplates}
              readOnly={readOnly}
              onChange={(items) => setDraft((current) => (current ? { ...current, workspaceTemplates: items } : current))}
              onAdd={() => setDraft((current) => (current ? { ...current, workspaceTemplates: [...current.workspaceTemplates, EMPTY_TEMPLATE('message', 'Nova mensagem')] } : current))}
            />
          </TabsContent>

          <TabsContent value="proposal" className="pt-4">
            <TemplateEditor
              title="Templates de proposta"
              description="Podem sair em texto direto ou como PDF comercial enviado pelo WhatsApp."
              items={draft.proposalTemplates}
              readOnly={readOnly}
              onChange={(items) => setDraft((current) => (current ? { ...current, proposalTemplates: items } : current))}
              onAdd={() => setDraft((current) => (current ? { ...current, proposalTemplates: [...current.proposalTemplates, EMPTY_TEMPLATE('proposal', 'Nova proposta', 'text')] } : current))}
            />
          </TabsContent>

          <TabsContent value="contract" className="pt-4">
            <TemplateEditor
              title="Templates de contrato e assinatura"
              description="Aqui ficam o PDF-base do contrato e também a mensagem de envio usada no fluxo oficial."
              items={draft.contractTemplates}
              readOnly={readOnly}
              onChange={(items) => setDraft((current) => (current ? { ...current, contractTemplates: items } : current))}
              onAdd={() => setDraft((current) => (current ? { ...current, contractTemplates: [...current.contractTemplates, EMPTY_TEMPLATE('contract', 'Novo contrato', 'pdf')] } : current))}
            />
          </TabsContent>
        </Tabs>

        <div className="flex justify-end">
          <Button onClick={() => void handleSave()} disabled={readOnly || saving}>
            <Save className="mr-2 h-4 w-4" />
            {saving ? 'Salvando...' : 'Salvar templates'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
