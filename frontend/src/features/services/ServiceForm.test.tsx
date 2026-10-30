import { http, HttpResponse } from 'msw';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { server, service } from '../../test/server';
import { renderPage } from '../../test/render';
import { ServiceForm } from './ServiceForm';
import { DeleteService } from './DeleteService';
describe('service mutations', () => {
  it('shows backend business validation errors', async () => {
    server.use(
      http.post('*/api/v1/services', () =>
        HttpResponse.json({ detail: 'Destination rejected by security policy' }, { status: 422 }),
      ),
    );
    renderPage(<ServiceForm />);
    await userEvent.type(screen.getByLabelText(/Nome do serviço/), 'Internal API');
    await userEvent.type(screen.getByLabelText(/URL de verificação/), 'http://localhost/health');
    await userEvent.click(screen.getByRole('button', { name: 'Cadastrar serviço' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Destination rejected by security policy',
    );
  });
  it('requires explicit confirmation before deleting a service', async () => {
    let deleted = false;
    server.use(
      http.delete('*/api/v1/services/:id', () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderPage(<DeleteService service={service} />);
    await userEvent.click(screen.getByRole('button', { name: 'Excluir Payments API' }));
    expect(await screen.findByRole('dialog')).toHaveTextContent('todo o histórico');
    expect(deleted).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
    expect(deleted).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Excluir Payments API' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Excluir definitivamente' }));
    expect(deleted).toBe(true);
  });
});
