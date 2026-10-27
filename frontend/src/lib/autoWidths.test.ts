import { describe, expect, it } from 'vitest';
import { layoutColumns, type ColumnSpec } from './autoWidths';
import { providerColor } from './providerColors';

const COLUMNS: ColumnSpec[] = [
  { id: 'model', min: 200, max: 400, grow: 2 },
  { id: 'id', min: 100, max: 200, grow: 1 },
  { id: 'price', min: 80, max: 80 },
];

describe('layoutColumns', () => {
  it('keeps minimums when there is no room', () => {
    expect(layoutColumns({ model: 50, id: 20, price: 30 }, COLUMNS, 300)).toEqual({
      model: 200,
      id: 100,
      price: 80,
    });
  });

  it('uses measured widths inside the clamps', () => {
    const widths = layoutColumns({ model: 260, id: 140, price: 80 }, COLUMNS, 480);
    expect(widths.model).toBeGreaterThanOrEqual(260);
    expect(widths.id).toBeGreaterThanOrEqual(140);
    expect(widths.price).toBe(80);
  });

  it('shares leftover width by weight, respecting maximums', () => {
    const widths = layoutColumns({}, COLUMNS, 700);
    expect(widths['price']).toBe(80);
    expect(widths.model).toBe(400); // capped
    expect(widths.id).toBe(200); // capped
    expect(widths.model + widths.id + widths.price).toBe(680);
  });

  it('grows proportionally when only part of the space is spare', () => {
    const widths = layoutColumns({}, COLUMNS, 460);
    expect(widths['price']).toBe(80);
    expect(widths.model + widths.id).toBe(380);
    expect(widths.model).toBeGreaterThan(widths.id);
  });
});

describe('providerColor', () => {
  it('uses stable brand colours', () => {
    expect(providerColor('anthropic')).toBe('orange');
    expect(providerColor('openrouter')).toBe('indigo');
    expect(providerColor('openai')).toBe('green');
  });

  it('falls back deterministically', () => {
    expect(providerColor('something-else')).toBe(providerColor('something-else'));
    expect(providerColor(null)).toBe('gray');
  });
});
