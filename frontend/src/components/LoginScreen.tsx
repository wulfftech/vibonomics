import { Button, Card, Center, Group, PasswordInput, Stack, Text, Title } from '@mantine/core';
import { ChartNoAxesCombined } from 'lucide-react';
import { useState } from 'react';
import { useLogin } from '../api/system';

/** The password form. `note` says what the password is for. */
export function LoginCard({ note }: { note: string }) {
  const login = useLogin();
  const [password, setPassword] = useState('');

  return (
    <Card withBorder padding="lg" w={360} maw="100%">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          login.mutate(password);
        }}
      >
        <Stack gap="md">
          <Group gap="sm">
            <ChartNoAxesCombined size={20} color="var(--mantine-primary-color-filled)" aria-hidden />
            <Title order={3}>Vibonomics</Title>
          </Group>
          <Text size="sm" c="dimmed">
            {note}
          </Text>
          <PasswordInput
            label="Password"
            autoComplete="current-password"
            value={password}
            autoFocus
            error={login.error?.message}
            onChange={(event) => setPassword(event.currentTarget.value)}
          />
          <Button type="submit" loading={login.isPending} disabled={password === ''}>
            Sign in
          </Button>
        </Stack>
      </form>
    </Card>
  );
}

/** Shown when `VIBONOMICS_PASSWORD` is set, reading is not open, and this browser has no session yet. */
export function LoginScreen() {
  return (
    <Center mih="100dvh" p="md">
      <LoginCard note="This instance is password protected." />
    </Center>
  );
}
