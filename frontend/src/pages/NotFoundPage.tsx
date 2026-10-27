import { Anchor, Stack, Text, Title } from '@mantine/core';
import { Link } from 'react-router';

export function NotFoundPage() {
  return (
    <Stack gap="lg">
      <Title order={2}>Not found</Title>
      <Text size="sm" c="dimmed">
        There is no page here.{' '}
        <Anchor component={Link} to="/" size="sm">
          Back to the models
        </Anchor>
      </Text>
    </Stack>
  );
}
