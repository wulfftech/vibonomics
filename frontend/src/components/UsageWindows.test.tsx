import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { tone } from '../lib/tone';
import { UsageWindows } from './UsageWindows';

describe('UsageWindows', () => {
  it('colours the percentage with tone(), so it clears AA in dark mode as well', () => {
    render(
      <MantineProvider>
        <UsageWindows
          windows={[
            { key: 'a', label: 'Rolling', used_percentage: 95 },
            { key: 'b', label: 'Weekly', used_percentage: 75 },
            { key: 'c', label: 'Monthly', used_percentage: 10 },
          ]}
        />
      </MantineProvider>,
    );
    expect(screen.getByText('95%').getAttribute('style') ?? '').toContain(tone('red'));
    expect(screen.getByText('75%').getAttribute('style') ?? '').toContain(tone('orange'));
    expect(screen.getByText('10%').getAttribute('style') ?? '').toContain(tone('teal'));
  });
});
