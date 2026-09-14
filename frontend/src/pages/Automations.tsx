import { useMemo, useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { SectionHeader } from '@/components/common/SectionHeader';
import { Plus, Zap, Play, Pause, Trash2, Copy, LayoutTemplate, Workflow, Loader2, Pencil } from 'lucide-react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from '@/components/ui/use-toast';
import { apiFetch } from '@/services/http';
import { PersonalizedFilterBuilder } from '@/components/common/PersonalizedFilterBuilder';
import {
  EMPTY_PERSONALIZED_FILTER,
  matchesPersonalizedFilter,
  type PersonalizedFilterDefinition,
  type PersonalizedFilterField,
} from '@/lib/personalized-filters';

const PRACTICE_AREA_META: Record<string, { label: string; color: string }> = {
  trabalhista:    { label: 'Trabalhista',    color: 'bg-blue-500/10 text-blue-600 border-blue-200' },
  previdenciario: { label: 'Previdenciário', color: 'bg-green-500/10 text-green-600 border-green-200' },
  bancario:       { label: 'Bancário',       color: 'bg-purple-500/10 text-purple-600 border-purple-200' },
  tributario:     { label: 'Tributário',     color: 'bg-orange-500/10 text-orange-600 border-orange-200' },
  seguros:        { label: 'Seguros',        color: 'bg-slate-500/10 text-slate-600 border-slate-200' },
  plano_saude:    { label: 'Plano de Saúde', color: 'bg-pink-500/10 text-pink-600 border-pink-200' },
  universal:      { label: 'Universal',      color: 'bg-gray-500/10 text-gray-600 border-gray-200' },
};

const TEMPLATES = [
  {
    id: 'trabalhista_sdr_completo',
    name: 'Trabalhista — SDR + Proposta Completo',
    description: 'SDR qualifica o lead via WhatsApp → Analyst analisa → Drafter redige minuta → ZapSign envia para assinar.',
    practice_area: 'trabalhista',
    node_count: 10,
  },
  {
    id: 'previdenciario_bpc_loas',
    name: 'Previdenciário — BPC/LOAS',
    description: 'Triagem de renda familiar e deficiência → Coleta de documentos → Análise INSS → Procuração.',
    practice_area: 'previdenciario',
    node_count: 10,
  },
  {
    id: 'bancario_rmc',
    name: 'Bancário — RMC',
    description: 'Triagem de crédito consignado → Extrato → Análise de abusividade → Carta de cobrança ou petição.',
    practice_area: 'bancario',
    node_count: 9,
  },
  {
    id: 'plano_saude_negativa',
    name: 'Plano de Saúde — Negativa',
    description: 'Urgência/emergência? → Notificação extrajudicial ou tutela de urgência → Procuração.',
    practice_area: 'plano_saude',
    node_count: 10,
  },
  {
    id: 'isencao_ir',
    name: 'Isenção de IR — Doença Grave',
    description: 'Triagem doença grave → Lei 7713/88 → Petição administrativa ou judicial + follow-up D+30.',
    practice_area: 'tributario',
    node_count: 10,
  },
  {
    id: 'seguros_sinistro',
    name: 'Seguros — Sinistro Negado',
    description: 'Triagem seguradora → Análise SUSEP → Reclamação ou petição → Procuração.',
    practice_area: 'seguros',
    node_count: 9,
  },
  {
    id: 'follow_up_3_etapas',
    name: 'Follow-up 3 Etapas',
    description: 'Mensagens automáticas D+1, D+3 com urgência e D+7. Pausa humana para lead frio.',
    practice_area: 'universal',
    node_count: 8,
  },
  {
    id: 'coleta_documentos',
    name: 'Coleta de Documentos',
    description: 'Envia lista de documentos → 2 lembretes automáticos → Pausa humana se não enviou.',
    practice_area: 'universal',
    node_count: 7,
  },
];

function TemplateCard({ template, onApply, applying = false }: {
  template: { id: string; name: string; description: string; practice_area: string; node_count: number };
  onApply: (id: string) => void;
  applying?: boolean;
}) {
  const meta = PRACTICE_AREA_META[template.practice_area] ?? PRACTICE_AREA_META.universal;
  return (
    <div className="bg-card border border-border rounded-xl p-4 flex flex-col gap-3 hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-base font-medium text-foreground leading-tight line-clamp-2">{template.name}</p>
        </div>
        <Badge variant="outline" className={`text-xs shrink-0 ${meta.color}`}>{meta.label}</Badge>
      </div>
      <p className="text-xs text-muted-foreground leading-relaxed flex-1">{template.description}</p>
      <div className="flex items-center justify-between">
        <Badge variant="secondary" className="text-xs tabular-nums">{template.node_count} nós</Badge>
        <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => onApply(template.id)} disabled={applying}>
          {applying ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Copy className="h-3 w-3 mr-1" />}
          {applying ? 'Aplicando...' : 'Aplicar'}
        </Button>
      </div>
    </div>
  );
}

interface ApiFlow {
  id: string;
  name: string;
  description?: string;
  practice_area?: string;
  is_template: boolean;
  is_active: boolean;
  trigger?: string;
  nodes?: unknown[];
  edges?: unknown[];
}

type FlowFilterRecord = {
  id: string;
  name: string;
  description?: string;
  practice_area?: string;
  is_active?: boolean;
  is_template?: boolean;
  node_count?: number;
  nodes?: unknown[];
};

export default function Automations() {
  const navigate = useNavigate();
  const [areaFilter, setAreaFilter] = useState<string>('all');
  const [personalizedFilter, setPersonalizedFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);
  const [applyingId, setApplyingId] = useState<string | null>(null);
  const [myFlows, setMyFlows] = useState<ApiFlow[]>([]);
  const [apiTemplates, setApiTemplates] = useState<ApiFlow[]>([]);
  const [loadingFlows, setLoadingFlows] = useState(true);
  const [loadingTemplates, setLoadingTemplates] = useState(true);

  // Carregar fluxos do workspace
  useEffect(() => {
    apiFetch<ApiFlow[]>('/api/v1/flows')
      .then(data => setMyFlows(data.filter(f => !f.is_template)))
      .catch(() => {})
      .finally(() => setLoadingFlows(false));
    apiFetch<ApiFlow[]>('/api/v1/flows/templates')
      .then(data => setApiTemplates(data))
      .catch(() => {})
      .finally(() => setLoadingTemplates(false));
  }, []);

  // Combinar templates da API com os estáticos como fallback
  const allTemplates = apiTemplates.length > 0
    ? apiTemplates.map(t => ({
        id: t.id,
        name: t.name,
        description: t.description ?? '',
        practice_area: t.practice_area ?? 'universal',
        node_count: (t.nodes as unknown[])?.length ?? 0,
      }))
    : TEMPLATES;

  const personalizedFields = useMemo<PersonalizedFilterField<FlowFilterRecord>[]>(() => [
    { key: 'name', label: 'Fluxo ou template', type: 'text' },
    { key: 'description', label: 'Descrição', type: 'text' },
    { key: 'practice_area', label: 'Área', type: 'select', options: Object.entries(PRACTICE_AREA_META).map(([value, meta]) => ({ value, label: meta.label })) },
    { key: 'is_active', label: 'Ativo', type: 'boolean' },
    { key: 'is_template', label: 'Template', type: 'boolean' },
    { key: 'node_count', label: 'Quantidade de etapas', type: 'number', getValue: (item) => item.node_count ?? item.nodes?.length ?? 0 },
  ], []);
  const filtered = allTemplates.filter((template) => (
    (areaFilter === 'all' || template.practice_area === areaFilter)
    && matchesPersonalizedFilter(template, personalizedFilter, personalizedFields)
  ));
  const filteredFlows = myFlows.filter((flow) => matchesPersonalizedFilter(flow, personalizedFilter, personalizedFields));

  const handleApply = async (id: string) => {
    setApplyingId(id);
    try {
      const applied = await apiFetch<ApiFlow>(`/api/v1/flows/templates/${id}/apply`, { method: 'POST' });
      setMyFlows(prev => [...prev, applied]);
      toast({ title: 'Template aplicado!', description: `Fluxo "${applied.name}" adicionado aos seus fluxos.` });
    } catch {
      toast({ variant: 'destructive', title: 'Erro ao aplicar template', description: 'Tente novamente.' });
    } finally {
      setApplyingId(null);
    }
  };

  const handleToggleActive = async (flow: ApiFlow) => {
    try {
      const updated = await apiFetch<ApiFlow>(`/api/v1/flows/${flow.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: !flow.is_active }),
      });
      setMyFlows(prev => prev.map(f => f.id === flow.id ? updated : f));
    } catch {
      toast({ variant: 'destructive', title: 'Erro ao atualizar fluxo' });
    }
  };

  const handleDelete = async (flowId: string) => {
    try {
      await apiFetch(`/api/v1/flows/${flowId}`, { method: 'DELETE' });
      setMyFlows(prev => prev.filter(f => f.id !== flowId));
      toast({ title: 'Fluxo removido' });
    } catch {
      toast({ variant: 'destructive', title: 'Erro ao remover fluxo' });
    }
  };

  return (
    <div className="space-y-4 lg:space-y-6 max-w-6xl">
      <SectionHeader
        title="Automações"
        description="Fluxos de atendimento inteligente — configure, ative e acompanhe"
        action={
          <Button size="sm" className="h-8 text-xs bg-brand-gradient text-white hover:opacity-90" asChild>
            <Link to="/automacoes/editor">
              <Plus className="h-3.5 w-3.5 mr-1.5" />
              Criar Fluxo
            </Link>
          </Button>
        }
      />

      <Tabs defaultValue="templates">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <TabsList className="h-8">
            <TabsTrigger value="meus" className="text-xs">
              <Workflow className="h-3.5 w-3.5 mr-1.5" />
              Meus Fluxos
            </TabsTrigger>
            <TabsTrigger value="templates" className="text-xs">
              <LayoutTemplate className="h-3.5 w-3.5 mr-1.5" />
              Biblioteca de Templates
            </TabsTrigger>
          </TabsList>
          <div className="flex items-center gap-2">
            <Select value={areaFilter} onValueChange={setAreaFilter}>
              <SelectTrigger className="h-8 text-xs w-44">
                <SelectValue placeholder="Filtrar por área" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todas as áreas</SelectItem>
                {Object.entries(PRACTICE_AREA_META).map(([key, meta]) => (
                  <SelectItem key={key} value={key}>{meta.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <PersonalizedFilterBuilder scope="flows-library" fields={personalizedFields} value={personalizedFilter} onChange={setPersonalizedFilter} compact />
          </div>
        </div>

        {/* Meus Fluxos */}
        <TabsContent value="meus" className="mt-4">
          {loadingFlows ? (
            <div className="flex justify-center py-16"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
          ) : filteredFlows.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center border-2 border-dashed border-border rounded-xl">
              <div className="w-12 h-12 rounded-xl bg-primary/10 flex items-center justify-center mb-3">
                <Zap className="h-6 w-6 text-primary" />
              </div>
              <h3 className="text-base font-medium mb-1">Nenhum fluxo criado ainda</h3>
              <p className="text-xs text-muted-foreground mb-4 max-w-xs">
                Crie um fluxo do zero ou aplique um template da biblioteca para começar rapidamente.
              </p>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" className="text-xs h-8" asChild>
                  <Link to="/automacoes/editor">Criar do zero</Link>
                </Button>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {filteredFlows.map(flow => {
                const area = flow.practice_area ?? 'universal';
                const meta = PRACTICE_AREA_META[area] ?? PRACTICE_AREA_META.universal;
                return (
                  <div key={flow.id} className="bg-card border border-border rounded-xl p-4 flex flex-col gap-3 hover:shadow-md transition-shadow">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-base font-medium text-foreground leading-tight min-w-0 flex-1 line-clamp-2">{flow.name}</p>
                      <Badge variant="outline" className={`text-xs shrink-0 ${meta.color}`}>{meta.label}</Badge>
                    </div>
                    {flow.description && <p className="text-xs text-muted-foreground leading-relaxed flex-1">{flow.description}</p>}
                    <div className="flex items-center justify-between mt-auto pt-1">
                      <Badge variant={flow.is_active ? 'default' : 'secondary'} className="text-xs">
                        {flow.is_active ? 'Ativo' : 'Pausado'}
                      </Badge>
                      <div className="flex gap-1">
                        <Button size="icon" variant="ghost" className="h-7 w-7" onClick={() => navigate(`/automacoes/editor/${flow.id}`)}>
                          <Pencil className="h-3 w-3" />
                        </Button>
                        <Button size="icon" variant="ghost" className="h-7 w-7" onClick={() => handleToggleActive(flow)}>
                          {flow.is_active ? <Pause className="h-3 w-3" /> : <Play className="h-3 w-3" />}
                        </Button>
                        <Button size="icon" variant="ghost" className="h-7 w-7 text-destructive hover:text-destructive" onClick={() => handleDelete(flow.id)}>
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </TabsContent>

        {/* Templates */}
        <TabsContent value="templates" className="mt-4">
          {loadingTemplates ? (
            <div className="flex justify-center py-16"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
          ) : (
            <>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {filtered.map(t => (
                  <TemplateCard
                    key={t.id}
                    template={t}
                    onApply={handleApply}
                    applying={applyingId === t.id}
                  />
                ))}
              </div>
              {filtered.length === 0 && (
                <div className="text-center py-12 text-sm text-muted-foreground">
                  Nenhum template encontrado para esta área.
                </div>
              )}
            </>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
