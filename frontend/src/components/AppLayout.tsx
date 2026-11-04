import {
  ActionIcon,
  AppShell,
  Badge,
  Burger,
  Button,
  Group,
  Loader,
  NavLink as MantineNavLink,
  Text,
  Tooltip,
  useComputedColorScheme,
  useMantineColorScheme,
  VisuallyHidden,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  ChartNoAxesColumn,
  ChartNoAxesCombined,
  LayoutList,
  LogIn,
  LogOut,
  Moon,
  Settings,
  Sun,
  WifiOff,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router';
import { useEventStream } from '../api/eventStream';
import { useAuthStatus, useLogout, useRefreshStatus, useSystemStatus } from '../api/system';

const NAV_ITEMS = [
  { to: '/', label: 'Models', icon: LayoutList, match: (path: string) => path === '/' || path.startsWith('/models') },
  { to: '/usage', label: 'Usage', icon: ChartNoAxesColumn, match: (path: string) => path.startsWith('/usage') },
  { to: '/settings', label: 'Settings', icon: Settings, match: (path: string) => path.startsWith('/settings') },
] as const;

const REFRESH_LABELS: Record<string, string> = {
  models: 'Refreshing models…',
  benchmarks: 'Refreshing benchmarks…',
  balances: 'Refreshing balances…',
  usage: 'Refreshing spend history…',
};

function ColorSchemeToggle() {
  const { setColorScheme } = useMantineColorScheme();
  const computed = useComputedColorScheme('light', { getInitialValueInEffect: true });
  const isDark = computed === 'dark';
  return (
    <Tooltip label={isDark ? 'Switch to the light theme' : 'Switch to the dark theme'}>
      <ActionIcon
        variant="default"
        size="lg"
        aria-label="Toggle the colour scheme"
        onClick={() => setColorScheme(isDark ? 'light' : 'dark')}
      >
        {isDark ? <Sun size={16} /> : <Moon size={16} />}
      </ActionIcon>
    </Tooltip>
  );
}

const DISCONNECTED_MESSAGE = 'Live updates disconnected';
const DISCONNECTED_GRACE_MS = 2000;

/** `active`, but only once it has stayed true for `delayMs`; false again the moment it stops. */
function useAfterGrace(active: boolean, delayMs: number): boolean {
  const [elapsed, setElapsed] = useState(false);
  useEffect(() => {
    if (!active) {
      return undefined;
    }
    const timer = setTimeout(() => setElapsed(true), delayMs);
    return () => {
      clearTimeout(timer);
      setElapsed(false);
    };
  }, [active, delayMs]);
  return active && elapsed;
}

function RefreshIndicator() {
  const status = useRefreshStatus();
  const { progress } = useEventStream();
  const running = status.data?.running ?? [];
  const kind = ['models', 'benchmarks', 'balances', 'usage'].find((k) => running.includes(k));
  if (kind === undefined) {
    return null;
  }
  const detail = progress[kind];
  return (
    <Tooltip label={detail ? `Fetching ${detail}` : REFRESH_LABELS[kind]} disabled={!detail}>
      <Group gap={6} wrap="nowrap" role="status" aria-live="polite">
        <Loader size={14} />
        <Text size="sm" c="dimmed" visibleFrom="sm">
          {REFRESH_LABELS[kind]}
        </Text>
      </Group>
    </Tooltip>
  );
}

/** Log out when there is a session; otherwise a way to the login that unlocks Settings and every change. */
function SessionControl() {
  const auth = useAuthStatus();
  const logout = useLogout();
  if (!auth.data?.enabled) {
    return null;
  }
  if (auth.data.authenticated) {
    return (
      <Button
        variant="subtle"
        color="gray"
        size="xs"
        leftSection={<LogOut size={14} />}
        loading={logout.isPending}
        onClick={() => logout.mutate()}
      >
        Log out
      </Button>
    );
  }
  return (
    <Button component={NavLink} to="/settings" variant="subtle" size="xs" leftSection={<LogIn size={14} />}>
      Log in to make changes
    </Button>
  );
}

/** The shell: a header with the instance name and live status, a navbar, and the page. */
export function AppLayout() {
  const system = useSystemStatus();
  const { connected } = useEventStream();
  const showDisconnected = useAfterGrace(!connected, DISCONNECTED_GRACE_MS);
  const { pathname } = useLocation();
  const [navOpened, { toggle: toggleNav, close: closeNav }] = useDisclosure(false);
  const instanceName = system.data?.instance_name || 'Vibonomics';

  useEffect(() => {
    closeNav();
  }, [pathname, closeNav]);

  useEffect(() => {
    document.title = instanceName;
  }, [instanceName]);

  return (
    <AppShell
      header={{ height: 56 }}
      navbar={{ width: 220, breakpoint: 'sm', collapsed: { mobile: !navOpened } }}
      padding="md"
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={navOpened} onClick={toggleNav} hiddenFrom="sm" size="sm" aria-label="Toggle navigation" />
            <ChartNoAxesCombined size={18} color="var(--mantine-primary-color-filled)" aria-hidden />
            <Text fw={600}>{instanceName}</Text>
          </Group>
          <Group gap="sm" wrap="nowrap">
            <RefreshIndicator />
            {showDisconnected && (
              <Tooltip label={`${DISCONNECTED_MESSAGE} — reconnecting…`}>
                <Badge color="yellow" variant="light" leftSection={<WifiOff size={12} />} aria-hidden>
                  <Text span visibleFrom="sm" inherit>
                    {DISCONNECTED_MESSAGE}
                  </Text>
                </Badge>
              </Tooltip>
            )}
            <VisuallyHidden aria-live="polite" role="status">
              {showDisconnected ? DISCONNECTED_MESSAGE : ''}
            </VisuallyHidden>
            <ColorSchemeToggle />
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="xs">
        <AppShell.Section grow>
          {NAV_ITEMS.map(({ to, label, icon: Icon, match }) => (
            <MantineNavLink
              key={to}
              component={NavLink}
              to={to}
              active={match(pathname)}
              label={label}
              onClick={closeNav}
              leftSection={<Icon size={16} />}
            />
          ))}
        </AppShell.Section>
        <AppShell.Section px="xs" py={4}>
          <SessionControl />
        </AppShell.Section>
        <AppShell.Section px="sm" py={4}>
          <Text size="xs" c="dimmed">
            {system.data ? `Vibonomics ${system.data.version}` : ' '}
          </Text>
        </AppShell.Section>
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
