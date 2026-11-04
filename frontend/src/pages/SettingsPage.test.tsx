import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { SettingsPage } from './SettingsPage';

let canChange = false;
vi.mock('../api/system', () => ({
  useCanChange: () => canChange,
  useLogin: () => ({ mutate: vi.fn(), isPending: false, error: null }),
}));
// Only the gate and the tabs are under test; the sections would fetch from the API.
vi.mock('./settings/ProvidersSection', () => ({ ProvidersSection: () => <div>providers section</div> }));
vi.mock('./settings/SubscriptionSection', () => ({ SubscriptionSection: () => <div>claude section</div> }));
vi.mock('./settings/OpenRouterSection', () => ({ OpenRouterSection: () => <div>openrouter section</div> }));
vi.mock('./settings/RefreshSection', () => ({ RefreshSection: () => <div>refresh section</div> }));

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

function renderPage(path = '/settings') {
  return render(
    <MantineProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/settings/:section" element={<SettingsPage />} />
          <Route path="/settings/:section/:sub" element={<SettingsPage />} />
        </Routes>
        <Where />
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

  it('keeps provider-specific settings in a second row under Providers, not in the first', () => {
    canChange = true;
    renderPage();
    const top = screen.getAllByRole('radiogroup')[0];
    expect(Array.from(top.querySelectorAll('label')).map((l) => l.textContent)).toEqual([
      'Providers',
      'Benchmarks',
      'Scoring',
      'Refresh',
      'Testing',
      'General',
    ]);
    const second = screen.getByRole('radiogroup', { name: 'Provider settings' });
    expect(Array.from(second.querySelectorAll('label')).map((l) => l.textContent)).toEqual([
      'All providers',
      'Claude',
      'OpenRouter',
    ]);
  });

  it('opens Claude and OpenRouter from the second row', async () => {
    canChange = true;
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole('radio', { name: 'OpenRouter' }));
    expect(screen.getByText('openrouter section')).toBeInTheDocument();
    expect(screen.getByTestId('where')).toHaveTextContent('/settings/providers/openrouter');
    await user.click(screen.getByRole('radio', { name: 'Claude' }));
    expect(screen.getByText('claude section')).toBeInTheDocument();
    await user.click(screen.getByRole('radio', { name: 'All providers' }));
    expect(screen.getByText('providers section')).toBeInTheDocument();
    expect(screen.getByTestId('where')).toHaveTextContent(/^\/settings\/providers$/);
  });

  it('has no second row on the other sections', () => {
    canChange = true;
    renderPage('/settings/refresh');
    expect(screen.getByText('refresh section')).toBeInTheDocument();
    expect(screen.queryByRole('radiogroup', { name: 'Provider settings' })).toBeNull();
  });

  it('sends an old address to where the section moved', () => {
    canChange = true;
    renderPage('/settings/subscription');
    expect(screen.getByText('claude section')).toBeInTheDocument();
    expect(screen.getByTestId('where')).toHaveTextContent('/settings/providers/claude');
  });

  it('does the same for the old presets address', () => {
    canChange = true;
    renderPage('/settings/presets');
    expect(screen.getByText('openrouter section')).toBeInTheDocument();
    expect(screen.getByTestId('where')).toHaveTextContent('/settings/providers/openrouter');
  });
});
