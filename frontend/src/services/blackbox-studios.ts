import { apiFetch } from '@/services/http';

export type PerformanceAudience = 'workspace_team' | 'saas_team' | 'workspace_benchmark';

export interface PerformanceRow {
  id: string;
  name: string;
  scopeName: string;
  role?: string | null;
  primaryCount: number;
  completedCount: number;
  pendingCount: number;
  qualityValue?: number | null;
  score: number;
}

export interface PerformanceBoard {
  audience: PerformanceAudience;
  periodDays: number;
  generatedAt: string;
  scoreFormula: string;
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  rows: PerformanceRow[];
}

export type CreativeKind = 'creative' | 'campaign';
export type CreativeStatus = 'draft' | 'approved' | 'archived';
export type CreativeChannel = 'meta' | 'google' | 'tiktok' | 'linkedin' | 'organic' | 'other';

export interface CreativeLibraryItem {
  id: string;
  name: string;
  kind: CreativeKind;
  channel: CreativeChannel;
  objective: string;
  status: CreativeStatus;
  tags: string[];
  assetUrl?: string | null;
  copyText?: string | null;
  notes?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface CreativeLibrary {
  currentVersion: number;
  etag?: string | null;
  updatedAt?: string | null;
  items: CreativeLibraryItem[];
}

export interface CreativeLibraryPage extends CreativeLibrary {
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

export interface AdvancedReportRow {
  key: string;
  label: string;
  category: string;
  categoryLabel: string;
  value: unknown;
  unit?: string | null;
  deltaPct?: number | null;
  hasData: boolean;
  note?: string | null;
  series: Array<{ date: string; value: number }>;
}

export interface AdvancedReport {
  generatedAt: string;
  periodDays: number;
  availableCategories: Array<{ key: string; label: string }>;
  appliedFilters: Record<string, unknown>;
  totalRows: number;
  rowsWithData: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  rows: AdvancedReportRow[];
  chartRows: Array<{ key: string; label: string; value: number; category: string }>;
  trendRows: Array<{ metricKey: string; metricLabel: string; date: string; value: number }>;
}

const baseUrl = '/api/v1/platform/blackbox-studios';

export function fetchWorkspaceTeamPerformance(
  periodDays: number,
  workspaceId?: string,
  limit = 25,
  offset = 0,
): Promise<PerformanceBoard> {
  const query = new URLSearchParams({
    periodDays: String(periodDays),
    limit: String(limit),
    offset: String(offset),
  });
  if (workspaceId) query.set('workspaceId', workspaceId);
  return apiFetch(`${baseUrl}/performance/workspace-team?${query}`);
}

export function fetchSaasTeamPerformance(
  periodDays: number,
  limit = 25,
  offset = 0,
): Promise<PerformanceBoard> {
  const query = new URLSearchParams({
    periodDays: String(periodDays),
    limit: String(limit),
    offset: String(offset),
  });
  return apiFetch(`${baseUrl}/performance/saas-team?${query}`);
}

export function fetchWorkspaceBenchmark(
  periodDays: number,
  limit = 25,
  offset = 0,
): Promise<PerformanceBoard> {
  const query = new URLSearchParams({
    periodDays: String(periodDays),
    limit: String(limit),
    offset: String(offset),
  });
  return apiFetch(`${baseUrl}/performance/workspace-benchmark?${query}`);
}

export function fetchCreativeLibrary(): Promise<CreativeLibrary> {
  return apiFetch(`${baseUrl}/creative-library`);
}

export function fetchCreativeLibraryPage(input: {
  status: 'active' | CreativeStatus | 'all';
  query?: string;
  limit?: number;
  offset?: number;
}): Promise<CreativeLibraryPage> {
  const query = new URLSearchParams({
    status: input.status,
    limit: String(input.limit ?? 25),
    offset: String(input.offset ?? 0),
  });
  if (input.query?.trim()) query.set('query', input.query.trim());
  return apiFetch(`${baseUrl}/creative-library/page?${query}`);
}

export function saveCreativeLibraryItem(input: {
  id?: string;
  name: string;
  kind: CreativeKind;
  channel: CreativeChannel;
  objective: string;
  status: CreativeStatus;
  tags: string[];
  assetUrl?: string;
  copyText?: string;
  notes?: string;
  expectedEtag?: string;
}): Promise<CreativeLibrary> {
  return apiFetch(`${baseUrl}/creative-library/items`, {
    method: 'PUT',
    body: JSON.stringify(input),
  });
}

export function archiveCreativeLibraryItem(
  itemId: string,
  expectedEtag?: string,
): Promise<CreativeLibrary> {
  return apiFetch(`${baseUrl}/creative-library/items/${encodeURIComponent(itemId)}/archive`, {
    method: 'POST',
    body: JSON.stringify({ expectedEtag }),
  });
}

export function fetchAdvancedReport(input: {
  periodDays: number;
  category?: string;
  query?: string;
  onlyWithData?: boolean;
  sortBy?: 'label' | 'category' | 'value';
  sortDirection?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
}): Promise<AdvancedReport> {
  const query = new URLSearchParams({
    periodDays: String(input.periodDays),
    onlyWithData: String(Boolean(input.onlyWithData)),
    sortBy: input.sortBy ?? 'label',
    sortDirection: input.sortDirection ?? 'asc',
    limit: String(input.limit ?? 150),
    offset: String(input.offset ?? 0),
  });
  if (input.category) query.set('category', input.category);
  if (input.query) query.set('query', input.query);
  return apiFetch(`${baseUrl}/reports?${query}`);
}
