import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
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
  {
    id: 1,
    task_id: 'instr-json',
    category: 'instruction',
    status: 'passed',
    score: 100,
    check_score: 100,
    transcript: [],
  },
  { id: 2, task_id: 'tool-pick', category: 'tools', status: 'error', score: 0 },
  { id: 3, task_id: 'agentic-fix-mean', category: 'agentic', status: 'ungraded', score: 0 },
] as unknown as EvalResultOut[];

// What the modal shows; a test swaps it to see another run.
let shown: { run: EvalRunOut; results: EvalResultOut[] } = { run, results };

vi.mock('../api/evals', () => ({
  isActiveRun: (status: string) => status === 'running',
  useRun: () => ({ isPending: false, isError: false, data: shown }),
  useRuns: () => ({ data: [shown.run], isPending: false, isError: false }),
  useJudges: () => ({ data: [] }),
  useSuites: () => ({ data: [] }),
  useCancelRun: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteRun: () => ({ mutate: vi.fn(), isPending: false }),
}));

let canChange = true;
vi.mock('../api/system', () => ({ useCanChange: () => canChange }));

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

describe('TestRunsModal, a score that rests on few tasks', () => {
  const summary = (over: Record<string, unknown>) => ({
    score: 100,
    tasks: 14,
    passed: 3,
    errors: 11,
    ungraded: 0,
    tokens: 1680,
    cost: 0,
    assessment: null,
    ...over,
  });
  const withSummary = (over: Record<string, unknown>) => ({
    run: { ...run, summary: { targets: { '3|acme/verify-model': summary(over) } } },
    results,
  });

  it('flags 3 of 14 scored as low coverage, and a model that completed its tasks as normal', () => {
    shown = withSummary({});
    try {
      const { unmount } = renderModal();
      expect(screen.getByText('Low coverage · 3 of 14 scored')).toBeInTheDocument();
      unmount();

      shown = withSummary({ passed: 12, errors: 2 });
      renderModal();
      expect(screen.queryByText(/Low coverage/)).not.toBeInTheDocument();
    } finally {
      shown = { run, results };
    }
  });

  it('says why a model that never started has no score', () => {
    const refused = {
      ...results[1],
      provider_id: 3,
      model_id: 'acme/verify-model',
      error: 'OpenCode serves it only to its own client',
    };
    shown = { run: { ...run, summary: { targets: {} } }, results: [refused] };
    try {
      renderModal();
      expect(screen.getByText(/Not run: OpenCode serves it only to its own client/)).toBeInTheDocument();
    } finally {
      shown = { run, results };
    }
  });

  it('does not call a model "not run" while its run is still going', () => {
    const failing = { ...results[1], provider_id: 3, model_id: 'acme/verify-model', error: 'HTTP 429' };
    shown = { run: { ...run, status: 'running', summary: {} }, results: [failing] };
    try {
      renderModal();
      expect(screen.queryByText(/Not run:/)).not.toBeInTheDocument();
    } finally {
      shown = { run, results };
    }
  });
});

/** Presses Tab from wherever focus is until it reaches the element, as a keyboard user would. */
async function tabTo(user: UserEvent, element: HTMLElement) {
  // The modal moves focus to its first control once it has opened; focusing something earlier would be undone.
  await waitFor(() => expect(document.activeElement).not.toBe(document.body));
  for (let step = 0; step < 20 && document.activeElement !== element; step += 1) {
    await user.tab();
  }
  expect(element).toHaveFocus();
}

describe('TestRunsModal, from the keyboard', () => {
  it('opens and closes a result row with Enter and Space, and says whether it is open', async () => {
    const user = userEvent.setup();
    renderModal();
    const toggle = screen.getByRole('button', { name: 'instr-json' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Transcript')).not.toBeInTheDocument();

    await tabTo(user, toggle);
    await user.keyboard('{Enter}');
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Transcript')).toBeInTheDocument();

    await user.keyboard(' ');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Transcript')).not.toBeInTheDocument();
  });

  it('opens a row once for one mouse click, on the button or elsewhere in the row', async () => {
    const user = userEvent.setup();
    renderModal();
    const toggle = screen.getByRole('button', { name: 'instr-json' });
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await user.click(screen.getByText('instruction'));
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
  });
});

describe('TestRunsModal, for a visitor who cannot change anything', () => {
  it('shows delete disabled, and enabled for someone who can', () => {
    canChange = false;
    try {
      const { unmount } = renderModal();
      expect(screen.getByRole('button', { name: /delete/i })).toBeDisabled();
      unmount();
      canChange = true;
      renderModal();
      expect(screen.getByRole('button', { name: /delete/i })).toBeEnabled();
    } finally {
      canChange = true;
    }
  });
});
