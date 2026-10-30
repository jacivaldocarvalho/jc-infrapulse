import type { CheckStatus } from '../api/client';
export function stateLabel(status: CheckStatus | null): 'UP' | 'DOWN' | 'Sem dados' {
  return status === null ? 'Sem dados' : status === 'UP' ? 'UP' : 'DOWN';
}
export function dateTime(value: string | number | null | undefined): string {
  return value
    ? new Intl.DateTimeFormat('pt-BR', {
        dateStyle: 'short',
        timeStyle: 'medium',
        timeZoneName: undefined,
      }).format(new Date(value))
    : '—';
}
export function number(value: number | null | undefined, unit = ''): string {
  return value == null
    ? '—'
    : `${new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 }).format(value)}${unit ? ` ${unit}` : ''}`;
}
export function pageNumber(raw: string | null, fallback: number, max = 1_000_000): number {
  const value = Number(raw);
  return raw !== null && Number.isInteger(value) && value >= 1 && value <= max ? value : fallback;
}
