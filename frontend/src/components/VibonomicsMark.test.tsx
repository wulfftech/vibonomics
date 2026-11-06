import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { VibonomicsMark } from './VibonomicsMark';

describe('VibonomicsMark', () => {
  it('is a 16 px image named for what it says, drawn from the favicon', () => {
    render(
      <MantineProvider>
        <VibonomicsMark />
      </MantineProvider>,
    );
    const mark = screen.getByRole('img', { name: 'Managed by Vibonomics' });
    expect(mark).toHaveAttribute('width', '16');
    expect(mark).toHaveAttribute('height', '16');
    expect(mark.querySelectorAll('path')).toHaveLength(2);
    expect(mark.querySelector('path')).toHaveAttribute('d', 'M3 3v16a2 2 0 0 0 2 2h16');
  });

  it('explains itself on hover', async () => {
    const user = userEvent.setup();
    render(
      <MantineProvider>
        <VibonomicsMark size={20} />
      </MantineProvider>,
    );
    const mark = screen.getByRole('img', { name: 'Managed by Vibonomics' });
    expect(mark).toHaveAttribute('width', '20');
    await user.hover(mark);
    // The tooltip opens after a transition, which a loaded full run can push past the default one second.
    expect(await screen.findByRole('tooltip', {}, { timeout: 5000 })).toHaveTextContent('Managed by Vibonomics');
  });
});
