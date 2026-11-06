import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  calls,
  fetchMock,
  item,
  live,
  plan,
  renderSection,
  savedPresets,
  SELECTED_KEY,
  sentTo,
  serve,
} from './presetTestKit';

const SLUG = 'vibonomics-best-value';

function open(slug = SLUG) {
  localStorage.setItem(SELECTED_KEY, JSON.stringify(slug));
}

async function editMinimumScore(user: ReturnType<typeof userEvent.setup>, value: string) {
  const field = await screen.findByLabelText('Minimum score');
  await user.clear(field);
  await user.type(field, value);
}

describe('OpenRouterSection preview and apply', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    localStorage.clear();
    open();
  });
  afterEach(() => vi.unstubAllGlobals());

  it('previews the plan and, once writing is allowed, applies exactly the plan it showed', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));
    expect(await screen.findByText('Switches deepseek/old to Flash, 31% more value.')).toBeInTheDocument();
    expect(sentTo('api/presets/plan')).toEqual({ slug: SLUG });

    await user.click(screen.getByRole('button', { name: 'Apply this plan' }));
    await waitFor(() => expect(calls('api/presets/apply')).toHaveLength(1));
    expect(sentTo('api/presets/apply')).toEqual({ slug: SLUG, digest: 'abc123' });
    // the view is refreshed from a new dry run once the write is done, and the catalogue is read again
    await waitFor(() => expect(calls('api/presets/plan')).toHaveLength(2));
    expect(sentTo('api/presets/plan')).toEqual({ slug: SLUG });
    await waitFor(() => expect(calls('api/presets/sync')).toHaveLength(1));
  });

  it('will not apply while writing is switched off, but still previews', async () => {
    serve({ items: [item({ enabled: false })] });
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));
    expect(await screen.findByText('Update')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply this plan' })).toBeDisabled();
    expect(screen.getByText(/Turn on .*Let Vibonomics write this preset/)).toBeInTheDocument();
    expect(calls('api/presets/apply')).toHaveLength(0);
  });

  it('offers nothing to apply when the plan is blocked', async () => {
    serve({
      plan: plan({ action: 'blocked', reason: 'No OpenRouter model passes the filters.', digest: null, request: null }),
    });
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));
    expect(await screen.findByText('No OpenRouter model passes the filters.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply this plan' })).toBeDisabled();
  });

  it('previews the saved settings by name, and sends no settings, when nothing was changed', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));
    await screen.findByText('Update');
    expect(sentTo('api/presets/plan')).toEqual({ slug: SLUG });
  });

  it('previews changes that are not saved, and will not apply that preview', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    await editMinimumScore(user, '80');
    expect(screen.getByText(/You have unsaved changes\. Preview tries them out/)).toBeInTheDocument();

    const button = screen.getByRole('button', { name: 'Preview' });
    expect(button).toBeEnabled(); // an unsaved change no longer stops a preview
    await user.click(button);
    expect(await screen.findByText('Unsaved settings')).toBeInTheDocument();
    const sent = sentTo('api/presets/plan') as { settings: { slug: string; min_score: number }; slug?: string };
    expect(sent.settings.min_score).toBe(80);
    expect(sent.settings.slug).toBe(SLUG);
    expect(sent.slug).toBeUndefined();
    expect(screen.getByRole('button', { name: 'Apply this plan' })).toBeDisabled();
    expect(calls('api/settings', 'PUT')).toHaveLength(0);
  });

  it('asks for a preview again once the previewed changes are saved, before it will apply', async () => {
    serve({ items: [item({ enabled: false })] });
    const user = userEvent.setup();
    renderSection();
    await editMinimumScore(user, '80');
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    await screen.findByText('Unsaved settings');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/made from settings that were not saved/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply this plan' })).toBeDisabled();
  });

  it('shows a plan only on the preset it was made for', async () => {
    serve({
      items: [item(), item({ slug: 'vibonomics-second' })],
      catalog: [
        live(SLUG, { managed: true, name: 'Best value' }),
        live('vibonomics-second', { managed: true, name: 'Second' }),
      ],
    });
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));
    await screen.findByText('Switches deepseek/old to Flash, 31% more value.');

    await user.click(screen.getByRole('button', { name: /Second/ }));
    expect(await screen.findByRole('heading', { name: 'Second' })).toBeInTheDocument();
    expect(screen.queryByText('Switches deepseek/old to Flash, 31% more value.')).toBeNull();
    expect(screen.getByRole('button', { name: 'Apply this plan' })).toBeDisabled();
  });

  it("shows this preset's last run, and nothing before it has run", async () => {
    serve({
      state: {
        [SLUG]: {
          last_run_at: new Date(Date.now() - 3_600_000).toISOString(),
          last_trigger: 'schedule',
          last_status: 'applied',
          last_message: 'Updated to version 5.',
          version: 5,
          model_id: 'deepseek/flash',
          applied_at: new Date(Date.now() - 3_600_000).toISOString(),
        },
      },
    });
    renderSection();
    expect(await screen.findByText(/\(schedule\): applied/)).toBeInTheDocument();
    expect(screen.getByText('Updated to version 5.')).toBeInTheDocument();
    expect(screen.getByText(/Last written: vibonomics-best-value v5 on deepseek\/flash/)).toBeInTheDocument();
  });

  it('has no last run card for a preset that has not run', async () => {
    serve({ state: { 'someone-else': { last_run_at: '2026-11-04T10:00:00Z', last_status: 'applied' } } });
    renderSection();
    await screen.findByRole('button', { name: 'Preview' });
    expect(screen.queryByRole('heading', { name: 'Last run' })).toBeNull();
  });
});

describe('OpenRouterSection save', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    localStorage.clear();
    open();
  });
  afterEach(() => vi.unstubAllGlobals());

  it('sends the update for the preset to OpenRouter when settings are saved with writing allowed, and says so first', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    await editMinimumScore(user, '80');
    expect(screen.getByText('Saving also sends the update to OpenRouter.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(calls('api/presets/apply')).toHaveLength(1));
    expect(sentTo('api/presets/apply')).toEqual({ slug: SLUG, digest: 'abc123' });
    // it planned from what was saved, by the slug, never from the draft
    expect(sentTo('api/presets/plan')).toEqual({ slug: SLUG });
    const order = fetchMock.mock.calls.map(([u, init]) => `${(init as RequestInit | undefined)?.method ?? 'GET'} ${u}`);
    expect(order.indexOf('PUT api/settings')).toBeLessThan(order.indexOf('POST api/presets/plan'));
    expect(order.indexOf('POST api/presets/plan')).toBeLessThan(order.indexOf('POST api/presets/apply'));
  });

  it('only saves, and sends nothing to OpenRouter, while writing is switched off', async () => {
    serve({ items: [item({ enabled: false })] });
    const user = userEvent.setup();
    renderSection();
    await editMinimumScore(user, '80');
    expect(screen.queryByText('Saving also sends the update to OpenRouter.')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.items[0].min_score).toBe(80));
    expect(calls('api/presets/plan')).toHaveLength(0);
    expect(calls('api/presets/apply')).toHaveLength(0);
  });

  it('sends nothing when the saved settings leave the preset as it is, and says that', async () => {
    serve({
      plan: plan({ action: 'unchanged', reason: 'Already up to date.', request: null, changes: [], digest: null }),
    });
    const user = userEvent.setup();
    renderSection();
    await editMinimumScore(user, '80');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls('api/presets/plan')).toHaveLength(1));
    expect(await screen.findAllByText('Already up to date.')).not.toHaveLength(0);
    expect(calls('api/presets/apply')).toHaveLength(0);
  });

  it('sends the update only for the preset that was edited, not for its neighbours saved with it', async () => {
    serve({
      items: [item(), item({ slug: 'vibonomics-second' })],
      catalog: [live(SLUG, { managed: true }), live('vibonomics-second', { managed: true })],
    });
    const user = userEvent.setup();
    renderSection();
    await editMinimumScore(user, '80');
    await user.click(screen.getByRole('button', { name: /^vibonomics-second/ }));
    await screen.findByRole('heading', { name: 'vibonomics-second' });
    // the neighbour is unchanged, so saving from its page saves the edit but sends nothing for it
    expect(screen.queryByText('Saving also sends the update to OpenRouter.')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.items.map((i) => i.min_score)).toEqual([80, 60]));
    expect(calls('api/presets/plan')).toHaveLength(0);
  });
});
