import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { EvalResultOut, EvalRunOut } from '../api/types';
import { TestRunsModal } from './TestRunsModal';

// The modal fetches through TanStack Query; these tests are about what it renders for a finished run.
const run: EvalRunOut = {
  id: 7,
  created_at: '2026-10-27T22:42:15Z',
  finished_at: '2026-10-27T22:43:00Z',
  status: 'done',
  suite: 'quick',
  judge_provider_id: 3,
  judge_model_id: 'acme/verify-model',
  targets: [{ provider_id: 3, model_id: 'acme/verify-model', name: 'Verify Model', provider_name: 'Acme' }],
  progress: { total: 3, done: 3 },
  summary: {
    targets: {
      '3|acme/verify-model': {
        score: 100,
        tasks: 3,
        passed: 1,
        errors: 1,
        ungraded: 1,
        tokens: 1234,
        cost: 0.0123,
        assessment: null,
      },
    },
    note: 'Tasks with no test result and no judge score (1) are marked ungraded and left out of the scores.',
    sandbox: 'disabled',
  },
  error: null,
};

const results = [
  { task_id: 'instr-json', category: 'instruction', status: 'passed', score: 100, check_score: 100 },
  { task_id: 'tool-pick', category: 'tools', status: 'error', score: 0 },
  { task_id: 'agentic-fix-mean', category: 'agentic', status: 'ungraded', score: 0 },
] as unknown as EvalResultOut[];

vi.mock('../api/evals', () => ({
  isActiveRun: () => false,
  useRun: () => ({ isPending: false, isError: false, data: { run, results } }),
  useRuns: () => ({ data: [run], isPending: false, isError: false }),
  useJudges: () => ({ data: [] }),
  useSuites: () => ({ data: [] }),
  useCancelRun: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteRun: () => ({ mutate: vi.fn(), isPending: false }),
}));

function renderModal() {
  return render(
    <MantineProvider>
      <TestRunsModal opened initialRunId={7} onClose={() => undefined} />
    </MantineProvider>,
  );
}

describe('TestRunsModal, ungraded and errored results', () => {
  it('explains why tasks were left out of the scores', () => {
    renderModal();
    expect(screen.getByText(/About these scores/i)).toBeInTheDocument();
    expect(screen.getByText(/are marked ungraded and left out of the scores/i)).toBeInTheDocument();
  });

  it('counts the excluded tasks on the target card', () => {
    renderModal();
    expect(screen.getByText(/1 errored · 1 ungraded — not scored/i)).toBeInTheDocument();
  });

  it('shows a dash rather than a zero for rows with no grade', () => {
    renderModal();
    const row = (taskId: string) => screen.getByText(taskId).closest('tr') as HTMLElement;

    // instr-json passed with 100; the other two carry a placeholder 0 that must not be shown.
    expect(row('instr-json')).toHaveTextContent('100');
    for (const taskId of ['tool-pick', 'agentic-fix-mean']) {
      const cells = row(taskId).querySelectorAll('td');
      const scoreCell = cells[3]; // Task, Category, Status, Score for a single-target run
      expect(scoreCell.textContent?.trim()).toBe('—');
    }
  });
});
