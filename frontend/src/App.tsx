import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { createBrowserRouter, RouterProvider, type RouteObject } from 'react-router';
import { setUnauthorizedListener } from './api/client';
import { EventStreamProvider } from './api/EventStreamProvider';
import { keys } from './api/keys';
import { useAuthStatus } from './api/system';
import { AppLayout } from './components/AppLayout';
import { ErrorState, LoadingState } from './components/DataState';
import { LoginScreen } from './components/LoginScreen';
import { routerBasename } from './lib/basename';
import { ModelsPage } from './pages/ModelsPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { SettingsPage } from './pages/SettingsPage';
import { cssVariablesResolver, theme } from './theme';

const ROUTES: RouteObject[] = [
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <ModelsPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: 'settings/:section', element: <SettingsPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];

function Routes() {
  const router = useMemo(() => createBrowserRouter(ROUTES, { basename: routerBasename() }), []);
  return <RouterProvider router={router} />;
}

/** Shows the login card while auth is on and this browser has no session; any 401 brings it back. */
function AuthGate() {
  const client = useQueryClient();
  const auth = useAuthStatus();

  useEffect(() => {
    setUnauthorizedListener(() => {
      client.setQueryData(keys.auth, { enabled: true, authenticated: false });
    });
    return () => setUnauthorizedListener(null);
  }, [client]);

  if (auth.isPending) {
    return <LoadingState />;
  }
  if (auth.isError) {
    return <ErrorState title="Vibonomics is unreachable" message={auth.error.message} />;
  }
  if (auth.data.enabled && !auth.data.authenticated) {
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
