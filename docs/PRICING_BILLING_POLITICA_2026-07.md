# KLM Hub — Pricing, Billing e Política de Custo de IA

Data: 22/07/2026. Status: **REGISTRO DE DECISÕES APROVADAS PELO PROPRIETÁRIO, sem execução nesta fase.**

Subordinado ao [`KLM_HUB_META_E_PLANO_SAAS_LOCAL_V5_2026-07-19.md`](./KLM_HUB_META_E_PLANO_SAAS_LOCAL_V5_2026-07-19.md): gates externos (APIs reais, credenciais, AWS) permanecem bloqueados até aprovação separada. Este documento é o insumo do esforço de billing/entitlements previsto no V5.

## 1. Decisões do proprietário (22/07/2026)

1. **Alvo de produção: AWS** (resolve a decisão de alvo único; a via VPS Hostinger + systemd será aposentada quando o gate AWS abrir).
2. **Lançamento 100% via API de modelos, sem self-host de GPU.** Modelos abertos (Gemma 4, Qwen3.6) entram como rota de catálogo via API serverless, não como infra própria.
3. **Self-host é gatilho de escala, não aposta inicial**: ligar quando o gasto mensal do tier SIMPLE via API ultrapassar R$ 3.000 por 2 meses seguidos, OU quando houver demanda paga pelo add-on de privacidade (inferência dedicada).
4. Ancoragem competitiva: **CRMs jurídicos** (ADVBOX, EasyJur), não softwares de gestão processual.

## 2. Planos e preços (proposta aprovada em direção; validar WTP antes de fixar)

Value metric: escritório (workspace) + cota de IA + usuários. Modelo híbrido: assinatura com cota embutida + excedente em pacotes.

| | Essencial | Escritório | Performance |
|---|---|---|---|
| Preço/mês | **R$ 197** | **R$ 497** | **R$ 997** |
| Usuários | 2 | 5 | 15 |
| Números WhatsApp | 1 | 1 | multi |
| Conversas IA/mês | 200 | 1.000 | 3.000 |
| Petições IA/mês | 10 | 40 | 120 |
| Agentes (SDR, insights, follow-up) | triagem básica | completos | completos + playbook próprio |
| API pública / relatórios avançados | não | relatórios | ambos |

Complementos: usuário extra R$ 39/mês; pacote 25 petições R$ 99; mensagens de marketing WhatsApp em repasse com markup 20%; anual com 2 meses grátis. Trial 14 dias sem cartão. **Sem freemium e sem "IA ilimitada" em nenhuma hipótese.**

Posicionamento vs âncora: Essencial bate a porta de entrada da ADVBOX (R$ 200); Escritório fica 25-30% acima de ADVBOX Banca CRM (R$ 380) e EasyJur Premium (R$ 389) justificado por omnichannel WhatsApp oficial + agentes + petições nativos; Performance fica 38% abaixo do teto ADVBOX Elite (R$ 1.600), com espaço deliberado para reajuste futuro.

Pendência antes de fixar preço público: van Westendorp rápido com 10-20 escritórios da rede do proprietário.

## 3. Estrutura de custo por tenant (medida/pesquisada em 07/2026)

Composição típica: **~70% API de IA, ~20% infra rateada, ~10% WhatsApp.**

### 3.1 IA (custo variável dominante; pressupõe Sprint 3 de custo executado)

| Operação | Rota | Custo unitário |
|---|---|---|
| Conversa SDR (~6 turnos, janela de histórico) | SIMPLE (haiku/flash-lite-class ou aberto via API) | R$ 0,05-0,30 |
| Transcrição de áudio | SIMPLE (flash-class) | R$ 0,01-0,05 |
| Insight de conversa (max_tokens ~500) | SIMPLE | ~R$ 0,05 |
| Petição (15k contexto + 8k saída, 2 passes) | PREMIUM (sonnet/opus-class) | R$ 2-3 |

Custo de IA por plano na cota 100%: Essencial ~R$ 90, Escritório ~R$ 220, Performance ~R$ 560. Margens: pior caso 34-45%; uso típico (60% da cota) 55-62%.

Condições sem as quais esta tabela NÃO vale (itens do plano de custo de IA): catálogo de roteamento versionado, janela de histórico no SDR, transcrição dentro do gateway, telemetria universal por workspace, max_tokens explícito, prompt caching.

### 3.2 WhatsApp Cloud API (cobrança por mensagem desde 01/2026)

Atendimento iniciado pelo cliente: grátis. Utility R$ 0,04-0,05. Marketing R$ 0,31-0,38 (SEMPRE repasse com markup, nunca embutido em plano). Sem mensalidade de integrador (Cloud API direta é argumento de venda: mercado cobra R$ 200-1.200/mês).

### 3.3 Infra AWS (fixo da plataforma; footprint medido: núcleo KLM ~1,7 GB RAM)

| Fase | Escritórios | Arquitetura | Custo/mês |
|---|---|---|---|
| Lançamento | 1-20 | EC2 t4g.medium (compose) + RDS db.t4g.micro + S3/CloudFront | ~US$ 61 ≈ R$ 335 (us-east-1) |
| Lançamento (alternativa gerenciada) | 1-20 | Fargate 2 tasks 0.5vCPU/1GB + RDS t4g.micro + ElastiCache t4g.micro + ALB | ~US$ 100 ≈ R$ 580 |
| Crescimento | 20-100 | Fargate tasks 1vCPU/2GB + db.t4g.small | ~R$ 950 |
| Escala | 100+ | 2-3 tasks + db classe m/r + réplica | ~R$ 1.700-2.200 |

Regras: região preferida sa-east-1 (+30-40% de custo, latência local, coerente com meta p95 < 300 ms; CloudFront para estático). **Evitar NAT Gateway** (subnet pública com IP público ou VPC endpoints). Graviton/ARM corta ~20% (exige build multi-arch). Aplicar a créditos AWS Activate antes do go-live. Recomendação: começar EC2+RDS em sa-east-1 (~R$ 450-500), migrar para Fargate acima de ~30 escritórios com o buildspec/ECS já existente.

Infra por tenant na fase 1: R$ 17-40/mês. Break-even absoluto: 2 clientes.

## 4. Política de roteamento de modelos (catálogo)

1. **SIMPLE** (sdr, crm, secretaria, qualificação, insights, transcrição): mini/lite/haiku-class via API. Candidata paralela: **Gemma 4 26B A4B ou Qwen3.6 via API serverless** (Bedrock/DeepInfra/Together, ~US$ 0,05-0,30/MTok), condicionada a eval de qualidade em português jurídico. Apache 2.0, sem restrição comercial.
2. **MEDIUM** (copiloto, closer, paralegal): flash/sonnet-class, max_tokens curto.
3. **COMPLEX** (juridico, multimodal): sonnet/gemini-pro-class, só com gatilho de triagem.
4. **PREMIUM** (peticao, klm_drafter): modelo top exclusivamente aqui, com streaming e cap por chamada.

**Self-host (Gemma 4 / Qwen3.6 em g6.xlarge ~US$ 587/mês)**: NÃO no lançamento. Gatilhos objetivos para reavaliar: (a) gasto do tier SIMPLE via API > R$ 3.000/mês por 2 meses seguidos (break-even ~60-100 escritórios), ou (b) demanda paga por add-on de privacidade "inferência dedicada" (R$ 400-600/mês por tenant; paga a GPU com 6-8 assinantes). Plugagem via `OllamaProvider`/rota local já existente no gateway, sem retrabalho.

## 5. Enforcement (entitlements = budget do gateway)

- Cota do plano vira budget por workspace no gateway (mecanismo já construído).
- Estouro de cota: degrada para rota mais barata + oferta de upgrade/pacote, nunca corta atendimento no meio nem vira custo aberto da plataforma.
- Alerta em 80% da cota (workspace e plataforma).
- Telemetria de custo por workspace é pré-requisito de billing: sem ela não há fatura confiável.
- O resumo exibido ao dono da conta resolve a mesma versão publicada pelo Super Admin que o runtime aplica ao enforcement. Rollouts novos, agendados ou por regra não podem deixar a UI com limites legados.

## 6. Riscos registrados

1. Preços são hipótese ancorada em comparáveis; sem pesquisa WTP, risco de deixar dinheiro na mesa no Performance (teto de mercado R$ 1.600).
2. Se o Sprint de custo de IA não for executado antes de ligar APIs, o custo por conversa multiplica por 5-10 e o Essencial opera no prejuízo.
3. Campanhas de marketing WhatsApp embutidas em plano destroem margem (10 mil msgs = R$ 3.500); manter repasse sempre.
4. Câmbio: tabelas em R$ usam referência R$ 5,50/US$; revisar trimestralmente.

## Fontes (consultadas em 07/2026)

WhatsApp por mensagem: nicechat.com.br, rmchat.com.br. CRM jurídico: advbox.com.br/planos, easyjur.com/planos. AWS: aws.amazon.com/fargate/pricing, economize.cloud (RDS t4g, ElastiCache t4g, g5/g6), cloudzero.com (ALB). Modelos: labellerr.com (Gemma 4), layer3labs.io (Gemma 4 vs Qwen 3.6), ai.google.dev/gemini-api/docs/pricing, pricepertoken.com (OpenAI). Preços Anthropic: tabela oficial (claude-api).
