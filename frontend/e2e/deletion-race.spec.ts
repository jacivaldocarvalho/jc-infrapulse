import { expect, test, type Page } from '@playwright/test';

// These tests write only to the database created by the disposable E2E runner.
test.beforeEach(() => {
  expect(process.env.INFRAPULSE_E2E_DISPOSABLE, 'Use a disposable E2E stack').toBe('1');
});

type Event = { event: string; at: number; path?: string; method?: string };
function observe(page: Page, id: string) {
  const timeline: Event[] = [];
  let committedAt: number | undefined;
  let committedIndex: number | undefined;
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith(`/api/v1/services/${id}`)) {
      timeline.push({
        event: 'request-start',
        at: Date.now(),
        path: new URL(request.url()).pathname,
        method: request.method(),
      });
    }
  });
  page.on('requestfailed', (request) => {
    if (new URL(request.url()).pathname.startsWith(`/api/v1/services/${id}`))
      timeline.push({
        event: 'request-canceled',
        at: Date.now(),
        path: new URL(request.url()).pathname,
      });
  });
  return {
    timeline,
    commit() {
      committedAt = Date.now();
      committedIndex = timeline.length;
      timeline.push({ event: 'DELETE-204-before-delivery', at: committedAt });
    },
    newReads() {
      // Sequence, rather than response time, distinguishes old in-flight reads.
      return committedIndex === undefined
        ? []
        : timeline
            .slice(committedIndex + 1)
            .filter((event) => event.event === 'request-start' && event.method === 'GET');
    },
  };
}

test('list deletion starts no new service reads during slow list revalidation', async ({
  page,
  request,
}, testInfo) => {
  const name = `INFRAPULSE-S05-TEST-RACE-LIST-${Date.now()}`;
  const created = await request.post('/api/v1/services', {
    data: { name, url: 'https://example.com/', is_active: false },
  });
  expect(created.status()).toBe(201);
  const { id } = await created.json();
  const audit = observe(page, id);
  let committed = false;
  let listWaiting = false;
  let releaseList!: () => void;
  const gate = new Promise<void>((resolve) => {
    releaseList = resolve;
  });
  await page.route(`**/api/v1/services/${id}`, async (route) => {
    if (route.request().method() !== 'DELETE') return route.continue();
    const response = await route.fetch();
    expect(response.status()).toBe(204);
    committed = true;
    audit.commit();
    await route.fulfill({ response });
  });
  await page.route('**/api/v1/services?*', async (route) => {
    if (committed) {
      listWaiting = true;
      await gate;
    }
    await route.continue();
  });
  try {
    // Twenty-four seeded services precede this newly created record.
    await page.goto('/services?page=2&limit=20');
    await expect(page.getByRole('link', { name, exact: true })).toBeVisible();
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: `Excluir ${name}`, exact: true }).click();
    await page.getByRole('button', { name: 'Excluir definitivamente', exact: true }).click();
    await expect.poll(() => listWaiting).toBe(true);
    await expect(page.getByRole('link', { name, exact: true })).toHaveCount(0);
    // Force another render/refetch action while the server still holds the list.
    await page.getByRole('checkbox', { name: 'Atualização a cada 30 s' }).uncheck();
    await page.waitForTimeout(150);
    expect(audit.newReads()).toEqual([]);
    await page.screenshot({
      path: testInfo.outputPath('slow-list-after-delete.png'),
      fullPage: true,
    });
    releaseList();
    await page.waitForLoadState('networkidle');
    expect(audit.newReads()).toEqual([]);
    expect((await request.get(`/api/v1/services/${id}`)).status()).toBe(404);
    console.log(
      'DELETE_RACE_EVIDENCE',
      JSON.stringify({
        scenario: 'list',
        newAutomaticReadsAfterSuccess: audit.newReads().length,
        timeline: audit.timeline,
      }),
    );
    // A deliberate fresh visit must still expose the ordinary backend 404.
    audit.timeline.push({ event: 'intentional-direct-visit', at: Date.now() });
    await page.goto(`/services/${id}`);
    await expect(page.getByRole('alert')).toContainText('Serviço não encontrado.');
  } finally {
    releaseList();
    const remaining = await request.get(`/api/v1/services/${id}`);
    if (remaining.status() === 200 && (await remaining.json()).name === name)
      await request.delete(`/api/v1/services/${id}`);
    await testInfo.attach('request-start-timeline', {
      body: JSON.stringify(audit.timeline, null, 2),
      contentType: 'application/json',
    });
  }
});

test('detail deletion cancels a pre-existing status read and navigates before slow list response', async ({
  page,
  request,
}, testInfo) => {
  const name = `INFRAPULSE-S05-TEST-RACE-DETAIL-${Date.now()}`;
  const created = await request.post('/api/v1/services', {
    data: { name, url: 'https://example.com/', is_active: false },
  });
  expect(created.status()).toBe(201);
  const { id } = await created.json();
  const audit = observe(page, id);
  let pendingStatus = false;
  let committed = false;
  let listWaiting = false;
  let releaseStatus!: () => void;
  let releaseList!: () => void;
  const statusGate = new Promise<void>((resolve) => {
    releaseStatus = resolve;
  });
  const listGate = new Promise<void>((resolve) => {
    releaseList = resolve;
  });
  await page.route(`**/api/v1/services/${id}/status`, async (route) => {
    const response = await route.fetch();
    pendingStatus = true;
    await statusGate;
    // The browser may already have aborted this pre-delete request.
    await route.fulfill({ response }).catch(() => {});
  });
  await page.route(`**/api/v1/services/${id}`, async (route) => {
    if (route.request().method() !== 'DELETE') return route.continue();
    const response = await route.fetch();
    expect(response.status()).toBe(204);
    committed = true;
    audit.commit();
    await route.fulfill({ response });
  });
  await page.route('**/api/v1/services?*', async (route) => {
    if (committed) {
      listWaiting = true;
      await listGate;
    }
    await route.continue();
  });
  try {
    await page.goto(`/services/${id}`);
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
    await expect.poll(() => pendingStatus).toBe(true);
    await page.getByRole('button', { name: `Excluir ${name}`, exact: true }).click();
    await page.getByRole('button', { name: 'Excluir definitivamente', exact: true }).click();
    await expect(page).toHaveURL(/\/services$/);
    await expect(page.getByRole('heading', { name: 'Serviços', exact: true })).toBeVisible();
    await expect.poll(() => listWaiting).toBe(true);
    expect(
      audit.timeline.some(
        (event) => event.event === 'request-canceled' && event.path?.endsWith('/status'),
      ),
    ).toBe(true);
    await page.waitForTimeout(150);
    expect(audit.newReads()).toEqual([]);
    await page.screenshot({
      path: testInfo.outputPath('detail-navigation-with-pending-read.png'),
      fullPage: true,
    });
    releaseStatus();
    releaseList();
    await page.waitForLoadState('networkidle');
    expect(audit.newReads()).toEqual([]);
    console.log(
      'DELETE_RACE_EVIDENCE',
      JSON.stringify({
        scenario: 'detail-pending-status',
        newAutomaticReadsAfterSuccess: audit.newReads().length,
        timeline: audit.timeline,
      }),
    );
    expect((await request.get(`/api/v1/services/${id}`)).status()).toBe(404);
  } finally {
    releaseStatus();
    releaseList();
    const remaining = await request.get(`/api/v1/services/${id}`);
    if (remaining.status() === 200 && (await remaining.json()).name === name)
      await request.delete(`/api/v1/services/${id}`);
    await testInfo.attach('request-start-timeline', {
      body: JSON.stringify(audit.timeline, null, 2),
      contentType: 'application/json',
    });
  }
});
