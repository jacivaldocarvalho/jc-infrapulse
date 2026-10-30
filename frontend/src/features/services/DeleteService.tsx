import { Button, Group, Modal, Stack, Text } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, type Service } from '../../shared/api/client';
import { ErrorNotice } from '../../shared/ui/Feedback';
import { deletionState, removeServiceFromLists, useServiceDeletions } from './deletion-state';
export function DeleteService({
  service,
  onDeleted,
}: {
  service: Service;
  onDeleted?: () => void;
}) {
  const [opened, setOpened] = useState(false);
  const client = useQueryClient();
  const state = deletionState(client);
  const deletions = useServiceDeletions();
  const blocked = deletions.pending.has(service.id) || deletions.removed.has(service.id);
  const cancelService = () =>
    Promise.all([
      client.cancelQueries({ queryKey: ['monitoring', service.id] }),
      client.cancelQueries({ queryKey: ['services', 'detail', service.id] }),
    ]);
  const mutation = useMutation({
    mutationFn: () => api.remove(service.id),
    onMutate: async () => {
      state.begin(service.id);
      try {
        await cancelService();
        return { started: true };
      } catch (error) {
        state.finish(service.id);
        throw error;
      }
    },
    onSuccess: async () => {
      // A pre-delete list response must never restore the removed row or its observers.
      await Promise.all([
        client.cancelQueries({ queryKey: ['services', 'list'] }),
        client.cancelQueries({ queryKey: ['services', 'total'] }),
        cancelService(),
      ]);
      state.complete(service.id);
      removeServiceFromLists(client, service);
      setOpened(false);
      onDeleted?.();
      client.removeQueries({ queryKey: ['monitoring', service.id] });
      client.removeQueries({ queryKey: ['services', 'detail', service.id] });
      await Promise.all([
        client.invalidateQueries({ queryKey: ['services', 'list'] }),
        client.invalidateQueries({ queryKey: ['services', 'total'] }),
      ]);
    },
    onError: (_error, _variables, context) => {
      if (!context?.started) return;
      state.finish(service.id);
      void client.invalidateQueries({ queryKey: ['monitoring', service.id] });
      void client.invalidateQueries({ queryKey: ['services', 'detail', service.id] });
    },
    onSettled: (_data, _error, _variables, context) => {
      if (context?.started) state.finish(service.id);
    },
  });
  return (
    <>
      <Button
        disabled={blocked}
        size="xs"
        color="red"
        variant="subtle"
        onClick={() => {
          mutation.reset();
          setOpened(true);
        }}
        aria-label={`Excluir ${service.name}`}
      >
        Excluir
      </Button>
      <Modal
        opened={opened}
        onClose={() => {
          if (!mutation.isPending) setOpened(false);
        }}
        title="Excluir serviço?"
        centered
        closeOnEscape={!mutation.isPending}
        closeOnClickOutside={!mutation.isPending}
      >
        <Stack>
          <Text>
            Excluir <strong>{service.name}</strong> removerá definitivamente o serviço e todo o
            histórico de verificações.
          </Text>
          {mutation.isError && <ErrorNotice error={mutation.error} />}
          <Group justify="flex-end">
            <Button
              variant="default"
              onClick={() => setOpened(false)}
              disabled={mutation.isPending}
            >
              Cancelar
            </Button>
            <Button color="red" onClick={() => mutation.mutate()} loading={mutation.isPending}>
              Excluir definitivamente
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
