/** Column sizing for the models table: content-sized columns that share leftover width. */

export interface ColumnSpec {
  id: string;
  min: number;
  max: number;
  /** Share of leftover width this column should take; 0 or absent means fixed. */
  grow?: number;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/**
 * Start from the measured content width (clamped), then hand out spare space to growing columns
 * in proportion to their weight, never past their maximum.
 */
export function layoutColumns(
  measured: Record<string, number>,
  columns: ColumnSpec[],
  available: number,
): Record<string, number> {
  const state = columns.map((column) => ({
    ...column,
    width: clamp(measured[column.id] ?? column.min, column.min, column.max),
  }));
  let extra = available - state.reduce((sum, column) => sum + column.width, 0);
  while (extra > 1) {
    const growing = state.filter((column) => (column.grow ?? 0) > 0 && column.width < column.max);
    if (growing.length === 0) {
      break;
    }
    const weight = growing.reduce((sum, column) => sum + (column.grow ?? 0), 0);
    let used = 0;
    for (const column of growing) {
      const room = column.max - column.width;
      const share = Math.min(room, (extra * (column.grow ?? 0)) / weight);
      column.width += share;
      used += share;
    }
    if (used < 1) {
      break;
    }
    extra -= used;
  }
  return Object.fromEntries(state.map((column) => [column.id, Math.round(column.width)]));
}
