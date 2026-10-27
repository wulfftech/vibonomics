/** Statuses that carry a real grade. Mirrors `GRADED_STATUSES` in `backend/vibonomics/scoring.py`. */
const GRADED_STATUSES = new Set(['passed', 'partial', 'failed']);

/**
 * Whether a result's `score` is a grade rather than the placeholder the API stores for rows it left out of
 * every average. `error` and `ungraded` rows keep `score: 0`, so showing that number would read as a zero.
 */
export function isGraded(status: string | null | undefined): boolean {
  return GRADED_STATUSES.has((status ?? '').toLowerCase());
}

/** Badge colours for the status strings the API uses (refreshes, balances, runs, results). */
export function statusColor(status: string | null | undefined): string {
  switch ((status ?? '').toLowerCase()) {
    case 'ok':
    case 'done':
    case 'pass':
    case 'passed':
    case 'success':
      return 'green';
    case 'failed':
    case 'fail':
    case 'error':
    case 'errored':
      return 'red';
    case 'partial':
    case 'warning':
    case 'timeout':
      return 'yellow';
    case 'running':
    case 'queued':
      return 'blue';
    default:
      return 'gray';
  }
}

export function statusLabel(status: string | null | undefined): string {
  if (!status) {
    return '—';
  }
  return status.charAt(0).toUpperCase() + status.slice(1).replace(/_/g, ' ');
}
