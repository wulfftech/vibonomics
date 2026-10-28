import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Copies through a selection and `execCommand`. `navigator.clipboard` exists only in a secure context, and this
 * app is often opened over plain http on a LAN address, where it is undefined.
 *
 * The field goes inside `host`, not on the body: a Mantine modal traps focus, and a field outside the modal
 * cannot be focused, so nothing would be selected.
 */
function copyBySelection(text: string, host: HTMLElement): boolean {
  const field = document.createElement('textarea');
  field.value = text;
  field.setAttribute('readonly', '');
  field.setAttribute('aria-hidden', 'true');
  field.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
  const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  host.appendChild(field);
  try {
    field.focus({ preventScroll: true });
    field.select();
    field.setSelectionRange(0, text.length);
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    field.remove();
    previous?.focus({ preventScroll: true });
  }
}

/** Resolves to whether the text reached the clipboard. Call it from a click, as browsers require. */
export async function copyText(text: string, host: HTMLElement = document.body): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Denied, or the page lacks focus: the selection route may still work.
    }
  }
  return copyBySelection(text, host);
}

/** `copy` runs `copyText`; `copied` and `failed` each hold for `timeout` ms, so the button can show the result. */
export function useCopyText(timeout = 1500) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copy = useCallback(
    async (text: string, host?: HTMLElement) => {
      const ok = await copyText(text, host);
      setState(ok ? 'copied' : 'failed');
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setState('idle'), timeout);
    },
    [timeout],
  );

  return { copied: state === 'copied', failed: state === 'failed', copy };
}
