import { describe, expect, it } from 'vitest';
import type { ModelRow, UsageProfile } from '../api/types';
import {
  DEFAULT_FILTERS,
  buildDisplayRows,
  effectiveSort,
  enrichModels,
  filterModels,
  foldOpenCodeOffers,
  sortModels,
  type ModelFilters,
} from './modelTable';

const profile: UsageProfile = {
  id: 'agentic',
  name: 'Agentic',
  input_weight: 3,
  output_weight: 1,
  cache_hit_rate: 0,
  task_input_tokens: 30_000,
  task_output_tokens: 10_000,
};

function row(overrides: Partial<ModelRow> & { id: number; name: string }): ModelRow {
  return {
    provider_id: 1,
    provider_type: 'openrouter',
    provider_name: 'OpenRouter',
    model_id: overrides.name.toLowerCase().replace(/ /g, '-'),
    creator: 'acme',
    canonical_id: `acme/${nameKey(overrides.name)}`,
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
    scores: { intelligence: 50, coding: 60, agentic: 70, composite: 60, own_test: null, source_count: 2 },
    reported_cost_per_task: null,
    test: null,
    ...overrides,
  };
}

function nameKey(name: string): string {
  return name.toLowerCase().replace(/ /g, '-');
}

const rows = [
  row({ id: 1, name: 'Alpha' }),
  row({
    id: 2,
    name: 'Bravo',
    is_free: true,
    price: { input: 0, output: 0, cache_read: null, cache_write: null },
    scores: { intelligence: 20, coding: 10, agentic: 5, composite: 10, own_test: 55, source_count: 1 },
    test: { score: 55, tasks: 5, runs: 1, last_run_at: null, tokens_per_task: 1000, cost_per_task: 0.01 },
    reported_cost_per_task: 0.02,
  }),
  row({
    id: 3,
    name: 'Charlie',
    discount: 0.5,
    list_price: { input: 6, output: 30, cache_read: null, cache_write: null },
    price: { input: 3, output: 15, cache_read: null, cache_write: null },
    new_discount: true,
    scores: { intelligence: null, coding: null, agentic: null, composite: null, own_test: null, source_count: 0 },
  }),
];

function filters(overrides: Partial<ModelFilters>): ModelFilters {
  return { ...DEFAULT_FILTERS, ...overrides };
}

describe('enrichModels', () => {
  const enriched = enrichModels(rows, profile);

  it('prefers measured cost, then reported, then the estimate', () => {
    expect(enriched[0].taskCostSource).toBe('estimated');
    expect(enriched[0].taskCost).toBeCloseTo(0.24);
    expect(enriched[1].taskCostSource).toBe('measured');
    expect(enriched[1].taskCost).toBeCloseTo(0.01);
  });

  it('keeps the undiscounted blended price for display', () => {
    // (3*3 + 15) / 4 = 6, and the list price is (3*6 + 30) / 4 = 12
    expect(enriched[2].blended).toBeCloseTo(6);
    expect(enriched[2].listBlended).toBeCloseTo(12);
  });

  it('scores value from the estimate, using an assumed cost for free models', () => {
    expect(enriched[0].value).toBeCloseTo(250);
    // Free model: (30k + 10k) tokens at the default .01/1M = .0004 per task.
    expect(enriched[1].valueCost).toBeCloseTo(0.0004);
    expect(enriched[1].value).toBeCloseTo(25000);
    expect(enriched[1].assumedFreeCost).toBe(0.01);
    expect(enriched[1].priceBand).toBe('Free');
    expect(enriched[2].value).toBeNull();
  });

  it('lets the assumed free cost be configured, including zero for infinite value', () => {
    const cheap = enrichModels(rows, profile, { freeModelCost: 1 })[1];
    expect(cheap.value).toBeCloseTo(250);
    const infinite = enrichModels(rows, profile, { freeModelCost: 0 })[1];
    expect(infinite.value).toBe(Infinity);
  });
});

describe('filterModels', () => {
  const enriched = enrichModels(rows, profile);

  it('filters by search across names and ids', () => {
    expect(filterModels(enriched, filters({ search: 'brav' })).map((m) => m.row.name)).toEqual(['Bravo']);
    expect(filterModels(enriched, filters({ search: 'acme/alpha' })).map((m) => m.row.name)).toEqual(['Alpha']);
  });

  it('applies quick chips together', () => {
    expect(filterModels(enriched, filters({ chips: ['free'] })).map((m) => m.row.name)).toEqual(['Bravo']);
    expect(filterModels(enriched, filters({ chips: ['discounted'] })).map((m) => m.row.name)).toEqual(['Charlie']);
    expect(filterModels(enriched, filters({ chips: ['scored', 'free'] })).map((m) => m.row.name)).toEqual(['Bravo']);
  });

  describe('batch variants', () => {
    const withBatch = enrichModels(
      [
        ...rows,
        row({ id: 4, name: 'Alpha (batch)', model_id: 'acme/alpha:batch' }),
        row({ id: 5, name: 'Delta', model_id: 'acme/batch-delta' }),
      ],
      profile,
    );

    it('hides them by default, and only by the :batch suffix', () => {
      expect(DEFAULT_FILTERS.nonBatchOnly).toBe(true);
      expect(filterModels(withBatch, DEFAULT_FILTERS).map((m) => m.row.name)).toEqual([
        'Alpha',
        'Bravo',
        'Charlie',
        'Delta',
      ]);
    });

    it('shows them once the filter is off', () => {
      expect(filterModels(withBatch, filters({ nonBatchOnly: false })).map((m) => m.row.name)).toContain(
        'Alpha (batch)',
      );
    });

    it('combines with the other filters', () => {
      expect(filterModels(withBatch, filters({ search: 'alpha' })).map((m) => m.row.name)).toEqual(['Alpha']);
      expect(filterModels(withBatch, filters({ search: 'alpha', nonBatchOnly: false })).map((m) => m.row.name)).toEqual(
        ['Alpha', 'Alpha (batch)'],
      );
    });
  });
});

describe('sorting', () => {
  const enriched = enrichModels(rows, profile);

  it('sorts best-first and puts unknown values last', () => {
    const sorted = sortModels(enriched, { key: 'score', dir: 'desc' });
    expect(sorted.map((m) => m.row.name)).toEqual(['Alpha', 'Bravo', 'Charlie']);
  });

  it('lets the Best value chip override the column sort', () => {
    expect(effectiveSort(filters({ chips: ['best'], sort: { key: 'name', dir: 'asc' } }))).toEqual({
      key: 'value',
      dir: 'desc',
    });
    const sorted = sortModels(enriched, effectiveSort(filters({ chips: ['best'] })));
    expect(sorted[0].row.name).toBe('Bravo');
  });
});

describe('grouping', () => {
  const enriched = enrichModels(rows, profile);

  it('adds group rows with aggregates and collapses on request', () => {
    const display = buildDisplayRows(enriched, 'priceBand', new Set());
    const groups = display.filter((d) => d.kind === 'group');
    // Alpha and Charlie both land in $2-10; Bravo is Free.
    expect(groups).toHaveLength(2);
    const collapsed = buildDisplayRows(
      enriched,
      'priceBand',
      new Set(groups.map((g) => (g.kind === 'group' ? g.group.key : ''))),
    );
    expect(collapsed.every((d) => d.kind === 'group')).toBe(true);
  });

  it('groups the same model across providers by canonical id', () => {
    const duplicate = row({ id: 9, name: 'Alpha', provider_id: 2, provider_name: 'Other' });
    const display = buildDisplayRows(enrichModels([rows[0], duplicate], profile), 'model', new Set());
    const groups = display.filter((d) => d.kind === 'group');
    expect(groups).toHaveLength(1);
    expect(display.filter((d) => d.kind === 'model')).toHaveLength(2);
  });
});

describe('folding the OpenCode Go offer', () => {
  const consoleRow = row({
    id: 20,
    name: 'Kimi K3',
    provider_id: 2,
    provider_type: 'opencode',
    provider_name: 'OpenCode Console',
  });
  const goRow = row({
    id: 21,
    name: 'Kimi K3',
    provider_id: 4,
    provider_type: 'opencode-go',
    provider_name: 'OpenCode Go',
  });
  const goOnly = row({
    id: 22,
    name: 'MiMo V2.5',
    provider_id: 4,
    provider_type: 'opencode-go',
    provider_name: 'OpenCode Go',
  });
  const consoleOnly = row({ id: 23, name: 'Nova Pro', provider_id: 2, provider_type: 'opencode' });
  const openRouter = row({ id: 24, name: 'Kimi K3', provider_id: 1, provider_name: 'OpenRouter' });

  it('keeps one row per model and marks the Console row', () => {
    const folded = foldOpenCodeOffers(enrichModels([consoleRow, goRow, goOnly, consoleOnly, openRouter], profile));
    expect(folded.map((model) => model.row.id).sort()).toEqual([20, 22, 23, 24]);
    expect(folded.find((model) => model.row.id === 20)?.alsoOnGo).toBe(true);
    expect(folded.filter((model) => model.alsoOnGo)).toHaveLength(1);
  });

  it('leaves a model that only one of the two lists alone', () => {
    const folded = foldOpenCodeOffers(enrichModels([goOnly, consoleOnly], profile));
    expect(folded).toHaveLength(2);
    expect(folded.some((model) => model.alsoOnGo)).toBe(false);
  });

  it('does nothing when only one OpenCode provider is in view', () => {
    const onlyGo = foldOpenCodeOffers(enrichModels([goRow, goOnly], profile));
    expect(onlyGo.map((model) => model.row.id)).toEqual([21, 22]);

    const onlyConsole = foldOpenCodeOffers(enrichModels([consoleRow, openRouter], profile));
    expect(onlyConsole.map((model) => model.row.id)).toEqual([20, 24]);
  });

  it('keeps the Go row when only it can be tested', () => {
    const untestableConsole = { ...consoleRow, testable: false };
    const folded = foldOpenCodeOffers(enrichModels([untestableConsole, goRow], profile));
    expect(folded.map((model) => model.row.id)).toEqual([21]);
    expect(folded[0].alsoOnGo).toBe(false);
  });
});
