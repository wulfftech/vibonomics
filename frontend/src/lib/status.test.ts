import { describe, expect, it } from 'vitest';
import { isGraded, statusColor, statusLabel } from './status';

describe('isGraded', () => {
  it('treats only the three graded statuses as a score', () => {
    expect(isGraded('passed')).toBe(true);
    expect(isGraded('partial')).toBe(true);
    expect(isGraded('failed')).toBe(true);
  });

  it('rejects the statuses whose stored score is a placeholder', () => {
    // The API stores score 0.0 for these so the column stays a number; showing it would read as a zero.
    expect(isGraded('error')).toBe(false);
    expect(isGraded('ungraded')).toBe(false);
    expect(isGraded('skipped')).toBe(false);
  });

  it('handles missing and oddly cased values', () => {
    expect(isGraded(null)).toBe(false);
    expect(isGraded(undefined)).toBe(false);
    expect(isGraded('')).toBe(false);
    expect(isGraded('PASSED')).toBe(true);
  });
});

describe('statusLabel', () => {
  it('labels the ungraded status the runner reports', () => {
    expect(statusLabel('ungraded')).toBe('Ungraded');
    expect(statusColor('ungraded')).toBe('gray');
  });
});
