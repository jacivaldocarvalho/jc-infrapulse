import { http, HttpResponse } from 'msw';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { server, service } from '../test/server';
import { renderPage } from '../test/render';
import ServicesPage from './ServicesPage';
describe('service inventory', () => {
  it('distinguishes global totals from visible states and fetches no off-page status', async () => {
    const requested: string[] = [];
    server.use(
      http.get('*/api/v1/services', ({ request }) => {
        const query = new URL(request.url).searchParams;
        return HttpResponse.json({
          items: [service],
          total:
            query.get('is_active') === 'true' ? 25 : query.get('is_active') === 'false' ? 6 : 31,
          limit: Number(query.get('limit')),
          offset: 0,
        });
      }),
      http.get('*/api/v1/services/:id/status', ({ params }) => {
        requested.push(String(params.id));
        return HttpResponse.json({
          service_id: params.id,
          last_status: 'UP',
          last_checked_at: '2026-10-01T12:00:00Z',
        });
      }),
    );
    renderPage(<ServicesPage dashboard />);
    await screen.findByText('✓ UP 1');
    expect(screen.getByText('31')).toBeInTheDocument();
    expect(screen.getByText(/Contagens parciais/)).toHaveTextContent('1 registros carregados');
    expect(requested).toEqual([service.id]);
    server.use(
      http.get('*/api/v1/services/:id/status', () => new HttpResponse(null, { status: 500 })),
    );
    await userEvent.click(screen.getByRole('button', { name: /Atualizar/ }));
    await screen.findByText('⚠ Erro de atualização · dados desatualizados');
    expect(screen.getByText('✓ UP 0')).toBeInTheDocument();
    expect(screen.getByText('⚠ Erro de atualização 1')).toBeInTheDocument();
    expect(screen.getByText('UP', { exact: true })).toBeInTheDocument();
  });
  it('offers the empty state', async () => {
    renderPage(<ServicesPage />);
    expect(await screen.findByText('Nenhum serviço encontrado')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Próxima' })).toBeDisabled();
  });
  it('offers recovery from request failures', async () => {
    server.use(http.get('*/api/v1/services', () => new HttpResponse(null, { status: 503 })));
    renderPage(<ServicesPage />);
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Erro ao carregar'));
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeInTheDocument();
  });
});
