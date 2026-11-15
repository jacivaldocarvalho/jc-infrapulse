# Observability — Sprint 04

## Arquitetura e fronteiras

API e worker possuem registries Prometheus independentes, listeners internos e
providers OpenTelemetry próprios. Prometheus coleta `api:9000/metrics` e
`worker:9001/metrics` a cada 15s. Cada processo exporta spans por OTLP HTTP para
`otel-collector:4318/v1/traces`; o Collector aplica memory limiter e batch e
encaminha por OTLP gRPC a `tempo:4317`. Grafana consulta Prometheus e Tempo.
Prometheus envia alertas ao Alertmanager. Não há integração externa de notificação.

A API continua sem scheduler. O worker continua limitado a uma instância.
Nenhuma migração foi adicionada. Histórico por serviço continua no PostgreSQL
pelas APIs das Sprints 02–03; não é reconstruído a partir do Prometheus.

## Operação local

Na raiz do repositório, copie `backend/.env.example` para `backend/.env` caso
esse arquivo ainda não exista. Configure senhas distintas, geradas localmente,
em `POSTGRES_PASSWORD` e `GRAFANA_ADMIN_PASSWORD`. Não versione o `.env`.

```bash
docker compose --env-file backend/.env config --quiet
docker compose --env-file backend/.env build api worker
docker compose --env-file backend/.env up -d --wait db
docker compose --env-file backend/.env run --rm --no-deps api alembic upgrade head
docker compose --env-file backend/.env up -d --wait
python3 scripts/verify-observability-ports.py jc-infrapulse --allow-local-interfaces
```

O último comando supõe o nome padrão de projeto `jc-infrapulse`. Caso use `-p`,
substitua o argumento pelo nome escolhido. Grafana: `http://localhost:3000`,
usuário `GRAFANA_ADMIN_USER` (padrão `admin`) e a senha configurada. Alterar a senha
no ambiente não redefine automaticamente um administrador já persistido.

Compose habilita métricas/traces e define bind interno `0.0.0.0`. Fora do Compose,
ambos estão desabilitados por padrão. Para desenvolvimento Python, configure
explicitamente `METRICS_ENABLED=true`, `TELEMETRY_ENABLED=true` e um Collector
acessível. O bind local padrão é `127.0.0.1`; nunca exponha interfaces operacionais
em redes não confiáveis. `/metrics` não é uma rota da API funcional na porta 8000.

```bash
docker compose --env-file backend/.env logs -f api worker otel-collector
# Preserva volumes e dados:
docker compose --env-file backend/.env down
```

## Redes, portas e recursos

| Serviço | Porta interna | Publicação padrão | Redes |
|---|---|---|---|
| API | 8000 e métricas 9000 | Somente `127.0.0.1:8000` | internal, api_access, telemetry |
| Worker | métricas 9001 | Nenhuma | internal, monitoring_access, telemetry |
| PostgreSQL | 5432 | Nenhuma | internal |
| Collector | OTLP HTTP 4318; health 13133 | Nenhuma | telemetry |
| Tempo | HTTP 3200; OTLP gRPC 4317 | Nenhuma | telemetry |
| Prometheus | 9090 | Nenhuma | telemetry |
| Alertmanager | 9093 | Nenhuma | telemetry |
| Grafana | 3000 | Somente `127.0.0.1:3000` | telemetry, grafana_access |

`internal` e `telemetry` são redes Docker internas. `monitoring_access` mantém o
egress existente do checker. PostgreSQL não participa de `telemetry`.
Portas declaradas em metadados `EXPOSE` das imagens não são portas publicadas.
O script de inspeção verifica bindings reais e a rede do banco sem ler ambientes.

Collector tem limite de 256 MiB (memory limiter em 192 MiB, spike 32 MiB);
Tempo e Prometheus, 512 MiB; Grafana, 384 MiB; Alertmanager, 128 MiB.
Os serviços de observabilidade também possuem limites de CPU no Compose.
Readiness de Prometheus, Alertmanager e Grafana é verificado por healthcheck.
Collector fornece `/` em 13133; Tempo, `/ready` em 3200. As imagens escolhidas
não fornecem uma ferramenta HTTP genérica para healthchecks Docker desses dois;
a integração consulta seus endpoints pela rede interna.

## Configuração do backend

| Variável | Padrão local | Semântica |
|---|---|---|
| METRICS_ENABLED | false | Inicializa listener do processo |
| METRICS_HOST | 127.0.0.1 | Bind; Compose força 0.0.0.0 |
| API_METRICS_PORT | 9000 | Porta API; 0 permite porta efêmera em testes |
| WORKER_METRICS_PORT | 9001 | Porta worker; 0 permite porta efêmera em testes |
| TELEMETRY_ENABLED | false | Habilita exporter OTLP |
| TELEMETRY_ENDPOINT | http://otel-collector:4318/v1/traces | Endpoint de traces, incluindo caminho |
| TELEMETRY_SAMPLE_RATIO | 0.1 | Probabilidade local, intervalo 0–1 |
| TELEMETRY_TIMEOUT_SECONDS | 1 | Prazo por exportação, máximo 2s |
| DEPLOYMENT_ENVIRONMENT | development | Atributo de ambiente nos resources |

Portas diferentes exigem atualizar também os targets do Prometheus. Serviço,
versão e ambiente são resources de traces. `service.name` é `infrapulse-api`
ou `infrapulse-worker`; versão vem dos metadados do pacote. Ambos os atributos
`deployment.environment` e `deployment.environment.name` são fornecidos para
compatibilidade. Resources não são enriquecidos automaticamente com ambiente,
comandos do processo, credenciais ou destinos.

## Catálogo de métricas

| Métrica | Tipo / unidade | Labels | Significado |
|---|---|---|---|
| http_server_requests_total | counter / requests | method, route, status_code | Uma observação por requisição funcional |
| http_server_request_duration_seconds | histogram / s | method, route, status_code | Tempo monotônico até conclusão/erro ASGI |
| infrapulse_checks_total | counter / checks | status | Checker retornou um resultado, antes de persistir |
| infrapulse_check_duration_seconds | histogram / s | status | `response_time_ms / 1000`, inclui DNS e falhas |
| infrapulse_checks_in_flight | gauge / tarefas | nenhuma | Capacidade ocupada, incluindo banco e persistência |
| infrapulse_worker_capacity | gauge / tarefas | nenhuma | Limite de concorrência configurado |
| infrapulse_scheduler_cycles_total | counter / ciclos | outcome | success, saturated, error, cancelled |
| infrapulse_scheduler_errors_total | counter / erros | stage | selection, target, checker, persist, heartbeat |
| infrapulse_scheduler_cycle_duration_seconds | histogram / s | nenhuma | Duração monotônica do ciclo, inclusive falhas |
| infrapulse_scheduler_last_healthy_cycle_timestamp_seconds | gauge / epoch UTC s | nenhuma | Último ciclo sem falha interna; inclui saturação e heartbeat em arquivo |
| infrapulse_last_up_check_timestamp_seconds | gauge / epoch UTC s | nenhuma | Instante em que este worker confirmou commit de UP |
| infrapulse_check_results_persisted_total | counter / resultados | status | Commit confirmado pelo store |
| infrapulse_check_results_discarded_total | counter / resultados | nenhuma | Serviço excluído antes de persistir |
| infrapulse_check_tasks_cancelled_total | counter / tarefas | nenhuma | Cancelamentos reconhecidos pela execução |
| infrapulse_active_services | gauge / serviços | nenhuma | Contagem amostrada a cada ciclo; -1 antes da primeira leitura |
| infrapulse_event_loop_last_tick_timestamp_seconds | gauge / epoch UTC s | nenhuma | Atualizado a cada segundo pelo event loop |
| infrapulse_process_start_timestamp_seconds | gauge / epoch UTC s | nenhuma | Inicialização da telemetria do processo |

`status` tem somente os sete estados existentes: UP, DOWN, TIMEOUT, DNS_ERROR,
TLS_ERROR, CONNECTION_ERROR, HTTP_ERROR. Labels `job` e `instance` são adicionados
pelo Prometheus, não identificam o serviço monitorado. Histogramas geram `le`,
`_bucket`, `_sum`, `_count`; o cliente também gera timestamps `_created` para
counters/histogramas. Não há labels de URL, IP, UUID, cliente ou erro textual.

Os registries compartilham um catálogo; consulte métricas de worker com
`job="infrapulse-worker"` e RED com `job="infrapulse-api"`. Séries sem labels
iniciam em zero, timestamps sem evento permanecem zero. Reinícios zeram métricas
locais; use `rate`/`increase` para counters. Não há consulta ao histórico inteiro
para reconstruí-las. Somente `UP` representa sucesso de destino.

Buckets de todos os histogramas, em segundos:
`0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, +Inf`.

A rota RED é o template resolvido, como `/api/v1/services/{service_id}`.
Rotas desconhecidas usam `unmatched`; métodos não padronizados usam `OTHER`.
`/health`, `/ready` e `/metrics` são excluídos de RED, spans e access logs
sanitizados. Isso inclui query strings dessas rotas. Métricas REST por serviço
continuam instrumentadas como qualquer rota funcional, usando seu template.
Falhas antes de responder contam como 500; para streaming já iniciado, conta o
status efetivamente enviado. Exceções não geram resultados fictícios DOWN.

## Saúde, lifecycle e privacidade

`up=1` demonstra que o exporter respondeu, não que o event loop, banco e destinos
estão saudáveis. Compare idade do tick, idade do ciclo saudável e `/ready`.
O heartbeat em arquivo da Sprint 03 foi preservado: é atualizado por ciclos
bem-sucedidos e continua sendo a fonte do healthcheck local do worker.

Listeners executam em threads próprias. SIGTERM/SIGINT interrompem agendamentos;
o scheduler mantém seu prazo `MONITORING_SHUTDOWN_SECONDS` para tarefas e cancela
as restantes. Gauges são decrementados em `finally`. Depois são encerrados o
pulse asyncio, listener e exporter; o engine é descartado e o arquivo removido.
O Compose mantém 75s de stop grace. O exporter adiciona até aproximadamente 4s
(2s para drenagem e até 2s para a exportação em curso), além da parada do listener.

A fila de traces contém no máximo 256 spans, com lotes de até 64. Quando cheia,
descarta os mais antigos. O processador próprio garante prazo de shutdown:
a implementação instalada do SDK não aplica o timeout solicitado em force_flush.
Exportações ficam fora do event loop, com timeout de rede limitado. Spans não
entregues dentro do prazo são descartados; Collector indisponível não bloqueia
startup, readiness ou checks. Não existe garantia de entrega de traces.

A instrumentação é explícita, sem monkey patch de HTTP/SQLAlchemy. Spans incluem
apenas nomes fixos, templates de rota, método, status HTTP ou `check.status`.
Operações de banco são spans de sessão/seleção/leitura/persistência, sem SQL,
parâmetros, DSN ou mensagens de exceção. Não há captura de headers, corpos, URL,
query string, IP resolvido ou nomes dos serviços monitorados.

O contexto externo aceita apenas `traceparent`, como link de um trace local novo.
`tracestate` e baggage são descartados. A decisão de sampling do cliente não
controla o sampling local. O contexto interno propaga via asyncio e `to_thread`.
Nenhum header de telemetria é injetado nos destinos monitorados.

Logs JSON incluem trace_id/span_id quando há contexto válido, inclusive spans
não amostrados. Logs de acesso brutos do Uvicorn são suprimidos; a aplicação emite
method/route template/status sanitizados. Logs internos dos exporters e clientes
HTTP são suprimidos para evitar mensagens de exceção com destinos ou segredos.
Logs do checker preservam run_id/service_id, sem colocá-los em labels ou spans.

## Dashboards e consultas

Grafana provisiona datasources `prometheus` e `tempo` e dashboards:

- InfraPulse API: rate, razão 5xx, p50/p95/p99, rotas, exporter e idade do loop.
- InfraPulse Worker: throughput, estados, p95, tarefas, erros e saúde do ciclo/loop.
- InfraPulse Reliability: proporção agregada UP por amostras, resultados
  persistidos, falhas técnicas e quantidade de serviços ativos.

Sem tráfego, razões e quantis podem ficar sem dados; não devem ser lidos como zero.
Reliability não oferece disponibilidade histórica por serviço. Essa informação
continua nas APIs REST respaldadas pelo PostgreSQL. Traces podem ser pesquisados
no Explore, datasource Tempo, por `{resource.service.name = "infrapulse-worker"}`.
Os trace_ids dos logs permitem investigação pontual, caso o trace tenha sido amostrado.

No Grafana 12.1, o plugin Tempo não implementa o endpoint backend genérico de
healthcheck. O teste valida uma consulta real de trace pelo proxy do datasource,
assim como as consultas PromQL de todos os painéis pelo proxy Prometheus.

Exemplos PromQL:

```promql
sum(rate(http_server_requests_total{job="infrapulse-api"}[5m]))
histogram_quantile(0.95, sum by(le)(rate(http_server_request_duration_seconds_bucket{job="infrapulse-api"}[5m])))
sum by(status)(rate(infrapulse_checks_total{job="infrapulse-worker"}[5m]))
time() - infrapulse_scheduler_last_healthy_cycle_timestamp_seconds{job="infrapulse-worker"}
```

## Alertas e configuração de notificações

Thresholds são configuráveis editando `observability/prometheus/alerts.yaml` e
recriando/reiniciando Prometheus após validar com promtool. Não há interpolação
implícita de variáveis de ambiente em PromQL. Regras iniciais:

| Alerta | Condição | for |
|---|---|---|
| API/WorkerDown | target `up=0` | 2m |
| API/WorkerNoData | série up ausente por 5m | 2m adicionais |
| EventLoopStalled | tick >60s, processo >120s, scrape UP | 2m |
| SchedulerStalled | ciclo >120s, processo >120s, scrape UP | 2m |
| NoSuccessfulChecks | serviços ativos, checks recentes, sem UP persistido por 10m, processo >10m | 2m |
| APIHighErrorRate | 5xx >5%, pelo menos 100 requests na janela de 5m | 5m |
| APIHighLatency | p95 >1s, pelo menos 100 requests em 5m | 5m |
| SchedulerErrors | erro interno observado nos últimos 5m | 2m |
| WorkerSaturated | ocupação >90% | 10m |

NoSuccessfulChecks não significa necessariamente defeito do worker: destinos
podem estar DOWN ou a persistência pode falhar. Correlacione estados, erros internos
e resultados persistidos. Sem serviços ativos ou sem checks recentes, não dispara.
Sem tráfego suficiente, não há alerta de latência ou erro HTTP.
A ausência de séries é separada da falha de scrape. Sem o próprio Prometheus não
há avaliação de regras; monitoramento externo da stack é uma evolução futura.

Alertmanager agrupa por alertname/job, espera 30s, reagrupa a cada 5m e repete
notificações a cada 4h. Severidades critical/warning têm rotas explícitas; critical
inibe warning do mesmo job/instance. O receiver `local-only` não envia mensagens:
alertas permanecem consultáveis na API/UI interna. Isso não é entrega externa.
Para um receiver real, configure uma integração em arquivo/secret local não
versionado, use suporte a arquivos de credenciais do receiver e monte o segredo
como somente leitura. Valide com `amtool check-config` e só então aplique a
configuração autorizada. Não coloque tokens no YAML versionado.

## Retenção, limites e troubleshooting

- Prometheus: 15 dias e limite de TSDB de 2 GB, o que ocorrer primeiro; volume persistente.
- Tempo: retenção de blocos 72h, WAL e blocos em volume local. Compactação é assíncrona;
  retenção não é um limite rígido de disco. Acompanhe o espaço livre.
- Grafana e Alertmanager: volumes para usuários/configuração local e silêncios.
- PostgreSQL: política de retenção da Sprint 03 permanece sem limpeza automática.
- Não há Loki nem armazenamento central de logs; use logs JSON do runtime.
- Uma réplica do worker e um processo por registry; multiprocess Uvicorn requer
  arquitetura de agregação adicional. Esta stack não tem alta disponibilidade.
- Threads de banco/resolver não são interrompidas por cancelamento asyncio, como
  na Sprint 03. Um commit pode terminar após o cancelamento sem ser reconhecido
  pelo counter de persistência. Métricas são operacionais, sem garantia exactly-once.

Se scrape falhar, confira bind `METRICS_HOST`, portas do target, rede telemetry e
logs de startup. Se `up=1` mas loop/ciclo estão antigos, investigue travamento ou
falha de banco. Se não houver traces, confirme sampling, endpoint com `/v1/traces`,
Collector em 13133, Tempo em `/ready` e logs dos dois componentes. A fila é volátil;
reiniciar o Collector não recupera spans já descartados pelo backend.
Se Grafana não autenticar após alterar senha, verifique o volume existente.
Não publique interfaces administrativas para contornar problemas de conectividade.

## Validação reproduzível

```bash
bash scripts/validate-observability.sh
cd backend
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/python -m pip check
```

O script cria um projeto exclusivo, credenciais efêmeras e PostgreSQL 17 em tmpfs;
valida config/rules e cenários com promtool, Collector e Alertmanager; executa a
stack, consulta métricas/traces/dashboards/alertas, verifica correlação com logs,
bindings reais, Collector indisponível, recuperação e shutdown. A suíte PostgreSQL
completa permanece no validador separado da D1. Limpa somente seu projeto e
volumes ao terminar. Não reutiliza PostgreSQL de desenvolvimento.

A rede é alocada pelo Docker, sem subnet fixa. O validador autoriza exclusivamente
o hostname `monitoring-target`, seu IP real `/32` e a porta 8080. Essa exceção existe
só no override de testes. Não lê `.env` local e não publica portas. Os dois testes
são selecionados nominalmente, sem skips; o teste normal é repetido após a
recuperação do Collector. Relatórios JUnit ficam em `OBSERVABILITY_RESULTS_DIR`
ou em um diretório temporário informado pelo script. Execute sequencialmente com
o Playwright quando compartilhar o mesmo daemon Docker.

Referências: [Collector](https://opentelemetry.io/docs/collector/configuration/),
[regras Prometheus](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/),
[provisionamento Grafana](https://grafana.com/docs/grafana/latest/administration/provisioning/)
e [API Tempo](https://grafana.com/docs/tempo/latest/api_docs/).
