import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { PresetPlan } from '../../api/types';
import { tone } from '../../lib/tone';
import { PresetPlanView } from './PresetPlanView';
import { plan } from './presetTestKit';

type Prompt = PresetPlan['system_prompt'];

const LIVE_TEXT = 'You are a careful assistant.\nNever run commands without asking.';
const PLANNED_TEXT = 'You are a terse assistant.';

function view(over: Partial<PresetPlan> = {}) {
  return render(
    <MantineProvider>
      <PresetPlanView plan={plan(over)} />
    </MantineProvider>,
  );
}

const prompt = (over: Partial<Prompt>): Pick<PresetPlan, 'system_prompt'> => ({
  system_prompt: { live: null, planned: null, source: 'none', changed: false, ...over },
});

describe('PresetPlanView guardrails this update writes', () => {
  it('lists what is written as key: value rows and what is removed as removed: key rows', () => {
    view({
      guardrails: {
        set: {
          temperature: 0.5,
          max_tokens: 4000,
          quantizations: ['fp8', 'bf16'],
          ignore: ['deepinfra'],
          require_parameters: true,
        },
        cleared: ['top_p', 'repetition_penalty'],
      },
    });
    expect(screen.getByText('Guardrails this update writes', { selector: 'p' })).toBeInTheDocument();
    const rows = within(screen.getByRole('list', { name: 'Guardrails this update writes' })).getAllByRole('listitem');
    expect(rows.map((row) => row.textContent)).toEqual([
      'temperature: 0.5',
      'max_tokens: 4000',
      'quantizations: fp8, bf16',
      'ignore: deepinfra',
      'require_parameters: true',
      'removed: top_p',
      'removed: repetition_penalty',
    ]);
    expect(within(rows[0]).getByText('temperature', { selector: 'code' })).toBeInTheDocument();
    expect(within(rows[5]).getByText('top_p', { selector: 'code' })).toBeInTheDocument();
  });

  it('shows a false value as false, not as nothing', () => {
    view({ guardrails: { set: { require_parameters: false }, cleared: [] } });
    expect(screen.getByRole('listitem')).toHaveTextContent('require_parameters: false');
  });

  it('says how long the system prompt is in its row, and leaves the text to the prompt block', () => {
    view({
      guardrails: { set: { system_prompt: PLANNED_TEXT }, cleared: [] },
      ...prompt({ live: LIVE_TEXT, planned: PLANNED_TEXT, source: 'managed', changed: true }),
    });
    const row = screen.getByRole('list', { name: 'Guardrails this update writes' });
    expect(row).toHaveTextContent('system_prompt: 26 characters, shown below');
    expect(row).not.toHaveTextContent(PLANNED_TEXT);
  });

  it('lists only the removed keys when nothing is written, and is hidden when there is neither', () => {
    const { unmount } = view({ guardrails: { set: {}, cleared: ['system_prompt'] } });
    expect(screen.getByRole('listitem')).toHaveTextContent('removed: system_prompt');
    unmount();
    view({ guardrails: { set: {}, cleared: [] } });
    expect(screen.queryByText('Guardrails this update writes')).toBeNull();
    expect(screen.queryByRole('list', { name: 'Guardrails this update writes' })).toBeNull();
  });
});

describe('PresetPlanView system prompt', () => {
  it('shows the live and the planned prompt side by side, each folded away until it is opened', async () => {
    const user = userEvent.setup();
    view(prompt({ live: LIVE_TEXT, planned: PLANNED_TEXT, source: 'managed', changed: true }));
    expect(screen.getByText('System prompt', { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByText('Live')).toBeInTheDocument();
    expect(screen.getByText('Planned')).toBeInTheDocument();

    const live = screen.getByRole('button', { name: 'Show live prompt (63 characters)' });
    const planned = screen.getByRole('button', { name: 'Show planned prompt (26 characters)' });
    expect(live).toHaveAttribute('aria-expanded', 'false');
    expect(planned).toHaveAttribute('aria-expanded', 'false');
    // a closed section is rendered at low priority, so it can arrive a moment after its button
    expect(await screen.findByLabelText('Live system prompt')).not.toBeVisible();
    expect(screen.getByLabelText('Planned system prompt')).not.toBeVisible();

    await user.click(live);
    await waitFor(() => expect(screen.getByLabelText('Live system prompt')).toBeVisible());
    expect(screen.getByLabelText('Live system prompt').textContent).toBe(LIVE_TEXT);
    expect(screen.getByLabelText('Planned system prompt')).not.toBeVisible();
    await user.click(planned);
    await waitFor(() => expect(screen.getByLabelText('Planned system prompt')).toBeVisible());
    expect(screen.getByLabelText('Planned system prompt').textContent).toBe(PLANNED_TEXT);
  });

  it('says the live side is empty when the preset has no prompt yet, and shows the new one', () => {
    view(prompt({ live: null, planned: PLANNED_TEXT, source: 'managed', changed: true }));
    expect(screen.getByText('None')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Show live prompt/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Show planned prompt (26 characters)' })).toBeInTheDocument();
  });

  it('says a managed prompt is already live when nothing changes, and still lets it be read', () => {
    view(prompt({ live: PLANNED_TEXT, planned: PLANNED_TEXT, source: 'managed', changed: false }));
    expect(screen.getByText('The prompt Vibonomics manages is already on OpenRouter.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Show live prompt/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Show planned prompt (26 characters)' })).toBeInTheDocument();
  });

  it('says the live prompt is resent unchanged, with its length, when it is not managed', () => {
    view(prompt({ live: LIVE_TEXT, planned: LIVE_TEXT, source: 'kept', changed: false }));
    expect(screen.getByText('The live prompt (63 characters) is resent unchanged.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Show/ })).toBeNull();
    expect(screen.queryByLabelText('Live system prompt')).toBeNull();
  });

  it('says it is one character where it is, and the count has a thousands separator', () => {
    const { unmount } = view(prompt({ live: 'x', planned: 'x', source: 'kept' }));
    expect(screen.getByText('The live prompt (1 character) is resent unchanged.')).toBeInTheDocument();
    unmount();
    view(prompt({ live: 'x'.repeat(12345), planned: 'x'.repeat(12345), source: 'kept' }));
    expect(screen.getByText('The live prompt (12,345 characters) is resent unchanged.')).toBeInTheDocument();
  });

  it('says the prompt will be removed when it is cleared', () => {
    view(prompt({ live: LIVE_TEXT, planned: null, source: 'cleared', changed: true }));
    expect(screen.getByText('The prompt will be removed.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Show/ })).toBeNull();
  });

  it('shows nothing about a preset that has no prompt and sets none', () => {
    view(prompt({ source: 'none' }));
    expect(screen.queryByText('System prompt', { selector: 'p' })).toBeNull();
    expect(screen.queryByText(/resent unchanged|will be removed|backed up/)).toBeNull();
  });
});

describe('PresetPlanView the system prompt backup', () => {
  const ago = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString();

  it('says when the previous prompt was backed up, beside the prompt', () => {
    view({
      ...prompt({ live: PLANNED_TEXT, planned: PLANNED_TEXT, source: 'managed', changed: false }),
      system_backup_at: ago(3),
    });
    expect(screen.getByText('The previous prompt was backed up 3 h ago.')).toBeInTheDocument();
  });

  it('shows the line even when the prompt is now gone, which is when the backup matters', () => {
    view({ ...prompt({ source: 'none' }), system_backup_at: ago(50) });
    expect(screen.getByText('System prompt', { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByText('The previous prompt was backed up 2 days ago.')).toBeInTheDocument();
  });

  it('has no backup line when there was no backup', () => {
    view({ ...prompt({ live: LIVE_TEXT, planned: LIVE_TEXT, source: 'kept' }), system_backup_at: null });
    expect(screen.queryByText(/backed up/)).toBeNull();
  });
});

describe('PresetPlanView where the guardrails apply', () => {
  const FLASH = {
    model_id: 'deepseek/flash',
    name: 'Flash',
    score: 71.4,
    task_cost: 0.055,
    value: 1298,
    discount: 0.3,
  };
  const capabilities: PresetPlan['capabilities'] = [
    {
      model_id: 'deepseek/flash',
      provider: 'deepinfra/fp8',
      supports: { temperature: 'yes', repetition_penalty: 'no' },
    },
    {
      model_id: 'acme/second',
      provider: null,
      supports: { temperature: 'unknown', repetition_penalty: 'yes' },
    },
  ];

  it('has a row for each model with its pinned provider or no pin, and a column for each parameter', () => {
    view({ picks: [FLASH], pick: FLASH, capabilities });
    expect(screen.getByText('Where the guardrails apply', { selector: 'p' })).toBeInTheDocument();
    const table = within(screen.getByRole('table', { name: 'Where the guardrails apply' }));
    expect(table.getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Model',
      'Provider',
      'temperature',
      'repetition_penalty',
    ]);
    const [, first, second] = table.getAllByRole('row');
    // the model is linked to its OpenRouter page, by its name where the plan has one, as elsewhere in the plan
    const link = within(first).getByRole('link', { name: 'Open Flash on OpenRouter' });
    expect(link).toHaveAttribute('href', 'https://openrouter.ai/deepseek/flash');
    expect(link).toHaveAttribute('target', '_blank');
    expect(within(first).getByText('deepinfra/fp8', { selector: 'code' })).toBeInTheDocument();
    expect(within(second).getByRole('link', { name: 'Open acme/second on OpenRouter' })).toHaveAttribute(
      'href',
      'https://openrouter.ai/acme/second',
    );
    expect(within(second).getByText('no pin')).toBeInTheDocument();
  });

  it('says in words, and with an icon, whether each parameter is supported, ignored or not known', () => {
    view({ picks: [FLASH], pick: FLASH, capabilities });
    const table = within(screen.getByRole('table', { name: 'Where the guardrails apply' }));
    const [, first, second] = table.getAllByRole('row');
    const cells = (row: HTMLElement) => within(row).getAllByRole('cell').slice(2);
    expect(cells(first).map((c) => c.textContent)).toEqual(['Supported', 'Not supported: ignored']);
    expect(cells(second).map((c) => c.textContent)).toEqual(['Unknown', 'Supported']);
    for (const cell of [...cells(first), ...cells(second)]) {
      expect(cell.querySelector('svg')).not.toBeNull();
      expect(cell.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
    }
  });

  it('colours each state with tone(), so the text clears AA in both colour schemes', () => {
    view({ picks: [FLASH], pick: FLASH, capabilities });
    const style = (text: string, index = 0) => screen.getAllByText(text)[index].getAttribute('style') ?? '';
    expect(style('Supported')).toContain(tone('green'));
    expect(style('Not supported: ignored')).toContain(tone('red'));
    expect(style('Unknown')).toContain(tone('gray'));
    const icon = (text: string) => screen.getAllByText(text)[0].parentElement?.querySelector('svg');
    expect(icon('Supported')?.getAttribute('style') ?? '').toContain(tone('green'));
    expect(icon('Not supported: ignored')?.getAttribute('style') ?? '').toContain(tone('red'));
    expect(icon('Unknown')?.getAttribute('style') ?? '').toContain(tone('gray'));
  });

  it('takes a parameter that one row lacks as not known', () => {
    view({
      capabilities: [
        { model_id: 'a/one', provider: 'x', supports: { temperature: 'yes', top_p: 'no' } },
        { model_id: 'a/two', provider: 'y', supports: { temperature: 'yes' } },
      ],
    });
    const rows = within(screen.getByRole('table', { name: 'Where the guardrails apply' })).getAllByRole('row');
    expect(within(rows[2]).getAllByRole('cell').at(-1)).toHaveTextContent('Unknown');
  });

  it('is hidden when no parameter needs an endpoint that supports it', () => {
    view({ capabilities: [] });
    expect(screen.queryByText('Where the guardrails apply')).toBeNull();
    expect(screen.queryByRole('table', { name: 'Where the guardrails apply' })).toBeNull();
  });
});
