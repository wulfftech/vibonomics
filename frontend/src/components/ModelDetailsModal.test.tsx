import { MantineProvider } from '@mantine/core';
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ModelDetail } from '../api/types';
import { TestsTab } from './ModelDetailsModal';

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
