import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ModelRow, UsageProfile } from '../api/types';
import { ModelsPage } from './ModelsPage';

const profile: UsageProfile = {
  id: 'agentic',
  name: 'Agentic',
  input_weight: 3,
  output_weight: 1,
  cache_hit_rate: 0,
  task_input_tokens: 30_000,
  task_output_tokens: 10_000,
};

function row(over: Partial<ModelRow> & { id: number; name: string }): ModelRow {
  return {
    provider_id: 1,
    provider_type: 'openrouter',
    provider_name: 'OpenRouter',
    model_id: over.name.toLowerCase().replace(/ /g, '-'),
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
    scores: { intelligence: 50, coding: 60, agentic: 70, composite: 60, own_test: null, source_count: 2 },
    reported_cost_per_task: null,
    test: null,
    ...over,
  };
}

// OpenCode lists a model once under Console and once under Go; the table folds the two into one row.
// A ":batch" copy is hidden by the default filter. Five rows come in, two are shown.
let models: ModelRow[] = [];

vi.mock('../api/models', () => ({
  useModels: () => ({
    data: { models, profiles: [profile], default_profile: 'agentic' },
    isPending: false,
    isError: false,
  }),
}));
vi.mock('../api/providers', () => ({ useProviders: () => ({ data: [] }) }));
vi.mock('../api/settings', () => ({ useSettings: () => ({ data: undefined }) }));
vi.mock('../api/system', () => ({
  useTriggerRefresh: () => ({ isPending: false, mutate: () => undefined }),
  useCanChange: () => true,
}));
vi.mock('../api/eventStream', () => ({ useEventStream: () => ({ markRequested: () => undefined }) }));
// The pieces below are tested on their own; this test is about the count under the table.
vi.mock('../components/models/BalancesStrip', () => ({ BalancesStrip: () => null }));
vi.mock('../components/models/ModelFiltersBar', () => ({ ModelFiltersBar: () => null }));
vi.mock('../components/models/ModelTable', () => ({ ModelTable: () => null }));
vi.mock('../components/ModelDetailsModal', () => ({ ModelDetailsModal: () => null }));
vi.mock('../components/SnippetModal', () => ({ SnippetModal: () => null }));
vi.mock('../components/RunTestsModal', () => ({ RunTestsModal: () => null }));
vi.mock('../components/TestRunsModal', () => ({ TestRunsModal: () => null }));

describe('ModelsPage', () => {
  beforeEach(() => {
    window.localStorage.clear();
    models = [
      row({ id: 1, name: 'Alpha' }),
      row({ id: 2, name: 'Alpha (batch)', model_id: 'acme/alpha:batch' }),
      row({ id: 3, name: 'Kimi K3', provider_id: 2, provider_type: 'opencode', provider_name: 'OpenCode Console' }),
      row({ id: 4, name: 'Kimi K3', provider_id: 4, provider_type: 'opencode-go', provider_name: 'OpenCode Go' }),
      row({ id: 5, name: 'Kimi K3', provider_id: 1, provider_type: 'openrouter', provider_name: 'OpenRouter' }),
    ];
  });

  it('counts the rows the table shows, after the batch copies are hidden and the Go offer is folded', () => {
    render(
      <MantineProvider>
        <ModelsPage />
      </MantineProvider>,
    );
    // Alpha, plus Kimi K3 on Console (with Go folded into it) and on OpenRouter: 3 of 5 rows.
    expect(screen.getByText(/Showing 3 of 5 models/)).toBeTruthy();
  });
});
