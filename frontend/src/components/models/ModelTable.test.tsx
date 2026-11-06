import { MantineProvider } from '@mantine/core';
import { render, screen, within } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import type { ModelRow, UsageProfile } from '../../api/types';
import { enrichModels, type DisplayRow } from '../../lib/modelTable';
import { ModelTable } from './ModelTable';
import { OWN_TEST_COMPOSITE_NOTE, OWN_TEST_NOTE } from './cells';

// jsdom has no layout, so the virtualiser would show no rows; this one shows them all.
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getVirtualItems: () =>
      Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * 45, end: (index + 1) * 45 })),
    getTotalSize: () => count * 45,
    measureElement: () => undefined,
  }),
}));

const profile: UsageProfile = {
  id: 'agentic',
  name: 'Agentic',
  input_weight: 3,
  output_weight: 1,
  cache_hit_rate: 0,
  task_input_tokens: 30_000,
  task_output_tokens: 10_000,
};

function row(id: number, name: string, preferred: boolean): ModelRow {
  return {
    id,
    name,
    provider_id: 1,
    provider_type: 'openrouter',
    provider_name: 'OpenRouter',
    model_id: `acme/${name.toLowerCase()}`,
    creator: 'acme',
    canonical_id: null,
    family: null,
    context_length: 128_000,
    max_output: 8192,
    input_modalities: ['text'],
    reasoning: true,
    tool_call: true,
    open_weights: false,
    release_date: '2026-01-01',
    price: { input: 3, output: 15, cache_read: null, cache_write: null },
    list_price: null,
    discount: null,
    discount_note: null,
    discount_until: null,
    is_free: false,
    pricing_source: 'test',
    first_seen: '2026-01-01T00:00:00Z',
    is_new: false,
    new_discount: false,
    price_drop: false,
    active: true,
    testable: true,
    scores: {
      intelligence: 50,
      coding: 60,
      agentic: 70,
      composite: 60,
      own_test: 81,
      own_test_preferred: preferred,
      source_count: 3,
    },
    reported_cost_per_task: null,
    test: null,
  };
}

function renderTable() {
  const models = enrichModels([row(1, 'Own', true), row(2, 'Blend', false)], profile);
  const rows: DisplayRow[] = models.map((model) => ({ kind: 'model', key: `r${model.row.id}`, model }));
  return render(
    <MantineProvider>
      <ModelTable
        rows={rows}
        profile={profile}
        sort={{ key: 'score', dir: 'desc' }}
        onSort={() => undefined}
        selected={new Set()}
        headerChecked={false}
        headerIndeterminate={false}
        headerDisabled={false}
        onToggleAll={() => undefined}
        onToggleGroup={() => undefined}
        handlers={{ onToggle: () => undefined, onOpen: () => undefined, onConfig: () => undefined }}
        emptyMessage="No models"
      />
    </MantineProvider>,
  );
}

function modelRow(name: string): HTMLElement {
  const tr = screen.getByText(name).closest('tr');
  if (tr === null) {
    throw new Error(`no row for ${name}`);
  }
  return tr;
}

describe('ModelTable own-test marker', () => {
  beforeAll(() => {
    // jsdom does not implement element scrolling or a canvas (the column widths fall back to a rough estimate).
    Element.prototype.scrollTo = () => undefined;
    HTMLCanvasElement.prototype.getContext = () => null;
  });

  it('marks coding, agentic and the composite of a model whose scores are its own tests, and only that model', () => {
    renderTable();
    const own = within(modelRow('Own'));
    expect(own.getAllByRole('img', { name: OWN_TEST_NOTE })).toHaveLength(2);
    expect(own.getAllByRole('img', { name: OWN_TEST_COMPOSITE_NOTE })).toHaveLength(1);
    expect(within(modelRow('Blend')).queryAllByRole('img')).toHaveLength(0);
  });

  it('puts the mark inside the score cells, so it adds no column', () => {
    renderTable();
    const cells = (name: string) => within(modelRow(name)).getAllByRole('cell');
    expect(cells('Own')).toHaveLength(cells('Blend').length);
    const coding = cells('Own')[9];
    expect(coding).toHaveTextContent('60');
    expect(within(coding).getByRole('img', { name: OWN_TEST_NOTE })).toBeInTheDocument();
  });
});
