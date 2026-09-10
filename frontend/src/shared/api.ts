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

  // JSON bodies reach this helper in two shapes: a plain object, or an
  // already-stringified payload (every call site passes JSON.stringify(...)).
  // Both need Content-Type: application/json - without it the browser sends
  // text/plain and FastAPI rejects the request with 422 INVALID_REQUEST.
  // FormData is left untouched so the browser can add the multipart boundary.
  if (body !== undefined && !(body instanceof FormData)) {
    if (typeof body !== 'string') {
      body = JSON.stringify(body);
    }
    // An explicit caller header wins (e.g. application/merge-patch+json).
    if (!headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
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

  // JSON bodies reach this helper in two shapes: a plain object, or an
  // already-stringified payload (every call site passes JSON.stringify(...)).
  // Both need Content-Type: application/json - without it the browser sends
  // text/plain and FastAPI rejects the request with 422 INVALID_REQUEST.
  // FormData is left untouched so the browser can add the multipart boundary.
  if (body !== undefined && !(body instanceof FormData)) {
    if (typeof body !== 'string') {
      body = JSON.stringify(body);
    }
    // An explicit caller header wins (e.g. application/merge-patch+json).
    if (!headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
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
