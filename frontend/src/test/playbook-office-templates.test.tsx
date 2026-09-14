/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PlaybookOfficeTemplatesEditor } from '@/klm/components/playbooks/PlaybookOfficeTemplatesEditor';
import { playbookFixture } from '@/test/playbooks-fixture';

const storage = vi.hoisted(() => ({
  download: vi.fn(),
  upload: vi.fn(),
}));
vi.mock('@/services/local-storage', async (original) => {
  const actual = await original<typeof import('@/services/local-storage')>();
  return {
    ...actual,
    downloadLocalStorageObject: storage.download,
    uploadLocalStorageObject: storage.upload,
  };
});

function renderEditor(onChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PlaybookOfficeTemplatesEditor content={playbookFixture.content} onChange={onChange} />
    </QueryClientProvider>,
  );
  return { onChange };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('PlaybookOfficeTemplatesEditor', () => {
  it('warns that generation is blocked without the firm letterhead and shows pending vs uploaded slots', () => {
    renderEditor();

    expect(screen.getByText(/nunca usamos um modelo genérico/i)).toBeTruthy();
    expect(screen.getAllByText('Pendente').length).toBeGreaterThan(0);
    expect(screen.getByText('Enviado')).toBeTruthy();
    expect(screen.getByText('procuracao-klm.docx · v1')).toBeTruthy();
  });

  it('rejects a non-.docx file client-side without calling upload or onChange', () => {
    const { onChange } = renderEditor();
    const row = screen.getByText('Contrato de honorários').closest('div.flex.flex-col') as HTMLElement;
    const input = row.querySelector('input[type="file"]') as HTMLInputElement;

    const file = new File(['conteudo'], 'contrato.pdf', { type: 'application/pdf' });
    fireEvent.change(input, { target: { files: [file] } });

    expect(storage.upload).not.toHaveBeenCalled();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('uploads a .docx and pins the returned object id/version onto that slot only', async () => {
    storage.upload.mockResolvedValue({
      objectId: 'object-contrato-honorarios',
      version: 3,
      originalFilename: 'contrato-honorarios-klm.docx',
    });
    const { onChange } = renderEditor();
    const row = screen.getByText('Contrato de honorários').closest('div.flex.flex-col') as HTMLElement;
    const input = row.querySelector('input[type="file"]') as HTMLInputElement;

    const file = new File(['conteudo'], 'contrato-honorarios-klm.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const updated = onChange.mock.calls[0][0];
    const template = updated.officeTemplates.find((item: { key: string }) => item.key === 'contrato_honorarios');
    expect(template.documentObjectId).toBe('object-contrato-honorarios');
    expect(template.documentObjectVersion).toBe(3);
    // Outros tipos permanecem intocados.
    const other = updated.officeTemplates.find((item: { key: string }) => item.key === 'peticao_inicial');
    expect(other.documentObjectId).toBeNull();
  });
});
