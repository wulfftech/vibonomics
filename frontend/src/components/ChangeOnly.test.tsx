import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ChangeOnly } from './ChangeOnly';

let canChange = true;
vi.mock('../api/system', () => ({ useCanChange: () => canChange }));

afterEach(() => {
  canChange = true;
});

function renderButton() {
  return render(
    <MantineProvider>
      <ChangeOnly reason="Log in to refresh">{(blocked) => <button disabled={blocked}>Refresh</button>}</ChangeOnly>
    </MantineProvider>,
  );
}

describe('ChangeOnly', () => {
  it('leaves the control enabled for someone who can change things', () => {
    renderButton();
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled();
  });

  it('disables it for a visitor', () => {
    canChange = false;
    renderButton();
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeDisabled();
  });
});
