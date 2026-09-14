import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { AlertCircle, FileBarChart, LoaderCircle, RotateCcw, Search } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { fetchAdvancedReport } from '@/services/blackbox-studios';

const REPORT_PAGE_SIZE = 25;

function formatValue(value: unknown, unit?: string | null): string {
  if (value === null || value === undefined) return 'Sem dado';
  if (typeof value !== 'number') return String(value);
  if (unit === 'brl') return value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
  if (unit === 'percent' || unit === '%') return `${value.toLocaleString('pt-BR')}%`;
  return value.toLocaleString('pt-BR');
}

export default function BlackboxAdvancedReports() {
  const [periodDays, setPeriodDays] = useState(30);
  const [category, setCategory] = useState('all');
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [onlyWithData, setOnlyWithData] = useState(false);
  const [sortBy, setSortBy] = useState<'label' | 'category' | 'value'>('label');
  const [page, setPage] = useState(0);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 350);
    return () => window.clearTimeout(timer);
  }, [search]);
  useEffect(() => {
    setPage(0);
  }, [category, debouncedSearch, onlyWithData, periodDays, sortBy]);

  const report = useQuery({
    queryKey: [
      'blackbox',
      'advanced-reports',
      periodDays,
      category,
      debouncedSearch,
      onlyWithData,
      sortBy,
      page,
    ],
    queryFn: () => fetchAdvancedReport({
      periodDays,
      category: category === 'all' ? undefined : category,
      query: debouncedSearch || undefined,
      onlyWithData,
      sortBy,
      sortDirection: sortBy === 'value' ? 'desc' : 'asc',
      limit: REPORT_PAGE_SIZE,
      offset: page * REPORT_PAGE_SIZE,
    }),
  });
  const trendKeys = useMemo(
    () => Array.from(new Set((report.data?.trendRows ?? []).map((row) => row.metricKey))).slice(0, 5),
    [report.data?.trendRows],
  );
  const trendData = useMemo(() => {
    const byDate = new Map<string, Record<string, string | number>>();
    for (const point of report.data?.trendRows ?? []) {
      const current = byDate.get(point.date) ?? { date: point.date };
      current[point.metricKey] = point.value;
      byDate.set(point.date, current);
    }
    return Array.from(byDate.values()).sort((left, right) => String(left.date).localeCompare(String(right.date)));
  }, [report.data?.trendRows]);

  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-5 p-6">
      <div>
        <div className="flex items-center gap-2">
          <FileBarChart className="h-5 w-5 text-blue-600" />
          <h1 className="text-xl font-semibold">Relatórios avançados</h1>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Filtros, tabelas e gráficos sobre o motor canônico de indicadores da plataforma.
        </p>
      </div>

      <Card>
        <CardContent className="grid gap-3 pt-6 md:grid-cols-[1fr_180px_180px_180px_auto]">
          <div className="relative">
            <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
            <Input value={search} onChange={(event) => setSearch(event.target.value)} className="pl-9" placeholder="Buscar indicador" />
          </div>
          <Select value={String(periodDays)} onValueChange={(value) => setPeriodDays(Number(value))}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {[7, 30, 90, 180, 365].map((period) => (
                <SelectItem key={period} value={String(period)}>{period} dias</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={category} onValueChange={setCategory}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todas as categorias</SelectItem>
              {(report.data?.availableCategories ?? []).map((item) => (
                <SelectItem key={item.key} value={item.key}>{item.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={sortBy} onValueChange={(value) => setSortBy(value as typeof sortBy)}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="label">Ordenar por nome</SelectItem>
              <SelectItem value="category">Ordenar por categoria</SelectItem>
              <SelectItem value="value">Ordenar por valor</SelectItem>
            </SelectContent>
          </Select>
          <Label className="flex min-h-10 items-center gap-2 rounded-md border px-3">
            <Checkbox checked={onlyWithData} onCheckedChange={(value) => setOnlyWithData(Boolean(value))} />
            Somente com dados
          </Label>
        </CardContent>
      </Card>

      {report.isLoading && !report.data && (
        <Card>
          <CardContent className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
            <LoaderCircle className="h-4 w-4 animate-spin" />
            Carregando indicadores...
          </CardContent>
        </Card>
      )}

      {report.error && !report.data && (
        <Card>
          <CardContent className="flex flex-wrap items-center justify-center gap-3 py-8 text-sm text-destructive">
            <AlertCircle className="h-4 w-4" />
            Não foi possível carregar os relatórios.
            <Button type="button" variant="outline" size="sm" onClick={() => void report.refetch()}>
              <RotateCcw className="mr-2 h-4 w-4" />
              Tentar novamente
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-2"><CardDescription>Indicadores filtrados</CardDescription></CardHeader>
          <CardContent><p className="text-2xl font-semibold">{report.data?.totalRows ?? '—'}</p></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardDescription>Com dados disponíveis</CardDescription></CardHeader>
          <CardContent><p className="text-2xl font-semibold">{report.data?.rowsWithData ?? '—'}</p></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardDescription>Período analisado</CardDescription></CardHeader>
          <CardContent><p className="text-2xl font-semibold">{periodDays} dias</p></CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle className="text-base">Comparação de indicadores numéricos</CardTitle>
            <CardDescription>Até 20 indicadores da página atual.</CardDescription>
          </CardHeader>
          <CardContent className="h-[360px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={report.data?.chartRows ?? []}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="label" hide />
                <YAxis />
                <Tooltip />
                <Bar dataKey="value" fill="#0b67f0" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle className="text-base">Séries históricas disponíveis</CardTitle>
            <CardDescription>Até cinco indicadores com série na página atual.</CardDescription>
          </CardHeader>
          <CardContent className="h-[360px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={trendData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" />
                <YAxis />
                <Tooltip />
                <Legend />
                {trendKeys.map((key, index) => (
                  <Line key={key} dataKey={key} type="monotone" stroke={['#0b67f0', '#16a34a', '#d97706', '#7c3aed', '#dc2626'][index]} dot={false} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      <Card className="min-w-0">
        <CardHeader>
          <CardTitle className="text-base">Tabela analítica</CardTitle>
          <CardDescription>Leitura detalhada e notas metodológicas do indicador.</CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Indicador</TableHead>
                <TableHead>Categoria</TableHead>
                <TableHead>Valor</TableHead>
                <TableHead>Variação</TableHead>
                <TableHead>Disponibilidade</TableHead>
                <TableHead>Nota</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(report.data?.rows ?? []).map((row) => (
                <TableRow key={row.key}>
                  <TableCell>
                    <div className="font-medium">{row.label}</div>
                    <div className="text-xs text-muted-foreground">{row.key}</div>
                  </TableCell>
                  <TableCell>{row.categoryLabel}</TableCell>
                  <TableCell>{formatValue(row.value, row.unit)}</TableCell>
                  <TableCell>{row.deltaPct == null ? 'Sem comparação' : `${row.deltaPct.toLocaleString('pt-BR')}%`}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{row.hasData ? 'Com dado' : 'Sem dado'}</Badge>
                  </TableCell>
                  <TableCell className="max-w-80 whitespace-normal text-xs text-muted-foreground">
                    {row.note ?? 'Sem observação adicional.'}
                  </TableCell>
                </TableRow>
              ))}
              {report.data && report.data.rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="py-8 text-center text-sm text-muted-foreground">
                    Nenhum indicador encontrado com esses filtros.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
          {report.data && report.data.rows.length > 0 && (
            <div className="mt-4 flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">
                {`${report.data.offset + 1}–${Math.min(report.data.offset + report.data.rows.length, report.data.totalRows)} de ${report.data.totalRows}`}
              </span>
              <span className="flex gap-2">
                <Button type="button" size="sm" variant="outline" disabled={page === 0 || report.isFetching} onClick={() => setPage((value) => Math.max(0, value - 1))}>
                  Anterior
                </Button>
                <Button type="button" size="sm" variant="outline" disabled={!report.data.hasMore || report.isFetching} onClick={() => setPage((value) => value + 1)}>
                  Próxima
                </Button>
              </span>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
