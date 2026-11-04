import { SegmentedControl, Stack, Title } from '@mantine/core';
import { Navigate, useNavigate, useParams } from 'react-router';
import { useCanChange } from '../api/system';
import { LoginCard } from '../components/LoginScreen';
import { BenchmarksSection } from './settings/BenchmarksSection';
import { GeneralSection } from './settings/GeneralSection';
import { OpenRouterSection } from './settings/OpenRouterSection';
import { ProvidersSection } from './settings/ProvidersSection';
import { RefreshSection } from './settings/RefreshSection';
import { ScoringSection } from './settings/ScoringSection';
import { SubscriptionSection } from './settings/SubscriptionSection';
import { TestingSection } from './settings/TestingSection';

const SECTIONS = [
  { value: 'providers', label: 'Providers' },
  { value: 'benchmarks', label: 'Benchmarks' },
  { value: 'scoring', label: 'Scoring' },
  { value: 'refresh', label: 'Refresh' },
  { value: 'testing', label: 'Testing' },
  { value: 'general', label: 'General' },
] as const;

/** Settings that belong to one provider sit in a second row under Providers, so each provider can grow its own. */
const PROVIDER_TABS = [
  { value: 'all', label: 'All providers' },
  { value: 'claude', label: 'Claude' },
  { value: 'openrouter', label: 'OpenRouter' },
] as const;

/** Where a section that moved is now, so an old link or bookmark still lands somewhere. */
const MOVED: Record<string, string> = {
  subscription: '/settings/providers/claude',
  presets: '/settings/providers/openrouter',
};

type Section = (typeof SECTIONS)[number]['value'];
type ProviderTab = (typeof PROVIDER_TABS)[number]['value'];

export function SettingsPage() {
  const { section, sub } = useParams();
  const navigate = useNavigate();
  const canChange = useCanChange();
  const active: Section = SECTIONS.find((s) => s.value === section)?.value ?? 'providers';
  const providerTab: ProviderTab = PROVIDER_TABS.find((t) => t.value === sub)?.value ?? 'all';

  if (section !== undefined && section in MOVED) {
    return <Navigate to={MOVED[section] ?? '/settings'} replace />;
  }

  // A visitor to an instance that is open to read has no business here: every control on these pages changes
  // something, and they would only meet a 401.
  if (!canChange) {
    return (
      <Stack gap="lg">
        <Title order={2}>Settings</Title>
        <LoginCard note="Settings are password protected. The rest of Vibonomics is open to read." />
      </Stack>
    );
  }

  return (
    <Stack gap="lg">
      <Title order={2}>Settings</Title>
      <Stack gap="xs" align="flex-start" style={{ maxWidth: '100%' }}>
        <SegmentedControl
          value={active}
          onChange={(value) => {
            void navigate(`/settings/${value}`);
          }}
          data={SECTIONS.map((s) => ({ ...s }))}
          style={{ maxWidth: '100%', overflowX: 'auto' }}
        />
        {active === 'providers' && (
          <SegmentedControl
            size="xs"
            value={providerTab}
            onChange={(value) => {
              void navigate(value === 'all' ? '/settings/providers' : `/settings/providers/${value}`);
            }}
            data={PROVIDER_TABS.map((t) => ({ ...t }))}
            aria-label="Provider settings"
            style={{ maxWidth: '100%', overflowX: 'auto' }}
          />
        )}
      </Stack>
      {active === 'providers' && providerTab === 'all' && <ProvidersSection />}
      {active === 'providers' && providerTab === 'claude' && <SubscriptionSection />}
      {active === 'providers' && providerTab === 'openrouter' && <OpenRouterSection />}
      {active === 'benchmarks' && <BenchmarksSection />}
      {active === 'scoring' && <ScoringSection />}
      {active === 'refresh' && <RefreshSection />}
      {active === 'testing' && <TestingSection />}
      {active === 'general' && <GeneralSection />}
    </Stack>
  );
}
