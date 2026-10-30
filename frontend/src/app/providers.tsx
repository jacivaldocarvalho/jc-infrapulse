import { MantineProvider, createTheme } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { createQueryClient } from './query-client';
import type { PropsWithChildren } from 'react';

const theme = createTheme({
  primaryColor: 'teal',
  defaultRadius: 'md',
  fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
  headings: { fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif', fontWeight: '650' },
  primaryShade: { light: 9, dark: 9 },
  colors: {
    teal: [
      '#e6fcf5',
      '#c3fae8',
      '#96f2d7',
      '#63e6be',
      '#38d9a9',
      '#20c997',
      '#12b886',
      '#0ca678',
      '#087f5b',
      '#066649',
    ],
  },
  autoContrast: true,
});
const client = createQueryClient();
export function Providers({ children }: PropsWithChildren) {
  return (
    <QueryClientProvider client={client}>
      <MantineProvider
        theme={theme}
        defaultColorScheme="auto"
        cssVariablesResolver={() => ({
          variables: {},
          light: {
            '--mantine-color-dimmed': '#5b6570',
            '--mantine-color-placeholder': '#66717d',
            '--mantine-color-error': '#a51124',
            '--mantine-color-teal-text': '#066649',
            '--mantine-color-teal-light-color': '#066649',
            '--mantine-color-orange-text': '#9c3c00',
            '--mantine-color-orange-light-color': '#9c3c00',
            '--mantine-color-red-text': '#a51124',
          },
          dark: {
            '--mantine-color-dimmed': '#b0bac6',
            '--mantine-color-placeholder': '#a1acb9',
            '--mantine-color-error': '#ffa8a8',
            '--mantine-color-teal-text': '#63e6be',
            '--mantine-color-orange-text': '#ffc078',
            '--mantine-color-red-text': '#ffa8a8',
          },
        })}
      >
        {children}
      </MantineProvider>
    </QueryClientProvider>
  );
}
