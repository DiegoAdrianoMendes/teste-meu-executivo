import { Fragment, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, Lightbulb } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * Renderer de markdown minimalista, sem dependência nova, feito sob medida para os
 * artigos da Aba Conhecimento. Suporta apenas o subconjunto usado nos artigos:
 * `## `/`### ` (títulos), listas `- ` e `1. `, blocos de citação `> `
 * (com os prefixos ATENÇÃO:/DICA: virando callouts), **negrito** e o link interno
 * `[[rota:/caminho|Rótulo]]`.
 */

export type KnowledgeHeading = { id: string; texto: string; nivel: 2 | 3 };

function slugify(value: string): string {
  return value
    .toLowerCase()
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-|-$)/g, '');
}

/** Extrai os `##` do corpo para montar o sumário lateral, sem renderizar nada. */
export function extractHeadings(corpo: string): KnowledgeHeading[] {
  const headings: KnowledgeHeading[] = [];
  const seen = new Map<string, number>();
  for (const rawLine of corpo.split('\n')) {
    const line = rawLine.trim();
    const match = /^(#{2,3})\s+(.*)$/.exec(line);
    if (!match) continue;
    const nivel = match[1].length === 2 ? 2 : 3;
    const texto = match[2].trim();
    let id = slugify(texto);
    const count = seen.get(id) ?? 0;
    seen.set(id, count + 1);
    if (count > 0) id = `${id}-${count}`;
    headings.push({ id, texto, nivel });
  }
  return headings;
}

/** Renderiza inline: **negrito** e `[[rota:/caminho|Rótulo]]`. */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /\[\[rota:([^|\]]+)\|([^\]]+)\]\]|\*\*([^*]+)\*\*/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let index = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(<Fragment key={`${keyPrefix}-t-${index++}`}>{text.slice(lastIndex, match.index)}</Fragment>);
    }
    if (match[1] !== undefined) {
      nodes.push(
        <Link
          key={`${keyPrefix}-link-${index++}`}
          to={match[1]}
          className="font-medium text-primary underline underline-offset-2 hover:text-primary/80"
        >
          {match[2]}
        </Link>,
      );
    } else if (match[3] !== undefined) {
      nodes.push(
        <strong key={`${keyPrefix}-b-${index++}`} className="font-semibold text-foreground">
          {match[3]}
        </strong>,
      );
    }
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    nodes.push(<Fragment key={`${keyPrefix}-t-${index}`}>{text.slice(lastIndex)}</Fragment>);
  }
  return nodes;
}

type Block =
  | { type: 'heading'; nivel: 2 | 3; texto: string; id: string }
  | { type: 'paragraph'; texto: string }
  | { type: 'ul'; itens: string[] }
  | { type: 'ol'; itens: string[] }
  | { type: 'callout'; tom: 'atencao' | 'dica' | 'nota'; linhas: string[] };

function parseBlocks(corpo: string): Block[] {
  const lines = corpo.replace(/\r\n/g, '\n').split('\n');
  const blocks: Block[] = [];
  const seen = new Map<string, number>();
  let i = 0;

  const nextHeadingId = (texto: string) => {
    let id = slugify(texto);
    const count = seen.get(id) ?? 0;
    seen.set(id, count + 1);
    if (count > 0) id = `${id}-${count}`;
    return id;
  };

  while (i < lines.length) {
    const line = lines[i].trim();

    if (!line) {
      i += 1;
      continue;
    }

    const headingMatch = /^(#{2,3})\s+(.*)$/.exec(line);
    if (headingMatch) {
      const nivel = headingMatch[1].length === 2 ? 2 : 3;
      const texto = headingMatch[2].trim();
      blocks.push({ type: 'heading', nivel, texto, id: nextHeadingId(texto) });
      i += 1;
      continue;
    }

    if (line.startsWith('> ')) {
      const linhas: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith('> ')) {
        linhas.push(lines[i].trim().slice(2).trim());
        i += 1;
      }
      const primeira = linhas[0] ?? '';
      let tom: 'atencao' | 'dica' | 'nota' = 'nota';
      let conteudo = linhas;
      if (/^aten[cç][aã]o:/i.test(primeira)) {
        tom = 'atencao';
        conteudo = [primeira.replace(/^aten[cç][aã]o:\s*/i, ''), ...linhas.slice(1)];
      } else if (/^dica:/i.test(primeira)) {
        tom = 'dica';
        conteudo = [primeira.replace(/^dica:\s*/i, ''), ...linhas.slice(1)];
      }
      blocks.push({ type: 'callout', tom, linhas: conteudo });
      continue;
    }

    if (/^[-*]\s+/.test(line)) {
      const itens: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        itens.push(lines[i].trim().replace(/^[-*]\s+/, ''));
        i += 1;
      }
      blocks.push({ type: 'ul', itens });
      continue;
    }

    if (/^\d+[.)]\s+/.test(line)) {
      const itens: string[] = [];
      while (i < lines.length && /^\d+[.)]\s+/.test(lines[i].trim())) {
        itens.push(lines[i].trim().replace(/^\d+[.)]\s+/, ''));
        i += 1;
      }
      blocks.push({ type: 'ol', itens });
      continue;
    }

    const paragrafo: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{2,3})\s+/.test(lines[i].trim()) && !lines[i].trim().startsWith('> ') && !/^[-*]\s+/.test(lines[i].trim()) && !/^\d+[.)]\s+/.test(lines[i].trim())) {
      paragrafo.push(lines[i].trim());
      i += 1;
    }
    blocks.push({ type: 'paragraph', texto: paragrafo.join(' ') });
  }

  return blocks;
}

const CALLOUT_STYLE: Record<'atencao' | 'dica' | 'nota', { className: string; icon: typeof AlertTriangle; label: string }> = {
  atencao: { className: 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200', icon: AlertTriangle, label: 'Atenção' },
  dica: { className: 'border-sky-300 bg-sky-50 text-sky-900 dark:border-sky-900 dark:bg-sky-950/30 dark:text-sky-200', icon: Lightbulb, label: 'Dica' },
  nota: { className: 'border-border bg-secondary text-secondary-foreground', icon: Lightbulb, label: 'Nota' },
};

export function KnowledgeMarkdown({ corpo }: { corpo: string }) {
  const blocks = parseBlocks(corpo);
  return (
    <div className="space-y-4 text-[13px] leading-6 text-foreground">
      {blocks.map((block, blockIndex) => {
        const key = `block-${blockIndex}`;
        if (block.type === 'heading') {
          const Tag = block.nivel === 2 ? 'h2' : 'h3';
          return (
            <Tag
              key={key}
              id={block.id}
              className={cn('scroll-mt-24 font-semibold text-foreground', block.nivel === 2 ? 'mt-8 text-[18px]' : 'mt-6 text-[15px]')}
            >
              {renderInline(block.texto, key)}
            </Tag>
          );
        }
        if (block.type === 'paragraph') {
          return (
            <p key={key} className="text-muted-foreground">
              {renderInline(block.texto, key)}
            </p>
          );
        }
        if (block.type === 'ul') {
          return (
            <ul key={key} className="list-disc space-y-1.5 pl-5 text-muted-foreground">
              {block.itens.map((item, itemIndex) => (
                <li key={`${key}-${itemIndex}`}>{renderInline(item, `${key}-${itemIndex}`)}</li>
              ))}
            </ul>
          );
        }
        if (block.type === 'ol') {
          return (
            <ol key={key} className="list-decimal space-y-1.5 pl-5 text-muted-foreground">
              {block.itens.map((item, itemIndex) => (
                <li key={`${key}-${itemIndex}`}>{renderInline(item, `${key}-${itemIndex}`)}</li>
              ))}
            </ol>
          );
        }
        const style = CALLOUT_STYLE[block.tom];
        const Icon = style.icon;
        return (
          <div key={key} className={cn('flex gap-3 rounded-lg border p-3.5', style.className)}>
            <Icon className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="space-y-1">
              <p className="text-[11px] font-semibold uppercase tracking-wide">{style.label}</p>
              {block.linhas.map((linha, linhaIndex) => (
                <p key={`${key}-linha-${linhaIndex}`} className="leading-5">
                  {renderInline(linha, `${key}-linha-${linhaIndex}`)}
                </p>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
