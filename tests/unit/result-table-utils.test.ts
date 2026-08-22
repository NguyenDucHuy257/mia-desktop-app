import { describe, expect, it } from 'vitest';
import { columnLabel, formatResultValue, isTotalResultColumn, orderResultColumns, parseResultNumber } from '../../src/features/results/result-table-utils';

describe('result table utilities', () => {
  it('formats requested monetary and rate columns with Vietnamese separators', () => {
    expect(formatResultValue('tgtcthue', 1234567)).toBe('1.234.567');
    expect(formatResultValue('dgia', '1234567.5')).toBe('1.234.567,5');
    expect(formatResultValue('tsuat', '10%')).toBe('10%');
    expect(formatResultValue('dvtte', 'VND')).toBe('VND');
  });

  it('parses numeric values used by dynamic totals', () => {
    expect(parseResultNumber('1.234,5')).toBe(1234.5);
    expect(parseResultNumber('10%')).toBe(10);
    expect(parseResultNumber('KCT')).toBeNull();
  });

  it('labels and orders invoice fields while keeping unknown payload columns', () => {
    expect(columnLabel('ttcktmai', 'details')).toBe('Tổng tiền CKTM');
    expect(columnLabel('tgtcthue', 'overview')).toBe('Tổng tiền chưa thuế');
    expect(orderResultColumns(['custom', 'dgia', 'id', 'shdon'], 'details')).toEqual(['shdon', 'dgia', 'custom']);
    expect(isTotalResultColumn('tgtttbso')).toBe(true);
  });
});
