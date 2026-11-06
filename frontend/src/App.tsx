import { MantineProvider } from '@mantine/core';
import { Notifications, notifications } from '@mantine/notifications';
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { createBrowserRouter, RouterProvider, type RouteObject } from 'react-router';
import { setUnauthorizedListener } from './api/client';
import { EventStreamProvider } from './api/EventStreamProvider';
import { keys } from './api/keys';
import { useAuthStatus } from './api/system';
import type { AuthStatus } from './api/types';
import { AppLayout } from './components/AppLayout';
import { ErrorState, LoadingState } from './components/DataState';
import { LoginScreen } from './components/LoginScreen';
import { needsLoginScreen } from './lib/auth';
import { routerBasename } from './lib/basename';
import { ModelsPage } from './pages/ModelsPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { SettingsPage } from './pages/SettingsPage';
import { UsagePage } from './pages/UsagePage';
import { cssVariablesResolver, theme } from './theme';

const ROUTES: RouteObject[] = [
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <ModelsPage /> },
      { path: 'usage', element: <UsagePage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: 'settings/:section', element: <SettingsPage /> },
      { path: 'settings/:section/:sub', element: <SettingsPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];

function Routes() {
  const router = useMemo(() => createBrowserRouter(ROUTES, { basename: routerBasename() }), []);
  return <RouterProvider router={router} />;
}

/**
 * Shows the login card while auth is on, this browser has no session and reading is not open; any 401 brings it
 * back. When reading is open a 401 means a change needs the password, so it says so and leaves the page alone.
 */
export function AuthGate() {
  const client = useQueryClient();
  const auth = useAuthStatus();

  useEffect(() => {
    setUnauthorizedListener(() => {
      const known = client.getQueryData<AuthStatus>(keys.auth);
      client.setQueryData(keys.auth, { enabled: true, authenticated: false, open_read: known?.open_read ?? false });
      if (known?.open_read) {
        notifications.show({
          id: 'login-needed',
          color: 'yellow',
          message: 'That needs the password. Log in under Settings.',
        });
      }
    });
    return () => setUnauthorizedListener(null);
  }, [client]);

  if (auth.data === undefined) {
    return auth.isError ? (
      <ErrorState title="Vibonomics is unreachable" message={auth.error.message} />
    ) : (
      <LoadingState />
    );
  }
  // Past this point a failed refetch (a restart, a dropped connection) keeps the status it had: tearing the pages
  // down would throw away whatever is half-typed in them for a blip that the retry in useAuthStatus clears.
  if (needsLoginScreen(auth.data)) {
    return <LoginScreen />;
  }
  return (
    <EventStreamProvider>
      <Routes />
    </EventStreamProvider>
  );
}

export function App({ env }: { env?: 'default' | 'test' }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false, refetchOnWindowFocus: false },
        },
      }),
  );

  return (
    <MantineProvider theme={theme} cssVariablesResolver={cssVariablesResolver} defaultColorScheme="auto" env={env}>
      <Notifications />
      <QueryClientProvider client={queryClient}>
        <AuthGate />
      </QueryClientProvider>
    </MantineProvider>
  );
}
