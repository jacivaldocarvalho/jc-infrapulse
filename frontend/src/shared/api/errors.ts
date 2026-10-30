export class ApiError extends Error {
  readonly status: number;
  readonly fields: Record<string, string>;
  constructor(status: number, body: unknown) {
    let message =
      status === 404
        ? 'Serviço não encontrado.'
        : status === 422
          ? 'Revise os dados informados.'
          : 'Não foi possível consultar a API. Tente novamente.';
    const fields: Record<string, string> = {};
    if (status === 422 && body && typeof body === 'object' && 'detail' in body) {
      const detail = body.detail;
      if (typeof detail === 'string') message = detail;
      if (Array.isArray(detail))
        for (const item of detail) {
          if (
            item &&
            typeof item === 'object' &&
            Array.isArray(item.loc) &&
            typeof item.msg === 'string'
          ) {
            const field = item.loc.at(-1);
            if (typeof field === 'string') fields[field] = item.msg;
          }
        }
    }
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.fields = fields;
  }
}
export function errorMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : 'Falha de conexão com a API. Verifique sua conexão e tente novamente.';
}
