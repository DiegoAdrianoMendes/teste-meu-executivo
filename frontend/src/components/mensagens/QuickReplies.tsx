import { Zap } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { useState } from 'react';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import type { MessageTemplate } from '@/types';

interface QuickRepliesProps {
  templates: MessageTemplate[];
  onSelect: (text: string) => void;
}

export function QuickReplies({ templates, onSelect }: QuickRepliesProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const filtered = templates
    .filter((template) => ['message', 'proposal', 'contract_message'].includes(template.kind))
    .filter((template) => {
      const haystack = `${template.label} ${template.body} ${template.category}`.toLowerCase();
      return haystack.includes(search.toLowerCase());
    });

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-foreground shrink-0">
          <Zap className="h-4 w-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-0" align="start" side="top" sideOffset={8}>
        <div className="px-3 py-2 border-b">
          <span className="text-sm font-semibold">Mensagens Rápidas</span>
        </div>
        <div className="border-b px-3 py-2">
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Buscar por categoria, título ou trecho..."
            className="h-8 text-sm"
          />
        </div>
        <div className="max-h-60 overflow-y-auto">
          {filtered.map((tpl) => (
            <button
              key={tpl.id}
              className="w-full text-left px-3 py-2 hover:bg-muted/50 transition-colors border-b last:border-0"
              onClick={() => {
                onSelect(tpl.body);
                setOpen(false);
              }}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate text-sm font-medium text-foreground">{tpl.label}</span>
                <div className="flex shrink-0 items-center gap-1">
                  {tpl.favorite && <Badge variant="secondary" className="h-4 px-1 text-2xs">favorito</Badge>}
                  <Badge variant="outline" className="h-4 max-w-[96px] px-1 text-2xs"><span className="truncate">{tpl.category}</span></Badge>
                </div>
              </div>
              <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{tpl.body}</p>
            </button>
          ))}
          {filtered.length === 0 && (
            <div className="px-3 py-6 text-center text-xs text-muted-foreground">
              Nenhum template encontrado para esta busca.
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
