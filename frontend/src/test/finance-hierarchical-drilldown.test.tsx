/** @vitest-environment jsdom */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { FinanceHierarchicalDrilldown } from '@/klm/components/FinanceDrilldownExplorer';

describe('FinanceHierarchicalDrilldown', () => {
  it('opens the next hierarchy level, preserves breadcrumb and paginates', () => {
    const select = vi.fn();
    const page = vi.fn();
    const root = vi.fn();
    render(<FinanceHierarchicalDrilldown
      groups={[{dimension:'cost_center',id:'center-1',label:'ADM · Administrativo',amount:'100'}]}
      dimension="cost_center" total={100} totalCount={51} offset={0} limit={25} hasMore
      breadcrumb={[{dimension:'root',label:'Financeiro',onSelect:root}]}
      onGroupSelect={select} onPageChange={page}
    />);
    fireEvent.click(screen.getByRole('button',{name:/ADM · Administrativo/}));
    expect(select).toHaveBeenCalledWith(expect.objectContaining({dimension:'cost_center',id:'center-1'}));
    fireEvent.click(screen.getByRole('button',{name:'Financeiro'}));
    expect(root).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button',{name:'Próxima'}));
    expect(page).toHaveBeenCalledWith(25);
    expect(screen.getByText('Página 1 de 3 · 51 lançamentos')).toBeInTheDocument();
  });

  it('prevents navigation through an unallocated aggregate', () => {
    const select = vi.fn();
    render(<FinanceHierarchicalDrilldown groups={[{dimension:'client',id:null,label:'Não alocado',amount:25}]} dimension="client" total={25} totalCount={1} offset={0} limit={25} hasMore={false} breadcrumb={[]} onGroupSelect={select} onPageChange={vi.fn()} />);
    const group = screen.getByRole('button',{name:/Não alocado/});
    expect(group).toBeDisabled();
    fireEvent.click(group);
    expect(select).not.toHaveBeenCalled();
  });
});
