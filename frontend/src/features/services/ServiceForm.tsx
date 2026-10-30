import { Button, Group, NumberInput, Paper, Stack, Switch, Text, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router';
import { api, type Service } from '../../shared/api/client';
import { ApiError } from '../../shared/api/errors';
import { ErrorNotice } from '../../shared/ui/Feedback';
import {
  defaults,
  servicePatch,
  servicePayload,
  validateService,
  type ServiceFormValues,
} from './form';
export function ServiceForm({ service }: { service?: Service }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const form = useForm<ServiceFormValues>({
    initialValues: service
      ? {
          name: service.name,
          url: service.url,
          check_interval_seconds: service.check_interval_seconds,
          timeout_seconds: service.timeout_seconds,
          is_active: service.is_active,
        }
      : defaults,
    validate: validateService,
  });
  const mutation = useMutation({
    mutationFn: (values: ServiceFormValues) =>
      service
        ? api.update(service.id, servicePatch(service, values))
        : api.create(servicePayload(values)),
    onSuccess: async (result) => {
      client.setQueryData(['services', 'detail', result.id], result);
      await client.invalidateQueries({ queryKey: ['services'] });
      await client.invalidateQueries({ queryKey: ['monitoring', result.id] });
      navigate(`/services/${result.id}`);
    },
    onError: (error) => {
      if (error instanceof ApiError) form.setErrors(error.fields);
    },
  });
  return (
    <Paper withBorder p={{ base: 'md', sm: 'xl' }} maw={760}>
      <form onSubmit={form.onSubmit((values) => mutation.mutate(values))} noValidate>
        <Stack gap="lg">
          <TextInput
            label="Nome do serviço"
            placeholder="Ex.: Payments API"
            required
            maxLength={120}
            {...form.getInputProps('name')}
            autoComplete="off"
          />
          <TextInput
            label="URL de verificação"
            description="Destino HTTP ou HTTPS. A política de segurança é validada pela API."
            placeholder="https://api.example.com/health"
            required
            {...form.getInputProps('url')}
            autoComplete="off"
          />
          <Group grow align="flex-start">
            <NumberInput
              label="Intervalo entre checks (s)"
              min={10}
              max={2147483647}
              allowDecimal={false}
              required
              {...form.getInputProps('check_interval_seconds')}
            />
            <NumberInput
              label="Timeout (s)"
              min={1}
              max={2147483647}
              allowDecimal={false}
              required
              {...form.getInputProps('timeout_seconds')}
            />
          </Group>
          <Switch
            label="Monitoramento ativo"
            description="Serviços inativos preservam o histórico, mas não recebem novos checks."
            {...form.getInputProps('is_active', { type: 'checkbox' })}
          />
          {mutation.isError && <ErrorNotice error={mutation.error} />}
          <Text size="xs" c="dimmed">
            O worker executa as verificações conforme o intervalo configurado. Salvar não dispara um
            check imediato.
          </Text>
          <Group justify="flex-end">
            <Button
              component={Link}
              to={service ? `/services/${service.id}` : '/services'}
              variant="default"
              disabled={mutation.isPending}
            >
              Cancelar
            </Button>
            <Button type="submit" loading={mutation.isPending}>
              {service ? 'Salvar alterações' : 'Cadastrar serviço'}
            </Button>
          </Group>
        </Stack>
      </form>
    </Paper>
  );
}
