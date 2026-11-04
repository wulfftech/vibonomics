import type { ModelDetail, ModelRow } from '../api/types';
import { discountLabel, money } from './format';

/** One upstream provider endpoint OpenRouter lists for a model. */
export interface RouteEndpoint {
  provider: string | null;
  /** The slug a `provider` request block takes, for example `deepinfra/fp8`. */
  tag: string;
  input: number | null;
  output: number | null;
  discount: number;
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

/** The endpoints a request can be pinned to: the ones with a slug, once each. */
export function routeEndpoints(endpoints: ModelDetail['endpoints']): RouteEndpoint[] {
  const seen = new Set<string>();
  const result: RouteEndpoint[] = [];
  for (const endpoint of endpoints) {
    const tag = text(endpoint.tag);
    if (tag === null || seen.has(tag)) {
      continue;
    }
    seen.add(tag);
    result.push({
      provider: text(endpoint.provider),
      tag,
      input: num(endpoint.input),
      output: num(endpoint.output),
      discount: num(endpoint.discount) ?? 0,
    });
  }
  return result;
}

export const DEFAULT_ROUTE = 'default';
const PROVIDER_PREFIX = 'provider:';

/** The select value that pins one provider. */
export function pinRoute(tag: string): string {
  return PROVIDER_PREFIX + tag;
}

export function pinnedTag(route: string): string | null {
  return route.startsWith(PROVIDER_PREFIX) ? route.slice(PROVIDER_PREFIX.length) : null;
}

export interface RouteQuery {
  variant?: 'floor' | 'nitro';
  provider?: string;
  fallbacks?: boolean;
}

/** The snippet request for a chosen route; null for OpenRouter's own routing, which the detail already carries. */
export function routeQuery(route: string, fallbacks: boolean): RouteQuery | null {
  if (route === 'floor' || route === 'nitro') {
    return { variant: route };
  }
  const tag = pinnedTag(route);
  return tag === null ? null : { provider: tag, fallbacks };
}

function sameMoney(a: number | null, b: number | null | undefined): boolean {
  return a !== null && b !== null && b !== undefined && Math.abs(a - b) < 1e-9;
}

/**
 * Where the selector starts. A discounted row's price belongs to one provider, so its config starts pinned to
 * that provider; otherwise OpenRouter's own routing, which is what the row's listed price describes.
 */
export function initialRoute(row: ModelRow, endpoints: RouteEndpoint[]): string {
  if ((row.discount ?? 0) <= 0) {
    return DEFAULT_ROUTE;
  }
  const source = endpoints.find(
    (e) => e.discount > 0 && sameMoney(e.input, row.price.input) && sameMoney(e.output, row.price.output),
  );
  return source ? pinRoute(source.tag) : DEFAULT_ROUTE;
}

function endpointLabel(endpoint: RouteEndpoint): string {
  const parts = [
    endpoint.provider ?? endpoint.tag,
    endpoint.tag,
    `${money(endpoint.input)} / ${money(endpoint.output)}`,
  ];
  if (endpoint.provider === null) {
    parts.shift();
  }
  if (endpoint.discount > 0) {
    parts.push(discountLabel(endpoint.discount));
  }
  return parts.join(' · ');
}

export interface RouteGroup {
  group: string;
  items: { value: string; label: string }[];
}

/** The selector's options. `:floor` and `:nitro` are variants, and a model id carries only one. */
export function routeGroups(modelId: string, endpoints: RouteEndpoint[]): RouteGroup[] {
  const routing: RouteGroup = {
    group: 'OpenRouter routing',
    items: [{ value: DEFAULT_ROUTE, label: 'Default routing' }],
  };
  if (!modelId.includes(':')) {
    routing.items.push(
      { value: 'floor', label: 'Cheapest provider (:floor)' },
      { value: 'nitro', label: 'Fastest provider (:nitro)' },
    );
  }
  const groups = [routing];
  if (endpoints.length > 0) {
    groups.push({
      group: 'Pin one provider',
      items: endpoints.map((endpoint) => ({ value: pinRoute(endpoint.tag), label: endpointLabel(endpoint) })),
    });
  }
  return groups;
}

/** One sentence on what the chosen route does, shown under the selector. */
export function routeExplanation(route: string, fallbacks: boolean): string {
  if (route === 'floor') {
    return 'Adds :floor to the model id, so OpenRouter tries providers cheapest first.';
  }
  if (route === 'nitro') {
    return 'Adds :nitro to the model id, so OpenRouter tries providers fastest first.';
  }
  if (pinnedTag(route) !== null) {
    return (
      'Sends a provider block asking for this endpoint, in the tabs that have room for one. A provider’s discount ' +
      'holds only while requests reach it' +
      (fallbacks ? ', and with fallbacks on they may not.' : ', so with fallbacks off they fail while it is down.') +
      ' Your OpenRouter account’s own provider settings still apply.'
    );
  }
  return 'OpenRouter spreads requests over the providers that are up, favouring cheap and reliable ones.';
}
