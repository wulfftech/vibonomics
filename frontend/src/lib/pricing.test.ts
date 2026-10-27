import { describe, expect, it } from 'vitest';
import {
  blendedColor,
  blendedPrice,
  effectiveInputPrice,
  makeValueIndex,
  priceBand,
  taskCost,
  valueOf,
  valueTiers,
  type Profile,
} from './pricing';

const profile: Profile = {
  input_weight: 3,
  output_weight: 1,
  cache_hit_rate: 0,
  task_input_tokens: 30_000,
  task_output_tokens: 10_000,
};

describe('effectiveInputPrice', () => {
  it('returns the plain input price without caching', () => {
    expect(effectiveInputPrice({ input: 3, output: 15, cache_read: 0.3 }, 0)).toBe(3);
  });

  it('mixes in the cache read price', () => {
    expect(effectiveInputPrice({ input: 3, output: 15, cache_read: 0.3 }, 0.5)).toBeCloseTo(1.65);
  });

  it('falls back to the input price when cache reads are not priced', () => {
    expect(effectiveInputPrice({ input: 2, output: 8 }, 0.8)).toBe(2);
  });

  it('is null when the input price is unknown', () => {
    expect(effectiveInputPrice({ output: 8 }, 0)).toBeNull();
  });
});

describe('blendedPrice', () => {
  it('weights input and output', () => {
    // (3*1 + 1*5) / 4 = 2
    expect(blendedPrice({ input: 1, output: 5 }, profile)).toBe(2);
  });

  it('honours the cache hit rate', () => {
    const cached = { ...profile, cache_hit_rate: 0.8 };
    // input: 10*0.2 + 0*0.8 = 2 -> (3*2 + 1*10) / 4 = 4
    expect(blendedPrice({ input: 10, output: 10, cache_read: 0 }, cached)).toBeCloseTo(4);
  });

  it('is null with a zero weight and unknown prices', () => {
    expect(blendedPrice({ input: 1, output: 1 }, { ...profile, input_weight: 0, output_weight: 0 })).toBeNull();
    expect(blendedPrice({ input: 1 }, profile)).toBeNull();
  });
});

describe('taskCost', () => {
  it('prices the profile-sized task', () => {
    // 30k * 3 / 1e6 + 10k * 15 / 1e6 = 0.09 + 0.15
    expect(taskCost({ input: 3, output: 15 }, profile)).toBeCloseTo(0.24);
  });

  it('is zero for free models and null without prices', () => {
    expect(taskCost({ input: 0, output: 0 }, profile, true)).toBe(0);
    expect(taskCost({ input: 1 }, profile)).toBeNull();
    expect(taskCost({ input: null, output: null }, profile)).toBeNull();
  });
});

describe('value', () => {
  it('divides score by cost, and is infinite when free', () => {
    expect(valueOf(80, 0.4)).toBe(200);
    expect(valueOf(80, 0)).toBe(Infinity);
    expect(valueOf(null, 0.4)).toBeNull();
    expect(valueOf(80, null)).toBeNull();
  });
});

describe('value index', () => {
  it('scales log-scaled values across the dataset', () => {
    const index = makeValueIndex([10, 100, 1000]);
    expect(index(10)).toBe(0);
    expect(index(1000)).toBe(100);
    expect(index(100)).toBeCloseTo(50);
    expect(index(Infinity)).toBe(100);
    expect(index(null)).toBeNull();
  });

  it('handles a single value', () => {
    const index = makeValueIndex([42]);
    expect(index(42)).toBe(100);
  });
});

describe('bands and tiers', () => {
  it('assigns price bands', () => {
    expect(priceBand(null, true)).toBe('Free');
    expect(priceBand(0, false)).toBe('Free');
    expect(priceBand(null, false)).toBe('Unknown');
    expect(priceBand(0.25, false)).toBe('< $0.50');
    expect(priceBand(1, false)).toBe('$0.50–2');
    expect(priceBand(5, false)).toBe('$2–10');
    expect(priceBand(25, false)).toBe('$10+');
  });

  it('assigns value tiers by rank', () => {
    const tiers = valueTiers([100, null, 40, 20, 10, 5, 4, 3, 2, 1, 0.5]);
    expect(tiers[0]).toBe('Top 10%');
    expect(tiers[1]).toBe('Unscored');
    expect(tiers.at(-1)).toBe('Rest');
  });
});

describe('blendedColor', () => {
  it('uses green for cheap, amber in the middle and red above $5', () => {
    expect(blendedColor(0.05)).toContain('green');
    expect(blendedColor(2)).toContain('orange');
    expect(blendedColor(10)).toContain('red');
    expect(blendedColor(null)).toBeUndefined();
    expect(blendedColor(0, true)).toContain('green');
  });
});
