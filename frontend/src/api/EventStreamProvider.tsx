import { notifications } from '@mantine/notifications';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { EventStreamContext } from './eventStream';
import { keys } from './keys';
import type { EvalRunOut, RefreshKind, RefreshStatus } from './types';

/**
 * The live update stream (`GET api/events`, server-sent events). Each message is JSON
 * `{event, data}`; this invalidates the queries the event touches and raises the toasts.
 */

type Payload = Record<string, unknown>;

const KIND_LABELS: Record<string, string> = {
  models: 'Models & prices',
  balances: 'Balances',
  benchmarks: 'Benchmarks',
  usage: 'Spend history',
};

function str(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

function num(value: unknown): number | undefined {
  return typeof value === 'number' ? value : undefined;
}

function setRunning(client: QueryClient, kind: string, running: boolean): void {
  client.setQueryData<RefreshStatus>(keys.refreshStatus, (old) => {
    const current = new Set(old?.running ?? []);
    if (running) {
      current.add(kind);
    } else {
      current.delete(kind);
    }
    return { running: [...current], last: old?.last ?? {} };
  });
}

function invalidateForRefresh(client: QueryClient, kind: string): void {
  void client.invalidateQueries({ queryKey: keys.providers });
  if (kind === 'models' || kind === 'benchmarks') {
    void client.invalidateQueries({ queryKey: keys.models });
    void client.invalidateQueries({ queryKey: keys.judges });
  }
  if (kind === 'usage') {
    void client.invalidateQueries({ queryKey: keys.usage });
  }
  if (kind === 'benchmarks') {
    void client.invalidateQueries({ queryKey: keys.settings });
    void client.invalidateQueries({ queryKey: keys.benchmarkRows });
  }
  void client.invalidateQueries({ queryKey: keys.refreshStatus });
  void client.invalidateQueries({ queryKey: keys.refreshRuns });
}

export function EventStreamProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [connected, setConnected] = useState(true);
  const [progress, setProgress] = useState<Partial<Record<string, string>>>({});
  const requested = useRef(new Set<string>());

  const markRequested = useCallback((kinds: RefreshKind[]) => {
    for (const kind of kinds) {
      requested.current.add(kind);
    }
  }, []);

  const handle = useCallback(
    (event: string, data: Payload) => {
      switch (event) {
        case 'refresh.started': {
          const kind = str(data.kind);
          if (kind) {
            setRunning(client, kind, true);
          }
          break;
        }
        case 'refresh.progress': {
          const kind = str(data.kind);
          const provider = str(data.provider) ?? str(data.message);
          if (kind && provider) {
            setProgress((old) => ({ ...old, [kind]: provider }));
          }
          break;
        }
        case 'refresh.finished': {
          const kind = str(data.kind) ?? '';
          const status = str(data.status) ?? 'ok';
          const message = str(data.message);
          setRunning(client, kind, false);
          setProgress((old) => ({ ...old, [kind]: undefined }));
          invalidateForRefresh(client, kind);
          const label = KIND_LABELS[kind] ?? kind;
          const wasRequested = requested.current.delete(kind);
          // A single-provider refresh is quiet unless it fails; the card shows its own spinner.
          const single = data.provider !== undefined;
          if (status === 'failed') {
            notifications.show({ message: `${label} refresh failed${message ? `: ${message}` : ''}`, color: 'red' });
          } else if (status === 'partial') {
            notifications.show({
              message: `${label} refresh partly failed${message ? `: ${message}` : ''}`,
              color: 'yellow',
            });
          } else if (wasRequested && !single) {
            notifications.show({ message: `${label} refreshed`, color: 'green' });
          }
          break;
        }
        case 'providers.changed':
          void client.invalidateQueries({ queryKey: keys.providers });
          void client.invalidateQueries({ queryKey: keys.models });
          void client.invalidateQueries({ queryKey: keys.judges });
          break;
        case 'settings.changed':
          void client.invalidateQueries({ queryKey: keys.settings });
          void client.invalidateQueries({ queryKey: keys.models });
          void client.invalidateQueries({ queryKey: keys.system });
          break;
        case 'presets.changed':
          // After a catalogue sync, a write and an adopt. Settings too: an adopt adds an item, and removing an adopted
          // one hands it back, so the list of managed presets may have changed under the page.
          void client.invalidateQueries({ queryKey: keys.presetState });
          void client.invalidateQueries({ queryKey: keys.presetCatalog });
          void client.invalidateQueries({ queryKey: keys.presetInspections });
          void client.invalidateQueries({ queryKey: keys.settings });
          break;
        case 'subscription.updated':
          void client.invalidateQueries({ queryKey: keys.claudeSubscription });
          void client.invalidateQueries({ queryKey: keys.providers });
          break;
        case 'benchmarks.changed':
          void client.invalidateQueries({ queryKey: keys.models });
          void client.invalidateQueries({ queryKey: keys.settings });
          void client.invalidateQueries({ queryKey: keys.benchmarkRows });
          break;
        case 'evals.progress': {
          const runId = num(data.run_id);
          if (runId === undefined) {
            break;
          }
          let known = false;
          client.setQueryData<EvalRunOut[]>(keys.runs, (old) =>
            old?.map((run) => {
              if (run.id !== runId) {
                return run;
              }
              known = true;
              return {
                ...run,
                status: run.status === 'queued' && (num(data.done) ?? 0) > 0 ? 'running' : run.status,
                progress: { ...run.progress, done: data.done, total: data.total, current: data.current },
              };
            }),
          );
          if (!known) {
            void client.invalidateQueries({ queryKey: keys.runs, exact: true });
          }
          void client.invalidateQueries({ queryKey: keys.run(runId) });
          break;
        }
        case 'evals.finished': {
          const runId = num(data.run_id);
          const status = str(data.status) ?? '';
          void client.invalidateQueries({ queryKey: keys.runs });
          void client.invalidateQueries({ queryKey: keys.models });
          if (runId !== undefined && status !== 'deleted') {
            if (status === 'done') {
              notifications.show({ message: `Test run #${runId} finished`, color: 'green' });
            } else if (status === 'failed') {
              notifications.show({ message: `Test run #${runId} failed`, color: 'red' });
            } else if (status === 'cancelled') {
              notifications.show({ message: `Test run #${runId} cancelled` });
            }
          }
          break;
        }
        default:
          break;
      }
    },
    [client],
  );

  useEffect(() => {
    if (typeof EventSource === 'undefined') {
      return undefined;
    }
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    let opened = false;

    const open = () => {
      source = new EventSource('api/events');
      source.onopen = () => {
        setConnected(true);
        if (opened) {
          // The server does not replay what was missed while the stream was down (a sleeping laptop, a dropped
          // proxy), and nothing refetches on focus, so anything on screen may be stale.
          void client.invalidateQueries();
        } else {
          opened = true;
          void client.invalidateQueries({ queryKey: keys.refreshStatus });
        }
      };
      source.onerror = () => {
        setConnected(false);
        // The browser retries on its own unless the response was an error; then reopen by hand.
        if (source?.readyState === EventSource.CLOSED && !disposed) {
          source.close();
          // A 401 (the session is gone) only ever shows up here. Ask the auth status, which the guard exempts,
          // so the login card replaces the stale page instead of this loop retrying over it.
          void client.invalidateQueries({ queryKey: keys.auth });
          retry = setTimeout(open, 5000);
        }
      };
      source.onmessage = (message: MessageEvent<string>) => {
        try {
          const parsed: unknown = JSON.parse(message.data);
          if (typeof parsed === 'object' && parsed !== null && 'event' in parsed) {
            const { event, data } = parsed as { event: unknown; data?: unknown };
            if (typeof event === 'string') {
              handle(event, typeof data === 'object' && data !== null ? (data as Payload) : {});
            }
          }
        } catch {
          // Not one of ours; ignore.
        }
      };
    };

    open();
    return () => {
      disposed = true;
      clearTimeout(retry);
      source?.close();
    };
  }, [client, handle]);

  const value = useMemo(() => ({ connected, progress, markRequested }), [connected, progress, markRequested]);

  return <EventStreamContext.Provider value={value}>{children}</EventStreamContext.Provider>;
}
