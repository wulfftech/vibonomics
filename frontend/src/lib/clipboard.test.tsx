import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CopyIconButton } from '../components/CopyIconButton';
import { copyText } from './clipboard';

// Over plain http (a LAN address) there is no navigator.clipboard, which is how the app is usually opened.

interface Copy {
  command: string;
  value: string;
  selected: string;
  focusedInside: Element | null;
}

function stubExecCommand(result = true): Copy[] {
  const calls: Copy[] = [];
  Object.defineProperty(document, 'execCommand', {
    configurable: true,
    value: (command: string) => {
      const field = document.activeElement as HTMLTextAreaElement;
      calls.push({
        command,
        value: field.value,
        selected: field.value.slice(field.selectionStart ?? 0, field.selectionEnd ?? 0),
        focusedInside: field.parentElement,
      });
      return result;
    },
  });
  return calls;
}

function stubClipboard(secure: boolean, writeText?: (text: string) => Promise<void>) {
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: secure });
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: writeText ? { writeText } : undefined });
}

afterEach(() => {
  Reflect.deleteProperty(document, 'execCommand');
  Reflect.deleteProperty(navigator, 'clipboard');
  Reflect.deleteProperty(window, 'isSecureContext');
  document.body.innerHTML = '';
});

describe('copyText', () => {
  it('copies by selection when there is no clipboard API, inside the host and then cleans up', async () => {
    stubClipboard(false);
    const calls = stubExecCommand();
    const host = document.body.appendChild(document.createElement('div'));

    expect(await copyText('openai/gpt-6:batch', host)).toBe(true);

    expect(calls).toEqual([
      { command: 'copy', value: 'openai/gpt-6:batch', selected: 'openai/gpt-6:batch', focusedInside: host },
    ]);
    expect(document.querySelector('textarea')).toBeNull();
  });

  it('uses the clipboard API in a secure context', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard(true, writeText);
    const calls = stubExecCommand();

    expect(await copyText('abc')).toBe(true);
    expect(writeText).toHaveBeenCalledWith('abc');
    expect(calls).toEqual([]);
  });

  it('falls back to selection when the clipboard API refuses', async () => {
    stubClipboard(true, vi.fn().mockRejectedValue(new Error('NotAllowedError')));
    const calls = stubExecCommand();

    expect(await copyText('abc')).toBe(true);
    expect(calls).toHaveLength(1);
  });

  it('reports a failure instead of claiming success', async () => {
    stubClipboard(false);
    stubExecCommand(false);
    expect(await copyText('abc')).toBe(false);
    expect(document.querySelector('textarea')).toBeNull();
  });
});

describe('CopyIconButton', () => {
  it('copies on click without the click reaching the row underneath', async () => {
    stubClipboard(false);
    const calls = stubExecCommand();
    const onRowClick = vi.fn();
    render(
      <MantineProvider>
        <div onClick={onRowClick}>
          <CopyIconButton value="anthropic/claude-opus-5.5" label="Copy model ID" />
        </div>
      </MantineProvider>,
    );

    await userEvent.click(screen.getByRole('button', { name: 'Copy model ID' }));

    expect(calls.map((call) => call.value)).toEqual(['anthropic/claude-opus-5.5']);
    expect(onRowClick).not.toHaveBeenCalled();
  });
});
