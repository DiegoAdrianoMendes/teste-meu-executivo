/**
 * Painel de Error Logs — lista erros capturados com código/hash, status e ações.
 * Acessível somente por workspace_admin / workspace_owner / internal_staff.
 */
import { useState, useEffect, useCallback } from 'react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { AlertTriangle, CheckCircle2, Clock, RefreshCw, ChevronDown, ChevronUp } from 'lucide-react'
import { apiFetch } from '@/services/http'

interface ErrorLog {
  id: string
  error_code: string
  error_hash: string
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info'
  status: 'open' | 'in_progress' | 'resolved'
  exception_type: string
  message: string
  location: string | null
  endpoint: string | null
  method: string | null
  occurrences: number
  first_seen_at: string | null
  last_seen_at: string | null
  resolved_at: string | null
  resolved_by: string | null
  resolution_notes: string | null
}

interface ErrorSummary {
  by_status: Record<string, number>
  by_severity: Record<string, number>
  total_open: number
  total_in_progress: number
  total_resolved: number
}

interface ErrorLogPage {
  items: ErrorLog[]
  total: number
  skip: number
  limit: number
}

const SEVERITY_CONFIG = {
  critical: { label: 'Crítico', className: 'bg-red-100 text-red-800 border-red-200' },
  high: { label: 'Alto', className: 'bg-orange-100 text-orange-800 border-orange-200' },
  medium: { label: 'Médio', className: 'bg-yellow-100 text-yellow-800 border-yellow-200' },
  low: { label: 'Baixo', className: 'bg-blue-100 text-blue-800 border-blue-200' },
  info: { label: 'Info', className: 'bg-gray-100 text-gray-700 border-gray-200' },
}

const STATUS_CONFIG = {
  open: { label: 'Não corrigido', icon: AlertTriangle, className: 'bg-red-50 text-red-700 border-red-200' },
  in_progress: { label: 'Em andamento', icon: Clock, className: 'bg-yellow-50 text-yellow-700 border-yellow-200' },
  resolved: { label: 'Corrigido', icon: CheckCircle2, className: 'bg-green-50 text-green-700 border-green-200' },
}

export function ErrorLogsPanel() {
  const [errors, setErrors] = useState<ErrorLog[]>([])
  const [summary, setSummary] = useState<ErrorSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('open')
  const [page, setPage] = useState(0)
  const [total, setTotal] = useState(0)
  const pageSize = 25
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [resolveDialog, setResolveDialog] = useState<{ open: boolean; error: ErrorLog | null; target: 'in_progress' | 'resolved' }>({ open: false, error: null, target: 'resolved' })
  const [resolveNotes, setResolveNotes] = useState('')
  const [saving, setSaving] = useState(false)

  const loadData = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const [errorsData, summaryData] = await Promise.all([
        apiFetch<ErrorLogPage>(`/api/v1/error-logs?status=${statusFilter}&limit=${pageSize}&skip=${page * pageSize}`),
        apiFetch<ErrorSummary>('/api/v1/error-logs/summary'),
      ])
      setErrors(errorsData.items ?? [])
      setTotal(errorsData.total)
      setSummary(summaryData)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Não foi possível consultar os erros.')
    } finally {
      setLoading(false)
    }
  }, [page, statusFilter])

  useEffect(() => {
    void loadData()
  }, [loadData])

  const handleUpdateStatus = async (errorId: string, newStatus: string, notes?: string) => {
    setSaving(true)
    setSaveError(null)
    try {
      await apiFetch<void>(`/api/v1/error-logs/${errorId}/status`, {
        method: 'PATCH',
        body: JSON.stringify({
          status: newStatus,
          resolution_notes: notes || undefined,
          reason: notes,
        }),
      })
      await loadData()
      setResolveDialog({ open: false, error: null, target: 'resolved' })
      setResolveNotes('')
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : 'Não foi possível atualizar o erro.')
    } finally {
      setSaving(false)
    }
  }

  const fmt = (iso: string | null) => {
    if (!iso) return '—'
    return new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
  }

  return (
    <div className="space-y-5">
      {/* Summary Cards */}
      {summary && (
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: 'Não corrigidos', value: summary.total_open, color: 'text-red-600', bg: 'bg-red-50' },
            { label: 'Em andamento', value: summary.total_in_progress, color: 'text-yellow-600', bg: 'bg-yellow-50' },
            { label: 'Corrigidos', value: summary.total_resolved, color: 'text-green-600', bg: 'bg-green-50' },
          ].map((s) => (
            <div key={s.label} className={`rounded-lg border p-4 ${s.bg}`}>
              <p className={`text-2xl font-bold tabular-nums ${s.color}`}>{s.value}</p>
              <p className="text-xs text-muted-foreground mt-1">{s.label}</p>
            </div>
          ))}
        </div>
      )}

      {/* Filters + Refresh */}
      <div className="flex items-center gap-3">
        <Select value={statusFilter} onValueChange={(value) => { setStatusFilter(value); setPage(0) }}>
          <SelectTrigger className="w-44">
            <SelectValue placeholder="Filtrar status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="open">Não corrigidos</SelectItem>
            <SelectItem value="in_progress">Em andamento</SelectItem>
            <SelectItem value="resolved">Corrigidos</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" onClick={() => void loadData()} disabled={loading}>
          <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
          Atualizar
        </Button>
      </div>

      {loadError ? <Alert variant="destructive"><AlertTriangle className="h-4 w-4" /><AlertTitle>Erros indisponíveis</AlertTitle><AlertDescription>{loadError}</AlertDescription></Alert> : null}

      {/* Error List */}
      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-16 rounded-lg bg-muted animate-pulse" />
          ))}
        </div>
      ) : loadError ? null : errors.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            <CheckCircle2 className="mx-auto h-8 w-8 text-green-400 mb-2" />
            Nenhum erro encontrado neste filtro.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {errors.map((err) => {
            const sev = SEVERITY_CONFIG[err.severity] ?? SEVERITY_CONFIG.medium
            const st = STATUS_CONFIG[err.status] ?? STATUS_CONFIG.open
            const StatusIcon = st.icon
            const isExpanded = expandedId === err.id

            return (
              <Card key={err.id} className="overflow-hidden">
                <div
                  className="flex items-start gap-3 p-4 cursor-pointer hover:bg-muted/30 transition-colors"
                  onClick={() => setExpandedId(isExpanded ? null : err.id)}
                >
                  {/* Left: codes */}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <code className="text-xs font-mono bg-muted px-1.5 py-0.5 rounded text-foreground font-semibold">
                        {err.error_code}
                      </code>
                      <code className="text-xs font-mono text-muted-foreground">
                        #{err.error_hash}
                      </code>
                      <Badge variant="outline" className={`text-xs ${sev.className}`}>
                        {sev.label}
                      </Badge>
                      <Badge variant="outline" className={`text-xs flex items-center gap-1 ${st.className}`}>
                        <StatusIcon className="h-2.5 w-2.5" />
                        {st.label}
                      </Badge>
                      {err.occurrences > 1 && (
                        <span className="text-xs text-muted-foreground tabular-nums">
                          {err.occurrences}× ocorrências
                        </span>
                      )}
                    </div>
                    <p className="text-sm font-medium text-foreground mt-1 truncate">{err.exception_type}</p>
                    <p className="text-xs text-muted-foreground truncate">{err.message}</p>
                    <div className="flex gap-3 mt-1 text-xs text-muted-foreground">
                      {err.endpoint && <span>{err.method} {err.endpoint}</span>}
                      <span>Última vez: {fmt(err.last_seen_at)}</span>
                    </div>
                  </div>
                  {/* Right: actions */}
                  <div className="flex items-center gap-2 shrink-0">
                    {err.status === 'open' && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-xs h-7"
                        onClick={(e) => { e.stopPropagation(); setResolveNotes(''); setSaveError(null); setResolveDialog({ open: true, error: err, target: 'in_progress' }) }}
                      >
                        Em andamento
                      </Button>
                    )}
                    {err.status !== 'resolved' && (
                      <Button
                        size="sm"
                        variant="default"
                        className="text-xs h-7 bg-green-600 hover:bg-green-700"
                        onClick={(e) => { e.stopPropagation(); setResolveNotes(''); setSaveError(null); setResolveDialog({ open: true, error: err, target: 'resolved' }) }}
                      >
                        Resolver
                      </Button>
                    )}
                    {isExpanded ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
                  </div>
                </div>

                {/* Expanded details */}
                {isExpanded && (
                  <div className="border-t bg-muted/20 px-4 py-3 space-y-2 text-xs text-muted-foreground">
                    {err.location && (
                      <div>
                        <span className="font-semibold text-foreground">Localização:</span>{' '}
                        <code className="bg-muted px-1 py-0.5 rounded">{err.location}</code>
                      </div>
                    )}
                    <div>
                      <span className="font-semibold text-foreground">Primeira ocorrência:</span>{' '}
                      {fmt(err.first_seen_at)}
                    </div>
                    {err.resolved_at && (
                      <div>
                        <span className="font-semibold text-foreground">Resolvido em:</span>{' '}
                        {fmt(err.resolved_at)} por {err.resolved_by ?? '—'}
                      </div>
                    )}
                    {err.resolution_notes && (
                      <div>
                        <span className="font-semibold text-foreground">Notas:</span>{' '}
                        {err.resolution_notes}
                      </div>
                    )}
                  </div>
                )}
              </Card>
            )
          })}
        </div>
      )}

      {!loading && !loadError && total > 0 ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">{page * pageSize + 1}–{Math.min((page + 1) * pageSize, total)} de {total}</p>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" disabled={page === 0 || loading} onClick={() => setPage((current) => Math.max(0, current - 1))}>Anterior</Button>
            <Button size="sm" variant="outline" disabled={(page + 1) * pageSize >= total || loading} onClick={() => setPage((current) => current + 1)}>Próxima</Button>
          </div>
        </div>
      ) : null}

      {/* Resolve Dialog */}
      <Dialog open={resolveDialog.open} onOpenChange={(o) => { if (!o) setResolveDialog({ open: false, error: null, target: 'resolved' }) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{resolveDialog.target === 'resolved' ? 'Marcar como corrigido' : 'Iniciar tratamento'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            {resolveDialog.error && (
              <div className="rounded-lg bg-muted p-3 text-sm">
                <code className="font-mono font-semibold">{resolveDialog.error.error_code}</code>
                <p className="text-muted-foreground mt-1 text-xs">{resolveDialog.error.message}</p>
              </div>
            )}
            <div>
              <label className="text-sm font-medium">Motivo e notas da alteração</label>
              <Textarea
                className="mt-1 text-sm"
                placeholder="Descreva a ação executada (mínimo de 8 caracteres)..."
                value={resolveNotes}
                onChange={(e) => setResolveNotes(e.target.value)}
                rows={3}
              />
            </div>
            {saveError ? <Alert variant="destructive"><AlertTriangle className="h-4 w-4" /><AlertTitle>Alteração não concluída</AlertTitle><AlertDescription>{saveError}</AlertDescription></Alert> : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setResolveDialog({ open: false, error: null, target: 'resolved' })}>
              Cancelar
            </Button>
            <Button
              className="bg-green-600 hover:bg-green-700"
              disabled={saving || resolveNotes.trim().length < 8}
              onClick={() => resolveDialog.error && void handleUpdateStatus(resolveDialog.error.id, resolveDialog.target, resolveNotes.trim())}
            >
              {saving ? 'Salvando...' : resolveDialog.target === 'resolved' ? 'Marcar como corrigido' : 'Iniciar tratamento'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
