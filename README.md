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

## Backend — Sprint 01

Requisitos: Docker com Compose v2+; para desenvolvimento sem containers, Python 3.12+.
Execute os comandos abaixo na raiz do repositório.

### Execução com Docker Compose

```bash
cp backend/.env.example backend/.env
```

Edite `backend/.env` e preencha `POSTGRES_PASSWORD` com uma senha local gerada,
por exemplo com `openssl rand -hex 24`. Não versione esse arquivo.

```bash
docker compose --env-file backend/.env up --build -d --wait
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
```

Para iniciar a API fora do Docker, configure em `backend/.env` um PostgreSQL
acessível pelo host; o banco do Compose não é publicado. Depois, em `backend/`:

```bash
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
- Modelos, migrations Alembic, registro de serviços, métricas Prometheus e
  instrumentação OpenTelemetry ficam para sprints posteriores, quando houver
  casos de uso e esquema de dados. Nenhuma tabela é criada nesta sprint.
