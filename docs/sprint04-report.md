# Sprint 04 — Observability & Telemetry

Data de validação: 2026-10-30. Branch: `develop`.

## Resultado

Implementadas métricas RED da API, métricas operacionais do worker/scheduler,
listeners internos com lifecycle próprio, heartbeat de event loop, tracing
OpenTelemetry explícito e logs JSON correlacionados/sanitizados. Adicionados
Collector, Prometheus, Tempo, Grafana e Alertmanager ao Compose com configuração
versionada. O worker permanece único e o transporte SSRF-safe não foi alterado.

O timestamp aprovado foi implementado como
`infrapulse_last_up_check_timestamp_seconds`, separado de
`infrapulse_scheduler_last_healthy_cycle_timestamp_seconds`. Resultados concluídos,
resultados persistidos, descartes e erros internos têm métricas distintas.

## Validações executadas

Comandos executados na raiz, exceto os indicados em `backend/`:

| Comando | Resultado final observado |
|---|---|
| `cd backend && .venv/bin/pytest -q` | 184 passed, 13 skipped, 10.29s |
| Suíte `compose.test.yaml` PostgreSQL 17, via script abaixo | 195 passed, 2 skipped, 13.22s |
| `.venv/bin/ruff check .` em backend (também verificado o script Python) | Aprovado |
| `.venv/bin/ruff format --check .` em backend | 52 arquivos formatados |
| `.venv/bin/mypy` em backend | Sem erros em 52 arquivos |
| `.venv/bin/python -m pip check` em backend | No broken requirements found |
| `docker compose --env-file /tmp/infrapulse-sprint04.env config --quiet` | Aprovado; nenhum segredo impresso |
| Config dos Composes de teste | Aprovado pelo script |
| `bash -n scripts/validate-observability.sh` | Aprovado |
| `git diff --check` | Aprovado |
| `git diff --exit-code -- AGENTS.md .entire .codex` | Sem alterações nesses caminhos |

Os 13 skips locais correspondem aos 11 casos PostgreSQL e aos dois casos que
exigem a stack de observabilidade. Na suíte PostgreSQL, somente os dois casos da
stack são omitidos; são executados separadamente com suas condições reais.

A execução final de `bash scripts/validate-observability.sh` usou projetos
`infrapulse-observability-137868` e `infrapulse-observability-137868-pg`:

- `promtool check config /etc/prometheus/prometheus.yaml`: aprovado.
- `promtool check rules /etc/prometheus/alerts.yaml`: 11 regras aprovadas.
- `promtool test rules /tests/rules.test.yaml`: aprovado, nove cenários.
- Collector `validate --config=/etc/otelcol/config.yaml`: aprovado.
- `amtool check-config /etc/alertmanager/config.yaml`: aprovado.
- Migrações e regressões completas em PostgreSQL 17: aprovadas.
- Teste da stack: 1 passed, 1 skipped em 51.88s (outage ainda não ativado).
- Teste com Collector parado: 1 passed, 1 deselected em 1.06s.

As versões utilizadas foram Prometheus 3.5.0, Alertmanager 0.28.1, Collector
contrib 0.128.0, Tempo 2.8.2, Grafana 12.1.0 e SDK OpenTelemetry Python 1.45.2.
O PostgreSQL da suíte valida efetivamente major version 17.

## Evidências de scraping e PromQL

Targets retornados pelo Prometheus após startup real:

```text
infrapulse-api    up    http://api:9000/metrics
infrapulse-worker up    http://worker:9001/metrics
```

Valores observados na consulta instantânea de integração:

```text
sum(http_server_requests_total{job="infrapulse-api"}) > 0                              2
infrapulse_checks_total{job="infrapulse-worker",status="UP"} > 0                       1
infrapulse_check_results_persisted_total{job="infrapulse-worker",status="UP"} > 0       1
infrapulse_last_up_check_timestamp_seconds{job="infrapulse-worker"} > 0                1793330920.1478071
infrapulse_scheduler_last_healthy_cycle_timestamp_seconds{job="infrapulse-worker"} > 0 1793330921.1450098
```

O serviço de teste foi cadastrado, verificado com HTTP real e consultado pela API
histórica; seu último estado foi UP. O cadastro foi excluído após a prova.

## Evidências de tracing, Grafana e alertas

Pesquisa TraceQL por resource.service.name encontrou os dois processos; consultas
individuais ao Tempo responderam HTTP 200:

```text
infrapulse-api    54ac1a6d2b081bc4761b7e4678b169
infrapulse-worker cc502397c32d4e9a6a7f10a033278dc3
```

Não existe caminho de exportação direto do backend ao Tempo: o envio é feito ao
Collector e o pipeline encaminha ao Tempo. A consulta individual também verificou
a ausência do hostname do destino monitorado no trace retornado.

Datasources Prometheus e Tempo foram encontrados pelo UID configurado. Prometheus
respondeu ao healthcheck do datasource; um trace real foi consultado por meio do
proxy do datasource Tempo. O endpoint backend genérico de healthcheck do plugin
Tempo retorna `plugin.notImplemented` nessa versão, então não foi tratado como
prova de indisponibilidade.

Os dashboards `InfraPulse API`, `InfraPulse Worker` e `InfraPulse Reliability`
foram recuperados pela API Grafana. Todas as expressões dos painéis foram
executadas com sucesso por meio do proxy Prometheus do Grafana. Não foi realizada
inspeção visual em navegador; a validação foi de provisionamento e consultas reais.

O alerta `InfraPulseIntegrationProbe`, definido apenas no ambiente de teste,
foi avaliado pelo Prometheus e recebido com estado `active` na API Alertmanager.
O receiver local não envia notificações externas e nenhuma integração foi criada.

## Falhas, shutdown e segurança

Com o Collector realmente parado, `/health` e `/ready` responderam 200 e um novo
serviço controlado recebeu UP persistido pelo worker. O endpoint de métricas
continuou disponível. Em seguida, o worker encerrou por SIGTERM; foram observados
`scheduler_stopped` e `worker_stopped`, este último às 03:29:21.236857 UTC.

Testes automatizados cobrem:

- Contagem única RED, templates, 404 desconhecido, HTTP 500 e histogramas.
- Todos os estados do checker, inclusive timeout real de DNS controlado.
- Counters de conclusão/commit/descarte e timestamps com semântica separada.
- Falha de seleção, checker, persistência e heartbeat em arquivo.
- Concorrência, saturação, decremento em finally e cancelamento.
- Inicialização/encerramento dos listeners e falha de bind com limpeza.
- Scrape incompleto durante shutdown sem thread remanescente.
- Fila limitada, force_flush com prazo e encerramento da thread exportadora.
- Collector indisponível, tanto unitariamente quanto na stack real.
- Atributos permitidos, ausência de detalhes sensíveis e correlação trace/span.
- Links externos sem baggage/tracestate ou controle remoto de sampling.
- Readiness sem spans de requisição ou de banco.
- Regressões existentes de SSRF/DNS rebinding, IP fixado, Host/SNI, TLS e proxies.
- Ausência de traceparent, tracestate e baggage nas requisições HTTP monitoradas.

A inspeção de runtime retornou `published=[]` para todos os nove serviços do
ambiente de integração. PostgreSQL estava exclusivamente na rede `*_internal`.
API/worker e Prometheus compartilhavam `*_telemetry`, comprovando conectividade
real de scrape sem publicação de portas.

O Compose principal foi validado separadamente: somente
`127.0.0.1:8000` (API) e `127.0.0.1:3000` (Grafana) possuem bindings declarados;
9000, 9001, 4318, 3200, 9090, 9093 e 5432 não são publicados. A stack de
desenvolvimento já existente na porta 8000 foi preservada; a nova stack foi
inicializada integralmente sob projeto isolado com portas de host removidas.
Portanto, a verificação de runtime refere-se à stack isolada e a do perfil
principal refere-se à configuração, sem alegar reinício do ambiente existente.

## Limitações e operação

- Uma instância do worker e um processo por registry; não há HA/multiprocess.
- Traces são amostrados e best effort, com perda possível por fila cheia,
  indisponibilidade ou esgotamento do prazo de shutdown.
- Spans de banco delimitam operações/sessões; não capturam SQL individual.
- Métricas são agregadas, não substituem histórico por serviço no PostgreSQL.
- Cancelamento de threads de banco não é possível: commits podem concluir após
  cancelamento sem confirmação no counter, como documentado na arquitetura.
- Collector/Tempo têm health endpoints consultados pela integração, sem probe
  Docker embutido nas imagens escolhidas.
- Tempo local tem retenção assíncrona, sem teto rígido de disco; PostgreSQL
  mantém a ausência de retenção automática da Sprint 03.
- Não há receiver externo, armazenamento central de logs ou teste visual de UI.

Projetos descartáveis e seus volumes são removidos pelo script ao terminar.
Logs detalhados da execução permanecem em
`/tmp/infrapulse-sprint04-final-validation.log`, fora do Git; os trace_ids acima
são evidências da execução descartável e não estarão disponíveis após a limpeza.
A documentação de operação e o script permitem reproduzir a prova completa.

## Documentação e commit sugerido

Produzidos `docs/observability.md`, este relatório e
`docs/adr/0002-observability-architecture.md`. README, configuração de exemplo e
referência operacional da Sprint 03 foram atualizados.

Sugestão: `feat(observability): add operational metrics tracing and monitoring stack`

Nenhum commit, push ou checkpoint manual foi executado. AGENTS.md, Entire e hooks
foram preservados. A relação de arquivos está abaixo.

## Arquivos modificados

- `README.md`
- `backend/.env.example`
- `backend/app/core/config.py`
- `backend/app/core/logging.py`
- `backend/app/db/session.py`
- `backend/app/main.py`
- `backend/app/monitoring/scheduler.py`
- `backend/app/worker.py`
- `backend/pyproject.toml`
- `backend/requirements.lock`
- `backend/tests/test_scheduler.py`
- `backend/tests/test_transport.py`
- `compose.yaml`
- `docs/monitoring.md`

## Arquivos criados

- `backend/app/observability/__init__.py`
- `backend/app/observability/http.py`
- `backend/app/observability/metrics.py`
- `backend/app/observability/processor.py`
- `backend/app/observability/tracing.py`
- `backend/tests/test_observability.py`
- `backend/tests/test_observability_stack.py`
- `compose.observability.test.yaml`
- `docs/adr/0002-observability-architecture.md`
- `docs/observability.md`
- `docs/sprint04-report.md`
- `observability/alertmanager/config.yaml`
- `observability/collector/config.yaml`
- `observability/grafana/dashboards/api.json`
- `observability/grafana/dashboards/reliability.json`
- `observability/grafana/dashboards/worker.json`
- `observability/grafana/provisioning/dashboards/dashboards.yaml`
- `observability/grafana/provisioning/datasources/sources.yaml`
- `observability/prometheus/alerts.yaml`
- `observability/prometheus/prometheus.yaml`
- `observability/tempo/config.yaml`
- `observability/tests/alerts.yaml`
- `observability/tests/prometheus.yaml`
- `observability/tests/rules.test.yaml`
- `scripts/validate-observability.sh`
- `scripts/verify-observability-ports.py`
