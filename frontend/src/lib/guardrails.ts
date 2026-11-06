import {
  GUARDRAIL_KEYS,
  normalizeGuardrails,
  type CatalogPreset,
  type GuardrailKey,
  type Guardrails,
  type PresetGuardrails,
  type PresetPlan,
} from '../api/types';

/**
 * Where a guardrail stands on a preset, the three states the owner chooses between: left as it is on OpenRouter (not
 * managed), set to a value, or removed from the preset.
 */
export type GuardrailMode = 'leave' | 'set' | 'remove';

export type NumberKey =
  'temperature' | 'max_tokens' | 'repetition_penalty' | 'frequency_penalty' | 'presence_penalty' | 'top_p';

export type Quantization = NonNullable<Guardrails['quantizations']>[number];

/**
 * The guardrails as the editor holds them. A number is `''` while it is Set but holds no number yet (an empty or
 * half-typed field), so that state exists in the draft, shows as an unsaved change, and blocks Save. It is never
 * sent: `guardrailProblems` names it, and Save and Preview are off while it does.
 */
export type GuardrailsDraft = { [K in keyof Guardrails]: K extends NumberKey ? Guardrails[K] | '' : Guardrails[K] };

type Slot<K extends GuardrailKey> = NonNullable<GuardrailsDraft[K]>;

/** OpenRouter's list of the precisions an endpoint serves at; an endpoint that does not say is `unknown`. */
export const QUANTIZATIONS: readonly Quantization[] = [
  'int4',
  'int8',
  'fp4',
  'mxfp4',
  'nvfp4',
  'fp6',
  'fp8',
  'mxfp8',
  'fp16',
  'bf16',
  'fp32',
  'unknown',
];

export const MAX_TOKENS_LIMIT = 2_000_000;
export const IGNORE_LIMIT = 50;
export const PROMPT_LIMIT = 20_000;
const SLUG_LIMIT = 100;

/** The ranges the backend accepts (the lower bound of a penalty or top_p is just above 0 there). */
export const NUMBER_LIMITS: Record<NumberKey, { min: number; max: number; step: number; whole?: boolean }> = {
  temperature: { min: 0, max: 2, step: 0.05 },
  max_tokens: { min: 1, max: MAX_TOKENS_LIMIT, step: 1000, whole: true },
  repetition_penalty: { min: 0.01, max: 2, step: 0.01 },
  frequency_penalty: { min: -2, max: 2, step: 0.05 },
  presence_penalty: { min: -2, max: 2, step: 0.05 },
  top_p: { min: 0.01, max: 1, step: 0.01 },
};

/** What a value input that is not Set shows in place of a value. */
export function idlePlaceholder(mode: GuardrailMode): string {
  return mode === 'leave' ? 'Left as it is' : 'Will be removed';
}

// What a key holds the moment it is switched to Set with nothing to start from.
const EMPTY: { [K in GuardrailKey]: Slot<K> } = {
  temperature: '',
  max_tokens: '',
  repetition_penalty: '',
  frequency_penalty: '',
  presence_penalty: '',
  top_p: '',
  quantizations: [],
  ignore: [],
  require_parameters: true,
  system_prompt: '',
};

/**
 * The draft form of guardrails that came from a saved item (which may lack some keys) or from an earlier draft. The
 * one cast is sound because a draft only differs from the saved type by holding `''` in a number it keeps as it is.
 */
export function draftOf(guardrails: PresetGuardrails | GuardrailsDraft | null | undefined): GuardrailsDraft {
  return normalizeGuardrails(guardrails as PresetGuardrails | null | undefined);
}

/** The saved type of a draft, for putting it back in the settings. Only call it once `guardrailProblems` is empty. */
export function asGuardrails(draft: GuardrailsDraft): PresetGuardrails {
  return draft as PresetGuardrails;
}

/** A key is Remove when it is in `clear`, Set when it holds a value, and Leave when it is neither. */
export function guardrailMode(
  guardrails: PresetGuardrails | GuardrailsDraft | null | undefined,
  key: GuardrailKey,
): GuardrailMode {
  const g = draftOf(guardrails);
  if (g[key] !== null) {
    return 'set';
  }
  return g.clear.includes(key) ? 'remove' : 'leave';
}

/**
 * Moves one key to a mode. Set keeps a value the key already holds, and otherwise starts from `seed` (the live value,
 * say) or from an empty one, which `guardrailProblems` then asks to be filled in. Leave and Remove drop the value, and
 * only Remove names the key in `clear`, so a key is never both set and removed.
 */
export function setGuardrailMode<K extends GuardrailKey>(
  guardrails: PresetGuardrails | GuardrailsDraft | null | undefined,
  key: K,
  mode: GuardrailMode,
  seed?: Slot<K>,
): GuardrailsDraft {
  const g = draftOf(guardrails);
  const others = g.clear.filter((k) => k !== key);
  if (mode === 'remove') {
    return { ...g, [key]: null, clear: GUARDRAIL_KEYS.filter((k) => k === key || others.includes(k)) };
  }
  if (mode === 'leave') {
    return { ...g, [key]: null, clear: others };
  }
  return { ...g, [key]: g[key] ?? seed ?? EMPTY[key], clear: others };
}

/** Gives a key a value, which makes it Set: it leaves `clear`. A null value is Leave. */
export function setGuardrailValue<K extends GuardrailKey>(
  guardrails: PresetGuardrails | GuardrailsDraft | null | undefined,
  key: K,
  value: GuardrailsDraft[K],
): GuardrailsDraft {
  const g = draftOf(guardrails);
  return { ...g, [key]: value, clear: g.clear.filter((k) => k !== key) };
}

const PROVIDER_SLUG = /^[A-Za-z0-9][A-Za-z0-9._-]*(\/[A-Za-z0-9._-]+)?$/;

function numberProblem(key: NumberKey, value: number | ''): string | null {
  const { min, max, whole } = NUMBER_LIMITS[key];
  const fine =
    typeof value === 'number' &&
    Number.isFinite(value) &&
    value >= min &&
    value <= max &&
    (!whole || Number.isInteger(value));
  return fine ? null : `Enter ${whole ? 'a whole number' : 'a number'} from ${min} to ${max.toLocaleString('en')}`;
}

function listProblem(key: 'quantizations' | 'ignore', value: readonly string[]): string | null {
  if (value.length === 0) {
    return key === 'ignore'
      ? 'Add at least one provider, or choose Remove'
      : 'Choose at least one precision, or choose Remove';
  }
  if (key === 'ignore') {
    if (value.length > IGNORE_LIMIT) {
      return `At most ${IGNORE_LIMIT} providers`;
    }
    const bad = value.find((slug) => slug.length > SLUG_LIMIT || !PROVIDER_SLUG.test(slug));
    if (bad !== undefined) {
      return `"${bad}" is not a provider slug: letters, digits, dots, hyphens and one optional /`;
    }
  }
  return null;
}

function promptProblem(value: unknown): string | null {
  if (typeof value !== 'string' || value.trim() === '') {
    return 'Write the prompt, or choose Remove';
  }
  return value.length > PROMPT_LIMIT ? `At most ${PROMPT_LIMIT.toLocaleString('en')} characters` : null;
}

/** What is wrong with the value a Set key holds, or null when it can be sent. */
function valueProblem(key: GuardrailKey, value: unknown): string | null {
  if (key === 'quantizations' || key === 'ignore') {
    return Array.isArray(value) ? listProblem(key, value as string[]) : 'Choose at least one';
  }
  if (key === 'require_parameters') {
    return typeof value === 'boolean' ? null : 'Choose on or off';
  }
  if (key === 'system_prompt') {
    return promptProblem(value);
  }
  return numberProblem(key, value as number | '');
}

/**
 * What stops these guardrails being saved or previewed, by key. Only a key that is Set can be wrong: Leave and Remove
 * hold no value. Empty, until the owner fixes every Set row.
 */
export function guardrailProblems(
  guardrails: PresetGuardrails | GuardrailsDraft | null | undefined,
): Partial<Record<GuardrailKey, string>> {
  const g = draftOf(guardrails);
  const problems: Partial<Record<GuardrailKey, string>> = {};
  for (const key of GUARDRAIL_KEYS) {
    if (g[key] === null) {
      continue;
    }
    const problem = g.clear.includes(key) ? 'This is both set and removed: choose one' : valueProblem(key, g[key]);
    if (problem !== null) {
      problems[key] = problem;
    }
  }
  return problems;
}

export function hasGuardrailProblems(guardrails: PresetGuardrails | GuardrailsDraft | null | undefined): boolean {
  return Object.keys(guardrailProblems(guardrails)).length > 0;
}

/** What a guardrail is on the live preset, or undefined when it has no value there. */
export function liveGuardrail(
  config: Record<string, unknown> | null,
  key: Exclude<GuardrailKey, 'system_prompt'>,
): unknown {
  if (config === null) {
    return undefined;
  }
  // These three live inside `provider`, the rest at the top.
  if (key === 'quantizations' || key === 'ignore' || key === 'require_parameters') {
    const provider = config.provider;
    return typeof provider === 'object' && provider !== null
      ? ((provider as Record<string, unknown>)[key] ?? undefined)
      : undefined;
  }
  return config[key] ?? undefined;
}

/** A live value as one short phrase for the "On OpenRouter now" line. */
export function describeLive(value: unknown): string {
  if (value === undefined || value === null) {
    return 'not set';
  }
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.length === 0 ? 'an empty list' : value.map(describeLive).join(', ');
  }
  return JSON.stringify(value) ?? 'not set';
}

/** The live value as the right type to start a Set row from, or undefined when it cannot be one. */
export function seedFromLive(
  key: Exclude<GuardrailKey, 'system_prompt'>,
  live: unknown,
): Slot<GuardrailKey> | undefined {
  if (key === 'require_parameters') {
    return typeof live === 'boolean' ? live : undefined;
  }
  if (key === 'quantizations' || key === 'ignore') {
    const names = Array.isArray(live) ? live.filter((entry): entry is string => typeof entry === 'string') : [];
    const kept =
      key === 'quantizations' ? names.filter((n) => (QUANTIZATIONS as readonly string[]).includes(n)) : names;
    return kept.length === 0 ? undefined : kept;
  }
  return typeof live === 'number' && Number.isFinite(live) ? live : undefined;
}

/** The live preset as the editor can know it. */
export interface LivePreset {
  /** The live config; null when the preset is not on OpenRouter yet. */
  config: Record<string, unknown> | null;
  /** The live system prompt: its text, null for none, or undefined when it has one that has not been read yet. */
  systemPrompt: string | null | undefined;
}

/**
 * What is live on OpenRouter, from the newer of the last sync and the last plan: both are live reads, so the one
 * with the higher version is the one that has seen the latest write.
 */
export function livePreset(catalog: CatalogPreset | null, plan: PresetPlan | undefined): LivePreset {
  const planNewer = plan !== undefined && (catalog === null || (plan.current_version ?? 0) >= (catalog.version ?? 0));
  if (plan !== undefined && planNewer) {
    return { config: plan.current_config, systemPrompt: plan.system_prompt.live };
  }
  if (catalog !== null) {
    return { config: catalog.config, systemPrompt: catalog.has_system_prompt ? undefined : null };
  }
  return { config: null, systemPrompt: null };
}
