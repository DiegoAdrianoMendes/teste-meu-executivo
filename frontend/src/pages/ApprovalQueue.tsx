import { useState } from 'react';
import { SectionHeader } from '@/components/common/SectionHeader';
import { PriorityBadge } from '@/components/common/PriorityBadge';
import { EmptyState } from '@/components/common/EmptyState';
import { ApprovalReviewPanel } from '@/components/approval/ApprovalReviewPanel';
import { useAppStore } from '@/store/app-context';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Check, X, Clock, Eye } from 'lucide-react';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { useIsMobile } from '@/hooks/use-mobile';
import type { ApprovalItem } from '@/types';

export default function ApprovalQueue() {
  const { state, resolveApproval } = useAppStore();
  const isMobile = useIsMobile();
  const [reviewItem, setReviewItem] = useState<ApprovalItem | null>(null);
  const pending = state.approvalItems.filter(a => a.status === 'pending');
  const resolved = state.approvalItems.filter(a => a.status !== 'pending');

  if (reviewItem) {
    const current = state.approvalItems.find(a => a.id === reviewItem.id);
    if (current && current.status === 'pending') {
      return (
        <div className="max-w-7xl">
          <ApprovalReviewPanel item={current} onBack={() => setReviewItem(null)} />
        </div>
      );
    }
  }

  return (
    <div className="space-y-6 max-w-6xl">
      <SectionHeader
        title="Fila de Aprovações"
        description={`${pending.length} pendente(s) · ${resolved.length} resolvido(s)`}
      />

      {pending.length === 0 ? (
        <EmptyState title="Nenhuma aprovação pendente" description="Todas as solicitações foram resolvidas." />
      ) : isMobile ? (
        /* Mobile: Card-based approval list */
        <div className="space-y-2">
          {pending.map(item => {
            return (
              <Card key={item.id} style={{ boxShadow: 'var(--shadow-card)' }}>
                <CardContent className="p-3 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="min-w-0 text-sm font-semibold truncate">{item.clientName}</span>
                    <PriorityBadge priority={item.priority} />
                  </div>
                  <p className="text-xs text-muted-foreground">{item.title}</p>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground flex items-center gap-1">
                      <Clock className="h-3 w-3 shrink-0" />
                      {(item.requestedBy || 'Equipe') } · {new Date(item.requestedAt).toLocaleDateString('pt-BR')}
                    </span>
                    <Badge variant="outline" className="shrink-0 text-xs">{item.type}</Badge>
                  </div>
                  <div className="flex items-center gap-1.5 pt-1">
                    <Button size="sm" variant="outline" className="h-7 text-xs gap-1 flex-1" onClick={() => setReviewItem(item)}>
                      <Eye className="h-3 w-3" /> Revisar
                    </Button>
                    <Button size="sm" className="h-7 text-xs gap-1 bg-[hsl(var(--success))] hover:bg-[hsl(var(--success))]/90 text-white" onClick={() => resolveApproval(item.id, 'approved')}>
                      <Check className="h-3 w-3" />
                    </Button>
                    <Button size="sm" variant="outline" className="h-7 text-xs gap-1 text-destructive border-destructive/30 hover:bg-destructive/10" onClick={() => resolveApproval(item.id, 'rejected')}>
                      <X className="h-3 w-3" />
                    </Button>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      ) : (
        <div className="border rounded-lg overflow-hidden" style={{ boxShadow: 'var(--shadow-card)' }}>
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/50">
                <TableHead className="text-xs uppercase tracking-wider font-semibold">Cliente</TableHead>
                <TableHead className="text-xs uppercase tracking-wider font-semibold">Tipo</TableHead>
                <TableHead className="text-xs uppercase tracking-wider font-semibold">Título</TableHead>
                <TableHead className="text-xs uppercase tracking-wider font-semibold">Solicitante</TableHead>
                <TableHead className="text-xs uppercase tracking-wider font-semibold">Data</TableHead>
                <TableHead className="text-xs uppercase tracking-wider font-semibold">Prioridade</TableHead>
                <TableHead className="text-xs uppercase tracking-wider font-semibold text-right">Ações</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {pending.map(item => {
                return (
                  <TableRow key={item.id} className="hover:bg-muted/50">
                    <TableCell>
                      <span className="text-sm font-semibold">{item.clientName}</span>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">{item.type}</Badge>
                    </TableCell>
                    <TableCell>
                      <span className="text-sm">{item.title}</span>
                    </TableCell>
                    <TableCell>
                      <span className="text-xs text-muted-foreground">{item.requestedBy || 'Equipe'}</span>
                    </TableCell>
                    <TableCell>
                      <span className="text-xs text-muted-foreground flex items-center gap-1">
                        <Clock className="h-3 w-3 shrink-0" />
                        {new Date(item.requestedAt).toLocaleDateString('pt-BR')}
                      </span>
                    </TableCell>
                    <TableCell>
                      <PriorityBadge priority={item.priority} />
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-1">
                        <Button size="sm" variant="outline" className="h-7 text-xs gap-1" onClick={() => setReviewItem(item)}>
                          <Eye className="h-3 w-3" /> Revisar
                        </Button>
                        <Button size="sm" className="h-7 text-xs gap-1 bg-[hsl(var(--success))] hover:bg-[hsl(var(--success))]/90 text-white" onClick={() => resolveApproval(item.id, 'approved')}>
                          <Check className="h-3 w-3" />
                        </Button>
                        <Button size="sm" variant="outline" className="h-7 text-xs gap-1 text-destructive border-destructive/30 hover:bg-destructive/10" onClick={() => resolveApproval(item.id, 'rejected')}>
                          <X className="h-3 w-3" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Resolved */}
      {resolved.length > 0 && (
        <div>
          <h3 className="text-base font-medium mb-3 text-muted-foreground">Resolvidos</h3>
          <div className="space-y-2">
            {resolved.map(item => (
              <Card key={item.id} className="border opacity-60">
                <CardContent className="p-3 flex items-center justify-between gap-2 flex-wrap">
                  <div className="flex items-center gap-3 min-w-0">
                    <p className="min-w-0 text-sm font-medium truncate">{item.title}</p>
                    <span className="text-xs text-muted-foreground shrink-0">{item.clientName}</span>
                  </div>
                  <Badge variant={item.status === 'approved' ? 'default' : 'destructive'} className="text-xs shrink-0">
                    {item.status === 'approved' ? 'Aprovado' : 'Rejeitado'}
                  </Badge>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
