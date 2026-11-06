import { screen, within } from '@testing-library/react';
import type userEvent from '@testing-library/user-event';
import { NO_GUARDRAILS, type PresetGuardrails } from '../../api/types';
import { item, live, renderSection, savedPresets, SELECTED_KEY, serve } from './presetTestKit';

/** Shared by the tests of the Guardrails card: a managed preset on OpenRouter, and the ways to drive its controls. */

export const SLUG = 'vibonomics-best-value';
export const SECOND = 'vibonomics-second';

export const LIVE_CONFIG = {
  model: 'acme/one',
  temperature: 0.2,
  max_tokens: 4096,
  repetition_penalty: 1.1,
  provider: { only: ['deepinfra'], quantizations: ['fp8'], ignore: ['badhost'], require_parameters: true },
};

/** The label of each guardrail, in the order of the form. */
export const LABELS = [
  'Temperature',
  'Max tokens',
  'Repetition penalty',
  'Frequency penalty',
  'Presence penalty',
  'Top p',
  'Quantizations',
  'Ignored providers',
  'Require parameters',
  'System prompt',
] as const;

type User = ReturnType<typeof userEvent.setup>;

/** A managed preset that is on OpenRouter with those settings, writing off, so Save sends no update. */
export function setup(guardrails: Partial<PresetGuardrails> = {}, extra: Parameters<typeof serve>[0] = {}) {
  serve({
    items: [item({ enabled: false, guardrails: { ...NO_GUARDRAILS, ...guardrails } })],
    catalog: [live(SLUG, { managed: true, config: LIVE_CONFIG, has_system_prompt: true })],
    ...extra,
  });
  localStorage.setItem(SELECTED_KEY, JSON.stringify(SLUG));
}

/** The page, once the guardrails are on it. */
export async function show() {
  renderSection();
  await screen.findByRole('heading', { name: 'Guardrails' });
}

export const requireSwitch = () => screen.getByRole('switch', { name: /support every parameter/ });
export const group = (label: string) => screen.getByRole('radiogroup', { name: `${label} setting` });
export const modeOf = (label: string) =>
  within(group(label)).getByRole('radio', { checked: true }).getAttribute('value');

export async function choose(user: User, label: string, mode: 'Leave' | 'Set' | 'Remove') {
  await user.click(within(await screen.findByRole('radiogroup', { name: `${label} setting` })).getByLabelText(mode));
}

/** What the page last saved for the first preset's guardrails. */
export const saved = () => savedPresets()?.items[0].guardrails;

export const save = (user: User) => user.click(screen.getByRole('button', { name: 'Save' }));
