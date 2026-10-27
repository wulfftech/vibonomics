/**
 * Client-side price maths. Blended prices and value depend on the usage profile picked in the UI,
 * so the server returns raw per-1M prices and the composite score, and everything else is here.
 *
 * - Cache-aware input price: `inEff = input·(1−c) + (cache_read ?? input)·c`
 * - Blended $/1M: `(iw·inEff + ow·output) / (iw + ow)`
 * - Estimated cost per task: `(tin·inEff + tout·output) / 1e6`
 * - Value: `composite / taskCost` (∞ for a free model with a score)
 * - Value index: log-scaled 0–100 across the dataset's finite values; ∞ → 100
 */

export interface Prices {
  input?: number | null;
  output?: number | null;
  cache_read?: number | null;
  cache_write?: number | null;
}

export interface Profile {
  input_weight: number;
  output_weight: number;
  cache_hit_rate: number;
  task_input_tokens: number;
  task_output_tokens: number;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function known(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value);
}

/** The input price per 1M tokens once the profile's cache hit rate is applied. */
export function effectiveInputPrice(price: Prices, cacheHitRate: number): number | null {
  if (!known(price.input)) {
    return null;
  }
  const c = clamp(cacheHitRate, 0, 1);
  const cacheRead = known(price.cache_read) ? price.cache_read : price.input;
  return price.input * (1 - c) + cacheRead * c;
}

export function blendedPrice(price: Prices, profile: Profile): number | null {
  const inEff = effectiveInputPrice(price, profile.cache_hit_rate);
  if (inEff === null || !known(price.output)) {
    return null;
  }
  const iw = Math.max(0, profile.input_weight);
  const ow = Math.max(0, profile.output_weight);
  if (iw + ow <= 0) {
    return null;
  }
  return (iw * inEff + ow * price.output) / (iw + ow);
}

/** Estimated USD for one task of the profile's size; 0 for free models, null when prices are unknown. */
export function taskCost(price: Prices, profile: Profile, isFree = false): number | null {
  if (isFree) {
    return 0;
  }
  const inEff = effectiveInputPrice(price, profile.cache_hit_rate);
  if (inEff === null || !known(price.output)) {
    return null;
  }
  return (profile.task_input_tokens * inEff + profile.task_output_tokens * price.output) / 1e6;
}

/** Score points per dollar per task. Infinity for a free model with a score; null without a score or price. */
export function valueOf(composite: number | null | undefined, cost: number | null): number | null {
  if (!known(composite) || cost === null) {
    return null;
  }
  if (cost <= 0) {
    return Infinity;
  }
  return composite / cost;
}

/**
 * Builds the 0–100 value index for a dataset: log-scaled between the smallest (→ 0) and largest
 * (→ 100) positive finite value. Infinity (free with a score) maps to 100.
 */
export function makeValueIndex(values: Iterable<number | null>): (value: number | null) => number | null {
  let min = Infinity;
  let max = -Infinity;
  for (const value of values) {
    if (value !== null && Number.isFinite(value) && value > 0) {
      min = Math.min(min, value);
      max = Math.max(max, value);
    }
  }
  const lmin = Math.log(min);
  const lmax = Math.log(max);
  return (value) => {
    if (value === null) {
      return null;
    }
    if (value === Infinity) {
      return 100;
    }
    if (!(value > 0)) {
      return 0;
    }
    if (!Number.isFinite(lmin) || !Number.isFinite(lmax) || lmax === lmin) {
      return 100;
    }
    return clamp(((Math.log(value) - lmin) / (lmax - lmin)) * 100, 0, 100);
  };
}

export const PRICE_BANDS = ['Free', '< $0.50', '$0.50–2', '$2–10', '$10+', 'Unknown'] as const;
export type PriceBand = (typeof PRICE_BANDS)[number];

export function priceBand(blended: number | null, isFree: boolean): PriceBand {
  if (isFree || blended === 0) {
    return 'Free';
  }
  if (blended === null) {
    return 'Unknown';
  }
  if (blended < 0.5) {
    return '< $0.50';
  }
  if (blended < 2) {
    return '$0.50–2';
  }
  if (blended < 10) {
    return '$2–10';
  }
  return '$10+';
}

export const VALUE_TIERS = ['Top 10%', 'Top 25%', 'Top 50%', 'Rest', 'Unscored'] as const;
export type ValueTier = (typeof VALUE_TIERS)[number];

/** Ranks the values (highest first) and returns each one's tier, in input order. */
export function valueTiers(values: (number | null)[]): ValueTier[] {
  const ranked = values
    .map((value, index) => ({ value, index }))
    .filter((entry): entry is { value: number; index: number } => entry.value !== null)
    .sort((a, b) => b.value - a.value || a.index - b.index);
  const tiers: ValueTier[] = values.map(() => 'Unscored');
  const n = ranked.length;
  ranked.forEach((entry, rank) => {
    const position = (rank + 1) / n;
    tiers[entry.index] =
      position <= 0.1 ? 'Top 10%' : position <= 0.25 ? 'Top 25%' : position <= 0.5 ? 'Top 50%' : 'Rest';
  });
  return tiers;
}

/** "(20 × input + 1 × output) / 21, input with an 80% cache hit rate" for tooltips. */
export function profileFormula(profile: Profile): string {
  const iw = profile.input_weight;
  const ow = profile.output_weight;
  const cache = profile.cache_hit_rate > 0 ? `; input at ${Math.round(profile.cache_hit_rate * 100)}% cache hits` : '';
  return `(${iw} × input + ${ow} × output) ÷ ${iw + ow}${cache}`;
}

/**
 * Shading for the blended price: cheapest is preferred. Under $1 uses accessible green shades
 * (varying by price), $1–5 an amber tone, and anything above $5 red. Free is the best case.
 */
export function blendedColor(blended: number | null, isFree = false): string | undefined {
  if (isFree || blended === 0) {
    return 'light-dark(var(--mantine-color-green-9), var(--mantine-color-green-3))';
  }
  if (blended === null || !Number.isFinite(blended)) {
    return undefined;
  }
  if (blended < 1) {
    if (blended < 0.1) {
      return 'light-dark(var(--mantine-color-green-9), var(--mantine-color-green-2))';
    }
    if (blended < 0.5) {
      return 'light-dark(var(--mantine-color-green-9), var(--mantine-color-green-3))';
    }
    return 'light-dark(var(--mantine-color-green-8), var(--mantine-color-green-4))';
  }
  if (blended < 5) {
    // Yellow text on white can't reach AA, so the light theme uses the darker amber shade.
    return 'light-dark(var(--mantine-color-orange-9), var(--mantine-color-yellow-4))';
  }
  return 'light-dark(var(--mantine-color-red-9), var(--mantine-color-red-4))';
}
