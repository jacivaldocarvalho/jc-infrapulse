import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { Providers } from './providers';
import { AppRouter } from './router';
describe('application shell', () => {
  it('offers navigation, keyboard skip link and a working theme toggle', async () => {
    render(
      <Providers>
        <AppRouter />
      </Providers>,
    );
    expect(await screen.findByRole('heading', { name: 'InfraPulse.' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Pular para o conteúdo' })).toHaveAttribute(
      'href',
      '#main-content',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Alternar tema' }));
    expect(document.documentElement).toHaveAttribute('data-mantine-color-scheme', 'dark');
  });
});
