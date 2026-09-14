import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Gauge, Loader2, ShieldAlert } from 'lucide-react';

import { KlmPill, KlmProgress } from '@/klm/components/Primitives';
import { fetchWhatsAppConnectionHealth } from '@/services/whatsapp';
import type { WhatsAppConnectionTier, WhatsAppQualityRating } from '@/types';

const TIER_LABELS: Record<WhatsAppConnectionTier, string> = {
  tier_250: '250 destinatários/dia',
  tier_1k: '1.000 destinatários/dia',
  tier_10k: '10.000 destinatários/dia',
  tier_100k: '100.000 destinatários/dia',
  unlimited: 'Ilimitado',
};

const QUALITY_LABELS: Record<WhatsAppQualityRating, string> = {
  verde: 'Qualidade alta',
  amarelo: 'Qualidade média',
  vermelho: 'Qualidade baixa',
};

function qualityTone(rating: WhatsAppQualityRating | null): 'green' | 'gold' | 'red' | 'muted' {
  if (rating === 'verde') return 'green';
  if (rating === 'amarelo') return 'gold';
  if (rating === 'vermelho') return 'red';
  return 'muted';
}

function progressTone(usageRatio: number): 'green' | 'gold' | 'red' {
  if (usageRatio >= 0.9) return 'red';
  if (usageRatio >= 0.6) return 'gold';
  return 'green';
}

export function WhatsAppConnectionHealthCard({ connectionId }: { connectionId: string }) {
  const healthQuery = useQuery({
    queryKey: ['whatsapp-connection-health', connectionId],
    queryFn: () => fetchWhatsAppConnectionHealth(connectionId),
    enabled: Boolean(connectionId),
    staleTime: 30_000,
  });

  if (healthQuery.isLoading) {
    return (
      <div className="mt-3 rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
        <Loader2 className="mr-2 inline h-3.5 w-3.5 animate-spin" /> Carregando saúde do número…
      </div>
    );
  }

  if (healthQuery.isError || !healthQuery.data) {
    return (
      <div className="mt-3 flex items-center gap-2 rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
        <AlertTriangle className="h-3.5 w-3.5" />
        Sem dados de saúde do número ainda para esta conexão.
      </div>
    );
  }

  const health = healthQuery.data;
  const usageRatio = health.dailyLimit ? Math.min(1, health.dailyUsed / health.dailyLimit) : 0;
  const usagePercent = Math.round(usageRatio * 100);

  return (
    <div className="mt-3 rounded-md border border-border bg-muted/30 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-xs font-medium text-foreground">
          <Gauge className="h-3.5 w-3.5 text-muted-foreground" />
          Saúde do número
        </div>
        <KlmPill tone={qualityTone(health.qualityRating)}>
          {health.qualityRating ? QUALITY_LABELS[health.qualityRating] : 'Qualidade desconhecida'}
        </KlmPill>
      </div>

      <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>Tier {TIER_LABELS[health.tier]}</span>
        <span>Estágio de rampa {health.rampUpStage}</span>
      </div>

      {health.dailyLimit ? (
        <div className="mt-2">
          <KlmProgress value={usagePercent} tone={progressTone(usageRatio)} />
          <p className="mt-1 text-xs text-muted-foreground">
            {health.dailyUsed.toLocaleString('pt-BR')} de {health.dailyLimit.toLocaleString('pt-BR')} destinatários únicos usados hoje ({usagePercent}%)
          </p>
        </div>
      ) : (
        <p className="mt-2 text-xs text-muted-foreground">
          {health.dailyUsed.toLocaleString('pt-BR')} destinatários únicos hoje · tier sem limite diário.
        </p>
      )}

      {!health.syncedFromMeta && (
        <div className="mt-2 flex items-start gap-1.5 rounded-md bg-secondary px-2 py-1.5 text-xs text-muted-foreground">
          <ShieldAlert className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{health.syncMessage}</span>
        </div>
      )}
    </div>
  );
}
