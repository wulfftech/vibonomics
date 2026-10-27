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
