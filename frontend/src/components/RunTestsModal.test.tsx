import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import type { ModelRow } from '../api/types';
import { RunTestsModal } from './RunTestsModal';

vi.mock('../api/evals', () => ({
  useSuites: () => ({ data: [], isPending: false, isError: false }),
  useJudges: () => ({ data: [], isPending: false }),
  useStartRun: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('../api/settings', () => ({ useSettings: () => ({ data: undefined }) }));
vi.mock('../api/system', () => ({ useSystemStatus: () => ({ data: undefined }) }));

const targets = [
  { id: 1, name: 'Alpha', provider_id: 1, provider_name: 'OpenRouter', model_id: 'acme/alpha' },
  { id: 2, name: 'Bravo', provider_id: 1, provider_name: 'OpenRouter', model_id: 'acme/bravo' },
] as unknown as ModelRow[];

function renderModal(onRemove: (id: number) => void) {
  return render(
    <MantineProvider>
      <MemoryRouter>
        <RunTestsModal
          opened
          targets={targets}
          onRemove={onRemove}
          onClose={() => undefined}
          onStarted={() => undefined}
        />
      </MemoryRouter>
    </MantineProvider>,
  );
}

/** Presses Tab from wherever focus is until it reaches the element, as a keyboard user would. */
async function tabTo(user: UserEvent, element: HTMLElement) {
  // The modal moves focus to its first control once it has opened; focusing something earlier would be undone.
  await waitFor(() => expect(document.activeElement).not.toBe(document.body));
  for (let step = 0; step < 20 && document.activeElement !== element; step += 1) {
    await user.tab();
  }
  expect(element).toHaveFocus();
}

describe('RunTestsModal, dropping a model from a run', () => {
  it('has a remove button per model that a screen reader can name', () => {
    renderModal(() => undefined);
    expect(screen.getByRole('button', { name: 'Remove Alpha · OpenRouter' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove Bravo · OpenRouter' })).toBeInTheDocument();
  });

  it('drops the model on Enter and on Space, reaching the button with Tab', async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    renderModal(onRemove);

    await tabTo(user, screen.getByRole('button', { name: 'Remove Alpha · OpenRouter' }));
    await user.keyboard('{Enter}');
    expect(onRemove).toHaveBeenLastCalledWith(1);

    await tabTo(user, screen.getByRole('button', { name: 'Remove Bravo · OpenRouter' }));
    await user.keyboard(' ');
    expect(onRemove).toHaveBeenLastCalledWith(2);
    expect(onRemove).toHaveBeenCalledTimes(2);
  });

  it('still drops the model for a mouse click', async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    renderModal(onRemove);
    await user.click(screen.getByRole('button', { name: 'Remove Bravo · OpenRouter' }));
    expect(onRemove).toHaveBeenCalledExactlyOnceWith(2);
  });
});
