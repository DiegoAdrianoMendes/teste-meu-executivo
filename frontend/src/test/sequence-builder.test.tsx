import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SequenceBuilder, type SequenceMessage } from '@/components/flow/SequenceBuilder';

describe('SequenceBuilder', () => {
  it('renders a vertical timeline with Agora first and editable hours/days afterwards', () => {
    render(<SequenceBuilder value={[
      { id: 'first', templateId: 'tpl-first', templateName: 'Boas-vindas', body: 'Olá', status: 'approved', windowStatus: 'new', bindings: [{ fieldKey: 'contact.name', value: 'Ana' }] },
      { id: 'second', templateId: 'tpl-second', templateName: 'Follow-up', body: 'Retorno', status: 'approved', windowStatus: 'paid', delayValue: 2, delayUnit: 'days', bindings: [{ fieldKey: 'contact.name', value: 'Ana' }] },
    ]} />);

    expect(screen.getAllByText('Agora').length).toBeGreaterThan(0);
    expect(screen.getByLabelText('Prazo da mensagem 2')).toHaveValue(2);
    expect(screen.getByLabelText('Unidade do prazo da mensagem 2')).toHaveValue('days');
    expect(screen.getByText('Janela nova')).toBeInTheDocument();
    expect(screen.getByText('Janela paga')).toBeInTheDocument();
    expect(screen.getByTestId('sequence-builder').querySelector('[aria-label="Timeline da sequência"]')).toBeInTheDocument();
  });

  it('offers approved templates above, warns for pending templates, and creates below', () => {
    const onCreateTemplate = vi.fn();
    render(<SequenceBuilder templates={[
      { id: 'approved', name: 'aviso_aprovado', status: 'approved', bodyText: 'Olá' },
      { id: 'pending', name: 'aviso_pendente', status: 'pending', bodyText: 'Aguardando' },
    ]} onCreateTemplate={onCreateTemplate} />);

    expect(screen.getByText('Escolher template aprovado')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /aviso_aprovado/ })).toBeInTheDocument();
    expect(screen.getByText('Templates pendentes não iniciam a sequência')).toBeInTheDocument();
    expect(screen.getByText(/A Meta precisa aprovar o modelo antes do envio/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Criar novo template' }));
    expect(onCreateTemplate).toHaveBeenCalledOnce();
  });

  it('keeps bindings as fieldKey plus resolved value and defaults interrupt_on_reply to true', () => {
    const onChange = vi.fn();
    const onStart = vi.fn();
    const initial: SequenceMessage[] = [{ id: 'first', templateId: 'approved', templateName: 'Boas-vindas', body: 'Olá', status: 'approved', bindings: [{ fieldKey: 'contact.first_name', value: '' }] }];
    render(<SequenceBuilder value={initial} templates={[{ id: 'approved', name: 'Boas-vindas', status: 'approved', variables: [{ fieldKey: 'contact.first_name', resolvedValue: null }] }]} onChange={onChange} onStart={onStart} />);

    expect(screen.getByRole('switch', { name: 'Interromper sequência ao responder' })).toBeChecked();
    const resolvedValue = screen.getByLabelText('Valor resolvido para contact.first_name');
    expect(resolvedValue).toHaveAttribute('readonly');
    expect(resolvedValue).toHaveValue('');
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sequência' }));
    expect(onStart).toHaveBeenCalledWith(expect.any(Array), true);
  });

  it('marks a pending message and keeps Iniciar sequência blocked', () => {
    render(<SequenceBuilder value={[{ id: 'pending', templateName: 'Ainda não aprovado', status: 'pending' }]} />);
    expect(screen.getByText('Pendente: esta mensagem não será enviada até a aprovação da Meta.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Iniciar sequência' })).toBeDisabled();
  });

  it('allows an approved static template without inventing a binding', () => {
    const onStart = vi.fn();
    render(<SequenceBuilder
      value={[{ id: 'static', templateId: 'static-template', templateName: 'Aviso', body: 'Texto fixo', status: 'approved', bindings: [] }]}
      templates={[{ id: 'static-template', name: 'Aviso', status: 'approved', variableCount: 0 }]}
      onStart={onStart}
    />);
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sequência' }));
    expect(onStart).toHaveBeenCalledWith(expect.any(Array), true);
  });

  it('keeps a pending template visible as an explicit, blocked stage', () => {
    function ControlledBuilder() {
      const [messages, setMessages] = useState<SequenceMessage[]>([{ id: 'first', templateId: 'approved', templateName: 'Aviso', body: 'Texto fixo', status: 'approved', bindings: [] }]);
      return <SequenceBuilder value={messages} onChange={(next) => setMessages(next)} templates={[
        { id: 'approved', name: 'Aviso', status: 'approved', variableCount: 0 },
        { id: 'pending', name: 'Aguardando Meta', status: 'pending', bodyText: 'Retorno {{1}}', variableCount: 1, variables: [{ fieldKey: '', resolvedValue: '' }] },
      ]} />;
    }
    render(<ControlledBuilder />);

    fireEvent.click(screen.getByRole('button', { name: 'Adicionar etapa' }));
    expect(screen.getAllByText('Aguardando Meta').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Pendente: esta mensagem não será enviada até a aprovação da Meta.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Iniciar sequência' })).toBeDisabled();
  });

  it('allows a pending later stage while requiring the first stage to be approved', () => {
    const onStart = vi.fn();
    render(<SequenceBuilder
      value={[
        { id: 'first', templateId: 'approved', templateName: 'Aviso', body: 'Agora', status: 'approved', bindings: [] },
        { id: 'second', templateId: 'pending', templateName: 'Depois', body: 'Pendente', status: 'pending', delayValue: 24, delayUnit: 'hours', bindings: [] },
      ]}
      templates={[
        { id: 'approved', name: 'Aviso', status: 'approved', variableCount: 0 },
        { id: 'pending', name: 'Depois', status: 'pending', variableCount: 0 },
      ]}
      onStart={onStart}
    />);
    expect(screen.getByRole('button', { name: 'Iniciar sequência' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sequência' }));
    expect(onStart).toHaveBeenCalledWith(expect.any(Array), true);
  });

  it('blocks a template with header or button variables and explains the body-only rule', () => {
    const onStart = vi.fn();
    render(<SequenceBuilder
      value={[{ id: 'first', templateId: 'unsupported', templateName: 'Cabeçalho dinâmico', body: 'Texto', status: 'approved', bindings: [], unsupportedVariableScopes: ['header'] }]}
      templates={[{ id: 'unsupported', name: 'Cabeçalho dinâmico', status: 'approved', variableCount: 0, sequenceEligible: false, unsupportedVariableScopes: ['header'] }]}
      onStart={onStart}
    />);
    expect(screen.getByText(/sequência ainda aceita apenas variáveis no corpo/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sequência' }));
    expect(onStart).not.toHaveBeenCalled();
    expect(screen.getByRole('option', { name: 'Selecione um template aprovado' })).toBeInTheDocument();
  });
});
