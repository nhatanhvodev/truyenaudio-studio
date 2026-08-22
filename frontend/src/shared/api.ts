let csrfToken: string | null = null;
let bootstrapPromise: Promise<string> | null = null;

type RequestOptions = Omit<RequestInit, 'body' | 'headers'> & {
  body?: BodyInit | object;
  headers?: HeadersInit;
};

export async function apiJson<T>(url: string, options: RequestOptions = {}): Promise<T> {
  return request<T>(url, options, false);
}

export async function apiForm<T>(url: string, form: FormData, options: RequestOptions = {}): Promise<T> {
  return request<T>(url, { ...options, method: options.method ?? 'POST', body: form }, false);
}

export async function apiBlob(url: string, options: RequestOptions = {}): Promise<Blob> {
  const method = (options.method ?? 'GET').toUpperCase();
  const stateChanging = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method);
  const headers = new Headers(options.headers);
  let body = options.body;

  if (body !== undefined && !(body instanceof FormData) && typeof body !== 'string') {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(body);
  }
  if (stateChanging) {
    headers.set('X-CSRF-Token', await bootstrapCsrf());
  }

  const response = await fetch(url, {
    ...options,
    method,
    headers,
    body: body as BodyInit | undefined,
    cache: method === 'GET' ? 'no-store' : options.cache,
  });
  if (!response.ok) {
    const payload = await parsePayload(response);
    throw new Error(String((payload as { detail?: unknown })?.detail ?? 'REQUEST_FAILED'));
  }
  return response.blob();
}

async function request<T>(url: string, options: RequestOptions, retried: boolean): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase();
  const stateChanging = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method);
  const headers = new Headers(options.headers);
  let body = options.body;

  if (body !== undefined && !(body instanceof FormData) && typeof body !== 'string') {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(body);
  }
  if (stateChanging) {
    headers.set('X-CSRF-Token', await bootstrapCsrf());
  }

  const response = await fetch(url, {
    ...options,
    method,
    headers,
    body: body as BodyInit | undefined,
    cache: method === 'GET' ? 'no-store' : options.cache,
  });
  if (response.status === 403 && stateChanging && !retried) {
    csrfToken = null;
    bootstrapPromise = null;
    return request<T>(url, options, true);
  }

  const payload = await parsePayload(response);
  if (!response.ok) {
    throw new Error(String((payload as { detail?: unknown })?.detail ?? 'REQUEST_FAILED'));
  }
  return payload as T;
}

async function bootstrapCsrf(): Promise<string> {
  if (csrfToken) {
    return csrfToken;
  }
  bootstrapPromise ??= fetch('/api/security/bootstrap', { cache: 'no-store' })
    .then(async (response) => {
      const payload = (await response.json()) as { csrfToken?: string };
      if (!response.ok || !payload.csrfToken) {
        throw new Error('CSRF_BOOTSTRAP_FAILED');
      }
      csrfToken = payload.csrfToken;
      return payload.csrfToken;
    })
    .finally(() => {
      bootstrapPromise = null;
    });
  return bootstrapPromise;
}

async function parsePayload(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return {};
  }
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}
