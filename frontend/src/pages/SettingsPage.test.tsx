import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { SettingsPage } from './SettingsPage';

let canChange = false;
vi.mock('../api/system', () => ({
  useCanChange: () => canChange,
  useLogin: () => ({ mutate: vi.fn(), isPending: false, error: null }),
}));
// Only the gate is under test; the sections would fetch from the API.
vi.mock('./settings/ProvidersSection', () => ({ ProvidersSection: () => <div>providers section</div> }));

function renderPage() {
  return render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/settings']}>
        <SettingsPage />
      </MemoryRouter>
    </MantineProvider>,
  );
}

describe('SettingsPage', () => {
  it('asks a visitor for the password instead of showing the settings', () => {
    canChange = false;
    renderPage();
    expect(screen.getByText(/Settings are password protected/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Password')).toBeInTheDocument();
    expect(screen.queryByText('providers section')).not.toBeInTheDocument();
  });

  it('shows the settings once the browser can change things', () => {
    canChange = true;
    renderPage();
    expect(screen.getByText('providers section')).toBeInTheDocument();
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
  });
});
