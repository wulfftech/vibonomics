import { describe, expect, it } from 'vitest';
import type { AuthStatus } from '../api/types';
import { canChange, needsLoginScreen } from './auth';

const status = (enabled: boolean, authenticated: boolean, open_read = false): AuthStatus => ({
  enabled,
  authenticated,
  open_read,
});

describe('needsLoginScreen', () => {
  it('shows it only when a password is set, there is no session and reading is closed', () => {
    expect(needsLoginScreen(status(true, false))).toBe(true);
    expect(needsLoginScreen(status(true, true))).toBe(false);
    expect(needsLoginScreen(status(false, true))).toBe(false);
  });

  it('lets visitors in when reading is open', () => {
    expect(needsLoginScreen(status(true, false, true))).toBe(false);
  });
});

describe('canChange', () => {
  it('allows it with no password, or after logging in', () => {
    expect(canChange(status(false, true))).toBe(true);
    expect(canChange(status(true, true, true))).toBe(true);
  });

  it('refuses a visitor, even when reading is open', () => {
    expect(canChange(status(true, false))).toBe(false);
    expect(canChange(status(true, false, true))).toBe(false);
  });

  it('refuses until the status is known', () => {
    expect(canChange(undefined)).toBe(false);
  });
});
