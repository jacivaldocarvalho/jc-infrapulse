import { Anchor, Stack, Text, Title } from '@mantine/core';
import { Link, useParams } from 'react-router';
import { useService } from '../features/services/queries';
import { ServiceForm } from '../features/services/ServiceForm';
import { ErrorNotice, Loading } from '../shared/ui/Feedback';
function EditForm({ id }: { id: string }) {
  const service = useService(id);
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
    <>
      {service.isError && <ErrorNotice error={service.error} stale />}
      <ServiceForm key={service.data.id} service={service.data} />
    </>
  );
}
export default function ServiceFormPage() {
  const { id } = useParams();
  return (
    <Stack gap="xl">
      <Anchor component={Link} to="/services" size="sm">
        ← Voltar aos serviços
      </Anchor>
      <div>
        <Title order={1}>{id ? 'Editar serviço' : 'Cadastrar serviço'}</Title>
        <Text c="dimmed" mt={6}>
          Defina o destino e a política de monitoramento HTTP.
        </Text>
      </div>
      {id ? <EditForm id={id} /> : <ServiceForm />}
    </Stack>
  );
}
