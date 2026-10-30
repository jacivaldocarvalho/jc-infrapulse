import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
const payments = '00000000-0000-0000-0000-000000000001';
const catalog = '00000000-0000-0000-0000-000000000002';
test('real CRUD through Nginx, PostgreSQL and an actual worker check', async ({
  page,
  request,
}, testInfo) => {
  await page.goto('/services/new');
  await page.getByLabel('Nome do serviço').fill('Checkout E2E');
  await page.getByLabel('URL de verificação').fill('http://monitoring-target:8081/');
  await page.getByLabel('Intervalo entre checks (s)').fill('10');
  await page.getByLabel('Timeout (s)').fill('2');
  await page.getByRole('button', { name: 'Cadastrar serviço', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Checkout E2E', exact: true })).toBeVisible();
  const id = new URL(page.url()).pathname.split('/').at(-1)!;
  await expect
    .poll(
      async () => (await (await request.get(`/api/v1/services/${id}/status`)).json()).last_status,
      { timeout: 30_000 },
    )
    .toBe('UP');
  await page.getByRole('button', { name: '↻ Atualizar estado', exact: true }).click();
  await expect(page.getByText('UP', { exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: '↻ Atualizar período até agora' }).click();
  const metrics = await (await request.get(`/api/v1/services/${id}/metrics`)).json();
  expect(metrics.total_checks).toBeGreaterThan(0);
  expect(metrics.uptime_percentage).toBe(100);
  await expect(page.getByText('100 %', { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('real-worker-up.png'), fullPage: true });
  await page.getByRole('link', { name: 'Editar serviço', exact: true }).click();
  await page.getByLabel('Nome do serviço').fill('Checkout E2E atualizado');
  await page.getByLabel('Intervalo entre checks (s)').fill('20');
  await page.getByRole('switch', { name: 'Monitoramento ativo' }).uncheck();
  await page.getByRole('button', { name: 'Salvar alterações' }).click();
  await expect(
    page.getByRole('heading', { name: 'Checkout E2E atualizado', exact: true }),
  ).toBeVisible();
  const persisted = await (await request.get(`/api/v1/services/${id}`)).json();
  expect(persisted).toMatchObject({
    name: 'Checkout E2E atualizado',
    is_active: false,
    check_interval_seconds: 20,
    timeout_seconds: 2,
  });
  await page.reload();
  await expect(page.getByRole('heading', { name: persisted.name, exact: true })).toBeVisible();
  await page.getByRole('button', { name: `Excluir ${persisted.name}`, exact: true }).click();
  await page.getByRole('button', { name: 'Cancelar', exact: true }).click();
  expect((await request.get(`/api/v1/services/${id}`)).status()).toBe(200);
  await page.getByRole('button', { name: `Excluir ${persisted.name}`, exact: true }).click();
  await page.getByRole('button', { name: 'Excluir definitivamente', exact: true }).click();
  await expect(page).toHaveURL(/\/services$/);
  expect((await request.get(`/api/v1/services/${id}`)).status()).toBe(404);
  expect((await request.get(`/api/v1/services/${id}/checks`)).status()).toBe(404);
});
test('historical metrics, distinct p95, chart windows and pagination', async ({
  page,
  request,
}, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`/services/${payments}`);
  await expect(page.getByRole('heading', { name: 'Payments API', exact: true })).toBeVisible();
  const metrics = await (await request.get(`/api/v1/services/${payments}/metrics`)).json();
  expect(metrics).toMatchObject({
    total_checks: 25,
    up_checks: 20,
    down_checks: 5,
    uptime_percentage: 80,
    average_response_time_ms: 22,
  });
  expect(metrics.p95_response_time_ms).toBeCloseTo(32.8);
  await expect(page.getByText('80 %', { exact: true })).toBeVisible();
  await expect(page.getByText('32,8 ms', { exact: true })).toBeVisible();
  await expect(page.getByText('Ⅱ Inativo', { exact: true })).toBeVisible();
  const history = page.getByRole('region', { name: 'Histórico de verificações', exact: true });
  await expect(history.locator('tbody tr')).toHaveCount(20);
  await history.getByRole('button', { name: 'Próxima', exact: true }).click();
  await expect(history.locator('tbody tr')).toHaveCount(5);
  const seriesRequests: string[] = [];
  page.on('request', (req) => {
    if (req.url().includes('/metrics?')) seriesRequests.push(req.url());
  });
  await page.getByRole('button', { name: 'Carregar gráficos', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Histórico de confiabilidade' })).toBeVisible();
  await expect(page.getByRole('img', { name: /Gráfico de disponibilidade/ })).toBeVisible();
  await page.getByRole('button', { name: 'Ver dados dos gráficos em tabela' }).click();
  expect(seriesRequests).toHaveLength(12);
  await expect(page.getByText('32,8 ms', { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('historical-charts.png'), fullPage: true });
  expect(errors).toEqual([]);
});
test('empty periods remain unknown, including empty charts', async ({ page }) => {
  await page.goto(`/services/${catalog}`);
  await expect(page.getByText('Sem verificações neste período;', { exact: false })).toBeVisible();
  await expect(page.getByText('Nenhuma verificação no período', { exact: true })).toBeVisible();
  await expect(page.getByText('0 %', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Carregar gráficos' }).click();
  await expect(page.getByText('Sem dados para os gráficos')).toBeVisible();
  await page.goto(`/services/${catalog}?start=invalid&end=invalid`);
  await expect(page.getByRole('alert')).toContainText('Período inválido');
});
test('visible scope, filtering, pagination and stale update errors', async ({ page }, testInfo) => {
  await page.goto('/');
  await expect(page.getByText('Contagens parciais', { exact: false })).toContainText(
    '20 registros carregados',
  );
  await expect(page.getByText('24', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('× DOWN 1', { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('dashboard-light.png'), fullPage: true });
  await page.route(`**/api/v1/services/${payments}/status`, (route) =>
    route.fulfill({ status: 503, contentType: 'application/json', body: '{}' }),
  );
  await page.getByRole('button', { name: '↻ Atualizar', exact: true }).click();
  await expect(
    page.getByText('⚠ Erro de atualização · dados desatualizados', { exact: true }),
  ).toBeVisible();
  await expect(page.getByText('× DOWN 0', { exact: true })).toBeVisible();
  await expect(page.getByText('⚠ Erro de atualização 1', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Próxima', exact: true }).click();
  await expect(page.getByText('Contagens parciais', { exact: false })).toContainText(
    '4 registros carregados',
  );
  await page.getByRole('combobox', { name: 'Atividade', exact: true }).click();
  await page.getByRole('option', { name: 'Ativos', exact: true }).click();
  await expect(page.getByText('Nenhum serviço encontrado', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/activity=active/);
});
test('proxy allowlist and SSRF policy preserve internal boundaries', async ({ request }) => {
  for (const path of [
    '/metrics',
    '/ready',
    '/health',
    '/docs',
    '/openapi.json',
    '/prometheus',
    '/tempo',
    '/alertmanager',
    '/worker',
    '/api/v1/admin',
    '/api/v1/services/not-a-uuid/status',
  ]) {
    expect((await request.get(path)).status(), path).toBe(404);
  }
  expect((await request.put('/api/v1/services', { data: {} })).status()).toBe(403);
  const rejected = await request.post('/api/v1/services', {
    data: { name: 'Blocked metadata', url: 'http://169.254.169.254/' },
  });
  expect(rejected.status()).toBe(422);
  const response = await request.get('/services/new');
  expect(response.status()).toBe(200);
  expect(response.headers()['content-security-policy']).toContain("connect-src 'self'");
  expect(response.headers()['x-content-type-options']).toBe('nosniff');
});
test('desktop and mobile keyboard navigation, both themes and accessibility', async ({
  page,
}, testInfo) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Visão geral', exact: true })).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Pular para o conteúdo' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#main-content')).toBeFocused();
  expect(
    (await page.getByRole('heading', { name: 'Visão geral', exact: true }).boundingBox())?.y,
  ).toBeGreaterThanOrEqual(72);
  const light = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
    .analyze();
  expect(light.violations).toEqual([]);
  await page.getByRole('button', { name: 'Alternar tema' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-mantine-color-scheme', 'dark');
  await page.screenshot({ path: testInfo.outputPath('dashboard-dark.png'), fullPage: true });
  const dark = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(dark.violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Abrir navegação' }).click();
  await page.getByRole('link', { name: 'Serviços', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Serviços', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('dashboard-mobile.png'), fullPage: true });
  await page.goto('/services/new');
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Cadastrar serviço', exact: true })).toBeVisible();
  await page.getByLabel('Nome do serviço').focus();
  await page.keyboard.type('Keyboard API');
  await expect(page.getByLabel('Nome do serviço')).toHaveValue('Keyboard API');
});
