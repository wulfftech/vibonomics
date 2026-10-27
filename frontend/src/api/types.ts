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

export type ClaudeSubscriptionOut = Schemas['ClaudeSubscriptionOut'];
export type SubscriptionSnapshot = Schemas['SnapshotOut'];
export type SubscriptionWindow = Schemas['WindowOut'];
export type ExtraUsage = Schemas['ExtraUsageOut'];
export type SpendLimit = Schemas['SpendLimitOut'];

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
}

export type SettingsSectionKey = keyof FullSettings;

export function fullSettings(settings: AppSettings): FullSettings {
  return {
    general: settings.general ?? { instance_name: 'Vibonomics', hide_inactive_models: true },
    refresh: settings.refresh ?? {
      models_interval_hours: 6,
      balances_interval_minutes: 30,
      benchmarks_interval_hours: 24,
      refresh_on_startup: true,
      openrouter_endpoint_discounts: true,
    },
    highlight: settings.highlight ?? { new_model_days: 7, new_discount_days: 7 },
    scoring: {
      intelligence_weight: 0.3,
      coding_weight: 0.35,
      agentic_weight: 0.35,
      own_tests_weight: 1,
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
  };
}
