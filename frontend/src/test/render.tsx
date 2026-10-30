import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router';
import { createQueryClient } from '../app/query-client';
export function renderPage(children: ReactNode, entry = '/') {
  const client = createQueryClient();
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MantineProvider>
          <MemoryRouter initialEntries={[entry]}>{children}</MemoryRouter>
        </MantineProvider>
      </QueryClientProvider>,
    ),
  };
}
