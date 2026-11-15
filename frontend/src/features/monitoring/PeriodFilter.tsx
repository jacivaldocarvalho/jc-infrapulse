import { Button, Group, Paper, Select, Stack, Text, TextInput } from '@mantine/core';
import { useState } from 'react';
import type { Window } from '../../shared/api/client';
import { localInput, recentWindow, validWindow } from './windows';
export function PeriodFilter({
  window,
  onChange,
}: {
  window: Window;
  onChange: (next: Window) => void;
}) {
  const [start, setStart] = useState(localInput(window.start));
  const [end, setEnd] = useState(localInput(window.end));
  const [error, setError] = useState('');
  return (
    <Paper withBorder p="lg">
      <Stack gap="sm">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const startDate = new Date(start);
            const endDate = new Date(end);
            if (
              !Number.isFinite(startDate.getTime()) ||
              !Number.isFinite(endDate.getTime()) ||
              !validWindow(startDate.toISOString(), endDate.toISOString())
            ) {
              setError('Informe um período positivo de até 90 dias.');
              return;
            }
            setError('');
            onChange({ start: startDate.toISOString(), end: endDate.toISOString() });
          }}
        >
          <Group align="flex-end">
            <TextInput
              type="datetime-local"
              label="Início · horário local"
              value={start}
              onChange={(event) => setStart(event.currentTarget.value)}
              required
            />
            <TextInput
              type="datetime-local"
              label="Fim · horário local (exclusivo)"
              value={end}
              onChange={(event) => setEnd(event.currentTarget.value)}
              required
            />
            <Button type="submit" variant="default">
              Aplicar período
            </Button>
            <Select
              label="Período rápido"
              placeholder="Selecionar"
              data={[
                { value: '1', label: 'Última hora' },
                { value: '24', label: 'Últimas 24 horas' },
                { value: '168', label: 'Últimos 7 dias' },
                { value: '2160', label: 'Últimos 90 dias' },
              ]}
              onChange={(value) => {
                if (value) onChange(recentWindow(Number(value)));
              }}
            />
          </Group>
        </form>
        {error && (
          <Text role="alert" c="red" size="sm">
            {error}
          </Text>
        )}
        <Text size="xs" c="dimmed">
          Janela fixa [início, fim), sem polling histórico. Use “Atualizar período até agora” para
          avançá-la. Máximo: 90 dias.
        </Text>
      </Stack>
    </Paper>
  );
}
