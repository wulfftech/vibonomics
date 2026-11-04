import { MantineProvider } from '@mantine/core';
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ModelDetail } from '../api/types';
import { FreeTerms, TestsTab } from './ModelDetailsModal';

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
