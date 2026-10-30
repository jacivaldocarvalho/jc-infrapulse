# Frontend Dashboard — Sprint 05

## Desenvolvimento local

Requisitos: Node.js 24 (>=24.15), npm, Python 3.12+ com o backend instalado segundo
`backend/requirements.lock`. A API pode continuar no container existente; gerar os
tipos não exige iniciar API, worker ou banco e não lê credenciais do backend.

A partir da raiz:

```bash
npm --prefix frontend ci
npm --prefix frontend run generate:api
npm --prefix frontend run dev
```

Abra `http://127.0.0.1:5173`. O proxy de desenvolvimento encaminha `/api/v1` à API
em `http://127.0.0.1:8000`. Para outro destino local, defina `API_PROXY_TARGET` no
ambiente do processo Vite. Essa configuração é exclusiva do servidor Node e não
é exposta no bundle. O cliente do navegador usa sempre a própria origem.

O gerador usa `backend/.venv/bin/python`, quando disponível, ou `python3`. A variável
`PYTHON` pode selecionar explicitamente um interpretador com o backend instalado.
`OPENAPI_SCHEMA_FILE` permite consumir um contrato exportado pelo estágio Python
do Docker. Nunca substitua esse arquivo por um schema escrito manualmente.

## Contratos e qualidade

`scripts/export-openapi.py` importa a aplicação atual e chama `app.openapi()`, sem
lifespan ou sessão de banco. `frontend/scripts/generate-api.mjs` usa
`openapi-typescript` e grava `src/shared/api/generated.d.ts`, ignorado pelo Git.
Não existe cópia manual de DTOs do backend. As funções de formulário representam
estado da interface e são convertidas aos payloads gerados antes do envio.

```bash
npm --prefix frontend run generate:api
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend test
npm --prefix frontend run build
```

O build sempre regenera os tipos antes da checagem TypeScript. As dependências
resolvidas ficam em `frontend/package-lock.json`; a instalação reproduzível usa
`npm ci`. Vitest executa regras e testes de componentes com Testing Library e MSW.

## Semântica da interface

- Inventário global: total, ativos e inativos, obtidos de `total` em três consultas
  de listagem com `limit=1`. As chamadas não constituem um snapshot transacional.
- UP/DOWN/sem dados: contagens exclusivas dos serviços da página visível. Serviços
  com consulta pendente ou falha de atualização são identificados separadamente.
- UP corresponde apenas ao enum `UP`. Todos os demais resultados técnicos são
  apresentados como DOWN, preservando a causa original no histórico/detalhe.
- Inativo é uma propriedade do cadastro, independente do último resultado.
- Erros de consulta não fabricam resultados nem removem o último dado recebido;
  a interface informa que esse dado está desatualizado.
- `/status` informa o último check geral. Métricas e histórico têm uma janela
  explícita `[start, end)`, com no máximo 90 dias. Datas são enviadas em UTC e
  apresentadas no horário local do navegador, indicado nos rótulos.
- Disponibilidade representa proporção de checks UP, sem ponderação temporal.
  Sem checks, disponibilidade e durações são ausentes, apresentadas como `—`.
- Média e p95 incluem todas as durações não nulas, inclusive checks com falha.
  O p95 do período vem diretamente de `/metrics` para a janela completa.
- Gráficos consultam 12 subperíodos contíguos, com no máximo quatro requisições
  de métricas simultâneas em toda a aplicação. Percentis nunca são combinados.
  As lacunas dos gráficos não são preenchidas com zero. Há tabela equivalente.

## Atualização e cache

Listagem e inventário global atualizam a cada 30 segundos. Estados são consultados
somente para os serviços visíveis; serviços inativos não têm polling de estado.
Há opção para pausar a atualização. A biblioteca pausa polling em abas ocultas e
as requisições recebem o AbortSignal da consulta. A fila compartilhada remove
trabalho cancelado antes de acessar a rede.

Métricas, histórico e gráficos usam janelas fixas, sem polling ou refetch por foco
/reconexão. O botão “Atualizar período até agora” avança explicitamente a janela.
O histórico fica em cache enquanto retido (cinco minutos após deixar de ser usado).
Persistências atrasadas podem alterar uma janela encerrada: a visão é uma consulta
armazenada em cache, não uma garantia de imutabilidade do banco. Uma nova janela
ou recarga do navegador obtém os dados atuais. Mutações invalidam os caches de
serviços e monitoramento relacionados; exclusão remove caches do serviço excluído.

A paginação usa offset. Checks novos podem deslocar resultados entre requisições;
a janela fixa reduz deslocamentos causados pela passagem do tempo. O limite de
`offset=10000` da API é respeitado; para dados além dele, reduza o período.

## Container e segurança

O Dockerfile usa estágios Python para exportar o OpenAPI, Node para gerar os tipos
e compilar a SPA, e Nginx sem privilégios para servir o resultado. Python, Node,
credenciais e variáveis de proxy não são necessários na imagem final.

O Compose principal adiciona somente `127.0.0.1:8080:8080` ao frontend. Ele participa
apenas de `api_access`, sem rede do PostgreSQL ou telemetria. O filesystem é somente
leitura, com `/tmp` efêmero e `no-new-privileges`. O endpoint `/healthz` pertence ao
Nginx e verifica o servidor estático; não substitui o readiness do FastAPI.

O proxy permite exclusivamente a coleção de serviços, recurso por UUID e leituras
de status/checks/metrics. Não encaminha `/health`, `/ready`, `/docs`, OpenAPI,
listeners de métricas ou serviços operacionais. Métodos também são limitados por
rota. As páginas da SPA usam fallback de `index.html`, permitindo navegação direta
e reload. Cabeçalhos CSP, anti-frame, nosniff e referrer-policy são definidos pelo
Nginx; logs JSON não incluem URL, query, corpo ou cabeçalhos.

Após configurar as senhas locais conforme o README e aplicar as migrações:

```bash
docker compose --env-file backend/.env build frontend
docker compose --env-file backend/.env up -d --wait frontend
```

Essa operação pode iniciar dependências; durante a implementação da sprint ela
não é executada no projeto de desenvolvimento existente. Não há autenticação na
SPA ou na API nesta sprint; a publicação permanece restrita ao loopback.

## E2E isolado

```bash
cd frontend
npx playwright install chromium
cd ..
python3 scripts/validate-dashboard.py
```

O script cria um projeto Compose com nome exclusivo, senhas efêmeras em memória,
PostgreSQL em tmpfs, migrações reais, API, worker, destino HTTP controlado e Nginx.
A porta 8080 precisa estar livre; o script recusa prosseguir se estiver ocupada.
Nenhum `.env` do desenvolvimento é utilizado. Ao terminar, inclusive em falhas,
apenas os recursos do projeto descartável são removidos, e os IDs dos containers
de desenvolvimento são comparados com o estado inicial.

O seed exige a flag de E2E e o destino exato `test-db/infrapulse_test`; cria 24
serviços inativos e 25 checks determinísticos. Separadamente, o Playwright cria um
serviço pela interface e comprova UP persistido pelo worker contra o HTTP real.
A suíte cobre CRUD, métricas, histórico, gráficos, filtros, atualização com erro,
proxy restrito, SSRF, reload, teclado, telas móveis e axe nos temas claro/escuro.
A auditoria automatizada não substitui avaliação humana completa de acessibilidade.

Screenshots e relatórios ficam em `frontend/test-results/` e
`frontend/playwright-report/`, ambos ignorados pelo Git. O HTML pode ser aberto com
`npx playwright show-report frontend/playwright-report` após a execução.

## Limitações

Não há busca textual, ordenação configurável, filtro global por estado ou resumo
global UP/DOWN na API. A interface não simula esses recursos sobre uma única página.
Gráficos custam 12 consultas por combinação de serviço/período; uma extensão futura
para agregação em lote precisará de aprovação de contrato. Não há autenticação,
WebSocket, notificações externas nem consulta direta ao Prometheus pelo navegador.
