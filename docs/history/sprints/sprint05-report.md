# Sprint 05 — Frontend Dashboard

Data: 2026-10-30. Branch: `develop`. Base: `7456565`.

## Entrega

Implementada uma SPA React/TypeScript/Vite com React Router, TanStack Query,
Mantine Core/Form e Mantine Charts/Recharts. A interface oferece inventário,
CRUD de serviços, estado atual, métricas por período, histórico paginado,
gráficos sob demanda, filtros existentes na API, atualização periódica de dados
atuais, temas claro/escuro e layout responsivo.

O backend, suas dependências, migrações e contratos permaneceram intactos. Nenhum
endpoint foi criado. Não foram implementadas autenticação ou autorização.

## Execução incremental — Fase 2

| Incremento | Entrega | Verificação executada antes da entrega seguinte |
| --- | --- | --- |
| 1 | Fundação, layout, navegação e temas | TypeScript, lint e teste de componente |
| 2 | OpenAPI offline, cliente tipado e erros | Geração real, testes MSW de 204/422/cancelamento e semaphore |
| 3 | Listagem, filtros e escopos | Testes de totais globais, serviços visíveis, vazio e atualização com erro |
| 4 | Cadastro, edição e exclusão | Regras temporais, PATCH mínimo, 422 do backend e confirmação |
| 5 | Detalhe, métricas, histórico e gráficos | Build, regras de período/nulos e testes de integração HTTP simulada |
| 6 | Cache, polling e atualização | Testes de ausência de polling histórico e limite de quatro consultas reais no MSW |
| 7 | Docker/Nginx/Compose e documentação | Build multiestágio, Nginx real, PostgreSQL/worker, Playwright e regressões |

Os testes do primeiro incremento tinham um cenário; a suíte cresceu a 19 testes
em oito arquivos. As verificações foram repetidas quando mudanças ou falhas
justificaram uma nova execução.

## Contratos e semântica

O contrato foi conferido em rotas, schemas, repositórios e no OpenAPI 3.1 gerado
em memória pela aplicação atual (oito caminhos, onze operações).
`scripts/export-openapi.py` não inicia lifespan, servidor, conexões ou worker.
A geração TypeScript é reproduzível com dependências fixadas e também integra o
build Docker. Duas gerações consecutivas produziram o mesmo SHA256:

```text
266c0ab32f554ed5d591a351c1f5c8183bc345d01af50d0361b959f666ed9c34
```

Inventário global usa os totais de listagens; UP/DOWN/sem dados e erros são
contagens explicitamente limitadas à página. Atividade é independente do resultado.
Erros de consulta não contam como DOWN nem como ausência de histórico. Dados
anteriores são preservados com aviso de desatualização.

Disponibilidade é proporção de checks UP, sem ponderação temporal. Valores ausentes
não são convertidos em zero. O p95 do período vem da consulta integral, nunca de
percentis de janelas individuais. Durações incluem as falhas conforme o backend.
Datas são transmitidas em UTC e exibidas com indicação de horário local.

Métricas e gráficos compartilham limite global de quatro requisições simultâneas.
A fila e as requisições recebem cancelamento. Somente serviços visíveis recebem
consultas de estado. Janelas históricas ficam fixas em cache, sem polling/refetch
por foco ou reconexão; o usuário pode avançar explicitamente o período.

## Resultados executados — Fase 3

| Verificação | Resultado |
| --- | --- |
| Vitest + Testing Library + MSW | 19 testes aprovados em oito arquivos |
| Playwright Chromium contra Nginx/FastAPI/PostgreSQL/worker reais | 6 cenários aprovados |
| Axe WCAG 2 A/AA e 2.1 AA, dashboard claro e escuro | Nenhuma violação detectada nos cenários auditados |
| TypeScript `tsc --noEmit` | Sem erros, incluindo código E2E |
| ESLint | Sem erros ou avisos na execução final |
| Prettier | Todos os arquivos correspondentes formatados |
| Build Vite de produção | Aprovado, com páginas e gráficos sob demanda |
| pytest local | 184 aprovados, 13 omitidos por dependerem de integração |
| pytest em PostgreSQL 17 descartável | 195 aprovados, 2 omitidos por dependerem da stack de observabilidade |
| Ruff backend | Lint e formatação aprovados, 52 arquivos |
| mypy backend | Sem problemas em 52 arquivos |
| `pip check` | Nenhuma dependência quebrada |
| Ruff dos quatro scripts Python novos/alterados | Lint e formatação aprovados |
| Stack real de observabilidade | Cenário normal e cenário Collector indisponível aprovados |
| Compose principal e Compose E2E | Configuração validada sem utilizar ou imprimir senhas locais |
| Nginx no container | `nginx -t` aprovado |
| Portas/rede/usuário reais | Frontend em 127.0.0.1:8080, usuário 101, filesystem somente leitura, rede apenas HTTP |
| Busca direcionada no bundle | Ausência dos marcadores verificados de credenciais, telemetria e destino privado de teste |
| `git diff --check` | Sem erros |

O build local produziu aproximadamente 123 kB gzip para o chunk principal e
118 kB gzip para gráficos, carregados separadamente; CSS principal aproximadamente
36 kB gzip. Esses tamanhos não constituem medição de desempenho em produção.

### Evidências de navegador

1. Cadastro pela interface com destino HTTP controlado, persistência PostgreSQL,
   UP real do worker, edição, inativação, reload, cancelamento e confirmação de
   exclusão. GET do serviço e checks retornaram 404 após exclusão.
2. Histórico determinístico: 25 checks, 20 UP e 5 TIMEOUT, disponibilidade de 80%,
   média de 22 ms e p95 de 32,8 ms, conferidos na API e na interface. Paginação
   exibiu 20 e depois 5 resultados. Gráficos fizeram 12 consultas por janela.
3. Ausência de histórico: disponibilidade/latências ausentes, tabela vazia e
   gráficos sem dados. Período inválido apresentou erro recuperável.
4. Totais globais de 24 serviços separados dos 20/4 registros nas páginas.
   Erro 503 de atualização preservou o último dado e saiu das contagens de estado.
   Filtro de ativos funcionou pela API e reiniciou a paginação.
5. Proxy rejeitou diagnósticos, métricas internas, rotas desconhecidas e métodos
   não autorizados. Cadastro de metadata IP retornou 422 pela política SSRF real.
   Cabeçalhos CSP e nosniff foram verificados no HTML servido pelo Nginx.
6. Teclado, link para conteúdo, foco e título abaixo do cabeçalho fixo, navegação
   móvel a 390×844, ausência de overflow na página, temas e auditoria axe.

Capturas locais, ignoradas pelo Git:

- [Dashboard claro](../../../frontend/test-results/dashboard-visible-scope-fi-07b8a-ion-and-stale-update-errors/dashboard-light.png)
- [Dashboard escuro](../../../frontend/test-results/dashboard-desktop-and-mobi-6cf46-th-themes-and-accessibility/dashboard-dark.png)
- [Dashboard móvel](../../../frontend/test-results/dashboard-desktop-and-mobi-6cf46-th-themes-and-accessibility/dashboard-mobile.png)
- [Histórico e gráficos](../../../frontend/test-results/dashboard-historical-metri-d9a79-hart-windows-and-pagination/historical-charts.png)
- [Check UP real do worker](../../../frontend/test-results/dashboard-real-CRUD-throug-76d3a--and-an-actual-worker-check/real-worker-up.png)

Relatório Playwright: `frontend/playwright-report/index.html`.
Logs detalhados da sessão, fora do Git:

```text
/tmp/infrapulse-sprint05-e2e.log
/tmp/infrapulse-sprint05-postgres.log
/tmp/infrapulse-sprint05-observability.log
/tmp/infrapulse-sprint05-frontend-build.log
```

### Evidências operacionais

A stack E2E publicou somente `127.0.0.1:8080`. API, worker, destino controlado e
PostgreSQL não publicaram portas. O frontend tinha somente a rede `api_access`;
o banco tinha somente a rede de dados interna. O Compose principal foi validado
com bindings exclusivos de loopback para API:8000, Grafana:3000 e frontend:8080.
Não se alega execução do novo frontend no projeto de desenvolvimento.

A regressão operacional confirmou scrapes API/worker como UP, recuperação de traces
no Tempo, consultas dos dashboards Grafana e recebimento de alerta pelo Alertmanager.
Com o Collector parado, a API permaneceu pronta e o worker persistiu outro UP.
Todas as portas dessa stack operacional descartável estavam sem publicação.

Os ambientes descartáveis foram removidos, inclusive bancos em tmpfs. A API e o
PostgreSQL de desenvolvimento mantiveram seus IDs, respectivamente
`73b8b6ff3fd3` e `b1ed7690ecac`, sem reinicialização. O `.env` local não foi editado.

## Correções identificadas na validação

Foram corrigidos: aspas das expressões regulares no Nginx; compatibilidade do
cliente com a interceptação MSW; seletores assíncronos/acessíveis dos testes;
contraste dos textos e botões nos dois temas; posição do título ao usar o link
para conteúdo; timezone obrigatório e subintervalos não vazios em períodos curtos.
Os resultados acima referem-se às execuções aprovadas após as respectivas correções.

## Segurança, workflow e limites

AGENTS.md, hooks, configurações Entire/Brain, código e contratos do backend foram
preservados. O Brain foi consultado por brief/recall. Como seu índice estava um
commit atrás, decisões foram conferidas diretamente nos arquivos atuais. A falha
do grafo não motivou reindexação, instalação ou alteração de configuração.

Não houve commits, pushes, reescrita de histórico ou checkpoints manuais.

Limitações mantidas e documentadas:

- UP/DOWN globais, busca textual, ordenação configurável e filtro global de estado
  exigem aprovação de extensão do backend; a interface não promete esses recursos.
- Cada gráfico custa até 12 consultas; não há garantia de snapshot entre chamadas.
- Checks persistidos tardiamente podem alterar períodos encerrados. Cache histórico
  não significa imutabilidade do banco; recarregar obtém uma nova leitura.
- Paginação de checks respeita offset máximo 10000; reduza o período além desse limite.
- E2E foi executado em Chromium. Firefox/WebKit e avaliação humana completa de
  acessibilidade/leitor de tela não foram executados.
- Não há autenticação, publicação pública, HA, notificações ou alteração do worker.
- A integração de desenvolvimento não foi iniciada automaticamente; a stack local
  preexistente foi preservada. O `.env` existente não tinha senha Grafana configurada;
  validações usaram somente senhas efêmeras, sem modificar esse arquivo.

## Reprodução e Conventional Commit — Fase 4

Instruções completas em [frontend.md](../../operations/frontend.md) e decisões no
[ADR 0003](../../adr/0003-frontend-dashboard.md).

```bash
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend test
cd frontend
npx playwright install chromium
cd ..
python3 scripts/validate-dashboard.py
```

Sugestão, sem execução:

```text
feat(frontend): add service reliability dashboard
```

## Inventário de arquivos

### Modificados (5)

- `.gitignore`
- `README.md`
- `compose.observability.test.yaml`
- `compose.yaml`
- `scripts/verify-observability-ports.py`

### Criados (60)

- `.dockerignore`
- `compose.frontend.test.yaml`
- `docs/adr/0003-frontend-dashboard.md`
- `docs/frontend.md`
- `docs/sprint05-report.md`
- `frontend/.dockerignore`
- `frontend/.env.example`
- `frontend/.prettierignore`
- `frontend/.prettierrc.json`
- `frontend/Dockerfile`
- `frontend/e2e/dashboard.spec.ts`
- `frontend/eslint.config.js`
- `frontend/functional-proxy.conf`
- `frontend/index.html`
- `frontend/nginx.conf`
- `frontend/package-lock.json`
- `frontend/package.json`
- `frontend/playwright.config.ts`
- `frontend/public/favicon.svg`
- `frontend/scripts/generate-api.mjs`
- `frontend/src/app/Layout.test.tsx`
- `frontend/src/app/Layout.tsx`
- `frontend/src/app/providers.tsx`
- `frontend/src/app/query-client.ts`
- `frontend/src/app/router.tsx`
- `frontend/src/features/monitoring/Charts.tsx`
- `frontend/src/features/monitoring/PeriodFilter.tsx`
- `frontend/src/features/monitoring/queries.test.tsx`
- `frontend/src/features/monitoring/queries.ts`
- `frontend/src/features/monitoring/windows.test.ts`
- `frontend/src/features/monitoring/windows.ts`
- `frontend/src/features/services/DeleteService.tsx`
- `frontend/src/features/services/ServiceForm.test.tsx`
- `frontend/src/features/services/ServiceForm.tsx`
- `frontend/src/features/services/form.test.ts`
- `frontend/src/features/services/form.ts`
- `frontend/src/features/services/queries.ts`
- `frontend/src/main.tsx`
- `frontend/src/pages/ServiceDetailPage.tsx`
- `frontend/src/pages/ServiceFormPage.tsx`
- `frontend/src/pages/ServicesPage.test.tsx`
- `frontend/src/pages/ServicesPage.tsx`
- `frontend/src/shared/api/client.test.ts`
- `frontend/src/shared/api/client.ts`
- `frontend/src/shared/api/errors.ts`
- `frontend/src/shared/lib/limiter.test.ts`
- `frontend/src/shared/lib/limiter.ts`
- `frontend/src/shared/lib/presentation.ts`
- `frontend/src/shared/ui/Feedback.tsx`
- `frontend/src/shared/ui/StatusBadge.tsx`
- `frontend/src/styles.css`
- `frontend/src/test/render.tsx`
- `frontend/src/test/server.ts`
- `frontend/src/test/setup.ts`
- `frontend/tsconfig.json`
- `frontend/vite.config.ts`
- `frontend/vitest.config.ts`
- `scripts/export-openapi.py`
- `scripts/seed-dashboard-e2e.py`
- `scripts/validate-dashboard.py`

### Gerados e ignorados

`frontend/src/shared/api/generated.d.ts`, `frontend/dist/`, `frontend/node_modules/`,
`frontend/test-results/` e `frontend/playwright-report/`. Não fazem parte da entrega
versionável; são reproduzidos pelos comandos documentados.
