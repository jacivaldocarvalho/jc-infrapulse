import { Button, Stack, Text, Title } from '@mantine/core';
import { BrowserRouter, Link, Route, Routes } from 'react-router';
import { Layout } from './Layout';

import { lazy, Suspense } from 'react';
import { Loading } from '../shared/ui/Feedback';
const ServicesPage = lazy(() => import('../pages/ServicesPage'));
const ServiceFormPage = lazy(() => import('../pages/ServiceFormPage'));
const ServiceDetailPage = lazy(() => import('../pages/ServiceDetailPage'));
function NotFound() {
  return (
    <Stack>
      <Title>Página não encontrada</Title>
      <Text>O endereço solicitado não existe.</Text>
      <Button component={Link} to="/" w="fit-content">
        Voltar ao início
      </Button>
    </Stack>
  );
}
export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<ServicesPage dashboard />} />
          <Route path="services" element={<ServicesPage />} />
          <Route path="services/new" element={<ServiceFormPage />} />
          <Route path="services/:id/edit" element={<ServiceFormPage />} />
          <Route
            path="services/:id"
            element={
              <Suspense fallback={<Loading />}>
                <ServiceDetailPage />
              </Suspense>
            }
          />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
