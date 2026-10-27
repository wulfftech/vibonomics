import { Alert, Center, Loader, Stack, Text } from '@mantine/core';
import { CircleAlert } from 'lucide-react';

/** The loading, empty and error states every data view shares. */

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <Center py="xl">
      <Stack align="center" gap="xs">
        <Loader size="sm" />
        <Text c="dimmed" size="sm">
          {label}
        </Text>
      </Stack>
    </Center>
  );
}

export function ErrorState({ title = 'Something went wrong', message }: { title?: string; message?: string }) {
  return (
    <Alert color="red" icon={<CircleAlert size={16} />} title={title}>
      {message ?? 'The request failed. Check the Vibonomics server log.'}
    </Alert>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <Center py="xl">
      <Text c="dimmed" size="sm">
        {message}
      </Text>
    </Center>
  );
}
