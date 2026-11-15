import {
  Alert,
  Anchor,
  Badge,
  Button,
  Checkbox,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { lazy, Suspense, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { useServiceDeletions } from '../features/services/deletion-state';
import { DeleteService } from '../features/services/DeleteService';
import { useService, useVisibleStatuses } from '../features/services/queries';
import { useChecks, useMetrics } from '../features/monitoring/queries';
import { PeriodFilter } from '../features/monitoring/PeriodFilter';
import { recentWindow, validWindow } from '../features/monitoring/windows';
import type { Window } from '../shared/api/client';
import { dateTime, number, pageNumber } from '../shared/lib/presentation';
import { Empty, ErrorNotice, Loading, Updated } from '../shared/ui/Feedback';
import { StatusBadge } from '../shared/ui/StatusBadge';
const Charts = lazy(() => import('../features/monitoring/Charts'));
function Detail({ id }: { id: string }) {
  const navigate = useNavigate();
  const deletions = useServiceDeletions();
  const blocked = deletions.pending.has(id) || deletions.removed.has(id);
  const [params, setParams] = useSearchParams();
  const [initial] = useState(() => recentWindow());
  const [live, setLive] = useState(true);
  const [showCharts, setShowCharts] = useState(false);
  const window: Window = {
    start: params.get('start') ?? initial.start,
    end: params.get('end') ?? initial.end,
  };
  const valid = validWindow(window.start, window.end);
  const page = pageNumber(params.get('page'), 1, 501);
  const service = useService(id);
  const [status] = useVisibleStatuses(service.data ? [service.data] : [], live);
  const metrics = useMetrics(id, window, !!service.data && valid);
  const checks = useChecks(id, window, page, !!service.data && valid);
  const changeWindow = (next: Window) => {
    setParams({ ...next, page: '1' });
  };
  if (service.isPending) return <Loading />;
  if (!service.data)
    return (
      <ErrorNotice
        error={service.error}
        retry={() => {
          void service.refetch();
        }}
      />
    );
  return (
    <Stack gap="xl">
      <Anchor component={Link} to="/services" size="sm">
        ← Voltar aos serviços
      </Anchor>
      <Group justify="space-between" align="flex-start">
        <div>
          <Text size="xs" c="teal" fw={700} tt="uppercase">
            Detalhe do serviço
          </Text>
          <Title order={1}>{service.data.name}</Title>
          <Text size="sm" c="dimmed" mt="xs" style={{ overflowWrap: 'anywhere' }}>
            {service.data.url}
          </Text>
        </div>
        <Group>
          <Button component={Link} to={`/services/${id}/edit`} variant="default">
            Editar serviço
          </Button>
          <DeleteService service={service.data} onDeleted={() => navigate('/services')} />
        </Group>
      </Group>
      {service.isError && <ErrorNotice error={service.error} stale />}
      <Paper withBorder p="lg">
        <Group justify="space-between" align="flex-start">
          <Stack gap="xs">
            <Text size="sm" fw={600}>
              Último estado conhecido · independente do período
            </Text>
            {status?.isPending ? (
              <Text size="sm">Carregando estado…</Text>
            ) : status?.data ? (
              <StatusBadge status={status.data.last_status} active={service.data.is_active} />
            ) : (
              <Badge color="orange">⚠ Erro de atualização</Badge>
            )}
            {!status?.data && !service.data.is_active && (
              <Badge color="gray" variant="outline">
                Ⅱ Inativo
              </Badge>
            )}
            <Text size="sm">
              Último check: {dateTime(status?.data?.last_checked_at)} · horário local
            </Text>
            {status?.data?.last_status && status.data.last_status !== 'UP' && (
              <Text size="xs" c="dimmed">
                Causa técnica: {status.data.last_status}
              </Text>
            )}
            {status?.isError && status.data && (
              <Text size="sm" c="orange">
                ⚠ Erro de atualização · dados desatualizados
              </Text>
            )}
          </Stack>
          <Stack gap="sm">
            <Checkbox
              disabled={blocked}
              label="Atualizar estado a cada 30 s"
              checked={live}
              onChange={(event) => setLive(event.currentTarget.checked)}
            />
            <Button
              variant="default"
              size="xs"
              disabled={blocked}
              onClick={() => {
                if (blocked) return;
                void status?.refetch();
              }}
            >
              ↻ Atualizar estado
            </Button>
            <Text size="xs" c="dimmed">
              Intervalo {service.data.check_interval_seconds} s · timeout{' '}
              {service.data.timeout_seconds} s
            </Text>
          </Stack>
        </Group>
      </Paper>
      {!valid ? (
        <Alert color="red" title="Período inválido" role="alert">
          Informe início e fim válidos, com duração positiva de até 90 dias.
          <Button ml="md" size="xs" onClick={() => changeWindow(recentWindow())}>
            Usar últimas 24 horas
          </Button>
        </Alert>
      ) : (
        <>
          <PeriodFilter
            key={`${window.start}:${window.end}`}
            window={window}
            onChange={changeWindow}
          />
          <Group justify="space-between">
            <Text size="sm" c="dimmed">
              {dateTime(window.start)} → {dateTime(window.end)} · horário local
            </Text>
            <Button
              variant="subtle"
              size="xs"
              onClick={() =>
                changeWindow(
                  recentWindow((Date.parse(window.end) - Date.parse(window.start)) / 3_600_000),
                )
              }
            >
              ↻ Atualizar período até agora
            </Button>
          </Group>
          {metrics.isPending ? (
            <Loading label="Carregando métricas do período…" />
          ) : metrics.data ? (
            <Stack gap="sm">
              {metrics.isError && <ErrorNotice error={metrics.error} stale />}
              <SimpleGrid cols={{ base: 1, xs: 2, lg: 4 }}>
                {[
                  ['Disponibilidade por checks', number(metrics.data.uptime_percentage, '%')],
                  ['Duração média', number(metrics.data.average_response_time_ms, 'ms')],
                  ['Duração p95', number(metrics.data.p95_response_time_ms, 'ms')],
                  ['Verificações no período', number(metrics.data.total_checks)],
                ].map(([label, value]) => (
                  <Paper key={label} withBorder p="lg">
                    <Text size="sm" c="dimmed">
                      {label}
                    </Text>
                    <div className="metric-value">{value}</div>
                  </Paper>
                ))}
              </SimpleGrid>
              <Text size="xs" c="dimmed">
                {metrics.data.up_checks} UP · {metrics.data.down_checks} falhas. Disponibilidade é a
                proporção de checks UP, sem ponderação pelo tempo. Durações incluem checks
                malsucedidos.
              </Text>
              {metrics.data.total_checks === 0 && (
                <Text size="sm">
                  Sem verificações neste período; disponibilidade e latências não estão disponíveis.
                </Text>
              )}
            </Stack>
          ) : (
            <ErrorNotice
              error={metrics.error}
              retry={() => {
                void metrics.refetch();
              }}
            />
          )}
          <Paper withBorder p="lg">
            <Group justify="space-between">
              <div>
                <Title order={2} size="h4">
                  Gráficos históricos
                </Title>
                <Text size="sm" c="dimmed">
                  Carregados sob demanda para este serviço e período.
                </Text>
              </div>
              <Button variant="light" onClick={() => setShowCharts((value) => !value)}>
                {showCharts ? 'Ocultar gráficos' : 'Carregar gráficos'}
              </Button>
            </Group>
            {showCharts && (
              <div style={{ marginTop: 24 }}>
                <Suspense fallback={<Loading />}>
                  <Charts id={id} window={window} />
                </Suspense>
              </div>
            )}
          </Paper>
          <Paper withBorder component="section" aria-label="Histórico de verificações">
            <Group justify="space-between" p="lg">
              <div>
                <Title order={2} size="h4">
                  Histórico de verificações
                </Title>
                <Text size="sm" c="dimmed">
                  Mais recentes primeiro · horários locais · duração em ms
                </Text>
              </div>
              <Text size="sm">{checks.data?.total ?? '—'} verificações</Text>
            </Group>
            {checks.isError && (
              <ErrorNotice
                error={checks.error}
                stale={!!checks.data}
                retry={() => {
                  void checks.refetch();
                }}
              />
            )}{' '}
            {checks.isPending ? (
              <Loading />
            ) : checks.data?.items.length === 0 ? (
              <Empty
                title="Nenhuma verificação no período"
                description="O serviço pode ainda não ter sido verificado, estar inativo ou não ter checks na janela selecionada."
              />
            ) : (
              checks.data && (
                <Table.ScrollContainer minWidth={720}>
                  <Table verticalSpacing="sm" horizontalSpacing="lg">
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th>Horário</Table.Th>
                        <Table.Th>Estado e causa</Table.Th>
                        <Table.Th>HTTP</Table.Th>
                        <Table.Th>Duração</Table.Th>
                        <Table.Th>Detalhe</Table.Th>
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {checks.data.items.map((check) => (
                        <Table.Tr key={check.id}>
                          <Table.Td className="mono">{dateTime(check.checked_at)}</Table.Td>
                          <Table.Td>
                            <StatusBadge status={check.status} />
                            {check.status !== 'UP' && (
                              <Text size="xs" c="dimmed">
                                {check.status}
                              </Text>
                            )}
                          </Table.Td>
                          <Table.Td>{check.http_status_code ?? '—'}</Table.Td>
                          <Table.Td>{number(check.response_time_ms, 'ms')}</Table.Td>
                          <Table.Td>
                            <Text size="xs">{check.error_message ?? '—'}</Text>
                          </Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                </Table.ScrollContainer>
              )
            )}
            <Group p="lg" justify="space-between">
              <Updated at={checks.dataUpdatedAt} />
              <Group>
                <Text size="sm">Página {page}</Text>
                <Button
                  variant="default"
                  size="xs"
                  disabled={page === 1}
                  onClick={() => setParams({ ...window, page: String(page - 1) })}
                >
                  Anterior
                </Button>
                <Button
                  variant="default"
                  size="xs"
                  disabled={!checks.data || page * 20 >= checks.data.total || page >= 501}
                  onClick={() => setParams({ ...window, page: String(page + 1) })}
                >
                  Próxima
                </Button>
              </Group>
            </Group>
            {checks.data && checks.data.total > 10020 && (
              <Text p="lg" size="xs" c="dimmed">
                Limite de paginação da API atingido. Reduza o período para consultar outros
                resultados.
              </Text>
            )}
          </Paper>
        </>
      )}
    </Stack>
  );
}
export default function ServiceDetailPage() {
  const { id } = useParams();
  return id ? <Detail key={id} id={id} /> : null;
}
