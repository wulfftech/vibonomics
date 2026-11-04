import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ModelRow } from '../../api/types';
import { discountLabel } from '../../lib/format';
import { modelBadgeLabels } from '../../lib/modelText';
import { ModelNameCell } from './cells';

const row: ModelRow = {
  id: 1,
  provider_id: 1,
  provider_type: 'openrouter',
  provider_name: 'OpenRouter',
  model_id: 'deepseek/deepseek-v4.1-flash',
  name: 'DeepSeek V4.1 Flash',
  creator: 'deepseek',
  canonical_id: 'deepseek/deepseek-v4.1-flash',
  family: null,
  context_length: 1_000_000,
  max_output: null,
  input_modalities: ['text'],
  reasoning: true,
  tool_call: true,
  open_weights: true,
  release_date: '2026-01-01',
  price: { input: 0.147, output: 0.588, cache_read: null, cache_write: null },
  list_price: { input: 0.3, output: 1.2, cache_read: null, cache_write: null },
  discount: 0.51,
  discount_note: '51% off via StreamLake',
  discount_until: null,
  is_free: true,
  pricing_source: 'test',
  first_seen: '2026-01-01T00:00:00Z',
  is_new: true,
  new_discount: true,
  price_drop: true,
  active: false,
  testable: true,
  scores: { intelligence: null, coding: null, agentic: null, composite: null, own_test: null, source_count: 0 },
  reported_cost_per_task: null,
  test: null,
};

function drawn(alsoOnGo: boolean): string {
  render(
    <MantineProvider>
      <ModelNameCell row={row} alsoOnGo={alsoOnGo} />
    </MantineProvider>,
  );
  return document.body.textContent ?? '';
}

describe('ModelNameCell', () => {
  it('draws every badge the table measures, and none it does not', () => {
    const text = drawn(true);
    for (const label of modelBadgeLabels(row, true)) {
      expect(text).toContain(label);
    }
    expect(screen.getAllByText(/^(New|New discount|Price cut|Free|Inactive|also on Go)$/)).toHaveLength(
      modelBadgeLabels(row, true).length,
    );
  });

  it('leaves the discount to its own column', () => {
    expect(drawn(false)).not.toContain(discountLabel(0.51));
  });
});

describe('modelBadgeLabels', () => {
  it('lists nothing for a plain model', () => {
    const plain = { ...row, is_new: false, new_discount: false, price_drop: false, is_free: false, active: true };
    expect(modelBadgeLabels(plain)).toEqual([]);
  });
});
