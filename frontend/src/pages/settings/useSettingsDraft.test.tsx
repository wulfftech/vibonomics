import { MantineProvider } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../api/client';
import { keys } from '../../api/keys';
import type { SettingsIn, SettingsOut } from '../../api/types';
import { SettingsPage } from '../SettingsPage';
import { GeneralSection } from './GeneralSection';
import { RefreshSection } from './RefreshSection';
import { ScoringSection } from './ScoringSection';

const mocks = vi.hoisted(() => ({
  get: vi.fn<(path: string) => Promise<unknown>>(),
  put: vi.fn<(path: string, options: { body: SettingsIn }) => Promise<unknown>>(),
}));
vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }));
vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  api: { get: mocks.get, put: mocks.put },
}));

const PROFILE = {
  id: 'agentic',
  name: 'Agentic',
  input_weight: 3,
  output_weight: 1,
  cache_hit_rate: 0.5,
  task_input_tokens: 30000,
  task_output_tokens: 10000,
};

function settingsOut(general: { instance_name: string; hide_inactive_models: boolean }): SettingsOut {
  return {
    settings: {
      general,
      scoring: {
        intelligence_weight: 0.3,
        coding_weight: 0.35,
        agentic_weight: 0.35,
        own_tests_weight: 1,
        free_model_cost: 0.01,
        default_profile: 'agentic',
        profiles: [PROFILE],
      },
    },
    sources: [],
    auth_enabled: false,
    secret_key_from_env: false,
  } as unknown as SettingsOut;
}

/** What the server holds; the section reads it through the mocked API, as another tab's save would change it. */
let server: SettingsOut;
let client: QueryClient;

function mount(section: React.ReactNode) {
  render(
    <MantineProvider>
      <QueryClientProvider client={client}>{section}</QueryClientProvider>
    </MantineProvider>,
  );
}

/** Another tab saves: the event stream tells this one to refetch, and the new copy arrives. */
async function savedElsewhere(next: SettingsOut) {
  server = next;
  await act(async () => {
    await client.invalidateQueries({ queryKey: keys.settings });
  });
}

function sentSettings() {
  const call = mocks.put.mock.calls.at(-1);
  if (call === undefined) {
    throw new Error('nothing was saved');
  }
  return call[1].body.settings;
}

beforeEach(() => {
  server = settingsOut({ instance_name: 'Vibonomics', hide_inactive_models: true });
  mocks.get.mockReset();
  mocks.put.mockReset();
  vi.mocked(notifications.show).mockReset();
  mocks.get.mockImplementation((path) =>
    Promise.resolve(
      path === '/api/settings'
        ? server
        : path === '/api/auth/status'
          ? { enabled: false, authenticated: false, open_read: false }
          : { version: '0.0.0', url_base: '' },
    ),
  );
  mocks.put.mockImplementation((_path, { body }) => {
    server = { ...server, settings: body.settings };
    return Promise.resolve(server);
  });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

describe('the Refresh section', () => {
  it('saves its own flag over the latest general settings, not over a stale copy of them', async () => {
    const user = userEvent.setup();
    mount(<RefreshSection />);
    await user.click(await screen.findByRole('switch', { name: /Hide inactive models/ }));

    await savedElsewhere(settingsOut({ instance_name: 'Renamed in another tab', hide_inactive_models: true }));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mocks.put).toHaveBeenCalledTimes(1));
    expect(sentSettings().general).toEqual({ instance_name: 'Renamed in another tab', hide_inactive_models: false });
  });

  it('does not count a rename elsewhere as an edit made here', async () => {
    const user = userEvent.setup();
    mount(<RefreshSection />);
    const hide = await screen.findByRole('switch', { name: /Hide inactive models/ });
    await user.click(hide);
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();

    await savedElsewhere(settingsOut({ instance_name: 'Renamed in another tab', hide_inactive_models: true }));
    await user.click(hide);

    expect(hide).toBeChecked();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Discard changes' })).not.toBeInTheDocument();
  });
});

describe('the General section', () => {
  it('saves the name without undoing the hide-inactive flag that another tab changed', async () => {
    const user = userEvent.setup();
    mount(<GeneralSection />);
    const name = await screen.findByLabelText('Instance name');
    await user.clear(name);
    await user.type(name, 'Homelab');

    await savedElsewhere(settingsOut({ instance_name: 'Vibonomics', hide_inactive_models: false }));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mocks.put).toHaveBeenCalledTimes(1));
    expect(sentSettings().general).toEqual({ instance_name: 'Homelab', hide_inactive_models: false });
  });

  it('keeps Discard when the name is blank, but will not save it', async () => {
    const user = userEvent.setup();
    mount(<GeneralSection />);
    const name = await screen.findByLabelText('Instance name');
    await user.clear(name);

    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(screen.getByLabelText('Instance name')).toHaveValue('Vibonomics');
  });

  it('reports a failed save once', async () => {
    mocks.put.mockRejectedValue(new ApiError(422, 'instance_name: too long'));
    const user = userEvent.setup();
    mount(<GeneralSection />);
    await user.type(await screen.findByLabelText('Instance name'), '!');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(notifications.show).toHaveBeenCalled());
    expect(notifications.show).toHaveBeenCalledTimes(1);
    expect(notifications.show).toHaveBeenCalledWith({ color: 'red', message: 'instance_name: too long' });
  });
});

describe('the Scoring section', () => {
  it('keeps Discard when a profile has no name, but will not save it', async () => {
    const user = userEvent.setup();
    mount(<ScoringSection />);
    await user.clear(await screen.findByLabelText('Profile name'));

    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(screen.getByLabelText('Profile name')).toHaveValue('Agentic');
  });
});

describe('the Settings page', () => {
  function mountPage() {
    const router = createMemoryRouter([{ path: '/settings/:section', element: <SettingsPage /> }], {
      initialEntries: ['/settings/general'],
    });
    mount(<RouterProvider router={router} />);
    return router;
  }

  it('asks before a section tab throws away an edit, and not before it moves on with nothing to lose', async () => {
    const user = userEvent.setup();
    const router = mountPage();
    await user.type(await screen.findByLabelText('Instance name'), '!');

    await user.click(screen.getByRole('radio', { name: 'Refresh' }));
    const dialog = await screen.findByRole('dialog', { name: 'Discard unsaved changes?' });
    expect(router.state.location.pathname).toBe('/settings/general');
    await user.click(within(dialog).getByRole('button', { name: 'Keep editing' }));
    expect(screen.getByLabelText('Instance name')).toHaveValue('Vibonomics!');

    await user.click(screen.getByRole('button', { name: 'Discard changes' }));
    await user.click(screen.getByRole('radio', { name: 'Refresh' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/settings/refresh'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
