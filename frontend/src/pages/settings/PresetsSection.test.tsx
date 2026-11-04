import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PresetPlan } from '../../api/types';
import { PresetPlanView } from './PresetPlanView';
import { PresetsSection } from './PresetsSection';

function plan(over: Partial<PresetPlan> = {}): PresetPlan {
  return {
    action: 'update',
    reason: 'Switches deepseek/old to Flash, 31% more value.',
    slug: 'vibonomics-best-value',
    enabled: true,
    auto_apply: false,
    generated_at: '2026-11-04T10:00:00Z',
    profile: 'agentic',
    task: { input_tokens: 600000, output_tokens: 20000, cache_hit_rate: 0.8 },
    considered: 42,
    pick: {
      model_id: 'deepseek/flash',
      name: 'Flash',
      score: 71.4,
      task_cost: 0.055,
      value: 1298,
      discount: 0.3,
    },
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
  it('shows what would happen, the live version, and the exact request', () => {
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
    expect(screen.getByLabelText('Request body')).toHaveTextContent('"model": "deepseek/flash"');
    expect(screen.getByLabelText('Request body')).toHaveTextContent('"messages"');
    const versions = within(screen.getByRole('table', { name: 'Preset versions' }));
    expect(versions.getByText('v3')).toBeInTheDocument();
    expect(versions.getByText('live')).toBeInTheDocument();
    expect(within(screen.getByRole('table', { name: 'Changes' })).getByText('"deepseek/flash"')).toBeInTheDocument();
  });

  it('says a preset that does not exist yet is not created, and shows no request when blocked', () => {
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
    expect(screen.queryByLabelText('Request body')).toBeNull();
    expect(screen.queryByRole('table', { name: 'Preset versions' })).toBeNull();
  });
});

const fetchMock = vi.fn();

function settingsBody(enabled: boolean) {
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
      presets: { enabled },
    },
    sources: [],
    sandbox: { effective_mode: 'disabled', sidecar_url_set: false },
    auth_enabled: true,
    secret_key_from_env: false,
  };
}

function serve(opts: { enabled: boolean; plan?: PresetPlan }) {
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    const json = (body: unknown, status = 200) =>
      Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
    if (url === 'api/settings') {
      return json(settingsBody(opts.enabled));
    }
    if (url === 'api/presets/state') {
      return json({ owned: [], last_run_at: null });
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
        <PresetsSection />
      </MantineProvider>
    </QueryClientProvider>,
  );
}

describe('PresetsSection', () => {
  beforeEach(() => fetchMock.mockReset());
  afterEach(() => vi.unstubAllGlobals());

  it('previews the plan and, once writing is allowed, applies exactly the plan it showed', async () => {
    serve({ enabled: true });
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));
    expect(await screen.findByText('Switches deepseek/old to Flash, 31% more value.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Apply this plan' }));
    await waitFor(() => {
      const applied = fetchMock.mock.calls.find(([url]) => url === 'api/presets/apply');
      expect(applied).toBeDefined();
      expect(JSON.parse((applied?.[1] as RequestInit).body as string)).toEqual({ digest: 'abc123' });
    });
    // the view is refreshed from a new dry run once the write is done
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === 'api/presets/plan')).toHaveLength(2));
  });

  it('will not apply while writing is switched off, but still previews', async () => {
    serve({ enabled: false });
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));
    expect(await screen.findByText('Update')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply this plan' })).toBeDisabled();
    expect(screen.getByText(/Turn on .*Let Vibonomics write this preset/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => url === 'api/presets/apply')).toBe(false);
  });

  it('offers nothing to apply when the plan is blocked', async () => {
    serve({
      enabled: true,
      plan: plan({ action: 'blocked', reason: 'No OpenRouter model passes the filters.', digest: null, request: null }),
    });
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));
    expect(await screen.findByText('No OpenRouter model passes the filters.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply this plan' })).toBeDisabled();
  });
});
