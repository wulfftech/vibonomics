import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { PresetPlan } from '../../api/types';
import { PresetPlanView } from './PresetPlanView';

const FLASH = { model_id: 'deepseek/flash', name: 'Flash', score: 71.4, task_cost: 0.055, value: 1298, discount: 0.3 };
const GLM = { model_id: 'z-ai/glm:free', name: 'GLM', score: 66, task_cost: 0.07, value: 940, discount: null };

function plan(over: Partial<PresetPlan> = {}): PresetPlan {
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
    pick: FLASH,
    picks: [FLASH],
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
      { version: 3, created_at: '2026-11-03T07:36:33Z', model: 'dots-studio/older:free', providers: [], live: false },
    ],
    config: { model: 'deepseek/flash' },
    changes: [],
    request: null,
    digest: 'abc123',
    warnings: [],
    route: 'fallback',
    sort: null,
    excluded: { models: 0, providers: [] },
    own_tests_preferred: false,
    passthrough: {},
    managed_keys: ['model', 'models', 'provider.only'],
    validation: [],
    validated_at: null,
    valid: true,
    state: { owned: ['vibonomics-best-value'], slugs: {} },
    ...over,
  };
}

function view(p: PresetPlan) {
  return render(
    <MantineProvider>
      <PresetPlanView plan={p} />
    </MantineProvider>,
  );
}

/** A link opens the page in a new tab without handing it the opener or the referrer. */
function expectOpensOpenRouter(link: HTMLElement, href: string) {
  expect(link).toHaveAttribute('href', href);
  expect(link).toHaveAttribute('target', '_blank');
  const rel = (link.getAttribute('rel') ?? '').split(/\s+/);
  expect(rel).toEqual(expect.arrayContaining(['noopener', 'noreferrer']));
}

describe('PresetPlanView model links', () => {
  it('links the model in the best value sentence to its OpenRouter page, and keeps the id beside it', () => {
    view(plan());
    const link = screen.getByRole('link', { name: 'Open Flash on OpenRouter' });
    expectOpensOpenRouter(link, 'https://openrouter.ai/deepseek/flash');
    expect(link).toHaveTextContent('Flash');
    // the id stays as plain code next to the name, not inside the link
    const code = screen.getByText('deepseek/flash', { selector: 'code' });
    expect(link).not.toContainElement(code);
    expect(code.parentElement).toBe(link.parentElement);
  });

  it('links the models in the order table, the providers table and the next best list', () => {
    view(
      plan({
        picks: [FLASH, GLM],
        providers: [
          { ...plan().providers[0], models: ['deepseek/flash', 'z-ai/glm:free'] },
          { ...plan().providers[0], tag: 'novita/fp4', provider: 'Novita', models: ['z-ai/glm:free'] },
        ],
      }),
    );

    const models = within(screen.getByRole('table', { name: 'Models' }));
    expectOpensOpenRouter(
      models.getByRole('link', { name: 'Open Flash on OpenRouter' }),
      'https://openrouter.ai/deepseek/flash',
    );
    expectOpensOpenRouter(
      models.getByRole('link', { name: 'Open GLM on OpenRouter' }),
      'https://openrouter.ai/z-ai/glm:free',
    );
    expect(models.getByText('z-ai/glm:free', { selector: 'code' })).toBeInTheDocument();

    const providers = within(screen.getByRole('table', { name: 'Providers' }));
    const forColumn = providers.getAllByRole('link');
    expect(forColumn.map((a) => a.getAttribute('href'))).toEqual([
      'https://openrouter.ai/deepseek/flash',
      'https://openrouter.ai/z-ai/glm:free',
      'https://openrouter.ai/z-ai/glm:free',
    ]);
    forColumn.forEach((a) => expect(a).toHaveAttribute('target', '_blank'));
    expect(providers.getAllByRole('row')[1]).toHaveTextContent('Flash, GLM');

    expectOpensOpenRouter(
      screen.getByRole('link', { name: 'Open Steady on OpenRouter' }),
      'https://openrouter.ai/acme/steady',
    );
  });

  it('links a provider row to the id when OpenRouter named a model the plan has no name for', () => {
    view(
      plan({
        picks: [FLASH, GLM],
        providers: [{ ...plan().providers[0], models: ['deepseek/flash', 'other/unlisted'] }],
      }),
    );
    const providers = within(screen.getByRole('table', { name: 'Providers' }));
    expectOpensOpenRouter(
      providers.getByRole('link', { name: 'Open other/unlisted on OpenRouter' }),
      'https://openrouter.ai/other/unlisted',
    );
  });

  it('links the model of each version, by its id', async () => {
    const user = userEvent.setup();
    view(plan());
    await user.click(screen.getByRole('button', { name: /Versions on OpenRouter/ }));
    const versions = within(await screen.findByRole('table', { name: 'Preset versions' }));
    expectOpensOpenRouter(
      versions.getByRole('link', { name: 'Open deepseek/old on OpenRouter' }),
      'https://openrouter.ai/deepseek/old',
    );
    expectOpensOpenRouter(
      versions.getByRole('link', { name: 'Open dots-studio/older:free on OpenRouter' }),
      'https://openrouter.ai/dots-studio/older:free',
    );
  });

  it('shows a dash, not a link, for a version with no model', async () => {
    const user = userEvent.setup();
    view(
      plan({
        versions: [{ version: 2, created_at: '2026-11-04T07:36:33Z', model: null, providers: [], live: true }],
      }),
    );
    await user.click(screen.getByRole('button', { name: /Versions on OpenRouter/ }));
    const versions = within(await screen.findByRole('table', { name: 'Preset versions' }));
    expect(versions.queryByRole('link')).toBeNull();
  });
});

describe('PresetPlanView versions', () => {
  const toggleName = 'Versions on OpenRouter (2, live v4)';

  it('keeps the versions closed until they are asked for, and says how many there are and which is live', () => {
    view(plan());
    const toggle = screen.getByRole('button', { name: toggleName });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByLabelText('Preset versions')).not.toBeVisible();
  });

  it('opens and closes with a click', async () => {
    const user = userEvent.setup();
    view(plan());
    await user.click(screen.getByRole('button', { name: toggleName }));
    expect(screen.getByRole('button', { name: toggleName })).toHaveAttribute('aria-expanded', 'true');
    await waitFor(() => expect(screen.getByLabelText('Preset versions')).toBeVisible());
    const versions = within(screen.getByLabelText('Preset versions'));
    expect(versions.getByText('v3')).toBeVisible();
    expect(versions.getByText('live')).toBeVisible();

    await user.click(screen.getByRole('button', { name: toggleName }));
    expect(screen.getByRole('button', { name: toggleName })).toHaveAttribute('aria-expanded', 'false');
    await waitFor(() => expect(screen.getByLabelText('Preset versions')).not.toBeVisible());
  });

  it.each([
    ['Enter', '{Enter}'],
    ['Space', ' '],
  ])('opens and closes from the keyboard with %s', async (_name, key) => {
    const user = userEvent.setup();
    view(plan());
    const toggle = screen.getByRole('button', { name: toggleName });
    // tab to it as a keyboard user would: the model links come first
    for (let stops = 0; stops < 10 && document.activeElement !== toggle; stops += 1) {
      await user.tab();
    }
    expect(toggle).toHaveFocus();

    await user.keyboard(key);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await waitFor(() => expect(screen.getByLabelText('Preset versions')).toBeVisible());

    await user.keyboard(key);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await waitFor(() => expect(screen.getByLabelText('Preset versions')).not.toBeVisible());
  });

  it('counts the versions and names the live one from the rows', () => {
    view(
      plan({
        current_version: 9,
        versions: [
          { version: 8, created_at: '2026-11-04T07:36:33Z', model: 'a/b', providers: [], live: false },
          { version: 7, created_at: '2026-11-03T07:36:33Z', model: 'a/c', providers: [], live: true },
          { version: 6, created_at: '2026-11-02T07:36:33Z', model: 'a/d', providers: [], live: false },
        ],
      }),
    );
    expect(screen.getByRole('button', { name: 'Versions on OpenRouter (3, live v7)' })).toBeInTheDocument();
  });

  it('falls back to the plan live version when no row is marked live, and to the count alone with neither', () => {
    const rows = [{ version: 5, created_at: '2026-11-04T07:36:33Z', model: 'a/b', providers: [], live: false }];
    const { unmount } = view(plan({ current_version: 5, versions: rows }));
    expect(screen.getByRole('button', { name: 'Versions on OpenRouter (1, live v5)' })).toBeInTheDocument();
    unmount();

    view(plan({ current_version: null, versions: rows }));
    expect(screen.getByRole('button', { name: 'Versions on OpenRouter (1)' })).toBeInTheDocument();
  });

  it('renders nothing for a preset with no versions', () => {
    view(plan({ current_version: null, versions: [] }));
    expect(screen.queryByRole('button', { name: /Versions on OpenRouter/ })).toBeNull();
    expect(screen.queryByLabelText('Preset versions')).toBeNull();
    expect(screen.queryByText(/Versions on OpenRouter/)).toBeNull();
  });
});

describe('PresetPlanView summary', () => {
  it('shows what would happen, the live version and the changed keys', () => {
    view(
      plan({
        warnings: ['No provider data for one runner-up.'],
        changes: [{ path: 'model', before: '"deepseek/old"', after: '"deepseek/flash"' }],
      }),
    );
    expect(screen.getByText('Update')).toBeInTheDocument();
    expect(screen.getByText('Switches deepseek/old to Flash, 31% more value.')).toBeInTheDocument();
    expect(screen.getByText('Live version').parentElement).toHaveTextContent('v4');
    expect(screen.getByText('@preset/vibonomics-best-value')).toBeInTheDocument();
    expect(screen.getByText('No provider data for one runner-up.')).toBeInTheDocument();
    expect(within(screen.getByRole('table', { name: 'Changes' })).getByText('"deepseek/flash"')).toBeInTheDocument();
  });

  it('keeps the API call out of the way until it is asked for', async () => {
    const user = userEvent.setup();
    view(
      plan({
        request: {
          method: 'POST',
          url: 'https://openrouter.ai/api/v1/presets/vibonomics-best-value/chat/completions',
          body: { model: 'deepseek/flash', messages: [{ role: 'user', content: 'Vibonomics preset update' }] },
        },
      }),
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
    view(
      plan({
        action: 'blocked',
        reason: 'A preset called vibonomics-best-value already exists.',
        current_version: null,
        versions: [],
        request: null,
        changes: [],
        digest: null,
      }),
    );
    expect(screen.getByText('Blocked')).toBeInTheDocument();
    expect(screen.getByText('Not created yet')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'View API call' })).toBeNull();
    expect(screen.queryByRole('table', { name: 'Preset versions' })).toBeNull();
  });

  it('lists several models in order, and the models each provider is there for', () => {
    view(
      plan({
        picks: [FLASH, GLM],
        providers: [
          { ...plan().providers[0], models: ['deepseek/flash', 'z-ai/glm:free'] },
          {
            ...plan().providers[0],
            tag: 'novita/fp4',
            provider: 'Novita',
            task_cost: 0.055,
            models: ['z-ai/glm:free'],
          },
        ],
      }),
    );
    const models = within(screen.getByRole('table', { name: 'Models' }));
    expect(models.getAllByRole('row').map((r) => r.textContent)).toEqual([
      '#ModelScorePer taskValue',
      '1Flash deepseek/flash71.4$0.0551,298',
      '2GLM z-ai/glm:free66$0.07940',
    ]);
  });
});

describe('PresetPlanView routing, blocklists and scores', () => {
  it('says the models are tried in order on the fallback route', () => {
    view(plan());
    expect(screen.getByText('Routing: fallback, OpenRouter tries the models in the order listed.')).toBeInTheDocument();
  });

  it('says what the sort route orders by, and price when none is named', () => {
    const { unmount } = view(plan({ route: 'sort', sort: 'latency' }));
    expect(screen.getByText('Routing: sort, OpenRouter orders the models by latency.')).toBeInTheDocument();
    unmount();
    view(plan({ route: 'sort', sort: null }));
    expect(screen.getByText('Routing: sort, OpenRouter orders the models by price.')).toBeInTheDocument();
  });

  it('says nothing about the blocklists when they left nothing out', () => {
    view(plan());
    expect(screen.queryByText(/Left out by your blocklists/)).toBeNull();
  });

  it.each([
    [
      { models: 3, providers: ['deepinfra', 'novita'] },
      'Left out by your blocklists: 3 models, providers: deepinfra, novita',
    ],
    [{ models: 1, providers: [] }, 'Left out by your blocklists: 1 model'],
    [{ models: 0, providers: ['novita'] }, 'Left out by your blocklists: providers: novita'],
  ])('says what the blocklists left out: %j', (excluded, line) => {
    view(plan({ excluded }));
    expect(screen.getByText(line)).toBeInTheDocument();
  });

  it('says when the scores are the owner own tests, and only then', () => {
    const { unmount } = view(plan({ own_tests_preferred: true }));
    expect(screen.getByText('Scores used: your own tests where a model has enough of them.')).toBeInTheDocument();
    unmount();
    view(plan({ own_tests_preferred: false }));
    expect(screen.queryByText(/Scores used/)).toBeNull();
  });
});

describe('PresetPlanView kept as it is', () => {
  it('shows the keys Vibonomics sends back unchanged as raw JSON, with the promise', () => {
    view(plan({ passthrough: { temperature: 0.2, provider: { data_collection: 'deny' } } }));
    expect(screen.getByText('Kept as it is')).toBeInTheDocument();
    expect(
      screen.getByText('Vibonomics has no integration for these keys and will send them back unchanged.'),
    ).toBeInTheDocument();
    const raw = screen.getByLabelText('Kept as it is, raw JSON');
    expect(raw.tagName).toBe('PRE');
    expect(JSON.parse(raw.textContent ?? '')).toEqual({ temperature: 0.2, provider: { data_collection: 'deny' } });
    expect(raw).toHaveTextContent('"data_collection": "deny"');
  });

  it('says nothing is kept when there are no such keys, but still shows the block', () => {
    view(plan({ passthrough: {} }));
    expect(screen.getByText('Kept as it is')).toBeInTheDocument();
    expect(screen.getByText('nothing')).toBeInTheDocument();
    expect(screen.queryByLabelText('Kept as it is, raw JSON')).toBeNull();
  });
});

describe('PresetPlanView link to the preset', () => {
  it('links the preset to its page on OpenRouter once it has an id, in the workspace OpenRouter names', () => {
    view(plan({ preset_id: 'pre_9', workspace: 'team-a' }));
    expectOpensOpenRouter(
      screen.getByRole('link', { name: /Open on OpenRouter/ }),
      'https://openrouter.ai/workspaces/team-a/presets/pre_9/edit',
    );
  });

  it('assumes the default workspace when none is named', () => {
    view(plan({ preset_id: 'pre_9', workspace: null }));
    expect(screen.getByRole('link', { name: /Open on OpenRouter/ })).toHaveAttribute(
      'href',
      'https://openrouter.ai/workspaces/default/presets/pre_9/edit',
    );
  });

  it('has no link for a preset that is not on OpenRouter yet', () => {
    view(plan({ preset_id: null, workspace: null, current_version: null, versions: [] }));
    expect(screen.queryByRole('link', { name: /Open on OpenRouter/ })).toBeNull();
    view(plan());
    expect(screen.queryByRole('link', { name: /Open on OpenRouter/ })).toBeNull();
  });
});
