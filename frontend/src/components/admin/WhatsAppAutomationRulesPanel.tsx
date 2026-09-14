import { useEffect, useMemo, useState } from 'react';
import { Bot, Clock3, Plus, Save, Trash2, WandSparkles } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import type {
  ConversationAutomationRule,
  FlowTemplateReference,
  KanbanColumnId,
  WhatsAppAutomationSettings,
} from '@/types';

interface WhatsAppAutomationRulesPanelProps {
  value: WhatsAppAutomationSettings | null;
  loading?: boolean;
  readOnly?: boolean;
  flowOptions?: FlowTemplateReference[];
  onSave: (value: WhatsAppAutomationSettings) => Promise<void>;
}

const COLUMN_OPTIONS: Array<{ value: KanbanColumnId; label: string }> = [
  { value: 'entrada', label: 'Entrada' },
  { value: 'qualificacao', label: 'Qualificação' },
  { value: 'proposta_fechamento', label: 'Proposta & fechamento' },
  { value: 'coleta_dados', label: 'Coleta de dados' },
  { value: 'triagem_juridica', label: 'Triagem jurídica' },
  { value: 'pesquisa_validacao', label: 'Pesquisa & validação' },
  { value: 'redacao_peca', label: 'Redação da peça' },
  { value: 'aprovacao_pendente', label: 'Aprovação pendente' },
  { value: 'pronto_protocolo', label: 'Pronto para protocolo' },
  { value: 'migrar_sistema', label: 'Migrar sistema' },
];

const EMPTY_RULE = (): ConversationAutomationRule => ({
  id: `automation-rule-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
  label: 'Nova regra',
  enabled: true,
  triggerType: 'keyword',
  keyword: '',
  replyText: '',
  flowTemplateId: null,
  targetQueueStatus: null,
  targetColumnId: null,
  escalateToHuman: false,
  onlyFirstMessage: false,
  silenceWindowHours: 0,
});

export function WhatsAppAutomationRulesPanel({
  value,
  loading,
  readOnly = false,
  flowOptions = [],
  onSave,
}: WhatsAppAutomationRulesPanelProps) {
  const [draft, setDraft] = useState<WhatsAppAutomationSettings | null>(value);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  const flowLabelMap = useMemo(
    () => new Map(flowOptions.map((flow) => [flow.id, flow.name])),
    [flowOptions],
  );

  const updateRule = (ruleId: string, patch: Partial<ConversationAutomationRule>) => {
    setDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        rules: current.rules.map((rule) => (rule.id === ruleId ? { ...rule, ...patch } : rule)),
      };
    });
  };

  const removeRule = (ruleId: string) => {
    setDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        rules: current.rules.filter((rule) => rule.id !== ruleId),
      };
    });
  };

  const addRule = () => {
    setDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        rules: [...current.rules, EMPTY_RULE()],
      };
    });
  };

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      await onSave({
        ...draft,
        onboardingCompleted: true,
        rules: draft.rules.map((rule) => ({
          ...rule,
          label: rule.label.trim() || 'Regra automática',
          keyword: rule.keyword?.trim() || null,
          replyText: rule.replyText?.trim() || null,
        })),
      });
    } finally {
      setSaving(false);
    }
  };

  if (loading || !draft) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">Carregando automações do inbox...</CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <WandSparkles className="h-4 w-4" />
          Automações simples do inbox
        </CardTitle>
        <CardDescription>
          Regras leves para palavra-chave, primeira resposta, fora do horário e roteamento para fluxo ou follow-up. Tudo respeita o canal oficial e a janela operacional.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {!draft.onboardingCompleted && (
          <div role="status" className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-amber-950">
            <p className="text-sm font-medium">Escolha obrigatória do escritório</p>
            <p className="mt-1 text-xs">
              Nenhuma resposta automática será enviada até o escritório revisar as sugestões abaixo e confirmar sua escolha. É válido manter todas desligadas.
            </p>
          </div>
        )}
        <div className="grid gap-4 md:grid-cols-3">
          <label className="flex items-center justify-between rounded-xl border p-4 md:col-span-1">
            <div className="space-y-1">
              <span className="text-sm font-medium">Automações habilitadas</span>
              <p className="text-xs text-muted-foreground">Desligue aqui se quiser deixar o inbox 100% manual por um período.</p>
            </div>
            <Switch
              checked={draft.enabled}
              onCheckedChange={(checked) => setDraft((current) => (current ? { ...current, enabled: checked } : current))}
              disabled={readOnly}
            />
          </label>
          <div className="rounded-xl border p-4 space-y-1.5">
            <Label>Timezone operacional</Label>
            <Input
              value={draft.timezone}
              onChange={(event) => setDraft((current) => (current ? { ...current, timezone: event.target.value } : current))}
              disabled={readOnly}
            />
          </div>
          <div className="grid grid-cols-2 gap-3 rounded-xl border p-4">
            <div className="space-y-1.5">
              <Label>Início do horário</Label>
              <Input
                type="number"
                min={0}
                max={23}
                value={draft.businessHoursStart}
                onChange={(event) => setDraft((current) => (current ? { ...current, businessHoursStart: Number(event.target.value) } : current))}
                disabled={readOnly}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Fim do horário</Label>
              <Input
                type="number"
                min={0}
                max={23}
                value={draft.businessHoursEnd}
                onChange={(event) => setDraft((current) => (current ? { ...current, businessHoursEnd: Number(event.target.value) } : current))}
                disabled={readOnly}
              />
            </div>
          </div>
        </div>

        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm font-medium">Regras ativas</p>
            <p className="text-xs text-muted-foreground">
              Combine resposta, escalada humana, fila de atendimento, etapa do Kanban e flow aplicado.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={addRule} disabled={readOnly}>
            <Plus className="mr-2 h-4 w-4" />
            Nova regra
          </Button>
        </div>

        <div className="space-y-4">
          {draft.rules.map((rule) => (
            <div key={rule.id} className="rounded-xl border p-4 space-y-4">
              <div className="flex items-start justify-between gap-3">
                <div className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">{rule.triggerType}</Badge>
                    {rule.flowTemplateId && <Badge variant="secondary">{flowLabelMap.get(rule.flowTemplateId) ?? 'Flow aplicado'}</Badge>}
                    {rule.targetQueueStatus && <Badge variant="outline">{rule.targetQueueStatus}</Badge>}
                    {rule.targetColumnId && <Badge variant="outline">{rule.targetColumnId}</Badge>}
                  </div>
                  <Input
                    value={rule.label}
                    onChange={(event) => updateRule(rule.id, { label: event.target.value })}
                    disabled={readOnly}
                    className="h-9 max-w-sm"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Switch
                      checked={rule.enabled}
                      onCheckedChange={(checked) => updateRule(rule.id, { enabled: checked })}
                      disabled={readOnly}
                    />
                    Ativa
                  </label>
                  <Button variant="ghost" size="icon" onClick={() => removeRule(rule.id)} disabled={readOnly}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <div className="space-y-1.5">
                  <Label>Disparo</Label>
                  <Select
                    value={rule.triggerType}
                    onValueChange={(value) => updateRule(rule.id, { triggerType: value })}
                    disabled={readOnly}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="welcome">Primeira mensagem</SelectItem>
                      <SelectItem value="off_hours">Fora do horário</SelectItem>
                      <SelectItem value="keyword">Palavra-chave</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label>Palavra-chave</Label>
                  <Input
                    value={rule.keyword ?? ''}
                    onChange={(event) => updateRule(rule.id, { keyword: event.target.value })}
                    placeholder="#fechar / boleto / humano"
                    disabled={readOnly || rule.triggerType !== 'keyword'}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Fila alvo</Label>
                  <Select
                    value={rule.targetQueueStatus ?? 'none'}
                    onValueChange={(value) => updateRule(rule.id, { targetQueueStatus: value === 'none' ? null : value as ConversationAutomationRule['targetQueueStatus'] })}
                    disabled={readOnly}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Sem troca de fila" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">Sem troca</SelectItem>
                      <SelectItem value="ai">IA</SelectItem>
                      <SelectItem value="human">Humano</SelectItem>
                      <SelectItem value="follow_up">Follow-up</SelectItem>
                      <SelectItem value="closed">Encerrada</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label>Etapa do Kanban</Label>
                  <Select
                    value={rule.targetColumnId ?? 'none'}
                    onValueChange={(value) => updateRule(rule.id, { targetColumnId: value === 'none' ? null : value as KanbanColumnId })}
                    disabled={readOnly}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Não mover etapa" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">Não mover</SelectItem>
                      {COLUMN_OPTIONS.map((column) => (
                        <SelectItem key={column.value} value={column.value}>{column.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>Flow aplicado</Label>
                  <Select
                    value={rule.flowTemplateId ?? 'none'}
                    onValueChange={(value) => updateRule(rule.id, { flowTemplateId: value === 'none' ? null : value })}
                    disabled={readOnly}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Sem flow" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">Sem flow</SelectItem>
                      {flowOptions.map((flow) => (
                        <SelectItem key={flow.id} value={flow.id}>
                          {flow.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex flex-wrap items-center gap-4 rounded-xl border bg-muted/20 p-4">
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Switch
                      checked={rule.escalateToHuman}
                      onCheckedChange={(checked) => updateRule(rule.id, { escalateToHuman: checked })}
                      disabled={readOnly}
                    />
                    Escalar para humano
                  </label>
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Switch
                      checked={rule.onlyFirstMessage}
                      onCheckedChange={(checked) => updateRule(rule.id, { onlyFirstMessage: checked })}
                      disabled={readOnly}
                    />
                    Só primeira mensagem
                  </label>
                </div>
              </div>

              {rule.triggerType === 'off_hours' && (
                <div className="max-w-xs space-y-1.5">
                  <Label htmlFor={`silence-window-${rule.id}`}>Silêncio após responder (horas)</Label>
                  <Input
                    id={`silence-window-${rule.id}`}
                    type="number"
                    min={1}
                    max={168}
                    value={rule.silenceWindowHours}
                    onChange={(event) => updateRule(rule.id, { silenceWindowHours: Number(event.target.value) })}
                    disabled={readOnly}
                  />
                  <p className="text-xs text-muted-foreground">
                    A mesma regra não responderá novamente ao mesmo contato durante esta janela. Outros contatos continuam atendidos.
                  </p>
                </div>
              )}

              <div className="space-y-1.5">
                <Label>Resposta automática</Label>
                <Textarea
                  value={rule.replyText ?? ''}
                  onChange={(event) => updateRule(rule.id, { replyText: event.target.value })}
                  placeholder="Mensagem curta e alinhada ao tom da marca."
                  disabled={readOnly}
                  className="min-h-[120px]"
                />
              </div>
            </div>
          ))}

          {draft.rules.length === 0 && (
            <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
              Nenhuma regra criada ainda. Use este painel para auto-responder, aplicar flows simples e escalar para humano por palavra-chave ou horário.
            </div>
          )}
        </div>

        <div className="rounded-xl border bg-muted/20 p-4">
          <div className="flex items-start gap-3">
            <Bot className="mt-0.5 h-4 w-4 text-primary" />
            <div className="space-y-1 text-sm">
              <p className="font-medium">Boas práticas já consideradas</p>
              <p className="text-xs text-muted-foreground">
                Dentro da janela de 24h o inbox pode responder livremente. Fora dela, os fluxos devem usar template oficial aprovado da Meta. O otimizador do composer apenas sugere texto; nunca dispara automaticamente.
              </p>
              <div className="flex items-center gap-2 text-xs text-muted-foreground tabular-nums">
                <Clock3 className="h-3.5 w-3.5" />
                Horário configurado: {draft.businessHoursStart}h às {draft.businessHoursEnd}h
              </div>
            </div>
          </div>
        </div>

        <div className="flex justify-end">
          <Button onClick={() => void handleSave()} disabled={readOnly || saving}>
            <Save className="mr-2 h-4 w-4" />
            {saving ? 'Salvando...' : draft.onboardingCompleted ? 'Salvar automações' : 'Confirmar escolha de automação'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
