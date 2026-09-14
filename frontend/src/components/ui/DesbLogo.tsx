import { buildDefaultBrand } from '@/branding/workspace-defaults';

/**
 * Compatibilidade para os consumidores legados do logo. Os assets vêm da
 * mesma identidade canônica usada como fallback do produto, evitando que as
 * telas públicas/autenticação voltem a apontar para arquivos KLM antigos.
 */

const PRODUCT_BRAND = buildDefaultBrand({ workspaceId: 'public' });

interface DesbLogoProps {
  variant?: 'icon' | 'full' | 'mark';
  size?: number;
  dark?: boolean;
  className?: string;
}

export function DesbLogo({ variant = 'icon', size = 32, dark = false, className = '' }: DesbLogoProps) {
  const full = variant === 'full';
  const src = full
    ? (dark ? PRODUCT_BRAND.assets.logoDarkUrl : PRODUCT_BRAND.assets.logoLightUrl)
    : PRODUCT_BRAND.assets.markUrl;
  return (
    <img
      src={src}
      alt={PRODUCT_BRAND.name}
      width={full ? undefined : size}
      height={size}
      data-brand-logo={full ? 'wordmark' : 'mark'}
      style={{ width: full ? 'auto' : size, height: size, display: 'block', objectFit: 'contain' }}
      className={className}
    />
  );
}
