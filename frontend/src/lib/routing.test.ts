import { describe, expect, it } from 'vitest';
import type { ModelDetail, ModelRow } from '../api/types';
import {
  DEFAULT_ROUTE,
  initialRoute,
  pinRoute,
  pinnedTag,
  routeEndpoints,
  routeGroups,
  routeQuery,
  type RouteEndpoint,
} from './routing';

const raw: ModelDetail['endpoints'] = [
  { provider: 'Wafer', tag: 'wafer/us', input: 0.045, output: 0.5, discount: 0 },
  { provider: 'DeepInfra', tag: 'deepinfra/fp8', input: 0.14, output: 0.42, discount: 0.3 },
  { provider: 'DeepInfra', tag: 'deepinfra/fp8', input: 0.14, output: 0.42, discount: 0.3 },
  { provider: 'No slug', input: 1, output: 2 },
  { provider: null, tag: 'mystery/fp4', input: null, output: 'x' },
];

const endpoints = routeEndpoints(raw);

function row(price: { input: number; output: number }, discount: number | null): ModelRow {
  return { model_id: 'acme/flash', price, discount } as unknown as ModelRow;
}

describe('routeEndpoints', () => {
  it('keeps each slug once and skips endpoints without one', () => {
    expect(endpoints.map((e) => e.tag)).toEqual(['wafer/us', 'deepinfra/fp8', 'mystery/fp4']);
  });

  it('reads what is there and nothing it cannot trust', () => {
    expect(endpoints[2]).toEqual<RouteEndpoint>({
      provider: null,
      tag: 'mystery/fp4',
      input: null,
      output: null,
      discount: 0,
    });
  });
});

describe('routeQuery', () => {
  it('has nothing to ask for under default routing', () => {
    expect(routeQuery(DEFAULT_ROUTE, true)).toBeNull();
  });

  it('asks for a variant or a pinned provider', () => {
    expect(routeQuery('floor', false)).toEqual({ variant: 'floor' });
    expect(routeQuery('nitro', false)).toEqual({ variant: 'nitro' });
    expect(routeQuery(pinRoute('deepinfra/fp8'), false)).toEqual({ provider: 'deepinfra/fp8', fallbacks: false });
    expect(routeQuery(pinRoute('deepinfra/fp8'), true)).toEqual({ provider: 'deepinfra/fp8', fallbacks: true });
  });

  it('round-trips a slug that contains slashes', () => {
    expect(pinnedTag(pinRoute('sail-research/fp4/us'))).toBe('sail-research/fp4/us');
    expect(pinnedTag('floor')).toBeNull();
  });
});

describe('initialRoute', () => {
  it('starts on the provider whose discount the row shows', () => {
    expect(initialRoute(row({ input: 0.14, output: 0.42 }, 0.3), endpoints)).toBe(pinRoute('deepinfra/fp8'));
  });

  it('starts on default routing when the row is not discounted, or its provider is not among the endpoints', () => {
    expect(initialRoute(row({ input: 0.045, output: 0.5 }, null), endpoints)).toBe(DEFAULT_ROUTE);
    expect(initialRoute(row({ input: 0.045, output: 0.5 }, 0), endpoints)).toBe(DEFAULT_ROUTE);
    expect(initialRoute(row({ input: 9, output: 9 }, 0.3), endpoints)).toBe(DEFAULT_ROUTE);
  });
});

describe('routeGroups', () => {
  it('offers the variants and every endpoint', () => {
    const groups = routeGroups('acme/flash', endpoints);
    expect(groups.map((g) => g.group)).toEqual(['OpenRouter routing', 'Pin one provider']);
    expect(groups[0].items.map((i) => i.value)).toEqual([DEFAULT_ROUTE, 'floor', 'nitro']);
    expect(groups[1].items.map((i) => i.value)).toEqual(endpoints.map((e) => pinRoute(e.tag)));
    expect(groups[1].items[1].label).toBe('DeepInfra · deepinfra/fp8 · $0.14 / $0.42 · −30%');
  });

  it('leaves out the variants for an id that already carries one, and the pins when there are none', () => {
    const groups = routeGroups('acme/flash:free', []);
    expect(groups).toHaveLength(1);
    expect(groups[0].items.map((i) => i.value)).toEqual([DEFAULT_ROUTE]);
  });
});
