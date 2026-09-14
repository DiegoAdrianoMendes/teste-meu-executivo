import type { Brand } from '@/types/frontend-contracts';

export type ExecutiveReportMetric = { label: string; value: string; meta?: string };
export type ExecutiveReportFunnelStage = { label: string; value: number; progress: number };
export type ExecutiveReportChannel = { label: string; leads: number; cac: string; roas: string };
export type ExecutiveReportRevenue = { paid: string; overdue: string; scheduled: string; receivables: string };

export type ExecutiveReportInput = {
  brand: Brand | null;
  periodLabel: string;
  generatedAt: Date;
  metrics: ExecutiveReportMetric[];
  funnel: ExecutiveReportFunnelStage[];
  channels: ExecutiveReportChannel[];
  revenue: ExecutiveReportRevenue;
  revenueTrend: number[];
};

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

/** Gráfico de tendência de receita em SVG estático (sem libs), a partir da série
 * já presente no payload de KPIs (kpiQuery.data.trend.revenue). */
function revenueTrendSvg(points: number[], accentColor: string): string {
  if (points.length < 2) return '';
  const width = 560;
  const height = 96;
  const max = Math.max(...points, 1);
  const min = Math.min(...points, 0);
  const range = max - min || 1;
  const step = width / (points.length - 1);
  const coords = points.map((value, index) => {
    const x = index * step;
    const y = height - ((value - min) / range) * (height - 8) - 4;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const linePath = `M${coords.join(' L')}`;
  const areaPath = `${linePath} L${width},${height} L0,${height} Z`;
  return `
    <svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" class="report-trend-svg" role="img" aria-label="Tendência de receita no período">
      <path d="${areaPath}" fill="${accentColor}" fill-opacity="0.12" stroke="none" />
      <path d="${linePath}" fill="none" stroke="${accentColor}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
    </svg>`;
}

function fallbackBrand() {
  return {
    name: 'Meu Executivo',
    tagline: 'Operação jurídica',
    primary: '#0057d8',
    foreground: '#031121',
    logoUrl: null as string | null,
  };
}

/** Monta o HTML timbrado do relatório executivo, com a marca do workspace ativo
 * (workspace-brand-provider), pronto para impressão/"Salvar como PDF" via window.print(). */
export function buildExecutiveReportHtml(input: ExecutiveReportInput): string {
  const fallback = fallbackBrand();
  const brandName = input.brand?.name?.trim() || fallback.name;
  const tagline = input.brand?.tagline?.trim() || fallback.tagline;
  const primary = input.brand?.colors?.primary || fallback.primary;
  const foreground = input.brand?.colors?.foreground || fallback.foreground;
  const logoUrl = input.brand?.assets?.logoLightUrl || fallback.logoUrl;
  const generatedAtLabel = input.generatedAt.toLocaleString('pt-BR', {
    dateStyle: 'long',
    timeStyle: 'short',
  });

  const metricsHtml = input.metrics.map((metric) => `
    <div class="report-metric">
      <span class="report-metric-label">${escapeHtml(metric.label)}</span>
      <span class="report-metric-value">${escapeHtml(metric.value)}</span>
      ${metric.meta ? `<span class="report-metric-meta">${escapeHtml(metric.meta)}</span>` : ''}
    </div>`).join('');

  const funnelHtml = input.funnel.map((stage) => `
    <div class="report-funnel-row">
      <div class="report-funnel-row-head">
        <span>${escapeHtml(stage.label)}</span>
        <span>${stage.value}</span>
      </div>
      <div class="report-bar-track"><div class="report-bar-fill" style="width:${Math.max(0, Math.min(100, stage.progress))}%"></div></div>
    </div>`).join('') || '<p class="report-empty">Nenhum caso no funil neste período.</p>';

  const channelsHtml = input.channels.length
    ? `<table class="report-table">
        <thead><tr><th>Canal</th><th>Leads</th><th>CAC</th><th>ROAS</th></tr></thead>
        <tbody>${input.channels.map((channel) => `
          <tr>
            <td>${escapeHtml(channel.label)}</td>
            <td>${channel.leads}</td>
            <td>${escapeHtml(channel.cac)}</td>
            <td>${escapeHtml(channel.roas)}</td>
          </tr>`).join('')}</tbody>
      </table>`
    : '<p class="report-empty">Nenhum canal com eventos neste período.</p>';

  const trendSvg = revenueTrendSvg(input.revenueTrend, primary);

  return `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8" />
<title>Relatório executivo · ${escapeHtml(brandName)}</title>
<style>
  :root { --report-primary: ${primary}; --report-foreground: ${foreground}; }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 28px 36px 40px;
    font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
    color: var(--report-foreground);
    background: #ffffff;
  }
  .report-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    border-bottom: 3px solid var(--report-primary);
    padding-bottom: 16px;
    margin-bottom: 24px;
  }
  .report-header-brand { display: flex; align-items: center; gap: 12px; }
  .report-header-brand img { height: 40px; width: auto; object-fit: contain; }
  .report-header-brand h1 { margin: 0; font-size: 18px; font-weight: 700; }
  .report-header-brand p { margin: 2px 0 0; font-size: 11px; color: #52606d; }
  .report-header-period { text-align: right; font-size: 11px; color: #52606d; }
  .report-header-period strong { display: block; font-size: 13px; color: var(--report-foreground); }
  h2.report-section-title {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--report-primary);
    margin: 28px 0 12px;
  }
  .report-metrics-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
  .report-metric {
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .report-metric-label { font-size: 10px; color: #52606d; font-weight: 600; }
  .report-metric-value { font-size: 19px; font-weight: 700; }
  .report-metric-meta { font-size: 9px; color: #7a8794; }
  .report-funnel-row { margin-bottom: 10px; }
  .report-funnel-row-head { display: flex; justify-content: space-between; font-size: 11px; font-weight: 600; margin-bottom: 4px; }
  .report-bar-track { height: 6px; border-radius: 999px; background: #e2e8f0; overflow: hidden; }
  .report-bar-fill { height: 100%; background: var(--report-primary); border-radius: 999px; }
  .report-table { width: 100%; border-collapse: collapse; font-size: 11px; }
  .report-table th, .report-table td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #e2e8f0; }
  .report-table th { color: #52606d; font-weight: 600; text-transform: uppercase; font-size: 9px; }
  .report-empty { font-size: 11px; color: #7a8794; }
  .report-revenue-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 12px; }
  .report-revenue-cell { border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px; }
  .report-revenue-cell span { display: block; }
  .report-revenue-cell .label { font-size: 10px; color: #52606d; font-weight: 600; }
  .report-revenue-cell .value { font-size: 15px; font-weight: 700; margin-top: 3px; }
  .report-trend-svg { display: block; margin-top: 8px; }
  .report-footer {
    margin-top: 32px;
    padding-top: 12px;
    border-top: 1px solid #e2e8f0;
    font-size: 10px;
    color: #7a8794;
    display: flex;
    justify-content: space-between;
  }
  @media print {
    @page { size: A4; margin: 14mm; }
    body { padding: 0; }
    .report-metrics-grid { grid-template-columns: repeat(5, 1fr); }
  }
</style>
</head>
<body>
  <header class="report-header">
    <div class="report-header-brand">
      ${logoUrl ? `<img src="${escapeHtml(logoUrl)}" alt="${escapeHtml(brandName)}" />` : ''}
      <div>
        <h1>${escapeHtml(brandName)}</h1>
        <p>${escapeHtml(tagline)}</p>
      </div>
    </div>
    <div class="report-header-period">
      Relatório executivo
      <strong>${escapeHtml(input.periodLabel)}</strong>
    </div>
  </header>

  <h2 class="report-section-title">Indicadores-chave</h2>
  <div class="report-metrics-grid">${metricsHtml}</div>

  <h2 class="report-section-title">Funil de conversão</h2>
  <div>${funnelHtml}</div>

  <h2 class="report-section-title">Canais de aquisição</h2>
  ${channelsHtml}

  <h2 class="report-section-title">Receita</h2>
  <div class="report-revenue-grid">
    <div class="report-revenue-cell"><span class="label">Recebido</span><span class="value">${escapeHtml(input.revenue.paid)}</span></div>
    <div class="report-revenue-cell"><span class="label">Em atraso</span><span class="value">${escapeHtml(input.revenue.overdue)}</span></div>
    <div class="report-revenue-cell"><span class="label">Agendado</span><span class="value">${escapeHtml(input.revenue.scheduled)}</span></div>
    <div class="report-revenue-cell"><span class="label">A receber</span><span class="value">${escapeHtml(input.revenue.receivables)}</span></div>
  </div>
  ${trendSvg}

  <footer class="report-footer">
    <span>Gerado pelo ${escapeHtml(brandName)} em ${escapeHtml(generatedAtLabel)}</span>
    <span>Dados locais do sistema · sem envio a terceiros</span>
  </footer>
</body>
</html>`;
}

/** Abre o relatório em uma nova janela e aciona o diálogo nativo de impressão
 * (o usuário escolhe "Salvar como PDF"). Zero dependência nova. */
export function openExecutiveReport(html: string): Window | null {
  const reportWindow = window.open('', '_blank');
  if (!reportWindow) return null;
  reportWindow.document.open();
  reportWindow.document.write(html);
  reportWindow.document.close();
  reportWindow.focus();
  reportWindow.print();
  return reportWindow;
}
