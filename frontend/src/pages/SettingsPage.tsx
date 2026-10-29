import { SegmentedControl, Stack, Title } from '@mantine/core';
import { useNavigate, useParams } from 'react-router';
import { useCanChange } from '../api/system';
import { LoginCard } from '../components/LoginScreen';
import { BenchmarksSection } from './settings/BenchmarksSection';
import { GeneralSection } from './settings/GeneralSection';
import { ProvidersSection } from './settings/ProvidersSection';
import { RefreshSection } from './settings/RefreshSection';
import { ScoringSection } from './settings/ScoringSection';
import { SubscriptionSection } from './settings/SubscriptionSection';
import { TestingSection } from './settings/TestingSection';

const SECTIONS = [
  { value: 'providers', label: 'Providers' },
  { value: 'subscription', label: 'Claude' },
  { value: 'benchmarks', label: 'Benchmarks' },
  { value: 'scoring', label: 'Scoring' },
  { value: 'refresh', label: 'Refresh' },
  { value: 'testing', label: 'Testing' },
  { value: 'general', label: 'General' },
] as const;

type Section = (typeof SECTIONS)[number]['value'];

export function SettingsPage() {
  const { section } = useParams();
  const navigate = useNavigate();
  const canChange = useCanChange();
  const active: Section = SECTIONS.find((s) => s.value === section)?.value ?? 'providers';

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
      <SegmentedControl
        value={active}
        onChange={(value) => {
          void navigate(`/settings/${value}`);
        }}
        data={SECTIONS.map((s) => ({ ...s }))}
        style={{ alignSelf: 'flex-start', maxWidth: '100%', overflowX: 'auto' }}
      />
      {active === 'providers' && <ProvidersSection />}
      {active === 'subscription' && <SubscriptionSection />}
      {active === 'benchmarks' && <BenchmarksSection />}
      {active === 'scoring' && <ScoringSection />}
      {active === 'refresh' && <RefreshSection />}
      {active === 'testing' && <TestingSection />}
      {active === 'general' && <GeneralSection />}
    </Stack>
  );
}
