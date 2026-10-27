import type { ModelRow, UsageProfile } from '../api/types';
import {
  blendedPrice,
  effectiveInputPrice,
  makeValueIndex,
  priceBand,
  taskCost,
  valueOf,
  valueTiers,
  type PriceBand,
  type Prices,
  type ValueTier,
} from './pricing';

/** Derived per-row figures for the Models table, computed once per data set and usage profile. */

export type CostSource = 'measured' | 'reported' | 'estimated';

export interface EnrichedModel {
  row: ModelRow;
  inputEff: number | null;
  blended: number | null;
  /** The undiscounted blended price, when a discount applies. */
  listBlended: number | null;
  estimatedTaskCost: number | null;
  /** The figure shown in $/task: measured by Vibonomics, then reported by benchmarks, else estimated. */
  taskCost: number | null;
  taskCostSource: CostSource | null;
  value: number | null;
  /** The cost basis used for value; for free models this is the configured assumed price. */
  valueCost: number | null;
  assumedFreeCost: number | null;
  valueIndex: number | null;
  priceBand: PriceBand;
  valueTier: ValueTier;
  haystack: string;
  /** This row stands in for an OpenCode Go offer of the same model that was folded away. */
  alsoOnGo: boolean;
}

export function listPrices(row: ModelRow): Prices | null {
  if (!row.list_price || !row.discount) {
    return null;
  }
  return {
    input: row.list_price.input,
    output: row.list_price.output,
    // Discounts apply to prompt/completion; the cache read price is unchanged.
    cache_read: row.price.cache_read,
  };
}

export interface EnrichOptions {
  /** Blended USD per 1M used to rank free models; 0 keeps their value infinite. */
  freeModelCost?: number;
}

export function enrichModels(rows: ModelRow[], profile: UsageProfile, options: EnrichOptions = {}): EnrichedModel[] {
  const freeCost = Math.max(0, options.freeModelCost ?? 0.01);
  const partial = rows.map((row) => {
    const blended = row.is_free ? 0 : blendedPrice(row.price, profile);
    const list = listPrices(row);
    const estimated = taskCost(row.price, profile, row.is_free);
    const valuePrice = row.is_free ? { input: freeCost, output: freeCost, cache_read: freeCost } : row.price;
    const valueCost = taskCost(valuePrice, profile, false);
    const measured = row.test?.cost_per_task ?? null;
    const reported = row.reported_cost_per_task;
    const [shown, source]: [number | null, CostSource | null] =
      measured !== null
        ? [measured, 'measured']
        : reported !== null
          ? [reported, 'reported']
          : estimated !== null
            ? [estimated, 'estimated']
            : [null, null];
    return {
      row,
      inputEff: effectiveInputPrice(row.price, profile.cache_hit_rate),
      blended,
      listBlended: list ? blendedPrice(list, profile) : null,
      estimatedTaskCost: estimated,
      taskCost: shown,
      taskCostSource: source,
      // Value uses the profile estimate so every model is costed on the same task size.
      value: valueOf(row.scores.composite, valueCost),
      valueCost,
      assumedFreeCost: row.is_free ? freeCost : null,
      haystack: [row.name, row.model_id, row.provider_name, row.creator ?? '', row.canonical_id ?? '']
        .join('\u0000')
        .toLowerCase(),
    };
  });
  const index = makeValueIndex(partial.map((entry) => entry.value));
  const tiers = valueTiers(partial.map((entry) => entry.value));
  return partial.map((entry, i) => ({
    ...entry,
    valueIndex: index(entry.value),
    priceBand: priceBand(entry.blended, entry.row.is_free),
    valueTier: tiers[i],
    alsoOnGo: false,
  }));
}

/**
 * OpenCode lists some models twice: once for Console (pay-as-you-go) and once for Go (the monthly
 * subscription). Both rows carry the same per-token rate, so showing both doubles the model and
 * invites a comparison that means nothing. Fold the Go offer into the Console row and mark it.
 *
 * The survivor is the row that can actually be tested; on a tie, or when only one side has a price,
 * the Console row wins. Filtering to just one of the two providers leaves the rows alone, because a
 * fold would otherwise hide the only offer the operator asked to see.
 */
export function foldOpenCodeOffers(models: EnrichedModel[]): EnrichedModel[] {
  const consoleByModel = new Map<string, EnrichedModel>();
  for (const model of models) {
    if (model.row.provider_type === 'opencode') {
      consoleByModel.set(model.row.model_id, model);
    }
  }
  if (consoleByModel.size === 0) {
    return models;
  }
  const goByModel = new Map<string, EnrichedModel>();
  for (const model of models) {
    if (model.row.provider_type === 'opencode-go') {
      goByModel.set(model.row.model_id, model);
    }
  }
  if (goByModel.size === 0) {
    return models;
  }

  const hidden = new Set<number>();
  const foldedInto = new Map<number, true>();
  for (const [modelId, consoleRow] of consoleByModel) {
    const goRow = goByModel.get(modelId);
    if (goRow === undefined) {
      continue;
    }
    if (preferredOffer(consoleRow, goRow) === 'console') {
      hidden.add(goRow.row.id);
      foldedInto.set(consoleRow.row.id, true);
    } else {
      hidden.add(consoleRow.row.id);
    }
  }
  if (hidden.size === 0) {
    return models;
  }
  return models
    .filter((model) => !hidden.has(model.row.id))
    .map((model) => (foldedInto.has(model.row.id) ? { ...model, alsoOnGo: true } : model));
}

/** Which of the two offers of one model the table keeps. */
function preferredOffer(consoleRow: EnrichedModel, goRow: EnrichedModel): 'console' | 'go' {
  if (consoleRow.row.testable !== goRow.row.testable) {
    return consoleRow.row.testable ? 'console' : 'go';
  }
  if ((consoleRow.blended !== null) !== (goRow.blended !== null)) {
    return consoleRow.blended !== null ? 'console' : 'go';
  }
  return 'console';
}

// -- filtering -------------------------------------------------------------------------------

export const QUICK_FILTERS = [
  { value: 'free', label: 'Free' },
  { value: 'best', label: 'Best value' },
  { value: 'new', label: 'New' },
  { value: 'discounted', label: 'Discounted' },
  { value: 'scored', label: 'Scored' },
  { value: 'tools', label: 'Tool calling' },
  { value: 'testable', label: 'Testable' },
] as const;

export type QuickFilter = (typeof QUICK_FILTERS)[number]['value'];

export const GROUP_OPTIONS = [
  { value: 'none', label: 'None' },
  { value: 'provider', label: 'Provider' },
  { value: 'creator', label: 'Creator' },
  { value: 'model', label: 'Model' },
  { value: 'priceBand', label: 'Price band' },
  { value: 'valueTier', label: 'Value tier' },
] as const;

export type GroupBy = (typeof GROUP_OPTIONS)[number]['value'];

export const SORT_KEYS = [
  'name',
  'provider',
  'blended',
  'taskCost',
  'intelligence',
  'coding',
  'agentic',
  'score',
  'value',
  'context',
  'release',
  'firstSeen',
] as const;

export type SortKey = (typeof SORT_KEYS)[number];
export type SortDir = 'asc' | 'desc';

export interface SortState {
  key: SortKey;
  dir: SortDir;
}

/** The direction a column sorts in on its first click: cheapest first, best first, A–Z. */
export function defaultDir(key: SortKey): SortDir {
  return key === 'name' || key === 'provider' || key === 'blended' || key === 'taskCost' ? 'asc' : 'desc';
}

export interface ModelFilters {
  search: string;
  chips: QuickFilter[];
  providers: string[];
  groupBy: GroupBy;
  profileId: string | null;
  minContext: number;
  sort: SortState;
}

export const DEFAULT_FILTERS: ModelFilters = {
  search: '',
  chips: [],
  providers: [],
  groupBy: 'none',
  profileId: null,
  minContext: 0,
  sort: { key: 'score', dir: 'desc' },
};

function matchesChip(model: EnrichedModel, chip: QuickFilter): boolean {
  const { row } = model;
  switch (chip) {
    case 'free':
      return row.is_free;
    case 'best':
    case 'scored':
      return row.scores.composite !== null && row.scores.composite !== undefined;
    case 'new':
      return row.is_new;
    case 'discounted':
      return (row.discount ?? 0) > 0 || row.price_drop || row.new_discount;
    case 'tools':
      return row.tool_call === true;
    case 'testable':
      return row.testable;
  }
}

export function filterModels(models: EnrichedModel[], filters: ModelFilters): EnrichedModel[] {
  const needle = filters.search.trim().toLowerCase();
  const providers = new Set(filters.providers);
  return models.filter(
    (model) =>
      (needle === '' || model.haystack.includes(needle)) &&
      (providers.size === 0 || providers.has(String(model.row.provider_id))) &&
      (filters.minContext <= 0 || (model.row.context_length ?? 0) >= filters.minContext) &&
      filters.chips.every((chip) => matchesChip(model, chip)),
  );
}

// -- sorting ---------------------------------------------------------------------------------

/** The sort the table actually applies: "Best value" forces value, best first. */
export function effectiveSort(filters: ModelFilters): SortState {
  return filters.chips.includes('best') ? { key: 'value', dir: 'desc' } : filters.sort;
}

export function sortValue(model: EnrichedModel, key: SortKey): number | string | null {
  const { row } = model;
  switch (key) {
    case 'name':
      return row.name.toLowerCase();
    case 'provider':
      return row.provider_name.toLowerCase();
    case 'blended':
      return model.blended;
    case 'taskCost':
      return model.taskCost;
    case 'intelligence':
      return row.scores.intelligence ?? null;
    case 'coding':
      return row.scores.coding ?? null;
    case 'agentic':
      return row.scores.agentic ?? null;
    case 'score':
      return row.scores.composite ?? null;
    case 'value':
      return model.value;
    case 'context':
      return row.context_length;
    case 'release':
      return row.release_date;
    case 'firstSeen':
      return row.first_seen;
  }
}

/** Sorts a copy; empty values always go last, ties fall back to the name. */
export function sortModels(models: EnrichedModel[], sort: SortState): EnrichedModel[] {
  const factor = sort.dir === 'asc' ? 1 : -1;
  const keyed = models.map((model) => ({ model, key: sortValue(model, sort.key) }));
  keyed.sort((a, b) => {
    if (a.key === null || b.key === null) {
      if (a.key === b.key) {
        return a.model.row.name.localeCompare(b.model.row.name);
      }
      return a.key === null ? 1 : -1;
    }
    let cmp: number;
    if (typeof a.key === 'string' && typeof b.key === 'string') {
      cmp = a.key.localeCompare(b.key);
    } else {
      const x = a.key as number;
      const y = b.key as number;
      cmp = x === y ? 0 : x < y ? -1 : 1;
    }
    return cmp * factor || a.model.row.name.localeCompare(b.model.row.name) || a.model.row.id - b.model.row.id;
  });
  return keyed.map((entry) => entry.model);
}

// -- grouping --------------------------------------------------------------------------------

export interface GroupSummary {
  key: string;
  label: string;
  sublabel: string | null;
  count: number;
  cheapestBlended: number | null;
  bestScore: number | null;
  bestValueIndex: number | null;
  /** Provider type for the colour chip when grouping by provider. */
  colorType: string | null;
  collapsed: boolean;
}

export type DisplayRow =
  { kind: 'group'; key: string; group: GroupSummary } | { kind: 'model'; key: string; model: EnrichedModel };

export function groupKey(
  model: EnrichedModel,
  groupBy: GroupBy,
): { key: string; label: string; sublabel: string | null } {
  const { row } = model;
  switch (groupBy) {
    case 'provider':
      return { key: `p:${row.provider_id}`, label: row.provider_name, sublabel: null };
    case 'creator':
      return { key: `c:${row.creator ?? ''}`, label: row.creator ?? 'Unknown creator', sublabel: null };
    case 'model':
      return row.canonical_id
        ? { key: `m:${row.canonical_id}`, label: row.name, sublabel: row.canonical_id }
        : { key: `m:@${row.provider_id}/${row.model_id}`, label: row.name, sublabel: 'Unmatched' };
    case 'priceBand':
      return { key: `b:${model.priceBand}`, label: model.priceBand, sublabel: null };
    case 'valueTier':
      return { key: `v:${model.valueTier}`, label: model.valueTier, sublabel: null };
    case 'none':
      return { key: '', label: '', sublabel: null };
  }
}

function minOf(a: number | null, b: number | null): number | null {
  return a === null ? b : b === null ? a : Math.min(a, b);
}

function maxOf(a: number | null, b: number | null): number | null {
  return a === null ? b : b === null ? a : Math.max(a, b);
}

/**
 * Flattens sorted rows into the table's display list. Groups appear in the order of their best row
 * under the active sort, so the group holding the top model comes first.
 */
export function buildDisplayRows(
  sorted: EnrichedModel[],
  groupBy: GroupBy,
  collapsed: ReadonlySet<string>,
): DisplayRow[] {
  if (groupBy === 'none') {
    return sorted.map((model) => ({ kind: 'model', key: `r${model.row.id}`, model }));
  }
  const groups = new Map<string, { summary: GroupSummary; members: EnrichedModel[] }>();
  for (const model of sorted) {
    const { key, label, sublabel } = groupKey(model, groupBy);
    let group = groups.get(key);
    if (group === undefined) {
      group = {
        summary: {
          key,
          label,
          sublabel,
          count: 0,
          cheapestBlended: null,
          bestScore: null,
          bestValueIndex: null,
          colorType: groupBy === 'provider' ? model.row.provider_type : null,
          collapsed: collapsed.has(key),
        },
        members: [],
      };
      groups.set(key, group);
    }
    const s = group.summary;
    s.count += 1;
    s.cheapestBlended = minOf(s.cheapestBlended, model.blended);
    s.bestScore = maxOf(s.bestScore, model.row.scores.composite ?? null);
    s.bestValueIndex = maxOf(s.bestValueIndex, model.valueIndex);
    group.members.push(model);
  }
  const out: DisplayRow[] = [];
  for (const { summary, members } of groups.values()) {
    out.push({ kind: 'group', key: `g${summary.key}`, group: summary });
    if (!summary.collapsed) {
      for (const model of members) {
        out.push({ kind: 'model', key: `r${model.row.id}`, model });
      }
    }
  }
  return out;
}
