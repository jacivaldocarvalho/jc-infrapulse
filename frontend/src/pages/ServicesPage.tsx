import {
  Badge,
  Button,
  Checkbox,
  Group,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { Link, useSearchParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useGlobalTotals, useServices, useVisibleStatuses } from '../features/services/queries';
import { dateTime, pageNumber, stateLabel } from '../shared/lib/presentation';
import { Empty, ErrorNotice, Loading, Updated } from '../shared/ui/Feedback';
import { StatusBadge } from '../shared/ui/StatusBadge';
import { useServiceDeletions } from '../features/services/deletion-state';
import { DeleteService } from '../features/services/DeleteService';
function GlobalTotals({ live }: { live: boolean }) {
  const totals = useGlobalTotals(live);
  return (
    <section aria-label="Indicadores globais">
      <Group mb="sm">
        <Text size="sm" fw={600}>
          Inventário global
        </Text>
        <Badge variant="outline" color="gray">
          Todos os serviços
        </Badge>
      </Group>
      <SimpleGrid cols={{ base: 1, xs: 3 }}>
        {totals.map((query, index) => (
          <Paper key={index} withBorder p="lg">
            <Text size="sm" c="dimmed">
              {['Serviços cadastrados', 'Monitoramento ativo', 'Monitoramento inativo'][index]}
            </Text>
            <div className="metric-value">{query.isPending ? '…' : (query.data?.total ?? '—')}</div>
            {query.isError && (
              <Text size="xs" c="red">
                ⚠ Erro de atualização{query.data ? ' · dados desatualizados' : ''}
              </Text>
            )}
          </Paper>
        ))}
      </SimpleGrid>
      <Text size="xs" c="dimmed" mt="xs">
        Totais consultados separadamente; alterações concorrentes podem causar diferença momentânea.
      </Text>
    </section>
  );
}
export default function ServicesPage({ dashboard = false }: { dashboard?: boolean }) {
  const queryClient = useQueryClient();
  const deletions = useServiceDeletions();
  const [params, setParams] = useSearchParams();
  const [live, setLive] = useState(true);
  const page = pageNumber(params.get('page'), 1);
  const limit = [10, 20, 50].includes(Number(params.get('limit')))
    ? Number(params.get('limit'))
    : 20;
  const activity = params.get('activity') ?? 'all';
  const isActive = activity === 'active' ? true : activity === 'inactive' ? false : undefined;
  const list = useServices(limit, (page - 1) * limit, isActive, live);
  const services = list.data?.items ?? [];
  const statuses = useVisibleStatuses(services, live);
  const count = (label: string) =>
    statuses.filter(
      (query) =>
        query.data !== undefined && !query.isError && stateLabel(query.data.last_status) === label,
    ).length;
  const failures = statuses.filter((query) => query.isError).length;
  const pending = statuses.filter((query) => query.isPending).length;
  const updateParams = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    if (key !== 'page') next.set('page', '1');
    setParams(next);
  };
  const refresh = () => {
    void queryClient.refetchQueries({ queryKey: ['services'], type: 'active' });
    statuses.forEach((query, index) => {
      if (deletions.pending.has(services[index].id) || deletions.removed.has(services[index].id))
        return;
      void query.refetch();
    });
  };
  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-start">
        <div>
          <Text c="teal" size="xs" fw={700} tt="uppercase" mb={5}>
            Monitoramento de APIs
          </Text>
          <Title order={1}>{dashboard ? 'Visão geral' : 'Serviços'}</Title>
          <Text c="dimmed" mt={6}>
            {dashboard
              ? 'Acompanhe seus serviços. Entenda cada sinal.'
              : 'Gerencie os destinos e suas políticas de verificação.'}
          </Text>
        </div>
        <Button
          component={Link}
          to="/services/new"
          leftSection={<span aria-hidden="true">＋</span>}
        >
          Novo serviço
        </Button>
      </Group>
      {dashboard && <GlobalTotals live={live} />}
      <Paper withBorder p="lg">
        <Group justify="space-between" mb="md">
          <div>
            <Title order={2} size="h4">
              Estado dos serviços nesta página
            </Title>
            <Text size="xs" c="dimmed">
              Contagens parciais · {services.length} registros carregados · não representam o total
              global
            </Text>
          </div>
          <Group>
            <Badge color="teal" variant="light">
              ✓ UP {count('UP')}
            </Badge>
            <Badge color="red" variant="light">
              × DOWN {count('DOWN')}
            </Badge>
            <Badge color="gray" variant="light">
              ◌ Sem dados {count('Sem dados')}
            </Badge>
            <Badge color="gray" variant="outline">
              Ⅱ Inativos {services.filter((s) => !s.is_active).length}
            </Badge>
            {pending > 0 && <Badge color="gray">Carregando {pending}</Badge>}
            {failures > 0 && <Badge color="orange">⚠ Erro de atualização {failures}</Badge>}
          </Group>
        </Group>
        <Text size="xs" c="dimmed">
          Atividade é independente do último resultado. Falhas de consulta não contam como DOWN ou
          sem dados.
        </Text>
      </Paper>
      <Paper withBorder>
        <Group justify="space-between" p="lg">
          <Group align="flex-end">
            <Select
              label="Atividade"
              value={['all', 'active', 'inactive'].includes(activity) ? activity : 'all'}
              data={[
                { value: 'all', label: 'Todos' },
                { value: 'active', label: 'Ativos' },
                { value: 'inactive', label: 'Inativos' },
              ]}
              onChange={(value) => updateParams('activity', value ?? 'all')}
              w={155}
            />
            <Select
              label="Por página"
              value={String(limit)}
              data={['10', '20', '50']}
              onChange={(value) => updateParams('limit', value ?? '20')}
              w={100}
            />
          </Group>
          <Group>
            <Checkbox
              label="Atualização a cada 30 s"
              checked={live}
              onChange={(event) => setLive(event.currentTarget.checked)}
            />
            <Button variant="default" onClick={refresh} loading={list.isFetching}>
              ↻ Atualizar
            </Button>
          </Group>
        </Group>
        {list.isError && (
          <div style={{ padding: 16 }}>
            <ErrorNotice error={list.error} retry={refresh} stale={!!list.data} />
          </div>
        )}
        {list.isPending ? (
          <Loading />
        ) : list.data && services.length === 0 ? (
          <Empty
            title={page > 1 ? 'Nenhum serviço nesta página' : 'Nenhum serviço encontrado'}
            description={
              page > 1
                ? 'Volte à primeira página ou ajuste os filtros.'
                : 'Cadastre um serviço ou ajuste o filtro de atividade.'
            }
          />
        ) : (
          list.data && (
            <Table.ScrollContainer minWidth={780}>
              <Table verticalSpacing="md" horizontalSpacing="lg" highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Serviço</Table.Th>
                    <Table.Th>Último estado</Table.Th>
                    <Table.Th>Última verificação · horário local</Table.Th>
                    <Table.Th>Intervalo</Table.Th>
                    <Table.Th>Ações</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {services.map((service, index) => {
                    const status = statuses[index];
                    return (
                      <Table.Tr key={service.id}>
                        <Table.Td>
                          <Link className="service-link" to={`/services/${service.id}`}>
                            {service.name}
                          </Link>
                          <Text size="xs" c="dimmed" maw={290} truncate title={service.url}>
                            {service.url}
                          </Text>
                        </Table.Td>
                        <Table.Td>
                          {status.isPending ? (
                            <Text size="xs">Carregando…</Text>
                          ) : status.data ? (
                            <StatusBadge
                              status={status.data.last_status}
                              active={service.is_active}
                            />
                          ) : (
                            <Badge color="orange">⚠ Erro de atualização</Badge>
                          )}
                          {!status.data && !service.is_active && (
                            <Badge color="gray" variant="outline">
                              Ⅱ Inativo
                            </Badge>
                          )}
                          {status.isError && status.data && (
                            <Text size="xs" c="orange">
                              ⚠ Erro de atualização · dados desatualizados
                            </Text>
                          )}
                          {status.data?.last_status && status.data.last_status !== 'UP' && (
                            <Text size="xs" c="dimmed" mt={4}>
                              {status.data.last_status}
                            </Text>
                          )}
                        </Table.Td>
                        <Table.Td>
                          <Text size="sm" className="mono">
                            {dateTime(status.data?.last_checked_at)}
                          </Text>
                        </Table.Td>
                        <Table.Td>{service.check_interval_seconds} s</Table.Td>
                        <Table.Td>
                          <Group gap={4} wrap="nowrap">
                            <Button
                              component={Link}
                              to={`/services/${service.id}/edit`}
                              variant="subtle"
                              size="xs"
                              aria-label={`Editar ${service.name}`}
                            >
                              Editar
                            </Button>
                            <DeleteService service={service} />
                          </Group>
                        </Table.Td>
                      </Table.Tr>
                    );
                  })}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )
        )}
        <Group p="lg" justify="space-between">
          <Updated at={list.dataUpdatedAt} fetching={list.isFetching} />
          <Group>
            <Text size="sm">
              Página {page} · {list.data?.total ?? '—'} serviços no filtro
            </Text>
            <Button
              size="xs"
              variant="default"
              disabled={page === 1}
              onClick={() => updateParams('page', String(page - 1))}
            >
              Anterior
            </Button>
            <Button
              size="xs"
              variant="default"
              disabled={!list.data || page * limit >= list.data.total}
              onClick={() => updateParams('page', String(page + 1))}
            >
              Próxima
            </Button>
            {page > 1 && services.length === 0 && (
              <Button size="xs" onClick={() => updateParams('page', '1')}>
                Primeira página
              </Button>
            )}
          </Group>
        </Group>
      </Paper>
      <Text size="xs" c="dimmed">
        O estado representa o último resultado persistido, não uma verificação em tempo real.
      </Text>
    </Stack>
  );
}
