# ADR 0002 — Telemetria operacional isolada por processo

Status: aceito para a Sprint 04, conforme arquitetura e ajustes aprovados.

## Contexto

API e worker independentes precisam de RED, saúde do scheduler e traces sem
introduzir labels por serviço, expor destinos ou alterar o transporte fixado da
Sprint 03. Exporter acessível não comprova saúde do event loop. Dependências de
telemetria não podem se tornar dependências de disponibilidade do produto.

## Decisão

Usar prometheus-client com registry e listener HTTP próprios para cada processo.
Listeners em threads são iniciados/encerrados pelo lifespan da API e pelo worker.
Não expor `/metrics` na porta funcional. Publicar somente API e Grafana em loopback;
colocar Prometheus/Collector/Tempo/Alertmanager em rede interna separada do banco.

Instrumentar explicitamente ASGI, scheduler, checker e operações de persistência
com a API/SDK OpenTelemetry. Não habilitar instrumentação automática de transporte
ou SQL. Registrar apenas templates de rota, métodos normalizados, status e nomes
fixos de operação. Não registrar mensagens de exceção, SQL, URLs, cabeçalhos ou
corpos. Não injetar tracing nos destinos. Recursos identificam papel, versão e
ambiente; não usam detectores automáticos de ambiente/processo.

Tratar traceparent externo como link de um trace local, sem baggage/tracestate ou
controle de sampling pelo cliente. Internamente usar propagação de contexto de
asyncio/to_thread. Emitir logs de acesso sanitizados com correlação trace/span e
suprimir mensagens brutas de acesso/clientes/exporters.

Separar métricas de resultado concluído, commit confirmado, último UP persistido,
último ciclo saudável e heartbeat do event loop. Preservar heartbeat em arquivo,
backpressure, concorrência, política SSRF e prazo de espera das tarefas no shutdown.

Exportar por OTLP HTTP com fila de 256 spans, lotes de até 64 e processador próprio
com drenagem limitada a 2s mais uma exportação em curso (timeout máximo 2s).
A versão instalada do SDK implementa force_flush sem aplicar o timeout recebido;
um processador explicitamente possuído evita depender desse comportamento para
o encerramento. Descartar backlog ao esgotar o prazo, encerrando a thread.

Collector aplica memory limiter antes de batch, fila/retry limitados e exporta ao
Tempo. Persistir Prometheus e Tempo em volumes, provisionar Grafana por arquivos
e encaminhar alertas para receiver local sem notificações externas.

## Consequências

Mantemos isolamento e cardinalidade limitada, ao custo de instrumentação manual
e responsabilidade pelo processador de exportação. Traces são best effort; fila
cheia, sampling, shutdown ou Collector indisponível podem causar perda. Nenhum
trace distribuído é enviado a serviços monitorados. Links externos permitem
correlação sem aceitar o contexto externo como pai de confiança.

Spans de banco delimitam operações/sessões sem capturar SQL individual. A contagem
de serviços ativos adiciona uma consulta por ciclo. Quantis Prometheus são
aproximações por buckets; não são o percentile_cont do histórico PostgreSQL.

Uma instância do worker e um processo por registry continuam sendo limites. A
stack local não é HA, não inclui retenção automática no PostgreSQL nem logs
centralizados. Alertmanager recebe/deduplica alertas, mas o receiver padrão não
notifica pessoas. Detalhes em [observability.md](../observability.md).
