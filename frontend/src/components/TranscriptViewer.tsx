import { Badge, Button, Card, Code, Group, Spoiler, Stack, Text } from '@mantine/core';
import { useState } from 'react';
import { EmptyState } from './DataState';

/**
 * Renders an eval transcript defensively: OpenAI-style messages (`role`, `content`, `tool_calls`),
 * Anthropic-style content blocks (`tool_use`, `tool_result`) and anything else as JSON.
 */

const LIMIT = 6000;

const ROLE_COLORS: Record<string, string> = {
  system: 'gray',
  user: 'blue',
  assistant: 'indigo',
  tool: 'orange',
  judge: 'grape',
};

type Json = Record<string, unknown>;

function isObject(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function pretty(value: unknown): string {
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

function LongText({ value, mono = false }: { value: string; mono?: boolean }) {
  const [full, setFull] = useState(false);
  const truncated = !full && value.length > LIMIT;
  const shown = truncated ? value.slice(0, LIMIT) : value;
  return (
    <Stack gap={4}>
      <Spoiler maxHeight={mono ? 160 : 200} showLabel="Expand" hideLabel="Collapse">
        {mono ? (
          <Code block style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {shown}
          </Code>
        ) : (
          <Text size="sm" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {shown}
          </Text>
        )}
      </Spoiler>
      {truncated && (
        <Group gap="xs">
          <Text size="xs" c="dimmed">
            {(value.length - LIMIT).toLocaleString()} more characters
          </Text>
          <Button variant="subtle" size="compact-xs" onClick={() => setFull(true)}>
            Show all
          </Button>
        </Group>
      )}
    </Stack>
  );
}

function ContentBlock({ block }: { block: unknown }) {
  if (typeof block === 'string') {
    return block.trim() ? <LongText value={block} /> : null;
  }
  if (!isObject(block)) {
    return <LongText value={pretty(block)} mono />;
  }
  const type = typeof block.type === 'string' ? block.type : '';
  if (type === 'text' && typeof block.text === 'string') {
    return <LongText value={block.text} />;
  }
  if (type === 'tool_use') {
    return (
      <Stack gap={2}>
        <Text size="xs" fw={600}>
          → {typeof block.name === 'string' ? block.name : 'tool call'}
        </Text>
        <LongText value={pretty(block.input)} mono />
      </Stack>
    );
  }
  if (type === 'tool_result') {
    const content = block.content;
    return (
      <Stack gap={2}>
        <Text size="xs" fw={600}>
          ← result
        </Text>
        <LongText value={typeof content === 'string' ? content : pretty(content)} mono />
      </Stack>
    );
  }
  return <LongText value={pretty(block)} mono />;
}

function ToolCalls({ calls }: { calls: unknown[] }) {
  return (
    <Stack gap={6}>
      {calls.map((call, index) => {
        const fn = isObject(call) && isObject(call.function) ? call.function : isObject(call) ? call : {};
        const name = typeof fn.name === 'string' ? fn.name : 'tool call';
        const args = 'arguments' in fn ? fn.arguments : 'input' in fn ? fn.input : call;
        return (
          <Stack key={isObject(call) && typeof call.id === 'string' ? call.id : index} gap={2}>
            <Text size="xs" fw={600}>
              → {name}
            </Text>
            <LongText value={pretty(args)} mono />
          </Stack>
        );
      })}
    </Stack>
  );
}

function Message({ entry }: { entry: unknown }) {
  if (!isObject(entry) || typeof entry.role !== 'string') {
    return (
      <Card withBorder padding="xs">
        <LongText value={pretty(entry)} mono />
      </Card>
    );
  }
  const role = entry.role;
  const content = entry.content;
  const toolCalls: unknown[] = Array.isArray(entry.tool_calls) ? (entry.tool_calls as unknown[]) : [];
  const name = typeof entry.name === 'string' ? entry.name : null;
  return (
    <Card withBorder padding="xs">
      <Stack gap={6}>
        <Group gap={6}>
          <Badge size="xs" variant="light" color={ROLE_COLORS[role] ?? 'gray'}>
            {role}
          </Badge>
          {name && (
            <Text size="xs" c="dimmed">
              {name}
            </Text>
          )}
        </Group>
        {role === 'tool' && typeof content === 'string' ? (
          <LongText value={content} mono />
        ) : Array.isArray(content) ? (
          (content as unknown[]).map((block, index) => <ContentBlock key={index} block={block} />)
        ) : content !== null && content !== undefined ? (
          <ContentBlock block={content} />
        ) : null}
        {toolCalls.length > 0 && <ToolCalls calls={toolCalls} />}
      </Stack>
    </Card>
  );
}

export function TranscriptViewer({ transcript }: { transcript: unknown[] | undefined }) {
  if (!transcript || transcript.length === 0) {
    return <EmptyState message="No transcript recorded." />;
  }
  return (
    <Stack gap={6}>
      {transcript.map((entry, index) => (
        <Message key={index} entry={entry} />
      ))}
    </Stack>
  );
}
