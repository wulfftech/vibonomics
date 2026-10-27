import type { paths } from './schema';

/**
 * A small fetch wrapper typed from the generated OpenAPI schema: the path, its parameters, the
 * request body and the response are all checked against `schema.d.ts`, so a backend change that
 * breaks a call fails `tsc` after `npm run gen:api`.
 *
 * URLs are relative (`api/models`), so they resolve against `<base href>` and work under any URL base.
 */

type Method = 'get' | 'post' | 'put' | 'delete';

type Operation<P extends keyof paths, M extends Method> = NonNullable<paths[P][M]>;

/** The schema paths that support `M`. */
export type PathsFor<M extends Method> = {
  [P in keyof paths]: paths[P][M] extends { responses: unknown } ? P : never;
}[keyof paths];

type JsonOf<R> = R extends { content: { 'application/json': infer J } } ? J : void;

/** The JSON body of the operation's success response (`void` for 204). */
type SuccessBody<O> = O extends { responses: infer R }
  ? {
      [S in keyof R]: S extends 204 ? void : S extends 200 | 201 | 202 ? JsonOf<R[S]> : never;
    }[keyof R]
  : never;

type PathParams<O> = O extends { parameters: { path: infer P } } ? P : never;
type QueryParams<O> = O extends { parameters: { query?: infer Q } } ? Q : never;
/** The JSON request body; handles optional request bodies (FastAPI marks a defaulted model optional). */
type RequestBody<O> = O extends { requestBody?: infer RB }
  ? RB extends { content: { 'application/json': infer B } }
    ? B
    : never
  : never;

type IfPresent<T, Key extends string> = [T] extends [never] ? { [K in Key]?: undefined } : { [K in Key]: T };

type Options<O> = IfPresent<PathParams<O>, 'path'> &
  IfPresent<RequestBody<O>, 'body'> & {
    query?: [QueryParams<O>] extends [never] ? undefined : QueryParams<O>;
    signal?: AbortSignal;
  };

/** Options are required only when the operation has path parameters or a required body. */
type OptionsArg<O> = [PathParams<O>] extends [never]
  ? [RequestBody<O>] extends [never]
    ? [options?: Options<O>]
    : [options: Options<O>]
  : [options: Options<O>];

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

type UnauthorizedListener = () => void;
let onUnauthorized: UnauthorizedListener | null = null;

/** The shell registers this so any 401 drops the UI back to the login card. */
export function setUnauthorizedListener(listener: UnauthorizedListener | null): void {
  onUnauthorized = listener;
}

function errorMessage(payload: unknown, fallback: string): string {
  if (typeof payload === 'object' && payload !== null && 'detail' in payload) {
    const detail = payload.detail;
    if (typeof detail === 'string') {
      return detail;
    }
    if (Array.isArray(detail)) {
      const parts = detail
        .map((entry: unknown) => {
          if (typeof entry === 'object' && entry !== null && 'msg' in entry) {
            const { msg, loc } = entry as { msg: unknown; loc?: unknown };
            const where = Array.isArray(loc) ? loc.filter((part) => part !== 'body').join('.') : '';
            return where ? `${where}: ${String(msg)}` : String(msg);
          }
          return '';
        })
        .filter(Boolean);
      if (parts.length > 0) {
        return parts.join('; ');
      }
    }
  }
  return fallback;
}

function buildUrl(path: string, pathParams?: object, query?: object): string {
  let url = path.replace(/^\/+/, '');
  if (pathParams !== undefined) {
    for (const [key, value] of Object.entries(pathParams)) {
      url = url.replace(`{${key}}`, encodeURIComponent(String(value)));
    }
  }
  if (query !== undefined) {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null) {
        search.set(key, String(value));
      }
    }
    const qs = search.toString();
    if (qs) {
      url += `?${qs}`;
    }
  }
  return url;
}

async function send(
  method: Method,
  path: string,
  options: { path?: object; query?: object; body?: unknown; signal?: AbortSignal } = {},
): Promise<unknown> {
  const init: RequestInit = {
    method: method.toUpperCase(),
    headers: { Accept: 'application/json' },
    credentials: 'same-origin',
    signal: options.signal,
  };
  if (options.body !== undefined) {
    init.headers = { ...init.headers, 'Content-Type': 'application/json' };
    init.body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.path, options.query), init);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error;
    }
    throw new ApiError(0, 'Could not reach the Vibonomics server.');
  }

  if (response.status === 204) {
    return undefined;
  }

  let payload: unknown = undefined;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/api/auth/')) {
      onUnauthorized?.();
    }
    throw new ApiError(response.status, errorMessage(payload, `${response.status} ${response.statusText}`.trim()));
  }

  return payload;
}

function request<M extends Method>(method: M) {
  return <P extends PathsFor<M>>(
    path: P,
    ...[options]: OptionsArg<Operation<P, M>>
  ): Promise<SuccessBody<Operation<P, M>>> => send(method, path, options) as Promise<SuccessBody<Operation<P, M>>>;
}

export const api = {
  get: request('get'),
  post: request('post'),
  put: request('put'),
  delete: request('delete'),
};
