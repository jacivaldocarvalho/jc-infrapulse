import { Badge, Group } from '@mantine/core';
import type { CheckStatus } from '../api/client';
import { stateLabel } from '../lib/presentation';
export function StatusBadge({ status, active }: { status: CheckStatus | null; active?: boolean }) {
  const label = stateLabel(status);
  return (
    <Group gap={6} wrap="wrap">
      <Badge
        color={label === 'UP' ? 'teal' : label === 'DOWN' ? 'red' : 'gray'}
        variant="light"
        leftSection={
          <span aria-hidden="true">{label === 'UP' ? '✓' : label === 'DOWN' ? '×' : '◌'}</span>
        }
      >
        {label}
      </Badge>
      {active === false && (
        <Badge color="gray" variant="outline">
          Ⅱ Inativo
        </Badge>
      )}
    </Group>
  );
}
