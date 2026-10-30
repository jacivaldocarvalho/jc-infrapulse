import { Alert, Button, Center, Loader, Stack, Text } from '@mantine/core';
import { errorMessage } from '../api/errors';
import { dateTime } from '../lib/presentation';
export function Loading({ label = 'Carregando dados…' }: { label?: string }) {
  return (
    <Center py="xl" role="status">
      <Stack align="center" gap="sm">
        <Loader size="sm" />
        <Text size="sm">{label}</Text>
      </Stack>
    </Center>
  );
}
export function ErrorNotice({
  error,
  retry,
  stale = false,
}: {
  error: unknown;
  retry?: () => void;
  stale?: boolean;
}) {
  return (
    <Alert
      color="red"
      title={stale ? '⚠ Erro de atualização · dados desatualizados' : '⚠ Erro ao carregar'}
      role="alert"
    >
      <Stack gap="sm">
        <Text size="sm">{errorMessage(error)}</Text>
        {retry && (
          <Button variant="light" color="red" size="xs" w="fit-content" onClick={retry}>
            Tentar novamente
          </Button>
        )}
      </Stack>
    </Alert>
  );
}
export function Empty({ title, description }: { title: string; description: string }) {
  return (
    <Stack align="center" py="xl" gap="xs">
      <Text size="xl" aria-hidden="true">
        ◌
      </Text>
      <Text fw={600}>{title}</Text>
      <Text size="sm" c="dimmed" ta="center">
        {description}
      </Text>
    </Stack>
  );
}
export function Updated({ at, fetching }: { at: number; fetching?: boolean }) {
  return (
    <Text size="xs" c="dimmed" role="status">
      {fetching ? 'Atualizando…' : `Atualizado em ${dateTime(at)} · horário local`}
    </Text>
  );
}
