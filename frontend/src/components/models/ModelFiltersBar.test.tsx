import { MantineProvider } from '@mantine/core';
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_FILTERS, MODEL_FILTERS_KEY, sanitizeFilters, type ModelFilters } from '../../lib/modelTable';
import { usePersistentState } from '../../lib/usePersistentState';
import { ModelFiltersBar } from './ModelFiltersBar';

function bar(filters: ModelFilters, showHideRestricted: boolean | undefined) {
  const onChange = vi.fn();
  render(
    <MantineProvider>
      <ModelFiltersBar
        filters={filters}
        onChange={onChange}
        profiles={[]}
        profile={undefined}
        providers={[]}
        showHideRestricted={showHideRestricted}
      />
    </MantineProvider>,
  );
  return onChange;
}

describe('Hide OpenCode-only control', () => {
  it('is offered next to the other quick filters when an OpenCode key is set', () => {
    bar(DEFAULT_FILTERS, true);
    expect(screen.getByRole('checkbox', { name: 'Hide OpenCode-only' })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Free' })).toBeInTheDocument();
  });

  it('is not shown without an OpenCode key, even when a saved filter is on', () => {
    bar({ ...DEFAULT_FILTERS, hideRestricted: true }, false);
    expect(screen.queryByText('Hide OpenCode-only')).toBeNull();
    expect(screen.getByRole('checkbox', { name: 'Free' })).toBeInTheDocument();
  });

  it('is not shown unless the page asks for it', () => {
    bar(DEFAULT_FILTERS, undefined);
    expect(screen.queryByText('Hide OpenCode-only')).toBeNull();
  });

  it('switches the filter on', () => {
    const onChange = bar(DEFAULT_FILTERS, true);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Hide OpenCode-only' }));
    const update = onChange.mock.calls[0][0] as (old: ModelFilters) => ModelFilters;
    expect(update(DEFAULT_FILTERS)).toEqual({ ...DEFAULT_FILTERS, hideRestricted: true });
  });

  it('switches it off again and shows a saved choice as checked', () => {
    const on = { ...DEFAULT_FILTERS, hideRestricted: true };
    const onChange = bar(on, true);
    const control = screen.getByRole('checkbox', { name: 'Hide OpenCode-only' });
    expect(control).toBeChecked();
    fireEvent.click(control);
    const update = onChange.mock.calls[0][0] as (old: ModelFilters) => ModelFilters;
    expect(update(on).hideRestricted).toBe(false);
  });
});

describe('Hide OpenCode-only persistence', () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => window.localStorage.clear());

  const useFilters = () => usePersistentState<ModelFilters>(MODEL_FILTERS_KEY, DEFAULT_FILTERS, sanitizeFilters);

  it('is off for a first visit', () => {
    const { result } = renderHook(useFilters);
    expect(result.current[0].hideRestricted).toBe(false);
  });

  it('is saved with the other filters and read back on the next visit', () => {
    const first = renderHook(useFilters);
    act(() => first.result.current[1]((old) => ({ ...old, hideRestricted: true, search: 'glm' })));
    expect(JSON.parse(window.localStorage.getItem(MODEL_FILTERS_KEY) ?? '{}')).toMatchObject({
      hideRestricted: true,
      search: 'glm',
    });
    first.unmount();
    const second = renderHook(useFilters);
    expect(second.result.current[0]).toMatchObject({ hideRestricted: true, search: 'glm' });
  });

  it('is off for filters saved before the control existed', () => {
    window.localStorage.setItem(MODEL_FILTERS_KEY, JSON.stringify({ search: 'glm', chips: ['free'] }));
    const { result } = renderHook(useFilters);
    expect(result.current[0]).toMatchObject({ search: 'glm', chips: ['free'], hideRestricted: false });
  });
});
