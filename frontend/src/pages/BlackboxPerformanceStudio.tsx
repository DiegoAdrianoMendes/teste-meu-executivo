import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { AlertCircle, BarChart3, Building2, LoaderCircle, RotateCcw, Trophy, UsersRound } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  fetchSaasTeamPerformance,
  fetchWorkspaceBenchmark,
  fetchWorkspaceTeamPerformance,
  type PerformanceBoard,
} from '@/services/blackbox-studios';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { isPlatformRoot } from '@/lib/platform-access';
import { useAppStore } from '@/store/app-context';

const periods = [7, 30, 90, 180];

function Board({
  board,
  emptyLabel,
  isLoading,
  error,
  onRetry,
  offset,
  onPageChange,
}: {
  board?: PerformanceBoard;
  emptyLabel: string;
  isLoading?: boolean;
  error?: Error | null;
  onRetry?: () => void;
  offset: number;
  onPageChange: (offset: number) => void;
}) {
  const chartRows = useMemo(
    () => (board?.rows ?? []).slice(0, 12).map((row) => ({ name: row.name, score: row.score })),
    [board],
  );
  useEffect(() => {
    if (!board || offset === 0 || board.rows.length > 0) return;
    onPageChange(Math.max(0, offset - board.limit));
  }, [board, offset, onPageChange]);
  if (isLoading && !board) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
          <LoaderCircle className="h-4 w-4 animate-spin" />
          Carregando dados do período...
        </CardContent>
      </Card>
    );
  }
  if (error && !board) {
    return (
      <Card>
        <CardContent className="flex flex-wrap items-center justify-center gap-3 py-10 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" />
          Não foi possível carregar estes dados.
          {onRetry && (
            <Button type="button" variant="outline" size="sm" onClick={onRetry}>
              <RotateCcw className="mr-2 h-4 w-4" />
              Tentar novamente
            </Button>
          )}
        </CardContent>
      </Card>
    );
  }
  if (!board?.rows.length) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          {emptyLabel}
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(420px,0.8fr)]">
      <Card className="min-w-0">
        <CardHeader>
          <CardTitle className="text-base">Classificação do período</CardTitle>
          <CardDescription>{board.scoreFormula}</CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Posição</TableHead>
                <TableHead>Nome</TableHead>
                <TableHead>Escopo</TableHead>
                <TableHead>Entradas</TableHead>
                <TableHead>Concluídos</TableHead>
                <TableHead>Pendentes</TableHead>
                <TableHead>Qualidade</TableHead>
                <TableHead className="text-right">Pontos</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {board.rows.map((row, index) => (
                <TableRow key={row.id}>
                  <TableCell>{offset + index + 1}</TableCell>
                  <TableCell className="font-medium">{row.name}</TableCell>
                  <TableCell>
                    <div>{row.scopeName}</div>
                    {row.role && <div className="text-xs text-muted-foreground">{row.role}</div>}
                  </TableCell>
                  <TableCell>{row.primaryCount}</TableCell>
                  <TableCell>{row.completedCount}</TableCell>
                  <TableCell>{row.pendingCount}</TableCell>
                  <TableCell>{row.qualityValue ?? 'Sem dado'}</TableCell>
                  <TableCell className="text-right font-semibold">{row.score}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t px-6 py-4 text-sm text-muted-foreground">
          <span>
            {board.total > 0
              ? `${offset + 1}–${Math.min(offset + board.rows.length, board.total)} de ${board.total}`
              : 'Nenhum resultado'}
          </span>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={offset === 0 || isLoading}
              onClick={() => onPageChange(Math.max(0, offset - board.limit))}
            >
              Anterior
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!board.hasMore || isLoading}
              onClick={() => onPageChange(offset + board.limit)}
            >
              Próxima
            </Button>
          </div>
        </div>
      </Card>
      <Card className="min-w-0">
        <CardHeader>
          <CardTitle className="text-base">Pontuação comparada</CardTitle>
          <CardDescription>Até 12 primeiras posições no filtro atual.</CardDescription>
        </CardHeader>
        <CardContent className="h-[360px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartRows} layout="vertical" margin={{ left: 12, right: 18 }}>
              <CartesianGrid strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" />
              <YAxis dataKey="name" type="category" width={130} tick={{ fontSize: 12 }} />
              <Tooltip />
              <Bar dataKey="score" fill="#0b67f0" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>
    </div>
  );
}

export default function BlackboxPerformanceStudio() {
  const { state } = useAppStore();
  const [periodDays, setPeriodDays] = useState(30);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [officeOffset, setOfficeOffset] = useState(0);
  const [saasOffset, setSaasOffset] = useState(0);
  const [benchmarkOffset, setBenchmarkOffset] = useState(0);
  const pageSize = 25;
  const isRoot = isPlatformRoot(state.currentUser);
  const office = useQuery({
    queryKey: ['blackbox', 'performance', 'office', periodDays, workspaceId, officeOffset],
    queryFn: () => fetchWorkspaceTeamPerformance(
      periodDays,
      workspaceId ?? undefined,
      pageSize,
      officeOffset,
    ),
    enabled: isRoot,
  });
  const saas = useQuery({
    queryKey: ['blackbox', 'performance', 'saas', periodDays, saasOffset],
    queryFn: () => fetchSaasTeamPerformance(periodDays, pageSize, saasOffset),
  });
  const benchmark = useQuery({
    queryKey: ['blackbox', 'performance', 'benchmark', periodDays, benchmarkOffset],
    queryFn: () => fetchWorkspaceBenchmark(periodDays, pageSize, benchmarkOffset),
    enabled: isRoot,
  });

  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-5 p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Trophy className="h-5 w-5 text-blue-600" />
            <h1 className="text-xl font-semibold">Desempenho e gamificação</h1>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Critérios transparentes, dados reais e públicos separados por responsabilidade.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Select
            value={String(periodDays)}
            onValueChange={(value) => {
              setPeriodDays(Number(value));
              setOfficeOffset(0);
              setSaasOffset(0);
              setBenchmarkOffset(0);
            }}
          >
            <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
            <SelectContent>
              {periods.map((period) => (
                <SelectItem key={period} value={String(period)}>Últimos {period} dias</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {isRoot && (
            <PlatformEntityCombobox
              className="w-72"
              entityType="accounts"
              purpose="performance_workspace"
              value={workspaceId}
              onChange={(value) => {
                setWorkspaceId(value);
                setOfficeOffset(0);
              }}
              label="Filtrar escritório"
              placeholder="Todos os escritórios"
            />
          )}
        </div>
      </div>

      <Tabs defaultValue={isRoot ? 'office' : 'saas'}>
        <TabsList>
          {isRoot && (
            <TabsTrigger value="office" className="gap-2">
              <UsersRound className="h-4 w-4" /> Equipes dos escritórios
            </TabsTrigger>
          )}
          <TabsTrigger value="saas" className="gap-2">
            <BarChart3 className="h-4 w-4" /> Equipe do SaaS
          </TabsTrigger>
          {isRoot && (
            <TabsTrigger value="benchmark" className="gap-2">
              <Building2 className="h-4 w-4" /> Benchmark
            </TabsTrigger>
          )}
        </TabsList>
        {isRoot && (
          <TabsContent value="office" className="mt-4">
            <Board
              board={office.data}
              emptyLabel="Nenhum dado de equipe encontrado para o filtro."
              isLoading={office.isLoading}
              error={office.error}
              onRetry={() => void office.refetch()}
              offset={officeOffset}
              onPageChange={setOfficeOffset}
            />
          </TabsContent>
        )}
        <TabsContent value="saas" className="mt-4">
          <Board
            board={saas.data}
            emptyLabel="Nenhum dado da equipe do SaaS no período."
            isLoading={saas.isLoading}
            error={saas.error}
            onRetry={() => void saas.refetch()}
            offset={saasOffset}
            onPageChange={setSaasOffset}
          />
        </TabsContent>
        {isRoot && (
          <TabsContent value="benchmark" className="mt-4 space-y-3">
            <Badge variant="outline">Exclusivo do titular · não exibido às contas</Badge>
            <Board
              board={benchmark.data}
              emptyLabel="Nenhum escritório disponível para comparação."
              isLoading={benchmark.isLoading}
              error={benchmark.error}
              onRetry={() => void benchmark.refetch()}
              offset={benchmarkOffset}
              onPageChange={setBenchmarkOffset}
            />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
