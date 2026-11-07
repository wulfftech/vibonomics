import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { calls, fetchMock, item, live, renderSection, savedPresets, SELECTED_KEY, serve } from './presetTestKit';

const SLUG = 'vibonomics-best-value';
const SECOND = 'vibonomics-second';

const TWO = {
  items: [item({ enabled: false }), item({ slug: SECOND, enabled: false, max_models: 2 })],
  catalog: [live(SLUG, { managed: true }), live(SECOND, { managed: true })],
};

function open(slug = SLUG) {
  localStorage.setItem(SELECTED_KEY, JSON.stringify(slug));
}

async function pick(user: ReturnType<typeof userEvent.setup>, label: string, option: RegExp | string) {
  await user.click(await screen.findByRole('combobox', { name: label }));
  // the list is still fading in, which hides it from the accessibility tree
  await user.click(await screen.findByRole('option', { name: option, hidden: true }));
}

beforeEach(() => {
  fetchMock.mockReset();
  localStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

describe('PresetEditor a new preset', () => {
  it('takes a name and adds the prefix itself, then saves it as a new item beside the others', async () => {
    serve({ items: [item({ enabled: false })] });
    open();
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'New preset' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'New preset' }));
    const add = dialog.getByRole('button', { name: 'Add preset' });
    expect(add).toBeDisabled();

    await user.type(dialog.getByLabelText('Preset name'), 'My fast preset');
    expect(dialog.getByText('vibonomics-my-fast-preset', { exact: false, selector: 'code' })).toBeInTheDocument();
    await user.click(add);

    expect(await screen.findByRole('heading', { name: 'vibonomics-my-fast-preset' })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    // in the list and in the header
    expect(screen.getAllByText('not created yet')).toHaveLength(2);
    expect(screen.getByLabelText('Preset name')).toHaveValue('my-fast-preset');
    expect(screen.getByLabelText('Preset name')).toHaveAttribute('readonly');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.items).toHaveLength(2));
    const [first, added] = savedPresets()?.items ?? [];
    expect(first.slug).toBe(SLUG);
    expect(added).toMatchObject({
      slug: 'vibonomics-my-fast-preset',
      enabled: false,
      adopted: false,
      route: 'fallback',
    });
    // writing is off for a new preset, so saving it creates nothing on OpenRouter
    expect(calls('api/presets/plan')).toHaveLength(0);
  });

  it('creates the preset on OpenRouter through the same save once writing is turned on', async () => {
    serve({ items: [item({ enabled: false })] });
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'New preset' }));
    await user.type(await screen.findByLabelText('Preset name'), 'fresh');
    await user.click(screen.getByRole('button', { name: 'Add preset' }));
    await user.click(await screen.findByLabelText(/Let Vibonomics write this preset/));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls('api/presets/apply')).toHaveLength(1));
    const planned = JSON.parse((calls('api/presets/plan')[0][1] as RequestInit).body as string) as { slug: string };
    expect(planned).toEqual({ slug: 'vibonomics-fresh' });
  });

  it('will not take a name that is already used, or is a preset already on the account, or has no letters', async () => {
    serve({
      items: [item({ enabled: false })],
      catalog: [live(SLUG, { managed: true }), live('vibonomics-taken')],
    });
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'New preset' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'New preset' }));
    const name = dialog.getByLabelText('Preset name');
    const add = dialog.getByRole('button', { name: 'Add preset' });

    await user.type(name, 'best value');
    expect(dialog.getByText('Vibonomics already manages a preset with this name')).toBeInTheDocument();
    expect(add).toBeDisabled();

    await user.clear(name);
    await user.type(name, 'Taken');
    expect(dialog.getByText(/already on your account/)).toBeInTheDocument();
    expect(add).toBeDisabled();

    await user.clear(name);
    await user.type(name, '!!!');
    expect(dialog.getByText('Use letters or numbers in the name')).toBeInTheDocument();
    expect(add).toBeDisabled();
  });

  it('copies the template that was chosen', async () => {
    serve({
      items: [item({ enabled: false })],
      catalog: [live(SLUG, { managed: true }), live('my-tools', { name: 'My tools' })],
    });
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'New preset' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'New preset' }));
    await user.type(dialog.getByLabelText('Preset name'), 'tooled');
    await user.click(dialog.getByRole('combobox', { name: 'Start from preset (optional)' }));
    await user.click(await screen.findByRole('option', { name: /My tools/, hidden: true }));
    await user.click(dialog.getByRole('button', { name: 'Add preset' }));
    await user.click(await screen.findByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.items.at(-1)?.template_slug).toBe('my-tools'));
  });
});

describe('PresetEditor the form', () => {
  it('lists the presets on the account to start from, leaving out the ones Vibonomics manages, and saves the one chosen', async () => {
    serve({
      items: [item({ enabled: false })],
      catalog: [
        live(SLUG, { managed: true }),
        live('my-tools', { name: 'My tools', version: 3, models: ['deepseek/ds'], providers: ['deepinfra'] }),
        live('plain', { models: [], providers: [] }),
      ],
    });
    open();
    const user = userEvent.setup();
    renderSection();
    const picker = await screen.findByRole('combobox', { name: 'Start from preset (optional)' });
    await user.click(picker);
    expect(await screen.findByText('deepseek/ds · only deepinfra · v3')).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: new RegExp(SLUG) })).toBeNull();
    await user.click(screen.getByText('My tools (my-tools)'));
    expect(picker).toHaveValue('My tools (my-tools)');

    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.items[0].template_slug).toBe('my-tools'));
  });

  it('falls back to typing a slug, and says why, when OpenRouter cannot list the presets', async () => {
    serve({ catalog: [], catalogError: 'Add an OpenRouter API key under Settings, Providers first.' });
    open();
    renderSection();
    const field = await screen.findByLabelText(/Start from preset/);
    expect(field).toHaveAttribute('placeholder', 'my-preset');
    expect(screen.getByText(/The list of your presets is unavailable: Add an OpenRouter API key/)).toBeInTheDocument();
  });

  it.each([
    ['Models listed', 'max_models', 3],
    ['Providers listed', 'max_providers', 10],
  ] as const)('saves at most the limit for %s', async (label, key, limit) => {
    serve({ items: [item({ enabled: false })] });
    open();
    const user = userEvent.setup();
    renderSection();
    const field = await screen.findByLabelText(label);
    await user.clear(field);
    await user.type(field, '15');
    await user.tab();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.items[0][key]).toBe(limit));
  });

  it('saves three models when three are asked for, the most OpenRouter takes in a preset', async () => {
    serve({ items: [item({ enabled: false })] });
    open();
    const user = userEvent.setup();
    renderSection();
    const field = await screen.findByLabelText('Models listed');
    expect(field).toHaveAccessibleDescription(/1 to 3/);
    await user.clear(field);
    await user.type(field, '3');
    await user.tab();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.items[0].max_models).toBe(3));
  });

  it('takes half an hour as the schedule', async () => {
    serve({
      items: [
        item({ enabled: false, auto_apply: false }),
        item({ slug: SECOND, enabled: true, auto_apply: true, interval_hours: 6 }),
      ],
    });
    open(SECOND);
    const user = userEvent.setup();
    renderSection();
    const field = await screen.findByLabelText('Every');
    await user.tripleClick(field);
    await user.keyboard('0.5');
    await user.tab();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.items[1].interval_hours).toBe(0.5));
  });

  it('saves a cost limit and a value floor, and saves none when they are cleared', async () => {
    serve({ items: [item({ enabled: false })] });
    open();
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
    await waitFor(() => expect(savedPresets()?.items[0].max_task_cost).toBe(0.05));
    expect(savedPresets()?.items[0].min_value).toBe(2000);
  });

  it('shows saved limits and clears them to no limit', async () => {
    serve({ items: [item({ enabled: false, max_task_cost: 0.05, min_value: 2000 })] });
    open();
    const user = userEvent.setup();
    renderSection();
    const cost = await screen.findByLabelText('Maximum cost per task');
    expect(cost).toHaveValue('$0.05');
    expect(screen.getByLabelText('Minimum value')).toHaveValue('2,000');
    await user.clear(cost);
    await user.clear(screen.getByLabelText('Minimum value'));
    await user.tab();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.items[0].max_task_cost).toBeNull());
    expect(savedPresets()?.items[0].min_value).toBeNull();
  });
});

describe('PresetEditor routing, blocklists and own tests', () => {
  it('saves the changes of one preset in its own item and leaves the others as they were', async () => {
    serve({
      ...TWO,
      items: [TWO.items[0], item({ slug: SECOND, auto_apply: true, interval_hours: 6 })],
    });
    open(SECOND);
    const user = userEvent.setup();
    renderSection();

    expect(await screen.findByRole('heading', { name: SECOND })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'Order the models by' })).toBeNull();
    await pick(user, 'Routing', /^Sort: let OpenRouter order/);
    expect(await screen.findByRole('combobox', { name: 'Order the models by' })).toHaveValue('Price');
    await pick(user, 'Order the models by', 'Latency');
    await user.type(
      screen.getByRole('combobox', { name: 'Blocked models for this preset' }),
      'openai/*{Enter}:free{Enter}',
    );
    await user.type(screen.getByRole('combobox', { name: 'Blocked providers for this preset' }), 'DeepInfra{Enter}');
    await pick(user, 'Own test scores', 'Prefer my own tests');
    const every = screen.getByLabelText('Every');
    await user.tripleClick(every);
    await user.keyboard('0.5');
    await user.tab();

    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()).toBeDefined());
    const saved = savedPresets()?.items ?? [];
    expect(saved[0]).toEqual(TWO.items[0]);
    expect(saved[1]).toMatchObject({
      slug: SECOND,
      route: 'sort',
      sort: 'latency',
      blocked_models: ['openai/*', ':free'],
      blocked_providers: ['DeepInfra'],
      prefer_own_tests: true,
      interval_hours: 0.5,
    });
  });

  it('says OpenRouter does not document the routing option, and explains the choices', async () => {
    serve({ items: [item({ enabled: false })] });
    open();
    renderSection();
    const routing = await screen.findByRole('combobox', { name: 'Routing' });
    expect(routing).toHaveValue('Fallback: try the models in order');
    expect(routing).toHaveAccessibleDescription(
      /OpenRouter does not document this option: after a write the app tells you if it did not keep it/,
    );
  });

  it.each([
    [null, 'Follow the global setting'],
    [true, 'Prefer my own tests'],
    [false, 'Use the blend'],
  ])('shows own test scores %s as "%s", and saves a change back to following', async (saved, shown) => {
    serve({ items: [item({ enabled: false, prefer_own_tests: saved })] });
    open();
    const user = userEvent.setup();
    renderSection();
    const select = await screen.findByRole('combobox', { name: 'Own test scores' });
    expect(select).toHaveValue(shown);
    await pick(user, 'Own test scores', saved === null ? 'Use the blend' : 'Follow the global setting');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.items[0].prefer_own_tests).toBe(saved === null ? false : null));
  });

  it('keeps the two free-only choices switched off until free models are allowed, and saves them', async () => {
    serve({ items: [item({ enabled: false, max_models: 2 })] });
    open();
    const user = userEvent.setup();
    renderSection();
    // a switch's description sits inside its label, so a label is found by how it begins
    const fallback = await screen.findByLabelText(/^Keep a paid model as the last fallback/);
    const freeOnly = screen.getByLabelText(/^Allow a lineup of free models only/);
    expect(fallback).toBeDisabled();
    expect(freeOnly).toBeDisabled();
    expect(freeOnly).toBeChecked();
    expect(fallback).not.toBeChecked();

    await user.click(screen.getByLabelText(/^Allow free models/));
    expect(fallback).toBeEnabled();
    expect(freeOnly).toBeEnabled();
    await user.click(fallback);
    await user.click(freeOnly);
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(savedPresets()?.items[0]).toMatchObject({
        include_free: true,
        paid_fallback: true,
        allow_free_only: false,
      }),
    );
  });

  it('says a paid fallback needs a second place when the preset lists one model', async () => {
    serve({ items: [item({ enabled: false, include_free: true, paid_fallback: true, max_models: 1 })] });
    open();
    renderSection();
    await screen.findByLabelText(/^Keep a paid model as the last fallback/);
    expect(screen.getByText(/Needs room for two or more models: raise Models listed/)).toBeInTheDocument();
  });

  it('saves the global blocklists without touching a preset, and keeps a pattern once whatever its case', async () => {
    serve({ items: [item({ enabled: false })] });
    const user = userEvent.setup();
    renderSection();
    expect(await screen.findByRole('heading', { name: 'Global settings' })).toBeInTheDocument();
    expect(
      screen.getByText(/Patterns, case-insensitive; \* matches anything, e\.g\. openai\/\*, \*:free, deepinfra\./),
    ).toBeInTheDocument();
    expect(screen.getByText(/this does not change what OpenRouter does at request time/)).toBeInTheDocument();

    await user.type(
      screen.getByRole('combobox', { name: 'Blocked models' }),
      'openai/*{Enter}OPENAI/*{Enter}*:free{Enter}',
    );
    await user.type(screen.getByRole('combobox', { name: 'Blocked providers' }), 'deepinfra{Enter}');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()).toBeDefined());
    expect(savedPresets()).toMatchObject({ blocked_models: ['openai/*', '*:free'], blocked_providers: ['deepinfra'] });
    expect(savedPresets()?.items).toHaveLength(1);
    // saving the blocklists sends nothing to OpenRouter: no preset was edited
    expect(calls('api/presets/plan')).toHaveLength(0);
  });

  it('shows the saved global blocklists and takes the last pattern away with Backspace', async () => {
    serve({ items: [], blocked_models: ['openai/*', '*:free'] });
    const user = userEvent.setup();
    renderSection();
    expect(await screen.findByText('openai/*')).toBeInTheDocument();
    expect(screen.getByText('*:free')).toBeInTheDocument();
    await user.click(screen.getByRole('combobox', { name: 'Blocked models' }));
    await user.keyboard('{Backspace}');
    expect(screen.queryByText('*:free')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.blocked_models).toEqual(['openai/*']));
  });
});

describe('PresetEditor stop managing', () => {
  it('takes only that preset out of the settings when saved, and says the preset stays on OpenRouter', async () => {
    serve({ ...TWO });
    open(SECOND);
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Stop managing' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Stop managing this preset?' }));
    expect(
      dialog.getByText(/stays on OpenRouter exactly as it is now; Vibonomics never deletes one/),
    ).toBeInTheDocument();
    expect(dialog.queryByText(/hands it back/)).toBeNull();
    await user.click(dialog.getByRole('button', { name: 'Stop managing' }));

    // nothing is sent until Save, and Discard brings it back
    expect(calls('api/settings', 'PUT')).toHaveLength(0);
    await user.click(await screen.findByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()).toBeDefined());
    expect(savedPresets()?.items.map((i) => i.slug)).toEqual([SLUG]);
    expect(savedPresets()?.items[0]).toEqual(TWO.items[0]);
  });

  it('can be undone with Discard before it is saved, and cancelled in the dialog', async () => {
    serve({ ...TWO });
    open(SECOND);
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Stop managing' }));
    await user.click(await screen.findByRole('button', { name: 'Keep managing' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.queryByRole('button', { name: 'Discard changes' })).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Stop managing' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Stop managing' }));
    await user.click(await screen.findByRole('button', { name: 'Discard changes' }));
    expect(await screen.findByRole('button', { name: new RegExp(`^${SECOND}`) })).toBeInTheDocument();
  });

  it('says an adopted preset is handed back too', async () => {
    serve({
      items: [item({ slug: 'hand-made', adopted: true, enabled: false })],
      catalog: [live('hand-made', { managed: true })],
    });
    open('hand-made');
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Stop managing' }));
    expect(await screen.findByText(/this also hands it back/)).toBeInTheDocument();
  });

  it('shows an adopted preset under the name it was given, which cannot be changed, and explains its scheduled runs', async () => {
    serve({
      items: [item({ slug: 'hand-made', adopted: true, enabled: false })],
      catalog: [live('hand-made', { managed: true })],
    });
    open('hand-made');
    renderSection();
    const name = await screen.findByLabelText('Preset name');
    expect(name).toHaveValue('hand-made');
    expect(name).toHaveAttribute('readonly');
    expect(screen.getByText(/keys Vibonomics sends back unchanged are the ones you reviewed/)).toBeInTheDocument();
    expect(screen.getAllByText('@preset/hand-made').length).toBeGreaterThan(0);
  });
});
