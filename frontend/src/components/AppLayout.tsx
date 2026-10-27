import {
  ActionIcon,
  AppShell,
  Badge,
  Burger,
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
import { ChartNoAxesCombined, LayoutList, Moon, Settings, Sun, WifiOff } from 'lucide-react';
import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router';
import { useEventStream } from '../api/eventStream';
import { useRefreshStatus, useSystemStatus } from '../api/system';

const NAV_ITEMS = [
  { to: '/', label: 'Models', icon: LayoutList, match: (path: string) => path === '/' || path.startsWith('/models') },
  { to: '/settings', label: 'Settings', icon: Settings, match: (path: string) => path.startsWith('/settings') },
] as const;

const REFRESH_LABELS: Record<string, string> = {
  models: 'Refreshing models…',
  benchmarks: 'Refreshing benchmarks…',
  balances: 'Refreshing balances…',
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
  const kind = ['models', 'benchmarks', 'balances'].find((k) => running.includes(k));
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
