import { useState } from 'react';
import { Send, Paperclip, Mic, Smile, Loader2, WandSparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/use-toast';
import { Badge } from '@/components/ui/badge';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { QuickReplies } from './QuickReplies';
import type { MessageOptimizationResult, MessageTemplate } from '@/types';

const QUICK_EMOJIS = ['🙂', '👍', '🙏', '✅', '📎', '📅', '⚖️', '💬'];

interface MessageInputProps {
  onSend: (text: string) => void | Promise<void>;
  disabled?: boolean;
  helperText?: string | null;
  templates?: MessageTemplate[];
  onOpenMedia?: () => void;
  onOptimize?: (body: string, tone: string) => Promise<MessageOptimizationResult>;
}

export function MessageInput({
  onSend,
  disabled = false,
  helperText,
  templates = [],
  onOpenMedia,
  onOptimize,
}: MessageInputProps) {
  const [text, setText] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [tone, setTone] = useState('clear');
  const [optimizing, setOptimizing] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [emojiOpen, setEmojiOpen] = useState(false);

  const handleSend = async () => {
    if (!text.trim() || disabled || isSending) return;
    const nextText = text.trim();
    setIsSending(true);
    try {
      await onSend(nextText);
      setText('');
    } catch (error) {
      toast({
        variant: 'destructive',
        title: 'Mensagem não enviada',
        description: error instanceof Error ? error.message : 'Não foi possível enviar a mensagem agora.',
      });
    } finally {
      setIsSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  };

  const handleOptimize = async () => {
    if (!onOptimize || !text.trim() || optimizing) return;
    setOptimizing(true);
    try {
      const result = await onOptimize(text.trim(), tone);
      setSuggestions(result.suggestions);
    } catch (error) {
      toast({
        variant: 'destructive',
        title: 'Não foi possível otimizar a mensagem',
        description: error instanceof Error ? error.message : 'Tente novamente em instantes.',
      });
    } finally {
      setOptimizing(false);
    }
  };

  return (
    <div className="border-t bg-card">
      {helperText && (
        <div className="border-b bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          {helperText}
        </div>
      )}
      {suggestions.length > 0 && (
        <div className="border-b bg-background px-3 py-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="min-w-0 truncate text-xs font-medium text-muted-foreground">Sugestões da IA para esta mensagem</p>
            <Button variant="ghost" size="sm" className="h-7 shrink-0 px-2 text-xs" onClick={() => setSuggestions([])}>
              Fechar
            </Button>
          </div>
          <div className="space-y-2">
            {suggestions.map((suggestion, index) => (
              <button
                key={`${index}-${suggestion.slice(0, 20)}`}
                type="button"
                onClick={() => {
                  setText(suggestion);
                  setSuggestions([]);
                }}
                className="w-full rounded-xl border bg-muted/20 p-3 text-left text-sm transition-colors hover:bg-muted/40"
              >
                <div className="mb-2 flex items-center gap-2">
                  <Badge variant="outline" className="shrink-0 tabular-nums">Sugestão {index + 1}</Badge>
                  <span className="min-w-0 truncate text-xs text-muted-foreground">Clique para usar no composer</span>
                </div>
                <p className="whitespace-pre-wrap text-sm text-foreground/85">{suggestion}</p>
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="flex items-end gap-2 p-3">
      <QuickReplies templates={templates} onSelect={t => setText(t)} />
      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8 text-muted-foreground shrink-0"
        disabled={disabled || isSending || !onOpenMedia}
        onClick={onOpenMedia}
        aria-label={onOpenMedia ? 'Anexar arquivo' : 'Anexos indisponíveis neste canal'}
        title={onOpenMedia ? 'Anexar arquivo' : 'Anexos indisponíveis neste canal'}
      >
        <Paperclip className="h-4 w-4" />
      </Button>
      <div className="flex-1 relative">
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          aria-label="Mensagem"
          placeholder={disabled ? 'Conecte um número oficial do WhatsApp para responder.' : 'Digite uma mensagem...'}
          rows={1}
          disabled={disabled || isSending}
          className="w-full resize-none rounded-xl border bg-muted/50 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring max-h-24 scrollbar-thin disabled:cursor-not-allowed disabled:opacity-60"
          style={{ minHeight: '36px' }}
        />
      </div>
      <div className="hidden items-center gap-2 md:flex">
        <Select value={tone} onValueChange={setTone}>
          <SelectTrigger className="h-8 w-[132px] text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="clear">Mais clara</SelectItem>
            <SelectItem value="short">Mais curta</SelectItem>
            <SelectItem value="commercial">Mais comercial</SelectItem>
            <SelectItem value="warm">Mais acolhedora</SelectItem>
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          size="sm"
          className="h-8 shrink-0"
          onClick={() => { void handleOptimize(); }}
          disabled={disabled || isSending || optimizing || !text.trim() || !onOptimize}
          title={onOptimize ? 'Otimizar mensagem' : 'Otimização indisponível neste canal'}
        >
          {optimizing ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <WandSparkles className="mr-1.5 h-4 w-4" />}
          Otimizar
        </Button>
      </div>
      <Popover open={emojiOpen} onOpenChange={setEmojiOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 text-muted-foreground shrink-0"
            disabled={disabled || isSending}
            aria-label="Inserir emoji"
            title="Inserir emoji"
          >
            <Smile className="h-4 w-4" />
          </Button>
        </PopoverTrigger>
        <PopoverContent side="top" align="end" className="w-auto p-2">
          <div className="grid grid-cols-4 gap-1" role="group" aria-label="Emojis rápidos">
            {QUICK_EMOJIS.map((emoji) => (
              <Button
                key={emoji}
                type="button"
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-base"
                onClick={() => {
                  setText((current) => `${current}${current && !current.endsWith(' ') ? ' ' : ''}${emoji}`);
                  setEmojiOpen(false);
                }}
                aria-label={`Inserir ${emoji}`}
              >
                {emoji}
              </Button>
            ))}
          </div>
        </PopoverContent>
      </Popover>
      {text.trim() ? (
        <Button size="icon" className="h-8 w-8 shrink-0 rounded-full" onClick={() => { void handleSend(); }} disabled={disabled || isSending}>
          {isSending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </Button>
      ) : (
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-muted-foreground shrink-0"
          disabled
          aria-label="Gravação de voz indisponível neste canal"
          title="Gravação de voz indisponível neste canal"
        >
          <Mic className="h-4 w-4" />
        </Button>
      )}
      </div>
    </div>
  );
}
