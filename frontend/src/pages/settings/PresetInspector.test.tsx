import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  calls,
  fetchMock,
  inspection,
  item,
  live,
  plan,
  renderSection,
  savedPresets,
  SELECTED_KEY,
  sentTo,
  serve,
} from './presetTestKit';

const HAND_MADE = live('my-tools', {
  name: 'My tools',
  models: ['deepseek/ds'],
  providers: ['deepinfra'],
  config: { model: 'deepseek/ds', temperature: 0.2 },
  passthrough_keys: ['temperature'],
});

function open(slug: string) {
  localStorage.setItem(SELECTED_KEY, JSON.stringify(slug));
}

async function startAdopting(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Adopt…' }));
  return within(await screen.findByRole('dialog', { name: 'Adopt my-tools' }));
}

beforeEach(() => {
  fetchMock.mockReset();
  localStorage.clear();
  open('my-tools');
});
afterEach(() => vi.unstubAllGlobals());

describe('PresetInspector a preset made by hand', () => {
  it('shows what OpenRouter holds, read only, with the versions and the raw config closed', async () => {
    serve({ items: [], catalog: [HAND_MADE] });
    const user = userEvent.setup();
    renderSection();
    expect(await screen.findByRole('heading', { name: 'My tools' })).toBeInTheDocument();
    expect(screen.getByText('@preset/my-tools')).toBeInTheDocument();
    expect(screen.queryByRole('img', { name: 'Managed by Vibonomics' })).toBeNull();
    // nothing here can be edited
    expect(screen.queryByLabelText('Minimum score')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();

    const model = screen.getByRole('link', { name: 'Open deepseek/ds on OpenRouter' });
    expect(model).toHaveAttribute('href', 'https://openrouter.ai/deepseek/ds');
    expect(model).toHaveAttribute('target', '_blank');
    expect(screen.getByText('deepinfra')).toBeInTheDocument();

    const versions = await screen.findByRole('button', { name: 'Versions on OpenRouter (2, live v3)' });
    expect(versions).toHaveAttribute('aria-expanded', 'false');
    // a closed section is rendered at low priority, so it can arrive a moment after its button
    expect(await screen.findByLabelText('Preset versions')).not.toBeVisible();
    const raw = screen.getByRole('button', { name: 'Raw live config' });
    expect(raw).toHaveAttribute('aria-expanded', 'false');
    expect(await screen.findByLabelText('Raw live config')).not.toBeVisible();

    await user.click(raw);
    await waitFor(() => expect(screen.getByLabelText('Raw live config')).toBeVisible());
    expect(JSON.parse(screen.getByLabelText('Raw live config').textContent ?? '')).toEqual(
      inspection('my-tools').config,
    );
    await user.click(versions);
    await waitFor(() => expect(screen.getByLabelText('Preset versions')).toBeVisible());
  });

  it('reads the preset again when asked', async () => {
    serve({ items: [], catalog: [HAND_MADE] });
    const user = userEvent.setup();
    renderSection();
    await screen.findByRole('button', { name: /Versions on OpenRouter/ });
    expect(calls('api/presets/inspect')).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(calls('api/presets/inspect')).toHaveLength(2));
    expect(sentTo('api/presets/inspect')).toEqual({ slug: 'my-tools' });
  });

  it('keeps showing the last sync, and says OpenRouter could not be read, when the inspection fails', async () => {
    serve({ items: [], catalog: [HAND_MADE], inspect: { status: 502, detail: 'OpenRouter is down.' } });
    renderSection();
    expect(
      await screen.findByText(/Could not read this preset from OpenRouter: OpenRouter is down\./),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open deepseek/ds on OpenRouter' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Versions on OpenRouter/ })).toBeNull();
  });

  it('warns that a preset with a system prompt is never written', async () => {
    serve({ items: [], catalog: [{ ...HAND_MADE, has_system_prompt: true }] });
    renderSection();
    expect(await screen.findByText(/has a system prompt\. Vibonomics never writes/)).toBeInTheDocument();
  });
});

describe('PresetInspector link to the preset on OpenRouter', () => {
  it('opens the preset in the workspace OpenRouter names for it, in a new tab', async () => {
    serve({ items: [], catalog: [HAND_MADE] });
    renderSection();
    await screen.findByRole('button', { name: /Versions on OpenRouter/ });
    const link = await screen.findByRole('link', { name: /Open on OpenRouter/ });
    // OpenRouter's own answer to the inspection wins over the copy from the last sync
    await waitFor(() =>
      expect(link).toHaveAttribute('href', 'https://openrouter.ai/workspaces/team-a/presets/pre_hand/edit'),
    );
    expect(link).toHaveAttribute('target', '_blank');
    expect(link.getAttribute('rel')).toContain('noopener');
  });

  it('assumes the default workspace when OpenRouter names none', async () => {
    serve({ items: [], catalog: [HAND_MADE], inspect: inspection('my-tools', { workspace: null, preset_id: null }) });
    renderSection();
    await screen.findByRole('button', { name: /Versions on OpenRouter/ });
    expect(await screen.findByRole('link', { name: /Open on OpenRouter/ })).toHaveAttribute(
      'href',
      'https://openrouter.ai/workspaces/default/presets/id-my-tools/edit',
    );
  });

  it('shows no link for a preset that has no id yet', async () => {
    serve({
      items: [],
      catalog: [{ ...HAND_MADE, preset_id: null }],
      inspect: inspection('my-tools', { preset_id: null }),
    });
    renderSection();
    await screen.findByRole('button', { name: /Versions on OpenRouter/ });
    expect(screen.queryByRole('link', { name: /Open on OpenRouter/ })).toBeNull();
  });

  it('is on the page of a preset Vibonomics manages, in the workspace its plan names', async () => {
    serve({
      catalog: [live('vibonomics-best-value', { managed: true, preset_id: 'pre_77' })],
      plan: plan({ preset_id: 'pre_77', workspace: 'team-b' }),
    });
    open('vibonomics-best-value');
    const user = userEvent.setup();
    renderSection();
    const link = await screen.findByRole('link', { name: /Open on OpenRouter/ });
    expect(link).toHaveAttribute('href', 'https://openrouter.ai/workspaces/default/presets/pre_77/edit');
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() =>
      expect(screen.getAllByRole('link', { name: /Open on OpenRouter/ })[0]).toHaveAttribute(
        'href',
        'https://openrouter.ai/workspaces/team-b/presets/pre_77/edit',
      ),
    );
  });
});

describe('AdoptPresetModal', () => {
  it('shows every key it would manage and the raw JSON it would send back, and adopts only once that is read', async () => {
    serve({ items: [item({ slug: 'vibonomics-other', enabled: false })], catalog: [HAND_MADE] });
    const user = userEvent.setup();
    renderSection();
    const dialog = await startAdopting(user);

    expect(await dialog.findByText('Kept as it is')).toBeInTheDocument();
    expect(
      dialog.getByText('Vibonomics has no integration for these keys and will send them back unchanged.'),
    ).toBeInTheDocument();
    for (const key of ['model', 'models', 'route', 'provider.only']) {
      expect(dialog.getByText(key, { selector: 'code' })).toBeInTheDocument();
    }
    const raw = dialog.getByLabelText('Kept as it is, raw JSON');
    expect(JSON.parse(raw.textContent ?? '')).toEqual({ temperature: 0.2, provider: { data_collection: 'deny' } });

    const confirm = dialog.getByRole('button', { name: 'Adopt this preset' });
    expect(confirm).toBeDisabled();
    await user.click(dialog.getByRole('checkbox', { name: 'I have read these keys' }));
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    await waitFor(() => expect(calls('api/presets/adopt')).toHaveLength(1));
    expect(sentTo('api/presets/adopt')).toEqual({ slug: 'my-tools', digest: 'digest-1' });
    // the dialog closes and the adopted preset is selected, under its own name, with writing off
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(await screen.findByLabelText('Preset name')).toHaveValue('my-tools');
    expect(screen.getByLabelText(/Let Vibonomics write this preset/)).not.toBeChecked();
    expect(screen.getAllByRole('img', { name: 'Managed by Vibonomics' }).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Adopt…' })).toBeNull();
    // the adopted item is not an unsaved change, and a later save keeps it
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Discard changes' })).toBeNull();
  });

  it('keeps an adopted preset when other edits are saved afterwards, so it is not handed back', async () => {
    serve({
      items: [item({ slug: 'vibonomics-other', enabled: false })],
      catalog: [HAND_MADE, live('vibonomics-other', { managed: true })],
    });
    const user = userEvent.setup();
    renderSection();
    const dialog = await startAdopting(user);
    await user.click(await dialog.findByRole('checkbox', { name: 'I have read these keys' }));
    await user.click(dialog.getByRole('button', { name: 'Adopt this preset' }));
    const field = await screen.findByLabelText('Minimum score');
    await user.clear(field);
    await user.type(field, '70');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()).toBeDefined());
    expect(savedPresets()?.items.map((i) => i.slug)).toEqual(['vibonomics-other', 'my-tools']);
    expect(savedPresets()?.items[1]).toMatchObject({ adopted: true, min_score: 70 });
  });

  it('says nothing is kept when the preset has no keys of that kind', async () => {
    serve({ items: [], catalog: [HAND_MADE], inspect: inspection('my-tools', { passthrough: {} }) });
    const user = userEvent.setup();
    renderSection();
    const dialog = await startAdopting(user);
    expect(await dialog.findByText('nothing')).toBeInTheDocument();
    expect(dialog.queryByLabelText('Kept as it is, raw JSON')).toBeNull();
  });

  it('cannot adopt a preset with a blocker, and shows each blocker', async () => {
    serve({
      items: [],
      catalog: [HAND_MADE],
      inspect: inspection('my-tools', {
        blockers: ['It is archived.', 'It has a system prompt.'],
        adoptable: false,
      }),
    });
    const user = userEvent.setup();
    renderSection();
    const dialog = await startAdopting(user);
    expect(await dialog.findByText('It is archived.')).toBeInTheDocument();
    expect(dialog.getByText('It has a system prompt.')).toBeInTheDocument();
    expect(dialog.getByRole('checkbox', { name: 'I have read these keys' })).toBeDisabled();
    expect(dialog.getByRole('button', { name: 'Adopt this preset' })).toBeDisabled();
    expect(calls('api/presets/adopt')).toHaveLength(0);
  });

  it('shows what the server said when the preset changed, and adopts the new version only after it is read again', async () => {
    let digest = 'old-digest';
    serve({
      items: [],
      catalog: [HAND_MADE],
      inspect: (slug) =>
        inspection(slug, {
          digest,
          passthrough: digest === 'old-digest' ? { temperature: 0.2 } : { temperature: 0.9 },
        }),
      adopt: (sent) =>
        sent.digest === 'old-digest'
          ? { status: 409, detail: 'The preset changed since you looked at it. Inspect it again.' }
          : item({ slug: sent.slug, adopted: true, enabled: false }),
    });
    const user = userEvent.setup();
    renderSection();
    const dialog = await startAdopting(user);
    await user.click(await dialog.findByRole('checkbox', { name: 'I have read these keys' }));
    digest = 'new-digest';
    await user.click(dialog.getByRole('button', { name: 'Adopt this preset' }));

    expect(await dialog.findByText('The preset changed since you looked at it. Inspect it again.')).toBeInTheDocument();
    await user.click(dialog.getByRole('button', { name: 'Inspect again' }));
    // what is shown is the new JSON, and the tick has to be given again
    await waitFor(() => expect(dialog.getByLabelText('Kept as it is, raw JSON')).toHaveTextContent('0.9'));
    expect(dialog.getByRole('checkbox', { name: 'I have read these keys' })).not.toBeChecked();
    expect(dialog.getByRole('button', { name: 'Adopt this preset' })).toBeDisabled();
    expect(dialog.queryByText(/changed since you looked/)).toBeNull();

    await user.click(dialog.getByRole('checkbox', { name: 'I have read these keys' }));
    await user.click(dialog.getByRole('button', { name: 'Adopt this preset' }));
    await waitFor(() => expect(calls('api/presets/adopt')).toHaveLength(2));
    expect(sentTo('api/presets/adopt')).toEqual({ slug: 'my-tools', digest: 'new-digest' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('can be cancelled, and adopts nothing', async () => {
    serve({ items: [], catalog: [HAND_MADE] });
    const user = userEvent.setup();
    renderSection();
    const dialog = await startAdopting(user);
    await user.click(await dialog.findByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(calls('api/presets/adopt')).toHaveLength(0);
  });

  it('says so when the preset cannot be read, and offers to try again', async () => {
    serve({ items: [], catalog: [HAND_MADE], inspect: { status: 502, detail: 'OpenRouter is down.' } });
    const user = userEvent.setup();
    renderSection();
    const dialog = await startAdopting(user);
    expect(await dialog.findByText('OpenRouter is down.')).toBeInTheDocument();
    expect(dialog.getByRole('button', { name: 'Inspect again' })).toBeInTheDocument();
    expect(dialog.queryByRole('button', { name: 'Adopt this preset' })).toBeNull();
  });
});

describe('PresetInspector a preset Vibonomics made and no longer keeps', () => {
  it('can be managed again, which adds its settings back', async () => {
    serve({ items: [], catalog: [live('vibonomics-old', { name: 'Old one', managed: true })] });
    open('vibonomics-old');
    const user = userEvent.setup();
    renderSection();
    expect(await screen.findByText(/Vibonomics made this preset but is not keeping it up to date/)).toBeInTheDocument();
    expect(screen.getAllByRole('img', { name: 'Managed by Vibonomics' }).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Adopt…' })).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Manage again' }));
    expect(await screen.findByLabelText('Preset name')).toHaveValue('old-one'.replace('old-one', 'old'));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(savedPresets()?.items).toHaveLength(1));
    expect(savedPresets()?.items[0]).toMatchObject({ slug: 'vibonomics-old', adopted: false, enabled: false });
  });
});
