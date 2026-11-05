import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PresetList, PresetPlan } from '../../api/types';
import { OpenRouterSection } from './OpenRouterSection';
import { PresetPlanView } from './PresetPlanView';

function plan(over: Partial<PresetPlan> = {}): PresetPlan {
  const pick = {
    model_id: 'deepseek/flash',
    name: 'Flash',
    score: 71.4,
    task_cost: 0.055,
    value: 1298,
    discount: 0.3,
  };
  return {
    action: 'update',
    reason: 'Switches deepseek/old to Flash, 31% more value.',
    slug: 'vibonomics-best-value',
    enabled: true,
    auto_apply: false,
    draft: false,
    generated_at: '2026-11-04T10:00:00Z',
    profile: 'agentic',
    task: { input_tokens: 600000, output_tokens: 20000, cache_hit_rate: 0.8 },
    considered: 42,
    pick,
    picks: [pick],
    kept_current: false,
    providers: [
      {
        tag: 'deepinfra/fp8',
        provider: 'DeepInfra',
        input: 0.1,
        output: 0.4,
        discount: 0.3,
        quantization: 'fp8',
        task_cost: 0.068,
        models: ['deepseek/flash'],
      },
    ],
    runners_up: [{ model_id: 'acme/steady', name: 'Steady', score: 70, task_cost: 0.07, value: 1100, discount: null }],
    current_version: 4,
    current_config: { model: 'deepseek/old' },
    versions: [
      { version: 4, created_at: '2026-11-04T07:36:33Z', model: 'deepseek/old', providers: ['deepinfra'], live: true },
      { version: 3, created_at: '2026-11-03T07:36:33Z', model: 'deepseek/older', providers: [], live: false },
    ],
    config: { model: 'deepseek/flash', provider: { only: ['deepinfra/fp8'] } },
    changes: [{ path: 'model', before: '"deepseek/old"', after: '"deepseek/flash"' }],
    request: {
      method: 'POST',
      url: 'https://openrouter.ai/api/v1/presets/vibonomics-best-value/chat/completions',
      body: { model: 'deepseek/flash', messages: [{ role: 'user', content: 'Vibonomics preset update' }] },
    },
    digest: 'abc123',
    warnings: ['No provider data for one runner-up.'],
    state: { owned: ['vibonomics-best-value'] },
    ...over,
  };
}

describe('PresetPlanView', () => {
  it('shows what would happen, the live version and the versions behind it', () => {
    render(
      <MantineProvider>
        <PresetPlanView plan={plan()} />
      </MantineProvider>,
    );
    expect(screen.getByText('Update')).toBeInTheDocument();
    expect(screen.getByText('Switches deepseek/old to Flash, 31% more value.')).toBeInTheDocument();
    expect(screen.getByText('Live version').parentElement).toHaveTextContent('v4');
    expect(screen.getByText('@preset/vibonomics-best-value')).toBeInTheDocument();
    expect(screen.getByText('No provider data for one runner-up.')).toBeInTheDocument();
    const versions = within(screen.getByRole('table', { name: 'Preset versions' }));
    expect(versions.getByText('v3')).toBeInTheDocument();
    expect(versions.getByText('live')).toBeInTheDocument();
    expect(within(screen.getByRole('table', { name: 'Changes' })).getByText('"deepseek/flash"')).toBeInTheDocument();
  });

  it('keeps the API call out of the way until it is asked for', async () => {
    const user = userEvent.setup();
    render(
      <MantineProvider>
        <PresetPlanView plan={plan()} />
      </MantineProvider>,
    );
    const toggle = screen.getByRole('button', { name: 'View API call' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByLabelText('Request body')).not.toBeVisible();

    await user.click(toggle);
    expect(screen.getByRole('button', { name: 'Hide API call' })).toHaveAttribute('aria-expanded', 'true');
    await waitFor(() => expect(screen.getByLabelText('Request body')).toBeVisible());
    expect(screen.getByLabelText('Request body')).toHaveTextContent('"model": "deepseek/flash"');
    expect(screen.getByLabelText('Request body')).toHaveTextContent('"messages"');

    await user.click(screen.getByRole('button', { name: 'Hide API call' }));
    await waitFor(() => expect(screen.getByLabelText('Request body')).not.toBeVisible());
  });

  it('says a preset that does not exist yet is not created, and offers no API call when blocked', () => {
    render(
      <MantineProvider>
        <PresetPlanView
          plan={plan({
            action: 'blocked',
            reason: 'A preset called vibonomics-best-value already exists.',
            current_version: null,
            versions: [],
            request: null,
            changes: [],
            digest: null,
          })}
        />
      </MantineProvider>,
    );
    expect(screen.getByText('Blocked')).toBeInTheDocument();
    expect(screen.getByText('Not created yet')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'View API call' })).toBeNull();
    expect(screen.queryByRole('table', { name: 'Preset versions' })).toBeNull();
  });

  it('lists several models in order, and the models each provider is there for', () => {
    const second = { model_id: 'z-ai/glm', name: 'GLM', score: 66, task_cost: 0.07, value: 940, discount: null };
    const first = plan().picks[0];
    render(
      <MantineProvider>
        <PresetPlanView
          plan={plan({
            picks: [first, second],
            providers: [
              { ...plan().providers[0], models: ['deepseek/flash', 'z-ai/glm'] },
              {
                tag: 'novita/fp4',
                provider: 'Novita',
                input: 0.08,
                output: 0.35,
                discount: 0,
                quantization: 'fp4',
                task_cost: 0.055,
                models: ['z-ai/glm'],
              },
            ],
          })}
        />
      </MantineProvider>,
    );
    const models = within(screen.getByRole('table', { name: 'Models' }));
    expect(models.getAllByRole('row').map((r) => r.textContent)).toEqual([
      '#ModelScorePer taskValue',
      '1Flash deepseek/flash71.4$0.0551,298',
      '2GLM z-ai/glm66$0.07940',
    ]);
    const providers = within(screen.getByRole('table', { name: 'Providers' }));
    expect(providers.getByText('Flash, GLM')).toBeInTheDocument();
    expect(providers.getByText('GLM')).toBeInTheDocument();
  });
});

const fetchMock = vi.fn();

function settingsBody(enabled: boolean, slug = 'vibonomics-best-value', extra: Record<string, unknown> = {}) {
  return {
    settings: {
      scoring: {
        default_profile: 'agentic',
        profiles: [
          {
            id: 'agentic',
            name: 'Agentic coding',
            input_weight: 20,
            output_weight: 1,
            cache_hit_rate: 0.8,
            task_input_tokens: 600000,
            task_output_tokens: 20000,
          },
        ],
      },
      presets: { enabled, slug, ...extra },
    },
    sources: [],
    sandbox: { effective_mode: 'disabled', sidecar_url_set: false },
    auth_enabled: true,
    secret_key_from_env: false,
  };
}

const LISTED: PresetList = {
  error: null,
  presets: [
    {
      slug: 'my-tools',
      name: 'My tools',
      version: 3,
      updated_at: '2026-11-03T00:00:00Z',
      models: ['deepseek/ds'],
      providers: ['deepinfra'],
    },
    { slug: 'plain', name: 'plain', version: 1, updated_at: '2026-11-01T00:00:00Z', models: [], providers: [] },
  ],
};

function serve(
  opts: {
    enabled?: boolean;
    plan?: PresetPlan;
    list?: PresetList | { status: number };
    saved?: Record<string, unknown>;
  } = {},
) {
  const enabled = opts.enabled ?? true;
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    const json = (body: unknown, status = 200) =>
      Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
    if (url === 'api/settings' && init?.method === 'PUT') {
      const sent = JSON.parse(init.body as string) as { settings: { presets: { slug: string } } };
      return json(settingsBody(enabled, sent.settings.presets.slug));
    }
    if (url === 'api/settings') {
      return json(settingsBody(enabled, undefined, opts.saved));
    }
    if (url === 'api/presets/state') {
      return json({ owned: [], last_run_at: null });
    }
    if (url === 'api/presets/list') {
      const list = opts.list ?? LISTED;
      return 'status' in list ? json({ detail: 'nope' }, list.status) : json(list);
    }
    if (url === 'api/presets/plan') {
      return json(opts.plan ?? plan());
    }
    if (url === 'api/presets/apply') {
      return json({
        applied: true,
        message: 'Updated vibonomics-best-value as version 5.',
        version: 5,
        unsaved: [],
        state: { owned: [] },
      });
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
        <OpenRouterSection />
      </MantineProvider>
    </QueryClientProvider>,
  );
}

const calls = (url: string) => fetchMock.mock.calls.filter(([u]) => u === url);

/** The body of the last preview request. */
function previewBody(): { settings?: Record<string, unknown> } {
  const sent = calls('api/presets/plan').at(-1);
  return JSON.parse((sent?.[1] as RequestInit).body as string) as { settings?: Record<string, unknown> };
}

/** The presets settings the section last PUT, as the server received them. */
function savedPresets(): Record<string, unknown> | undefined {
  const put = calls('api/settings').find(([, init]) => (init as RequestInit | undefined)?.method === 'PUT');
  if (put === undefined) {
    return undefined;
  }
  return (JSON.parse((put[1] as RequestInit).body as string) as { settings: { presets: Record<string, unknown> } })
    .settings.presets;
}

describe('OpenRouterSection', () => {
  beforeEach(() => fetchMock.mockReset());
  afterEach(() => vi.unstubAllGlobals());

  it('previews the plan and, once writing is allowed, applies exactly the plan it showed', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));
    expect(await screen.findByText('Switches deepseek/old to Flash, 31% more value.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Apply this plan' }));
    await waitFor(() => {
      const applied = calls('api/presets/apply')[0];
      expect(applied).toBeDefined();
      expect(JSON.parse((applied[1] as RequestInit).body as string)).toEqual({ digest: 'abc123' });
    });
    // the view is refreshed from a new dry run once the write is done
    await waitFor(() => expect(calls('api/presets/plan')).toHaveLength(2));
  });

  it('will not apply while writing is switched off, but still previews', async () => {
    serve({ enabled: false });
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

  it('takes a name and adds the prefix itself', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    const name = await screen.findByLabelText('Preset name');
    expect(name).toHaveValue('best-value'); // the saved slug, without the prefix that is always there
    await user.clear(name);
    expect(screen.getByText('Give the preset a name')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();

    await user.type(name, 'My fast preset');
    expect(screen.getAllByText(/@preset\/vibonomics-my-fast-preset/, { selector: 'code' }).length).toBeGreaterThan(1);
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.slug).toBe('My fast preset'));
  });

  it('lists the presets on the account to start from, and saves the one chosen', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    const picker = await screen.findByRole('combobox', { name: 'Start from preset (optional)' });
    await user.click(picker);
    expect(await screen.findByText('deepseek/ds · only deepinfra · v3')).toBeInTheDocument();
    await user.click(screen.getByText('My tools (my-tools)'));
    expect(picker).toHaveValue('My tools (my-tools)');

    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.template_slug).toBe('my-tools'));
  });

  it('falls back to typing a slug, and says why, when OpenRouter cannot list the presets', async () => {
    serve({ list: { error: 'Add an OpenRouter API key under Settings, Providers first.', presets: [] } });
    renderSection();
    const field = await screen.findByLabelText(/Start from preset/);
    expect(field).toHaveAttribute('placeholder', 'my-preset');
    expect(screen.getByText(/The list of your presets is unavailable: Add an OpenRouter API key/)).toBeInTheDocument();
  });

  it.each([
    ['Models listed', 'max_models', 3],
    ['Providers listed', 'max_providers', 10],
  ])('saves at most the limit for %s', async (label, key, limit) => {
    serve();
    const user = userEvent.setup();
    renderSection();
    const field = await screen.findByLabelText(label);
    await user.clear(field);
    await user.type(field, '15');
    await user.tab();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.[key]).toBe(limit));
  });

  it('saves three models when three are asked for, the most OpenRouter takes in a preset', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    const field = await screen.findByLabelText('Models listed');
    expect(field).toHaveAccessibleDescription(/1 to 3/);
    await user.clear(field);
    await user.type(field, '3');
    await user.tab();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.max_models).toBe(3));
  });

  it('takes half an hour as the schedule', async () => {
    serve({ saved: { auto_apply: true, interval_hours: 6 } });
    const user = userEvent.setup();
    renderSection();
    const field = await screen.findByLabelText('Every');
    await user.tripleClick(field);
    await user.keyboard('0.5');
    await user.tab();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.interval_hours).toBe(0.5));
  });

  it('sends the update to OpenRouter when settings are saved with writing allowed, and says so beforehand', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    const field = await screen.findByLabelText('Minimum score');
    await user.clear(field);
    await user.type(field, '80');
    expect(screen.getByText('Saving also sends the update to OpenRouter.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(calls('api/presets/apply')).toHaveLength(1));
    expect(JSON.parse((calls('api/presets/apply')[0][1] as RequestInit).body as string)).toEqual({ digest: 'abc123' });
    // it planned from what was saved, never from the draft
    expect(previewBody()).toEqual({});
    const order = fetchMock.mock.calls.map(([u, init]) => `${(init as RequestInit | undefined)?.method ?? 'GET'} ${u}`);
    expect(order.indexOf('PUT api/settings')).toBeLessThan(order.indexOf('POST api/presets/plan'));
    expect(order.indexOf('POST api/presets/plan')).toBeLessThan(order.indexOf('POST api/presets/apply'));
  });

  it('only saves, and sends nothing to OpenRouter, while writing is switched off', async () => {
    serve({ enabled: false });
    const user = userEvent.setup();
    renderSection();
    const field = await screen.findByLabelText('Minimum score');
    await user.clear(field);
    await user.type(field, '80');
    expect(screen.queryByText('Saving also sends the update to OpenRouter.')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.min_score).toBe(80));
    expect(calls('api/presets/plan')).toHaveLength(0);
    expect(calls('api/presets/apply')).toHaveLength(0);
  });

  it('sends nothing when the saved settings leave the preset as it is, and says that', async () => {
    serve({ plan: plan({ action: 'unchanged', reason: 'Already up to date.', request: null, changes: [] }) });
    const user = userEvent.setup();
    renderSection();
    const field = await screen.findByLabelText('Minimum score');
    await user.clear(field);
    await user.type(field, '80');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls('api/presets/plan')).toHaveLength(1));
    expect(await screen.findAllByText('Already up to date.')).not.toHaveLength(0);
    expect(calls('api/presets/apply')).toHaveLength(0);
  });

  it('previews the saved settings, and says nothing of settings, when nothing was changed', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));
    await screen.findByText('Update');
    expect(previewBody()).toEqual({});
  });

  it('previews changes that are not saved, and will not apply that preview', async () => {
    serve({ plan: plan({ draft: true }) });
    const user = userEvent.setup();
    renderSection();
    const field = await screen.findByLabelText('Minimum score');
    await user.clear(field);
    await user.type(field, '80');
    expect(screen.getByText(/You have unsaved changes\. Preview tries them out/)).toBeInTheDocument();

    const button = screen.getByRole('button', { name: 'Preview' });
    expect(button).toBeEnabled(); // an unsaved change no longer stops a preview
    await user.click(button);
    expect(await screen.findByText('Unsaved settings')).toBeInTheDocument();
    expect(previewBody().settings?.min_score).toBe(80);
    expect(screen.getByRole('button', { name: 'Apply this plan' })).toBeDisabled();
    expect(
      calls('api/settings').filter(([, init]) => (init as RequestInit | undefined)?.method === 'PUT'),
    ).toHaveLength(0);
  });

  it('asks for a preview again once the previewed changes are saved, before it will apply', async () => {
    serve({ plan: plan({ draft: true }) });
    const user = userEvent.setup();
    renderSection();
    const field = await screen.findByLabelText('Minimum score');
    await user.clear(field);
    await user.type(field, '80');
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    await screen.findByText('Unsaved settings');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/made from settings that were not saved/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply this plan' })).toBeDisabled();
  });

  it('does not offer a preview with no name to preview', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    await user.clear(await screen.findByLabelText('Preset name'));
    expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
  });

  it('saves a cost limit and a value floor, and saves none when they are cleared', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    const cost = await screen.findByLabelText('Maximum cost per task');
    const value = screen.getByLabelText('Minimum value');
    expect(cost).toHaveValue('');
    expect(value).toHaveValue('');
    await user.type(cost, '0.05');
    await user.type(value, '2000');
    await user.tab();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.max_task_cost).toBe(0.05));
    expect(savedPresets()?.min_value).toBe(2000);
  });

  it('shows saved limits and clears them to no limit', async () => {
    serve({ saved: { max_task_cost: 0.05, min_value: 2000 } });
    const user = userEvent.setup();
    renderSection();
    const cost = await screen.findByLabelText('Maximum cost per task');
    expect(cost).toHaveValue('$0.05');
    expect(screen.getByLabelText('Minimum value')).toHaveValue('2,000');
    await user.clear(cost);
    await user.clear(screen.getByLabelText('Minimum value'));
    await user.tab();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.max_task_cost).toBeNull());
    expect(savedPresets()?.min_value).toBeNull();
  });
});
