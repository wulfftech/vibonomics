import { describe, expect, it } from 'vitest';
import {
  axisMoney,
  axisTicks,
  bigCount,
  columnPath,
  dayLabel,
  labelIndexes,
  linePath,
  longDay,
  shareLabel,
} from './usage';

describe('dayLabel and longDay', () => {
  it('read the date as written, whatever the time zone', () => {
    expect(dayLabel('2026-10-30')).toBe('30 Oct');
    expect(dayLabel('2026-01-05')).toBe('5 Jan');
    expect(longDay('2026-10-30')).toBe('Fri 30 Oct 2026');
    expect(dayLabel('nonsense')).toBe('nonsense');
    expect(longDay('nonsense')).toBe('nonsense');
  });
});

describe('bigCount', () => {
  it('compacts counts that run to billions', () => {
    expect(bigCount(5_704_993_149)).toBe('5.70B');
    expect(bigCount(54_875_160)).toBe('54.9M');
    expect(bigCount(125_000_000)).toBe('125M');
    expect(bigCount(12_345)).toBe('12.3k');
    expect(bigCount(940)).toBe('940');
    expect(bigCount(0)).toBe('0');
    expect(bigCount(null)).toBe('—');
  });
});

describe('axisMoney', () => {
  it('keeps cents for small values and compacts thousands', () => {
    expect(axisMoney(0)).toBe('$0');
    expect(axisMoney(0.5)).toBe('$0.5');
    expect(axisMoney(2.5)).toBe('$2.5');
    expect(axisMoney(25)).toBe('$25');
    expect(axisMoney(1500)).toBe('$1.5k');
    expect(axisMoney(12_000)).toBe('$12k');
  });
});

describe('axisTicks', () => {
  it('gives clean steps and a top that holds the maximum', () => {
    expect(axisTicks(8.1)).toEqual({ top: 10, ticks: [0, 2, 4, 6, 8, 10] });
    expect(axisTicks(10)).toEqual({ top: 10, ticks: [0, 2, 4, 6, 8, 10] });
    expect(axisTicks(0.43)).toEqual({ top: 0.5, ticks: [0, 0.1, 0.2, 0.3, 0.4, 0.5] });
    expect(axisTicks(23)).toEqual({ top: 25, ticks: [0, 5, 10, 15, 20, 25] });
    expect(axisTicks(1234)).toEqual({ top: 1250, ticks: [0, 250, 500, 750, 1000, 1250] });
  });

  it('never cuts the maximum off, and has a range to draw for nothing at all', () => {
    for (const max of [0.0043, 0.37, 1, 1.01, 7, 42, 99.9, 100, 4321, 5_000_000]) {
      const { top, ticks } = axisTicks(max);
      expect(top).toBeGreaterThanOrEqual(max);
      expect(top).toBeLessThan(max * 2);
      expect(ticks[0]).toBe(0);
      expect(ticks.at(-1)).toBe(top);
    }
    expect(axisTicks(0)).toEqual({ top: 1, ticks: [0, 0.25, 0.5, 0.75, 1] });
    expect(axisTicks(Number.NaN).top).toBe(1);
  });
});

describe('labelIndexes', () => {
  it('always labels the latest day and spaces the rest', () => {
    expect(labelIndexes(5, 640)).toEqual([0, 1, 2, 3, 4]);
    const picked = labelIndexes(30, 320);
    expect(picked.at(-1)).toBe(29);
    expect(picked.length).toBeLessThanOrEqual(5);
    expect(picked[1] - picked[0]).toBe(picked[2] - picked[1]);
    expect(labelIndexes(0, 320)).toEqual([]);
  });
});

describe('linePath', () => {
  it('lifts the pen at a gap', () => {
    expect(linePath([{ x: 0, y: 1 }, { x: 10, y: 2 }, null, { x: 30, y: 3 }])).toBe('M0.0 1.0L10.0 2.0M30.0 3.0');
    expect(linePath([null, null])).toBe('');
  });
});

describe('columnPath', () => {
  it('is square at the baseline and rounded at the top', () => {
    expect(columnPath(10, 20, 50, 100)).toBe('M10 100V54Q10 50 14 50H26Q30 50 30 54V100Z');
  });

  it('rounds no more than the column is wide or tall, and draws nothing for no height', () => {
    expect(columnPath(0, 4, 98, 100)).toBe('M0 100V100Q0 98 2 98H2Q4 98 4 100V100Z');
    expect(columnPath(0, 10, 100, 100)).toBe('');
  });
});

describe('shareLabel', () => {
  it('rounds, and says when a part is real but tiny', () => {
    expect(shareLabel(50, 200)).toBe('25%');
    expect(shareLabel(0.2, 200)).toBe('<1%');
    expect(shareLabel(0, 200)).toBe('0%');
    expect(shareLabel(1, 0)).toBe('—');
  });
});
