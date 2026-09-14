import { Star } from 'lucide-react';

interface StarsRatingProps {
  rating: number;
  max?: number;
  interactive?: boolean;
  onRate?: (rating: number) => void;
  size?: 'sm' | 'md';
}

export function StarsRating({ rating, max = 5, interactive = false, onRate, size = 'sm' }: StarsRatingProps) {
  const sizeClass = size === 'md' ? 'h-4 w-4' : 'h-3 w-3';

  return (
    <div className="flex items-center gap-0.5">
      {Array.from({ length: max }, (_, i) => (
        <Star
          key={i}
          className={`${sizeClass} ${interactive ? 'cursor-pointer hover:scale-110 transition-transform' : ''} ${
            i < rating ? 'fill-[hsl(var(--warning))] text-[hsl(var(--warning))]' : 'text-muted-foreground/30'
          }`}
          onClick={interactive && onRate ? (e) => { e.stopPropagation(); onRate(i + 1); } : undefined}
        />
      ))}
    </div>
  );
}
