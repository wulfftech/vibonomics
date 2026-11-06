import type { CatalogPreset, PresetSettings } from '../api/types';

/** The prefix every preset Vibonomics writes starts with; it is how the backend knows a preset is its own. */
export const PRESET_PREFIX = 'vibonomics-';

/** The slug a typed name becomes, as the backend makes it: lower case, hyphenated, prefixed. Empty for no name. */
export function presetSlug(name: string): string {
  const base = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  if (base === '') {
    return '';
  }
  return base.startsWith(PRESET_PREFIX) ? base : PRESET_PREFIX + base;
}

/** The part of a slug a person types: the slug without its prefix. */
export function presetName(slug: string): string {
  return slug.toLowerCase().startsWith(PRESET_PREFIX) ? slug.slice(PRESET_PREFIX.length) : slug;
}

/** The slug an item is written under: the typed name made into a slug, unless it was adopted and keeps its name. */
export function itemSlug(item: Pick<PresetSettings, 'slug' | 'adopted'>): string {
  return item.adopted ? item.slug.trim() : presetSlug(item.slug);
}

/** Said wherever a blocklist is edited, so the owner knows what a pattern is and what blocking does not do. */
export const BLOCKLIST_HELP =
  'Patterns, case-insensitive; * matches anything, e.g. openai/*, *:free, deepinfra. Blocked models and providers are never considered by the update runs; this does not change what OpenRouter does at request time.';

/** Said wherever the keys Vibonomics does not manage are shown, so the owner reads the same promise everywhere. */
export const KEPT_AS_IT_IS = 'Vibonomics has no integration for these keys and will send them back unchanged.';

/** Trimmed, without blanks, and without a pattern twice (patterns ignore case, so "X" and "x" are one). */
export function cleanPatterns(patterns: string[]): string[] {
  const seen = new Set<string>();
  const kept: string[] = [];
  for (const pattern of patterns.map((p) => p.trim())) {
    if (pattern !== '' && !seen.has(pattern.toLowerCase())) {
      seen.add(pattern.toLowerCase());
      kept.push(pattern);
    }
  }
  return kept;
}

/**
 * Why a name typed for a new preset cannot be used, or null when it can. `taken` holds the slugs Vibonomics already
 * keeps settings for; `onAccount` holds every slug on the OpenRouter account.
 */
export function newNameProblem(
  name: string,
  taken: ReadonlySet<string>,
  onAccount: ReadonlySet<string>,
): string | null {
  const slug = presetSlug(name);
  if (name.trim() !== '' && slug === '') {
    return 'Use letters or numbers in the name';
  }
  if (taken.has(slug)) {
    return 'Vibonomics already manages a preset with this name';
  }
  if (onAccount.has(slug)) {
    return 'A preset with this name is already on your account. Choose it in the list instead';
  }
  return null;
}

/** What is selected in the preset list when it is the pinned "Global settings" entry; no slug can contain an @. */
export const GLOBAL_KEY = '@global';

/** One row of the preset list: a preset on the account, an item Vibonomics keeps, or both. */
export interface PresetEntry {
  /** The full slug, which names the row and what is selected. */
  slug: string;
  name: string;
  version: number | null;
  archived: boolean;
  /** Shows the Vibonomics mark: Vibonomics created or adopted it, or has settings for it. */
  managed: boolean;
  /** Settings exist for it but OpenRouter has not heard of it yet. */
  notCreated: boolean;
  catalog: CatalogPreset | null;
  /** Its settings; null when Vibonomics keeps none (a hand-made preset, or one it created and stopped managing). */
  item: PresetSettings | null;
}

const byName = (a: CatalogPreset, b: CatalogPreset) =>
  (a.name || a.slug).localeCompare(b.name || b.slug, undefined, { sensitivity: 'base' });

/**
 * The rows of the preset list. Settings come first, in the order they are kept (so a row does not jump while it is
 * edited), then presets Vibonomics made and no longer keeps, then the rest of the account, each by name.
 */
export function presetEntries(catalog: readonly CatalogPreset[], items: readonly PresetSettings[]): PresetEntry[] {
  const bySlug = new Map(catalog.map((p) => [p.slug, p]));
  const kept = new Set<string>();
  const entries: PresetEntry[] = [];
  for (const item of items) {
    const slug = itemSlug(item);
    if (kept.has(slug)) {
      continue;
    }
    kept.add(slug);
    const live = bySlug.get(slug) ?? null;
    entries.push({
      slug,
      name: live?.name || slug,
      version: live?.version ?? null,
      archived: live?.status === 'archived',
      managed: true,
      notCreated: live === null,
      catalog: live,
      item,
    });
  }
  const others = catalog.filter((p) => !kept.has(p.slug));
  const row = (p: CatalogPreset): PresetEntry => ({
    slug: p.slug,
    name: p.name || p.slug,
    version: p.version,
    archived: p.status === 'archived',
    managed: p.managed,
    notCreated: false,
    catalog: p,
    item: null,
  });
  return [
    ...entries,
    ...others
      .filter((p) => p.managed)
      .sort(byName)
      .map(row),
    ...others
      .filter((p) => !p.managed)
      .sort(byName)
      .map(row),
  ];
}

/** Whether a row matches what was typed in the search box, in its name or its slug. */
export function matchesSearch(entry: Pick<PresetEntry, 'name' | 'slug'>, query: string): boolean {
  const needle = query.trim().toLowerCase();
  return needle === '' || entry.name.toLowerCase().includes(needle) || entry.slug.toLowerCase().includes(needle);
}

const OPENROUTER = 'https://openrouter.ai';

function encodeSegment(segment: string): string {
  // "." and "..": the browser would resolve them away, and the link would leave the model pages.
  if (segment === '.' || segment === '..') {
    return segment.replace(/\./g, '%2E');
  }
  // A colon is legal in a path and is how OpenRouter spells a variant (":free"), so it stays readable.
  return encodeURIComponent(segment).replace(/%3A/gi, ':');
}

/** A model's page on OpenRouter. The id's own slash stays, as OpenRouter's addresses are author/slug[:variant]. */
export function openRouterModelUrl(modelId: string): string {
  try {
    const segments = modelId
      .trim()
      .split('/')
      .filter((segment) => segment !== '')
      .map(encodeSegment);
    if (segments.length > 0) {
      return `${OPENROUTER}/${segments.join('/')}`;
    }
  } catch {
    // encodeURIComponent throws on a lone surrogate; a link that opens the model list beats a page that will not render.
  }
  return `${OPENROUTER}/models`;
}

/**
 * A preset's edit page on OpenRouter. OpenRouter files a preset under a workspace and calls the first one `default`,
 * so that is the guess when none is named. Null when there is no id, because there is no page to open yet.
 */
export function openRouterPresetUrl(presetId: string | null | undefined, workspace?: string | null): string | null {
  const id = presetId?.trim();
  if (!id) {
    return null;
  }
  try {
    return `${OPENROUTER}/workspaces/${encodeSegment(workspace?.trim() || 'default')}/presets/${encodeSegment(id)}/edit`;
  } catch {
    // A lone surrogate cannot be encoded; no link is better than one that goes somewhere else.
    return null;
  }
}
