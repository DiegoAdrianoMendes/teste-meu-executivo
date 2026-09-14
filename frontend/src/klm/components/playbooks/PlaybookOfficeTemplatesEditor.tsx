import { useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CircleAlert, Download, FileText, LoaderCircle, Trash2, Upload } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/use-toast';
import { KlmPill } from '@/klm/components/Primitives';
import { downloadLocalStorageObject, saveBlob, uploadLocalStorageObject } from '@/services/local-storage';
import type { PlaybookContent, PlaybookOfficeTemplate } from '@/services/playbooks';

type Props = {
  content: PlaybookContent;
  onChange: (content: PlaybookContent) => void;
};

const DOCX_EXTENSION = /\.docx$/i;

function OfficeTemplateRow({
  template,
  onUpload,
  onRemove,
  uploading,
}: {
  template: PlaybookOfficeTemplate;
  onUpload: (file: File) => void;
  onRemove: () => void;
  uploading: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const { toast } = useToast();
  const sent = Boolean(template.documentObjectId && template.documentObjectVersion);

  return (
    <div className="flex flex-col gap-3 border-b px-4 py-3 last:border-b-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-[13px] font-semibold">{template.label}</p>
          <KlmPill tone={sent ? 'green' : 'gold'}>{sent ? 'Enviado' : 'Pendente'}</KlmPill>
          {template.obrigatorio && <KlmPill tone="black">Obrigatório</KlmPill>}
          <span className="text-[10px] uppercase text-muted-foreground">.{template.formato}</span>
        </div>
        <p className="mt-1 truncate text-[11px] text-muted-foreground">
          {sent
            ? `${template.documentFilename ?? 'arquivo'} · v${template.documentObjectVersion}`
            : 'Nenhum arquivo timbrado enviado ainda para este tipo.'}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <input
          ref={inputRef}
          type="file"
          accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.currentTarget.value = '';
            if (!file) return;
            if (!DOCX_EXTENSION.test(file.name)) {
              toast({
                variant: 'destructive',
                title: 'Formato não aceito',
                description: 'Envie o modelo timbrado em .docx.',
              });
              return;
            }
            onUpload(file);
          }}
        />
        {sent && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() =>
              void downloadLocalStorageObject(template.documentObjectId!, template.documentObjectVersion!).then(
                (blob) => saveBlob(blob, template.documentFilename ?? `${template.key}.docx`),
              )
            }
          >
            <Download className="mr-2 h-4 w-4" />
            Baixar
          </Button>
        )}
        <Button type="button" size="sm" variant="outline" disabled={uploading} onClick={() => inputRef.current?.click()}>
          {uploading ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Upload className="mr-2 h-4 w-4" />}
          {sent ? 'Substituir' : 'Enviar modelo'}
        </Button>
        {sent && (
          <Button type="button" size="icon" variant="ghost" aria-label={`Remover modelo de ${template.label}`} onClick={onRemove}>
            <Trash2 className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}

export function PlaybookOfficeTemplatesEditor({ content, onChange }: Props) {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const replaceTemplate = (next: PlaybookOfficeTemplate) => {
    onChange({
      ...content,
      officeTemplates: content.officeTemplates.map((item) => (item.key === next.key ? next : item)),
    });
  };

  const upload = useMutation({
    mutationFn: async ({ key, file }: { key: PlaybookOfficeTemplate['key']; file: File }) => ({
      key,
      stored: await uploadLocalStorageObject(file),
    }),
    onSuccess: async ({ key, stored }) => {
      const current = content.officeTemplates.find((item) => item.key === key);
      if (current) {
        replaceTemplate({
          ...current,
          documentObjectId: stored.objectId,
          documentObjectVersion: stored.version,
          documentFilename: stored.originalFilename,
        });
      }
      await queryClient.invalidateQueries({ queryKey: ['local-storage'] });
      toast({ title: 'Modelo timbrado enviado e versionado.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Modelo não enviado', description: error.message }),
  });

  const pendingMandatory = content.officeTemplates.filter(
    (item) => item.obrigatorio && !item.documentObjectId,
  );

  return (
    <div className="space-y-4 p-4">
      <div className="flex items-start gap-2 border-l-2 border-primary bg-primary/5 px-3 py-2.5 text-[11px] leading-4">
        <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <p>
          <strong>Seus documentos serão gerados com o SEU timbrado e formatação.</strong> Sem modelo enviado, a geração
          do tipo fica bloqueada — nunca usamos um modelo genérico.
        </p>
      </div>

      {pendingMandatory.length > 0 && (
        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <FileText className="h-3.5 w-3.5" />
          {pendingMandatory.length === 1
            ? '1 modelo obrigatório ainda pendente de envio.'
            : `${pendingMandatory.length} modelos obrigatórios ainda pendentes de envio.`}
        </p>
      )}

      <div className="overflow-hidden rounded-md border">
        {content.officeTemplates.map((template) => (
          <OfficeTemplateRow
            key={template.key}
            template={template}
            uploading={upload.isPending && upload.variables?.key === template.key}
            onUpload={(file) => upload.mutate({ key: template.key, file })}
            onRemove={() =>
              replaceTemplate({
                ...template,
                documentObjectId: null,
                documentObjectVersion: null,
                documentFilename: null,
              })
            }
          />
        ))}
      </div>
    </div>
  );
}
