import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { cn } from '@/lib/utils';

type ContactAvatarProps = {
  name: string;
  initials: string;
  avatarUrl?: string | null;
  className?: string;
  fallbackClassName?: string;
  imageClassName?: string;
};

export function ContactAvatar({
  name,
  initials,
  avatarUrl,
  className,
  fallbackClassName,
  imageClassName,
}: ContactAvatarProps) {
  return (
    <Avatar className={cn('bg-[#031121]', className)}>
      {avatarUrl ? (
        <AvatarImage
          src={avatarUrl}
          alt={`Foto de ${name}`}
          className={cn('object-cover', imageClassName)}
          referrerPolicy="no-referrer"
        />
      ) : null}
      <AvatarFallback className={cn('bg-[#031121] text-2xs font-semibold text-primary', fallbackClassName)}>
        {initials}
      </AvatarFallback>
    </Avatar>
  );
}
