import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScoringSection } from './ScoringSection';

vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }));

const fetchMock = vi.fn();

const PROFILE = {
  id: 'agentic',
  name: 'Agentic coding',
  input_weight: 20,
  output_weight: 1,
  cache_hit_rate: 0.8,
  task_input_tokens: 600000,
  task_output_tokens: 20000,
};

/** What the server holds for scoring; the two own-test settings are absent unless a test sets them. */
function settingsBody(scoring: Record<string, unknown> = {}) {
  return {
    settings: { scoring: { default_profile: 'agentic', profiles: [PROFILE], ...scoring } },
    sources: [],
    sandbox: { effective_mode: 'disabled', sidecar_url_set: false },
    auth_enabled: true,
    secret_key_from_env: false,
  };
}

function serve(scoring: Record<string, unknown> = {}) {
  let held = settingsBody(scoring);
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    const json = (body: unknown, status = 200) =>
      Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
    if (url === 'api/settings' && init?.method === 'PUT') {
      // The server answers with what it stored.
      held = { ...held, settings: (JSON.parse(init.body as string) as { settings: typeof held.settings }).settings };
      return json(held);
    }
    if (url === 'api/settings') {
      return json(held);
    }
    return json({ detail: `unexpected ${init?.method ?? 'GET'} ${url}` }, 500);
  });
  vi.stubGlobal('fetch', fetchMock);
}

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider>
        <ScoringSection />
      </MantineProvider>
    </QueryClientProvider>,
  );
}

/** The scoring settings the section last PUT, as the server received them. */
function savedScoring(): Record<string, unknown> {
  const put = fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'PUT').at(-1);
  expect(put).toBeDefined();
  return (JSON.parse((put?.[1] as RequestInit).body as string) as { settings: { scoring: Record<string, unknown> } })
    .settings.scoring;
}

// Mantine wraps the description inside the switch's label, so the accessible name carries it too.
const SWITCH = /^Prefer my own test scores/;
const FEWEST = 'Fewest graded tasks';

describe('ScoringSection: preferring your own test scores', () => {
  beforeEach(() => fetchMock.mockReset());
  afterEach(() => vi.unstubAllGlobals());

  it('starts with the switch off and the server defaults, and says what the switch does', async () => {
    serve();
    renderSection();
    const toggle = await screen.findByRole('switch', { name: SWITCH });
    expect(toggle).not.toBeChecked();
    expect(screen.getByLabelText(FEWEST)).toHaveValue('5');
    expect(screen.getByText(/its coding and agentic score IS its own test score/)).toBeInTheDocument();
    expect(screen.getByText(/Intelligence stays as computed/)).toBeInTheDocument();
  });

  it('keeps the minimum disabled while the switch is off, and enables it with the switch', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    const toggle = await screen.findByRole('switch', { name: SWITCH });
    expect(screen.getByLabelText(FEWEST)).toBeDisabled();

    await user.click(toggle);
    expect(screen.getByLabelText(FEWEST)).toBeEnabled();

    await user.click(toggle);
    expect(screen.getByLabelText(FEWEST)).toBeDisabled();
  });

  it('saves the switch and the minimum with the rest of the scoring settings', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('switch', { name: SWITCH }));
    const fewest = screen.getByLabelText(FEWEST);
    await user.clear(fewest);
    await user.type(fewest, '12');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(savedScoring()).toMatchObject({ prefer_own_tests: true, prefer_own_tests_min_tasks: 12 }),
    );
    // the weights and profiles go back as they were
    expect(savedScoring()).toMatchObject({ own_tests_weight: 1, default_profile: 'agentic', profiles: [PROFILE] });
  });

  it('saves a switch turned back off, with the minimum it had', async () => {
    serve({ prefer_own_tests: true, prefer_own_tests_min_tasks: 20 });
    const user = userEvent.setup();
    renderSection();
    const toggle = await screen.findByRole('switch', { name: SWITCH });
    expect(toggle).toBeChecked();
    expect(screen.getByLabelText(FEWEST)).toHaveValue('20');

    await user.click(toggle);
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(savedScoring()).toMatchObject({ prefer_own_tests: false, prefer_own_tests_min_tasks: 20 }),
    );
  });

  it('caps the minimum at 200, the most the server accepts', async () => {
    serve({ prefer_own_tests: true });
    const user = userEvent.setup();
    renderSection();
    const fewest = await screen.findByLabelText(FEWEST);
    await user.clear(fewest);
    await user.type(fewest, '999');
    expect(fewest).toHaveValue('200');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedScoring()).toMatchObject({ prefer_own_tests_min_tasks: 200 }));
  });

  it('raises the minimum to 1 when 0 is typed, since a model needs at least one graded task', async () => {
    serve({ prefer_own_tests: true });
    const user = userEvent.setup();
    renderSection();
    const fewest = await screen.findByLabelText(FEWEST);
    await user.clear(fewest);
    await user.type(fewest, '0');
    expect(fewest).toHaveValue('1');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedScoring()).toMatchObject({ prefer_own_tests_min_tasks: 1 }));
  });

  it('says the own-tests weight has no effect on a model whose own score is preferred', async () => {
    serve();
    renderSection();
    const weight = await screen.findByLabelText('Vibonomics tests');
    expect(weight).toBeInTheDocument();
    expect(screen.getByText(/no effect on a model whose own score is preferred/i)).toBeInTheDocument();
  });
});
