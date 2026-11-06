import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { dateTime } from '../../lib/format';
import { calls, fetchMock, item, live, renderSection, SELECTED_KEY, serve } from './presetTestKit';

const MARK = 'Managed by Vibonomics';

const CATALOG = [
  live('vibonomics-best-value', { name: 'Best value', managed: true, version: 4 }),
  live('my-tools', { name: 'My tools', version: 3, models: ['deepseek/ds'], providers: ['deepinfra'] }),
  live('old-one', { name: 'Old one', status: 'archived', version: 9 }),
];

const hoursAgo = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString();

beforeEach(() => {
  fetchMock.mockReset();
  localStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

describe('PresetsPanel list', () => {
  it('lists the presets on the account, with the mark only on the ones Vibonomics manages', async () => {
    serve({ catalog: CATALOG });
    renderSection();
    await screen.findByRole('button', { name: /^Old one/ });
    const panel = within(screen.getByRole('region', { name: 'Presets' }));

    const managed = panel.getByRole('button', { name: /^Best value/ });
    expect(within(managed).getByRole('img', { name: MARK })).toBeInTheDocument();
    expect(managed).toHaveTextContent('vibonomics-best-value');
    expect(managed).toHaveTextContent('v4');

    const handMade = panel.getByRole('button', { name: /^My tools/ });
    expect(within(handMade).queryByRole('img')).toBeNull();
    expect(handMade).toHaveTextContent('my-tools');
    expect(handMade).toHaveTextContent('v3');

    const archived = panel.getByRole('button', { name: /^Old one/ });
    expect(within(archived).getByText('archived')).toBeInTheDocument();
    expect(within(archived).queryByRole('img')).toBeNull();
    expect(panel.getAllByRole('img', { name: MARK })).toHaveLength(1);
  });

  it('pins the global settings first, and puts the presets Vibonomics keeps before the rest', async () => {
    serve({ catalog: CATALOG });
    renderSection();
    await screen.findByRole('button', { name: /^Old one/ });
    const panel = within(screen.getByRole('region', { name: 'Presets' }));
    const names = panel
      .getAllByRole('button')
      .map((b) => b.textContent ?? '')
      .filter((text) => /Global settings|Best value|My tools|Old one/.test(text))
      .map((text) => /Global settings|Best value|My tools|Old one/.exec(text)?.[0]);
    expect(names).toEqual(['Global settings', 'Best value', 'My tools', 'Old one']);
  });

  it('also lists a preset that has settings but is not on OpenRouter yet, marked as not created', async () => {
    serve({ items: [item(), item({ slug: 'vibonomics-draft' })], catalog: CATALOG });
    renderSection();
    const panel = within(await screen.findByRole('region', { name: 'Presets' }));
    const row = await panel.findByRole('button', { name: /^vibonomics-draft/ });
    expect(within(row).getByText('not created yet')).toBeInTheDocument();
    expect(within(row).getByRole('img', { name: MARK })).toBeInTheDocument();
  });

  it('offers a search only for a long list, and filters on the name and the slug', async () => {
    const many = Array.from({ length: 9 }, (_, i) => live(`preset-${i}`, { name: i === 4 ? 'Needle' : `Plain ${i}` }));
    const { unmount } = (serve({ catalog: CATALOG }), renderSection());
    await screen.findByRole('region', { name: 'Presets' });
    expect(screen.queryByRole('textbox', { name: 'Search presets' })).toBeNull();
    unmount();

    serve({ catalog: many });
    const user = userEvent.setup();
    renderSection();
    const search = await screen.findByRole('textbox', { name: 'Search presets' });
    await user.type(search, 'needle');
    const panel = within(screen.getByRole('region', { name: 'Presets' }));
    expect(panel.getByRole('button', { name: /^Needle/ })).toBeInTheDocument();
    expect(panel.queryByRole('button', { name: /^Plain 1/ })).toBeNull();
    expect(panel.getByRole('button', { name: /^Global settings/ })).toBeInTheDocument();

    await user.clear(search);
    await user.type(search, 'preset-7');
    expect(panel.getByRole('button', { name: /^Plain 7/ })).toBeInTheDocument();
    expect(panel.queryByRole('button', { name: /^Needle/ })).toBeNull();

    await user.clear(search);
    await user.type(search, 'zzz');
    expect(panel.getByText(/Nothing matches/)).toBeInTheDocument();
  });

  it('shows how long ago the list was read, with the exact time on hover', async () => {
    const at = hoursAgo(2);
    serve({ catalog: CATALOG, syncedAt: at });
    renderSection();
    const stamp = await screen.findByText(/^Synced 2 h ago/);
    expect(stamp).toHaveAttribute('title', dateTime(at));
  });

  it('says so when the list has never been read', async () => {
    serve({ catalog: [], syncedAt: null, items: [] });
    renderSection();
    expect(await screen.findByText(/No presets yet/)).toBeInTheDocument();
    expect(screen.getByText('Not synced yet')).toBeInTheDocument();
  });

  it('shows what the last sync could not read as a small warning that can be dismissed', async () => {
    serve({ catalog: CATALOG, catalogError: 'Could not read 2 of your presets from OpenRouter.' });
    const user = userEvent.setup();
    renderSection();
    expect(await screen.findByText('Could not read 2 of your presets from OpenRouter.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText('Could not read 2 of your presets from OpenRouter.')).toBeNull();
  });

  it('marks a preset whose settings are not saved', async () => {
    serve({ catalog: CATALOG });
    localStorage.setItem(SELECTED_KEY, JSON.stringify('vibonomics-best-value'));
    const user = userEvent.setup();
    renderSection();
    const row = await screen.findByRole('button', { name: /^Best value/ });
    expect(within(row).queryByText('unsaved')).toBeNull();
    const field = await screen.findByLabelText('Minimum score');
    await user.clear(field);
    await user.type(field, '75');
    expect(within(row).getByText('unsaved')).toBeInTheDocument();
  });

  it('allows no more than twenty presets', async () => {
    const twenty = Array.from({ length: 20 }, (_, i) => item({ slug: `vibonomics-p${i}`, enabled: false }));
    serve({ items: twenty, catalog: [] });
    renderSection();
    const button = await screen.findByRole('button', { name: 'New preset' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', 'Vibonomics manages at most 20 presets');
  });
});

describe('PresetsPanel refresh', () => {
  it('reads the account now, and shows the new list and the new time', async () => {
    serve({
      catalog: CATALOG,
      syncedAt: hoursAgo(5),
      afterSync: {
        catalog: [...CATALOG, live('just-made', { name: 'Just made' })],
        syncedAt: new Date().toISOString(),
      },
    });
    const user = userEvent.setup();
    renderSection();
    expect(await screen.findByText(/^Synced 5 h ago/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Just made/ })).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Refresh presets from OpenRouter' }));
    expect(await screen.findByText('Synced just now')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /^Just made/ })).toBeInTheDocument();
    expect(calls('api/presets/sync', 'POST')).toHaveLength(1);
  });

  it('can be operated from the keyboard', async () => {
    serve({ catalog: CATALOG });
    const user = userEvent.setup();
    renderSection();
    const refresh = await screen.findByRole('button', { name: 'Refresh presets from OpenRouter' });
    refresh.focus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(calls('api/presets/sync', 'POST')).toHaveLength(1));
    refresh.focus();
    await user.keyboard(' ');
    await waitFor(() => expect(calls('api/presets/sync', 'POST')).toHaveLength(2));
  });
});

describe('PresetsPanel selection', () => {
  it('shows the preset that is chosen, and marks the row that is current', async () => {
    serve({ catalog: CATALOG });
    const user = userEvent.setup();
    renderSection();
    const global = await screen.findByRole('button', { name: /^Global settings/ });
    expect(global).toHaveAttribute('aria-current', 'true');
    expect(await screen.findByRole('heading', { name: 'Global settings' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^My tools/ }));
    expect(await screen.findByRole('heading', { name: 'My tools' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^My tools/ })).toHaveAttribute('aria-current', 'true');
    expect(global).not.toHaveAttribute('aria-current');
    expect(screen.queryByRole('heading', { name: 'Global settings' })).toBeNull();

    await user.click(screen.getByRole('button', { name: /^Best value/ }));
    expect(await screen.findByLabelText('Minimum score')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Adopt…' })).toBeNull();
  });

  it('remembers the choice after a reload', async () => {
    serve({ catalog: CATALOG });
    const user = userEvent.setup();
    const first = renderSection();
    await user.click(await screen.findByRole('button', { name: /^My tools/ }));
    await screen.findByRole('heading', { name: 'My tools' });
    first.unmount();

    renderSection();
    expect(await screen.findByRole('heading', { name: 'My tools' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^My tools/ })).toHaveAttribute('aria-current', 'true');
  });

  it('falls back to the global settings when the remembered preset is gone', async () => {
    serve({ catalog: CATALOG });
    localStorage.setItem(SELECTED_KEY, JSON.stringify('deleted-long-ago'));
    renderSection();
    expect(await screen.findByRole('heading', { name: 'Global settings' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Global settings/ })).toHaveAttribute('aria-current', 'true');
  });

  it('chooses a row with Enter or Space from the keyboard, and reaches it with Tab', async () => {
    serve({ catalog: CATALOG });
    const user = userEvent.setup();
    renderSection();
    const target = await screen.findByRole('button', { name: /^My tools/ });
    for (let stops = 0; stops < 12 && document.activeElement !== target; stops += 1) {
      await user.tab();
    }
    expect(target).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(await screen.findByRole('heading', { name: 'My tools' })).toBeInTheDocument();

    const other = screen.getByRole('button', { name: /^Old one/ });
    other.focus();
    await user.keyboard(' ');
    expect(await screen.findByRole('heading', { name: 'Old one' })).toBeInTheDocument();
    expect(other).toHaveAttribute('aria-current', 'true');
  });
});

describe('PresetsPanel for a visitor', () => {
  it('shows only a log-in note, and never asks for the catalogue, when this browser cannot change things', async () => {
    serve({ catalog: CATALOG, canChange: false });
    renderSection();
    expect(await screen.findByText('Log in to manage presets.')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Presets' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'New preset' })).toBeNull();
    expect(calls('api/presets/catalog')).toHaveLength(0);
    expect(calls('api/presets/state')).toHaveLength(0);
  });

  it('shows the log-in note instead of an error when the catalogue answers 401, and does not ask again', async () => {
    serve({ catalog: { status: 401 } });
    renderSection();
    expect(await screen.findByText('Log in to manage presets.')).toBeInTheDocument();
    expect(screen.queryByText(/Not authenticated/)).toBeNull();
    expect(screen.queryByRole('region', { name: 'Presets' })).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 1500));
    expect(calls('api/presets/catalog')).toHaveLength(1);
  });
});
