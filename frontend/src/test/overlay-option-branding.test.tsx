/** @vitest-environment jsdom */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { Command, CommandItem, CommandList } from '@/components/ui/command';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu';
import { Popover, PopoverContent } from '@/components/ui/popover';
import { Select, SelectContent, SelectItem } from '@/components/ui/select';

const HIGHLIGHT_BACKGROUND = 'bg-[hsl(var(--option-highlight))]';
const HIGHLIGHT_FOREGROUND = 'text-[hsl(var(--option-highlight-foreground))]';

afterEach(cleanup);

describe('overlay option branding', () => {
  it('keeps select and dropdown options on the neutral interaction token', () => {
    render(
      <>
        <Select open value="open">
          <SelectContent>
            <SelectItem value="open">Aberta</SelectItem>
          </SelectContent>
        </Select>
        <DropdownMenu open>
          <DropdownMenuContent>
            <DropdownMenuItem>Editar</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </>,
    );

    const selectOption = document.querySelector<HTMLElement>('[role="option"]');
    const dropdownOption = document.querySelector<HTMLElement>('[role="menuitem"]');
    expect(selectOption).toHaveTextContent('Aberta');
    expect(dropdownOption).toHaveTextContent('Editar');

    for (const option of [selectOption!, dropdownOption!]) {
      expect(option).toHaveClass(`focus:${HIGHLIGHT_BACKGROUND}`);
      expect(option).toHaveClass(`focus:${HIGHLIGHT_FOREGROUND}`);
      expect(option).not.toHaveClass('focus:bg-accent');
    }
  });

  it('keeps command options inside popovers independent from workspace accent', () => {
    render(
      <Popover open>
        <PopoverContent>
          <Command>
            <CommandList>
              <CommandItem value="responsavel">Responsável</CommandItem>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>,
    );

    const option = screen.getByRole('option', { name: 'Responsável' });
    expect(option).toHaveClass(`data-[selected='true']:${HIGHLIGHT_BACKGROUND}`);
    expect(option).toHaveClass(`data-[selected=true]:${HIGHLIGHT_FOREGROUND}`);
    expect(option).not.toHaveClass("data-[selected='true']:bg-accent");
  });
});
