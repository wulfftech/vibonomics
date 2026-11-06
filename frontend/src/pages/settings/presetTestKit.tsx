import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { vi } from 'vitest';
import {
  PRESET_DEFAULTS,
  type CatalogPreset,
  type PresetCatalog,
  type PresetInspect,
  type PresetPlan,
  type PresetSettings,
  type PresetSlugState,
} from '../../api/types';
import { OpenRouterSection } from './OpenRouterSection';

/** Shared by the tests of the OpenRouter settings: fixtures, and a fetch that behaves like the server. */

export const fetchMock = vi.fn();

// jsdom has no scrollIntoView, which a Select calls to show the option that is chosen when it opens.
Element.prototype.scrollIntoView = () => undefined;

export const SELECTED_KEY = 'vibonomics.presets.selected';

const FLASH = { model_id: 'deepseek/flash', name: 'Flash', score: 71.4, task_cost: 0.055, value: 1298, discount: 0.3 };

export function plan(over: Partial<PresetPlan> = {}): PresetPlan {
  return {
    action: 'update',
    reason: 'Switches deepseek/old to Flash, 31% more value.',
    slug: 'vibonomics-best-value',
    enabled: true,
    auto_apply: false,
    route: 'fallback',
    sort: null,
    draft: false,
    generated_at: '2026-11-04T10:00:00Z',
    profile: 'agentic',
    task: { input_tokens: 600000, output_tokens: 20000, cache_hit_rate: 0.8 },
    considered: 42,
    pick: FLASH,
    picks: [FLASH],
    kept_current: false,
    providers: [],
    runners_up: [],
    preset_id: 'pre_1',
    workspace: 'team-a',
    current_version: 4,
    current_config: { model: 'deepseek/old' },
    versions: [],
    config: { model: 'deepseek/flash' },
    changes: [{ path: 'model', before: '"deepseek/old"', after: '"deepseek/flash"' }],
    request: null,
    digest: 'abc123',
    warnings: [],
    excluded: { models: 0, providers: [] },
    own_tests_preferred: false,
    passthrough: {},
    managed_keys: ['model', 'models', 'provider.only'],
    state: { owned: ['vibonomics-best-value'], slugs: {} },
    ...over,
  };
}

export function item(over: Partial<PresetSettings> = {}): PresetSettings {
  return { ...PRESET_DEFAULTS, enabled: true, ...over };
}

export function live(slug: string, over: Partial<CatalogPreset> = {}): CatalogPreset {
  return {
    slug,
    preset_id: `id-${slug}`,
    name: slug,
    status: 'active',
    version: 3,
    updated_at: '2026-11-03T00:00:00Z',
    models: [],
    providers: [],
    config: { model: 'acme/one' },
    has_system_prompt: false,
    passthrough_keys: [],
    managed: false,
    ...over,
  };
}

export function inspection(slug: string, over: Partial<PresetInspect> = {}): PresetInspect {
  return {
    slug,
    status: 'active',
    version: 3,
    updated_at: '2026-11-03T00:00:00Z',
    preset_id: 'pre_hand',
    workspace: 'team-a',
    config: { model: 'acme/one', temperature: 0.2, provider: { only: ['deepinfra'], data_collection: 'deny' } },
    has_system_prompt: false,
    models: ['acme/one'],
    providers: ['deepinfra'],
    versions: [
      { version: 3, created_at: '2026-11-03T07:36:33Z', model: 'acme/one', providers: ['deepinfra'], live: true },
      { version: 2, created_at: '2026-11-02T07:36:33Z', model: 'acme/zero', providers: [], live: false },
    ],
    managed_keys: ['model', 'models', 'route', 'provider.only'],
    passthrough: { temperature: 0.2, provider: { data_collection: 'deny' } },
    managed: false,
    adoptable: true,
    blockers: [],
    digest: 'digest-1',
    ...over,
  };
}

/** What the fake server holds, and what it was last asked to keep. */
export interface World {
  items: PresetSettings[];
  blocked_models: string[];
  blocked_providers: string[];
  catalog: CatalogPreset[];
  catalogError: string | null;
  syncedAt: string | null;
  state: Record<string, PresetSlugState>;
}

type AdoptAnswer = PresetSettings | { status: number; detail: string };

export interface ServeOptions {
  items?: PresetSettings[];
  blocked_models?: string[];
  blocked_providers?: string[];
  catalog?: CatalogPreset[] | { status: number };
  catalogError?: string | null;
  syncedAt?: string | null;
  /** What a sync finds; the catalogue after Refresh. */
  afterSync?: { catalog: CatalogPreset[]; syncedAt: string };
  state?: Record<string, PresetSlugState>;
  plan?: PresetPlan;
  inspect?: PresetInspect | ((slug: string) => PresetInspect) | { status: number; detail: string };
  adopt?: AdoptAnswer | ((sent: { slug: string; digest: string }) => AdoptAnswer);
  canChange?: boolean;
  applied?: boolean;
}

function json(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  );
}

/** Installs the fetch stub. The returned world follows every save, so a later read sees it. */
export function serve(opts: ServeOptions = {}): World {
  const world: World = {
    items: opts.items ?? [item()],
    blocked_models: opts.blocked_models ?? [],
    blocked_providers: opts.blocked_providers ?? [],
    catalog: Array.isArray(opts.catalog) ? opts.catalog : [live('vibonomics-best-value', { managed: true })],
    catalogError: opts.catalogError ?? null,
    syncedAt: opts.syncedAt === undefined ? '2026-11-05T09:00:00Z' : opts.syncedAt,
    state: opts.state ?? {},
  };
  const settingsBody = () => ({
    settings: {
      scoring: {
        default_profile: 'agentic',
        profiles: [
          {
            id: 'agentic',
            name: 'Agentic coding',
            input_weight: 20,
            output_weight: 1,
            cache_hit_rate: 0.8,
            task_input_tokens: 600000,
            task_output_tokens: 20000,
          },
        ],
      },
      presets: { items: world.items, blocked_models: world.blocked_models, blocked_providers: world.blocked_providers },
    },
    sources: [],
    sandbox: { effective_mode: 'disabled', sidecar_url_set: false },
    auth_enabled: true,
    secret_key_from_env: false,
  });
  const catalogBody = (): PresetCatalog => ({
    synced_at: world.syncedAt,
    syncing: false,
    error: world.catalogError,
    presets: world.catalog,
  });
  const body = (init?: RequestInit) =>
    JSON.parse((init?.body as string | undefined) ?? '{}') as Record<string, unknown>;

  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (url === 'api/auth/status') {
      const can = opts.canChange ?? true;
      return json({ enabled: !can, authenticated: can, open_read: !can });
    }
    if (url === 'api/settings' && method === 'PUT') {
      const sent = (body(init).settings as { presets: Omit<World, 'catalog'> & { items: PresetSettings[] } }).presets;
      world.items = sent.items;
      world.blocked_models = sent.blocked_models;
      world.blocked_providers = sent.blocked_providers;
      return json(settingsBody());
    }
    if (url === 'api/settings') {
      return json(settingsBody());
    }
    if (url === 'api/presets/state') {
      return json({ owned: [], slugs: world.state });
    }
    if (url === 'api/presets/catalog') {
      return Array.isArray(opts.catalog) || opts.catalog === undefined
        ? json(catalogBody())
        : json({ detail: 'Not authenticated' }, opts.catalog.status);
    }
    if (url === 'api/presets/sync') {
      if (opts.afterSync !== undefined) {
        world.catalog = opts.afterSync.catalog;
        world.syncedAt = opts.afterSync.syncedAt;
      }
      return json(catalogBody());
    }
    if (url === 'api/presets/plan') {
      const sent = body(init) as { slug?: string; settings?: PresetSettings };
      const slug = sent.slug ?? sent.settings?.slug ?? 'vibonomics-best-value';
      return json({
        ...(opts.plan ?? plan()),
        slug,
        draft: sent.settings !== undefined || (opts.plan?.draft ?? false),
      });
    }
    if (url === 'api/presets/apply') {
      return json({
        applied: opts.applied ?? true,
        message: 'Updated vibonomics-best-value as version 5.',
        version: 5,
        unsaved: [],
        state: { owned: [], slugs: {} },
      });
    }
    if (url === 'api/presets/inspect') {
      const slug = (body(init).slug as string | undefined) ?? '';
      const made = opts.inspect;
      // an inspection has a status of its own ("active"), so a failure is told apart by its detail
      if (made !== undefined && typeof made !== 'function' && 'detail' in made) {
        return json({ detail: made.detail }, made.status);
      }
      return json(typeof made === 'function' ? made(slug) : (made ?? inspection(slug)));
    }
    if (url === 'api/presets/adopt') {
      const sent = body(init) as { slug: string; digest: string };
      const made = typeof opts.adopt === 'function' ? opts.adopt(sent) : opts.adopt;
      if (made !== undefined && 'detail' in made) {
        return json({ detail: made.detail }, made.status);
      }
      const adopted = made ?? item({ slug: sent.slug, adopted: true, enabled: false });
      world.items = [...world.items, adopted];
      world.catalog = world.catalog.map((p) => (p.slug === adopted.slug ? { ...p, managed: true } : p));
      return json(adopted);
    }
    return json({ detail: `unexpected ${method} ${url}` }, 500);
  });
  vi.stubGlobal('fetch', fetchMock);
  return world;
}

export function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider>
        <OpenRouterSection />
      </MantineProvider>
    </QueryClientProvider>,
  );
}

export const calls = (url: string, method?: string) =>
  fetchMock.mock.calls.filter(
    ([u, init]) =>
      u === url && (method === undefined || ((init as RequestInit | undefined)?.method ?? 'GET') === method),
  );

/** The JSON body of the n-th (last by default) request to a URL. */
export function sentTo(url: string, which = -1): Record<string, unknown> {
  const call = calls(url).at(which);
  return JSON.parse(((call?.[1] as RequestInit | undefined)?.body as string | undefined) ?? '{}') as Record<
    string,
    unknown
  >;
}

/** The presets settings the page last PUT, as the server received them. */
export function savedPresets():
  { items: PresetSettings[]; blocked_models: string[]; blocked_providers: string[] } | undefined {
  const put = calls('api/settings', 'PUT').at(-1);
  if (put === undefined) {
    return undefined;
  }
  return (
    JSON.parse((put[1] as RequestInit).body as string) as { settings: { presets: ReturnType<typeof savedPresets> } }
  ).settings.presets;
}
