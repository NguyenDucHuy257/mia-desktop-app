import { describe, expect, it } from 'vitest';
import { phaseWeightedPercent } from '../../src/features/artifacts/use-xml-html-download-lifecycle';

describe('XML/HTML batch progress', () => {
  it('uses real source and copy phase fractions without resetting', () => {
    const values = [
      phaseWeightedPercent(0, 0, 2),
      phaseWeightedPercent(50, 0, 2),
      phaseWeightedPercent(100, 0, 2),
      phaseWeightedPercent(100, 25, 2),
      phaseWeightedPercent(100, 75, 2),
      phaseWeightedPercent(100, 100, 2),
    ];
    expect(values[0]).toBe(0);
    expect(values.at(-1)).toBe(100);
    expect(values.every((value) => value >= 0 && value <= 100)).toBe(true);
    expect(values).toEqual([...values].sort((left, right) => left - right));
  });

  it('weights one selected artifact without inventing timer progress', () => {
    expect(phaseWeightedPercent(100, 0, 1)).toBe(50);
    expect(phaseWeightedPercent(100, 50, 1)).toBe(75);
    expect(phaseWeightedPercent(100, 100, 1)).toBe(100);
  });
});
