import { FileClock, FolderClosed, Send } from 'lucide-react';
import { KlmPanel } from '@/klm/components/Primitives';

/**
 * Slots das abas "Peças", "Documentos" e "Protocolo" do detalhe de uma
 * demanda (`DemandaDetailPage`) — nomeados e importáveis desde já para que
 * a fase seguinte (Sprint B/C) só precise trocar o corpo, sem mexer no
 * container de abas. Cada uma aponta para o módulo real que vai
 * alimentá-la, já construído no backend:
 *   - Peças      → editor jurídico (Onda 6, app/api/v1/endpoints/legal_editor.py)
 *   - Documentos → DMS jurídico (Onda 7, app/api/v1/endpoints/legal_documents.py)
 *   - Protocolo  → pasta de protocolo (Onda 7, app/api/v1/endpoints/protocol_folder.py)
 * Estado honesto: nunca finge dado que ainda não foi cabeado nesta tela.
 */

function PlaceholderPanel({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof FileClock;
  title: string;
  description: string;
}) {
  return (
    <KlmPanel>
      <div className="flex items-start gap-3 p-6">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-secondary">
          <Icon className="h-4 w-4 text-muted-foreground" />
        </span>
        <div>
          <p className="text-sm font-semibold">{title}</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
        </div>
      </div>
    </KlmPanel>
  );
}

export function LegalArtifactsTab({ demandId }: { demandId: string }) {
  void demandId;
  return (
    <PlaceholderPanel
      icon={FileClock}
      title="Peças da demanda — em construção nesta tela"
      description="O editor jurídico (rascunho, revisão adversarial e verificação de citações) já existe no backend. Esta aba vai listar as peças da demanda e abrir o editor por versão."
    />
  );
}

export function LegalDocumentsTab({ demandId }: { demandId: string }) {
  void demandId;
  return (
    <PlaceholderPanel
      icon={FolderClosed}
      title="Documentos da demanda — em construção nesta tela"
      description="O DMS jurídico (upload, versionamento e vínculo a fatos/provas) já existe no backend. Esta aba vai trazer a árvore de documentos da demanda."
    />
  );
}

export function LegalProtocolTab({ demandId }: { demandId: string }) {
  void demandId;
  return (
    <PlaceholderPanel
      icon={Send}
      title="Pasta de protocolo — em construção nesta tela"
      description="A pasta de protocolo (manifesto, hash e handoff humano) já existe no backend. Esta aba vai montar e liberar a pasta para protocolo direto desta demanda."
    />
  );
}
