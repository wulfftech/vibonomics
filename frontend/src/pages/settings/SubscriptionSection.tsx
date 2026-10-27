import { Alert, Anchor, Button, Card, Code, CopyButton, Group, Stack, Text, Title, Tooltip } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { Check, CircleAlert, Copy } from 'lucide-react';
import { Link } from 'react-router';
import { useProviders } from '../../api/providers';
import { useClaudeSubscription, useClearClaudeSnapshot, useRotateClaudeToken } from '../../api/subscription';
import { ErrorState, LoadingState } from '../../components/DataState';
import { SubscriptionDetails } from '../../components/models/SubscriptionDetails';
import { claudeUsage } from '../../lib/claudeUsage';
import { relativeTime } from '../../lib/format';

function CopyCode({ value, label }: { value: string; label: string }) {
  return (
    <Group gap="xs" justify="flex-end">
      <CopyButton value={value} timeout={2000}>
        {({ copied, copy }) => (
          <Tooltip label={copied ? 'Copied' : `Copy ${label}`} withArrow>
            <Button
              variant="light"
              size="xs"
              leftSection={copied ? <Check size={14} /> : <Copy size={14} />}
              onClick={copy}
              aria-label={`Copy ${label}`}
            >
              {copied ? 'Copied' : 'Copy'}
            </Button>
          </Tooltip>
        )}
      </CopyButton>
    </Group>
  );
}

export function SubscriptionSection() {
  const claude = useClaudeSubscription();
  const providers = useProviders();
  const rotate = useRotateClaudeToken();
  const clear = useClearClaudeSnapshot();

  if (claude.isPending) {
    return <LoadingState />;
  }
  if (claude.isError) {
    return <ErrorState message={claude.error.message} />;
  }
  const data = claude.data;
  const usage = claudeUsage(data.snapshot, providers.data ?? []);
  const settingsJson = JSON.stringify({ statusLine: { type: 'command', command: data.statusline_command } }, null, 2);

  return (
    <Stack gap="lg">
      <Stack gap={4}>
        <Title order={3}>Claude subscription</Title>
        <Text size="sm" c="dimmed">
          Claude Pro and Max plans don&apos;t have a dollar balance. Usage is a rolling 5-hour session window and a
          weekly window (Max also has per-model weekly windows); the same windows cover Claude, Claude Code and cloud
          sessions. Usage credits are the optional pay-as-you-go overage and are measured in money per month.
        </Text>
      </Stack>

      <Card withBorder padding="md">
        <Stack gap="md">
          <Group justify="space-between" wrap="nowrap">
            <Title order={4}>Current usage</Title>
            {data.snapshot && (
              <Button
                variant="subtle"
                size="xs"
                color="red"
                loading={clear.isPending}
                onClick={() =>
                  clear.mutate(undefined, {
                    onSuccess: () => notifications.show({ message: 'Usage data cleared', color: 'green' }),
                    onError: (error) => notifications.show({ message: error.message, color: 'red' }),
                  })
                }
              >
                Clear received usage
              </Button>
            )}
          </Group>
          {usage ? (
            <>
              <SubscriptionDetails usage={usage} />
              <Text size="xs" c="dimmed">
                {usage.live ? 'Live from Claude Code' : 'From the Anthropic login token'}
                {usage.updatedAt ? ` · updated ${relativeTime(usage.updatedAt)}` : ''}
              </Text>
            </>
          ) : (
            <Text size="sm" c="dimmed">
              Nothing received yet. Set up the status line below; usage appears after your next Claude Code turn.
            </Text>
          )}
        </Stack>
      </Card>

      <Card withBorder padding="md">
        <Stack gap="md">
          <Title order={4}>Live updates from Claude Code</Title>
          <Text size="sm" c="dimmed">
            A status line is a Claude Code setting, not a Vibonomics one: Claude Code runs a command you configure every
            time it redraws its footer and shows whatever that command prints. The same JSON it feeds the command
            contains the rate-limit windows. Point the command at Vibonomics and the card on the Models page stays
            current, while the footer shows a short confirmation.
          </Text>

          <Text size="sm" fw={500}>
            1. Find your settings file
          </Text>
          <Text size="sm" c="dimmed">
            <Code>statusLine</Code> lives in Claude Code&apos;s user settings, <Code>settings.json</Code> inside the{' '}
            <Code>.claude</Code> folder in your home directory. Create the file if it does not exist yet.
          </Text>
          <Code block>
            {`Windows  C:\\Users\\<you>\\.claude\\settings.json\nmacOS    /Users/<you>/.claude/settings.json\nLinux    /home/<you>/.claude/settings.json`}
          </Code>
          <Text size="xs" c="dimmed">
            Claude Code itself can write it for you: run <Code>/statusline</Code> in a session and describe what you
            want, for example &ldquo;run this command&rdquo; with the command below. Project settings in a
            repository&apos;s <Code>.claude/settings.json</Code> also work, but the user file applies everywhere.
          </Text>

          <Text size="sm" fw={500}>
            2. Add the status line to it
          </Text>
          <Text size="sm" c="dimmed">
            Add this key <strong>inside</strong> the file&apos;s existing outermost <Code>{'{ … }'}</Code>, alongside
            keys such as <Code>model</Code> — not as a second <Code>{'{ … }'}</Code> block. A file with two top-level
            objects is not valid JSON, and Claude Code then ignores every setting in it, not just this one. Keep the
            settings that are already there.
          </Text>
          <Code block style={{ whiteSpace: 'pre-wrap' }}>
            {settingsJson}
          </Code>
          <CopyCode value={settingsJson} label="the settings snippet" />

          <Text size="sm" fw={500}>
            3. Or set the command directly
          </Text>
          <Text size="sm" c="dimmed">
            The snippet&apos;s <Code>command</Code> value on its own, if you keep your settings in another way:
          </Text>
          <Code block style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
            {data.statusline_command}
          </Code>
          <CopyCode value={data.statusline_command} label="the status line command" />

          <Text size="xs" c="dimmed">
            This replaces any existing status line. To keep yours, run it first and the Vibonomics command after it,
            separated by <Code>;</Code>. The command carries an ingest token, so treat it as a secret; rotate it below
            if it leaks. The endpoint only accepts usage snapshots, never model requests. The command needs a POSIX
            shell: macOS and Linux have one, and on Windows Claude Code runs it through Git Bash (a Claude Code
            requirement), so it works as-is; without Git Bash the equivalent is a <Code>curl</Code> POST with the same
            headers and <Code>--data-binary @-</Code>. If nothing appears, check that the file parses (for example{' '}
            <Code>python -m json.tool settings.json</Code>) and that <Code>statusLine</Code> sits at the top level.
          </Text>
          <Group gap="xs">
            <Button
              variant="default"
              loading={rotate.isPending}
              onClick={() => {
                rotate.mutate(undefined, {
                  onSuccess: () =>
                    notifications.show({
                      message: 'Ingest token replaced. Update your status line command.',
                      color: 'green',
                    }),
                  onError: (error) => notifications.show({ message: error.message, color: 'red' }),
                });
              }}
            >
              Rotate ingest token
            </Button>
            <Anchor href={data.docs_url} target="_blank" rel="noreferrer" size="sm">
              Claude Code status line documentation
            </Anchor>
          </Group>
        </Stack>
      </Card>

      <Card withBorder padding="md">
        <Stack gap="sm">
          <Title order={4}>Usage credits (overage)</Title>
          <Text size="sm">
            When usage credits are on, Anthropic bills the extra tokens per month against a spend limit or balance.
            Claude Code reads that from the claude.ai usage endpoint, which needs a login token with the{' '}
            <Code>user:profile</Code> scope. Add one under{' '}
            <Anchor component={Link} to="/settings/providers" size="sm">
              Providers → Anthropic
            </Anchor>{' '}
            (&ldquo;Claude Code login token&rdquo;) to show the monthly credit spend here. Tokens from{' '}
            <Code>claude setup-token</Code> only have inference scope and won&apos;t work.
          </Text>
          <Text size="xs" c="dimmed">
            That endpoint is not a documented public API. The login token expires (Claude Code refreshes its own copy;
            Vibonomics never refreshes yours, to avoid invalidating it), so the status-line route is the reliable one.
          </Text>
        </Stack>
      </Card>

      <Alert color="yellow" variant="light" icon={<CircleAlert size={16} />} title="Cloud sessions">
        Claude Code on the web (cloud sessions) uses the same 5-hour and weekly windows as local Claude Code. There is
        no separate cloud-session credit to display.
      </Alert>
    </Stack>
  );
}
