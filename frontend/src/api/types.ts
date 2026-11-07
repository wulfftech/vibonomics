import type { components } from './schema';

/** Short names for the generated schema types the UI works with. */
type Schemas = components['schemas'];

export type ModelRow = Schemas['ModelRow'];
export type ModelsResponse = Schemas['ModelsResponse'];
export type ModelDetail = Schemas['ModelDetail'];
export type ModelMetric = Schemas['vibonomics__api__models__MetricOut'];
export type PriceOut = Schemas['PriceOut'];
export type UsageProfile = Schemas['UsageProfile'];
export type SnippetOut = Schemas['SnippetOut'];
export type OtherOffer = Schemas['OtherOfferOut'];

export type ProviderOut = Schemas['ProviderOut'];
export type ProviderTypeOut = Schemas['ProviderTypeOut'];
export type ProviderIn = Schemas['ProviderIn'];
export type ProviderUpdate = Schemas['ProviderUpdate'];
export type BalanceOut = Schemas['BalanceOut'];
export type ProviderTestOverride = Schemas['ProviderTestOverride'];
export type ProviderTestResult = Schemas['TestResult'];

export type SettingsOut = Schemas['SettingsOut'];
export type SettingsIn = Schemas['SettingsIn'];
export type AppSettings = Schemas['AppSettings'];
export type BenchmarkSourceOut = Schemas['BenchmarkSourceOut'];
export type SourceMetric = Schemas['vibonomics__api__settings__MetricOut'];

export type SpendOut = Schemas['SpendOut'];
export type SpendBucket = Schemas['SpendBucketOut'];
export type SpendDay = Schemas['SpendDayOut'];
export type SpendNamed = Schemas['SpendNamedOut'];
export type SpendKey = Schemas['SpendKeyOut'];
export type SpendAccount = Schemas['SpendAccountOut'];

export type PresetSettings = Schemas['PresetSettings'];
export type PresetsSettings = Schemas['PresetsSettings'];
export type PresetList = Schemas['PresetListOut'];
export type PresetSummary = Schemas['PresetSummaryOut'];
export type PresetCatalog = Schemas['PresetCatalogOut'];
export type CatalogPreset = Schemas['CatalogPresetOut'];
export type PresetInspect = Schemas['PresetInspectOut'];
export type PresetPlan = Schemas['PresetPlanOut'];
export type PresetState = Schemas['PresetStateOut'];
export type PresetSlugState = Schemas['PresetSlugStateOut'];
export type PresetApplyResult = Schemas['PresetApplyOut'];
export type PresetVersion = Schemas['PresetVersionOut'];
export type PresetProvider = Schemas['PresetProviderOut'];
export type PresetCandidate = Schemas['PresetCandidateOut'];
export type PresetGuardrails = Schemas['PresetGuardrails'];
export type PresetCapability = Schemas['PresetCapabilityOut'];

export type ClaudeSubscriptionOut = Schemas['ClaudeSubscriptionOut'];
export type SubscriptionSnapshot = Schemas['SnapshotOut'];
export type SubscriptionWindow = Schemas['WindowOut'];
export type ExtraUsage = Schemas['ExtraUsageOut'];
export type SpendLimit = Schemas['SpendLimitOut'];
export type SessionStats = Schemas['SessionStatsOut'];

export type RefreshStatus = Schemas['RefreshStatus'];
export type RefreshKind = NonNullable<Schemas['RefreshIn']['kinds']>[number];
export type SystemStatus = Schemas['SystemStatus'];
export type AuthStatus = Schemas['AuthStatus'];

export type ScoreRow = Schemas['ScoreRowOut'];
export type CanonicalOut = Schemas['CanonicalOut'];

export type SuiteOut = Schemas['SuiteOut'];
export type JudgeOut = Schemas['JudgeOut'];
export type EvalRunOut = Schemas['EvalRunOut'];
export type EvalRunDetail = Schemas['EvalRunDetail'];
export type EvalResultOut = Schemas['EvalResultOut'];
export type EvalRunIn = Schemas['EvalRunIn'];
export type ModelRef = Schemas['ModelRef'];

/**
 * `AppSettings` with every section present. The server always sends all of them, but the schema
 * marks `default_factory` fields optional; `fullSettings` fills any gap with the server defaults.
 */
export interface FullSettings {
  general: Schemas['GeneralSettings'];
  refresh: Schemas['RefreshSettings'];
  highlight: Schemas['HighlightSettings'];
  scoring: Schemas['ScoringSettings'] & { profiles: UsageProfile[] };
  benchmarks: Schemas['BenchmarkSettings'] & { sources: NonNullable<Schemas['BenchmarkSettings']['sources']> };
  evals: Schemas['EvalSettings'];
  presets: PresetsSettings & { items: PresetSettings[]; blocked_models: string[]; blocked_providers: string[] };
}

export type GuardrailKey = NonNullable<PresetGuardrails['clear']>[number];
/** The guardrails as the server keeps them, every key present. */
export type Guardrails = Required<PresetGuardrails>;

/** In the order the server lists them, which is also the order of the form. */
export const GUARDRAIL_KEYS: readonly GuardrailKey[] = [
  'temperature',
  'max_tokens',
  'repetition_penalty',
  'frequency_penalty',
  'presence_penalty',
  'top_p',
  'quantizations',
  'ignore',
  'require_parameters',
  'system_prompt',
];

/** Every guardrail left alone: nothing is managed, so whatever is live on OpenRouter stays. */
export const NO_GUARDRAILS: Guardrails = {
  temperature: null,
  max_tokens: null,
  repetition_penalty: null,
  frequency_penalty: null,
  presence_penalty: null,
  top_p: null,
  quantizations: null,
  ignore: null,
  require_parameters: null,
  system_prompt: null,
  clear: [],
};

/**
 * Guardrails with every key present and `clear` in the server's order, so a draft built from them compares equal to
 * what is saved (the comparison is by JSON text, which keeps key order and array order).
 */
export function normalizeGuardrails(guardrails: PresetGuardrails | null | undefined): Guardrails {
  const given = guardrails ?? {};
  return {
    temperature: given.temperature ?? null,
    max_tokens: given.max_tokens ?? null,
    repetition_penalty: given.repetition_penalty ?? null,
    frequency_penalty: given.frequency_penalty ?? null,
    presence_penalty: given.presence_penalty ?? null,
    top_p: given.top_p ?? null,
    quantizations: given.quantizations ?? null,
    ignore: given.ignore ?? null,
    require_parameters: given.require_parameters ?? null,
    system_prompt: given.system_prompt ?? null,
    clear: GUARDRAIL_KEYS.filter((key) => given.clear?.includes(key)),
  };
}

/** One preset as the server starts it, every field present so a draft compares equal to what is saved. */
export const PRESET_DEFAULTS: PresetSettings = {
  enabled: false,
  auto_apply: false,
  interval_hours: 24,
  adopted: false,
  slug: 'vibonomics-best-value',
  template_slug: null,
  profile: null,
  dimension: 'composite',
  min_score: 60,
  require_tool_call: true,
  min_context: 0,
  max_task_cost: null,
  min_value: null,
  include_free: false,
  paid_fallback: false,
  allow_free_only: true,
  switch_margin_pct: 10,
  max_models: 1,
  max_providers: 3,
  blocked_models: [],
  blocked_providers: [],
  route: 'fallback',
  sort: null,
  prefer_own_tests: null,
  guardrails: NO_GUARDRAILS,
};

export type SettingsSectionKey = keyof FullSettings;

export function fullSettings(settings: AppSettings): FullSettings {
  return {
    general: settings.general ?? { instance_name: 'Vibonomics', hide_inactive_models: true },
    refresh: settings.refresh ?? {
      models_interval_hours: 6,
      balances_interval_minutes: 30,
      benchmarks_interval_hours: 24,
      usage_interval_hours: 12,
      refresh_on_startup: true,
      openrouter_endpoint_discounts: true,
    },
    highlight: settings.highlight ?? { new_model_days: 7, new_discount_days: 7 },
    scoring: {
      intelligence_weight: 0.3,
      coding_weight: 0.35,
      agentic_weight: 0.35,
      own_tests_weight: 1,
      prefer_own_tests: false,
      prefer_own_tests_min_tasks: 5,
      free_model_cost: 0.01,
      default_profile: 'agentic',
      ...settings.scoring,
      profiles: settings.scoring?.profiles ?? [],
    },
    benchmarks: {
      aa_terms_acknowledged: false,
      ...settings.benchmarks,
      sources: settings.benchmarks?.sources ?? {},
    },
    evals: settings.evals ?? {
      max_steps: 14,
      max_output_tokens: 4096,
      concurrency: 2,
      request_timeout_seconds: 180,
      sandbox_mode: 'auto',
    },
    presets: {
      blocked_models: [],
      blocked_providers: [],
      ...settings.presets,
      items: (settings.presets?.items ?? []).map((item) => ({
        ...PRESET_DEFAULTS,
        ...item,
        guardrails: normalizeGuardrails(item.guardrails),
      })),
    },
  };
}
