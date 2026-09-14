/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import {
  ConversationCollaborationBar,
  type ConversationCollaborationBarProps,
  type ConversationMentionTarget,
  type ConversationMessageReference,
} from '@/klm/components/ConversationCollaborationBar';

afterEach(cleanup);

const reference: ConversationMessageReference = {
  id: 'message-42',
  authorLabel: 'Ana Paula Ribeiro',
  body: 'O plano negou o medicamento novamente e enviou esta justificativa.',
  occurredAtLabel: '14:32',
};

const userTarget: ConversationMentionTarget = {
  id: 'user-marina',
  kind: 'user',
  name: 'Marina Costa',
  handle: 'marina.costa',
  detail: 'Advogada',
};

const teamTarget: ConversationMentionTarget = {
  id: 'team-health',
  kind: 'team',
  name: 'Equipe Saúde',
  handle: 'saude',
  memberCount: 4,
};

function collaborationProps(overrides: Partial<ConversationCollaborationBarProps> = {}): ConversationCollaborationBarProps {
  return {
    conversationKind: 'group',
    conversationLabel: 'Caso Zolgensma',
    reference: null,
    participants: [
      { id: 'participant-ana', name: 'Ana Paula Ribeiro', roleLabel: 'Cliente', presence: 'online' },
      { id: 'participant-marina', name: 'Marina Costa', handle: 'marina.costa', roleLabel: 'Advogada', presence: 'away' },
    ],
    participantCount: 3,
    mentionTargets: [userTarget, teamTarget],
    selectedMentions: [],
    capabilities: {
      reply: true,
      mentionUsers: true,
      mentionTeams: true,
      viewParticipants: true,
    },
    onReply: vi.fn(),
    onClearReference: vi.fn(),
    onMentionSelect: vi.fn(),
    onMentionRemove: vi.fn(),
    ...overrides,
  };
}

describe('ConversationCollaborationBar', () => {
  it('shows a selected message as a compact reference and exposes reply and cancel actions', () => {
    const props = collaborationProps();
    const { rerender } = render(<ConversationCollaborationBar {...props} />);

    expect(screen.queryByTestId('conversation-message-reference')).not.toBeInTheDocument();

    const selectedProps = { ...props, reference };
    rerender(<ConversationCollaborationBar {...selectedProps} />);

    const referenceRegion = screen.getByRole('region', { name: 'Mensagem selecionada como referência' });
    expect(within(referenceRegion).getByText(reference.authorLabel)).toBeInTheDocument();
    expect(within(referenceRegion).getByText(reference.body)).toHaveClass('truncate');

    fireEvent.click(within(referenceRegion).getByRole('button', { name: 'Responder' }));
    expect(props.onReply).toHaveBeenCalledOnce();
    expect(props.onReply).toHaveBeenCalledWith(reference);

    fireEvent.click(within(referenceRegion).getByRole('button', { name: 'Cancelar referência' }));
    expect(props.onClearReference).toHaveBeenCalledOnce();
    expect(document.querySelector('a[href]')).not.toBeInTheDocument();
  });

  it('selects users and teams from the mention picker without leaving the conversation', () => {
    const onMentionSelect = vi.fn();
    const onMentionRemove = vi.fn();
    const props = collaborationProps({ onMentionSelect, onMentionRemove });
    const { rerender } = render(
      <ConversationCollaborationBar
        {...props}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Mencionar usuário ou equipe' }));
    const userPicker = screen.getByRole('dialog', { name: 'Selecionar menção' });
    const searchInput = within(userPicker).getByRole('combobox', { name: 'Buscar usuários ou equipes' });
    expect(within(userPicker).getByText('Usuários')).toBeInTheDocument();
    expect(within(userPicker).getByText('Equipes')).toBeInTheDocument();
    fireEvent.change(searchInput, { target: { value: 'Marina' } });
    expect(within(userPicker).queryByRole('option', { name: /Equipe Saúde/ })).not.toBeInTheDocument();

    fireEvent.click(within(userPicker).getByRole('option', { name: /Marina Costa/ }));
    expect(onMentionSelect).toHaveBeenLastCalledWith(userTarget);
    expect(screen.queryByRole('dialog', { name: 'Selecionar menção' })).not.toBeInTheDocument();

    rerender(<ConversationCollaborationBar {...props} selectedMentions={[userTarget]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Mencionar usuário ou equipe' }));
    const teamPicker = screen.getByRole('dialog', { name: 'Selecionar menção' });
    expect(within(teamPicker).getByRole('combobox', { name: 'Buscar usuários ou equipes' })).toHaveValue('');
    expect(within(teamPicker).getByLabelText('Já mencionada').closest('[role="option"]')).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(within(teamPicker).getByRole('option', { name: /Equipe Saúde/ }));
    expect(onMentionSelect).toHaveBeenLastCalledWith(teamTarget);

    fireEvent.click(screen.getByRole('button', { name: 'Remover menção @marina.costa' }));
    expect(onMentionRemove).toHaveBeenCalledWith(userTarget);
    expect(document.querySelector('a[href]')).not.toBeInTheDocument();
  });

  it('identifies a group and keeps its participant list in a local popup', () => {
    render(<ConversationCollaborationBar {...collaborationProps()} />);

    const groupButton = screen.getByRole('button', { name: 'Conversa em grupo, 3 participantes' });
    expect(groupButton).toHaveTextContent('Grupo');
    fireEvent.click(groupButton);

    const participantDialog = screen.getByRole('dialog', { name: 'Participantes da conversa' });
    expect(within(participantDialog).getByText('Caso Zolgensma')).toBeInTheDocument();
    expect(within(participantDialog).getByText('Ana Paula Ribeiro')).toBeInTheDocument();
    expect(within(participantDialog).getByText('Marina Costa')).toBeInTheDocument();
    expect(within(participantDialog).getByText('+1 participante não listado')).toBeInTheDocument();
  });

  it('adds an internal participant in one option click and keeps provider group management separate', () => {
    const onParticipantAdd = vi.fn();
    render(
      <ConversationCollaborationBar
        {...collaborationProps({
          participantTargets: [userTarget, teamTarget],
          canManageParticipants: true,
          providerGroupManagement: {
            available: false,
            message: 'A rota em Coexistência não oferece gestão de grupos oficiais.',
          },
          onParticipantAdd,
        })}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Adicionar participante interno' }));
    const participantDialog = screen.getByRole('dialog', { name: 'Participantes da conversa' });
    expect(within(participantDialog).getByText('Participação interna Meu Executivo')).toBeInTheDocument();
    expect(within(participantDialog).getByText(/não altera o grupo oficial do provedor/i)).toBeInTheDocument();
    expect(within(participantDialog).getByLabelText('Gestão do grupo oficial do provedor')).toHaveTextContent('gate fechado');

    fireEvent.click(within(participantDialog).getByRole('option', { name: /Equipe Saúde/ }));

    expect(onParticipantAdd).toHaveBeenCalledOnce();
    expect(onParticipantAdd).toHaveBeenCalledWith(teamTarget);
    expect(document.querySelector('a[href]')).not.toBeInTheDocument();
  });

  it('gates replies, participant details, and each mention target category', () => {
    render(
      <ConversationCollaborationBar
        {...collaborationProps({
          reference,
          capabilities: {
            reply: false,
            mentionUsers: true,
            mentionTeams: false,
            viewParticipants: false,
          },
          capabilityMessages: {
            reply: 'Resposta bloqueada pela política do caso.',
            mentionTeams: 'Menções coletivas exigem permissão adicional.',
            viewParticipants: 'Participantes protegidos.',
          },
        })}
      />,
    );

    expect(screen.getByRole('button', { name: 'Responder' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Conversa em grupo; participantes restritos/ })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Mencionar usuário ou equipe' }));
    const picker = screen.getByRole('dialog', { name: 'Selecionar menção' });
    expect(within(picker).getByText('Marina Costa')).toBeInTheDocument();
    expect(within(picker).queryByText('Equipe Saúde')).not.toBeInTheDocument();
    expect(within(picker).getByText('Menções coletivas exigem permissão adicional.')).toBeInTheDocument();
  });

  it('disables the mention picker when neither user nor team mentions are allowed', () => {
    render(
      <ConversationCollaborationBar
        {...collaborationProps({
          conversationKind: 'individual',
          participants: [{ id: 'participant-ana', name: 'Ana Paula Ribeiro', roleLabel: 'Cliente' }],
          participantCount: 1,
          capabilities: {
            reply: true,
            mentionUsers: false,
            mentionTeams: false,
            viewParticipants: true,
          },
        })}
      />,
    );

    expect(screen.getByRole('button', { name: 'Conversa individual, 1 participante' })).toHaveTextContent('Individual');
    expect(screen.getByRole('button', { name: /Menções indisponíveis/ })).toBeDisabled();
  });
});
