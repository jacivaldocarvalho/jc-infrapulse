# InfraPulse

API Reliability & Observability Platform

Projeto de engenharia de software para monitoramento
de APIs, métricas de confiabilidade e incidentes.

## Tecnologias
- Python / FastAPI
- PostgreSQL
- Docker
- Prometheus / OpenTelemetry
- OpenAI Codex + Entire

## Desenvolvimento
Desenvolvimento assistido por IA com rastreabilidade
de sessões e decisões técnicas.

## Backend — Sprints 01 e 02

Requisitos: Docker com Compose v2+; para desenvolvimento sem containers, Python 3.12+.
Execute os comandos abaixo na raiz do repositório.

### Execução com Docker Compose

```bash
cp backend/.env.example backend/.env
```

Edite `backend/.env` e preencha `POSTGRES_PASSWORD` com uma senha local gerada,
por exemplo com `openssl rand -hex 24`. Não versione esse arquivo.

```bash
docker compose --env-file backend/.env build api
docker compose --env-file backend/.env up -d --wait db
docker compose --env-file backend/.env run --rm --no-deps api alembic upgrade head
docker compose --env-file backend/.env up -d --wait api
curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/api/v1
docker compose --env-file backend/.env ps
docker compose --env-file backend/.env logs api
```

A API fica disponível em `http://localhost:8000`; documentação em `/docs`.
O argumento `--env-file` é necessário para interpolar as variáveis do serviço `db`.
O Compose define `POSTGRES_HOST=db` para a API, independentemente do valor local.
O PostgreSQL não publica portas no host. Ambos os serviços usam uma rede interna;
a API também usa uma rede bridge para publicar HTTP somente em `127.0.0.1`.
Os dados ficam no volume nomeado `postgres_data`.

```bash
docker compose --env-file backend/.env down
```

Esse comando preserva os dados. A opção `down -v` apaga o volume e seus dados;
use apenas quando desejar reinicializar o banco local. Alterar a senha no `.env`
não altera a senha de um banco já inicializado no volume.

### Desenvolvimento e qualidade

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -c backend/requirements.lock -e 'backend[dev]'
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/python -m pip check
```

Para iniciar a API fora do Docker, configure em `backend/.env` um PostgreSQL
acessível pelo host; o banco do Compose não é publicado. Depois, em `backend/`:

```bash
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

### Configuração e decisões

- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER` e
  `POSTGRES_PASSWORD` configuram a conexão. A senha é obrigatória e não tem padrão.
- `SERVICE_NAME` identifica a API; `LOG_LEVEL` define o nível dos logs JSON.
- `DB_CONNECT_TIMEOUT` (segundos) e `DB_STATEMENT_TIMEOUT_MS` limitam as operações
  do readiness. Os valores padrão são 3 e 3000, respectivamente.
- `/health` indica que o processo responde e não acessa o banco.
- `/ready` executa `SELECT 1` com SQLAlchemy e psycopg: retorna
  `200 {"status":"ready"}` ou `503 {"status":"not_ready"}`. Os erros de banco
  não são incluídos na resposta nem nos logs de falha do readiness.
- `/api/v1` retorna nome e versão; a versão vem dos metadados do pacote.
- Rotas síncronas executam o driver síncrono no pool de threads do FastAPI.
  Sessões são fechadas por requisição; o engine é descartado no encerramento.
- A aplicação usa factory e injeção de sessão para testes sem PostgreSQL.
  A imagem roda com usuário sem privilégios e seu health check usa `/ready`.
- `pyproject.toml` registra as faixas de compatibilidade; `requirements.lock`
  registra as versões resolvidas, incluindo as ferramentas de desenvolvimento.
- A Sprint 02 adiciona o registro de serviços e migrações Alembic. Execução de
  health checks dos serviços, métricas Prometheus e instrumentação OpenTelemetry
  ficam para sprints posteriores.

## Service Registry API — Sprint 02

| Método e caminho | Resultado |
| --- | --- |
| `POST /api/v1/services` | Cria um serviço; HTTP 201 com o registro |
| `GET /api/v1/services` | Retorna `items`, `total`, `limit`, `offset` |
| `GET /api/v1/services/{id}` | Consulta por UUID; HTTP 200 |
| `PATCH /api/v1/services/{id}` | Atualização parcial; HTTP 200 |
| `DELETE /api/v1/services/{id}` | Exclusão definitiva; HTTP 204 sem corpo |

Exemplo de cadastro (a URL cadastrada não recebe requisições nesta sprint):

```bash
curl -X POST http://localhost:8000/api/v1/services \
  -H 'Content-Type: application/json' \
  -d '{"name":"Example API","url":"https://api.example.com/health"}'
curl 'http://localhost:8000/api/v1/services?limit=20&offset=0&is_active=true'
```

`name` é obrigatório, tem espaços nas extremidades removidos e deve conter de
1 a 120 caracteres. `url` é obrigatória e normalizada pelo Pydantic como HTTP/HTTPS.
Os padrões são `check_interval_seconds=60`, `timeout_seconds=5` e `is_active=true`.
Intervalo mínimo: 10 segundos; timeout mínimo: 1 segundo e sempre menor que o
intervalo. Inteiros são limitados ao máximo do INTEGER PostgreSQL (2147483647).
Campos desconhecidos, tipos inválidos, UUIDs inválidos e payloads inválidos
retornam 422; um UUID válido inexistente retorna 404.

No PATCH, campos omitidos são preservados, `null` é rejeitado e a regra temporal
é validada sobre o estado completo resultante. `{}` é permitido. Nomes e URLs
não são únicos: múltiplos registros podem representar políticas distintas.

A paginação usa `limit` de 1 a 100 (padrão 20) e `offset` não negativo (padrão 0).
O filtro opcional `is_active` aceita ambos os estados. A ordenação é crescente
por `created_at`, depois `id`; o total considera o filtro antes da paginação.
Como em paginação por offset, alterações concorrentes entre requisições podem
mudar a composição das páginas.

### Persistência e decisões arquiteturais

- Rotas cuidam de HTTP; schemas cuidam do contrato e validação; `service_registry`
  aplica regras de negócio e delimita transações; `service_repository` concentra
  consultas SQLAlchemy; o modelo define o mapeamento e constraints.
- Cada requisição recebe sua própria sessão. Escritas usam transações com
  commit ou rollback automático no encerramento do bloco; a resposta só é
  produzida após o commit. PATCH e DELETE bloqueiam a linha com `FOR UPDATE`
  no PostgreSQL para serializar alterações concorrentes no mesmo registro.
- UUIDs são gerados pela aplicação. `created_at` e `updated_at` são `TIMESTAMPTZ`,
  inicializados pelo banco e serializados em UTC (`Z`). `updated_at` é alterado
  pela camada de serviços somente quando os valores normalizados mudam; PATCH
  vazio ou sem mudanças preserva o valor. `created_at` permanece imutável.
  Não há trigger: escritas diretas fora da aplicação precisam manter essa política.
- Constraints do banco reforçam nome não vazio, tamanho e regras temporais.
  A política de URLs é aplicada na aplicação. Não existe `create_all()` no
  fluxo de produção; ele é usado apenas na fixture SQLite dos testes locais.
- Alembic usa a mesma configuração de ambiente da aplicação, sem credenciais
  no arquivo INI. Não há migração automática ao iniciar cada processo da API.

### Migrações

Antes de iniciar ou atualizar a API, execute `alembic upgrade head` uma única vez
no banco de destino. Em Docker, use a sequência documentada acima; localmente,
execute os comandos em `backend/`, com o ambiente virtual e `.env` configurados:

```bash
.venv/bin/alembic current
.venv/bin/alembic upgrade head
.venv/bin/alembic check
```

A revisão inicial é `0001`. Migrações ficam versionadas em `backend/alembic/versions/`.
Para futuras alterações, gere uma revisão com `alembic revision --autogenerate -m
"descricao"`, revise o SQL e versione o arquivo. `alembic downgrade base` remove
a tabela e seus dados; a reversibilidade é testada somente no banco descartável.
Em ambientes persistentes, planeje backup e revisão operacional antes de rollback.
O readiness atual testa conectividade com `SELECT 1`; ele não substitui a verificação
de versão do esquema durante o deploy.

### Política de URLs e SSRF

O cadastro rejeita credenciais embutidas, hosts sem domínio, `localhost` e seus
subdomínios, sufixos locais/reservados conhecidos e IPs privados, loopback,
link-local, compartilhados, reservados, multicast e não especificados. Endereços
IPv4 alternativos normalizados pelo parser também passam pela classificação;
IPv6 mapeado e mecanismos de transição são rejeitados conservadoramente.

Por padrão, destinos internos são proibidos. Um operador pode autorizar hosts
exatos via variável de ambiente JSON, por exemplo:

```dotenv
SERVICE_INTERNAL_HOSTS=["api.internal","10.10.0.5"]
```

Use hosts normalizados em minúsculas, sem ponto final e sem colchetes para IPv6.
A lista não aceita correspondência por prefixo ou wildcard. A exceção permite
somente os hosts nomeados e nunca permite credenciais embutidas. Ela não pode
ser definida pelo payload da API. O padrão seguro é `[]`.

Esta é uma proteção no cadastro, **não uma garantia sobre os IPs resolvidos por
DNS**. Não há resolução DNS nem requisições HTTP para as URLs cadastradas nesta
sprint. Um domínio aparentemente público ainda pode resolver para uma rede interna.

**Requisito obrigatório da Sprint 03:** antes de habilitar o executor HTTP,
resolver e validar todos os endereços do destino, vincular a conexão ao IP validado
e revalidar em cada nova conexão e em cada redirecionamento, incluindo mudanças
de host/protocolo. Impedir DNS rebinding, acesso a redes internas não autorizadas
e bypass por IPv6; aplicar a mesma política explícita de exceções internas.

### Testes com PostgreSQL 17 isolado

Os testes locais usam SQLite em memória e mocks para checks de processo. Os
testes específicos de PostgreSQL são omitidos nessa execução. Para executar
toda a suíte contra PostgreSQL real, a partir da raiz:

```bash
export INFRAPULSE_TEST_PASSWORD="$(openssl rand -hex 24)"
docker compose -f compose.test.yaml -p infrapulse-sprint02-test config --quiet
docker compose -f compose.test.yaml -p infrapulse-sprint02-test build tests
docker compose -f compose.test.yaml -p infrapulse-sprint02-test up \
  --abort-on-container-exit --exit-code-from tests
docker compose -f compose.test.yaml -p infrapulse-sprint02-test down
unset INFRAPULSE_TEST_PASSWORD
```

Execute `down` mesmo se os testes falharem. O arquivo Compose é independente do
ambiente de desenvolvimento: rede interna exclusiva, nenhuma porta publicada,
dados em tmpfs e senha efêmera fornecida pelo ambiente. Não usa `backend/.env`
nem o volume `postgres_data`. Não execute duas suítes simultâneas com o mesmo
nome de projeto Compose.

As fixtures exigem a flag `INFRAPULSE_RUN_INTEGRATION=1` e recusam outro destino
que não seja host `test-db`, banco e usuário `infrapulse_test`. Elas executam
upgrade, downgrade, novo upgrade e `alembic check` antes dos testes. As tabelas
são limpas entre testes de API apenas nesse banco descartável. A suíte verifica
CRUD, paginação, filtros, validações, SSRF, persistência entre sessões distintas,
timezone, defaults e constraints reais do PostgreSQL 17.

Valide o Compose principal sem imprimir sua configuração interpolada:

```bash
docker compose --env-file backend/.env config --quiet
```
