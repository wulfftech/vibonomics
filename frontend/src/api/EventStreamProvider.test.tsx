import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EventStreamProvider } from './EventStreamProvider';
import { keys } from './keys';

class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readyState = FakeEventSource.CONNECTING;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((message: MessageEvent<string>) => void) | null = null;

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
}

let client: QueryClient;

function mount() {
  return render(
    <QueryClientProvider client={client}>
      <EventStreamProvider>
        <div>app</div>
      </EventStreamProvider>
    </QueryClientProvider>,
  );
}

function latest(): FakeEventSource {
  const source = FakeEventSource.instances.at(-1);
  if (source === undefined) {
    throw new Error('no EventSource was opened');
  }
  return source;
}

function stale(key: readonly unknown[]): boolean | undefined {
  return client.getQueryState(key)?.isInvalidated;
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  for (const key of [keys.models, keys.settings, keys.providers, keys.auth, keys.refreshStatus]) {
    client.setQueryData(key, { seeded: true });
  }
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('EventStreamProvider reconnects', () => {
  it('leaves the data alone when the stream first opens, apart from the refresh status', () => {
    mount();
    act(() => latest().onopen?.());
    expect(stale(keys.refreshStatus)).toBe(true);
    expect(stale(keys.models)).toBe(false);
    expect(stale(keys.settings)).toBe(false);
    expect(stale(keys.providers)).toBe(false);
  });

  it('refetches everything when the stream opens again, because the server does not replay missed events', () => {
    mount();
    act(() => latest().onopen?.());
    act(() => {
      latest().onerror?.();
      latest().onopen?.();
    });
    expect(stale(keys.models)).toBe(true);
    expect(stale(keys.settings)).toBe(true);
    expect(stale(keys.providers)).toBe(true);
  });

  it('treats the open after a hand-made retry as a reconnect too', () => {
    vi.useFakeTimers();
    mount();
    act(() => latest().onopen?.());
    act(() => {
      latest().readyState = FakeEventSource.CLOSED;
      latest().onerror?.();
    });
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(stale(keys.models)).toBe(false);
    act(() => latest().onopen?.());
    expect(stale(keys.models)).toBe(true);
  });
});

describe('EventStreamProvider and a lost session', () => {
  it('asks the auth status again when the stream is closed for good, so the login card can show', () => {
    mount();
    act(() => latest().onopen?.());
    expect(stale(keys.auth)).toBe(false);
    act(() => {
      latest().readyState = FakeEventSource.CLOSED;
      latest().onerror?.();
    });
    expect(stale(keys.auth)).toBe(true);
  });

  it('does not touch the auth status while the browser is still retrying by itself', () => {
    mount();
    act(() => latest().onopen?.());
    act(() => {
      latest().readyState = FakeEventSource.CONNECTING;
      latest().onerror?.();
    });
    expect(stale(keys.auth)).toBe(false);
  });
});

describe('EventStreamProvider and the presets', () => {
  it('refreshes the catalogue, the run records, the inspections and the settings when a preset changes', () => {
    const touched = [keys.presetCatalog, keys.presetState, keys.presetInspect('hand-made'), keys.settings];
    for (const key of touched) {
      client.setQueryData(key, { seeded: true });
    }
    mount();
    act(() => latest().onopen?.());
    expect(touched.map(stale)).toEqual([false, false, false, false]);

    const message = { data: JSON.stringify({ event: 'presets.changed', data: {} }) } as MessageEvent<string>;
    act(() => latest().onmessage?.(message));
    expect(touched.map(stale)).toEqual([true, true, true, true]);
    expect(stale(keys.models)).toBe(false);
  });
});
