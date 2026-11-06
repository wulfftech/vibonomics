import { MantineProvider } from '@mantine/core';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, Link, RouterProvider } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { SaveBar } from './SaveBar';

function bar(props: Partial<Parameters<typeof SaveBar>[0]> = {}) {
  return <SaveBar dirty={false} saving={false} onSave={vi.fn()} onReset={vi.fn()} {...props} />;
}

function renderBare(node: React.ReactNode) {
  return render(<MantineProvider>{node}</MantineProvider>);
}

/** The bar in a data router, the way the app mounts it, with a way out of the section. */
function renderRouted(dirty: boolean) {
  const router = createMemoryRouter(
    [
      {
        path: '/settings/general',
        element: (
          <>
            <Link to="/settings/refresh">Away</Link>
            {bar({ dirty })}
          </>
        ),
      },
      { path: '/settings/refresh', element: <div>refresh section</div> },
    ],
    { initialEntries: ['/settings/general'] },
  );
  render(
    <MantineProvider>
      <RouterProvider router={router} />
    </MantineProvider>,
  );
  return router;
}

function beforeUnload(): boolean {
  const event = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(event);
  return event.defaultPrevented;
}

describe('SaveBar buttons', () => {
  it('offers neither Discard nor Save until something is edited', () => {
    renderBare(bar());
    expect(screen.queryByRole('button', { name: 'Discard changes' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('saves an edited draft', () => {
    renderBare(bar({ dirty: true }));
    expect(screen.getByRole('button', { name: 'Discard changes' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
  });

  it('still offers Discard when the draft cannot be saved yet', () => {
    renderBare(bar({ dirty: true, canSave: false }));
    expect(screen.getByRole('button', { name: 'Discard changes' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });
});

describe('SaveBar and unsaved edits', () => {
  it('asks the browser to confirm closing the tab while edited, and only then', () => {
    const { rerender } = renderBare(bar());
    expect(beforeUnload()).toBe(false);
    rerender(<MantineProvider>{bar({ dirty: true })}</MantineProvider>);
    expect(beforeUnload()).toBe(true);
    rerender(<MantineProvider>{bar({ dirty: false })}</MantineProvider>);
    expect(beforeUnload()).toBe(false);
  });

  it('stops asking once the section is gone', () => {
    const { unmount } = renderBare(bar({ dirty: true }));
    unmount();
    expect(beforeUnload()).toBe(false);
  });

  it('lets the user leave a section that has no edits without a prompt', async () => {
    const router = renderRouted(false);
    await userEvent.setup().click(screen.getByRole('link', { name: 'Away' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/settings/refresh'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('asks before an edited section is left, and stays when the user keeps editing', async () => {
    const user = userEvent.setup();
    const router = renderRouted(true);
    await user.click(screen.getByRole('link', { name: 'Away' }));
    const dialog = await screen.findByRole('dialog', { name: 'Discard unsaved changes?' });
    expect(router.state.location.pathname).toBe('/settings/general');

    await user.click(within(dialog).getByRole('button', { name: 'Keep editing' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(router.state.location.pathname).toBe('/settings/general');
    expect(screen.getByRole('link', { name: 'Away' })).toBeInTheDocument();
  });

  it('leaves once the user agrees to discard', async () => {
    const user = userEvent.setup();
    const router = renderRouted(true);
    await user.click(screen.getByRole('link', { name: 'Away' }));
    const dialog = await screen.findByRole('dialog', { name: 'Discard unsaved changes?' });
    await user.click(within(dialog).getByRole('button', { name: 'Discard and leave' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/settings/refresh'));
    expect(screen.getByText('refresh section')).toBeInTheDocument();
  });
});
