import { MantineProvider } from '@mantine/core';
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ModelDetail } from '../api/types';
import { tone } from '../lib/tone';
import { FreeTerms, ModelDetailsModal, ScoreCard, TestsTab } from './ModelDetailsModal';

// What the detail query returns for the open model. With no id it is disabled, so it holds nothing and is pending.
let loaded: ModelDetail | undefined;
vi.mock('../api/models', () => ({
  useModelDetail: (id: number | null) =>
    id === null
      ? { data: undefined, isPending: true, isError: false }
      : { data: loaded, isPending: false, isError: false },
}));
vi.mock('./SnippetTabs', () => ({ SnippetTabs: () => null }));

// A result that was not graded has a placeholder score of 0. It must read as a dash, not as a zero the
// model earned: the run summary already does this, and this tab has to agree with it.
const detail = {
  row: { test: null },
  tests: [
    {
      run_id: 7,
      task_id: 'instr-json',
      category: 'instruction',
      status: 'passed',
      score: 100,
      created_at: '2026-10-27T22:42:15Z',
    },
    {
      run_id: 7,
      task_id: 'tool-pick',
      category: 'tools',
      status: 'error',
      score: 0,
      created_at: '2026-10-27T22:42:16Z',
    },
    {
      run_id: 7,
      task_id: 'agentic-fix-mean',
      category: 'agentic',
      status: 'ungraded',
      score: 0,
      created_at: '2026-10-27T22:42:17Z',
    },
    {
      run_id: 7,
      task_id: 'acc-context',
      category: 'accuracy',
      status: 'failed',
      score: 0,
      created_at: '2026-10-27T22:42:18Z',
    },
  ],
} as unknown as ModelDetail;

function scoreCell(taskId: string): string {
  const row = screen.getByText(taskId).closest('tr');
  if (!row) throw new Error(`no row for ${taskId}`);
  return within(row).getAllByRole('cell')[3].textContent ?? '';
}

describe('TestsTab', () => {
  it('shows a score only for graded results', () => {
    render(
      <MantineProvider>
        <TestsTab detail={detail} />
      </MantineProvider>,
    );
    expect(scoreCell('instr-json')).toBe('100');
    expect(scoreCell('acc-context')).toBe('0'); // failed is a real zero
    expect(scoreCell('tool-pick')).not.toBe('0'); // errored: no grade
    expect(scoreCell('agentic-fix-mean')).not.toBe('0'); // ungraded: no grade
  });
});

describe('ScoreCard', () => {
  it('colours the score with tone(), so it clears AA in both schemes', () => {
    render(
      <MantineProvider>
        <ScoreCard label="Composite" value={85} />
        <ScoreCard label="Coding" value={45} />
      </MantineProvider>,
    );
    expect(screen.getByText('85').getAttribute('style') ?? '').toContain(tone('green'));
    expect(screen.getByText('45').getAttribute('style') ?? '').toContain(tone('yellow'));
  });
});

// OpenCode's free models: which client may call them, and what the docs say happens to your prompts.
function termsDetail(over: Record<string, unknown>, dataUse: string | null): ModelDetail {
  return {
    row: { provider_type: 'opencode', client_restricted: null, ...over },
    data_use: dataUse,
  } as unknown as ModelDetail;
}

function renderTerms(detail: ModelDetail) {
  return render(
    <MantineProvider>
      <FreeTerms detail={detail} />
    </MantineProvider>,
  );
}

describe('FreeTerms', () => {
  it('says plainly that a free OpenCode model works only in the OpenCode client, and what it does with your data', () => {
    renderTerms(
      termsDetail(
        { client_restricted: 'opencode' },
        'During its free period, collected data may be used to improve the model.',
      ),
    );
    expect(screen.getByText('Who can call it')).toBeTruthy();
    expect(screen.getByText(/Only the OpenCode client\. Any other client gets HTTP 403/)).toBeTruthy();
    expect(screen.getByText('Your data')).toBeTruthy();
    expect(screen.getByText(/collected data may be used to improve the model/)).toBeTruthy();
    expect(screen.getByRole('link', { name: /OpenCode's model docs/ }).getAttribute('href')).toBe(
      'https://opencode.ai/v2/docs/console/models/',
    );
  });

  it('gives a model with a data note but no restriction (a Go free model) only the data note', () => {
    renderTerms(
      termsDetail(
        { provider_type: 'opencode-go' },
        'Your data is not used for training. It is retained for 30 days for safety reasons.',
      ),
    );
    expect(screen.queryByText('Who can call it')).toBeNull();
    expect(screen.getByText(/retained for 30 days/)).toBeTruthy();
    expect(screen.getByRole('link', { name: /OpenCode's model docs/ }).getAttribute('href')).toBe(
      'https://opencode.ai/v2/docs/console/go/',
    );
  });

  it('draws nothing for a model with neither', () => {
    renderTerms(termsDetail({}, null));
    expect(screen.queryByText('Free model terms')).toBeNull();
  });
});

describe('ModelDetailsModal, closing', () => {
  loaded = {
    row: {
      id: 1,
      name: 'Alpha',
      model_id: 'acme/alpha',
      provider_type: 'openrouter',
      provider_name: 'OpenRouter',
      input_modalities: ['text'],
      price: { input: 3, output: 15, cache_read: null, cache_write: null },
      list_price: null,
      discount: null,
      pricing_source: 'test',
      first_seen: '2026-01-01T00:00:00Z',
      scores: {},
    },
    description: null,
    tests: [],
  } as unknown as ModelDetail;

  it('keeps the last model on screen while it fades out, instead of flashing "Loading…"', () => {
    const modal = (modelId: number | null) => (
      <MantineProvider>
        <ModelDetailsModal
          modelId={modelId}
          profile={undefined}
          onChangeModel={() => undefined}
          onClose={() => undefined}
        />
      </MantineProvider>
    );
    const { rerender } = render(modal(1));
    expect(screen.getByText('Prices per 1M tokens')).toBeInTheDocument();

    rerender(modal(null));
    expect(screen.getByText('Prices per 1M tokens')).toBeInTheDocument();
    expect(screen.queryByText('Loading…')).not.toBeInTheDocument();
  });
});
