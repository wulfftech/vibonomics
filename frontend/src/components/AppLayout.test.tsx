import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ColorSchemeToggle } from './AppLayout';

// AppLayout pulls in the event stream and several queries; the colour control needs none of them.
vi.mock('../api/eventStream', () => ({ useEventStream: () => ({}) }));
vi.mock('../api/system', () => ({}));

// Mantine's own storage key and values: a choice made before this control had a third state must keep working.
const STORAGE_KEY = 'mantine-color-scheme-value';

function renderToggle(stored?: string) {
  if (stored !== undefined) {
    window.localStorage.setItem(STORAGE_KEY, stored);
  }
  return render(
    <MantineProvider defaultColorScheme="auto">
      <ColorSchemeToggle />
    </MantineProvider>,
  );
}

describe('ColorSchemeToggle', () => {
  beforeEach(() => window.localStorage.clear());

  it('says which scheme is set and what a click switches to', () => {
    renderToggle('dark');
    expect(screen.getByRole('button', { name: 'Colour scheme: Dark. Switch to Match system' })).toBeInTheDocument();
  });

  it('steps from system to light to dark and back to system, so the OS can be followed again', async () => {
    const user = userEvent.setup();
    renderToggle();
    expect(screen.getByRole('button', { name: /^Colour scheme: Match system/ })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^Colour scheme/ }));
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('light');
    expect(screen.getByRole('button', { name: 'Colour scheme: Light. Switch to Dark' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^Colour scheme/ }));
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('dark');

    await user.click(screen.getByRole('button', { name: /^Colour scheme/ }));
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('auto');
    expect(screen.getByRole('button', { name: 'Colour scheme: Match system. Switch to Light' })).toBeInTheDocument();
  });
});
