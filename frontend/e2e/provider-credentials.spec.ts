import { expect, test, type Page } from '@playwright/test';

const origin = 'http://127.0.0.1:8765';

/**
 * U08 — browser E2E for provider credential lifecycle (save → rotate → delete)
 * against the REAL backend routes and the REAL UI.
 *
 * Assertions follow plan §2: no fabricated evidence, no cloud call, no secret
 * outside the one sanctioned place (the request body of
 * PUT /api/cloud-profiles/{id}/credential to our own loopback origin), and no
 * secret in localStorage or in any API response.
 *
 * The fake secrets ("sk-test-...") are never sent anywhere except the local
 * loopback server; the server stores them in the OS keyring (Windows Credential
 * Manager), which is the app's designed backend-only storage — not cloud.
 *
 * The profile itself has no DELETE route (API offers only credential DELETE and
 * PATCH); its credential is deleted at the end and the profile row lives in the
 * ephemeral per-run E2E database (frontend/e2e/.tmp-data, wiped on boot).
 */

const dumpWebStorage = (page: Page) =>
  page.evaluate(() => {
    const dump = (storage: Storage) => {
      const entries: [string, string][] = [];
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        if (key !== null) entries.push([key, storage.getItem(key) ?? '']);
      }
      return entries;
    };
    return {
      localStorage: dump(window.localStorage),
      sessionStorage: dump(window.sessionStorage),
    };
  });

test('U08: credential save, rotate and delete stay masked and leak nothing', async ({
  page,
  request,
}) => {
  // --- Provision a provider profile through the real API -------------------
  const bootstrapResponse = await request.get('/api/security/bootstrap');
  expect(bootstrapResponse.ok()).toBeTruthy();
  const bootstrap = (await bootstrapResponse.json()) as { csrfToken: string };
  const headers = { Origin: origin, 'X-CSRF-Token': bootstrap.csrfToken };

  const displayName = `E2E Provider ${Date.now()}`;
  const createResponse = await request.post('/api/cloud-profiles', {
    headers,
    data: {
      providerKind: 'TRANSLATOR',
      adapterName: 'qwen-mt',
      displayName,
    },
  });
  expect(createResponse.status()).toBe(201);
  const created = (await createResponse.json()) as {
    id: string;
    secretConfigured: boolean;
    adapterName: string;
  };
  expect(created.adapterName).toBe('qwen-mt');
  expect(created.secretConfigured).toBe(false);

  const firstSecret = `sk-test-e2e-${Date.now()}-first`;
  const rotatedSecret = `sk-test-e2e-${Date.now()}-rotated`;

  // --- Instrument every request/response of the page -----------------------
  const pageRequests: { url: string; method: string; body: string }[] = [];
  page.on('request', (req) => {
    const raw = req.postDataBuffer();
    pageRequests.push({
      url: req.url(),
      method: req.method(),
      body: raw ? Buffer.from(raw).toString('utf8') : '',
    });
  });
  const credentialResponses: { status: number; body: string }[] = [];
  const cloudProfileResponses: string[] = [];
  page.on('response', async (res) => {
    const url = res.url();
    if (!url.includes('/api/cloud-profiles')) return;
    let body = '';
    try {
      body = await res.text();
    } catch {
      body = '<unavailable>';
    }
    if (/\/credential$/.test(url)) credentialResponses.push({ status: res.status(), body });
    else cloudProfileResponses.push(body);
  });

  // --- Open the real Settings → Providers screen ---------------------------
  await page.goto('/settings/providers');
  await expect(page.getByRole('heading', { name: 'AI Providers' })).toBeVisible();
  const row = page.getByRole('row').filter({ hasText: displayName });
  await expect(row).toBeVisible();
  await expect(row.getByText('Chưa có')).toBeVisible();
  const secretInput = row.getByLabel(`Credential cho ${displayName}`);
  await expect(secretInput).toHaveAttribute('type', 'password');
  await expect(secretInput).toHaveValue('');

  // --- Save the first credential (real PUT through the real UI) ------------
  await secretInput.fill(firstSecret);
  await row.getByRole('button', { name: 'Lưu key' }).click();
  await expect(row.getByText('Đã cấu hình')).toBeVisible();
  // A save error would surface here (e.g. 422 INVALID_REQUEST) — never assert
  // success while the UI is reporting a failure.
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(secretInput).toHaveValue('');
  await expect(secretInput).toHaveAttribute('type', 'password');

  // No input anywhere on the page still holds the secret.
  const inputValuesAfterSave = await page
    .locator('input')
    .evaluateAll((elements) => elements.map((el) => (el as HTMLInputElement).value));
  for (const value of inputValuesAfterSave) {
    expect(value).not.toContain(firstSecret);
  }

  let storage = await dumpWebStorage(page);
  expect(JSON.stringify(storage.localStorage)).not.toContain(firstSecret);
  expect(JSON.stringify(storage.sessionStorage)).not.toContain(firstSecret);

  // --- Rotate: a different secret through the same UI ----------------------
  await secretInput.fill(rotatedSecret);
  await row.getByRole('button', { name: 'Lưu key' }).click();
  await expect(row.getByText('Đã cấu hình')).toBeVisible();
  await expect(secretInput).toHaveValue('');
  await expect(secretInput).toHaveAttribute('type', 'password');

  const inputValuesAfterRotate = await page
    .locator('input')
    .evaluateAll((elements) => elements.map((el) => (el as HTMLInputElement).value));
  for (const value of inputValuesAfterRotate) {
    expect(value).not.toContain(firstSecret);
    expect(value).not.toContain(rotatedSecret);
  }

  storage = await dumpWebStorage(page);
  const storageDump = JSON.stringify(storage.localStorage) + JSON.stringify(storage.sessionStorage);
  expect(storageDump).not.toContain(firstSecret);
  expect(storageDump).not.toContain(rotatedSecret);

  // --- The masked state comes from the real server payload -----------------
  const listResponse = await request.get('/api/cloud-profiles');
  expect(listResponse.ok()).toBeTruthy();
  const listPayload = (await listResponse.json()) as {
    profiles: { id: string; secretConfigured: boolean; config: Record<string, unknown> }[];
  };
  const stored = listPayload.profiles.find((profile) => profile.id === created.id);
  expect(stored?.secretConfigured).toBe(true);
  // The response shows state only — never the secret itself.
  const listDump = JSON.stringify(listPayload);
  expect(listDump).not.toContain(firstSecret);
  expect(listDump).not.toContain(rotatedSecret);

  // Both PUTs really reached the server and succeeded.
  expect(credentialResponses).toHaveLength(2);
  for (const response of credentialResponses) {
    expect(response.status).toBe(200);
    expect(response.body).not.toContain(firstSecret);
    expect(response.body).not.toContain(rotatedSecret);
  }

  // --- No secret leaves the page except to the sanctioned endpoint ---------
  for (const req of pageRequests) {
    const url = new URL(req.url);
    if (url.protocol === 'data:' || url.protocol === 'blob:' || url.protocol === 'about:') continue;
    // No request to a foreign origin at all.
    expect(url.origin, `request to unexpected origin: ${req.url}`).toBe(origin);

    const isStateChanging = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(req.method);
    const carriesSecret =
      req.body.includes(firstSecret) || req.body.includes(rotatedSecret);
    if (isStateChanging || carriesSecret) {
      // Secret-bearing or state-changing traffic goes only to the profile API,
      // without a query string and with nothing but the secret field in the body.
      expect(url.pathname, req.url).toMatch(/^\/api\/cloud-profiles\/[^/]+\/credential$/);
      expect(url.search, req.url).toBe('');
      if (carriesSecret) {
        const parsed = JSON.parse(req.body) as Record<string, unknown>;
        expect(Object.keys(parsed).sort()).toEqual(['secret']);
      }
    } else {
      expect(req.body, `unexpected body on ${req.url}`).toBe('');
    }
  }
  const credentialPuts = pageRequests.filter(
    (req) => req.method === 'PUT' && req.url.endsWith(`/api/cloud-profiles/${created.id}/credential`),
  );
  expect(credentialPuts).toHaveLength(2);
  expect(JSON.parse(credentialPuts[0].body)).toEqual({ secret: firstSecret });
  expect(JSON.parse(credentialPuts[1].body)).toEqual({ secret: rotatedSecret });

  // Every /api/cloud-profiles body the page received stays secret-free.
  for (const body of cloudProfileResponses) {
    expect(body).not.toContain(firstSecret);
    expect(body).not.toContain(rotatedSecret);
  }

  // --- Delete the credential through the real UI ---------------------------
  await row.getByRole('button', { name: 'Xoá key' }).click();
  await expect(row.getByText('Chưa có')).toBeVisible();

  const afterDelete = await request.get('/api/cloud-profiles');
  expect(afterDelete.ok()).toBeTruthy();
  const afterDeletePayload = (await afterDelete.json()) as {
    profiles: { id: string; secretConfigured: boolean }[];
  };
  const deleted = afterDeletePayload.profiles.find((profile) => profile.id === created.id);
  expect(deleted?.secretConfigured).toBe(false);
  expect(JSON.stringify(afterDeletePayload)).not.toContain(firstSecret);
  expect(JSON.stringify(afterDeletePayload)).not.toContain(rotatedSecret);

  storage = await dumpWebStorage(page);
  const finalStorageDump = JSON.stringify(storage.localStorage) + JSON.stringify(storage.sessionStorage);
  expect(finalStorageDump).not.toContain(firstSecret);
  expect(finalStorageDump).not.toContain(rotatedSecret);
});
