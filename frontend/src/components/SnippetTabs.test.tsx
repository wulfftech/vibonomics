import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ModelDetail, SnippetOut } from '../api/types';
import { SnippetTabs } from './SnippetTabs';

function snippet(content: string): SnippetOut {
  return { id: 'curl', harness: 'curl', title: 'Chat completion request', language: 'bash', content, notes: null };
}

function detail(providerType: string, discount: number | null): ModelDetail {
  return {
    row: {
      id: 7,
      provider_type: providerType,
      model_id: 'acme/flash',
      price: { input: 0.14, output: 0.42 },
      discount,
    },
    endpoints: [
      { provider: 'Wafer', tag: 'wafer/us', input: 0.045, output: 0.5, discount: 0 },
      { provider: 'DeepInfra', tag: 'deepinfra/fp8', input: 0.14, output: 0.42, discount: 0.3 },
    ],
    snippets: [snippet('curl with default routing')],
  } as unknown as ModelDetail;
}

function renderTabs(model: ModelDetail) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider>
        <SnippetTabs detail={model} />
      </MantineProvider>
    </QueryClientProvider>,
  );
}

const fetchMock = vi.fn();

function answerWith(snippets: SnippetOut[]) {
  fetchMock.mockResolvedValue(new Response(JSON.stringify(snippets), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
}

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe('SnippetTabs', () => {
  it('starts a discounted OpenRouter row on the provider that discount belongs to', async () => {
    answerWith([snippet('curl pinned to deepinfra')]);
    renderTabs(detail('openrouter', 0.3));
    expect(await screen.findByText('curl pinned to deepinfra')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('api/models/7/snippets?provider=deepinfra%2Ffp8&fallbacks=false');
    expect(screen.getByRole('combobox', { name: 'Route via' })).toHaveValue(
      'DeepInfra · deepinfra/fp8 · $0.14 / $0.42 · −30%',
    );
    expect(screen.getByLabelText('Allow fallbacks to other providers')).not.toBeChecked();
  });

  it('shows the default snippets, and asks for nothing, when OpenRouter routes as usual', () => {
    renderTabs(detail('openrouter', null));
    expect(screen.getByText('curl with default routing')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Route via' })).toHaveValue('Default routing');
    expect(screen.queryByLabelText('Allow fallbacks to other providers')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('has no routing choice for any other provider', () => {
    renderTabs(detail('deepseek', 0.3));
    expect(screen.getByText('curl with default routing')).toBeInTheDocument();
    expect(screen.queryByLabelText('Route via')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
