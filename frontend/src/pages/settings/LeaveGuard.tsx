import { Button, Group, Modal, Text } from '@mantine/core';
import { useCallback, useContext, useEffect } from 'react';
import { UNSAFE_DataRouterContext, useBlocker, type BlockerFunction } from 'react-router';

/** Asks the browser to confirm closing or reloading the tab. */
function useWarnOnUnload(when: boolean) {
  useEffect(() => {
    if (!when) {
      return undefined;
    }
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [when]);
}

function RouteBlocker({ when }: { when: boolean }) {
  const shouldBlock = useCallback<BlockerFunction>(
    ({ currentLocation, nextLocation }) => when && currentLocation.pathname !== nextLocation.pathname,
    [when],
  );
  const blocker = useBlocker(shouldBlock);
  return (
    <Modal
      opened={blocker.state === 'blocked'}
      onClose={() => blocker.reset?.()}
      title="Discard unsaved changes?"
      centered
    >
      <Text size="sm">Leaving this section throws away what you changed in it.</Text>
      <Group justify="flex-end" mt="lg">
        <Button variant="default" onClick={() => blocker.reset?.()}>
          Keep editing
        </Button>
        <Button color="red" onClick={() => blocker.proceed?.()}>
          Discard and leave
        </Button>
      </Group>
    </Modal>
  );
}

/**
 * Stops unsaved edits vanishing without a word. A section's draft is component state, so moving to another section
 * or closing the tab would drop it. Renders nothing until `when` is true.
 */
export function LeaveGuard({ when }: { when: boolean }) {
  useWarnOnUnload(when);
  // useBlocker only works under a data router. The app has one; a component rendered bare, or under a MemoryRouter
  // (the tests of the sections), has none, and there is nothing to navigate away from there.
  return useContext(UNSAFE_DataRouterContext) === null ? null : <RouteBlocker when={when} />;
}
