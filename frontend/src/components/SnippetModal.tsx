import { Modal } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useModelDetail } from '../api/models';
import { ErrorState, LoadingState } from './DataState';
import { SnippetTabs } from './SnippetTabs';

/** Copyable harness config for one model. */
export function SnippetModal({ modelId, onClose }: { modelId: number | null; onClose: () => void }) {
  const mobile = useMediaQuery('(max-width: 48em)');
  const detail = useModelDetail(modelId);
  const title = detail.data ? `Config — ${detail.data.row.name}` : 'Config';

  return (
    <Modal opened={modelId !== null} onClose={onClose} title={title} size="lg" fullScreen={mobile}>
      {detail.isPending && <LoadingState />}
      {detail.isError && <ErrorState message={detail.error.message} />}
      {detail.data && <SnippetTabs snippets={detail.data.snippets} />}
    </Modal>
  );
}
