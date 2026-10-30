import type { Service, ServiceInput, ServicePatch } from '../../shared/api/client';
export type ServiceFormValues = {
  name: string;
  url: string;
  check_interval_seconds: number | string;
  timeout_seconds: number | string;
  is_active: boolean;
};
export const defaults: ServiceFormValues = {
  name: '',
  url: '',
  check_interval_seconds: 60,
  timeout_seconds: 5,
  is_active: true,
};
export function validateService(values: ServiceFormValues): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!values.name.trim() || values.name.trim().length > 120)
    errors.name = 'Informe um nome com 1 a 120 caracteres.';
  try {
    const url = new URL(values.url.trim());
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password)
      throw new Error();
  } catch {
    errors.url = 'Informe uma URL HTTP ou HTTPS sem credenciais.';
  }
  for (const [field, min] of [
    ['check_interval_seconds', 10],
    ['timeout_seconds', 1],
  ] as const) {
    const value = values[field];
    if (typeof value !== 'number' || !Number.isInteger(value) || value < min || value > 2147483647)
      errors[field] = `Informe um inteiro entre ${min} e 2147483647.`;
  }
  if (
    !errors.timeout_seconds &&
    !errors.check_interval_seconds &&
    Number(values.timeout_seconds) >= Number(values.check_interval_seconds)
  )
    errors.timeout_seconds = 'O timeout deve ser menor que o intervalo.';
  return errors;
}
export function servicePayload(values: ServiceFormValues): ServiceInput {
  return {
    name: values.name.trim(),
    url: values.url.trim(),
    check_interval_seconds: Number(values.check_interval_seconds),
    timeout_seconds: Number(values.timeout_seconds),
    is_active: values.is_active,
  };
}
export function servicePatch(original: Service, values: ServiceFormValues): ServicePatch {
  return Object.fromEntries(
    Object.entries(servicePayload(values)).filter(
      ([key, value]) => original[key as keyof Service] !== value,
    ),
  );
}
