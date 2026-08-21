import { describe, expect, it } from 'vitest';
import {
  adjustDateText,
  datePartAtCaret,
  normalizeDateText,
  parseDateText,
} from '../../src/components/date-input-utils';

describe('date input helpers', () => {
  it('accepts one digit day and month and normalizes them', () => {
    expect(parseDateText('5/8/2026')).toBe('2026-08-05');
    expect(normalizeDateText('5/8/2026')).toBe('05/08/2026');
  });

  it('rejects impossible calendar dates', () => {
    expect(parseDateText('31/2/2026')).toBeNull();
    expect(parseDateText('0/8/2026')).toBeNull();
  });

  it('detects the date part under the caret', () => {
    const value = '05/08/2026';
    expect(datePartAtCaret(value, 1)).toBe('day');
    expect(datePartAtCaret(value, 4)).toBe('month');
    expect(datePartAtCaret(value, 8)).toBe('year');
  });

  it('adjusts day month and year while keeping a valid date', () => {
    expect(adjustDateText('31/01/2026', 'day', 1)).toBe('01/02/2026');
    expect(adjustDateText('31/01/2026', 'month', 1)).toBe('28/02/2026');
    expect(adjustDateText('29/02/2024', 'year', 1)).toBe('28/02/2025');
  });
});
