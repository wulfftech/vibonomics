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
    state: { owned: ['vibonomics-best-value'] },
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
