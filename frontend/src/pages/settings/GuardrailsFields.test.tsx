import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NO_GUARDRAILS, type GuardrailKey } from '../../api/types';
import { choose, modeOf, requireSwitch, save, saved, setup, show, SLUG } from './guardrailsTestKit';
import { fetchMock, live, plan } from './presetTestKit';

beforeEach(() => {
  fetchMock.mockReset();
  localStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

const KEYS: [label: string, key: GuardrailKey][] = [
  ['Temperature', 'temperature'],
  ['Max tokens', 'max_tokens'],
  ['Repetition penalty', 'repetition_penalty'],
  ['Frequency penalty', 'frequency_penalty'],
  ['Presence penalty', 'presence_penalty'],
  ['Top p', 'top_p'],
  ['Quantizations', 'quantizations'],
  ['Ignored providers', 'ignore'],
  ['Require parameters', 'require_parameters'],
  ['System prompt', 'system_prompt'],
];

describe('GuardrailsCard every guardrail Remove and Leave', () => {
  it.each(KEYS)('%s: Remove saves the key as cleared and with no value', async (label, key) => {
    setup();
    const user = userEvent.setup();
    await show();
    await choose(user, label, 'Remove');
    await save(user);
    await waitFor(() => expect(saved()).toBeDefined());
    expect(saved()).toEqual({ ...NO_GUARDRAILS, clear: [key] });
  });

  it.each(KEYS)('%s: Leave saves nothing for the key, even after it was Set and Removed', async (label, key) => {
    setup();
    const user = userEvent.setup();
    await show();
    const other = key === 'temperature' ? 'Top p' : 'Temperature';
    await choose(user, other, 'Remove');
    await choose(user, label, 'Set');
    await choose(user, label, 'Remove');
    await choose(user, label, 'Leave');
    expect(modeOf(label)).toBe('leave');
    await save(user);
    await waitFor(() => expect(saved()).toBeDefined());
    expect(saved()?.[key]).toBeNull();
    expect(saved()?.clear).not.toContain(key);
  });
});

describe('GuardrailsCard quantizations', () => {
  async function pickPrecision(user: ReturnType<typeof userEvent.setup>, name: string) {
    await user.click(screen.getByRole('combobox', { name: 'Quantizations' }));
    await user.click(await screen.findByRole('option', { name, hidden: true }));
  }

  it('Set starts from the live list, takes more precisions, and saves them', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    await choose(user, 'Quantizations', 'Set');
    expect(screen.getByRole('combobox', { name: 'Quantizations' })).toBeEnabled();
    expect(document.querySelector('.mantine-MultiSelect-pill')).toHaveTextContent('fp8');
    await pickPrecision(user, 'bf16');
    await save(user);
    await waitFor(() => expect(saved()).toEqual({ ...NO_GUARDRAILS, quantizations: ['fp8', 'bf16'] }));
  });

  it('offers every precision OpenRouter lists, unknown included', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    await choose(user, 'Quantizations', 'Set');
    await user.click(screen.getByRole('combobox', { name: 'Quantizations' }));
    const first = await screen.findByRole('option', { name: 'int4', hidden: true });
    const options = within(first.closest('[role="listbox"]') as HTMLElement).getAllByRole('option', { hidden: true });
    expect(options.map((o) => o.textContent)).toEqual([
      'int4',
      'int8',
      'fp4',
      'mxfp4',
      'nvfp4',
      'fp6',
      'fp8',
      'mxfp8',
      'fp16',
      'bf16',
      'fp32',
      'unknown',
    ]);
  });

  it('an empty list is an error and keeps Save off, until Remove or a precision is chosen', async () => {
    setup({ clear: [] }, { catalog: [live(SLUG, { managed: true, config: { model: 'acme/one' } })] });
    const user = userEvent.setup();
    await show();
    await choose(user, 'Quantizations', 'Set');
    expect(screen.getByText('Choose at least one precision, or choose Remove')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await pickPrecision(user, 'fp16');
    expect(screen.queryByText(/Choose at least one precision/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
    await choose(user, 'Quantizations', 'Remove');
    await save(user);
    await waitFor(() => expect(saved()).toEqual({ ...NO_GUARDRAILS, clear: ['quantizations'] }));
  });
});

describe('GuardrailsCard ignored providers', () => {
  it('Set starts from the live list, adds a provider with Enter, and saves the list', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    await choose(user, 'Ignored providers', 'Set');
    await user.type(
      screen.getByRole('combobox', { name: 'Ignored providers' }),
      'deepinfra{Enter}google-vertex/europe{Enter}',
    );
    await save(user);
    await waitFor(() =>
      expect(saved()).toEqual({ ...NO_GUARDRAILS, ignore: ['badhost', 'deepinfra', 'google-vertex/europe'] }),
    );
  });

  it('flags a name that is not a provider slug, and Remove takes the list off', async () => {
    setup({}, { catalog: [live(SLUG, { managed: true, config: { model: 'acme/one' } })] });
    const user = userEvent.setup();
    await show();
    await choose(user, 'Ignored providers', 'Set');
    expect(screen.getByText('Add at least one provider, or choose Remove')).toBeInTheDocument();
    await user.type(screen.getByRole('combobox', { name: 'Ignored providers' }), 'not a slug/x/y{Enter}');
    expect(await screen.findByText(/is not a provider slug/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await choose(user, 'Ignored providers', 'Remove');
    await save(user);
    await waitFor(() => expect(saved()).toEqual({ ...NO_GUARDRAILS, clear: ['ignore'] }));
  });
});

describe('GuardrailsCard require parameters', () => {
  it('Set shows a switch that starts from the live value, and saves it on or off', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    const toggle = requireSwitch();
    expect(toggle).toBeDisabled();
    await choose(user, 'Require parameters', 'Set');
    expect(toggle).toBeEnabled();
    expect(toggle).toBeChecked();
    await user.click(toggle);
    await save(user);
    await waitFor(() => expect(saved()).toEqual({ ...NO_GUARDRAILS, require_parameters: false }));
    // false is a value: it is saved as Set, not as left alone
    expect(modeOf('Require parameters')).toBe('set');
  });

  it('Remove clears it and Leave sends nothing', async () => {
    setup({ require_parameters: true });
    const user = userEvent.setup();
    await show();
    expect(requireSwitch()).toBeChecked();
    await choose(user, 'Require parameters', 'Remove');
    expect(requireSwitch()).not.toBeChecked();
    await save(user);
    await waitFor(() => expect(saved()).toEqual({ ...NO_GUARDRAILS, clear: ['require_parameters'] }));
    await choose(user, 'Require parameters', 'Leave');
    await save(user);
    await waitFor(() => expect(saved()).toEqual(NO_GUARDRAILS));
  });
});

describe('GuardrailsCard system prompt', () => {
  it('Set shows an empty box with a counter, which has to be filled before Save', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    await choose(user, 'System prompt', 'Set');
    const box = screen.getByRole('textbox', { name: 'System prompt' });
    expect(box).toBeEnabled();
    expect(box).toHaveAttribute('maxlength', '20000');
    expect(screen.getByText('0 / 20,000 characters')).toBeInTheDocument();
    expect(screen.getByText('Write the prompt, or choose Remove')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();

    await user.type(box, 'Answer in one line.');
    expect(screen.getByText('19 / 20,000 characters')).toBeInTheDocument();
    expect(screen.queryByText('Write the prompt, or choose Remove')).toBeNull();
    await save(user);
    await waitFor(() => expect(saved()).toEqual({ ...NO_GUARDRAILS, system_prompt: 'Answer in one line.' }));
  });

  it('starts from the live prompt when a plan has read it', async () => {
    setup(
      {},
      {
        plan: plan({ system_prompt: { live: 'Be kind.', planned: 'Be kind.', source: 'kept', changed: false } }),
      },
    );
    const user = userEvent.setup();
    await show();
    await user.click(await screen.findByRole('button', { name: 'Preview' }));
    await screen.findByText('On OpenRouter now: 8 characters');
    await choose(user, 'System prompt', 'Set');
    expect(screen.getByRole('textbox', { name: 'System prompt' })).toHaveValue('Be kind.');
  });

  it('a blank prompt is not a prompt, and Remove takes the live one off', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    await choose(user, 'System prompt', 'Set');
    await user.type(screen.getByRole('textbox', { name: 'System prompt' }), '   ');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await choose(user, 'System prompt', 'Remove');
    expect(screen.getByRole('textbox', { name: 'System prompt' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
    await save(user);
    await waitFor(() => expect(saved()).toEqual({ ...NO_GUARDRAILS, system_prompt: null, clear: ['system_prompt'] }));
  });

  it('Leave sends no prompt, so the server resends the live one', async () => {
    setup({ top_p: 0.5 });
    const user = userEvent.setup();
    await show();
    expect(modeOf('System prompt')).toBe('leave');
    await choose(user, 'Top p', 'Remove');
    await save(user);
    await waitFor(() => expect(saved()).toBeDefined());
    expect(saved()?.system_prompt).toBeNull();
    expect(saved()?.clear).toEqual(['top_p']);
  });
});

describe('GuardrailsCard from the keyboard', () => {
  it('moves between Leave, Set and Remove with the arrow keys, and each move takes effect', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    const leave = within(await screen.findByRole('radiogroup', { name: 'Temperature setting' })).getByLabelText(
      'Leave',
    );
    act(() => leave.focus());
    expect(leave).toHaveFocus();
    expect(leave).toBeChecked();

    await user.keyboard('{ArrowRight}');
    expect(modeOf('Temperature')).toBe('set');
    expect(screen.getByRole('textbox', { name: 'Temperature' })).toBeEnabled();
    expect(screen.getByRole('textbox', { name: 'Temperature' })).toHaveValue('0.2');

    await user.keyboard('{ArrowRight}');
    expect(modeOf('Temperature')).toBe('remove');
    expect(screen.getByRole('textbox', { name: 'Temperature' })).toBeDisabled();

    await user.keyboard('{ArrowLeft}{ArrowLeft}');
    expect(modeOf('Temperature')).toBe('leave');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('reaches each group with Tab, one stop for each, and chooses with Space', async () => {
    setup();
    const user = userEvent.setup();
    await show();
    const temperature = within(await screen.findByRole('radiogroup', { name: 'Temperature setting' }));
    const maxTokens = within(screen.getByRole('radiogroup', { name: 'Max tokens setting' }));
    act(() => temperature.getByLabelText('Leave').focus());
    // the checked radio is the one stop of its group; the disabled input is skipped
    await user.tab();
    expect(maxTokens.getByLabelText('Leave')).toHaveFocus();
    await user.tab({ shift: true });
    expect(temperature.getByLabelText('Leave')).toHaveFocus();
    await user.tab();
    await user.tab();
    await user.keyboard(' ');
    expect(modeOf('Temperature')).toBe('leave');
    expect(modeOf('Max tokens')).toBe('leave');
    act(() => maxTokens.getByLabelText('Remove').focus());
    await user.keyboard(' ');
    expect(modeOf('Max tokens')).toBe('remove');
  });

  it('types a value straight after choosing Set with the keyboard', async () => {
    setup({}, { catalog: [live(SLUG, { managed: true, config: { model: 'acme/one' } })] });
    const user = userEvent.setup();
    await show();
    const leave = within(await screen.findByRole('radiogroup', { name: 'Temperature setting' })).getByLabelText(
      'Leave',
    );
    act(() => leave.focus());
    await user.keyboard('{ArrowRight}');
    await user.tab();
    expect(screen.getByRole('textbox', { name: 'Temperature' })).toHaveFocus();
    await user.keyboard('1.2');
    await save(user);
    await waitFor(() => expect(saved()).toEqual({ ...NO_GUARDRAILS, temperature: 1.2 }));
  });
});
