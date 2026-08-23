import { describe, expect, it } from 'vitest';
import { formatResultCell, formatTaxRate, formatVietnameseNumber } from '../../src/features/results/result-presentation';
import { emptyInvoiceSelection, exclusionFromSelection, invoiceSelected, selectionCount, toggleInvoice } from '../../src/features/results/result-selection';

describe('Vietnamese result presentation', () => {
  it('groups integers without creating fake decimal places', () => {
    expect(formatVietnameseNumber(1234567)).toBe('1.234.567');
    expect(formatResultCell('tgtcthue', '1234567')).toBe('1.234.567');
    expect(formatResultCell('tgia', 1.25)).toBe('1,25');
  });

  it('formats tax rates once and handles null numeric fields safely', () => {
    expect(formatTaxRate(10)).toBe('10%');
    expect(formatTaxRate('10%')).toBe('10%');
    expect(formatResultCell('tthue', null)).toBe('—');
    expect(formatResultCell('dvtte', 'VND')).toBe('VND');
  });
});

describe('invoice-level selection and session exclusion representation', () => {
  it('shares one stable invoice selection across duplicate detail lines', () => {
    const key = 'purchase|query|0101|AA|1|1';
    const selection = toggleInvoice(emptyInvoiceSelection(), key);
    expect(invoiceSelected(selection, key)).toBe(true);
    expect(selectionCount(selection, 999)).toBe(1);
  });

  it('represents select-all across filtered pages as one rule with exceptions', () => {
    const selection = {
      allMatching: true,
      selected: new Set<string>(),
      deselected: new Set(['purchase|query|0101|AA|2|1']),
    };
    expect(selectionCount(selection, 8250)).toBe(8249);
    const exclusion = exclusionFromSelection(
      { keys: [], rules: [] }, selection, 'details',
      { connection_id: 'conn_1', search: 'Dịch vụ', column_filters: { ten: { values: ['Dịch vụ'] } } },
    );
    expect(exclusion.rules).toHaveLength(1);
    expect(exclusion.rules[0].kind).toBe('details');
    expect(exclusion.rules[0].query.column_filters).toEqual({ ten: { values: ['Dịch vụ'] } });
    expect(exclusion.rules[0].except_keys).toHaveLength(1);
  });

  it('starts every Results mount with a new empty selection object', () => {
    const first = emptyInvoiceSelection();
    first.selected.add('invoice-a');
    expect(emptyInvoiceSelection().selected.size).toBe(0);
  });
});
