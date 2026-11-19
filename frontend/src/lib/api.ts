import { z } from 'zod';

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function request<T>(
  path: string,
  schema?: z.ZodType<T>,
  options: RequestInit = {},
): Promise<T> {
  if (!path.startsWith('/') || path.startsWith('//'))
    throw new Error('Only this workspace can receive application requests.');
  let response: Response;
  try {
    response = await fetch(path, {
      ...options,
      credentials: 'same-origin',
      headers: { Accept: 'application/json', ...options.headers },
    });
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error;
    throw new ApiError(
      'Cannot reach your workspace. Check that Reel Relay is running, then retry.',
      0,
    );
  }
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new ApiError(
      'The server returned an unreadable response. Retry or check the service logs.',
      response.status,
    );
  }
  if (!response.ok) {
    const obj = data as {
      error?: string;
      detail?: string | { msg: string; loc?: (string | number)[] }[];
    };
    const detail = Array.isArray(obj?.detail)
      ? obj.detail
          .map((d) => {
            const field = d.loc?.filter((v) => v !== 'body').join('.');
            return field ? `${field}: ${d.msg}` : d.msg;
          })
          .join('; ')
      : obj?.detail;
    throw new ApiError(
      (obj?.error && detail ? `${obj.error}: ${detail}` : obj?.error || detail) ||
        'The request could not be completed.',
      response.status,
    );
  }
  if (!schema) return data as T;
  const result = schema.safeParse(data);
  if (!result.success)
    throw new ApiError(
      'The server and interface versions do not match. Refresh after updating Reel Relay.',
      502,
    );
  return result.data;
}

export function mutate<T = { ok: boolean; message?: string }>(
  path: string,
  body: unknown,
  csrf?: string,
) {
  return request<T>(path, undefined, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-Relay-Token': csrf } : {}) },
    body: JSON.stringify(body),
  });
}
export function message(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong. Please retry.';
}
export function safeExternalUrl(value: string | undefined | null) {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' ? url.href : undefined;
  } catch {
    return undefined;
  }
}
