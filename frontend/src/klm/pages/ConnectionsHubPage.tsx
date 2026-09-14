import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Clock,
  Copy,
  ExternalLink,
  Facebook,
  Instagram,
  KeyRound,
  Loader2,
  MessageCircle,
  PlugZap,
  RefreshCw,
  ShieldQuestion,
  Sparkles,
  Webhook,
} from 'lucide-react';

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useToast } from '@/hooks/use-toast';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { OperationalCopilot } from '@/klm/components/OperationalCopilot';
import { WhatsAppConnectionHealthCard } from '@/klm/components/WhatsAppConnectionHealthCard';
import { klmApi, type KlmOperationalCopilotAction } from '@/klm/api';
import { resolveApiHttpUrl } from '@/services/api-origin';
import { ApiError } from '@/services/http';
import {
  createManualWhatsAppConnection,
  fetchWhatsAppConnections,
  fetchWhatsAppWebhookInfo,
} from '@/services/whatsapp';
import {
  createInstagramConnection,
  fetchInstagramAuthorizeUrl,
  fetchInstagramConnections,
  fetchInstagramTokenStatus,
} from '@/services/instagramOauth';
import type { InstagramTokenStatus } from '@/services/instagramOauth';
import {
  createMessengerConnection,
  fetchMessengerAuthorizeUrl,
  fetchMessengerConnections,
  fetchMessengerTokenStatus,
} from '@/services/messengerOauth';
import type { MessengerTokenStatus } from '@/services/messengerOauth';
import { useOptionalAppStore } from '@/store/app-context';
import type { WhatsAppConnectionSummary } from '@/types';
import { PermissionGate } from '@/klm/components/PermissionGate';
import { MetaEmbeddedSignupButton } from '@/components/meta/MetaEmbeddedSignupButton';
import { completeMetaEmbeddedSignup } from '@/services/whatsapp';

type StepStatus = 'pending' | 'done' | 'error';

type WizardPersistedState = {
  checks: Record<string, boolean>;
  fields: {
    phoneNumberId: string;
    businessAccountId: string;
  };
  errorMessage: string | null;
  showWizard: boolean;
};

const STORAGE_KEY_PREFIX = 'klm_wizard_whatsapp_b1';

function storageKey(workspaceId: string | null | undefined) {
  return `${STORAGE_KEY_PREFIX}:${workspaceId || 'sem-workspace'}`;
}

function loadPersistedState(workspaceId: string | null | undefined): WizardPersistedState {
  const fallback: WizardPersistedState = {
    checks: {},
    fields: { phoneNumberId: '', businessAccountId: '' },
    errorMessage: null,
    showWizard: false,
  };
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = window.localStorage.getItem(storageKey(workspaceId));
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    return {
      checks: parsed?.checks && typeof parsed.checks === 'object' ? parsed.checks : {},
      fields: {
        phoneNumberId: typeof parsed?.fields?.phoneNumberId === 'string' ? parsed.fields.phoneNumberId : '',
        businessAccountId: typeof parsed?.fields?.businessAccountId === 'string' ? parsed.fields.businessAccountId : '',
      },
      errorMessage: typeof parsed?.errorMessage === 'string' ? parsed.errorMessage : null,
      showWizard: Boolean(parsed?.showWizard),
    };
  } catch {
    return fallback;
  }
}

function savePersistedState(workspaceId: string | null | undefined, state: WizardPersistedState) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(storageKey(workspaceId), JSON.stringify(state));
  } catch {
    // Armazenamento local indisponível (modo privado/cota excedida): a retomada
    // simplesmente não fica salva; o wizard continua funcionando nesta sessão.
  }
}

type ChecklistStep = {
  id: string;
  title: string;
  body: string[];
  checklist: { id: string; label: string }[];
  docsUrl?: string;
};

const CHECKLIST_STEPS: ChecklistStep[] = [
  {
    id: 'prerequisitos',
    title: 'Passo 1 · Pré-requisitos',
    body: [
      'Antes de abrir o developers.facebook.com, confirme três coisas para não travar no meio do processo.',
    ],
    checklist: [
      { id: 'conta_meta', label: 'Você tem uma conta Meta (Facebook) pessoal ativa que poderá administrar o app Business.' },
      { id: 'numero_livre', label: 'O número de telefone que vai virar o WhatsApp oficial não está registrado em nenhum outro WhatsApp (app comum, Business App ou outra WABA) — remova o WhatsApp desse número do celular antes de prosseguir.' },
      { id: 'display_name', label: 'Você já definiu o nome de exibição (display name) do número seguindo as diretrizes da Meta: nome real do escritório ou marca, sem emojis, sem termos genéricos como "vendas" ou "promoção", compatível com o que aparece no seu site.' },
    ],
  },
  {
    id: 'criar_app',
    title: 'Passo 2 · Criar o app Business',
    body: [
      'No developers.facebook.com, clique em "Meus Apps" → "Criar App". Escolha o tipo "Business" e, quando perguntado o caso de uso, selecione "Business Messaging" (ou "Outro" → "Business" nas versões mais novas do assistente).',
      'Dê um nome ao app (ex.: "MV Advocacia WhatsApp") e vincule-o à conta Business Manager do escritório quando solicitado.',
    ],
    checklist: [
      { id: 'app_criado', label: 'Criei o app tipo Business com o caso de uso de mensagens (Business Messaging).' },
    ],
    docsUrl: 'https://developers.facebook.com/apps',
  },
  {
    id: 'produto_whatsapp',
    title: 'Passo 3 · Adicionar o produto WhatsApp e vincular a WABA',
    body: [
      'Dentro do app, na lista de produtos, clique em "Adicionar produto" e escolha "WhatsApp". A tela de configuração do WhatsApp vai pedir para você criar uma nova WhatsApp Business Account (WABA) ou vincular uma existente.',
      'Adicione o número de telefone do escritório, escolha a verificação por SMS ou chamada de voz e confirme o código recebido. Depois defina o display name (o mesmo validado no Passo 1).',
    ],
    checklist: [
      { id: 'waba_numero', label: 'Criei/vinculei a WABA e cadastrei o número, com verificação SMS ou voz concluída.' },
    ],
  },
  {
    id: 'business_verification',
    title: 'Passo 4 · Business Verification do escritório',
    body: [
      'No business.facebook.com/settings, na seção "Segurança do negócio", inicie a "Verificação da empresa". A Meta vai pedir documentos do escritório (CNPJ, comprovante de endereço, e-mail no domínio da empresa ou telefone comercial).',
      'Sem essa verificação concluída, o número fica limitado a enviar mensagens apenas para números de teste cadastrados manualmente — ou seja, nenhum cliente real recebe mensagem ainda. O prazo de análise da Meta costuma levar de 1 a 5 dias úteis.',
    ],
    checklist: [
      { id: 'verificacao_iniciada', label: 'Iniciei (ou já concluí) a Business Verification do escritório no Business Manager.' },
    ],
    docsUrl: 'https://business.facebook.com/settings/security-center',
  },
  {
    id: 'system_user',
    title: 'Passo 5 · Criar System User e gerar o token permanente',
    body: [
      'Em business.facebook.com/settings → "Usuários" → "Usuários do sistema", crie um System User com papel de administrador.',
      'Gere um token de acesso para ele e, na lista de permissões, marque obrigatoriamente whatsapp_business_messaging e whatsapp_business_management. Escolha validade "Nunca expira" (token permanente) e copie o valor imediatamente — a Meta só mostra uma vez.',
    ],
    checklist: [
      { id: 'system_user_token', label: 'Criei o System User e gerei o token permanente com os escopos whatsapp_business_messaging e whatsapp_business_management.' },
    ],
  },
];

type ErrorGuide = {
  match: (message: string) => boolean;
  humanMessage: string;
  nextStep: string;
};

const ERROR_GUIDES: ErrorGuide[] = [
  {
    match: (m) => /\bKLM-EXT-001\b/i.test(m),
    humanMessage: 'A conexão não foi enviada à Meta porque as entregas externas estão desabilitadas neste ambiente.',
    nextStep: 'Peça ao responsável pela operação do ambiente para habilitar o gate de entregas externas e reiniciar o serviço. Não gere outro token nem altere as credenciais por causa deste erro.',
  },
  {
    match: (m) => /token|access.?token|autoriza|permiss/i.test(m),
    humanMessage: 'O token informado não foi aceito pela Meta (pode estar expirado, incompleto ou sem os escopos certos).',
    nextStep: 'Volte ao Passo 5, gere um novo token permanente do System User conferindo os escopos whatsapp_business_messaging e whatsapp_business_management, e cole o valor novo aqui.',
  },
  {
    match: (m) => /phone.?number.?id|número|numero/i.test(m),
    humanMessage: 'O Phone Number ID informado não corresponde a um número WhatsApp válido para esta conta.',
    nextStep: 'Copie o Phone Number ID diretamente da tela do produto WhatsApp no developers.facebook.com (não é o número de telefone, é o ID numérico ao lado dele) e tente novamente.',
  },
  {
    match: (m) => /waba|business.?account/i.test(m),
    humanMessage: 'O WhatsApp Business Account ID informado não foi encontrado ou não pertence ao seu app.',
    nextStep: 'Confira o WABA ID na mesma tela do produto WhatsApp (em "Detalhes da conta") e cole o valor exato, sem espaços.',
  },
  {
    match: () => true,
    humanMessage: 'A Meta recusou a conexão com os dados enviados.',
    nextStep: 'Revise token, Phone Number ID e WABA ID um a um. Se o erro continuar, confira o troubleshooting abaixo — os quatro problemas mais comuns nesta etapa estão listados ali.',
  },
];

function describeError(error: unknown): { humanMessage: string; nextStep: string } {
  let raw = 'Não foi possível concluir a conexão.';
  if (error instanceof ApiError) {
    const detail = error.detail as { code?: string; message?: string; detail?: string } | string | undefined;
    if (typeof detail === 'string') raw = detail;
    else if (detail && typeof detail === 'object') {
      if (typeof detail.message === 'string') raw = detail.message;
      if (typeof detail.code === 'string') raw = `${raw} ${detail.code}`;
    }
    else if (error.message) raw = error.message;
  } else if (error instanceof Error) {
    raw = error.message;
  }
  const guide = ERROR_GUIDES.find((candidate) => candidate.match(raw)) ?? ERROR_GUIDES[ERROR_GUIDES.length - 1];
  return { humanMessage: guide.humanMessage, nextStep: guide.nextStep };
}

function StepStatusIcon({ status }: { status: StepStatus }) {
  if (status === 'done') return <CheckCircle2 className="h-5 w-5 text-emerald-500" aria-label="Concluído" />;
  if (status === 'error') return <AlertTriangle className="h-5 w-5 text-red-500" aria-label="Erro" />;
  return <Circle className="h-5 w-5 text-muted-foreground" aria-label="Pendente" />;
}

function CopyableField({ label, value }: { label: string; value: string }) {
  const { toast } = useToast();
  return (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      <div className="flex items-center gap-2">
        <Input readOnly value={value} className="font-mono text-xs" onFocus={(event) => event.currentTarget.select()} />
        <Button
          type="button"
          size="icon"
          variant="outline"
          aria-label={`Copiar ${label}`}
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(value);
              toast({ title: 'Copiado', description: `${label} copiado para a área de transferência.` });
            } catch {
              toast({ title: 'Não foi possível copiar', description: 'Selecione o texto manualmente e copie.', variant: 'destructive' });
            }
          }}
        >
          <Copy className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

function CopyableCode({ label, code }: { label: string; code: string }) {
  const { toast } = useToast();
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-foreground">{label}</span>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 px-2 text-xs"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(code);
              toast({ title: 'Copiado', description: `${label} copiado para a área de transferência.` });
            } catch {
              toast({ title: 'Não foi possível copiar', description: 'Selecione o texto manualmente e copie.', variant: 'destructive' });
            }
          }}
        >
          <Copy className="mr-1 h-3 w-3" />Copiar
        </Button>
      </div>
      <pre className="overflow-x-auto rounded-md border border-border bg-secondary p-3 text-xs leading-4 text-foreground"><code>{code}</code></pre>
    </div>
  );
}

const SITE_LEAD_TEST_PAYLOAD = {
  name: 'Lead de teste — guia de conexões',
  email: 'lead.teste@meuexecutivo.local',
  phone: '5561999999999',
  message: 'Envio de teste feito pelo guia de conexões do Meu Executivo.',
  utm_source: 'guia-conexoes',
};

const ELEMENTOR_PAYLOAD_EXAMPLE = `POST {webhookUrl}
Content-Type: application/json

{
  "name": "{field:name}",
  "email": "{field:email}",
  "phone": "{field:phone}",
  "message": "{field:message}",
  "utm_source": "{field:utm_source}",
  "_hp": ""
}`;

const WIX_VELO_EXAMPLE = `// Código Velo (Wix) — página do formulário
import wixFetch from 'wix-fetch';

export function form1_wixFormSubmitted() {
  wixFetch.fetch("{webhookUrl}", {
    method: "post",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: $w("#inputName").value,
      email: $w("#inputEmail").value,
      phone: $w("#inputPhone").value,
      message: $w("#inputMessage").value,
      _hp: "",
    }),
  });
}`;

const RD_STATION_EXAMPLE = `Automação → Fluxo de automação → gatilho "Conversão em formulário"
→ ação "Enviar dados via Webhook (HTTP POST)"

URL do Webhook: {webhookUrl}
Método: POST · Formato: JSON

Mapeamento de campos:
  name  → Nome
  email → E-mail
  phone → Telefone`;

const HTML_FORM_EXAMPLE = `<form id="lead-form">
  <input name="name" placeholder="Nome" required>
  <input name="email" type="email" placeholder="E-mail">
  <input name="phone" placeholder="Telefone/WhatsApp">
  <textarea name="message" placeholder="Mensagem"></textarea>
  <input type="text" name="_hp" style="display:none" tabindex="-1" autocomplete="off">
  <button type="submit">Enviar</button>
</form>
<script>
document.getElementById('lead-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.target));
  await fetch('{webhookUrl}', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  event.target.reset();
});
</script>`;

function SiteLeadsGuideCard({ workspaceId, canManageIntegrations }: { workspaceId: string | null; canManageIntegrations: boolean }) {
  const { toast } = useToast();
  const [tokenState, setTokenState] = useState<{ token: string; webhookUrl: string } | null>(null);
  const [testResult, setTestResult] = useState<{ status: string; reason: string } | null>(null);

  const statusQuery = useQuery({
    queryKey: ['integrations-status-hub', workspaceId],
    queryFn: klmApi.integrationsStatus,
  });
  const siteLeadsStatus = statusQuery.data?.site_leads;

  const rotateMutation = useMutation({
    mutationFn: () => klmApi.rotateSiteLeadsWebhookToken(),
    onSuccess: (result) => {
      setTokenState(result);
      setTestResult(null);
      void statusQuery.refetch();
      toast({ title: 'URL do webhook gerada', description: 'Copie agora — por segurança, ela só é exibida uma vez.' });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível gerar a URL', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const testMutation = useMutation({
    mutationFn: () => {
      if (!tokenState) throw new Error('Gere a URL do webhook antes de enviar um teste.');
      return klmApi.sendTestSiteLead(tokenState.token, SITE_LEAD_TEST_PAYLOAD);
    },
    onSuccess: (result) => setTestResult(result),
    onError: (error) => {
      toast({ title: 'Falha ao enviar o teste', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const fullWebhookUrl = tokenState ? resolveApiHttpUrl(tokenState.webhookUrl, window.location.origin) : null;
  const webhookUrlPlaceholder = fullWebhookUrl ?? 'https://seu-workspace.meuexecutivo.tech/api/v1/webhooks/leads/SEU-TOKEN';

  return (
    <KlmPanel className="p-6">
      <div className="flex items-start gap-3">
        <Webhook className="mt-0.5 h-5 w-5 text-primary" />
        <div className="min-w-0 flex-1 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-base font-medium text-foreground">Leads do seu site</h3>
            <KlmPill tone={siteLeadsStatus?.connected ? 'green' : 'muted'}>
              {siteLeadsStatus?.connected ? 'Webhook configurado' : 'Ainda não configurado'}
            </KlmPill>
          </div>

          <p className="text-sm text-muted-foreground">
            Conecte o formulário do site do escritório direto ao CRM: cada envio vira um lead novo na coluna
            "Novo lead", e o lead recebe a primeira mensagem automática por um template aprovado do WhatsApp. Se
            esse primeiro contato falhar (sem template configurado, sem conexão do WhatsApp ou sem telefone
            informado no formulário), o card do lead fica sinalizado e uma tarefa é criada para um humano assumir
            manualmente — a falha nunca é silenciosa.
          </p>

          <div className="space-y-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => rotateMutation.mutate()}
              disabled={rotateMutation.isPending || !canManageIntegrations}
            >
              {rotateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {siteLeadsStatus?.connected ? 'Rotacionar URL do webhook' : 'Gerar URL do webhook'}
            </Button>
            {siteLeadsStatus?.connected && !tokenState && (
              <p className="text-xs text-muted-foreground">
                Já existe uma URL configurada para este workspace. Por segurança, o token não fica salvo em texto
                puro — para ver (ou copiar) a URL de novo é preciso rotacionar, o que invalida a URL anterior nos
                formulários já publicados.
              </p>
            )}
            {!canManageIntegrations && (
              <p className="text-xs text-muted-foreground">Sua permissão atual não inclui gestão de integrações.</p>
            )}
          </div>

          {fullWebhookUrl && (
            <div className="space-y-3">
              <CopyableField label="URL do webhook" value={fullWebhookUrl} />
              <Alert>
                <ShieldQuestion className="h-4 w-4" />
                <AlertTitle>Guarde esta URL agora</AlertTitle>
                <AlertDescription>
                  Ela não é exibida de novo depois que você sair desta tela. Cole-a na configuração do seu
                  formulário (veja o passo a passo abaixo) antes de continuar.
                </AlertDescription>
              </Alert>
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => testMutation.mutate()}
                  disabled={testMutation.isPending || !canManageIntegrations}
                >
                  {testMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Enviar teste
                </Button>
                <span className="text-xs text-muted-foreground">Envia um lead de exemplo direto para este webhook.</span>
              </div>
              {testResult && (
                testResult.status === 'accepted' ? (
                  <Alert>
                    <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                    <AlertTitle>Lead de teste caiu no CRM</AlertTitle>
                    <AlertDescription>
                      Motivo registrado pelo backend: {testResult.reason}. Confira a coluna "Novo lead" do funil.
                    </AlertDescription>
                  </Alert>
                ) : (
                  <Alert variant="destructive">
                    <AlertTriangle className="h-4 w-4" />
                    <AlertTitle>O teste foi rejeitado</AlertTitle>
                    <AlertDescription>Motivo: {testResult.reason}.</AlertDescription>
                  </Alert>
                )
              )}
            </div>
          )}

          <div className="rounded-md border border-border p-4 text-xs text-muted-foreground">
            <p className="font-medium text-foreground">Como o Meu Executivo trata cada envio</p>
            <ul className="mt-2 list-disc space-y-1 pl-4">
              <li>Aceita JSON ou form-encoded, com os nomes de campo mais comuns (name/nome, email, phone/telefone/whatsapp, message/mensagem) e qualquer utm_* enviado junto.</li>
              <li>Se o mesmo e-mail ou telefone enviar de novo em até 24h, reaproveita o lead existente em vez de duplicar.</li>
              <li>Um campo oculto chamado <code className="rounded bg-secondary px-1">_hp</code> (vazio nos formulários reais) funciona como honeypot: se vier preenchido, o envio é descartado silenciosamente.</li>
            </ul>
          </div>

          <Accordion type="single" collapsible className="w-full">
            <AccordionItem value="wordpress">
              <AccordionTrigger>WordPress / Elementor</AccordionTrigger>
              <AccordionContent className="space-y-3">
                <p>
                  No Elementor Pro, abra o formulário → aba "Ações após o envio" → adicione a ação "Webhook" e cole
                  a URL abaixo. Em WordPress sem Elementor Pro (ex.: Contact Form 7, WPForms), use um plugin de
                  webhook genérico como "WPWebhooks" ou "CF7 to Webhook" e aponte para a mesma URL.
                </p>
                <CopyableCode label="Configuração do Webhook" code={ELEMENTOR_PAYLOAD_EXAMPLE.replace('{webhookUrl}', webhookUrlPlaceholder)} />
              </AccordionContent>
            </AccordionItem>
            <AccordionItem value="wix">
              <AccordionTrigger>Wix</AccordionTrigger>
              <AccordionContent className="space-y-3">
                <p>
                  O Wix não tem um envio de webhook nativo para formulários padrão fora do plano de Automações. A
                  forma confiável é habilitar o editor de código (Velo) na página do formulário e chamar a URL no
                  evento de envio, como no exemplo abaixo.
                </p>
                <CopyableCode label="Código Velo" code={WIX_VELO_EXAMPLE.replace('{webhookUrl}', webhookUrlPlaceholder)} />
              </AccordionContent>
            </AccordionItem>
            <AccordionItem value="rdstation">
              <AccordionTrigger>RD Station</AccordionTrigger>
              <AccordionContent className="space-y-3">
                <p>
                  Crie um Fluxo de Automação com gatilho "Conversão em formulário" e adicione a ação "Enviar dados
                  via Webhook (HTTP POST)", colando a URL abaixo e mapeando nome/e-mail/telefone do lead.
                </p>
                <CopyableCode label="Configuração do fluxo" code={RD_STATION_EXAMPLE.replace('{webhookUrl}', webhookUrlPlaceholder)} />
              </AccordionContent>
            </AccordionItem>
            <AccordionItem value="html">
              <AccordionTrigger>HTML puro (qualquer site)</AccordionTrigger>
              <AccordionContent className="space-y-3">
                <p>Formulário e script prontos para colar em qualquer página estática ou builder que aceite HTML/JS customizado.</p>
                <CopyableCode label="HTML + JavaScript" code={HTML_FORM_EXAMPLE.replace('{webhookUrl}', webhookUrlPlaceholder)} />
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </div>
      </div>
    </KlmPanel>
  );
}

const IG_APP_REVIEW_CHECKLIST_STORAGE_PREFIX = 'klm_ig_app_review_checklist';

function igChecklistStorageKey(workspaceId: string | null | undefined) {
  return `${IG_APP_REVIEW_CHECKLIST_STORAGE_PREFIX}:${workspaceId || 'sem-workspace'}`;
}

const IG_APP_REVIEW_ITEMS = [
  { id: 'scope_basic', label: 'instagram_business_basic aprovado em Advanced Access (App Review).' },
  { id: 'scope_messages', label: 'instagram_business_manage_messages aprovado em Advanced Access (App Review).' },
  { id: 'scope_comments', label: 'instagram_business_manage_comments aprovado em Advanced Access (App Review).' },
  { id: 'business_verification', label: 'Business Verification do escritório concluída no Business Manager.' },
] as const;

function loadIgChecklist(workspaceId: string | null | undefined): Record<string, boolean> {
  if (typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(igChecklistStorageKey(workspaceId));
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function saveIgChecklist(workspaceId: string | null | undefined, checks: Record<string, boolean>) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(igChecklistStorageKey(workspaceId), JSON.stringify(checks));
  } catch {
    // Sem persistência local disponível: o checklist volta a marcar tudo pendente nesta sessão.
  }
}

function InstagramAppReviewChecklist({ workspaceId }: { workspaceId: string | null | undefined }) {
  const [checks, setChecks] = useState<Record<string, boolean>>(() => loadIgChecklist(workspaceId));

  useEffect(() => {
    setChecks(loadIgChecklist(workspaceId));
  }, [workspaceId]);

  useEffect(() => {
    saveIgChecklist(workspaceId, checks);
  }, [workspaceId, checks]);

  const allDone = IG_APP_REVIEW_ITEMS.every((item) => checks[item.id]);

  return (
    <KlmPanel className="p-6" tone="soft">
      <div className="flex items-start gap-3">
        <ShieldQuestion className="mt-0.5 h-5 w-5 text-primary" />
        <div className="min-w-0 flex-1 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-sm font-semibold text-foreground">Jornada A · App Review do Instagram (uma vez, para todo o escritório)</h4>
            <KlmPill tone={allDone ? 'green' : 'muted'}>{allDone ? 'concluído' : 'pendente'}</KlmPill>
          </div>
          <p className="text-sm text-muted-foreground">
            Antes de qualquer OAuth Instagram Login funcionar de verdade para os clientes, o app do Meu Executivo precisa ter Advanced
            Access aprovado pela Meta nos três escopos abaixo e a verificação da empresa concluída. Marque manualmente
            conforme cada item avança — este status é apenas informativo nesta tela; a Meta é a fonte oficial.
          </p>
          <div className="space-y-2">
            {IG_APP_REVIEW_ITEMS.map((item) => (
              <label key={item.id} className="flex items-start gap-2 text-sm">
                <Checkbox
                  checked={Boolean(checks[item.id])}
                  onCheckedChange={(value) => setChecks((prev) => ({ ...prev, [item.id]: Boolean(value) }))}
                />
                <span className={checks[item.id] ? 'text-foreground' : 'text-muted-foreground'}>{item.label}</span>
              </label>
            ))}
          </div>
        </div>
      </div>
    </KlmPanel>
  );
}

function InstagramTokenBadge({ status: tokenStatus }: { status: InstagramTokenStatus | undefined }) {
  if (!tokenStatus) return null;
  if (tokenStatus.state === 'configured') {
    return <KlmPill tone="green">Token configurado</KlmPill>;
  }
  if (tokenStatus.state === 'expiring') {
    return (
      <KlmPill tone="gold">
        Expira em {tokenStatus.expiresInDays ?? '?'} {tokenStatus.expiresInDays === 1 ? 'dia' : 'dias'}
      </KlmPill>
    );
  }
  if (tokenStatus.state === 'oauth_prepared') {
    return <KlmPill tone="gold">Preparado, aguardando aprovação de APIs externas</KlmPill>;
  }
  return <KlmPill tone="muted">Não conectado</KlmPill>;
}

function InstagramConnectionCard({ workspaceId, canManageIntegrations }: { workspaceId: string | null; canManageIntegrations: boolean }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [accountName, setAccountName] = useState('');
  const [username, setUsername] = useState('');

  const connectionsQuery = useQuery({
    queryKey: ['instagram-connections-hub', workspaceId],
    queryFn: fetchInstagramConnections,
  });

  const connection = connectionsQuery.data?.[0];

  const tokenStatusQuery = useQuery({
    queryKey: ['instagram-token-status', connection?.id],
    queryFn: () => fetchInstagramTokenStatus(connection!.id),
    enabled: Boolean(connection?.id),
  });

  const createMutation = useMutation({
    mutationFn: () => createInstagramConnection({ accountName: accountName.trim() || undefined, username: username.trim() || undefined }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['instagram-connections-hub', workspaceId] });
      toast({ title: 'Conexão Instagram criada', description: 'Cadastro local salvo; agora inicie a autorização OAuth abaixo.' });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível criar a conexão', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const authorizeMutation = useMutation({
    mutationFn: () => {
      const redirectUri = `${window.location.origin}/app/conexoes/instagram/callback`;
      return fetchInstagramAuthorizeUrl(connection!.id, redirectUri);
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['instagram-token-status', connection?.id] });
      window.open(result.authorizationUrl, '_self');
    },
    onError: (error) => {
      toast({ title: 'Não foi possível preparar a autorização', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  return (
    <KlmPanel className="p-6">
      <div className="flex items-start gap-3">
        <Instagram className="mt-0.5 h-5 w-5 text-primary" />
        <div className="min-w-0 flex-1 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-base font-medium text-foreground">Conectar Instagram</h3>
            <InstagramTokenBadge status={tokenStatusQuery.data} />
          </div>

          {!connection && !connectionsQuery.isLoading && (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Ainda não há conexão Instagram cadastrada neste workspace. Informe o nome da conta profissional (Business ou
                Creator) para começar — o OAuth real com a Meta acontece no próximo passo.
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1">
                  <Label htmlFor="ig-account-name">Nome da conta</Label>
                  <Input id="ig-account-name" value={accountName} onChange={(event) => setAccountName(event.target.value)} placeholder="Perfil Oficial do Escritório" />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="ig-username">@usuário (opcional)</Label>
                  <Input id="ig-username" value={username} onChange={(event) => setUsername(event.target.value)} placeholder="@escritorio" />
                </div>
              </div>
              <Button type="button" onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !canManageIntegrations}>
                {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Cadastrar conexão Instagram
              </Button>
            </div>
          )}

          {connection && (
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">
                {connection.accountName || connection.username || 'Conta Instagram'} · pré-requisito: conta profissional
                (Business ou Creator) vinculada a uma Página do Facebook. Automação de mensagens tem um teto de ~200
                mensagens/hora por conta, imposto pela própria Meta.
              </p>

              <div className="rounded-md border border-border p-4 text-xs text-muted-foreground">
                <p className="mb-2 font-medium text-foreground">O que vai acontecer ao clicar em "Iniciar conexão OAuth":</p>
                <ol className="list-decimal space-y-1 pl-4">
                  <li>O Meu Executivo monta a URL oficial de autorização do Instagram Login com os 3 escopos necessários e um state assinado (anti-CSRF, validade curta).</li>
                  <li>Você seria redirecionado à Meta para fazer login e escolher a conta profissional.</li>
                  <li>A Meta chama de volta o callback do Meu Executivo com um código de autorização.</li>
                  <li>O callback valida o state e, quando o gate do ambiente estiver aberto, troca e cifra a credencial. Com o gate fechado, registra o estado preparado sem fingir conexão.</li>
                </ol>
              </div>

              <Button
                type="button"
                variant="outline"
                onClick={() => authorizeMutation.mutate()}
                disabled={authorizeMutation.isPending || !canManageIntegrations}
              >
                {authorizeMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Iniciar conexão OAuth
              </Button>
              {!canManageIntegrations && (
                <p className="text-xs text-muted-foreground">Sua permissão atual não inclui gestão de integrações.</p>
              )}

            </div>
          )}
        </div>
      </div>
    </KlmPanel>
  );
}

const PAGE_APP_REVIEW_CHECKLIST_STORAGE_PREFIX = 'klm_page_app_review_checklist';

function pageChecklistStorageKey(workspaceId: string | null | undefined) {
  return `${PAGE_APP_REVIEW_CHECKLIST_STORAGE_PREFIX}:${workspaceId || 'sem-workspace'}`;
}

const PAGE_APP_REVIEW_ITEMS = [
  { id: 'scope_messaging', label: 'pages_messaging aprovado em Advanced Access (App Review).' },
  { id: 'scope_metadata', label: 'pages_manage_metadata aprovado em Advanced Access (App Review).' },
  { id: 'scope_engagement', label: 'pages_read_engagement aprovado em Advanced Access (App Review).' },
  { id: 'business_verification', label: 'Verificação da empresa (Business Manager) concluída para a Página.' },
] as const;

function loadPageChecklist(workspaceId: string | null | undefined): Record<string, boolean> {
  if (typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(pageChecklistStorageKey(workspaceId));
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function savePageChecklist(workspaceId: string | null | undefined, checks: Record<string, boolean>) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(pageChecklistStorageKey(workspaceId), JSON.stringify(checks));
  } catch {
    // Sem persistência local disponível: o checklist volta a marcar tudo pendente nesta sessão.
  }
}

function PageAppReviewChecklist({ workspaceId }: { workspaceId: string | null | undefined }) {
  const [checks, setChecks] = useState<Record<string, boolean>>(() => loadPageChecklist(workspaceId));

  useEffect(() => {
    setChecks(loadPageChecklist(workspaceId));
  }, [workspaceId]);

  useEffect(() => {
    savePageChecklist(workspaceId, checks);
  }, [workspaceId, checks]);

  const allDone = PAGE_APP_REVIEW_ITEMS.every((item) => checks[item.id]);

  return (
    <KlmPanel className="p-6" tone="soft">
      <div className="flex items-start gap-3">
        <ShieldQuestion className="mt-0.5 h-5 w-5 text-primary" />
        <div className="min-w-0 flex-1 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-sm font-semibold text-foreground">Jornada B · App Review da Página do Facebook (uma vez, para todo o escritório)</h4>
            <KlmPill tone={allDone ? 'green' : 'muted'}>{allDone ? 'concluído' : 'pendente'}</KlmPill>
          </div>
          <p className="text-sm text-muted-foreground">
            Antes de qualquer OAuth de Página funcionar de verdade para os clientes, o app do Meu Executivo precisa ter Advanced
            Access aprovado pela Meta nos três escopos abaixo e a verificação da empresa concluída. Marque manualmente
            conforme cada item avança — este status é apenas informativo nesta tela; a Meta é a fonte oficial.
          </p>
          <div className="space-y-2">
            {PAGE_APP_REVIEW_ITEMS.map((item) => (
              <label key={item.id} className="flex items-start gap-2 text-sm">
                <Checkbox
                  checked={Boolean(checks[item.id])}
                  onCheckedChange={(value) => setChecks((prev) => ({ ...prev, [item.id]: Boolean(value) }))}
                />
                <span className={checks[item.id] ? 'text-foreground' : 'text-muted-foreground'}>{item.label}</span>
              </label>
            ))}
          </div>
        </div>
      </div>
    </KlmPanel>
  );
}

function PageTokenBadge({ status: tokenStatus }: { status: MessengerTokenStatus | undefined }) {
  if (!tokenStatus) return null;
  if (tokenStatus.state === 'configured') {
    return <KlmPill tone="green">Token configurado</KlmPill>;
  }
  if (tokenStatus.state === 'expiring') {
    return (
      <KlmPill tone="gold">
        Expira em {tokenStatus.expiresInDays ?? '?'} {tokenStatus.expiresInDays === 1 ? 'dia' : 'dias'}
      </KlmPill>
    );
  }
  if (tokenStatus.state === 'oauth_prepared') {
    return <KlmPill tone="gold">Preparado, aguardando aprovação de APIs externas</KlmPill>;
  }
  return <KlmPill tone="muted">Não conectado</KlmPill>;
}

function PageConnectionCard({ workspaceId, canManageIntegrations }: { workspaceId: string | null; canManageIntegrations: boolean }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [accountName, setAccountName] = useState('');
  const [pageId, setPageId] = useState('');

  const connectionsQuery = useQuery({
    queryKey: ['messenger-connections-hub', workspaceId],
    queryFn: fetchMessengerConnections,
  });

  const connection = connectionsQuery.data?.[0];

  const tokenStatusQuery = useQuery({
    queryKey: ['messenger-token-status', connection?.id],
    queryFn: () => fetchMessengerTokenStatus(connection!.id),
    enabled: Boolean(connection?.id),
  });

  const createMutation = useMutation({
    mutationFn: () => createMessengerConnection({ accountName: accountName.trim() || undefined, pageId: pageId.trim() || undefined }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messenger-connections-hub', workspaceId] });
      toast({ title: 'Conexão de Página criada', description: 'Cadastro local salvo; agora inicie a autorização OAuth abaixo.' });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível criar a conexão', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  const authorizeMutation = useMutation({
    mutationFn: () => {
      const redirectUri = `${window.location.origin}/app/conexoes/messenger/callback`;
      return fetchMessengerAuthorizeUrl(connection!.id, redirectUri);
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['messenger-token-status', connection?.id] });
      window.open(result.authorizationUrl, '_self');
    },
    onError: (error) => {
      toast({ title: 'Não foi possível preparar a autorização', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' });
    },
  });

  return (
    <KlmPanel className="p-6">
      <div className="flex items-start gap-3">
        <Facebook className="mt-0.5 h-5 w-5 text-primary" />
        <div className="min-w-0 flex-1 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-base font-medium text-foreground">Conectar Página do Facebook</h3>
            <PageTokenBadge status={tokenStatusQuery.data} />
          </div>

          {!connection && !connectionsQuery.isLoading && (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Ainda não há conexão de Página cadastrada neste workspace. Informe o nome da Página do Facebook do
                escritório para começar — o OAuth real com a Meta acontece no próximo passo.
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1">
                  <Label htmlFor="page-account-name">Nome da Página</Label>
                  <Input id="page-account-name" value={accountName} onChange={(event) => setAccountName(event.target.value)} placeholder="Página Oficial do Escritório" />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="page-id">Page ID (opcional)</Label>
                  <Input id="page-id" value={pageId} onChange={(event) => setPageId(event.target.value)} placeholder="1234567890" />
                </div>
              </div>
              <Button type="button" onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !canManageIntegrations}>
                {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Cadastrar conexão de Página
              </Button>
            </div>
          )}

          {connection && (
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">
                {connection.accountName || connection.pageId || 'Página do Facebook'} · pré-requisito: você precisa ser
                administrador da Página no Facebook Business Manager.
              </p>

              <div className="rounded-md border border-border p-4 text-xs text-muted-foreground">
                <p className="mb-2 font-medium text-foreground">O que vai acontecer ao clicar em "Iniciar conexão OAuth":</p>
                <ol className="list-decimal space-y-1 pl-4">
                  <li>O Meu Executivo monta a URL oficial de autorização do Facebook Login for Business com os 3 escopos necessários (pages_messaging, pages_manage_metadata, pages_read_engagement) e um state assinado (anti-CSRF, validade curta, mesmo esquema do Instagram).</li>
                  <li>Você seria redirecionado à Meta para fazer login e escolher a Página.</li>
                  <li>A Meta chama de volta o callback do Meu Executivo com um código de autorização.</li>
                  <li>O callback valida o state e, quando o gate do ambiente estiver aberto, troca e cifra a credencial. Com o gate fechado, registra o estado preparado sem fingir conexão.</li>
                </ol>
              </div>

              <Button
                type="button"
                variant="outline"
                onClick={() => authorizeMutation.mutate()}
                disabled={authorizeMutation.isPending || !canManageIntegrations}
              >
                {authorizeMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Iniciar conexão OAuth
              </Button>
              {!canManageIntegrations && (
                <p className="text-xs text-muted-foreground">Sua permissão atual não inclui gestão de integrações.</p>
              )}

            </div>
          )}
        </div>
      </div>
    </KlmPanel>
  );
}

export default function ConnectionsHubPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const store = useOptionalAppStore();
  const legacyStore = store as (typeof store & {
    activeWorkspace?: { id?: string | null } | null;
  }) | null;
  const workspaceId = store?.state?.activeWorkspace?.id ?? legacyStore?.activeWorkspace?.id ?? null;

  const [persisted, setPersisted] = useState<WizardPersistedState>(() => loadPersistedState(workspaceId));
  const [accessToken, setAccessToken] = useState('');
  const [registrationPin, setRegistrationPin] = useState('');
  const [appSecret, setAppSecret] = useState('');

  useEffect(() => {
    setPersisted(loadPersistedState(workspaceId));
    // Tokens nunca ficam salvos localmente por segurança; sempre reinicia em branco.
    setAccessToken('');
    setRegistrationPin('');
    setAppSecret('');
  }, [workspaceId]);

  useEffect(() => {
    savePersistedState(workspaceId, persisted);
  }, [workspaceId, persisted]);

  const connectionsQuery = useQuery({
    queryKey: ['whatsapp-connections-hub', workspaceId],
    queryFn: fetchWhatsAppConnections,
  });

  const activeConnections = useMemo(
    () => (connectionsQuery.data ?? []).filter((connection) => connection.status === 'connected'),
    [connectionsQuery.data],
  );
  const hasActiveConnection = activeConnections.length > 0 && !persisted.showWizard;
  const wizardVisible = !connectionsQuery.isLoading && (!hasActiveConnection || persisted.showWizard);
  const copilotContext = useMemo(() => ({
    whatsapp: {
      connection_count: connectionsQuery.data?.length ?? 0,
      active_connection_count: activeConnections.length,
      wizard_visible: wizardVisible,
      statuses: (connectionsQuery.data ?? []).slice(0, 20).map((connection) => ({
        status: connection.status,
        is_default: connection.isDefault,
      })),
    },
  }), [activeConnections.length, connectionsQuery.data, wizardVisible]);

  const webhookInfoQuery = useQuery({
    queryKey: ['whatsapp-webhook-info', workspaceId],
    queryFn: fetchWhatsAppWebhookInfo,
    enabled: wizardVisible,
  });

  const manualMutation = useMutation({
    mutationFn: () =>
      createManualWhatsAppConnection({
        accessToken,
        phoneNumberId: persisted.fields.phoneNumberId.trim(),
        businessAccountId: persisted.fields.businessAccountId.trim(),
        registrationPin: registrationPin.trim() || undefined,
        isDefault: true,
        appSecret: appSecret.trim() || undefined,
      }),
    onSuccess: (connection) => {
      setPersisted((prev) => ({ ...prev, errorMessage: null }));
      setAccessToken('');
      setRegistrationPin('');
      setAppSecret('');
      queryClient.invalidateQueries({ queryKey: ['whatsapp-connections-hub', workspaceId] });
      queryClient.invalidateQueries({ queryKey: ['whatsapp-connections-page'] });
      toast({
        title: 'WhatsApp conectado',
        description: `${connection.displayPhoneNumber || connection.phoneNumber || 'Número'} está com status "${connection.status}".`,
      });
    },
    onError: (error) => {
      const { humanMessage, nextStep } = describeError(error);
      setPersisted((prev) => ({ ...prev, errorMessage: `${humanMessage} ${nextStep}` }));
    },
  });

  const embeddedMutation = useMutation({
    mutationFn: completeMetaEmbeddedSignup,
    onSuccess: (connection) => {
      setPersisted((previous) => ({ ...previous, errorMessage: null, showWizard: false }));
      void queryClient.invalidateQueries({ queryKey: ['whatsapp-connections-hub', workspaceId] });
      void queryClient.invalidateQueries({ queryKey: ['whatsapp-connections-page'] });
      toast({
        title: 'WhatsApp conectado por coexistência',
        description: connection.coexistenceSyncStatus === 'requested'
          ? 'A Meta aceitou a conexão e iniciou a sincronização de contatos e histórico.'
          : 'A conexão foi salva. Consulte o diagnóstico para acompanhar a sincronização inicial.',
      });
    },
    onError: (error) => {
      const { humanMessage, nextStep } = describeError(error);
      setPersisted((previous) => ({ ...previous, errorMessage: `${humanMessage} ${nextStep}` }));
    },
  });

  function toggleCheck(id: string, value: boolean) {
    setPersisted((prev) => ({ ...prev, checks: { ...prev.checks, [id]: value } }));
  }

  function stepStatus(step: ChecklistStep): StepStatus {
    return step.checklist.every((item) => persisted.checks[item.id]) ? 'done' : 'pending';
  }

  const webhookStepDone = Boolean(persisted.checks.webhook_colado);
  const finalConnection = manualMutation.data;
  const step7Status: StepStatus = finalConnection ? 'done' : persisted.errorMessage ? 'error' : 'pending';

  function startNewConnectionWizard() {
    setPersisted((prev) => ({ ...prev, showWizard: true }));
  }

  const canManageIntegrations = store?.can ? store.can('integrations', 'manage') : true;
  const focusSection = (section: string) => {
    const target = document.querySelector<HTMLElement>(`[data-copilot-section="${section}"]`);
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  const applyCopilotActions = async (actions: KlmOperationalCopilotAction[]) => {
    for (const action of actions) {
      const actionType = typeof action.payload.type === 'string' ? action.payload.type : '';
      if (actionType === 'connections.start_whatsapp_wizard') {
        if (!canManageIntegrations) {
          throw new Error('Seu perfil não pode iniciar uma nova conexão.');
        }
        startNewConnectionWizard();
        window.requestAnimationFrame(() => focusSection('whatsapp'));
        continue;
      }
      if (actionType === 'connections.focus_section') {
        const section = typeof action.payload.section === 'string'
          && ['overview', 'whatsapp', 'instagram', 'facebook'].includes(action.payload.section)
          ? action.payload.section
          : 'overview';
        focusSection(section);
        continue;
      }
      if (actionType === 'connections.refresh') {
        await Promise.all([
          connectionsQuery.refetch(),
          queryClient.invalidateQueries({ queryKey: ['instagram-connections-hub', workspaceId] }),
          queryClient.invalidateQueries({ queryKey: ['instagram-token-status'] }),
          queryClient.invalidateQueries({ queryKey: ['messenger-connections-hub', workspaceId] }),
          queryClient.invalidateQueries({ queryKey: ['messenger-token-status'] }),
        ]);
      }
    }
    return 'A central foi atualizada. Nenhuma credencial foi preenchida ou conexão externa criada.';
  };

  return (
    <div className="space-y-6">
      <span data-copilot-section="overview" className="block scroll-mt-24" aria-hidden="true" />
      <KlmSectionTitle
        title="Central de Conexão"
        description="Conecte o WhatsApp oficial do escritório em modo manual (BYOA): cada passo tem instrução completa, sem depender de aprovação prévia da Meta para o Meu Executivo."
      />
      <div className="flex justify-end">
        <Button asChild variant="outline" size="sm">
          <Link to="/app/conexoes/whatsapp/webhooks"><Webhook className="mr-2 h-4 w-4" />Ver os 33 webhooks</Link>
        </Button>
      </div>

      <span data-copilot-section="site-leads" className="block scroll-mt-24" aria-hidden="true" />
      <SiteLeadsGuideCard workspaceId={workspaceId} canManageIntegrations={canManageIntegrations} />

      <span data-copilot-section="whatsapp" className="block scroll-mt-24" aria-hidden="true" />
      {connectionsQuery.isLoading && (
        <KlmPanel className="p-6 text-sm text-muted-foreground">
          <Loader2 className="mr-2 inline h-4 w-4 animate-spin" /> Verificando conexões existentes do workspace…
        </KlmPanel>
      )}

      {connectionsQuery.isError && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Não foi possível consultar as conexões</AlertTitle>
          <AlertDescription>
            {connectionsQuery.error instanceof Error ? connectionsQuery.error.message : 'Tente novamente em instantes.'}
          </AlertDescription>
        </Alert>
      )}

      {hasActiveConnection && (
        <KlmPanel className="p-6">
          <div className="flex flex-col gap-4">
            <div className="flex items-center gap-2">
              <PlugZap className="h-5 w-5 text-emerald-500" />
              <h3 className="text-base font-medium text-foreground">WhatsApp já conectado neste workspace</h3>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {activeConnections.map((connection) => (
                <div key={connection.id} className="rounded-md border border-border p-4">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium text-foreground">
                      {connection.displayName || connection.displayPhoneNumber || connection.phoneNumber || 'Número oficial'}
                    </span>
                    <KlmPill tone="green">{connection.status}</KlmPill>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {connection.displayPhoneNumber || connection.phoneNumber || 'Sem telefone visível'} · WABA {connection.wabaId || 'não informado'}
                  </p>
                  {connection.isDefault && <p className="mt-1 text-xs text-emerald-600">Linha padrão do workspace</p>}
                  {connection.connectionType === 'TECH_PROVIDER' && connection.tokenLifecycleState === 'unknown' && (
                    <p className="mt-2 text-xs text-amber-700">
                      O vencimento desta autorização antiga não foi informado pela Meta. Reconecte pelo Embedded Signup para registrar a validade sem trocar o número.
                    </p>
                  )}
                  {connection.tokenLifecycleState === 'expiring' && (
                    <p className="mt-2 text-xs font-medium text-amber-700">
                      A autorização Meta vence em {connection.tokenExpiresInDays ?? 0} dia(s). Reconecte antes do vencimento.
                    </p>
                  )}
                  {connection.tokenLifecycleState === 'expired' && (
                    <p className="mt-2 text-xs font-medium text-destructive">
                      A autorização Meta expirou. Reautentique pelo Embedded Signup para retomar a conexão.
                    </p>
                  )}
                  {connection.connectionType === 'TECH_PROVIDER' && ['unknown', 'expiring', 'expired'].includes(connection.tokenLifecycleState ?? '') && (
                    <PermissionGate module="integrations" action="manage" actionLabel="reautenticar a Meta">
                      <Button type="button" variant="link" className="mt-1 h-auto px-0 text-xs" onClick={startNewConnectionWizard}>
                        Reautenticar com a Meta
                      </Button>
                    </PermissionGate>
                  )}
                  <WhatsAppConnectionHealthCard connectionId={connection.id} />
                </div>
              ))}
            </div>
            <div>
              <PermissionGate module="integrations" action="manage" actionLabel="conectar outro número">
                <Button type="button" variant="outline" onClick={startNewConnectionWizard}>
                  Conectar outro número
                </Button>
              </PermissionGate>
            </div>
          </div>
        </KlmPanel>
      )}

      <span data-copilot-section="instagram" className="block scroll-mt-24" aria-hidden="true" />
      <InstagramConnectionCard workspaceId={workspaceId} canManageIntegrations={canManageIntegrations} />
      <InstagramAppReviewChecklist workspaceId={workspaceId} />

      <span data-copilot-section="facebook" className="block scroll-mt-24" aria-hidden="true" />
      <PageConnectionCard workspaceId={workspaceId} canManageIntegrations={canManageIntegrations} />
      <PageAppReviewChecklist workspaceId={workspaceId} />

      {wizardVisible && (
        <>
          <KlmPanel className="p-6">
            <h3 className="mb-4 text-base font-medium text-foreground">Conectar WhatsApp (modo manual)</h3>
            <ol className="space-y-6">
              {CHECKLIST_STEPS.map((step) => {
                const status = stepStatus(step);
                return (
                  <li key={step.id} className="rounded-md border border-border p-4">
                    <div className="flex items-start gap-3">
                      <StepStatusIcon status={status} />
                      <div className="min-w-0 flex-1 space-y-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <h4 className="text-sm font-semibold text-foreground">{step.title}</h4>
                          {step.docsUrl && (
                            <a
                              href={step.docsUrl}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                            >
                              Abrir no developers.facebook.com <ExternalLink className="h-3 w-3" />
                            </a>
                          )}
                        </div>
                        {step.body.map((paragraph, index) => (
                          <p key={index} className="text-sm text-muted-foreground">{paragraph}</p>
                        ))}
                        <div className="space-y-2 pt-1">
                          {step.checklist.map((item) => (
                            <label key={item.id} className="flex items-start gap-2 text-sm">
                              <Checkbox
                                checked={Boolean(persisted.checks[item.id])}
                                onCheckedChange={(value) => toggleCheck(item.id, Boolean(value))}
                              />
                              <span className={persisted.checks[item.id] ? 'text-foreground' : 'text-muted-foreground'}>{item.label}</span>
                            </label>
                          ))}
                        </div>
                      </div>
                    </div>
                  </li>
                );
              })}

              <li className="rounded-md border border-border p-4">
                <div className="flex items-start gap-3">
                  <StepStatusIcon status={webhookStepDone ? 'done' : 'pending'} />
                  <div className="min-w-0 flex-1 space-y-3">
                    <h4 className="text-sm font-semibold text-foreground">Passo 6 · Configurar o webhook</h4>
                    <p className="text-sm text-muted-foreground">
                      No app do developers.facebook.com, produto WhatsApp → "Configuração" → seção "Webhook", cole a URL de callback e o token de verificação abaixo, depois assine o campo <code className="rounded bg-muted px-1">messages</code>.
                    </p>
                    {webhookInfoQuery.isLoading && (
                      <p className="text-xs text-muted-foreground"><Loader2 className="mr-1 inline h-3 w-3 animate-spin" /> Carregando URL e token do webhook…</p>
                    )}
                    {webhookInfoQuery.isError && (
                      <Alert variant="destructive">
                        <AlertTriangle className="h-4 w-4" />
                        <AlertTitle>Não foi possível carregar os dados do webhook</AlertTitle>
                        <AlertDescription>
                          {webhookInfoQuery.error instanceof Error ? webhookInfoQuery.error.message : 'Tente novamente.'}
                        </AlertDescription>
                      </Alert>
                    )}
                    {webhookInfoQuery.data && (
                      <div className="space-y-3">
                        <CopyableField label="Callback URL" value={webhookInfoQuery.data.webhookUrl} />
                        {webhookInfoQuery.data.verifyToken ? (
                          <CopyableField label="Verify Token" value={webhookInfoQuery.data.verifyToken} />
                        ) : (
                          <Alert>
                            <ShieldQuestion className="h-4 w-4" />
                            <AlertTitle>Verify Token ainda não configurado neste ambiente</AlertTitle>
                            <AlertDescription>
                              Peça ao time de operações para definir o token de verificação do WhatsApp antes de cadastrar o webhook na Meta.
                            </AlertDescription>
                          </Alert>
                        )}
                        <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                          {webhookInfoQuery.data.instructions.map((instruction, index) => (
                            <li key={index}>{instruction}</li>
                          ))}
                        </ul>
                        {(webhookInfoQuery.data.recommendedFields ?? []).length > 0 && (
                          <div className="space-y-2 rounded-lg border border-border p-3">
                            <p className="text-xs font-semibold text-foreground">
                              Campos recomendados para a operação completa
                            </p>
                            <div className="flex flex-wrap gap-1.5">
                              {(webhookInfoQuery.data.recommendedFields ?? []).map((field) => (
                                <code key={field} className="rounded bg-muted px-2 py-1 text-[11px] text-foreground">
                                  {field}
                                </code>
                              ))}
                            </div>
                            <p className="text-[11px] text-muted-foreground">
                              A lista mostra o que o Meu Executivo sabe processar. A assinatura efetiva é confirmada pelos seletores do painel da Meta.
                            </p>
                          </div>
                        )}
                        <Alert>
                          <ShieldQuestion className="h-4 w-4" />
                          <AlertTitle>Onde achar o App Secret do seu app</AlertTitle>
                          <AlertDescription>
                            No app do developers.facebook.com, vá em "Configurações" → "Básico". O App Secret fica escondido atrás de um botão "Mostrar" (a Meta pode pedir sua senha). Copie o valor e cole no campo "App Secret" do Passo 7, logo abaixo — cada escritório usa o App Secret do próprio app, e o Meu Executivo guarda esse valor cifrado, nunca em texto puro.
                          </AlertDescription>
                        </Alert>
                        {!webhookInfoQuery.data.appSecretConfigured && (
                          <p className="text-xs text-muted-foreground">
                            Este ambiente não tem um App Secret global de fallback configurado — sem o App Secret desta conexão preenchido no Passo 7, o webhook deste número não vai validar a assinatura da Meta.
                          </p>
                        )}
                      </div>
                    )}
                    <label className="flex items-start gap-2 text-sm">
                      <Checkbox
                        checked={webhookStepDone}
                        onCheckedChange={(value) => toggleCheck('webhook_colado', Boolean(value))}
                      />
                      <span className={webhookStepDone ? 'text-foreground' : 'text-muted-foreground'}>
                        Colei a URL e o Verify Token no app e cliquei em "Verificar e salvar" com sucesso.
                      </span>
                    </label>
                  </div>
                </div>
              </li>

              <li className="rounded-md border border-border p-4">
                <div className="flex items-start gap-3">
                  <StepStatusIcon status={step7Status} />
                  <div className="min-w-0 flex-1 space-y-3">
                    <h4 className="text-sm font-semibold text-foreground">Passo 7 · Colar as credenciais no Meu Executivo</h4>
                    <p className="text-sm text-muted-foreground">
                      Cole os três valores gerados nos passos anteriores. O Meu Executivo valida direto com a Meta: se der certo, o número aparece conectado logo abaixo.
                    </p>
                    <form
                      className="grid gap-3 sm:grid-cols-2"
                      onSubmit={(event) => {
                        event.preventDefault();
                        manualMutation.mutate();
                      }}
                    >
                      <div className="space-y-1 sm:col-span-2">
                        <Label htmlFor="access-token">Token de acesso permanente (System User)</Label>
                        <Input
                          id="access-token"
                          type="password"
                          autoComplete="off"
                          value={accessToken}
                          onChange={(event) => setAccessToken(event.target.value)}
                          placeholder="EAAG..."
                          required
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="phone-number-id">Phone Number ID</Label>
                        <Input
                          id="phone-number-id"
                          value={persisted.fields.phoneNumberId}
                          onChange={(event) =>
                            setPersisted((prev) => ({ ...prev, fields: { ...prev.fields, phoneNumberId: event.target.value } }))
                          }
                          placeholder="1234567890123456"
                          required
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="waba-id">WhatsApp Business Account ID (WABA)</Label>
                        <Input
                          id="waba-id"
                          value={persisted.fields.businessAccountId}
                          onChange={(event) =>
                            setPersisted((prev) => ({ ...prev, fields: { ...prev.fields, businessAccountId: event.target.value } }))
                          }
                          placeholder="9876543210987654"
                          required
                        />
                      </div>
                      <div className="space-y-1 sm:col-span-2">
                        <Label htmlFor="registration-pin">PIN de verificação em duas etapas (se o número já tiver um configurado)</Label>
                        <Input
                          id="registration-pin"
                          inputMode="numeric"
                          maxLength={6}
                          value={registrationPin}
                          onChange={(event) => setRegistrationPin(event.target.value)}
                          placeholder="Opcional · 6 dígitos"
                        />
                      </div>
                      <div className="space-y-1 sm:col-span-2">
                        <Label htmlFor="app-secret">App Secret do seu app Meta</Label>
                        <Input
                          id="app-secret"
                          type="password"
                          autoComplete="off"
                          value={appSecret}
                          onChange={(event) => setAppSecret(event.target.value)}
                          placeholder={
                            finalConnection?.appSecretConfigured
                              ? 'Já configurado · deixe em branco para manter'
                              : 'Copiado de Configurações → Básico no app'
                          }
                        />
                        <p className="text-xs text-muted-foreground">
                          Valida a assinatura do webhook desta conexão. Fica cifrado no Meu Executivo e nunca é mostrado de novo depois de salvo; deixe em branco para manter o valor já salvo.
                        </p>
                      </div>
                      {persisted.errorMessage && (
                        <div className="sm:col-span-2">
                          <Alert variant="destructive">
                            <AlertTriangle className="h-4 w-4" />
                            <AlertTitle>Não deu certo desta vez</AlertTitle>
                            <AlertDescription>{persisted.errorMessage}</AlertDescription>
                          </Alert>
                        </div>
                      )}
                      {finalConnection && (
                        <div className="sm:col-span-2">
                          <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 p-4">
                            <div className="flex items-center justify-between gap-2">
                              <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                                <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                                {finalConnection.displayPhoneNumber || finalConnection.phoneNumber || 'Número oficial'} conectado
                              </span>
                              <KlmPill tone="green">{finalConnection.status}</KlmPill>
                            </div>
                            <p className="mt-1 text-xs text-muted-foreground">
                              {finalConnection.webhookRegistered ? 'Webhook já registrado.' : 'Webhook ainda pendente de confirmação — reveja o Passo 6.'}
                              {' '}
                              {finalConnection.appSecretConfigured
                                ? 'App Secret desta conexão está salvo.'
                                : 'Sem App Secret próprio salvo — o webhook depende do App Secret global do ambiente.'}
                            </p>
                          </div>
                        </div>
                      )}
                      <div className="sm:col-span-2">
                        <Button type="submit" disabled={manualMutation.isPending || !canManageIntegrations}>
                          {manualMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                          Conectar WhatsApp
                        </Button>
                        {!canManageIntegrations && (
                          <p className="mt-1 text-xs text-muted-foreground">Sua permissão atual não inclui gestão de integrações.</p>
                        )}
                      </div>
                    </form>
                  </div>
                </div>
              </li>
            </ol>
          </KlmPanel>

          <KlmPanel className="p-6" tone="soft">
            <div className="flex items-start gap-3">
              <Sparkles className="mt-0.5 h-5 w-5 text-primary" />
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <h4 className="text-sm font-semibold text-foreground">Conectar o WhatsApp Business existente</h4>
                  <Badge variant="outline">Coexistência</Badge>
                </div>
                <p className="text-sm text-muted-foreground">
                  Abra o popup oficial da Meta, escolha a WABA e o número que já funciona no WhatsApp Business App. O Meu Executivo mantém o modo manual disponível e registra separadamente o estado da sincronização inicial.
                </p>
                <p className="text-xs text-muted-foreground">
                  <Clock className="mr-1 inline h-3 w-3" />
                  A disponibilidade depende da configuração e das permissões concedidas pela Meta neste ambiente. O botão fica bloqueado quando a configuração pública estiver incompleta.
                </p>
                <MetaEmbeddedSignupButton
                  disabled={!canManageIntegrations || embeddedMutation.isPending}
                  onComplete={async (result) => {
                    await embeddedMutation.mutateAsync(result);
                  }}
                />
              </div>
            </div>
          </KlmPanel>

          <KlmPanel className="p-6">
            <h4 className="mb-3 text-sm font-semibold text-foreground">Problemas comuns nesta etapa</h4>
            <Accordion type="single" collapsible className="w-full">
              <AccordionItem value="display-name">
                <AccordionTrigger>Display name recusado pela Meta</AccordionTrigger>
                <AccordionContent>
                  A Meta rejeita nomes genéricos, com emojis, símbolos, URLs ou termos que soem promocionais (ex.: "PROMOÇÃO", "OFERTA"). Use o nome real do escritório ou marca, exatamente como aparece no seu site e nas redes sociais, e reenvie o pedido de aprovação do número na tela do produto WhatsApp.
                </AccordionContent>
              </AccordionItem>
              <AccordionItem value="numero-preso">
                <AccordionTrigger>Número preso em outra WABA</AccordionTrigger>
                <AccordionContent>
                  Se o número já foi cadastrado em outra WhatsApp Business Account (de outro app, de uma agência, ou de um teste anterior), a Meta não deixa vinculá-lo de novo até ele ser removido de lá. Peça ao administrador da WABA anterior para excluir o número em business.facebook.com → WhatsApp Manager → Números de telefone, ou abra um chamado no Business Help Center pedindo a liberação.
                </AccordionContent>
              </AccordionItem>
              <AccordionItem value="pin-133005">
                <AccordionTrigger>Erro 133005 — PIN de verificação em duas etapas</AccordionTrigger>
                <AccordionContent>
                  Esse código aparece quando o número já tem a verificação em duas etapas ativada e a Meta pede o PIN de 6 dígitos configurado anteriormente. Se você não lembra o PIN, é possível resetá-lo pela API (endpoint de reset de two-step) ou, sem acesso à API, redefinir manualmente no app WhatsApp Business instalado no celular, em Configurações → Conta → Confirmação em duas etapas. Depois de obter/redefinir o PIN, cole-o no campo "PIN de verificação" do Passo 7 aqui no Meu Executivo.
                </AccordionContent>
              </AccordionItem>
              <AccordionItem value="verificacao-pendente">
                <AccordionTrigger>Business Verification pendente ou recusada</AccordionTrigger>
                <AccordionContent>
                  Enquanto a verificação estiver "Em análise", o número funciona só com contatos de teste cadastrados manualmente — não é erro do Meu Executivo, é limite da própria Meta. Se ela vier como "Recusada", confira em business.facebook.com/settings/security-center o motivo exato (documento ilegível, CNPJ divergente do nome do Business, endereço não confere) e reenvie os documentos corrigidos; o prazo de nova análise costuma repetir o ciclo de 1 a 5 dias úteis.
                </AccordionContent>
              </AccordionItem>
            </Accordion>
          </KlmPanel>
        </>
      )}

      <OperationalCopilot
        domain="connections"
        title="Copiloto de Conexões"
        subtitle="Configuração segura por canal"
        welcome="Diga qual canal quer configurar ou diagnosticar. Eu levo ao passo certo e atualizo o estado, sem receber nem armazenar credenciais."
        placeholder="Ex.: abra o assistente para conectar um novo WhatsApp."
        contextKey="connections-hub"
        contextSnapshot={copilotContext}
        workspaceId={workspaceId ?? undefined}
        userId={store?.state?.currentUser?.id}
        canApply={store?.can('integrations', 'view') ?? true}
        onApply={applyCopilotActions}
      />
    </div>
  );
}
