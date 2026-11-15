import '@mantine/charts/styles.css';
import { LineChart } from '@mantine/charts';
import { Accordion, Paper, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core';
import type { Window } from '../../shared/api/client';
import { dateTime, number } from '../../shared/lib/presentation';
import { Empty, ErrorNotice, Loading } from '../../shared/ui/Feedback';
import { useSeries } from './queries';
export default function Charts({ id, window }: { id: string; window: Window }) {
  const series = useSeries(id, window);
  if (series.isPending) return <Loading label="Carregando as janelas históricas…" />;
  if (!series.data)
    return (
      <ErrorNotice
        error={series.error}
        retry={() => {
          void series.refetch();
        }}
      />
    );
  const data = series.data.map((item) => ({
    label: new Intl.DateTimeFormat('pt-BR', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(item.start ?? window.start)),
    uptime: item.uptime_percentage,
    average: item.average_response_time_ms,
    p95: item.p95_response_time_ms,
  }));
  return (
    <Stack>
      <div>
        <Title order={2} size="h3">
          Histórico de confiabilidade
        </Title>
        <Text size="sm" c="dimmed">
          Até 12 janelas contíguas · horários locais · lacunas representam ausência de dados
        </Text>
      </div>
      {series.isError && <ErrorNotice error={series.error} stale />}
      {series.data.every((item) => item.total_checks === 0) ? (
        <Empty
          title="Sem dados para os gráficos"
          description="Não há verificações no período selecionado."
        />
      ) : (
        <SimpleGrid cols={{ base: 1, lg: 2 }}>
          <Paper withBorder p="lg" className="chart-container">
            <Title order={3} size="h5" mb="lg">
              Disponibilidade por checks (%)
            </Title>
            <div
              role="img"
              aria-label="Gráfico de disponibilidade por janela; valores disponíveis na tabela abaixo"
            >
              <LineChart
                h={240}
                data={data}
                dataKey="label"
                series={[{ name: 'uptime', color: 'teal.7', label: 'Checks UP (%)' }]}
                curveType="linear"
                connectNulls={false}
                yAxisProps={{ domain: [0, 100] }}
                valueFormatter={(value) => number(value, '%')}
              />
            </div>
          </Paper>
          <Paper withBorder p="lg" className="chart-container">
            <Title order={3} size="h5" mb="lg">
              Duração das verificações (ms)
            </Title>
            <div
              role="img"
              aria-label="Gráfico de média e p95 por janela; valores disponíveis na tabela abaixo"
            >
              <LineChart
                h={240}
                data={data}
                dataKey="label"
                series={[
                  { name: 'average', color: 'blue.6', label: 'Média (ms)' },
                  { name: 'p95', color: 'grape.6', label: 'p95 (ms)' },
                ]}
                withLegend
                curveType="linear"
                connectNulls={false}
                valueFormatter={(value) => number(value, 'ms')}
              />
            </div>
          </Paper>
        </SimpleGrid>
      )}
      <Text size="xs" c="dimmed">
        Cada p95 é calculado pela API sobre os checks da respectiva janela. O p95 do período é
        consultado separadamente.
      </Text>
      <Accordion variant="contained">
        <Accordion.Item value="data">
          <Accordion.Control>Ver dados dos gráficos em tabela</Accordion.Control>
          <Accordion.Panel>
            <Table.ScrollContainer minWidth={650}>
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Início</Table.Th>
                    <Table.Th>Fim exclusivo</Table.Th>
                    <Table.Th>Checks</Table.Th>
                    <Table.Th>Disponibilidade</Table.Th>
                    <Table.Th>Média</Table.Th>
                    <Table.Th>p95</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {series.data.map((item) => (
                    <Table.Tr key={item.start}>
                      <Table.Td>{dateTime(item.start)}</Table.Td>
                      <Table.Td>{dateTime(item.end)}</Table.Td>
                      <Table.Td>{item.total_checks}</Table.Td>
                      <Table.Td>{number(item.uptime_percentage, '%')}</Table.Td>
                      <Table.Td>{number(item.average_response_time_ms, 'ms')}</Table.Td>
                      <Table.Td>{number(item.p95_response_time_ms, 'ms')}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Accordion.Panel>
        </Accordion.Item>
      </Accordion>
    </Stack>
  );
}
