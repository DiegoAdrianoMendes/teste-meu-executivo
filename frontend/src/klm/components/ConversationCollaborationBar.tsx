import { useState } from 'react';
import {
  AtSign,
  Building2,
  Check,
  LockKeyhole,
  LoaderCircle,
  MessageCircleReply,
  Quote,
  UserRound,
  UserRoundPlus,
  UsersRound,
  X,
} from 'lucide-react';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';

export type ConversationCollaborationKind = 'individual' | 'group';

export interface ConversationMessageReference {
  id: string;
  authorLabel: string;
  body: string;
  occurredAtLabel?: string;
}

export interface ConversationParticipant {
  id: string;
  name: string;
  handle?: string;
  roleLabel?: string;
  avatarUrl?: string;
  initials?: string;
  presence?: 'online' | 'away' | 'offline';
}

export interface ConversationMentionTarget {
  id: string;
  kind: 'user' | 'team';
  name: string;
  handle?: string;
  detail?: string;
  avatarUrl?: string;
  initials?: string;
  memberCount?: number;
}

export interface ConversationCollaborationCapabilities {
  reply: boolean;
  mentionUsers: boolean;
  mentionTeams: boolean;
  viewParticipants: boolean;
}

export interface ConversationProviderGroupManagementStatus {
  available: boolean;
  message: string;
}

export type ConversationCollaborationCapability = keyof ConversationCollaborationCapabilities;

export interface ConversationCollaborationBarProps {
  conversationKind: ConversationCollaborationKind;
  conversationLabel?: string;
  reference?: ConversationMessageReference | null;
  participants?: readonly ConversationParticipant[];
  participantCount?: number;
  participantTargets?: readonly ConversationMentionTarget[];
  canManageParticipants?: boolean;
  participantMutationKey?: string | null;
  providerGroupManagement?: ConversationProviderGroupManagementStatus;
  mentionTargets?: readonly ConversationMentionTarget[];
  selectedMentions?: readonly ConversationMentionTarget[];
  capabilities: ConversationCollaborationCapabilities;
  capabilityMessages?: Partial<Record<ConversationCollaborationCapability, string>>;
  className?: string;
  onReply: (reference: ConversationMessageReference) => void;
  onClearReference: () => void;
  onParticipantAdd?: (target: ConversationMentionTarget) => void;
  onMentionSelect: (target: ConversationMentionTarget) => void;
  onMentionRemove?: (target: ConversationMentionTarget) => void;
}

const defaultCapabilityMessages: Record<ConversationCollaborationCapability, string> = {
  reply: 'Seu perfil não pode responder a esta mensagem.',
  mentionUsers: 'Seu perfil não pode mencionar usuários.',
  mentionTeams: 'Seu perfil não pode mencionar equipes.',
  viewParticipants: 'Seu perfil não pode consultar os participantes.',
};

function entityInitials(name: string, provided?: string) {
  if (provided?.trim()) return provided.trim().slice(0, 2).toUpperCase();
  const generated = name
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();
  return generated || '?';
}

function mentionToken(target: ConversationMentionTarget) {
  return `@${(target.handle || target.name).trim().replace(/^@/, '')}`;
}

function participantLabel(count: number) {
  return `${count} ${count === 1 ? 'participante' : 'participantes'}`;
}

function EntityAvatar({
  name,
  avatarUrl,
  initials,
  team = false,
}: {
  name: string;
  avatarUrl?: string;
  initials?: string;
  team?: boolean;
}) {
  return (
    <Avatar className="h-7 w-7 border border-border bg-card">
      {avatarUrl ? <AvatarImage src={avatarUrl} alt="" /> : null}
      <AvatarFallback className={cn('text-2xs font-semibold', team ? 'bg-[#031121] text-white' : 'bg-primary/15 text-[#6f4a05] dark:text-primary')}>
        {team ? <Building2 className="h-3.5 w-3.5" /> : entityInitials(name, initials)}
      </AvatarFallback>
    </Avatar>
  );
}

export function ConversationCollaborationBar({
  conversationKind,
  conversationLabel,
  reference,
  participants = [],
  participantCount,
  participantTargets = [],
  canManageParticipants = false,
  participantMutationKey,
  providerGroupManagement,
  mentionTargets = [],
  selectedMentions = [],
  capabilities,
  capabilityMessages,
  className,
  onReply,
  onClearReference,
  onParticipantAdd,
  onMentionSelect,
  onMentionRemove,
}: ConversationCollaborationBarProps) {
  const [mentionOpen, setMentionOpen] = useState(false);
  const [participantsOpen, setParticipantsOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState('');
  const [participantQuery, setParticipantQuery] = useState('');

  const totalParticipants = Math.max(participantCount ?? participants.length, participants.length);
  const conversationTypeLabel = conversationKind === 'group' ? 'Grupo' : 'Individual';
  const canMention = capabilities.mentionUsers || capabilities.mentionTeams;
  const users = mentionTargets.filter((target) => target.kind === 'user' && capabilities.mentionUsers);
  const teams = mentionTargets.filter((target) => target.kind === 'team' && capabilities.mentionTeams);
  const participantUsers = participantTargets.filter((target) => target.kind === 'user');
  const participantTeams = participantTargets.filter((target) => target.kind === 'team');
  const selectedMentionKeys = new Set(selectedMentions.map((target) => `${target.kind}:${target.id}`));
  const capabilityMessage = (capability: ConversationCollaborationCapability) => (
    capabilityMessages?.[capability] || defaultCapabilityMessages[capability]
  );

  const handleMentionOpenChange = (open: boolean) => {
    if (!canMention) return;
    setMentionOpen(open);
    if (!open) setMentionQuery('');
  };

  const selectMention = (target: ConversationMentionTarget) => {
    onMentionSelect(target);
    setMentionOpen(false);
    setMentionQuery('');
  };

  const addParticipant = (target: ConversationMentionTarget) => {
    if (!canManageParticipants || !onParticipantAdd || participantMutationKey) return;
    onParticipantAdd(target);
    setParticipantQuery('');
  };

  const conversationAriaLabel = capabilities.viewParticipants
    ? `Conversa ${conversationKind === 'group' ? 'em grupo' : 'individual'}, ${participantLabel(totalParticipants)}`
    : `Conversa ${conversationKind === 'group' ? 'em grupo' : 'individual'}; participantes restritos`;

  return (
    <section
      aria-label="Colaboração da conversa"
      className={cn('min-w-0 border-y border-border bg-card px-2 py-2 sm:px-3', className)}
    >
      {reference ? (
        <div
          role="region"
          aria-label="Mensagem selecionada como referência"
          data-testid="conversation-message-reference"
          className="mb-2 grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto_auto] items-center gap-2 rounded-md border border-border bg-secondary/70 px-2 py-1.5"
        >
          <Quote className="h-3.5 w-3.5 shrink-0 text-[#0057d8]" />
          <div className="min-w-0">
            <div className="flex min-w-0 items-baseline gap-2 text-2xs">
              <strong className="truncate font-semibold">{reference.authorLabel}</strong>
              {reference.occurredAtLabel ? <span className="shrink-0 text-2xs text-muted-foreground">{reference.occurredAtLabel}</span> : null}
            </div>
            <p className="truncate text-2xs leading-4 text-muted-foreground" title={reference.body}>{reference.body}</p>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 shrink-0 px-2 text-2xs"
            disabled={!capabilities.reply}
            aria-label="Responder"
            title={capabilities.reply ? 'Responder usando esta mensagem como referência' : capabilityMessage('reply')}
            onClick={() => onReply(reference)}
          >
            <MessageCircleReply className="h-3 w-3" />
            Responder
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0 text-muted-foreground"
            aria-label="Cancelar referência"
            title="Cancelar referência"
            onClick={onClearReference}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      ) : null}

      <div className="flex min-w-0 items-center gap-1.5 overflow-x-auto pb-0.5">
        <Popover
          open={participantsOpen && capabilities.viewParticipants}
          onOpenChange={(open) => {
            if (capabilities.viewParticipants) {
              setParticipantsOpen(open);
              if (!open) setParticipantQuery('');
            }
          }}
        >
          <PopoverTrigger asChild>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 shrink-0 gap-1.5 px-2 text-2xs"
              disabled={!capabilities.viewParticipants}
              aria-label={conversationAriaLabel}
              title={capabilities.viewParticipants ? `Ver ${participantLabel(totalParticipants)}` : capabilityMessage('viewParticipants')}
            >
              {conversationKind === 'group' ? <UsersRound className="h-3.5 w-3.5" /> : <UserRound className="h-3.5 w-3.5" />}
              <span>{conversationTypeLabel}</span>
              {capabilities.viewParticipants ? (
                <span className="rounded bg-secondary px-1.5 py-0.5 text-2xs text-muted-foreground">{totalParticipants}</span>
              ) : (
                <LockKeyhole className="h-3 w-3 text-muted-foreground" />
              )}
            </Button>
          </PopoverTrigger>
          <PopoverContent
            role="dialog"
            aria-label="Participantes da conversa"
            align="start"
            side="top"
            sideOffset={6}
            className="max-h-[calc(100vh-1rem)] w-[min(20rem,calc(100vw-1rem))] overflow-y-auto p-0"
          >
            <div className="flex min-w-0 items-center gap-2 border-b px-3 py-2">
              {conversationKind === 'group' ? <UsersRound className="h-3.5 w-3.5 text-[#0057d8]" /> : <UserRound className="h-3.5 w-3.5 text-[#0057d8]" />}
              <div className="min-w-0 flex-1">
                <p className="truncate text-2xs font-semibold">{conversationLabel || `Conversa ${conversationTypeLabel.toLocaleLowerCase('pt-BR')}`}</p>
                <p className="text-2xs text-muted-foreground">{participantLabel(totalParticipants)}</p>
              </div>
            </div>
            <ul className="max-h-64 divide-y overflow-y-auto" aria-label="Lista de participantes">
              {participants.map((participant) => (
                <li key={`${participant.id}:${participant.roleLabel ?? ''}:${participant.name}`} className="flex min-w-0 items-center gap-2 px-3 py-2">
                  <div className="relative shrink-0">
                    <EntityAvatar name={participant.name} avatarUrl={participant.avatarUrl} initials={participant.initials} />
                    {participant.presence ? (
                      <span
                        className={cn(
                          'absolute bottom-0 right-0 h-2 w-2 rounded-full border border-card',
                          participant.presence === 'online' && 'bg-emerald-500',
                          participant.presence === 'away' && 'bg-amber-500',
                          participant.presence === 'offline' && 'bg-muted-foreground/50',
                        )}
                        aria-label={participant.presence === 'online' ? 'online' : participant.presence === 'away' ? 'ausente' : 'offline'}
                      />
                    ) : null}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-2xs font-semibold">{participant.name}</p>
                    <p className="truncate text-2xs text-muted-foreground">
                      {[participant.handle ? `@${participant.handle.replace(/^@/, '')}` : null, participant.roleLabel].filter(Boolean).join(' · ') || 'Participante'}
                    </p>
                  </div>
                </li>
              ))}
              {!participants.length ? <li className="px-3 py-6 text-center text-2xs text-muted-foreground">Nenhum participante informado.</li> : null}
            </ul>
            {totalParticipants > participants.length ? (
              <p className="border-t px-3 py-2 text-2xs text-muted-foreground">
                +{totalParticipants - participants.length} {totalParticipants - participants.length === 1 ? 'participante não listado' : 'participantes não listados'}
              </p>
            ) : null}
            {canManageParticipants && onParticipantAdd ? (
              <div className="border-t" aria-label="Adicionar participante interno">
                <div className="flex items-start gap-2 px-3 py-2">
                  <UserRoundPlus className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0057d8]" />
                  <div className="min-w-0">
                    <p className="text-2xs font-semibold">Participação interna Meu Executivo</p>
                    <p className="mt-0.5 text-2xs leading-3 text-muted-foreground">
                      Adicione um usuário ou equipe em um clique. Isso não altera o grupo oficial do provedor.
                    </p>
                  </div>
                </div>
                <Command label="Adicionar usuário ou equipe à participação interna" className="border-t">
                  <CommandInput
                    value={participantQuery}
                    onValueChange={setParticipantQuery}
                    aria-label="Buscar participante interno"
                    placeholder="Buscar usuário ou equipe..."
                    className="h-9 text-2xs"
                  />
                  <CommandList className="max-h-52">
                    <CommandEmpty className="py-5 text-2xs text-muted-foreground">Nenhum usuário ou equipe disponível.</CommandEmpty>
                    {participantUsers.length ? (
                      <CommandGroup heading="Usuários disponíveis" className="[&_[cmdk-group-heading]]:text-2xs">
                        {participantUsers.map((target) => {
                          const key = `${target.kind}:${target.id}`;
                          const pending = participantMutationKey === key;
                          return (
                            <CommandItem
                              key={key}
                              value={`${target.name} ${target.handle || ''} ${target.detail || ''}`}
                              className="gap-2 py-2"
                              disabled={Boolean(participantMutationKey)}
                              onSelect={() => addParticipant(target)}
                            >
                              <EntityAvatar name={target.name} avatarUrl={target.avatarUrl} initials={target.initials} />
                              <div className="min-w-0 flex-1">
                                <p className="truncate text-2xs font-semibold">{target.name}</p>
                                <p className="truncate text-2xs text-muted-foreground">{target.handle ? mentionToken(target) : target.detail || 'Usuário do workspace'}</p>
                              </div>
                              {pending ? <LoaderCircle className="h-3.5 w-3.5 shrink-0 animate-spin" aria-label="Adicionando participante" /> : <UserRoundPlus className="h-3.5 w-3.5 shrink-0 text-[#0b7435]" />}
                            </CommandItem>
                          );
                        })}
                      </CommandGroup>
                    ) : null}
                    {participantTeams.length ? (
                      <CommandGroup heading="Equipes disponíveis" className="[&_[cmdk-group-heading]]:text-2xs">
                        {participantTeams.map((target) => {
                          const key = `${target.kind}:${target.id}`;
                          const pending = participantMutationKey === key;
                          return (
                            <CommandItem
                              key={key}
                              value={`${target.name} ${target.handle || ''} ${target.detail || ''}`}
                              className="gap-2 py-2"
                              disabled={Boolean(participantMutationKey)}
                              onSelect={() => addParticipant(target)}
                            >
                              <EntityAvatar name={target.name} avatarUrl={target.avatarUrl} initials={target.initials} team />
                              <div className="min-w-0 flex-1">
                                <p className="truncate text-2xs font-semibold">{target.name}</p>
                                <p className="truncate text-2xs text-muted-foreground">{target.memberCount != null ? participantLabel(target.memberCount) : target.detail || 'Equipe do workspace'}</p>
                              </div>
                              {pending ? <LoaderCircle className="h-3.5 w-3.5 shrink-0 animate-spin" aria-label="Adicionando participante" /> : <UserRoundPlus className="h-3.5 w-3.5 shrink-0 text-[#0b7435]" />}
                            </CommandItem>
                          );
                        })}
                      </CommandGroup>
                    ) : null}
                  </CommandList>
                </Command>
              </div>
            ) : null}
            {providerGroupManagement ? (
              <div className="border-t px-3 py-2" aria-label="Gestão do grupo oficial do provedor">
                <div className="flex items-center gap-1.5 text-2xs font-semibold">
                  {providerGroupManagement.available ? <Check className="h-3 w-3 text-[#0b7435]" /> : <LockKeyhole className="h-3 w-3 text-muted-foreground" />}
                  <span>Grupo oficial do provedor · {providerGroupManagement.available ? 'gate habilitado' : 'gate fechado'}</span>
                </div>
                <p className="mt-1 text-2xs leading-3 text-muted-foreground">{providerGroupManagement.message}</p>
              </div>
            ) : null}
          </PopoverContent>
        </Popover>

        {canManageParticipants && onParticipantAdd && capabilities.viewParticipants ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 shrink-0 px-2 text-2xs"
            aria-label="Adicionar participante interno"
            aria-haspopup="dialog"
            aria-expanded={participantsOpen}
            title="Adicionar usuário ou equipe à participação interna da plataforma"
            onClick={() => {
              setParticipantQuery('');
              setParticipantsOpen(true);
            }}
          >
            <UserRoundPlus className="h-3.5 w-3.5" />
            Participante
          </Button>
        ) : null}

        <Popover open={mentionOpen && canMention} onOpenChange={handleMentionOpenChange}>
          <PopoverTrigger asChild>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 shrink-0 px-2 text-2xs"
              disabled={!canMention}
              aria-label={canMention ? 'Mencionar usuário ou equipe' : `Menções indisponíveis. ${capabilityMessage('mentionUsers')} ${capabilityMessage('mentionTeams')}`}
              title={canMention ? 'Mencionar usuário ou equipe' : `${capabilityMessage('mentionUsers')} ${capabilityMessage('mentionTeams')}`}
            >
              <AtSign className="h-3.5 w-3.5" />
              Mencionar
            </Button>
          </PopoverTrigger>
          <PopoverContent
            role="dialog"
            aria-label="Selecionar menção"
            align="start"
            side="top"
            sideOffset={6}
            className="w-[min(22rem,calc(100vw-1rem))] p-0"
          >
            <Command label="Buscar usuários ou equipes">
              <CommandInput
                value={mentionQuery}
                onValueChange={setMentionQuery}
                aria-label="Buscar usuários ou equipes"
                placeholder="Buscar usuário ou equipe..."
                className="h-9 text-2xs"
              />
              <CommandList className="max-h-64">
                <CommandEmpty className="py-6 text-2xs text-muted-foreground">Nenhuma menção encontrada.</CommandEmpty>
                {users.length ? (
                  <CommandGroup heading="Usuários" className="[&_[cmdk-group-heading]]:text-2xs">
                    {users.map((target) => {
                      const selected = selectedMentionKeys.has(`${target.kind}:${target.id}`);
                      return (
                        <CommandItem
                          key={`${target.kind}:${target.id}`}
                          value={`${target.name} ${target.handle || ''} ${target.detail || ''}`}
                          className="gap-2 py-2"
                          disabled={selected}
                          onSelect={() => selectMention(target)}
                        >
                          <EntityAvatar name={target.name} avatarUrl={target.avatarUrl} initials={target.initials} />
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-2xs font-semibold">{target.name}</p>
                            <p className="truncate text-2xs text-muted-foreground">{mentionToken(target)}{target.detail ? ` · ${target.detail}` : ''}</p>
                          </div>
                          {selected ? <Check className="h-3.5 w-3.5 shrink-0 text-[#0b7435]" aria-label="Já mencionada" /> : null}
                        </CommandItem>
                      );
                    })}
                  </CommandGroup>
                ) : null}
                {teams.length ? (
                  <CommandGroup heading="Equipes" className="[&_[cmdk-group-heading]]:text-2xs">
                    {teams.map((target) => {
                      const selected = selectedMentionKeys.has(`${target.kind}:${target.id}`);
                      return (
                        <CommandItem
                          key={`${target.kind}:${target.id}`}
                          value={`${target.name} ${target.handle || ''} ${target.detail || ''}`}
                          className="gap-2 py-2"
                          disabled={selected}
                          onSelect={() => selectMention(target)}
                        >
                          <EntityAvatar name={target.name} avatarUrl={target.avatarUrl} initials={target.initials} team />
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-2xs font-semibold">{target.name}</p>
                            <p className="truncate text-2xs text-muted-foreground">
                              {mentionToken(target)}{target.memberCount != null ? ` · ${participantLabel(target.memberCount)}` : target.detail ? ` · ${target.detail}` : ''}
                            </p>
                          </div>
                          {selected ? <Check className="h-3.5 w-3.5 shrink-0 text-[#0b7435]" aria-label="Já mencionada" /> : null}
                        </CommandItem>
                      );
                    })}
                  </CommandGroup>
                ) : null}
              </CommandList>
            </Command>
            {!capabilities.mentionUsers || !capabilities.mentionTeams ? (
              <div className="flex items-center gap-1.5 border-t px-3 py-2 text-2xs text-muted-foreground">
                <LockKeyhole className="h-3 w-3 shrink-0" />
                <span>{!capabilities.mentionUsers ? capabilityMessage('mentionUsers') : capabilityMessage('mentionTeams')}</span>
              </div>
            ) : null}
          </PopoverContent>
        </Popover>

        {selectedMentions.length ? (
          <div className="flex min-w-0 items-center gap-1" aria-label="Menções selecionadas">
            {selectedMentions.map((target) => {
              const token = mentionToken(target);
              return onMentionRemove ? (
                <button
                  key={`${target.kind}:${target.id}`}
                  type="button"
                  className="inline-flex h-7 max-w-[10rem] shrink-0 items-center gap-1 rounded-md border border-primary/30 bg-primary/10 px-2 text-2xs font-semibold text-[#6f4a05] transition-colors hover:bg-primary/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring dark:text-primary"
                  aria-label={`Remover menção ${token}`}
                  title={`Remover ${token}`}
                  onClick={() => onMentionRemove(target)}
                >
                  <span className="truncate">{token}</span>
                  <X className="h-3 w-3 shrink-0" />
                </button>
              ) : (
                <span key={`${target.kind}:${target.id}`} className="inline-flex h-7 max-w-[10rem] shrink-0 items-center rounded-md border border-primary/30 bg-primary/10 px-2 text-2xs font-semibold text-[#6f4a05] dark:text-primary" title={token}>
                  <span className="truncate">{token}</span>
                </span>
              );
            })}
          </div>
        ) : null}
      </div>
    </section>
  );
}
