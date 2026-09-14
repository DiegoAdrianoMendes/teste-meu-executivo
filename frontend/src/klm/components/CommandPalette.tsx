import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { BookOpen, BriefcaseBusiness, FileText, Landmark, LayoutGrid, ListTodo, MessagesSquare, Scale } from 'lucide-react';

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from '@/components/ui/command';
import { knowledgeArticles } from '@/content/knowledge';
import { klmApi, type KlmFunnelCard, type KlmThread } from '@/klm/api';
import type { KlmNavItem, KlmWorkspaceMode } from '@/klm/navigation';
import { listCollaborativeTasks } from '@/services/tasks';

export type CommandPaletteProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  navigationItems: KlmNavItem[];
  funnelCards: KlmFunnelCard[];
  threads: KlmThread[];
  contextualPath: (path: string) => string;
  workspaceMode?: KlmWorkspaceMode;
  legalSearchPermissions?: {
    tasks: boolean;
    processes: boolean;
    demands: boolean;
    documents: boolean;
  };
};

type LegalCommandSearchGroupsProps = {
  open: boolean;
  query: string;
  permissions: NonNullable<CommandPaletteProps['legalSearchPermissions']>;
  go: (path: string) => void;
};

/**
 * Mantém as consultas do Jurídico fora da árvore preservada. Além de evitar
 * requests nos modos Geral/Comercial, isso garante que consumidores antigos
 * da CommandPalette não passem a depender de React Query.
 */
function LegalCommandSearchGroups({ open, query, permissions, go }: LegalCommandSearchGroupsProps) {
  const legalQuery = query.trim();
  const enabled = open && legalQuery.length >= 2;
  const tasksQuery = useQuery({
    queryKey: ['legal-global-search', 'tasks', legalQuery],
    queryFn: () => listCollaborativeTasks({ view: 'mine', search: legalQuery, limit: 8 }),
    enabled: enabled && permissions.tasks,
    staleTime: 30_000,
    retry: false,
  });
  const documentsQuery = useQuery({
    queryKey: ['legal-global-search', 'documents', legalQuery],
    queryFn: () => klmApi.listLegalDocuments({ q: legalQuery, limit: 8 }),
    enabled: enabled && permissions.documents,
    staleTime: 30_000,
    retry: false,
  });
  const processesQuery = useQuery({
    queryKey: ['legal-global-search', 'processes'],
    queryFn: () => klmApi.listMonitoredProcesses(),
    enabled: enabled && permissions.processes,
    staleTime: 30_000,
    retry: false,
  });
  const demandsQuery = useQuery({
    queryKey: ['legal-global-search', 'demands'],
    queryFn: () => klmApi.listLegalDemands({ limit: 100, offset: 0 }),
    enabled: enabled && permissions.demands,
    staleTime: 30_000,
    retry: false,
  });

  const normalizedQuery = legalQuery.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('pt-BR');
  const normalizedDigits = legalQuery.replace(/\D/g, '');
  const matchingProcesses = (processesQuery.data ?? []).filter((process) => (
    `${process.numeroCnj} ${process.tribunal ?? ''}`
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLocaleLowerCase('pt-BR')
      .includes(normalizedQuery)
      || (normalizedDigits.length >= 4 && process.numeroCnj.replace(/\D/g, '').includes(normalizedDigits))
  )).slice(0, 8);
  const matchingDemands = (demandsQuery.data?.items ?? []).filter((demand) => (
    `${demand.externalRef ?? ''} ${demand.nature} ${demand.area ?? ''} ${demand.subarea ?? ''}`
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLocaleLowerCase('pt-BR')
      .includes(normalizedQuery)
  )).slice(0, 8);
  const isLoading = [tasksQuery, documentsQuery, processesQuery, demandsQuery]
    .some((result) => result.isFetching);

  return (
    <>
      {(tasksQuery.data?.items.length ?? 0) > 0 && (
        <>
          <CommandSeparator />
          <CommandGroup heading="Tarefas jurídicas">
            {tasksQuery.data!.items.map((task) => (
              <CommandItem
                key={task.id}
                value={`tarefa ${task.title} ${task.description ?? ''} ${task.clientLabel ?? ''} ${task.matterLabel ?? ''}`}
                onSelect={() => go(`/app/tarefas?q=${encodeURIComponent(task.title)}`)}
              >
                <ListTodo className="mr-2 h-4 w-4 text-primary" />
                <span className="min-w-0 flex-1 truncate">{task.title}</span>
                <span className="ml-2 shrink-0 text-2xs text-muted-foreground">{task.matterLabel ?? task.clientLabel ?? 'Caso 360'}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        </>
      )}

      {matchingProcesses.length > 0 && (
        <>
          <CommandSeparator />
          <CommandGroup heading="Processos">
            {matchingProcesses.map((process) => (
              <CommandItem
                key={process.id}
                value={`processo ${process.numeroCnj} ${process.tribunal ?? ''}`}
                onSelect={() => go('/app/processos')}
              >
                <Landmark className="mr-2 h-4 w-4 text-primary" />
                <span className="min-w-0 flex-1 truncate">{process.numeroCnj}</span>
                <span className="ml-2 shrink-0 text-2xs text-muted-foreground">{process.tribunal ?? 'Tribunal não informado'}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        </>
      )}

      {matchingDemands.length > 0 && (
        <>
          <CommandSeparator />
          <CommandGroup heading="Demandas jurídicas">
            {matchingDemands.map((demand) => (
              <CommandItem
                key={demand.id}
                value={`demanda caso ${demand.externalRef ?? ''} ${demand.nature} ${demand.area ?? ''} ${demand.subarea ?? ''}`}
                onSelect={() => go(`/app/demandas/${encodeURIComponent(demand.id)}`)}
              >
                <Scale className="mr-2 h-4 w-4 text-primary" />
                <span className="min-w-0 flex-1 truncate">{demand.externalRef || demand.area || demand.nature.replaceAll('_', ' ')}</span>
                <span className="ml-2 shrink-0 text-2xs text-muted-foreground">{demand.phase.replaceAll('_', ' ')}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        </>
      )}

      {(documentsQuery.data?.length ?? 0) > 0 && (
        <>
          <CommandSeparator />
          <CommandGroup heading="Documentos jurídicos">
            {documentsQuery.data!.map((document) => (
              <CommandItem
                key={document.id}
                value={`documento ${document.originalFilename} ${document.documentType}`}
                onSelect={() => go(document.matterId ? `/app/leads?matter=${encodeURIComponent(document.matterId)}` : '/app/demandas')}
              >
                <FileText className="mr-2 h-4 w-4 text-primary" />
                <span className="min-w-0 flex-1 truncate">{document.originalFilename}</span>
                <span className="ml-2 shrink-0 text-2xs text-muted-foreground">{document.documentType.replaceAll('_', ' ')}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        </>
      )}

      {enabled && [tasksQuery, documentsQuery, processesQuery, demandsQuery].some((result) => result.isError) && (
        <CommandGroup heading="Disponibilidade">
          <CommandItem disabled value="fonte juridica indisponivel">
            Uma fonte jurídica está temporariamente indisponível; os demais resultados permanecem válidos.
          </CommandItem>
        </CommandGroup>
      )}
      {enabled && isLoading && (
        <CommandGroup heading="Pesquisa jurídica">
          <CommandItem disabled value="carregando pesquisa juridica">
            Carregando fontes autorizadas…
          </CommandItem>
        </CommandGroup>
      )}
    </>
  );
}

/**
 * Cmd+K / Ctrl+K: busca unificada de páginas (respeitando permissões, via
 * `navigationItems` já filtrado), artigos da Central de Conhecimento, e
 * leads/casos + conversas. No modo Jurídico, acrescenta consultas tipadas às
 * fontes tenant já existentes; os modos preservados continuam estritamente
 * com os dados que o shell já carregava.
 */
export function CommandPalette({
  open,
  onOpenChange,
  navigationItems,
  funnelCards,
  threads,
  contextualPath,
  workspaceMode = 'commercial',
  legalSearchPermissions = { tasks: false, processes: false, demands: false, documents: false },
}: CommandPaletteProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');

  const limitedCards = useMemo(() => funnelCards.slice(0, 40), [funnelCards]);
  const limitedThreads = useMemo(() => threads.slice(0, 40), [threads]);

  function go(path: string) {
    onOpenChange(false);
    setQuery('');
    navigate(path);
  }

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) setQuery('');
    onOpenChange(nextOpen);
  }

  return (
    <CommandDialog open={open} onOpenChange={handleOpenChange}>
      <CommandInput
        placeholder={workspaceMode === 'legal'
          ? 'Pesquisar cliente, caso, processo, tarefa ou documento…'
          : 'Buscar página, artigo de ajuda, lead/caso ou conversa…'}
        aria-label={workspaceMode === 'legal' ? 'Pesquisa jurídica global' : 'Busca rápida (Cmd+K)'}
        value={query}
        onValueChange={setQuery}
      />
      <CommandList>
        <CommandEmpty>Nada encontrado. Tente outro termo.</CommandEmpty>

        <CommandGroup heading="Páginas">
          {navigationItems.map((item) => (
            <CommandItem
              key={item.path}
              value={`pagina ${item.label}`}
              onSelect={() => go(contextualPath(item.path))}
            >
              <LayoutGrid className="mr-2 h-4 w-4 text-primary" />
              {item.label}
            </CommandItem>
          ))}
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading="Central de Conhecimento">
          {knowledgeArticles.map((article) => (
            <CommandItem
              key={article.slug}
              value={`ajuda conhecimento ${article.titulo} ${article.resumo}`}
              onSelect={() => go(`/app/conhecimento?artigo=${article.slug}`)}
            >
              <BookOpen className="mr-2 h-4 w-4 text-primary" />
              <span className="min-w-0 flex-1 truncate">{article.titulo}</span>
            </CommandItem>
          ))}
        </CommandGroup>

        {limitedCards.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading={workspaceMode === 'legal' ? 'Clientes e casos' : 'CRM'}>
              {limitedCards.map((card) => (
                <CommandItem
                  key={card.id}
                  value={`lead caso ${card.name} ${card.subject} ${card.object} ${card.source}`}
                  onSelect={() => go(`/app/leads?matter=${encodeURIComponent(card.id)}`)}
                >
                  <BriefcaseBusiness className="mr-2 h-4 w-4 text-primary" />
                  <span className="min-w-0 flex-1 truncate">{card.name}</span>
                  <span className="ml-2 shrink-0 text-2xs text-muted-foreground">{card.object}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}

        {limitedThreads.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Conversas">
              {limitedThreads.map((thread) => (
                <CommandItem
                  key={thread.id}
                  value={`conversa ${thread.subject ?? ''} ${thread.objectName ?? ''} ${thread.lastMessagePreview ?? ''}`}
                  onSelect={() => go(`/app/conversas?thread=${encodeURIComponent(thread.id)}`)}
                >
                  <MessagesSquare className="mr-2 h-4 w-4 text-primary" />
                  <span className="min-w-0 flex-1 truncate">{thread.subject || thread.objectName || 'Conversa sem assunto'}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}

        {workspaceMode === 'legal' && (
          <LegalCommandSearchGroups open={open} query={query} permissions={legalSearchPermissions} go={go} />
        )}
      </CommandList>
    </CommandDialog>
  );
}
