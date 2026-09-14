import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Archive, BookOpenText, CalendarClock, CheckCircle2, CircleDollarSign, Download, FileCheck2, FileUp, History, ListFilter, Megaphone, MessageCircleReply, PauseCircle, PlayCircle, Plus, RefreshCw, ShieldBan, ShieldCheck, Sparkles, StopCircle, Users, Workflow } from 'lucide-react';

import { SectionHeader } from '@/components/common/SectionHeader';
import { MetaCommercialChargePanel } from '@/components/admin/MetaCommercialChargePanel';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { FieldHelp, FieldHelpLabel } from '@/components/ui/field-help';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { useAppStore } from '@/store/app-context';
import { addAudienceListMember, analyzeMetaTemplateDraft, createAudienceList, createCampaign, downloadAudienceListCsv, fetchAudienceList, fetchAudienceLists, fetchCampaigns, fetchMetaCommercialCharges, fetchMetaTemplates, fetchOptIns, fetchSuppressions, importAudienceListCsv, launchCampaign, pauseCampaign, resumeCampaign, saveOptIn, saveSuppression, stopCampaign, syncMetaTemplates, updateAudienceList } from '@/services/campaigns';
import type { EffectiveCapability } from '@/services/platform-commercial-catalog';
import type { WorkspaceCapabilityManifestStatus } from '@/services/workspace-capabilities';
import { fetchConversationAssignees, fetchWhatsAppConnections } from '@/services/whatsapp';
import { klmApi, type KlmFlow } from '@/klm/api';
import type { AudienceList, CampaignAudienceFilter, CampaignMetaCommercialCharge, ConversationAssignee, MetaTemplate, WhatsAppCampaign, WhatsAppConnectionSummary } from '@/types';
import { PersonalizedFilterBuilder } from '@/components/common/PersonalizedFilterBuilder';
import {
  EMPTY_PERSONALIZED_FILTER,
  matchesPersonalizedFilter,
  type PersonalizedFilterDefinition,
  type PersonalizedFilterField,
} from '@/lib/personalized-filters';
import { META_PHONE_HELP, normalizeWhatsAppPhone } from '@/lib/whatsapp-phone';
import { ApiError } from '@/services/http';

const DEFAULT_FILTERS: CampaignAudienceFilter = {
  audienceListIds: [],
  includeCrmContacts: true,
  columnIds: [],
  practiceAreas: [],
  leadSources: [],
  assignedUserIds: [],
  classifications: [],
  queueStatuses: [],
  createdFrom: null,
  createdTo: null,
  hasOpenTasks: null,
  includeUnlinkedContacts: true,
  includeBlocked: false,
  search: '',
};

const DEFAULT_DRAFT = {
  whatsappConnectionId: '',
  title: '',
  description: '',
  metaTemplateId: '',
  headerParameters: [] as string[],
  bodyParameters: [] as string[],
  buttonParameters: [] as Array<{ index: number; value: string }>,
  scheduledAt: '',
  followUpFlowId: '',
  flowStartMode: 'manual' as 'manual' | 'on_delivery' | 'on_reply',
  audienceFilter: DEFAULT_FILTERS,
};

const DEFAULT_TEMPLATE_DRAFT = {
  name: '',
  categoryIntent: 'utility' as 'utility' | 'marketing',
  body: '',
  sampleVariables: '',
};

type TemplateLibraryItem = typeof DEFAULT_TEMPLATE_DRAFT & { id: string; title: string; purpose: string };

type CampaignTemplateParameter = {
  position: number;
  example: string;
};

const TEMPLATE_PLACEHOLDER = /\{\{\s*(\d+)\s*\}\}/g;

function templateExampleValues(component: Record<string, unknown>, scope: 'header' | 'body'): string[] {
  const example = component.example;
  if (!example || typeof example !== 'object') return [];
  if (scope === 'header') {
    const values = (example as { header_text?: unknown }).header_text;
    return Array.isArray(values) ? values.filter((value): value is string => typeof value === 'string') : [];
  }
  const values = (example as { body_text?: unknown }).body_text;
  if (!Array.isArray(values) || !Array.isArray(values[0])) return [];
  return values[0].filter((value): value is string => typeof value === 'string');
}

function campaignTemplateParameters(
  template: MetaTemplate | null,
  scope: 'header' | 'body',
): CampaignTemplateParameter[] {
  const component = template?.components.find((item) => String(item.type ?? '').toUpperCase() === scope.toUpperCase());
  const text = typeof component?.text === 'string' ? component.text : '';
  const positions = Array.from(text.matchAll(TEMPLATE_PLACEHOLDER), (match) => Number(match[1]));
  const uniquePositions = [...new Set(positions)].sort((left, right) => left - right);
  const examples = component ? templateExampleValues(component, scope) : [];
  return uniquePositions.map((position, index) => ({ position, example: examples[index] ?? '' }));
}

function campaignTemplateButtonParameters(template: MetaTemplate | null): Array<{ index: number; example: string }> {
  const component = template?.components.find((item) => String(item.type ?? '').toUpperCase() === 'BUTTONS');
  const buttons = Array.isArray(component?.buttons) ? component.buttons : [];
  return buttons.flatMap((button, index) => {
    if (!button || typeof button !== 'object') return [];
    const row = button as Record<string, unknown>;
    const type = String(row.type ?? '').toUpperCase();
    const url = typeof row.url === 'string' ? row.url : '';
    if (type !== 'URL' || !/\{\{\s*\d+\s*\}\}/.test(url)) return [];
    const example = Array.isArray(row.example) && typeof row.example[0] === 'string' ? row.example[0] : '';
    return [{ index, example }];
  });
}

const AREA_TEMPLATE_LIBRARY: Record<string, Omit<TemplateLibraryItem, 'id'>[]> = {
  consumer: [
    { title: 'Atualização da reclamação', purpose: 'Andamento', name: 'atualizacao_reclamacao_consumidor', categoryIntent: 'utility', body: 'Olá {{1}}, sua solicitação sobre {{2}} recebeu uma atualização: {{3}}. Consulte os detalhes em {{4}}.', sampleVariables: 'Maria, cobrança indevida, resposta recebida, link do portal' },
    { title: 'Pedido de comprovantes', purpose: 'Documentos', name: 'documentos_demanda_consumidor', categoryIntent: 'utility', body: 'Olá {{1}}, para continuar a análise da sua solicitação, precisamos de {{2}} até {{3}}. Envie pelo link {{4}}.', sampleVariables: 'Maria, nota fiscal e comprovante, 30/07, link seguro' },
  ],
  trabalhista: [
    { title: 'Documentos trabalhistas', purpose: 'Documentos', name: 'documentos_caso_trabalhista', categoryIntent: 'utility', body: 'Olá {{1}}, para avançarmos no seu caso trabalhista, precisamos de {{2}} até {{3}}. Envie pelo link {{4}}.', sampleVariables: 'João, holerites e termo de rescisão, 30/07, link seguro' },
    { title: 'Confirmação de audiência', purpose: 'Compromisso', name: 'confirmacao_audiencia_trabalhista', categoryIntent: 'utility', body: 'Olá {{1}}, sua audiência está confirmada para {{2}}, às {{3}}. As orientações estão disponíveis em {{4}}.', sampleVariables: 'João, 30/07, 14h, link do portal' },
  ],
  empresarial: [
    { title: 'Revisão de contrato', purpose: 'Andamento', name: 'atualizacao_revisao_contrato', categoryIntent: 'utility', body: 'Olá {{1}}, a revisão do contrato {{2}} avançou para a etapa {{3}}. Consulte os próximos passos em {{4}}.', sampleVariables: 'Ana, contrato de fornecimento, validação final, link do portal' },
    { title: 'Assinatura pendente', purpose: 'Documento', name: 'lembrete_assinatura_contrato', categoryIntent: 'utility', body: 'Olá {{1}}, o documento {{2}} está disponível para assinatura até {{3}} no link {{4}}.', sampleVariables: 'Ana, contrato social, 30/07, link seguro' },
  ],
  saude: [
    { title: 'Documentos de saúde', purpose: 'Documentos', name: 'documentos_demanda_saude', categoryIntent: 'utility', body: 'Olá {{1}}, para continuar a análise da sua solicitação, precisamos de {{2}} até {{3}}. Envie pelo link {{4}}.', sampleVariables: 'Maria, relatório médico e negativa, 30/07, link seguro' },
    { title: 'Atualização da solicitação', purpose: 'Andamento', name: 'atualizacao_solicitacao_saude', categoryIntent: 'utility', body: 'Olá {{1}}, sua solicitação referente a {{2}} recebeu uma atualização: {{3}}. Consulte os detalhes em {{4}}.', sampleVariables: 'Maria, tratamento solicitado, documento recebido, link do portal' },
  ],
  erro_medico: [
    { title: 'Solicitação de prontuário', purpose: 'Documentos', name: 'solicitacao_prontuario_medico', categoryIntent: 'utility', body: 'Olá {{1}}, para analisar o atendimento realizado em {{2}}, precisamos do prontuário e dos exames de {{3}}. Envie em {{4}}.', sampleVariables: 'Maria, Hospital Central, 15/06, link seguro' },
    { title: 'Avaliação técnica atualizada', purpose: 'Andamento', name: 'atualizacao_avaliacao_erro_medico', categoryIntent: 'utility', body: 'Olá {{1}}, a avaliação técnica sobre {{2}} avançou para {{3}}. Consulte a atualização em {{4}}.', sampleVariables: 'Maria, atendimento hospitalar, revisão dos documentos, link do portal' },
  ],
  bancario: [
    { title: 'Documentos da operação bancária', purpose: 'Documentos', name: 'documentos_operacao_bancaria', categoryIntent: 'utility', body: 'Olá {{1}}, para analisar a operação {{2}}, precisamos de {{3}} até {{4}}. Envie em {{5}}.', sampleVariables: 'Maria, contrato 1234, contrato e extratos, 30/07, link seguro' },
    { title: 'Atualização da contestação bancária', purpose: 'Andamento', name: 'atualizacao_contestacao_bancaria', categoryIntent: 'utility', body: 'Olá {{1}}, sua contestação sobre {{2}} recebeu a atualização {{3}}. Consulte os detalhes em {{4}}.', sampleVariables: 'Maria, cobrança não reconhecida, resposta da instituição, link do portal' },
  ],
  previdenciario: [
    { title: 'Documentos previdenciários', purpose: 'Documentos', name: 'documentos_beneficio_previdenciario', categoryIntent: 'utility', body: 'Olá {{1}}, para analisar o benefício {{2}}, precisamos de {{3}} até {{4}}. Envie em {{5}}.', sampleVariables: 'Maria, auxílio por incapacidade, CNIS e laudos, 30/07, link seguro' },
    { title: 'Atualização do benefício', purpose: 'Andamento', name: 'atualizacao_beneficio_previdenciario', categoryIntent: 'utility', body: 'Olá {{1}}, o pedido do benefício {{2}} recebeu a atualização {{3}} em {{4}}. Consulte em {{5}}.', sampleVariables: 'Maria, aposentadoria, exigência aberta, 28/07, link do portal' },
  ],
  civel: [
    { title: 'Documentos da demanda cível', purpose: 'Documentos', name: 'documentos_demanda_civel', categoryIntent: 'utility', body: 'Olá {{1}}, para continuar a demanda sobre {{2}}, precisamos de {{3}} até {{4}}. Envie em {{5}}.', sampleVariables: 'Maria, descumprimento contratual, contrato e conversas, 30/07, link seguro' },
    { title: 'Movimentação da demanda cível', purpose: 'Andamento', name: 'movimentacao_demanda_civel', categoryIntent: 'utility', body: 'Olá {{1}}, houve uma nova movimentação na demanda {{2}}: {{3}}. Consulte os detalhes em {{4}}.', sampleVariables: 'Maria, processo 1234, despacho publicado, link do portal' },
  ],
  consumidor: [
    { title: 'Atualização da reclamação', purpose: 'Andamento', name: 'atualizacao_reclamacao_consumidor', categoryIntent: 'utility', body: 'Olá {{1}}, sua reclamação sobre {{2}} recebeu a atualização {{3}}. Consulte em {{4}}.', sampleVariables: 'Maria, cobrança indevida, resposta do fornecedor, link do portal' },
    { title: 'Comprovantes da relação de consumo', purpose: 'Documentos', name: 'comprovantes_relacao_consumo', categoryIntent: 'utility', body: 'Olá {{1}}, para analisar a compra ou serviço {{2}}, precisamos de {{3}} até {{4}}. Envie em {{5}}.', sampleVariables: 'Maria, pedido 1234, nota fiscal e conversas, 30/07, link seguro' },
  ],
  digital: [
    { title: 'Evidências do incidente digital', purpose: 'Documentos', name: 'evidencias_incidente_digital', categoryIntent: 'utility', body: 'Olá {{1}}, para analisar o incidente {{2}}, precisamos de {{3}} até {{4}}. Envie em {{5}}.', sampleVariables: 'Maria, invasão da conta, capturas e protocolos, 30/07, link seguro' },
    { title: 'Atualização do incidente digital', purpose: 'Andamento', name: 'atualizacao_incidente_digital', categoryIntent: 'utility', body: 'Olá {{1}}, o tratamento do incidente {{2}} avançou para {{3}}. Veja as medidas registradas em {{4}}.', sampleVariables: 'Maria, acesso indevido, preservação de evidências, link do portal' },
  ],
  propriedade_intelectual: [
    { title: 'Documentos de marca ou obra', purpose: 'Documentos', name: 'documentos_propriedade_intelectual', categoryIntent: 'utility', body: 'Olá {{1}}, para analisar {{2}}, precisamos de {{3}} até {{4}}. Envie em {{5}}.', sampleVariables: 'Maria, registro da marca Exemplo, certificados e arquivos, 30/07, link seguro' },
    { title: 'Atualização do registro', purpose: 'Andamento', name: 'atualizacao_registro_propriedade_intelectual', categoryIntent: 'utility', body: 'Olá {{1}}, o registro de {{2}} recebeu a atualização {{3}} em {{4}}. Consulte em {{5}}.', sampleVariables: 'Maria, marca Exemplo, exame formal concluído, 28/07, link do portal' },
  ],
  seguros: [
    { title: 'Documentos do sinistro', purpose: 'Documentos', name: 'documentos_sinistro_seguro', categoryIntent: 'utility', body: 'Olá {{1}}, para analisar o sinistro {{2}}, precisamos de {{3}} até {{4}}. Envie em {{5}}.', sampleVariables: 'Maria, sinistro 1234, apólice e comprovantes, 30/07, link seguro' },
    { title: 'Atualização do sinistro', purpose: 'Andamento', name: 'atualizacao_sinistro_seguro', categoryIntent: 'utility', body: 'Olá {{1}}, o sinistro {{2}} recebeu a atualização {{3}}. Consulte os próximos passos em {{4}}.', sampleVariables: 'Maria, sinistro 1234, análise concluída, link do portal' },
  ],
  tributario: [
    { title: 'Documentos tributários', purpose: 'Documentos', name: 'documentos_demanda_tributaria', categoryIntent: 'utility', body: 'Olá {{1}}, para analisar o tributo {{2}} no período {{3}}, precisamos de {{4}}. Envie em {{5}}.', sampleVariables: 'Maria, imposto de renda, 2025, declarações e informes, link seguro' },
    { title: 'Atualização da demanda tributária', purpose: 'Andamento', name: 'atualizacao_demanda_tributaria', categoryIntent: 'utility', body: 'Olá {{1}}, a demanda tributária sobre {{2}} recebeu a atualização {{3}} em {{4}}. Consulte em {{5}}.', sampleVariables: 'Maria, restituição, protocolo processado, 28/07, link do portal' },
  ],
  familia: [
    { title: 'Documentos da demanda familiar', purpose: 'Documentos', name: 'documentos_demanda_familia', categoryIntent: 'utility', body: 'Olá {{1}}, para continuar a demanda sobre {{2}}, precisamos de {{3}} até {{4}}. Envie em {{5}}.', sampleVariables: 'Maria, guarda, certidões e comprovantes, 30/07, link seguro' },
    { title: 'Atualização da demanda familiar', purpose: 'Andamento', name: 'atualizacao_demanda_familia', categoryIntent: 'utility', body: 'Olá {{1}}, a demanda de família sobre {{2}} recebeu a atualização {{3}}. Consulte com privacidade em {{4}}.', sampleVariables: 'Maria, guarda, audiência designada, link do portal' },
  ],
  imobiliario: [
    { title: 'Documentos do imóvel', purpose: 'Documentos', name: 'documentos_demanda_imobiliaria', categoryIntent: 'utility', body: 'Olá {{1}}, para analisar o imóvel {{2}}, precisamos de {{3}} até {{4}}. Envie em {{5}}.', sampleVariables: 'Maria, matrícula 1234, matrícula e contrato, 30/07, link seguro' },
    { title: 'Atualização da demanda imobiliária', purpose: 'Andamento', name: 'atualizacao_demanda_imobiliaria', categoryIntent: 'utility', body: 'Olá {{1}}, a demanda sobre o imóvel {{2}} recebeu a atualização {{3}}. Consulte em {{4}}.', sampleVariables: 'Maria, unidade 101, análise documental concluída, link do portal' },
  ],
};

const GENERIC_AREA_TEMPLATES: Omit<TemplateLibraryItem, 'id'>[] = [
  { title: 'Solicitação de documentos', purpose: 'Documentos', name: 'solicitacao_documentos', categoryIntent: 'utility', body: 'Olá {{1}}, para continuar o atendimento, precisamos de {{2}} até {{3}}. Envie pelo link {{4}}.', sampleVariables: 'Maria, documentos necessários, 30/07, link seguro' },
  { title: 'Atualização do caso', purpose: 'Andamento', name: 'atualizacao_andamento_caso', categoryIntent: 'utility', body: 'Olá {{1}}, seu caso recebeu uma atualização: {{2}}. Consulte os próximos passos em {{3}}.', sampleVariables: 'Maria, nova movimentação, link do portal' },
  { title: 'Documentos recebidos', purpose: 'Documentos', name: 'confirmacao_documentos_recebidos', categoryIntent: 'utility', body: 'Olá {{1}}, confirmamos o recebimento de {{2}} em {{3}}. Acompanhe a análise em {{4}}.', sampleVariables: 'Maria, comprovantes solicitados, 28/07, link do portal' },
  { title: 'Documento pendente', purpose: 'Documentos', name: 'lembrete_documento_pendente', categoryIntent: 'utility', body: 'Olá {{1}}, ainda falta o documento {{2}} para continuarmos seu atendimento. Envie até {{3}} por {{4}}.', sampleVariables: 'Maria, comprovante de residência, 30/07, link seguro' },
  { title: 'Documento inválido', purpose: 'Documentos', name: 'correcao_documento_invalido', categoryIntent: 'utility', body: 'Olá {{1}}, não foi possível validar {{2}} pelo motivo {{3}}. Envie uma nova versão em {{4}}.', sampleVariables: 'Maria, documento de identidade, imagem ilegível, link seguro' },
  { title: 'Análise documental concluída', purpose: 'Documentos', name: 'analise_documental_concluida', categoryIntent: 'utility', body: 'Olá {{1}}, concluímos a análise dos documentos de {{2}}. O resultado e os próximos passos estão em {{3}}.', sampleVariables: 'Maria, atendimento 1234, link do portal' },
  { title: 'Prazo próximo', purpose: 'Prazo', name: 'lembrete_prazo_proximo', categoryIntent: 'utility', body: 'Olá {{1}}, lembramos que o prazo para {{2}} termina em {{3}}. Consulte as orientações em {{4}}.', sampleVariables: 'Maria, envio dos documentos, 30/07, link do portal' },
  { title: 'Prazo alterado', purpose: 'Prazo', name: 'comunicacao_prazo_alterado', categoryIntent: 'utility', body: 'Olá {{1}}, o prazo referente a {{2}} foi alterado de {{3}} para {{4}}. Veja os detalhes em {{5}}.', sampleVariables: 'Maria, entrega do formulário, 30/07, 02/08, link do portal' },
  { title: 'Prazo confirmado', purpose: 'Prazo', name: 'confirmacao_prazo', categoryIntent: 'utility', body: 'Olá {{1}}, confirmamos o prazo de {{2}} para {{3}}. As instruções estão disponíveis em {{4}}.', sampleVariables: 'Maria, envio da manifestação, 30/07, link do portal' },
  { title: 'Reunião confirmada', purpose: 'Compromisso', name: 'confirmacao_reuniao', categoryIntent: 'utility', body: 'Olá {{1}}, sua reunião está confirmada para {{2}}, às {{3}}. Acesse por {{4}}.', sampleVariables: 'Maria, 30/07, 14h, link da reunião' },
  { title: 'Lembrete de reunião', purpose: 'Compromisso', name: 'lembrete_reuniao', categoryIntent: 'utility', body: 'Olá {{1}}, sua reunião sobre {{2}} será em {{3}}, às {{4}}. Confira as orientações em {{5}}.', sampleVariables: 'Maria, atualização do caso, 30/07, 14h, link do portal' },
  { title: 'Reunião reagendada', purpose: 'Compromisso', name: 'reagendamento_reuniao', categoryIntent: 'utility', body: 'Olá {{1}}, sua reunião foi reagendada para {{2}}, às {{3}}. Confirme ou solicite ajuste em {{4}}.', sampleVariables: 'Maria, 31/07, 15h, link do portal' },
  { title: 'Audiência confirmada', purpose: 'Compromisso', name: 'confirmacao_audiencia', categoryIntent: 'utility', body: 'Olá {{1}}, sua audiência está confirmada para {{2}}, às {{3}}. Consulte local e orientações em {{4}}.', sampleVariables: 'Maria, 05/08, 10h, link do portal' },
  { title: 'Lembrete de audiência', purpose: 'Compromisso', name: 'lembrete_audiencia', categoryIntent: 'utility', body: 'Olá {{1}}, lembramos que sua audiência será em {{2}}, às {{3}}. Prepare-se conforme {{4}}.', sampleVariables: 'Maria, 05/08, 10h, link de orientações' },
  { title: 'Atendimento agendado', purpose: 'Compromisso', name: 'confirmacao_atendimento', categoryIntent: 'utility', body: 'Olá {{1}}, seu atendimento foi agendado para {{2}}, às {{3}}, pelo canal {{4}}.', sampleVariables: 'Maria, 30/07, 14h, videoconferência' },
  { title: 'Protocolo criado', purpose: 'Andamento', name: 'confirmacao_protocolo', categoryIntent: 'utility', body: 'Olá {{1}}, seu atendimento foi registrado sob o protocolo {{2}} em {{3}}. Acompanhe em {{4}}.', sampleVariables: 'Maria, 2026-00123, 28/07, link do portal' },
  { title: 'Nova movimentação', purpose: 'Andamento', name: 'nova_movimentacao_caso', categoryIntent: 'utility', body: 'Olá {{1}}, houve uma nova movimentação em {{2}}: {{3}}. Consulte os detalhes em {{4}}.', sampleVariables: 'Maria, caso 1234, documento juntado, link do portal' },
  { title: 'Etapa concluída', purpose: 'Andamento', name: 'etapa_concluida', categoryIntent: 'utility', body: 'Olá {{1}}, a etapa {{2}} do seu atendimento foi concluída em {{3}}. O próximo passo é {{4}}.', sampleVariables: 'Maria, análise inicial, 28/07, aguardar retorno' },
  { title: 'Caso em análise', purpose: 'Andamento', name: 'caso_em_analise', categoryIntent: 'utility', body: 'Olá {{1}}, seu caso {{2}} está em análise pela equipe responsável. A previsão de atualização é {{3}}.', sampleVariables: 'Maria, 1234, 02/08' },
  { title: 'Caso encerrado', purpose: 'Andamento', name: 'comunicacao_encerramento_caso', categoryIntent: 'utility', body: 'Olá {{1}}, o atendimento {{2}} foi encerrado em {{3}}. Consulte o resumo e os documentos em {{4}}.', sampleVariables: 'Maria, 1234, 28/07, link do portal' },
  { title: 'Responsável alterado', purpose: 'Atendimento', name: 'alteracao_responsavel', categoryIntent: 'utility', body: 'Olá {{1}}, o responsável pelo atendimento {{2}} agora é {{3}}. Seus canais de contato permanecem em {{4}}.', sampleVariables: 'Maria, 1234, Ana Souza, link do portal' },
  { title: 'Resposta disponível', purpose: 'Atendimento', name: 'resposta_disponivel', categoryIntent: 'utility', body: 'Olá {{1}}, há uma nova resposta disponível no atendimento {{2}}. Consulte-a em {{3}}.', sampleVariables: 'Maria, 1234, link do portal' },
  { title: 'Confirmação de solicitação', purpose: 'Atendimento', name: 'confirmacao_solicitacao', categoryIntent: 'utility', body: 'Olá {{1}}, recebemos sua solicitação sobre {{2}} em {{3}}. O protocolo é {{4}}.', sampleVariables: 'Maria, atualização cadastral, 28/07, 2026-00123' },
  { title: 'Dados cadastrais pendentes', purpose: 'Cadastro', name: 'dados_cadastrais_pendentes', categoryIntent: 'utility', body: 'Olá {{1}}, precisamos confirmar {{2}} para atualizar seu cadastro. Responda até {{3}} em {{4}}.', sampleVariables: 'Maria, endereço e telefone, 30/07, link seguro' },
  { title: 'Cadastro atualizado', purpose: 'Cadastro', name: 'confirmacao_cadastro_atualizado', categoryIntent: 'utility', body: 'Olá {{1}}, seus dados cadastrais foram atualizados em {{2}}. Confira as informações em {{3}}.', sampleVariables: 'Maria, 28/07, link do portal' },
  { title: 'Pagamento identificado', purpose: 'Financeiro', name: 'confirmacao_pagamento', categoryIntent: 'utility', body: 'Olá {{1}}, identificamos o pagamento referente a {{2}}, realizado em {{3}}. Consulte o comprovante em {{4}}.', sampleVariables: 'Maria, parcela de julho, 28/07, link do portal' },
  { title: 'Pagamento pendente', purpose: 'Financeiro', name: 'lembrete_pagamento_pendente', categoryIntent: 'utility', body: 'Olá {{1}}, o pagamento de {{2}}, com vencimento em {{3}}, ainda consta como pendente. Consulte em {{4}}.', sampleVariables: 'Maria, parcela de julho, 30/07, link do portal' },
  { title: 'Boleto disponível', purpose: 'Financeiro', name: 'boleto_disponivel', categoryIntent: 'utility', body: 'Olá {{1}}, o boleto referente a {{2}}, com vencimento em {{3}}, está disponível em {{4}}.', sampleVariables: 'Maria, parcela de julho, 30/07, link seguro' },
  { title: 'Assinatura solicitada', purpose: 'Documento', name: 'solicitacao_assinatura', categoryIntent: 'utility', body: 'Olá {{1}}, o documento {{2}} está disponível para assinatura até {{3}} em {{4}}.', sampleVariables: 'Maria, contrato de atendimento, 30/07, link seguro' },
  { title: 'Assinatura confirmada', purpose: 'Documento', name: 'confirmacao_assinatura', categoryIntent: 'utility', body: 'Olá {{1}}, confirmamos a assinatura do documento {{2}} em {{3}}. Sua cópia está em {{4}}.', sampleVariables: 'Maria, contrato de atendimento, 28/07, link do portal' },
];

const COLUMN_OPTIONS = [
  { value: 'entrada', label: 'Entrada' },
  { value: 'qualificacao', label: 'Qualificação' },
  { value: 'proposta_fechamento', label: 'Proposta & fechamento' },
  { value: 'coleta_dados', label: 'Coleta de dados' },
];

const CLASSIFICATION_OPTIONS = ['Premium', 'Forte', 'Bom', 'Frio'];
const QUEUE_OPTIONS = ['ai', 'human', 'follow_up', 'closed'];

// Categoria Meta: marketing custa ~7x mais que utility/authentication por conversa.
const CATEGORY_LABEL: Record<string, string> = {
  marketing: 'marketing (~7x)',
  utility: 'utility',
  authentication: 'auth',
};
const CATEGORY_BADGE_CLASS: Record<string, string> = {
  marketing: 'border-amber-300 text-amber-800 dark:border-amber-800 dark:text-amber-200',
  utility: 'border-sky-300 text-sky-800 dark:border-sky-800 dark:text-sky-200',
  authentication: 'border-violet-300 text-violet-800 dark:border-violet-800 dark:text-violet-200',
};
const QUALITY_BADGE_CLASS: Record<string, string> = {
  GREEN: 'border-emerald-300 text-emerald-800 dark:border-emerald-800 dark:text-emerald-200',
  YELLOW: 'border-amber-300 text-amber-800 dark:border-amber-800 dark:text-amber-200',
  RED: 'border-red-300 text-red-800 dark:border-red-800 dark:text-red-200',
  green: 'border-emerald-300 text-emerald-800 dark:border-emerald-800 dark:text-emerald-200',
  yellow: 'border-amber-300 text-amber-800 dark:border-amber-800 dark:text-amber-200',
  red: 'border-red-300 text-red-800 dark:border-red-800 dark:text-red-200',
};

const CAMPAIGN_FIELD_HELP = {
  csv: {
    label: 'Arquivo CSV',
    description: 'É uma planilha salva em formato CSV UTF-8. Cada linha representa um contato e a primeira linha traz os nomes das colunas.',
    whereToFind: 'Na sua planilha, use “Salvar como” ou “Baixar” e escolha CSV UTF-8. Inclua ao menos a coluna telefone.',
  },
  outgoingNumber: {
    label: 'Número oficial de saída',
    description: 'É a conexão do WhatsApp Business usada para enviar esta campanha pela API oficial.',
    whereToFind: 'Em Configurações > Integrações > WhatsApp. Cadastre e valide uma conexão Cloud API ou Coexistence.',
  },
  metaTemplate: {
    label: 'Template aprovado da Meta',
    description: 'É a mensagem-modelo que a Meta já autorizou para iniciar conversas fora da janela de atendimento.',
    whereToFind: 'No Gerenciador do WhatsApp da Meta, em Modelos de mensagem. Depois, volte aqui e use “Sincronizar templates”.',
  },
  templateName: {
    label: 'Nome do modelo',
    description: 'Identifica o template que será criado no WhatsApp Manager da Meta. Use um nome claro, curto e sem dados de cliente.',
    whereToFind: 'Defina um nome operacional, como atualizacao_prazo_documentos ou lembrete_reuniao.',
  },
  templateCategoryIntent: {
    label: 'Categoria pretendida',
    description: 'Utility costuma cobrir atualizações transacionais e solicitações esperadas. Marketing cobre promoção, captação, remarketing e ofertas.',
    whereToFind: 'Escolha pelo objetivo real da mensagem, não pelo custo desejado.',
  },
  templateBody: {
    label: 'Texto do modelo',
    description: 'É o corpo que será submetido à Meta. Evite promessas, urgência artificial, preço, desconto ou linguagem promocional quando a intenção for utility.',
    whereToFind: 'Escreva a versão com variáveis como {{1}} e mantenha exemplos reais separados.',
  },
  templateVariables: {
    label: 'Exemplos das variáveis',
    description: 'Mostra valores de exemplo para a Meta entender o contexto das variáveis do template.',
    whereToFind: 'Use exemplos seguros e genéricos, separados por vírgula, como Maria, 30/07, link do portal.',
  },
  scheduling: {
    label: 'Agendamento',
    description: 'Define a data e a hora em que o disparo poderá começar, em vez de ser iniciado manualmente agora.',
    whereToFind: 'Escolha no calendário deste campo e confira o fuso horário configurado para o workspace.',
  },
  followUpFlow: {
    label: 'Flow após o disparo',
    description: 'É uma sequência automática que pode continuar o atendimento depois que a campanha for enviada.',
    whereToFind: 'Em Automação > Flows. Somente flows ativos aparecem nesta lista.',
  },
  flowStart: {
    label: 'Início do flow',
    description: 'Escolhe se o flow começa manualmente, quando a mensagem for entregue ou quando a pessoa responder.',
    whereToFind: 'Escolha aqui depois de selecionar um flow. Em caso de dúvida, mantenha “Manual”.',
  },
  headerParameters: {
    label: 'Parâmetros do cabeçalho',
    description: 'Informe um valor em cada campo para substituir as variáveis do cabeçalho do template.',
    whereToFind: 'Os campos são derivados do cabeçalho aprovado e exibem o exemplo cadastrado pela Meta quando ele existe.',
  },
  bodyParameters: {
    label: 'Parâmetros da mensagem',
    description: 'Informe um valor em cada campo para substituir as variáveis do texto principal do template.',
    whereToFind: 'Os campos são derivados do corpo aprovado e exibem o exemplo cadastrado pela Meta quando ele existe.',
  },
  audienceSources: {
    label: 'Fontes da audiência',
    description: 'Define se os destinatários virão dos contatos do CRM, de listas importadas ou de ambos. Repetidos são reunidos pelo telefone.',
    whereToFind: 'Os contatos ficam no CRM; as listas reutilizáveis são criadas na seção “Listas de audiência” desta página.',
  },
  audienceSearch: {
    label: 'Busca da audiência',
    description: 'Restringe os contatos do CRM por nome, telefone ou demanda. No sheet de Conversas, começa com o contato atual.',
    whereToFind: 'Use o nome ou telefone exibido na conversa ou no card do CRM. Apague o texto para não aplicar esta restrição.',
  },
  funnelStage: {
    label: 'Etapa principal',
    description: 'Filtra a audiência pela etapa atual do contato no funil comercial.',
    whereToFind: 'No card do contato, em Funil ou CRM.',
  },
  classification: {
    label: 'Classificação',
    description: 'Filtra contatos pela classificação comercial usada pelo escritório, como Premium, Forte, Bom ou Frio.',
    whereToFind: 'No card do contato, em CRM > Classificação.',
  },
  assignee: {
    label: 'Responsável',
    description: 'Envia somente para contatos atribuídos à pessoa escolhida.',
    whereToFind: 'O responsável aparece no cabeçalho da conversa e no card do CRM.',
  },
  queue: {
    label: 'Fila operacional',
    description: 'Filtra pelo estado de atendimento: IA, humano, follow-up ou encerrado.',
    whereToFind: 'A fila atual aparece na conversa e pode ser alterada pelas regras de atendimento.',
  },
  unlinkedContacts: {
    label: 'Contatos sem card',
    description: 'Inclui pessoas que existem nos canais de atendimento, mas ainda não possuem card vinculado no CRM.',
    whereToFind: 'Esses contatos aparecem em Conversas, mesmo quando ainda não há um caso ou card associado.',
  },
  blockedContacts: {
    label: 'Contatos bloqueados',
    description: 'Inclui na prévia contatos marcados como bloqueados. Regras de consentimento e supressão ainda podem impedir o envio.',
    whereToFind: 'O bloqueio aparece no cadastro do contato e no histórico operacional. Use esta opção apenas para conferência.',
  },
  advancedCampaignFilter: {
    label: 'Filtro avançado de campanhas',
    description: 'Combina condições para localizar campanhas antigas por status, datas, destinatários, entregas ou flow.',
    whereToFind: 'Use o botão de filtros ao lado das ações de Conversas no histórico de campanhas.',
  },
  consentStatus: {
    label: 'Status do consentimento',
    description: 'Opt-in registra que a pessoa aceitou receber mensagens; opt-out registra que ela retirou essa autorização.',
    whereToFind: 'Use o consentimento registrado em formulário, contrato ou atendimento. Não presuma autorização.',
  },
  consentSource: {
    label: 'Origem do consentimento',
    description: 'É uma anotação curta sobre onde a autorização foi obtida, para facilitar conferência e auditoria.',
    whereToFind: 'Informe a origem real, como formulário do site, contrato ou atendimento, conforme a evidência disponível.',
  },
  suppressionReason: {
    label: 'Motivo da supressão',
    description: 'Explica por que o telefone não deve participar de novos disparos, como pedido do cliente ou bloqueio interno.',
    whereToFind: 'Consulte o pedido da pessoa ou a ocorrência registrada no histórico de atendimento.',
  },
  suppressionActive: {
    label: 'Supressão ativa',
    description: 'Quando ativa, impede que o telefone entre em novas campanhas.',
    whereToFind: 'Mantenha ativa enquanto o bloqueio ou pedido de não contato continuar válido.',
  },
} as const;

type CampaignsProps = {
  embedded?: boolean;
  contextThreadId?: string | null;
  contextMatterId?: string | null;
  contextContactName?: string | null;
  onConversationFilter?: (filter: 'todos' | 'com_disparo' | 'disparo_sem_retorno', campaignId?: string) => void;
};

type ContactsCapacityPresentation = {
  statusLabel: string;
  detail: string;
  materializationAllowed: boolean;
  blockingReason: string | null;
  launchWarning: string | null;
};

function getContactsCapacityPresentation(
  manifestStatus: WorkspaceCapabilityManifestStatus,
  capability: EffectiveCapability | null,
): ContactsCapacityPresentation {
  if (manifestStatus === 'loading' || manifestStatus === 'idle') {
    return {
      statusLabel: 'Verificando',
      detail: 'A capacidade contratada está sendo conferida.',
      materializationAllowed: false,
      blockingReason: 'Aguarde a validação da capacidade de contatos antes de lançar.',
      launchWarning: null,
    };
  }
  if (manifestStatus === 'not_configured') {
    return {
      statusLabel: 'Contrato não configurado',
      detail: 'O contrato canônico da conta ainda não define a capacidade de contatos.',
      materializationAllowed: false,
      blockingReason: 'Configure o contrato ou solicite um upgrade antes de criar novos contatos.',
      launchWarning: null,
    };
  }
  if (manifestStatus === 'forbidden') {
    return {
      statusLabel: 'Consulta não autorizada',
      detail: 'Seu perfil não pode consultar a capacidade comercial desta conta.',
      materializationAllowed: false,
      blockingReason: 'A capacidade de contatos não pôde ser autorizada para este lançamento.',
      launchWarning: null,
    };
  }
  if (manifestStatus === 'unavailable') {
    return {
      statusLabel: 'Capacidade indisponível',
      detail: 'O manifesto comercial não pôde ser consultado agora.',
      materializationAllowed: false,
      blockingReason: 'Tente novamente quando a capacidade de contatos estiver disponível.',
      launchWarning: null,
    };
  }
  if (!capability || !capability.contracted || !capability.enabled) {
    const isInvalidConfiguration = capability?.denialReason === 'not_configured';
    return {
      statusLabel: isInvalidConfiguration ? 'Limite não configurado' : 'Não contratada',
      detail: isInvalidConfiguration
        ? 'A capacidade existe, mas não possui um limite finito válido.'
        : 'O plano atual não inclui a criação de novos contatos.',
      materializationAllowed: false,
      blockingReason: isInvalidConfiguration
        ? 'Defina um limite finito ou solicite um upgrade antes de criar novos contatos.'
        : 'Solicite um upgrade ou bônus de capacidade antes de criar novos contatos.',
      launchWarning: null,
    };
  }
  if (!capability.permitted && capability.denialReason === 'quota_exhausted') {
    return {
      statusLabel: 'Limite atingido',
      detail: 'O estoque para novos contatos acabou. Campanhas compostas somente por contatos existentes ainda podem ser lançadas.',
      materializationAllowed: false,
      blockingReason: null,
      launchWarning: 'O estoque de contatos está esgotado. O backend fará o preflight e só aceitará o lançamento se nenhum contato novo precisar ser criado.',
    };
  }
  if (!capability.permitted) {
    const byReason: Record<NonNullable<EffectiveCapability['denialReason']>, {
      label: string;
      detail: string;
      blockingReason: string;
    }> = {
      quota_exhausted: {
        label: 'Limite atingido',
        detail: 'A conta consumiu todo o estoque disponível de contatos.',
        blockingReason: 'O backend deve revalidar o estoque de contatos.',
      },
      suspended: {
        label: 'Conta suspensa',
        detail: 'O contrato não está operacional para criar novos contatos.',
        blockingReason: 'Regularize o contrato da conta antes de criar novos contatos.',
      },
      permission_denied: {
        label: 'Capacidade revogada',
        detail: 'Um override da conta bloqueia a criação de novos contatos.',
        blockingReason: 'A capacidade de contatos foi revogada para esta conta.',
      },
      not_configured: {
        label: 'Limite não configurado',
        detail: 'A capacidade não possui um limite finito válido.',
        blockingReason: 'Defina um limite finito antes de criar novos contatos.',
      },
      not_contracted: {
        label: 'Não contratada',
        detail: 'O plano atual não inclui a criação de novos contatos.',
        blockingReason: 'Solicite um upgrade antes de criar novos contatos.',
      },
    };
    const reason = capability.denialReason
      ? byReason[capability.denialReason]
      : {
          label: 'Criação bloqueada',
          detail: 'O backend não autorizou novas materializações de contato.',
          blockingReason: 'A capacidade de contatos não está disponível para este lançamento.',
        };
    return {
      statusLabel: reason.label,
      detail: reason.detail,
      materializationAllowed: false,
      blockingReason: reason.blockingReason,
      launchWarning: null,
    };
  }
  return {
    statusLabel: 'Disponível',
    detail: 'O backend revalidará o estoque quando houver materialização de contato.',
    materializationAllowed: true,
    blockingReason: null,
    launchWarning: null,
  };
}

class ContactCapacityClientBlockError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ContactCapacityClientBlockError';
  }
}

type CampaignActionError = { code: string; message: string };

function describeCampaignActionError(error: unknown): CampaignActionError {
  if (error instanceof ApiError) {
    const detail = error.detail && typeof error.detail === 'object' ? error.detail : null;
    const code = detail && 'code' in detail && typeof detail.code === 'string'
      ? detail.code
      : `HTTP_${error.status}`;
    return { code, message: error.message };
  }
  return {
    code: 'CAMPAIGN_ACTION_FAILED',
    message: error instanceof Error ? error.message : 'A operação foi abortada antes de concluir.',
  };
}

function formatContactCount(value: number | null): string {
  return value == null ? 'Não disponível' : new Intl.NumberFormat('pt-BR').format(value);
}

export default function Campaigns({
  embedded = false,
  contextThreadId = null,
  contextMatterId = null,
  contextContactName = null,
  onConversationFilter,
}: CampaignsProps = {}) {
  const { toast } = useToast();
  const appStore = useAppStore();
  const { can } = appStore;
  const capabilityManifest = appStore.capabilityManifest ?? null;
  const capabilityManifestStatus = appStore.capabilityManifestStatus ?? 'unavailable';
  const contactsCapability = capabilityManifestStatus === 'ready'
    ? appStore.getCapability?.('contacts') ?? null
    : null;
  const contactsCapacity = getContactsCapacityPresentation(
    capabilityManifestStatus,
    contactsCapability,
  );
  const queryClient = useQueryClient();
  const csvInputRef = useRef<HTMLInputElement>(null);
  const listsSectionRef = useRef<HTMLElement>(null);
  const newCampaignSectionRef = useRef<HTMLDivElement>(null);
  const campaignHistorySectionRef = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState(() => ({
    ...DEFAULT_DRAFT,
    audienceFilter: {
      ...DEFAULT_FILTERS,
      search: embedded ? contextContactName?.trim() ?? '' : '',
    },
  }));
  const [templates, setTemplates] = useState<MetaTemplate[]>([]);
  const [assignees, setAssignees] = useState<ConversationAssignee[]>([]);
  const [connections, setConnections] = useState<WhatsAppConnectionSummary[]>([]);
  const [optInForm, setOptInForm] = useState({ phone: '', status: 'opted_in', source: 'manual' });
  const [suppressionForm, setSuppressionForm] = useState({ phone: '', reason: 'manual', isActive: true });
  const [listForm, setListForm] = useState({ name: '', description: '' });
  const [templateDraft, setTemplateDraft] = useState(DEFAULT_TEMPLATE_DRAFT);
  const [templateAreaKey, setTemplateAreaKey] = useState('');
  const [templateLibrarySearch, setTemplateLibrarySearch] = useState('');
  const [memberForm, setMemberForm] = useState({ phone: '', name: '', email: '' });
  const normalizedMemberPhone = normalizeWhatsAppPhone(memberForm.phone);
  const normalizedOptInPhone = normalizeWhatsAppPhone(optInForm.phone);
  const normalizedSuppressionPhone = normalizeWhatsAppPhone(suppressionForm.phone);
  const [selectedListId, setSelectedListId] = useState<string | null>(null);
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [campaignActionErrors, setCampaignActionErrors] = useState<Record<string, CampaignActionError>>({});
  const [campaignFilter, setCampaignFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);
  const [campaignBuilderStep, setCampaignBuilderStep] = useState<1 | 2 | 3 | 4>(1);

  const campaignsQuery = useQuery({
    queryKey: ['whatsapp-campaigns'],
    queryFn: fetchCampaigns,
  });
  const commercialChargesQuery = useQuery({
    queryKey: ['whatsapp-commercial-charges'],
    queryFn: () => fetchMetaCommercialCharges(),
    staleTime: 30_000,
    enabled: can('finance', 'view'),
    retry: false,
  });
  const optInsQuery = useQuery({
    queryKey: ['whatsapp-opt-ins'],
    queryFn: fetchOptIns,
  });
  const suppressionsQuery = useQuery({
    queryKey: ['whatsapp-suppressions'],
    queryFn: fetchSuppressions,
  });
  const audienceListsQuery = useQuery({
    queryKey: ['whatsapp-audience-lists'],
    queryFn: () => fetchAudienceLists(false),
  });
  const selectedListQuery = useQuery({
    queryKey: ['whatsapp-audience-list', selectedListId],
    queryFn: () => fetchAudienceList(selectedListId!),
    enabled: Boolean(selectedListId),
  });
  const flowsQuery = useQuery({
    queryKey: ['klm', 'flows'],
    queryFn: klmApi.flows,
  });
  const areasQuery = useQuery({ queryKey: ['klm', 'areas'], queryFn: klmApi.areas });

  useEffect(() => {
    if (!embedded) return;
    const contextualSearch = contextContactName?.trim() ?? '';
    setDraft((current) => {
      if (current.audienceFilter.search === contextualSearch) return current;
      return {
        ...current,
        audienceFilter: {
          ...current.audienceFilter,
          search: contextualSearch,
        },
      };
    });
  }, [contextContactName, contextThreadId, embedded]);

  useEffect(() => {
    let cancelled = false;
    const loadBase = async () => {
      try {
        const [nextConnections, nextAssignees] = await Promise.all([
          fetchWhatsAppConnections(),
          fetchConversationAssignees(),
        ]);
        if (cancelled) return;
        const officialConnections = nextConnections.filter(
          (connection) =>
            connection.status === 'connected'
            && connection.connectionType !== 'QR_CODE',
        );
        setConnections(officialConnections);
        setAssignees(nextAssignees);
        setDraft((current) => ({
          ...current,
          whatsappConnectionId: current.whatsappConnectionId || officialConnections[0]?.id || '',
        }));
      } catch {
        if (!cancelled) {
          setConnections([]);
          setAssignees([]);
        }
      }
    };
    void loadBase();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!draft.whatsappConnectionId) {
      setTemplates([]);
      return;
    }
    let cancelled = false;
    const loadTemplates = async () => {
      try {
        const data = await fetchMetaTemplates(draft.whatsappConnectionId);
        const approved = data.filter((template) => template.status === 'approved');
        if (!cancelled) {
          setTemplates(approved);
          setDraft((current) => ({
            ...current,
            metaTemplateId: current.metaTemplateId && approved.some((template) => template.id === current.metaTemplateId)
              ? current.metaTemplateId
              : approved[0]?.id || '',
          }));
        }
      } catch {
        if (!cancelled) {
          setTemplates([]);
        }
      }
    };
    void loadTemplates();
    return () => {
      cancelled = true;
    };
  }, [draft.whatsappConnectionId]);

  useEffect(() => {
    const lists = audienceListsQuery.data ?? [];
    if (selectedListId && lists.some((item) => item.id === selectedListId)) return;
    setSelectedListId(lists[0]?.id ?? null);
  }, [audienceListsQuery.data, selectedListId]);

  const totals = useMemo(() => {
    const campaigns = campaignsQuery.data ?? [];
    return campaigns.reduce(
      (acc, campaign) => {
        acc.total += 1;
        acc.sent += campaign.sentCount;
        acc.delivered += campaign.deliveredCount;
        acc.read += campaign.readCount;
        acc.failed += campaign.failedCount;
        return acc;
      },
      { total: 0, sent: 0, delivered: 0, read: 0, failed: 0 },
    );
  }, [campaignsQuery.data]);
  const campaignFilterFields = useMemo<PersonalizedFilterField<WhatsAppCampaign>[]>(() => [
    { key: 'title', label: 'Campanha', type: 'text' },
    { key: 'description', label: 'Descrição', type: 'text' },
    { key: 'status', label: 'Status', type: 'select', options: [...new Set((campaignsQuery.data ?? []).map((campaign) => campaign.status))].map((value) => ({ value, label: value.replaceAll('_', ' ') })) },
    { key: 'flowStartMode', label: 'Início do flow', type: 'select', options: [{ value: 'manual', label: 'Manual' }, { value: 'on_delivery', label: 'Após entrega' }, { value: 'on_reply', label: 'Após resposta' }] },
    { key: 'followUpFlowId', label: 'Possui flow vinculado', type: 'boolean', getValue: (campaign) => Boolean(campaign.followUpFlowId) },
    { key: 'scheduledAt', label: 'Agendada para', type: 'date' },
    { key: 'totalRecipients', label: 'Destinatários', type: 'number' },
    { key: 'eligibleRecipients', label: 'Elegíveis', type: 'number' },
    { key: 'deliveredCount', label: 'Entregues', type: 'number' },
    { key: 'readCount', label: 'Lidas', type: 'number' },
    { key: 'failedCount', label: 'Falhas', type: 'number' },
    { key: 'createdAt', label: 'Criada em', type: 'date' },
  ], [campaignsQuery.data]);
  const visibleCampaigns = useMemo(
    () => (campaignsQuery.data ?? []).filter((campaign) => matchesPersonalizedFilter(campaign, campaignFilter, campaignFilterFields)),
    [campaignFilter, campaignFilterFields, campaignsQuery.data],
  );
  const selectedCampaignTemplate = useMemo(
    () => templates.find((template) => template.id === draft.metaTemplateId) ?? null,
    [draft.metaTemplateId, templates],
  );
  const campaignHeaderParameters = useMemo(
    () => campaignTemplateParameters(selectedCampaignTemplate, 'header'),
    [selectedCampaignTemplate],
  );
  const campaignBodyParameters = useMemo(
    () => campaignTemplateParameters(selectedCampaignTemplate, 'body'),
    [selectedCampaignTemplate],
  );
  const campaignButtonParameters = useMemo(
    () => campaignTemplateButtonParameters(selectedCampaignTemplate),
    [selectedCampaignTemplate],
  );

  useEffect(() => {
    setDraft((current) => {
      const headerParameters = campaignHeaderParameters.map((_, index) => current.headerParameters[index] ?? '');
      const bodyParameters = campaignBodyParameters.map((_, index) => current.bodyParameters[index] ?? '');
      const buttonParameters = campaignButtonParameters.map((item) => current.buttonParameters.find((candidate) => candidate.index === item.index) ?? { index: item.index, value: '' });
      if (
        headerParameters.length === current.headerParameters.length
        && headerParameters.every((value, index) => value === current.headerParameters[index])
        && bodyParameters.length === current.bodyParameters.length
        && bodyParameters.every((value, index) => value === current.bodyParameters[index])
        && buttonParameters.length === current.buttonParameters.length
        && buttonParameters.every((value, index) => value.value === current.buttonParameters[index]?.value && value.index === current.buttonParameters[index]?.index)
      ) {
        return current;
      }
      return { ...current, headerParameters, bodyParameters, buttonParameters };
    });
  }, [campaignBodyParameters, campaignButtonParameters, campaignHeaderParameters]);
  // Qualquer lançamento pode materializar um contato na conexão de saída,
  // inclusive quando a audiência nasceu do CRM em outra conexão. Por isso a
  // UI nunca tenta inferir que uma campanha é isenta do estoque de contatos.
  const campaignLaunchBlockReason = (): string | null => (
    !contactsCapacity.materializationAllowed ? contactsCapacity.blockingReason : null
  );
  const activeTemplateAreas = useMemo(() => (areasQuery.data ?? []).filter((area) => area.isActive), [areasQuery.data]);
  const selectedTemplateArea = activeTemplateAreas.find((area) => area.areaKey === templateAreaKey) ?? activeTemplateAreas[0] ?? null;
  const areaTemplateLibrary = useMemo<TemplateLibraryItem[]>(() => {
    if (!selectedTemplateArea) return [];
    const normalizedKey = selectedTemplateArea.areaKey.toLowerCase();
    const specificTemplates = AREA_TEMPLATE_LIBRARY[normalizedKey]
      ?? Object.entries(AREA_TEMPLATE_LIBRARY).find(([key]) => normalizedKey.includes(key))?.[1]
      ?? [];
    const templates = [...specificTemplates, ...GENERIC_AREA_TEMPLATES]
      .filter((template, index, all) => all.findIndex((candidate) => candidate.name === template.name) === index)
      .slice(0, 30);
    return templates.map((template, index) => ({
      ...template,
      id: `${selectedTemplateArea.areaKey}-${index}`,
      name: `${template.name}_${selectedTemplateArea.areaKey}`.replace(/_+/g, '_'),
    }));
  }, [selectedTemplateArea]);
  const visibleAreaTemplates = useMemo(() => {
    const search = templateLibrarySearch.trim().toLocaleLowerCase('pt-BR');
    if (!search) return areaTemplateLibrary;
    return areaTemplateLibrary.filter((template) => `${template.title} ${template.purpose} ${template.body}`.toLocaleLowerCase('pt-BR').includes(search));
  }, [areaTemplateLibrary, templateLibrarySearch]);

  useEffect(() => {
    if (!activeTemplateAreas.length) return setTemplateAreaKey('');
    if (!activeTemplateAreas.some((area) => area.areaKey === templateAreaKey)) setTemplateAreaKey(activeTemplateAreas[0].areaKey);
  }, [activeTemplateAreas, templateAreaKey]);

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['whatsapp-campaigns'] }),
      queryClient.invalidateQueries({ queryKey: ['whatsapp-commercial-charges'] }),
      queryClient.invalidateQueries({ queryKey: ['whatsapp-opt-ins'] }),
      queryClient.invalidateQueries({ queryKey: ['whatsapp-suppressions'] }),
      queryClient.invalidateQueries({ queryKey: ['whatsapp-audience-lists'] }),
      queryClient.invalidateQueries({ queryKey: ['whatsapp-audience-list'] }),
      queryClient.invalidateQueries({ queryKey: ['workspace-capability-manifest'] }),
    ]);
  };

  const createMutation = useMutation({
    mutationFn: async () => createCampaign({
      whatsappConnectionId: draft.whatsappConnectionId,
      title: draft.title,
      description: draft.description || null,
      metaTemplateId: draft.metaTemplateId,
      headerParameters: draft.headerParameters,
      bodyParameters: draft.bodyParameters,
      buttonParameters: draft.buttonParameters,
      scheduledAt: draft.scheduledAt ? new Date(draft.scheduledAt).toISOString() : null,
      followUpFlowId: draft.followUpFlowId || null,
      flowStartMode: draft.flowStartMode,
      audienceFilter: {
        ...draft.audienceFilter,
        search: draft.audienceFilter.search?.trim() || null,
      },
    }),
    onSuccess: async () => {
      toast({ title: 'Campanha criada em rascunho.' });
      setCampaignBuilderStep(1);
      setDraft((current) => ({
        ...DEFAULT_DRAFT,
        whatsappConnectionId: current.whatsappConnectionId,
        audienceFilter: {
          ...DEFAULT_FILTERS,
          search: embedded ? contextContactName?.trim() ?? '' : '',
        },
      }));
      await invalidate();
    },
    onError: (error) => {
      toast({
        variant: 'destructive',
        title: 'Não foi possível criar a campanha',
        description: error instanceof Error ? error.message : 'Revise o número oficial, template e audiência.',
      });
    },
  });

  const syncTemplatesMutation = useMutation({
    mutationFn: async () => syncMetaTemplates(draft.whatsappConnectionId || null),
    onSuccess: (data) => {
      const approved = data.filter((template) => template.status === 'approved');
      setTemplates(approved);
      setDraft((current) => ({
        ...current,
        metaTemplateId: current.metaTemplateId && approved.some((template) => template.id === current.metaTemplateId)
          ? current.metaTemplateId
          : approved[0]?.id || '',
      }));
      toast({ title: 'Templates oficiais sincronizados.' });
    },
    onError: (error) => {
      toast({
        variant: 'destructive',
        title: 'Erro ao sincronizar templates',
        description: error instanceof Error ? error.message : 'Não foi possível sincronizar agora.',
      });
    },
  });

  const actionMutation = useMutation({
    mutationFn: async ({ action, campaignId }: { action: 'launch' | 'dry-run' | 'pause' | 'resume' | 'stop'; campaignId: string }) => {
      if (action === 'launch') {
        const blockingReason = campaignLaunchBlockReason();
        if (blockingReason) throw new ContactCapacityClientBlockError(blockingReason);
        return launchCampaign(campaignId, { dryRun: false });
      }
      if (action === 'dry-run') return launchCampaign(campaignId, { dryRun: true, sampleSize: 8 });
      if (action === 'pause') return pauseCampaign(campaignId);
      if (action === 'resume') return resumeCampaign(campaignId);
      return stopCampaign(campaignId);
    },
    onSuccess: async (_, variables) => {
      setCampaignActionErrors((current) => {
        if (!(variables.campaignId in current)) return current;
        const next = { ...current };
        delete next[variables.campaignId];
        return next;
      });
      const successMessages = {
        launch: {
          title: 'Lançamento aceito para processamento.',
          description: 'O backend revalidou a audiência e as quotas conhecidas. Acompanhe o resultado por destinatário no histórico.',
        },
        'dry-run': {
          title: 'Prévia de audiência atualizada.',
          description: 'Nenhum envio nem novo contato foi criado pelo dry-run.',
        },
        pause: { title: 'Campanha pausada.', description: 'Novos envios ficaram suspensos.' },
        resume: { title: 'Campanha retomada.', description: 'O processamento continuará com validação por destinatário.' },
        stop: { title: 'Campanha encerrada.', description: 'Nenhum novo envio será iniciado.' },
      } satisfies Record<typeof variables.action, { title: string; description: string }>;
      toast(successMessages[variables.action]);
      await invalidate();
    },
    onError: (error, variables) => {
      if (variables.action === 'launch') {
        setCampaignActionErrors((current) => ({
          ...current,
          [variables.campaignId]: describeCampaignActionError(error),
        }));
      }
      toast({
        variant: 'destructive',
        title: variables.action === 'launch'
          ? 'Lançamento não concluído'
          : variables.action === 'dry-run'
            ? 'Prévia não concluída'
            : 'Ação da campanha não concluída',
        description: variables.action === 'launch'
          ? error instanceof ContactCapacityClientBlockError
            ? error.message
            : `A API não aceitou ou não concluiu o lançamento. ${error instanceof Error ? error.message : 'A quota, a Meta ou a fila bloqueou a operação.'}`
          : error instanceof Error ? error.message : 'A Meta ou a fila bloqueou a operação.',
      });
    },
  });

  const optInMutation = useMutation({
    mutationFn: async () => saveOptIn({
      phone: normalizedOptInPhone ?? optInForm.phone,
      channel: 'whatsapp',
      status: optInForm.status,
      source: optInForm.source,
    }),
    onSuccess: async () => {
      toast({ title: 'Opt-in atualizado.' });
      setOptInForm({ phone: '', status: 'opted_in', source: 'manual' });
      await invalidate();
    },
    onError: (error) => {
      toast({
        variant: 'destructive',
        title: 'Opt-in não registrado',
        description: error instanceof Error
          ? error.message
          : 'O consentimento não foi salvo. Entre novamente e repita a operação.',
      });
    },
  });

  const templateAnalysisMutation = useMutation({
    mutationFn: () => analyzeMetaTemplateDraft({
      ...templateDraft,
      practiceArea: selectedTemplateArea?.name ?? null,
    }),
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Análise por IA indisponível',
      description: error instanceof Error ? error.message : 'Verifique a configuração dos modelos e tente novamente.',
    }),
  });

  const suppressionMutation = useMutation({
    mutationFn: async () => saveSuppression({
      phone: normalizedSuppressionPhone ?? suppressionForm.phone,
      reason: suppressionForm.reason,
      isActive: suppressionForm.isActive,
    }),
    onSuccess: async () => {
      toast({ title: 'Supressão atualizada.' });
      setSuppressionForm({ phone: '', reason: 'manual', isActive: true });
      await invalidate();
    },
  });

  const createListMutation = useMutation({
    mutationFn: () => createAudienceList({
      name: listForm.name.trim(),
      description: listForm.description.trim() || null,
      source: 'manual',
    }),
    onSuccess: async (created) => {
      setListForm({ name: '', description: '' });
      setSelectedListId(created.id);
      toast({ title: 'Lista criada.' });
      await invalidate();
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Não foi possível criar a lista',
      description: error instanceof Error ? error.message : 'Revise o nome da lista.',
    }),
  });

  const addMemberMutation = useMutation({
    mutationFn: () => addAudienceListMember(selectedListId!, {
      phone: normalizedMemberPhone ?? memberForm.phone,
      name: memberForm.name.trim() || null,
      email: memberForm.email.trim() || null,
    }),
    onSuccess: async () => {
      setMemberForm({ phone: '', name: '', email: '' });
      toast({ title: 'Contato adicionado à lista.' });
      await invalidate();
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Contato não adicionado',
      description: error instanceof Error ? error.message : 'Revise o telefone.',
    }),
  });

  const importListMutation = useMutation({
    mutationFn: () => importAudienceListCsv(selectedListId!, csvFile!),
    onSuccess: async (result) => {
      setCsvFile(null);
      if (csvInputRef.current) csvInputRef.current.value = '';
      toast({
        title: 'CSV processado.',
        description: `${result.imported} entradas adicionadas, ${result.updated} atualizadas, ${result.normalized} telefones normalizados automaticamente, ${result.duplicates} duplicados e ${result.invalid} inválidos. A importação não presume quantos contatos canônicos são novos; o backend revalidará a quota no lançamento.`,
      });
      await invalidate();
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'CSV não importado',
      description: error instanceof Error
        ? `A importação não foi confirmada. ${error.message}`
        : 'A importação não foi confirmada. Use CSV UTF-8 com coluna telefone.',
    }),
  });

  const archiveListMutation = useMutation({
    mutationFn: (audienceList: AudienceList) => updateAudienceList(audienceList.id, { status: 'archived' }),
    onSuccess: async () => {
      toast({ title: 'Lista arquivada sem apagar o histórico.' });
      setSelectedListId(null);
      await invalidate();
    },
  });

  const exportListMutation = useMutation({
    mutationFn: downloadAudienceListCsv,
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Exportação não concluída',
      description: error instanceof Error ? error.message : 'Seu perfil pode não ter acesso aos dados protegidos.',
    }),
  });

  const revealSection = (target: HTMLElement | null) => {
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    target.focus({ preventScroll: true });
  };

  const templateAnalysis = templateAnalysisMutation.data;
  const approvalDisplay = getApprovalDisplay(templateAnalysis?.approvalLikelihood);
  const updateTemplateDraft = (next: typeof DEFAULT_TEMPLATE_DRAFT) => {
    templateAnalysisMutation.reset();
    setTemplateDraft(next);
  };

  return (
    <div className={embedded ? 'space-y-4' : 'space-y-6'}>
      {!embedded && <SectionHeader
        title="Disparos e listas"
        description="Campanhas pela API oficial, com listas reutilizáveis, filtros do CRM, consentimento, supressão, agendamento, métricas e continuação por flow."
      />}
      {embedded && (
        <div role="region" aria-label="Disparos no contexto da conversa atual" className="space-y-3 border border-primary/30 bg-primary/5 p-3 text-xs">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <strong className="block">Você continua na conversa de {contextContactName || 'contato selecionado'}</strong>
              <span className="mt-1 block text-muted-foreground">A audiência começa filtrada por este contato. A conversa e o histórico permanecem selecionados quando você aplicar um filtro.</span>
            </div>
            <div className="flex flex-wrap gap-1.5 text-xs text-muted-foreground">
              {contextThreadId && <Badge variant="outline">Conversa vinculada</Badge>}
              {contextMatterId && <Badge variant="outline">Caso vinculado</Badge>}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2" aria-label="Atalhos desta área">
            <Button type="button" variant="outline" size="sm" onClick={() => revealSection(listsSectionRef.current)}>
              <ListFilter className="mr-1.5 h-4 w-4" />Listas
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={() => revealSection(newCampaignSectionRef.current)}>
              <Megaphone className="mr-1.5 h-4 w-4" />Nova campanha
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={() => revealSection(campaignHistorySectionRef.current)}>
              <History className="mr-1.5 h-4 w-4" />Histórico de campanhas
            </Button>
            <span className="mx-1 hidden h-6 w-px bg-border sm:block" aria-hidden="true" />
            <Button type="button" variant="secondary" size="sm" disabled={!onConversationFilter} onClick={() => onConversationFilter?.('com_disparo')}>
              <Megaphone className="mr-1.5 h-4 w-4" />Conversas com disparo
            </Button>
            <Button type="button" variant="secondary" size="sm" disabled={!onConversationFilter} onClick={() => onConversationFilter?.('disparo_sem_retorno')}>
              <MessageCircleReply className="mr-1.5 h-4 w-4" />Disparos sem retorno
            </Button>
            <Button type="button" variant="ghost" size="sm" disabled={!onConversationFilter} onClick={() => onConversationFilter?.('todos')}>
              <History className="mr-1.5 h-4 w-4" />Voltar ao histórico da conversa
            </Button>
          </div>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-5">
        {[
          { label: 'Campanhas', value: totals.total, icon: Megaphone },
          { label: 'Enviadas', value: totals.sent, icon: CheckCircle2 },
          { label: 'Entregues', value: totals.delivered, icon: Users },
          { label: 'Lidas', value: totals.read, icon: ShieldCheck },
          { label: 'Falhas', value: totals.failed, icon: ShieldBan },
        ].map((item) => (
          <Card key={item.label}>
            <CardContent className="flex items-center justify-between py-5">
              <div>
                <p className="text-xs uppercase tracking-wide text-muted-foreground">{item.label}</p>
                <p className="mt-2 text-2xl font-bold tabular-nums">{item.value}</p>
              </div>
              <item.icon className="h-5 w-5 text-primary" />
            </CardContent>
          </Card>
        ))}
      </div>

      <Card aria-labelledby="contacts-capacity-title">
        <CardContent className="space-y-4 py-5">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <p id="contacts-capacity-title" className="flex items-center gap-2 text-sm font-semibold">
                <Users className="h-4 w-4 text-primary" aria-hidden="true" />
                Capacidade de contatos da conta
              </p>
              <p className="mt-1 text-xs text-muted-foreground">{contactsCapacity.detail}</p>
            </div>
            <Badge
              variant={contactsCapacity.materializationAllowed ? 'secondary' : 'destructive'}
              role="status"
              aria-live="polite"
            >
              {contactsCapacity.statusLabel}
            </Badge>
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="border-l-2 border-primary/25 pl-3">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Em uso</p>
              <p className="mt-1 text-lg font-semibold tabular-nums">{formatContactCount(contactsCapability?.used ?? null)}</p>
            </div>
            <div className="border-l-2 border-primary/25 pl-3">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Limite contratado</p>
              <p className="mt-1 text-lg font-semibold tabular-nums">{formatContactCount(contactsCapability?.limit ?? null)}</p>
              {contactsCapability?.graceLimit != null && contactsCapability.graceLimit !== contactsCapability.limit && (
                <p className="text-xs text-muted-foreground">Teto com tolerância: {formatContactCount(contactsCapability.graceLimit)}</p>
              )}
            </div>
            <div className="border-l-2 border-primary/25 pl-3">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Saldo regular</p>
              <p className="mt-1 text-lg font-semibold tabular-nums">{formatContactCount(contactsCapability?.remaining ?? null)}</p>
            </div>
          </div>
          <div className="flex flex-col gap-2 border-t pt-3 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
            <p>
              Listas, campanhas e conversas existentes continuam consultáveis. Esta tela não estima quantos telefones de uma lista ainda se tornarão contatos novos.
            </p>
            {!contactsCapacity.materializationAllowed && (
              can('configuracoes', 'view')
                ? <Button asChild variant="outline" size="sm"><Link to="/app/configuracoes">Revisar plano e capacidade</Link></Button>
                : <span className="font-medium text-foreground">Peça ao administrador um upgrade ou bônus.</span>
            )}
          </div>
          {capabilityManifest?.generatedAt && (
            <p className="text-[11px] text-muted-foreground">
              Capacidade atualizada em {new Date(capabilityManifest.generatedAt).toLocaleString('pt-BR')}.
            </p>
          )}
        </CardContent>
      </Card>

      {can('finance', 'view') && <MetaCommercialChargePanel
          value={commercialChargesQuery.data ?? null}
          loading={commercialChargesQuery.isPending && !commercialChargesQuery.isError}
          error={commercialChargesQuery.isError ? 'Não foi possível conferir a cobrança comercial das mensagens oficiais.' : null}
          onRetry={() => { void commercialChargesQuery.refetch(); }}
          compact={embedded}
        />}

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <CardTitle className="flex items-center gap-2 text-lg font-semibold">
                <FileCheck2 className="h-5 w-5 text-primary" />
                Modelos para aprovação Meta
              </CardTitle>
              <CardDescription>
                Escreva templates de utilidade antes de submeter, veja sinais de recategorização para marketing e estime a faixa de custo relativa.
              </CardDescription>
            </div>
            <Badge variant="outline" className={approvalDisplay.badgeClass}>{templateAnalysisMutation.isPending ? 'Analisando...' : approvalDisplay.label}</Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <section className="border-b pb-5" aria-labelledby="template-library-title">
            <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
              <div>
                <p id="template-library-title" className="flex items-center gap-2 text-sm font-semibold"><BookOpenText className="h-4 w-4 text-primary" />Banco de modelos da conta</p>
                <p className="mt-1 text-xs text-muted-foreground">30 modelos de utilidade vinculados à área ativa selecionada.</p>
              </div>
              {activeTemplateAreas.length > 0 && <Select value={selectedTemplateArea?.areaKey ?? ''} onValueChange={setTemplateAreaKey}><SelectTrigger className="w-full md:w-[260px]" aria-label="Área do banco de modelos"><SelectValue /></SelectTrigger><SelectContent>{activeTemplateAreas.map((area) => <SelectItem key={area.id} value={area.areaKey}>{area.name}</SelectItem>)}</SelectContent></Select>}
            </div>
            {areasQuery.isPending ? <p className="mt-4 text-xs text-muted-foreground">Carregando áreas disponíveis...</p> : areaTemplateLibrary.length ? (
              <div className="mt-4 space-y-3">
                <div className="flex items-center gap-2"><Input value={templateLibrarySearch} onChange={(event) => setTemplateLibrarySearch(event.target.value)} aria-label="Buscar no banco de modelos" placeholder="Buscar por prazo, documento, reunião..." /><Badge variant="secondary" className="shrink-0 tabular-nums">{visibleAreaTemplates.length} modelos</Badge></div>
                <ScrollArea className="h-72 pr-3">
                  <div className="grid gap-2 md:grid-cols-2">
                    {visibleAreaTemplates.map((template) => <button key={template.id} type="button" className="flex min-h-20 items-start justify-between gap-3 rounded-lg border bg-background p-3 text-left transition-colors hover:border-primary/40 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => updateTemplateDraft({ name: template.name, categoryIntent: template.categoryIntent, body: template.body, sampleVariables: template.sampleVariables })} aria-label={`Usar modelo ${template.title}`}><span><span className="block text-sm font-semibold">{template.title}</span><span className="mt-1 block text-xs text-muted-foreground">{template.purpose} · {selectedTemplateArea?.name}</span></span><Badge variant="outline" className="shrink-0">Utilidade</Badge></button>)}
                  </div>
                  {!visibleAreaTemplates.length && <p className="py-10 text-center text-xs text-muted-foreground">Nenhum modelo corresponde à busca.</p>}
                </ScrollArea>
              </div>
            ) : <div className="mt-4 rounded-lg border border-dashed p-4 text-xs text-muted-foreground">Nenhuma área ativa nesta conta. Ative uma área em Áreas e times para liberar seus modelos.</div>}
          </section>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
          <div className="space-y-4">
            <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_220px]">
              <div className="space-y-1.5">
                <FieldHelpLabel htmlFor="meta-template-name" {...CAMPAIGN_FIELD_HELP.templateName} />
                <Input
                  id="meta-template-name"
                  value={templateDraft.name}
                  onChange={(event) => updateTemplateDraft({ ...templateDraft, name: event.target.value })}
                  placeholder="atualizacao_prazo_documentos"
                />
              </div>
              <div className="space-y-1.5">
                <FieldHelpLabel htmlFor="meta-template-category-intent" {...CAMPAIGN_FIELD_HELP.templateCategoryIntent} />
                <Select
                  value={templateDraft.categoryIntent}
                  onValueChange={(value: 'utility' | 'marketing') => updateTemplateDraft({ ...templateDraft, categoryIntent: value })}
                >
                  <SelectTrigger id="meta-template-category-intent"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="utility">Utilidade</SelectItem>
                    <SelectItem value="marketing">Marketing</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1.5">
              <FieldHelpLabel htmlFor="meta-template-body" {...CAMPAIGN_FIELD_HELP.templateBody} />
              <Textarea
                id="meta-template-body"
                value={templateDraft.body}
                onChange={(event) => updateTemplateDraft({ ...templateDraft, body: event.target.value })}
                className="min-h-[150px]"
                placeholder="Olá {{1}}, sua reunião está confirmada para {{2}}. Acesse {{3}} para consultar os documentos necessários."
              />
            </div>
            <div className="space-y-1.5">
              <FieldHelpLabel htmlFor="meta-template-variables" {...CAMPAIGN_FIELD_HELP.templateVariables} />
              <Input
                id="meta-template-variables"
                value={templateDraft.sampleVariables}
                onChange={(event) => updateTemplateDraft({ ...templateDraft, sampleVariables: event.target.value })}
                placeholder="Maria, 30/07 às 14h, link do portal"
              />
            </div>
          </div>

          <div className="rounded-xl border bg-muted/20 p-4">
            <div className="flex items-start gap-3">
              <Sparkles className="mt-0.5 h-5 w-5 text-primary" />
              <div>
                <p className="text-sm font-semibold">Leitura da IA para submissão</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">{templateAnalysis?.summary ?? (templateDraft.body.trim().length >= 10 ? 'Texto pronto. Solicite uma análise real ao modelo configurado para este workspace.' : 'Escreva ao menos 10 caracteres para liberar a análise.')}</p>
              </div>
            </div>
            <div className="mt-4 grid gap-2 sm:grid-cols-3 xl:grid-cols-1">
              <div className="rounded-lg border bg-background p-3">
                <p className="text-xs uppercase tracking-wide text-muted-foreground">Categoria provável</p>
                <p className="mt-1 text-sm font-semibold">{templateAnalysis ? formatTemplateCategory(templateAnalysis.predictedCategory) : 'Ainda não analisada'}</p>
              </div>
              <div className="rounded-lg border bg-background p-3">
                <p className="flex items-center gap-1.5 text-xs uppercase tracking-wide text-muted-foreground"><CircleDollarSign className="h-3.5 w-3.5" />Faixa de custo</p>
                <p className="mt-1 text-sm font-semibold">{templateAnalysis?.costBand ?? 'Aguardando análise'}</p>
              </div>
              <div className="rounded-lg border bg-background p-3">
                <p className="text-xs uppercase tracking-wide text-muted-foreground">Ajuste recomendado</p>
                <p className="mt-1 text-sm font-semibold">{templateAnalysis?.recommendation ?? 'Analisar antes de submeter'}</p>
              </div>
            </div>
            <ul className="mt-4 space-y-2 text-xs leading-5 text-muted-foreground">
              {(templateAnalysis?.signals ?? ['A decisão final de categoria e aprovação continua sendo da Meta.']).map((signal) => <li key={signal}>• {signal}</li>)}
            </ul>
            {templateAnalysis && <p className="mt-3 text-[11px] text-muted-foreground">Analisado por {templateAnalysis.provider} · {templateAnalysis.modelUsed} · confiança {Math.round(templateAnalysis.confidence * 100)}%</p>}
            {templateAnalysisMutation.isError && <p role="alert" className="mt-3 text-xs text-destructive">Não foi possível executar a análise. A configuração do modelo pode estar indisponível.</p>}
            <Button type="button" className="mt-4 w-full" onClick={() => templateAnalysisMutation.mutate()} disabled={templateDraft.body.trim().length < 10 || templateAnalysisMutation.isPending}>
              <Sparkles className="mr-2 h-4 w-4" />{templateAnalysisMutation.isPending ? 'Analisando com IA...' : templateAnalysis ? 'Analisar novamente' : 'Analisar com IA'}
            </Button>
          </div>
          </div>
        </CardContent>
      </Card>

      <section ref={listsSectionRef} tabIndex={-1} aria-labelledby="audience-lists-title" className="scroll-mt-4 overflow-hidden border-y bg-background outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <div className="grid min-h-[360px] lg:grid-cols-[300px_minmax(0,1fr)]">
          <aside className="border-b p-4 lg:border-b-0 lg:border-r">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p id="audience-lists-title" className="text-lg font-semibold">Listas de audiência</p>
                <p className="text-xs text-muted-foreground">Importe, deduplique e reutilize.</p>
              </div>
              <ListFilter className="h-4 w-4 text-primary" />
            </div>
            <div className="mt-4 space-y-2">
              <Input
                aria-label="Nome da nova lista"
                value={listForm.name}
                onChange={(event) => setListForm((current) => ({ ...current, name: event.target.value }))}
                placeholder="Nome da nova lista"
              />
              <Input
                aria-label="Descrição da nova lista"
                value={listForm.description}
                onChange={(event) => setListForm((current) => ({ ...current, description: event.target.value }))}
                placeholder="Descrição opcional"
              />
              <Button
                className="w-full"
                variant="outline"
                onClick={() => createListMutation.mutate()}
                disabled={!listForm.name.trim() || createListMutation.isPending}
              >
                <Plus className="mr-2 h-4 w-4" />
                Criar lista
              </Button>
            </div>
            <ScrollArea className="mt-4 h-52 pr-2">
              <div className="space-y-1.5">
                {(audienceListsQuery.data ?? []).map((item) => (
                  <button
                    type="button"
                    key={item.id}
                    className={`w-full border px-3 py-2.5 text-left transition-colors ${selectedListId === item.id ? 'border-primary bg-primary/5' : 'hover:bg-muted/40'}`}
                    onClick={() => setSelectedListId(item.id)}
                  >
                    <span className="block truncate text-sm font-semibold">{item.name}</span>
                    <span className="mt-1 flex items-center justify-between text-xs text-muted-foreground">
                      <span className="tabular-nums">{item.validCount} válidos</span>
                      <span>{item.source.toUpperCase()}</span>
                    </span>
                  </button>
                ))}
                {audienceListsQuery.isError && (
                  <p role="status" aria-label="Erro ao carregar listas" className="border border-dashed border-destructive/50 p-4 text-center text-xs text-destructive">
                    Não foi possível carregar as listas.{' '}
                    <button type="button" className="underline" onClick={() => { void audienceListsQuery.refetch(); }}>Tentar novamente</button>
                  </p>
                )}
                {audienceListsQuery.isSuccess && !(audienceListsQuery.data ?? []).length && (
                  <p className="border border-dashed p-4 text-center text-xs text-muted-foreground">Crie a primeira lista para importar contatos.</p>
                )}
              </div>
            </ScrollArea>
          </aside>

          <div className="min-w-0 p-4 lg:p-5">
            {selectedListQuery.data ? (
              <div className="space-y-5">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-base font-medium">{selectedListQuery.data.name}</h2>
                      <Badge variant="outline">{selectedListQuery.data.memberCount} contatos</Badge>
                      {selectedListQuery.data.sensitiveDataHidden && <Badge variant="secondary">Dados protegidos</Badge>}
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{selectedListQuery.data.description || 'Lista reutilizável para campanhas e flows.'}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button variant="outline" size="sm" onClick={() => exportListMutation.mutate(selectedListQuery.data)} disabled={exportListMutation.isPending || selectedListQuery.data.sensitiveDataHidden}>
                      <Download className="mr-1.5 h-4 w-4" />Exportar CSV
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => archiveListMutation.mutate(selectedListQuery.data)} disabled={archiveListMutation.isPending}>
                      <Archive className="mr-1.5 h-4 w-4" />Arquivar
                    </Button>
                  </div>
                </div>

                <div className="grid gap-3 sm:grid-cols-5">
                  <MetricCard label="Válidos" value={selectedListQuery.data.validCount} />
                  <MetricCard label="Inválidos" value={selectedListQuery.data.invalidCount} />
                  <MetricCard label="Duplicados" value={selectedListQuery.data.duplicateCount} />
                  <MetricCard label="Opt-ins no último CSV" value={Number(selectedListQuery.data.importMetadata.optInsRecorded ?? 0)} />
                  <MetricCard label="Normalizados no último CSV" value={Number(selectedListQuery.data.importMetadata.normalized ?? 0)} />
                </div>

                <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(300px,0.75fr)]">
                  <div className="border p-3">
                    <p className="text-sm font-medium">Adicionar contato</p>
                    <div className="mt-3 grid gap-2 md:grid-cols-[1fr_1fr_1fr_auto]">
                      <Input aria-label="Nome do contato da lista" value={memberForm.name} onChange={(event) => setMemberForm((current) => ({ ...current, name: event.target.value }))} placeholder="Nome" />
                      <Input aria-label="Telefone do contato da lista" value={memberForm.phone} onChange={(event) => setMemberForm((current) => ({ ...current, phone: event.target.value }))} onBlur={() => normalizedMemberPhone && setMemberForm((current) => ({ ...current, phone: normalizedMemberPhone }))} placeholder="5561999999999" inputMode="tel" autoComplete="tel" aria-invalid={Boolean(memberForm.phone && !normalizedMemberPhone)} />
                      <Input aria-label="E-mail do contato da lista" value={memberForm.email} onChange={(event) => setMemberForm((current) => ({ ...current, email: event.target.value }))} placeholder="E-mail" />
                      <Button onClick={() => addMemberMutation.mutate()} disabled={!normalizedMemberPhone || addMemberMutation.isPending}>Adicionar</Button>
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">{META_PHONE_HELP} Números brasileiros formatados e com DDD recebem o +55 automaticamente.</p>
                  </div>
                  <div className="border p-3">
                    <div className="flex items-center gap-1">
                      <p className="text-sm font-medium">Importar CSV</p>
                      <FieldHelp {...CAMPAIGN_FIELD_HELP.csv} />
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">Colunas aceitas: nome, telefone, e-mail, origem, campanha e opt-in. A importação corrige automaticamente os números válidos para o formato Meta; números internacionais devem incluir DDI.</p>
                    <p className="mt-2 border-l-2 border-amber-400 pl-2 text-xs text-muted-foreground" role="status">
                      Impacto: o CSV atualiza a lista de audiência, mas não permite prever quantos telefones já existem como contatos. Se houver materialização futura, o backend revalidará a quota antes do envio.
                    </p>
                    <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                      <Input id="audience-list-csv" aria-label="Arquivo CSV da lista" ref={csvInputRef} type="file" accept=".csv,text/csv" onChange={(event) => setCsvFile(event.target.files?.[0] ?? null)} />
                      <Button variant="outline" onClick={() => importListMutation.mutate()} disabled={!csvFile || importListMutation.isPending}>
                        <FileUp className="mr-1.5 h-4 w-4" />{importListMutation.isPending ? 'Processando' : 'Importar'}
                      </Button>
                    </div>
                  </div>
                </div>

                <ScrollArea className="h-44 border">
                  <div className="min-w-[620px] divide-y">
                    <div className="grid grid-cols-[1.2fr_1fr_1.2fr_100px] gap-3 bg-muted/30 px-3 py-2 text-xs font-medium text-muted-foreground">
                      <span>Nome</span><span>Telefone</span><span>E-mail</span><span>Status</span>
                    </div>
                    {selectedListQuery.data.members.slice(0, 100).map((member) => (
                      <div key={member.id} className="grid grid-cols-[1.2fr_1fr_1.2fr_100px] gap-3 px-3 py-2 text-xs">
                        <span className="truncate">{member.name || 'Sem nome'}</span>
                        <span className="truncate font-medium">{member.phone}</span>
                        <span className="truncate text-muted-foreground">{member.email || '—'}</span>
                        <Badge variant={member.status === 'valid' ? 'secondary' : 'outline'} className="w-fit">{member.status}</Badge>
                      </div>
                    ))}
                    {!selectedListQuery.data.members.length && <p className="p-6 text-center text-xs text-muted-foreground">A lista ainda não possui contatos.</p>}
                  </div>
                </ScrollArea>
              </div>
            ) : (
              <div className="flex min-h-[300px] items-center justify-center border border-dashed text-sm text-muted-foreground">
                {selectedListQuery.isLoading
                  ? 'Carregando lista...'
                  : selectedListQuery.isError
                    ? (
                      <span role="status" aria-label="Erro ao carregar lista selecionada" className="text-destructive">
                        Não foi possível carregar a lista selecionada.{' '}
                        <button type="button" className="underline" onClick={() => { void selectedListQuery.refetch(); }}>Tentar novamente</button>
                      </span>
                    )
                    : audienceListsQuery.isError
                      ? 'Não foi possível carregar as listas.'
                      : 'Selecione ou crie uma lista.'}
              </div>
            )}
          </div>
        </div>
      </section>

      <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
        <Card ref={newCampaignSectionRef} tabIndex={-1} className="scroll-mt-4 outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <CardHeader>
            <CardTitle className="text-lg font-semibold">Nova campanha</CardTitle>
            <CardDescription>
              Escolha um número oficial do workspace, sincronize os templates aprovados da Meta e monte a audiência com filtros do CRM.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <nav aria-label="Etapas da nova campanha" className="grid grid-cols-4 border">
              {[
                { step: 1 as const, label: 'Mensagem', ready: Boolean(draft.whatsappConnectionId && draft.metaTemplateId && draft.title) },
                { step: 2 as const, label: 'Automação', ready: false, optional: true },
                { step: 3 as const, label: 'Audiência', ready: Boolean(draft.audienceFilter.includeCrmContacts || draft.audienceFilter.audienceListIds.length) },
                { step: 4 as const, label: 'Revisão', ready: false },
              ].map((item) => (
                <button key={item.step} type="button" onClick={() => setCampaignBuilderStep(item.step)} className={`min-w-0 border-r px-2 py-3 text-left last:border-r-0 ${campaignBuilderStep === item.step ? 'bg-primary/10 text-primary' : 'hover:bg-muted/50'}`} aria-current={campaignBuilderStep === item.step ? 'step' : undefined}>
                  <span className="block text-[10px] font-semibold uppercase">Etapa {item.step}</span><span className="mt-0.5 block truncate text-xs font-semibold">{item.label}</span>{item.ready && <span className="mt-1 block text-[10px] text-emerald-700">Concluída</span>}{item.optional && <span className="mt-1 block text-[10px] text-muted-foreground">Opcional</span>}
                </button>
              ))}
            </nav>

            <div className={campaignBuilderStep === 1 ? 'space-y-5' : 'hidden'}>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <FieldHelpLabel htmlFor="campaign-outgoing-number" {...CAMPAIGN_FIELD_HELP.outgoingNumber} />
                <Select
                  value={draft.whatsappConnectionId || ''}
                  onValueChange={(value) => setDraft((current) => ({ ...current, whatsappConnectionId: value, metaTemplateId: '' }))}
                >
                  <SelectTrigger id="campaign-outgoing-number">
                    <SelectValue placeholder="Escolha o número oficial" />
                  </SelectTrigger>
                  <SelectContent>
                    {connections.map((connection) => (
                      <SelectItem key={connection.id} value={connection.id}>
                        {[connection.displayName, connection.displayPhoneNumber || connection.phoneNumber].filter(Boolean).join(' • ')}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="min-w-0 space-y-1.5">
                <FieldHelpLabel htmlFor="campaign-meta-template" {...CAMPAIGN_FIELD_HELP.metaTemplate} />
                <div className="flex min-w-0 items-start gap-2">
                  <Select
                    value={draft.metaTemplateId || ''}
                    onValueChange={(value) => setDraft((current) => ({ ...current, metaTemplateId: value }))}
                  >
                    <SelectTrigger id="campaign-meta-template" className="min-w-0 flex-1 overflow-hidden text-left">
                      <SelectValue placeholder="Escolha o template">
                        {selectedCampaignTemplate
                          ? `${selectedCampaignTemplate.name} · ${selectedCampaignTemplate.language} · ${selectedCampaignTemplate.status}`
                          : undefined}
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {templates.map((template) => (
                        <SelectItem key={template.id} value={template.id}>
                          <span className="flex min-w-0 items-center gap-1.5">
                            <span className="min-w-0 truncate">{template.name} · {template.language} · {template.status}</span>
                            <Badge variant="outline" className={`shrink-0 ${CATEGORY_BADGE_CLASS[template.category ?? ''] ?? ''}`}>
                              {CATEGORY_LABEL[template.category ?? ''] ?? template.category ?? 'sem categoria'}
                            </Badge>
                            {template.qualityRating && (
                              <Badge variant="outline" className={`shrink-0 ${QUALITY_BADGE_CLASS[template.qualityRating] ?? ''}`}>
                                qualidade {template.qualityRating.toLowerCase()}
                              </Badge>
                            )}
                          </span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button className="shrink-0" aria-label="Sincronizar templates aprovados da Meta" title="Sincronizar templates aprovados da Meta" variant="outline" size="icon" onClick={() => syncTemplatesMutation.mutate()} disabled={!draft.whatsappConnectionId || syncTemplatesMutation.isPending}>
                    <RefreshCw className={`h-4 w-4 ${syncTemplatesMutation.isPending ? 'animate-spin' : ''}`} />
                  </Button>
                </div>
                {selectedCampaignTemplate && (
                  <div className="flex min-w-0 flex-wrap items-center gap-1.5" aria-label="Classificação do template selecionado">
                    <Badge variant="outline" className={CATEGORY_BADGE_CLASS[selectedCampaignTemplate.category ?? ''] ?? ''}>
                      {CATEGORY_LABEL[selectedCampaignTemplate.category ?? ''] ?? selectedCampaignTemplate.category ?? 'sem categoria'}
                    </Badge>
                    {selectedCampaignTemplate.qualityRating && (
                      <Badge variant="outline" className={QUALITY_BADGE_CLASS[selectedCampaignTemplate.qualityRating] ?? ''}>
                        qualidade {selectedCampaignTemplate.qualityRating.toLowerCase()}
                      </Badge>
                    )}
                  </div>
                )}
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="campaign-title">Título da campanha</Label>
                <Input id="campaign-title" value={draft.title} onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))} placeholder="Follow-up de propostas paradas" />
              </div>
              <div className="space-y-1.5">
                <FieldHelpLabel htmlFor="campaign-scheduled-at" {...CAMPAIGN_FIELD_HELP.scheduling} />
                <Input id="campaign-scheduled-at" type="datetime-local" value={draft.scheduledAt} onChange={(event) => setDraft((current) => ({ ...current, scheduledAt: event.target.value }))} />
              </div>
            </div>
            </div>

            <div className={campaignBuilderStep === 2 ? 'space-y-5' : 'hidden'}>
            <div className="space-y-1.5">
              <Label htmlFor="campaign-description">Descrição</Label>
              <Textarea id="campaign-description" value={draft.description} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} className="min-h-[90px]" />
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <FieldHelpLabel htmlFor="campaign-follow-up-flow" {...CAMPAIGN_FIELD_HELP.followUpFlow} />
                <Select value={draft.followUpFlowId || 'none'} onValueChange={(value) => setDraft((current) => ({ ...current, followUpFlowId: value === 'none' ? '' : value }))}>
                  <SelectTrigger id="campaign-follow-up-flow"><SelectValue placeholder="Sem flow vinculado" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Sem flow vinculado</SelectItem>
                    {(flowsQuery.data ?? []).filter((flow: KlmFlow) => flow.is_active).map((flow: KlmFlow) => (
                      <SelectItem key={flow.id} value={flow.id}>{flow.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {flowsQuery.isError && (
                  <p role="status" aria-label="Erro ao carregar flows" className="text-xs text-destructive">
                    Não foi possível carregar os flows; o vínculo fica indisponível até a consulta responder.{' '}
                    <button type="button" className="underline" onClick={() => { void flowsQuery.refetch(); }}>Tentar novamente</button>
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <FieldHelpLabel htmlFor="campaign-flow-start" {...CAMPAIGN_FIELD_HELP.flowStart} />
                <Select value={draft.flowStartMode} onValueChange={(value: 'manual' | 'on_delivery' | 'on_reply') => setDraft((current) => ({ ...current, flowStartMode: value }))} disabled={!draft.followUpFlowId}>
                  <SelectTrigger id="campaign-flow-start"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="manual">Manual</SelectItem>
                    <SelectItem value="on_delivery">Na primeira entrega</SelectItem>
                    <SelectItem value="on_reply">Na primeira resposta</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <CampaignParameterFields
                scope="header"
                parameters={campaignHeaderParameters}
                values={draft.headerParameters}
                onChange={(index, value) => setDraft((current) => ({
                  ...current,
                  headerParameters: current.headerParameters.map((item, itemIndex) => itemIndex === index ? value : item),
                }))}
              />
              <CampaignParameterFields
                scope="body"
                parameters={campaignBodyParameters}
                values={draft.bodyParameters}
                onChange={(index, value) => setDraft((current) => ({
                  ...current,
                  bodyParameters: current.bodyParameters.map((item, itemIndex) => itemIndex === index ? value : item),
                }))}
              />
              {campaignButtonParameters.length > 0 && <div className="space-y-2"><div><p className="text-sm font-medium">Sufixos dinâmicos dos botões</p><p className="text-xs text-muted-foreground">Cada valor é enviado pelo índice real do botão na mensagem Meta.</p></div>{campaignButtonParameters.map((parameter) => <div key={parameter.index} className="space-y-1.5"><Label htmlFor={`campaign-button-parameter-${parameter.index}`}>Botão índice {parameter.index}</Label><Input id={`campaign-button-parameter-${parameter.index}`} value={draft.buttonParameters.find((item) => item.index === parameter.index)?.value ?? ''} onChange={(event) => setDraft((current) => ({ ...current, buttonParameters: current.buttonParameters.map((item) => item.index === parameter.index ? { ...item, value: event.target.value } : item) }))} placeholder={parameter.example ? `Exemplo: ${parameter.example}` : 'Sufixo dinâmico'} /></div>)}</div>}
            </div>
            </div>

            <div className={campaignBuilderStep === 3 ? 'space-y-4 border p-4' : 'hidden'}>
              <div>
                <div className="flex items-center gap-1">
                  <p className="text-sm font-medium">Audiência combinada</p>
                  <FieldHelp {...CAMPAIGN_FIELD_HELP.audienceSources} />
                </div>
                <p className="text-xs text-muted-foreground">Una filtros do CRM e listas importadas; a prévia deduplica por telefone e aplica opt-in e supressões.</p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                <div className="flex items-center gap-3 border px-3 py-3 text-sm">
                  <Checkbox
                    id="campaign-include-crm"
                    checked={draft.audienceFilter.includeCrmContacts}
                    onCheckedChange={(checked) => setDraft((current) => ({ ...current, audienceFilter: { ...current.audienceFilter, includeCrmContacts: checked === true } }))}
                  />
                  <Label htmlFor="campaign-include-crm" className="font-normal">Incluir contatos filtrados do CRM</Label>
                  <FieldHelp {...CAMPAIGN_FIELD_HELP.audienceSources} />
                </div>
                {audienceListsQuery.isError && (
                  <p role="status" aria-label="Erro ao carregar listas de audiência" className="sm:col-span-2 xl:col-span-3 text-xs text-destructive">
                    Não foi possível carregar as listas de audiência.{' '}
                    <button type="button" className="underline" onClick={() => { void audienceListsQuery.refetch(); }}>Tentar novamente</button>
                  </p>
                )}
                {(audienceListsQuery.data ?? []).map((item) => {
                  const checked = draft.audienceFilter.audienceListIds.includes(item.id);
                  return (
                    <label key={item.id} className="flex items-center gap-3 border px-3 py-3 text-sm">
                      <Checkbox
                        checked={checked}
                        onCheckedChange={(next) => setDraft((current) => ({
                          ...current,
                          audienceFilter: {
                            ...current.audienceFilter,
                            audienceListIds: next === true
                              ? [...current.audienceFilter.audienceListIds, item.id]
                              : current.audienceFilter.audienceListIds.filter((id) => id !== item.id),
                          },
                        }))}
                      />
                      <span className="min-w-0"><span className="block truncate font-semibold">{item.name}</span><span className="text-xs text-muted-foreground tabular-nums">{item.validCount} válidos</span></span>
                    </label>
                  );
                })}
              </div>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <div className="space-y-1.5">
                  <FieldHelpLabel htmlFor="campaign-audience-search" {...CAMPAIGN_FIELD_HELP.audienceSearch} />
                  <Input
                    id="campaign-audience-search"
                    aria-label="Buscar na audiência"
                    value={draft.audienceFilter.search ?? ''}
                    onChange={(event) => setDraft((current) => ({ ...current, audienceFilter: { ...current.audienceFilter, search: event.target.value } }))}
                    placeholder="nome, telefone, demanda"
                  />
                </div>
                <div className="space-y-1.5">
                  <FieldHelpLabel htmlFor="campaign-funnel-stage" {...CAMPAIGN_FIELD_HELP.funnelStage} />
                  <Select
                    value={draft.audienceFilter.columnIds[0] ?? 'all'}
                    onValueChange={(value) => setDraft((current) => ({ ...current, audienceFilter: { ...current.audienceFilter, columnIds: value === 'all' ? [] : [value] } }))}
                  >
                    <SelectTrigger id="campaign-funnel-stage">
                      <SelectValue placeholder="Todas" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Todas</SelectItem>
                      {COLUMN_OPTIONS.map((column) => (
                        <SelectItem key={column.value} value={column.value}>{column.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <FieldHelpLabel htmlFor="campaign-classification" {...CAMPAIGN_FIELD_HELP.classification} />
                  <Select
                    value={draft.audienceFilter.classifications[0] ?? 'all'}
                    onValueChange={(value) => setDraft((current) => ({ ...current, audienceFilter: { ...current.audienceFilter, classifications: value === 'all' ? [] : [value] } }))}
                  >
                    <SelectTrigger id="campaign-classification">
                      <SelectValue placeholder="Todas" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Todas</SelectItem>
                      {CLASSIFICATION_OPTIONS.map((classification) => (
                        <SelectItem key={classification} value={classification}>{classification}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <FieldHelpLabel htmlFor="campaign-assignee" {...CAMPAIGN_FIELD_HELP.assignee} />
                  <Select
                    value={draft.audienceFilter.assignedUserIds[0] ?? 'all'}
                    onValueChange={(value) => setDraft((current) => ({ ...current, audienceFilter: { ...current.audienceFilter, assignedUserIds: value === 'all' ? [] : [value] } }))}
                  >
                    <SelectTrigger id="campaign-assignee">
                      <SelectValue placeholder="Todos" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Todos</SelectItem>
                      {assignees.map((assignee) => (
                        <SelectItem key={assignee.id} value={assignee.id}>{assignee.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-3">
                <div className="space-y-1.5">
                  <FieldHelpLabel htmlFor="campaign-queue" {...CAMPAIGN_FIELD_HELP.queue} />
                  <Select
                    value={draft.audienceFilter.queueStatuses[0] ?? 'all'}
                    onValueChange={(value) => setDraft((current) => ({ ...current, audienceFilter: { ...current.audienceFilter, queueStatuses: value === 'all' ? [] : [value] } }))}
                  >
                    <SelectTrigger id="campaign-queue">
                      <SelectValue placeholder="Todas" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Todas</SelectItem>
                      {QUEUE_OPTIONS.map((item) => (
                        <SelectItem key={item} value={item}>{item}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex items-center justify-between rounded-xl border p-4">
                  <FieldHelpLabel htmlFor="campaign-include-unlinked" label="Incluir sem card" description={CAMPAIGN_FIELD_HELP.unlinkedContacts.description} whereToFind={CAMPAIGN_FIELD_HELP.unlinkedContacts.whereToFind} />
                  <Switch
                    id="campaign-include-unlinked"
                    checked={draft.audienceFilter.includeUnlinkedContacts}
                    onCheckedChange={(checked) => setDraft((current) => ({ ...current, audienceFilter: { ...current.audienceFilter, includeUnlinkedContacts: checked } }))}
                  />
                </div>
                <div className="flex items-center justify-between rounded-xl border p-4">
                  <FieldHelpLabel htmlFor="campaign-include-blocked" label="Incluir bloqueados" description={CAMPAIGN_FIELD_HELP.blockedContacts.description} whereToFind={CAMPAIGN_FIELD_HELP.blockedContacts.whereToFind} />
                  <Switch
                    id="campaign-include-blocked"
                    checked={draft.audienceFilter.includeBlocked}
                    onCheckedChange={(checked) => setDraft((current) => ({ ...current, audienceFilter: { ...current.audienceFilter, includeBlocked: checked } }))}
                  />
                </div>
              </div>
            </div>

            {campaignBuilderStep === 4 && <div className="space-y-4">
              <div className="border-y py-4"><p className="text-sm font-semibold">Revisão antes de criar</p><p className="mt-1 text-xs text-muted-foreground">Confira a rota, o modelo aprovado, a audiência e a continuação do atendimento.</p></div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="border p-3"><span className="text-[10px] uppercase text-muted-foreground">Campanha</span><strong className="mt-1 block text-xs">{draft.title || 'Título pendente'}</strong></div>
                <div className="border p-3"><span className="text-[10px] uppercase text-muted-foreground">Agendamento</span><strong className="mt-1 block text-xs">{draft.scheduledAt ? new Date(draft.scheduledAt).toLocaleString('pt-BR') : 'Envio manual'}</strong></div>
                <div className="border p-3"><span className="text-[10px] uppercase text-muted-foreground">Audiência</span><strong className="mt-1 block text-xs">{[draft.audienceFilter.includeCrmContacts ? 'CRM' : null, draft.audienceFilter.audienceListIds.length ? `${draft.audienceFilter.audienceListIds.length} lista(s)` : null].filter(Boolean).join(' · ') || 'Nenhuma fonte selecionada'}</strong></div>
                <div className="border p-3"><span className="text-[10px] uppercase text-muted-foreground">Continuação</span><strong className="mt-1 block text-xs">{draft.followUpFlowId ? `Flow · ${draft.flowStartMode}` : 'Sem flow vinculado'}</strong></div>
              </div>
              {(!draft.whatsappConnectionId || !draft.metaTemplateId || !draft.title) && <p role="alert" className="border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">Volte à etapa Mensagem e complete número oficial, template e título.</p>}
              {!draft.audienceFilter.includeCrmContacts && !draft.audienceFilter.audienceListIds.length && <p role="alert" className="border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">Volte à etapa Audiência e escolha ao menos uma fonte de contatos.</p>}
            </div>}

            <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-4">
              <Button type="button" variant="outline" onClick={() => setCampaignBuilderStep((current) => Math.max(1, current - 1) as 1 | 2 | 3 | 4)} disabled={campaignBuilderStep === 1}>Voltar</Button>
              {campaignBuilderStep < 4 ? <Button type="button" onClick={() => setCampaignBuilderStep((current) => Math.min(4, current + 1) as 1 | 2 | 3 | 4)} disabled={(campaignBuilderStep === 1 && (!draft.whatsappConnectionId || !draft.metaTemplateId || !draft.title)) || (campaignBuilderStep === 3 && !draft.audienceFilter.includeCrmContacts && !draft.audienceFilter.audienceListIds.length)}>Continuar</Button> : <Button onClick={() => createMutation.mutate()} disabled={!draft.whatsappConnectionId || !draft.metaTemplateId || !draft.title || (!draft.audienceFilter.includeCrmContacts && !draft.audienceFilter.audienceListIds.length) || createMutation.isPending}>{createMutation.isPending ? 'Criando...' : 'Criar campanha em rascunho'}</Button>}
            </div>
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg font-semibold">Opt-in e blacklist</CardTitle>
              <CardDescription>
                Garanta consentimento antes do disparo e bloqueie números que não devem entrar em novas campanhas.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="space-y-3">
                <p className="text-sm font-medium">Cadastrar opt-in</p>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor="consent-phone">Telefone</Label>
                    <Input id="consent-phone" value={optInForm.phone} onChange={(event) => setOptInForm((current) => ({ ...current, phone: event.target.value }))} onBlur={() => normalizedOptInPhone && setOptInForm((current) => ({ ...current, phone: normalizedOptInPhone }))} placeholder="5561999999999" inputMode="tel" autoComplete="tel" aria-describedby="consent-phone-help" aria-invalid={Boolean(optInForm.phone && !normalizedOptInPhone)} />
                    <p id="consent-phone-help" className="text-xs text-muted-foreground">{META_PHONE_HELP}</p>
                  </div>
                  <div className="space-y-1.5">
                    <FieldHelpLabel htmlFor="consent-status" {...CAMPAIGN_FIELD_HELP.consentStatus} />
                    <Select value={optInForm.status} onValueChange={(value) => setOptInForm((current) => ({ ...current, status: value }))}>
                      <SelectTrigger id="consent-status"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="opted_in">Opt-in</SelectItem>
                        <SelectItem value="opted_out">Opt-out</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <FieldHelpLabel htmlFor="consent-source" {...CAMPAIGN_FIELD_HELP.consentSource} />
                    <Input id="consent-source" value={optInForm.source} onChange={(event) => setOptInForm((current) => ({ ...current, source: event.target.value }))} placeholder="formulário, contrato..." />
                  </div>
                  <div className="flex items-end justify-end sm:col-span-2"><Button className="w-full sm:w-auto" onClick={() => optInMutation.mutate()} disabled={!normalizedOptInPhone || optInMutation.isPending}>Salvar opt-in</Button></div>
                </div>
              </div>

              <div className="space-y-3">
                <p className="text-sm font-medium">Cadastrar supressão</p>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor="suppression-phone">Telefone</Label>
                    <Input id="suppression-phone" value={suppressionForm.phone} onChange={(event) => setSuppressionForm((current) => ({ ...current, phone: event.target.value }))} onBlur={() => normalizedSuppressionPhone && setSuppressionForm((current) => ({ ...current, phone: normalizedSuppressionPhone }))} placeholder="5561999999999" inputMode="tel" autoComplete="tel" aria-describedby="suppression-phone-help" aria-invalid={Boolean(suppressionForm.phone && !normalizedSuppressionPhone)} />
                    <p id="suppression-phone-help" className="text-xs text-muted-foreground">{META_PHONE_HELP}</p>
                  </div>
                  <div className="space-y-1.5">
                    <FieldHelpLabel htmlFor="suppression-reason" {...CAMPAIGN_FIELD_HELP.suppressionReason} />
                    <Input id="suppression-reason" value={suppressionForm.reason} onChange={(event) => setSuppressionForm((current) => ({ ...current, reason: event.target.value }))} placeholder="pedido do cliente..." />
                  </div>
                  <div className="flex items-end">
                    <div className="flex h-10 w-full items-center justify-between gap-2 rounded-xl border px-3">
                      <FieldHelpLabel htmlFor="suppression-active" label="Ativa" description={CAMPAIGN_FIELD_HELP.suppressionActive.description} whereToFind={CAMPAIGN_FIELD_HELP.suppressionActive.whereToFind} />
                      <Switch id="suppression-active" checked={suppressionForm.isActive} onCheckedChange={(checked) => setSuppressionForm((current) => ({ ...current, isActive: checked }))} />
                    </div>
                  </div>
                  <div className="flex items-end justify-end sm:col-span-2"><Button className="w-full sm:w-auto" onClick={() => suppressionMutation.mutate()} disabled={!normalizedSuppressionPhone || suppressionMutation.isPending}>Salvar supressão</Button></div>
                </div>
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <div className="rounded-xl border p-3">
                  <p className="text-sm font-medium">Opt-ins recentes</p>
                  <ScrollArea className="mt-3 h-48">
                    <div className="space-y-2">
                      {optInsQuery.isError && (
                        <p role="status" aria-label="Erro ao carregar opt-ins" className="text-xs text-destructive">
                          Não foi possível carregar os opt-ins.{' '}
                          <button type="button" className="underline" onClick={() => { void optInsQuery.refetch(); }}>Tentar novamente</button>
                        </p>
                      )}
                      {optInsQuery.isSuccess && !(optInsQuery.data ?? []).length && (
                        <p className="text-xs text-muted-foreground">Nenhum opt-in registrado.</p>
                      )}
                      {(optInsQuery.data ?? []).slice(0, 8).map((item) => (
                        <div key={item.id} className="rounded-lg border bg-muted/20 p-3 text-sm">
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-medium">{item.phone}</span>
                            <Badge variant={item.status === 'opted_in' ? 'secondary' : 'outline'}>{item.status}</Badge>
                          </div>
                          <p className="mt-1 text-xs text-muted-foreground">{item.source || 'manual'} · {new Date(item.updatedAt).toLocaleString('pt-BR')}</p>
                        </div>
                      ))}
                    </div>
                  </ScrollArea>
                </div>
                <div className="rounded-xl border p-3">
                  <p className="text-sm font-medium">Supressões ativas</p>
                  <ScrollArea className="mt-3 h-48">
                    <div className="space-y-2">
                      {suppressionsQuery.isError && (
                        <p role="status" aria-label="Erro ao carregar supressões" className="text-xs text-destructive">
                          Não foi possível carregar as supressões.{' '}
                          <button type="button" className="underline" onClick={() => { void suppressionsQuery.refetch(); }}>Tentar novamente</button>
                        </p>
                      )}
                      {suppressionsQuery.isSuccess && !(suppressionsQuery.data ?? []).length && (
                        <p className="text-xs text-muted-foreground">Nenhuma supressão registrada.</p>
                      )}
                      {(suppressionsQuery.data ?? []).slice(0, 8).map((item) => (
                        <div key={item.id} className="rounded-lg border bg-muted/20 p-3 text-sm">
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-medium">{item.phone}</span>
                            <Badge variant={item.isActive ? 'destructive' : 'outline'}>{item.isActive ? 'ativa' : 'inativa'}</Badge>
                          </div>
                          <p className="mt-1 text-xs text-muted-foreground">{item.reason} · {new Date(item.updatedAt).toLocaleString('pt-BR')}</p>
                        </div>
                      ))}
                    </div>
                  </ScrollArea>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      <Card ref={campaignHistorySectionRef} tabIndex={-1} className="scroll-mt-4 outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <CardHeader>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div><CardTitle className="text-lg font-semibold">Histórico de campanhas</CardTitle><CardDescription>Consulte campanhas criadas, teste a audiência antes do lançamento e acompanhe entregas, respostas e falhas.</CardDescription></div>
            <div className="flex flex-wrap items-center gap-2">
              {embedded ? (
                <>
                  <Button variant="outline" size="sm" disabled={!onConversationFilter} onClick={() => onConversationFilter?.('com_disparo')}><Megaphone className="mr-1.5 h-4 w-4" />Com disparo</Button>
                  <Button variant="outline" size="sm" disabled={!onConversationFilter} onClick={() => onConversationFilter?.('disparo_sem_retorno')}><MessageCircleReply className="mr-1.5 h-4 w-4" />Sem retorno</Button>
                  <Button variant="ghost" size="sm" disabled={!onConversationFilter} onClick={() => onConversationFilter?.('todos')}><History className="mr-1.5 h-4 w-4" />Histórico da conversa</Button>
                </>
              ) : (
                <>
                  <Button asChild variant="outline" size="sm"><Link to="/app/conversas?broadcast=with"><Megaphone className="mr-1.5 h-4 w-4" />Com disparo</Link></Button>
                  <Button asChild variant="outline" size="sm"><Link to="/app/conversas?broadcast=awaiting_reply"><MessageCircleReply className="mr-1.5 h-4 w-4" />Sem retorno</Link></Button>
                  <Button asChild variant="ghost" size="sm"><Link to="/app/conversas"><History className="mr-1.5 h-4 w-4" />Histórico de conversas</Link></Button>
                </>
              )}
              <FieldHelp {...CAMPAIGN_FIELD_HELP.advancedCampaignFilter} />
              <PersonalizedFilterBuilder scope="campaigns" fields={campaignFilterFields} value={campaignFilter} onChange={setCampaignFilter} />
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {visibleCampaigns.map((campaign) => (
              <CampaignRow
                key={campaign.id}
                campaign={campaign}
                commercialCharge={can('finance', 'view') ? commercialChargesQuery.data?.campaigns.find((item) => item.campaignId === campaign.id) ?? null : undefined}
                busy={actionMutation.isPending}
                flowName={(flowsQuery.data ?? []).find((flow) => flow.id === campaign.followUpFlowId)?.name ?? null}
                launchBlockedReason={campaignLaunchBlockReason()}
                launchWarning={contactsCapacity.launchWarning}
                launchError={campaignActionErrors[campaign.id] ?? null}
                onAction={(action) => actionMutation.mutate({ action, campaignId: campaign.id })}
                onOpenAwaitingReplies={onConversationFilter ? () => onConversationFilter('disparo_sem_retorno', campaign.id) : undefined}
                allowExternalConversationLinks={!embedded}
              />
            ))}
            {visibleCampaigns.length === 0 && (
              <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
                Nenhuma campanha criada ainda. Salve um rascunho acima para começar.
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

const CAMPAIGN_STATUS_LABELS: Record<WhatsAppCampaign['status'], string> = {
  draft: 'em rascunho',
  scheduled: 'agendada',
  running: 'em execução',
  paused: 'pausada',
  completed: 'concluída',
  stopped: 'encerrada',
  failed: 'com falha',
};

function CampaignRow({
  campaign,
  commercialCharge,
  busy,
  flowName,
  launchBlockedReason,
  launchWarning,
  launchError,
  onAction,
  onOpenAwaitingReplies,
  allowExternalConversationLinks,
}: {
  campaign: WhatsAppCampaign;
  commercialCharge?: CampaignMetaCommercialCharge | null;
  busy: boolean;
  flowName: string | null;
  launchBlockedReason: string | null;
  launchWarning: string | null;
  launchError: CampaignActionError | null;
  onAction: (action: 'launch' | 'dry-run' | 'pause' | 'resume' | 'stop') => void;
  onOpenAwaitingReplies?: () => void;
  allowExternalConversationLinks: boolean;
}) {
  const recipientsWithError = campaign.sampleRecipients.filter((recipient) => Boolean(recipient.lastError));
  const campaignLastError = recipientsWithError[0]?.lastError ?? null;
  const failureReasonSummary = Object.entries(
    recipientsWithError
      .filter((recipient) => recipient.status === 'failed' || recipient.status === 'skipped')
      .reduce<Record<string, number>>((acc, recipient) => {
        const reason = recipient.lastError!.trim();
        acc[reason] = (acc[reason] ?? 0) + 1;
        return acc;
      }, {}),
  ).sort(([, a], [, b]) => b - a);
  const failureTooltip = failureReasonSummary
    .map(([reason, count]) => (count > 1 ? `${reason} (${count}x)` : reason))
    .join('\n');
  const launchStateReason = campaign.status !== 'draft'
    ? `Lançar só está disponível em rascunho; esta campanha está ${CAMPAIGN_STATUS_LABELS[campaign.status] ?? campaign.status}.`
    : campaign.eligibleRecipients === 0
      ? 'Nenhum destinatário elegível: revise a audiência ou rode o dry-run antes de lançar.'
      : null;
  const launchDisabledReason = launchBlockedReason ?? launchStateReason;
  return (
    <div className="rounded-2xl border p-4 space-y-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-semibold">{campaign.title}</p>
            <Badge variant="outline">{campaign.status}</Badge>
            {campaign.templateSnapshot && <Badge variant="secondary">{campaign.templateSnapshot.templateName}</Badge>}
            {flowName && <Badge variant="outline"><Workflow className="mr-1 h-3 w-3" />{flowName} · {campaign.flowStartMode}</Badge>}
          </div>
          {campaign.description && <p className="text-sm text-muted-foreground">{campaign.description}</p>}
          {campaignLastError && (
            <p className="flex items-start gap-1.5 text-xs text-destructive" role="status">
              <ShieldBan className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span><strong>Último erro:</strong> {campaignLastError}</span>
            </p>
          )}
          <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground tabular-nums">
            <span>Total: {campaign.totalRecipients}</span>
            <span>Elegíveis: {campaign.eligibleRecipients}</span>
            <span>Bloqueados: {campaign.blockedRecipients}</span>
            {campaign.scheduledAt && (
              <span className="inline-flex items-center gap-1">
                <CalendarClock className="h-3.5 w-3.5" />
                {new Date(campaign.scheduledAt).toLocaleString('pt-BR')}
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {onOpenAwaitingReplies ? (
            <Button variant="outline" size="sm" onClick={onOpenAwaitingReplies}><MessageCircleReply className="mr-1.5 h-4 w-4" />Abrir sem retorno</Button>
          ) : allowExternalConversationLinks ? (
            <Button asChild variant="outline" size="sm"><Link to={`/app/conversas?broadcast=awaiting_reply&campaign=${campaign.id}`}><MessageCircleReply className="mr-1.5 h-4 w-4" />Abrir sem retorno</Link></Button>
          ) : null}
          <Button variant="outline" size="sm" onClick={() => onAction('dry-run')} disabled={busy}>
            <RefreshCw className="mr-1.5 h-4 w-4" />
            Dry-run
          </Button>
          <Button
            size="sm"
            onClick={() => onAction('launch')}
            disabled={busy || campaign.status === 'running' || Boolean(launchDisabledReason)}
            title={launchDisabledReason ?? 'O backend revalidará audiência e quotas antes do processamento.'}
          >
            <PlayCircle className="mr-1.5 h-4 w-4" />
            Lançar
          </Button>
          <Button variant="outline" size="sm" onClick={() => onAction('pause')} disabled={busy || campaign.status !== 'running'}>
            <PauseCircle className="mr-1.5 h-4 w-4" />
            Pausar
          </Button>
          <Button variant="outline" size="sm" onClick={() => onAction('resume')} disabled={busy || campaign.status !== 'paused'}>
            <PlayCircle className="mr-1.5 h-4 w-4" />
            Retomar
          </Button>
          <Button variant="destructive" size="sm" onClick={() => onAction('stop')} disabled={busy || campaign.status === 'completed' || campaign.status === 'stopped'}>
            <StopCircle className="mr-1.5 h-4 w-4" />
            Encerrar
          </Button>
          {launchBlockedReason && campaign.status === 'draft' && (
            <p className="basis-full text-xs text-destructive" role="alert">
              Lançamento bloqueado: {launchBlockedReason} O dry-run e o histórico continuam disponíveis.
            </p>
          )}
          {launchWarning && campaign.status === 'draft' && (
            <p className="basis-full text-xs text-amber-700 dark:text-amber-300" role="status">
              Atenção: {launchWarning}
            </p>
          )}
          {!launchBlockedReason && launchStateReason && campaign.status === 'draft' && (
            <p className="basis-full text-xs text-amber-700 dark:text-amber-300" role="status">
              {launchStateReason}
            </p>
          )}
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-4">
        <MetricCard label="Enviadas" value={campaign.sentCount} />
        <MetricCard label="Entregues" value={campaign.deliveredCount} />
        <MetricCard label="Lidas" value={campaign.readCount} />
        <div title={failureTooltip || undefined}>
          <MetricCard label="Falhas" value={launchError ? '—' : campaign.failedCount} />
        </div>
      </div>

      {launchError && (
        <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-3" role="alert">
          <p className="text-sm font-semibold text-destructive">Lançamento abortado</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Código {launchError.code}: {launchError.message} Nenhum total de falhas foi confirmado.
          </p>
        </div>
      )}

      {failureReasonSummary.length > 0 && (
        <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-3">
          <p className="text-xs font-medium text-destructive">Principais motivos de falha (amostra recente)</p>
          <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
            {failureReasonSummary.slice(0, 4).map(([reason, count]) => (
              <li key={reason} className="flex items-start justify-between gap-3">
                <span className="min-w-0 break-words">{reason}</span>
                <Badge variant="outline" className="shrink-0">{count}x</Badge>
              </li>
            ))}
          </ul>
        </div>
      )}

      {commercialCharge !== undefined && <CampaignCommercialChargeStrip value={commercialCharge} />}

      {campaign.audience && (
        <div className="rounded-xl border bg-muted/20 p-4">
          <p className="text-sm font-medium">Amostra da audiência</p>
          <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {campaign.audience.sampleRecipients.slice(0, 6).map((recipient, index) => (
              <div key={`${campaign.id}-${index}`} className="rounded-lg border bg-background p-3 text-sm">
                <p className="font-semibold truncate">{recipient.name || recipient.phone}</p>
                <p className="text-xs text-muted-foreground tabular-nums">{recipient.phone}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function CampaignCommercialChargeStrip({ value }: { value: CampaignMetaCommercialCharge | null }) {
  if (!value) {
    return <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">Cobrança oficial ainda não calculada para esta campanha.</div>;
  }
  const format = (amount: string, currency: string) => new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 8,
  }).format(Number(amount));
  return (
    <div className="rounded-lg border bg-muted/10 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><p className="text-sm font-medium">Cobrança da API oficial</p><p className="text-xs text-muted-foreground">Confirmada por entrega; projeção pela audiência elegível.</p></div>
        {value.missingRates.length > 0 && <Badge variant="outline" className="border-amber-300 text-amber-800 dark:border-amber-800 dark:text-amber-200">{value.missingRates.reduce((sum, item) => sum + item.recipientCount, 0)} sem tarifa</Badge>}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {value.totalsByCurrency.map((total) => (
          <div key={total.currency} className="rounded-md border bg-background px-3 py-2 text-xs tabular-nums">
            <strong>{format(total.confirmedCommercialCharge, total.currency)}</strong>
            <span className="ml-2 text-muted-foreground">confirmado · {format(total.estimatedCommercialCharge, total.currency)} projetado</span>
          </div>
        ))}
        {!value.totalsByCurrency.length && <span className="text-xs text-muted-foreground">Nenhum valor calculável até o cadastro da tarifa vigente.</span>}
      </div>
    </div>
  );
}

function CampaignParameterFields({
  scope,
  parameters,
  values,
  onChange,
}: {
  scope: 'header' | 'body';
  parameters: CampaignTemplateParameter[];
  values: string[];
  onChange: (index: number, value: string) => void;
}) {
  const isHeader = scope === 'header';
  const label = isHeader ? 'Parâmetros do cabeçalho' : 'Parâmetros da mensagem';
  const help = isHeader ? CAMPAIGN_FIELD_HELP.headerParameters : CAMPAIGN_FIELD_HELP.bodyParameters;
  return (
    <div className="space-y-2">
      <div className="flex min-h-7 items-center gap-1">
        <p className="text-sm font-medium">{label}</p>
        <FieldHelp {...help} />
      </div>
      {parameters.length === 0 ? (
        <p className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">
          Este template não possui variáveis no {isHeader ? 'cabeçalho' : 'corpo'}.
        </p>
      ) : (
        parameters.map((parameter, index) => {
          const id = `campaign-${scope}-parameter-${parameter.position}`;
          const variable = `{{${parameter.position}}}`;
          return (
            <div key={`${scope}-${parameter.position}`} className="space-y-1.5">
              <Label htmlFor={id}>{isHeader ? 'Cabeçalho' : 'Mensagem'} {variable}</Label>
              <Input
                id={id}
                aria-label={`${label} ${variable}`}
                value={values[index] ?? ''}
                onChange={(event) => onChange(index, event.target.value)}
                placeholder={parameter.example ? `Exemplo: ${parameter.example}` : `Valor para ${variable}`}
              />
              {parameter.example && <p className="text-xs text-muted-foreground">Exemplo do template: {parameter.example}</p>}
            </div>
          );
        })
      )}
    </div>
  );
}

function getApprovalDisplay(value?: 'high' | 'medium' | 'low') {
  if (value === 'high') return { label: 'Boa chance de aprovação', badgeClass: 'border-emerald-300 text-emerald-800 dark:border-emerald-800 dark:text-emerald-200' };
  if (value === 'medium') return { label: 'Chance média', badgeClass: 'border-amber-300 text-amber-800 dark:border-amber-800 dark:text-amber-200' };
  if (value === 'low') return { label: 'Baixa chance', badgeClass: 'border-red-300 text-red-800 dark:border-red-800 dark:text-red-200' };
  return { label: 'Aguardando análise', badgeClass: 'border-slate-300 text-slate-700 dark:border-slate-700 dark:text-slate-200' };
}

function formatTemplateCategory(value: 'utility' | 'marketing' | 'authentication') {
  if (value === 'utility') return 'Utilidade';
  if (value === 'authentication') return 'Autenticação';
  return 'Marketing';
}

function MetricCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-xl border bg-muted/10 p-4">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-2 text-2xl font-bold tabular-nums">{value}</p>
    </div>
  );
}
