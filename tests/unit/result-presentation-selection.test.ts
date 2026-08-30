import { describe, expect, it } from 'vitest';
import { formatMoney, formatResultCell, formatTaxRate, formatVietnameseNumber } from '../../src/features/results/result-presentation';
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

  it('rounds every monetary cell and footer value to an integer without float tails', () => {
    expect(formatMoney(800343445)).toBe('800.343.445');
    expect(formatMoney(7146694)).toBe('7.146.694');
    expect(formatMoney(1234567.0)).toBe('1.234.567');
    expect(formatMoney(1924545.7000000002)).toBe('1.924.546');
    expect(formatMoney(1870182.2999999998)).toBe('1.870.182');
    expect(formatMoney(1924545.7000000002)).not.toContain(',7000000002');
    expect(formatMoney(1870182.2999999998)).not.toContain(',2999999998');
    expect(formatMoney(null)).toBe('\u2014');
    expect(formatResultCell('tgtcthue', 130483745, 'number')).toBe('130.483.745');
    expect(formatResultCell('dgia', 579982.5, 'number')).toBe('579.983');
    expect(formatResultCell('overview_tgtttbso', 5500000, 'number')).toBe('5.500.000');
    expect(formatResultCell('difference_tgtttbso', 50000, 'number')).toBe('50.000');
    expect(formatMoney(-0)).toBe('0');
    expect(formatMoney('-0.00')).toBe('0');
    expect(formatVietnameseNumber(-0)).toBe('0');
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
