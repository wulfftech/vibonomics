import type { AuthStatus } from '../api/types';

/** Whether to show the full-page login: a password is set, this browser has no session, and reading is not open. */
export function needsLoginScreen(auth: AuthStatus): boolean {
  return auth.enabled && !auth.authenticated && !auth.open_read;
}

/** Whether this browser may change things: there is no password, or it has logged in. */
export function canChange(auth: AuthStatus | undefined): boolean {
  return auth !== undefined && (!auth.enabled || auth.authenticated);
}
