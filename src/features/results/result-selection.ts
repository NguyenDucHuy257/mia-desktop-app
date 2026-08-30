import type { ResultExclusion, ResultExclusionRule, ResultQuery } from '../../lib/runtime-bridge';

export interface InvoiceSelection {
  allMatching: boolean;
  selected: Set<string>;
  deselected: Set<string>;
}

export const emptyInvoiceSelection = (): InvoiceSelection => ({
  allMatching: false, selected: new Set(), deselected: new Set(),
});

export function invoiceSelected(selection: InvoiceSelection, invoiceKey: string) {
  return selection.allMatching
    ? !selection.deselected.has(invoiceKey)
    : selection.selected.has(invoiceKey);
}

export function selectionCount(selection: InvoiceSelection, filteredInvoiceCount: number) {
  return selection.allMatching
    ? Math.max(0, filteredInvoiceCount - selection.deselected.size)
    : selection.selected.size;
}

export function toggleInvoice(selection: InvoiceSelection, invoiceKey: string): InvoiceSelection {
  const selected = new Set(selection.selected);
  const deselected = new Set(selection.deselected);
  if (selection.allMatching) {
    deselected.has(invoiceKey) ? deselected.delete(invoiceKey) : deselected.add(invoiceKey);
  } else {
    selected.has(invoiceKey) ? selected.delete(invoiceKey) : selected.add(invoiceKey);
  }
  return { ...selection, selected, deselected };
}

export function exclusionFromSelection(
  current: ResultExclusion,
  selection: InvoiceSelection,
  kind: 'overview' | 'details',
  query: ResultQuery,
): ResultExclusion {
  if (!selection.allMatching) {
    return { ...current, keys: [...new Set([...current.keys, ...selection.selected])] };
  }
  const rule: ResultExclusionRule = {
    kind,
    query: { ...query, cursor: null, limit: 1, exclusion: undefined },
    except_keys: [...selection.deselected],
  };
  return { ...current, rules: [...current.rules, rule] };
}
