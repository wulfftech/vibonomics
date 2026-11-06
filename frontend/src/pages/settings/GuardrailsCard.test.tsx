import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NO_GUARDRAILS, type PresetGuardrails } from '../../api/types';
import {
  choose,
  LABELS,
  LIVE_CONFIG,
  modeOf,
  requireSwitch,
  save,
  saved,
  setup,
  show,
  SLUG,
  SECOND,
} from './guardrailsTestKit';
import { calls, fetchMock, item, live, plan, savedPresets, SELECTED_KEY, sentTo, serve } from './presetTestKit';

beforeEach(() => {
  fetchMock.mockReset();
  localStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

describe('GuardrailsCard what it shows', () => {
  it('opens with every guardrail left as it is, every value input off, and the notes the owner needs', async () => {
    setup();
    await show();
    expect(await screen.findByRole('heading', { name: 'Guardrails' })).toBeInTheDocument();
    expect(screen.getByText(/Limits against runaway agents\./)).toHaveTextContent(
      /repetition_penalty is rarely sent by clients, so it is the lever that works\./,
    );
    for (const label of LABELS) {
      expect(modeOf(label)).toBe('leave');
    }
    expect(screen.getByRole('textbox', { name: 'Temperature' })).toBeDisabled();
    expect(screen.getByRole('textbox', { name: 'Max tokens' })).toBeDisabled();
    expect(screen.getByRole('textbox', { name: 'System prompt' })).toBeDisabled();
    expect(requireSwitch()).toBeDisabled();
    expect(screen.getByRole('combobox', { name: 'Quantizations' })).toBeDisabled();
    expect(screen.getByRole('combobox', { name: 'Ignored providers' })).toBeDisabled();
    expect(screen.getByText(/DeepSeek's own endpoint ignores temperature and the penalties/)).toBeInTheDocument();
    expect(screen.getByText(/Reasoning tokens count toward this cap.*stored as max_tokens/)).toBeInTheDocument();
    expect(screen.getByText(/1\.0 is off; 1\.05 to 1\.15 is typical/)).toBeInTheDocument();
    expect(screen.getByText(/including those OpenRouter reports as unknown/)).toBeInTheDocument();
    expect(screen.getByText(/Vibonomics blocks an update that would cause that/)).toBeInTheDocument();
    expect(screen.getByText(/The previous prompt is backed up each time it is replaced/)).toBeInTheDocument();
    // nothing is unsaved yet
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('says what each guardrail is on OpenRouter now, from the last sync', async () => {
    setup();
    await show();
    await screen.findByRole('heading', { name: 'Guardrails' });
    const now = (text: string) => screen.getAllByText(`On OpenRouter now: ${text}`);
    expect(now('0.2')).toHaveLength(1);
    expect(now('4096')).toHaveLength(1);
    expect(now('1.1')).toHaveLength(1);
    expect(now('fp8')).toHaveLength(1);
    expect(now('badhost')).toHaveLength(1);
    expect(now('true')).toHaveLength(1);
    // frequency and presence penalty and top_p are not on the preset
    expect(now('not set')).toHaveLength(3);
    // the text of the prompt has not been read: only that there is one
    expect(now('set (preview to read it)')).toHaveLength(1);
  });

  it('prefers the live config of a newer plan, and shows the live prompt text length from it', async () => {
    setup(
      {},
      {
        plan: plan({
          current_version: 5,
          current_config: { ...LIVE_CONFIG, temperature: 0.9 },
          system_prompt: { live: 'x'.repeat(1234), planned: 'x'.repeat(1234), source: 'kept', changed: false },
        }),
      },
    );
    const user = userEvent.setup();
    await show();
    expect(await screen.findByText('On OpenRouter now: 0.2')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    expect(await screen.findByText('On OpenRouter now: 0.9')).toBeInTheDocument();
    expect(screen.getByText('On OpenRouter now: 1,234 characters')).toBeInTheDocument();
  });

  it('says the preset is not created yet when there is no live config', async () => {
    serve({ items: [item({ enabled: false })], catalog: [] });
    localStorage.setItem(SELECTED_KEY, JSON.stringify(SLUG));
    await show();
    await screen.findByRole('heading', { name: 'Guardrails' });
    expect(screen.getAllByText('On OpenRouter now: not created yet')).toHaveLength(10);
  });
});

describe('GuardrailsCard temperature in its three states', () => {
  it('Set starts from the live value, saves what is typed, and the saved item holds it', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    await choose(user, 'Temperature', 'Set');
    const input = screen.getByRole('textbox', { name: 'Temperature' });
    expect(input).toBeEnabled();
    expect(input).toHaveValue('0.2');
    await user.clear(input);
    await user.type(input, '0.65');
    await save(user);
    await waitFor(() => expect(saved()).toBeDefined());
    expect(saved()).toEqual({ ...NO_GUARDRAILS, temperature: 0.65 });
    expect(calls('api/presets/plan')).toHaveLength(0);
  });

  it('Remove saves the key as cleared and no value', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    await choose(user, 'Temperature', 'Remove');
    expect(screen.getByRole('textbox', { name: 'Temperature' })).toBeDisabled();
    await save(user);
    await waitFor(() => expect(saved()).toBeDefined());
    expect(saved()).toEqual({ ...NO_GUARDRAILS, clear: ['temperature'] });
  });

  it('Leave saves nothing for the key, and going Set then Leave again is not a change', async () => {
    setup({ top_p: 0.9 });
    const user = userEvent.setup();
    await show();
    await choose(user, 'Temperature', 'Set');
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
    await choose(user, 'Temperature', 'Leave');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(modeOf('Top p')).toBe('set');
    expect(screen.getByRole('textbox', { name: 'Top p' })).toHaveValue('0.9');
    // another key is changed, so this one is sent as it was: left alone
    await choose(user, 'Max tokens', 'Remove');
    await save(user);
    await waitFor(() => expect(saved()).toBeDefined());
    expect(saved()).toEqual({ ...NO_GUARDRAILS, top_p: 0.9, clear: ['max_tokens'] });
  });

  it('shows a saved value as Set and a saved removal as Remove, and takes either back to Leave', async () => {
    setup({ temperature: 0.4, clear: ['max_tokens'] });
    const user = userEvent.setup();
    await show();
    expect(await screen.findByRole('textbox', { name: 'Temperature' })).toHaveValue('0.4');
    expect(modeOf('Temperature')).toBe('set');
    expect(modeOf('Max tokens')).toBe('remove');
    await choose(user, 'Temperature', 'Leave');
    await choose(user, 'Max tokens', 'Leave');
    await save(user);
    await waitFor(() => expect(saved()).toBeDefined());
    expect(saved()).toEqual(NO_GUARDRAILS);
  });

  it('goes from Remove to Set and back to Remove without ever sending both', async () => {
    setup({ clear: ['temperature'] });
    const user = userEvent.setup();
    await show();
    await choose(user, 'Temperature', 'Set');
    const input = screen.getByRole('textbox', { name: 'Temperature' });
    await user.clear(input);
    await user.type(input, '1');
    await save(user);
    await waitFor(() => expect(saved()).toEqual({ ...NO_GUARDRAILS, temperature: 1 }));
    await choose(user, 'Temperature', 'Remove');
    await save(user);
    await waitFor(() => expect(saved()).toEqual({ ...NO_GUARDRAILS, clear: ['temperature'] }));
  });

  it('takes a negative penalty typed with its minus sign', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    await choose(user, 'Frequency penalty', 'Set');
    await user.type(screen.getByRole('textbox', { name: 'Frequency penalty' }), '-0.5');
    await save(user);
    await waitFor(() => expect(saved()).toBeDefined());
    expect(saved()?.frequency_penalty).toBe(-0.5);
  });
});

describe('GuardrailsCard max_tokens in its three states', () => {
  it('Set saves a whole number, Remove clears it, Leave sends nothing', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    await choose(user, 'Max tokens', 'Set');
    const input = screen.getByRole('textbox', { name: 'Max tokens' });
    expect(input).toHaveValue('4,096');
    await user.clear(input);
    await user.type(input, '8000');
    await save(user);
    await waitFor(() => expect(saved()).toEqual({ ...NO_GUARDRAILS, max_tokens: 8000 }));

    await choose(user, 'Max tokens', 'Remove');
    await save(user);
    await waitFor(() => expect(saved()).toEqual({ ...NO_GUARDRAILS, clear: ['max_tokens'] }));

    await choose(user, 'Max tokens', 'Leave');
    await save(user);
    await waitFor(() => expect(saved()).toEqual(NO_GUARDRAILS));
  });

  it('will not take a decimal, and flags a value over 2,000,000 without changing it', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    await choose(user, 'Max tokens', 'Set');
    const input = screen.getByRole('textbox', { name: 'Max tokens' });
    await user.clear(input);
    await user.type(input, '12.5');
    expect(input).toHaveValue('125');
    await user.clear(input);
    await user.type(input, '3000000');
    await user.tab();
    expect(input).toHaveValue('3,000,000');
    expect(input).toBeInvalid();
    expect(screen.getByText('Enter a whole number from 1 to 2,000,000')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });
});

describe('GuardrailsCard an unfinished Set', () => {
  it('shows the error, turns Save and Preview off, and sends nothing until it is fixed', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    await choose(user, 'Repetition penalty', 'Remove');
    await choose(user, 'Top p', 'Set');
    const input = screen.getByRole('textbox', { name: 'Top p' });
    expect(input).toHaveValue('');
    expect(input).toBeInvalid();
    expect(screen.getByText('Enter a number from 0.01 to 1')).toBeInTheDocument();
    expect(screen.getByText(/Some settings below need a value/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
    expect(screen.getByText(/Save is off until the guardrails of vibonomics-best-value/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Discard changes' })).toBeEnabled();

    await user.type(input, '5');
    expect(screen.getByText('Enter a number from 0.01 to 1')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await user.clear(input);
    await user.type(input, '0.5');
    expect(screen.queryByText('Enter a number from 0.01 to 1')).toBeNull();
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Preview' })).toBeEnabled();
    expect(calls('api/settings', 'PUT')).toHaveLength(0);
    expect(calls('api/presets/plan')).toHaveLength(0);
  });

  it('is cleared by choosing Leave or Remove, and by Discard changes', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    await choose(user, 'Temperature', 'Set');
    await user.clear(screen.getByRole('textbox', { name: 'Temperature' }));
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await choose(user, 'Temperature', 'Remove');
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();

    await choose(user, 'Temperature', 'Set');
    await user.clear(screen.getByRole('textbox', { name: 'Temperature' }));
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(modeOf('Temperature')).toBe('leave');
    expect(screen.getByRole('textbox', { name: 'Temperature' })).toHaveValue('');
    expect(screen.queryByText(/Enter a number from 0 to 2/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('holds back Save from another preset too, and says which preset it is', async () => {
    serve({
      items: [item({ enabled: false }), item({ slug: SECOND, enabled: false })],
      catalog: [live(SLUG, { managed: true }), live(SECOND, { managed: true })],
    });
    localStorage.setItem(SELECTED_KEY, JSON.stringify(SECOND));
    const user = userEvent.setup();
    await show();
    await choose(user, 'Temperature', 'Set');
    await user.click(screen.getByRole('button', { name: new RegExp(SLUG) }));
    expect(await screen.findByRole('heading', { name: SLUG })).toBeInTheDocument();
    expect(modeOf('Temperature')).toBe('leave');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(screen.getByText(/Save is off until the guardrails of vibonomics-second/)).toBeInTheDocument();
  });

  it('keeps one preset guardrails apart from the next', async () => {
    serve({
      items: [item({ enabled: false }), item({ slug: SECOND, enabled: false })],
      catalog: [live(SLUG, { managed: true }), live(SECOND, { managed: true })],
    });
    localStorage.setItem(SELECTED_KEY, JSON.stringify(SLUG));
    const user = userEvent.setup();
    await show();
    await choose(user, 'Temperature', 'Set');
    await user.type(screen.getByRole('textbox', { name: 'Temperature' }), '0.3');
    await user.click(screen.getByRole('button', { name: new RegExp(SECOND) }));
    expect(await screen.findByRole('heading', { name: SECOND })).toBeInTheDocument();
    expect(modeOf('Temperature')).toBe('leave');
    expect(screen.getByRole('textbox', { name: 'Temperature' })).toHaveValue('');
    await user.click(screen.getByRole('button', { name: new RegExp(SLUG) }));
    expect(await screen.findByRole('textbox', { name: 'Temperature' })).toHaveValue('0.3');
    await save(user);
    await waitFor(() => expect(savedPresets()?.items).toHaveLength(2));
    expect(savedPresets()?.items[0].guardrails?.temperature).toBe(0.3);
    expect(savedPresets()?.items[1].guardrails).toEqual(NO_GUARDRAILS);
  });
});

describe('GuardrailsCard with the rest of the editor', () => {
  it('previews the unsaved guardrails as they stand', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    await choose(user, 'Temperature', 'Remove');
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() => expect(calls('api/presets/plan')).toHaveLength(1));
    const sent = sentTo('api/presets/plan') as { settings: { guardrails: PresetGuardrails } };
    expect(sent.settings.guardrails).toEqual({ ...NO_GUARDRAILS, clear: ['temperature'] });
  });

  it('sends the update with Save when writing is on, with the guardrails already saved', async () => {
    serve({ items: [item({ enabled: true })], catalog: [live(SLUG, { managed: true })] });
    localStorage.setItem(SELECTED_KEY, JSON.stringify(SLUG));
    const user = userEvent.setup();
    await show();
    await choose(user, 'Max tokens', 'Set');
    await user.type(screen.getByRole('textbox', { name: 'Max tokens' }), '2000');
    expect(screen.getByText('Saving also sends the update to OpenRouter.')).toBeInTheDocument();
    await save(user);
    await waitFor(() => expect(calls('api/presets/apply')).toHaveLength(1));
    expect(saved()).toEqual({ ...NO_GUARDRAILS, max_tokens: 2000 });
    // the plan that was applied is of the saved preset, not of a draft
    expect(sentTo('api/presets/plan')).toEqual({ slug: SLUG });
  });

  it('still guards the tab against closing while a guardrail is Set and unfinished, as for any unsaved edit', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    const closing = () => {
      const event = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };
    expect(closing()).toBe(false);
    await choose(user, 'Temperature', 'Set');
    await user.clear(screen.getByRole('textbox', { name: 'Temperature' }));
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(closing()).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(closing()).toBe(false);
  });

  it('marks the preset in the list as unsaved while a guardrail is edited', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    await choose(user, 'Temperature', 'Remove');
    expect(screen.getByRole('button', { name: new RegExp(SLUG) })).toHaveTextContent(/unsaved/i);
  });
});
