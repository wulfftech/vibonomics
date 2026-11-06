import { notifications } from '@mantine/notifications';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PresetPlan } from '../../api/types';
import { calls, fetchMock, plan, renderSection, SELECTED_KEY, sentTo, serve } from './presetTestKit';

vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }));

const SLUG = 'vibonomics-best-value';

type Finding = PresetPlan['validation'][number];

const FAILED: Finding = {
  level: 'error',
  check: 'provider',
  model: 'deepseek/flash',
  provider: 'deepinfra/fp8',
  message: 'deepinfra/fp8 is no longer listed for deepseek/flash on OpenRouter.',
};
const SLOW: Finding = {
  level: 'warning',
  check: 'health',
  model: 'deepseek/flash',
  provider: 'novita/fp4',
  message: 'novita/fp4 on deepseek/flash was up 40% of the last 30 minutes.',
};
const PASSED: Finding = {
  level: 'ok',
  check: 'listing',
  model: 'deepseek/flash',
  provider: null,
  message: '3 live endpoints, pins verified',
};

const CHECKED_AT = '2026-11-04T10:00:00Z';

/** A plan the live check has been run on. It is valid unless a finding is an error. */
function checked(findings: Finding[], over: Partial<PresetPlan> = {}): PresetPlan {
  return plan({
    validation: findings,
    validated_at: CHECKED_AT,
    valid: !findings.some((f) => f.level === 'error'),
    ...over,
  });
}

const REFUSED = `Live check failed: ${FAILED.message}`;

function open() {
  localStorage.setItem(SELECTED_KEY, JSON.stringify(SLUG));
}

async function editMinimumScore(user: ReturnType<typeof userEvent.setup>, value: string) {
  const field = await screen.findByLabelText('Minimum score');
  await user.clear(field);
  await user.type(field, value);
}

const applyButton = () => screen.getByRole('button', { name: 'Apply this plan' });

beforeEach(() => {
  fetchMock.mockReset();
  vi.mocked(notifications.show).mockClear();
  localStorage.clear();
  open();
});
afterEach(() => vi.unstubAllGlobals());

describe('OpenRouterSection live check on Preview and Apply', () => {
  it('turns Apply off and says why when the live check found an error', async () => {
    serve({ plan: checked([FAILED, PASSED]) });
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));

    expect(await screen.findByText('Checked against live providers')).toBeInTheDocument();
    expect(screen.getByText(/The live check found a problem, so this update will not be sent/)).toBeInTheDocument();
    expect(screen.getByText(FAILED.message, { exact: false })).toBeInTheDocument();
    expect(applyButton()).toBeDisabled();
    expect(calls('api/presets/apply')).toHaveLength(0);
  });

  it('leaves Apply on when the live check found warnings only, and applies the plan it showed', async () => {
    serve({ plan: checked([SLOW, PASSED]) });
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));

    expect(await screen.findByText('Checked against live providers')).toBeInTheDocument();
    expect(screen.getByText('Warning:')).toBeInTheDocument();
    expect(screen.queryByText(/will not be sent/)).toBeNull();
    expect(applyButton()).toBeEnabled();

    await user.click(applyButton());
    await waitFor(() => expect(calls('api/presets/apply')).toHaveLength(1));
    expect(sentTo('api/presets/apply')).toEqual({ slug: SLUG, digest: 'abc123' });
  });

  it('shows the block and the other wording for an unchanged preset whose live state fails the check', async () => {
    serve({
      plan: checked([FAILED], { action: 'unchanged', reason: 'Already up to date.', digest: null, changes: [] }),
    });
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));

    expect(await screen.findByText('Checked against live providers')).toBeInTheDocument();
    expect(screen.getByText('The live check found a problem with what is on OpenRouter now.')).toBeInTheDocument();
    expect(screen.queryByText(/will not be sent/)).toBeNull();
    expect(applyButton()).toBeDisabled();
  });

  it('shows no live check for a plan that was not checked, and Apply is on', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));

    await screen.findByText('Update');
    expect(screen.queryByText('Checked against live providers')).toBeNull();
    expect(applyButton()).toBeEnabled();
  });
});

describe('OpenRouterSection live check when Save sends the update', () => {
  it('sends nothing when the fresh plan fails the live check, and says so with the first error', async () => {
    serve({ plan: checked([SLOW, FAILED, { ...FAILED, message: 'A second problem.' }]) });
    const user = userEvent.setup();
    renderSection();
    await editMinimumScore(user, '80');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(notifications.show).toHaveBeenCalledWith(
        expect.objectContaining({
          message: `Saved. Not sent to OpenRouter: the live check failed: ${FAILED.message}`,
          color: 'yellow',
        }),
      ),
    );
    // it planned from what was saved, by the slug, and stopped there
    expect(sentTo('api/presets/plan')).toEqual({ slug: SLUG });
    expect(calls('api/presets/apply')).toHaveLength(0);
    // the plan on screen carries the block, so the owner can see why
    expect(await screen.findByText('Checked against live providers')).toBeInTheDocument();
    expect(applyButton()).toBeDisabled();
  });

  it('still sends the update when the fresh plan only has warnings', async () => {
    serve({ plan: checked([SLOW, PASSED]) });
    const user = userEvent.setup();
    renderSection();
    await editMinimumScore(user, '80');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(calls('api/presets/apply')).toHaveLength(1));
    expect(sentTo('api/presets/apply')).toEqual({ slug: SLUG, digest: 'abc123' });
    expect(notifications.show).not.toHaveBeenCalledWith(
      expect.objectContaining({ message: expect.stringContaining('Not sent to OpenRouter') as unknown }),
    );
  });
});

describe('OpenRouterSection live check refusing the write', () => {
  /** The plan before the write passed, and the plan after it does not: the listing changed in between. */
  const changedUnderneath = (earlier: number) => (earlier === 0 ? checked([PASSED]) : checked([FAILED]));

  it('shows the 409 message and plans again, so the block appears, when Apply is refused', async () => {
    serve({ plan: changedUnderneath, applyError: { status: 409, detail: REFUSED } });
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));
    await screen.findByText('3 live endpoints, pins verified', { exact: false });
    expect(screen.queryByText(/will not be sent/)).toBeNull();

    await user.click(applyButton());
    await waitFor(() => expect(notifications.show).toHaveBeenCalledWith({ message: REFUSED, color: 'red' }));
    await waitFor(() => expect(calls('api/presets/plan')).toHaveLength(2));
    expect(sentTo('api/presets/plan')).toEqual({ slug: SLUG });
    expect(
      await screen.findByText(/The live check found a problem, so this update will not be sent/),
    ).toBeInTheDocument();
    expect(screen.getByText(FAILED.message, { exact: false })).toBeInTheDocument();
    expect(applyButton()).toBeDisabled();
  });

  it('shows the 409 message and plans again when the update Save sent is refused', async () => {
    serve({ plan: changedUnderneath, applyError: { status: 409, detail: REFUSED } });
    const user = userEvent.setup();
    renderSection();
    await editMinimumScore(user, '80');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(notifications.show).toHaveBeenCalledWith({ message: REFUSED, color: 'red' }));
    expect(calls('api/presets/apply')).toHaveLength(1);
    await waitFor(() => expect(calls('api/presets/plan')).toHaveLength(2));
    expect(
      await screen.findByText(/The live check found a problem, so this update will not be sent/),
    ).toBeInTheDocument();
    expect(applyButton()).toBeDisabled();
  });
});
