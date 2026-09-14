/** Identidade visual do produto Meu Executivo. */
export const BRAND = {
  name: "Meu Executivo",
  tagline: "Sistema operacional com IA",
  workspaceTagline: "Operação jurídica inteligente",
  full: "Meu Executivo",
  company: "Meu Executivo",
  domain: "meuexecutivo.com.br",
  url: "https://meuexecutivo.com.br",
  description: "Sistema operacional com IA para escritórios.",
  email: "contato@klmadvogados.com",
  supportEmail: "contato@klmadvogados.com",
  agents: {
    sdr: "SDR IA",
    analyst: "Jurídico IA",
    drafter: "Petição IA",
    ops: "Meu Executivo",
  },
  ai: {
    label: "Meu Executivo IA",
    promise: "Atendimento, CRM e automações com supervisão humana",
  },
  colors: {
    black: "#020C22",
    navy: "#031121",
    gold: "#0066F7",
    goldText: "#0057D8",
    paper: "#F6F9FC",
    ai: "#0066F7",
  },
  version: "1.0.0",
} as const;

export const BRAND_INITIALS = "ME";

export function runtimeEnvironmentStatus(hostname = typeof window === "undefined" ? "" : window.location.hostname) {
  if (hostname === "localhost" || hostname === "127.0.0.1" || hostname.endsWith(".local")) {
    return "Simulação local ativa";
  }
  if (hostname.startsWith("staging-") || hostname.includes(".staging.")) {
    return "Ambiente de homologação";
  }
  return "Ambiente de produção";
}
