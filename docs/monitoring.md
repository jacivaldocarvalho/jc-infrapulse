# HTTP Monitoring — Sprint 03

## Execução e configuração

A API e o monitoramento são processos independentes. A API não inicia tarefas
periódicas no lifespan. Execute as migrações uma vez antes de iniciar ambos:

```bash
docker compose --env-file backend/.env build api worker
docker compose --env-file backend/.env up -d --wait db
docker compose --env-file backend/.env run --rm --no-deps api alembic upgrade head
docker compose --env-file backend/.env up -d --wait api worker
docker compose --env-file backend/.env logs -f worker
```

Localmente, em `backend/`, use `.venv/bin/python -m app.worker`. Para encerrar,
envie SIGTERM/SIGINT ou execute `docker compose --env-file backend/.env stop worker`.
Não execute mais de uma instância do worker, inclusive durante deploys.

| Variável | Padrão | Efeito |
| --- | --- | --- |
| `MONITORING_CONCURRENCY` | `10` | Máximo de verificações em andamento; intervalo 1–100 |
| `MONITORING_POLL_SECONDS` | `5` | Ciclo de seleção; intervalo 1–60 segundos |
| `MONITORING_CONNECT_TIMEOUT` | `3` | Limite de conexão TCP e de handshake TLS, por etapa |
| `MONITORING_RESPONSE_MAX_BYTES` | `1048576` | Máximo de bytes de corpo transferidos por resposta |
| `MONITORING_SHUTDOWN_SECONDS` | `15` | Prazo para concluir tarefas antes de cancelá-las |
| `MONITORING_ALLOWED_PORTS` | `[80,443]` | Portas de destino permitidas, JSON |
| `MONITORING_ALLOWED_HOSTS` | `[]` | Restrição adicional por hosts exatos; vazio não adiciona restrição |
| `SERVICE_INTERNAL_HOSTS` | `[]` | Hosts exatos candidatos à exceção interna |
| `MONITORING_INTERNAL_NETWORKS` | `[]` | Redes privadas autorizadas para a exceção interna |
| `MONITORING_HEARTBEAT_PATH` | `/tmp/infrapulse-worker-heartbeat` | Arquivo local de saúde |

O `timeout_seconds` de cada serviço é o prazo total: inclui DNS, TCP, TLS,
cabeçalhos e leitura do corpo. O timeout de conexão é limitado por esse prazo.
O encerramento de sockets pode adicionar até um segundo de limpeza.
O corpo é consumido em streaming, descartado e nunca persistido. Uma leitura de
transporte pode exceder o limite de corpo em até um bloco de 64 KiB antes da
rejeição; o processo não acumula o corpo. Cabeçalhos são limitados pelo parser
HTTP/1.1 do HTTP Core/h11. Não há descompressão automática.

O worker usa o usuário sem privilégios da imagem do backend, não publica portas
e participa da rede interna do banco e de uma bridge de saída HTTP. PostgreSQL
permanece apenas na rede interna, sem publicação de portas.

## Agendamento, backpressure e reinicialização

O intervalo é contado entre inícios, identificados por `checked_at`. Apenas
serviços ativos são selecionados. Antes de executar uma tarefa, o worker relê
seu cadastro, incluindo atividade, URL, timeout e intervalo. Alterações após essa
leitura passam a valer na próxima execução; desativar um serviço não cancela uma
requisição já iniciada.

A seleção ocorre uma vez por ciclo quando há capacidade. Ela busca apenas a
quantidade de serviços que cabe na capacidade restante, priorizando serviços
sem histórico e depois a última verificação mais antiga. Um índice permite
buscar o último resultado sem agregar todo o histórico. Não existe fila ilimitada.
Sob saturação, verificações atrasam; não são reproduzidos todos os ciclos perdidos.
A precisão é limitada pelo polling, duração das verificações e capacidade.

O conjunto de tarefas em execução impede sobreposição por serviço. Um prazo
monotônico em memória impede repetição imediata inclusive quando a persistência
falha. A próxima seleção pode levar em conta alterações de intervalo apenas
depois de expirar esse prazo, calculado com a configuração da tentativa anterior.
Sessões SQLAlchemy são criadas e fechadas nas threads responsáveis pelas operações.
Nenhuma sessão é compartilhada e nenhuma transação engloba uma conexão HTTP.

Após reiniciar, a última verificação persistida recupera o intervalo. Serviços
sem histórico ou vencidos ficam elegíveis imediatamente, sujeitos à capacidade.
Tentativas interrompidas antes do commit não têm registro e podem ser repetidas.
Falhas de banco são registradas sem detalhes sensíveis; não há fila durável de
resultados nem garantia de exactly-once. Um resultado de serviço removido durante
a requisição é descartado. A exclusão posterior remove o histórico em cascata.

SIGTERM/SIGINT interrompem novos agendamentos, aguardam tarefas no prazo de graça
e cancelam as restantes. Threads de banco/resolver já iniciadas não podem ser
interrompidas pelo cancelamento asyncio. As operações PostgreSQL têm timeouts;
o resolver do sistema depende de sua configuração operacional. O Compose concede
75 segundos antes de encerrar o container à força.

O heartbeat é atualizado pelo ciclo do scheduler, inclusive quando toda a
capacidade está ocupada. O health check valida PID e idade do arquivo; falhas
consecutivas de seleção no banco tornam o heartbeat obsoleto. Isso mede a saúde
do processo, não a disponibilidade dos destinos nem sucesso de persistência de
cada resultado. O Compose informa o estado unhealthy; não reinicia o processo
automaticamente só por esse estado.

**Somente uma instância de monitoramento é suportada.** Réplicas exigem leases,
locks distribuídos ou fila com coordenação e idempotência; um semáforo local não
coordena processos distintos.

## Resultados e métricas

| `status` | Significado |
| --- | --- |
| `UP` | HTTP 2xx, resposta concluída dentro dos limites |
| `DOWN` | Resposta HTTP não 2xx, inclusive 3xx sem seguir Location |
| `TIMEOUT` | Prazo total ou de conexão excedido |
| `DNS_ERROR` | Resolução falhou ou não retornou endereços |
| `TLS_ERROR` | Falha de handshake, certificado ou hostname |
| `CONNECTION_ERROR` | Falha TCP, reset ou erro de leitura/escrita de transporte |
| `HTTP_ERROR` | Protocolo inválido, resposta excessiva ou destino rejeitado pela política |

O status detalha a causa técnica. Para disponibilidade, somente `UP` é sucesso;
`down_checks` conta todos os resultados diferentes de `UP`, não apenas o enum
`DOWN`. Um serviço bloqueado pela política não recebe uma requisição e seu
resultado é `HTTP_ERROR` com mensagem genérica. Falhas inesperadas de execução ou
persistência geram logs, sem fabricar resultados de monitoramento.

`checked_at` é o início UTC da tentativa; `created_at` é atribuído pelo banco.
`response_time_ms` mede com relógio monotônico o tempo de execução até o resultado,
incluindo DNS e falhas; não é apenas tempo até o primeiro byte. Média e p95 usam
todas as durações não nulas do período, inclusive verificações malsucedidas.
O p95 usa interpolação contínua (`percentile_cont(0.95)` no PostgreSQL).

```text
uptime_percentage = up_checks / total_checks * 100
```

Esse indicador representa proporção de verificações bem-sucedidas, não
disponibilidade ponderada pelo tempo. Sem verificações: contadores zero,
disponibilidade, latências e último estado do período `null`.

## Contrato REST

| Endpoint GET | Resposta |
| --- | --- |
| `/api/v1/services/{id}/checks` | `items`, `total`, `limit`, `offset` |
| `/api/v1/services/{id}/metrics` | Contadores, disponibilidade, média, p95, último resultado do período |
| `/api/v1/services/{id}/status` | `service_id`, `last_status`, `last_checked_at`, sem janela temporal |

Histórico e métricas aceitam `start` e `end` em ISO 8601 com timezone. Os padrões
são agora menos 24 horas e agora. Ao consultar um período histórico, informe os
dois limites. A janela precisa ser positiva e ter no máximo 90 dias, usando
`start <= checked_at < end`. Respostas são normalizadas para UTC.

Histórico: `limit` entre 1 e 100, padrão 20; `offset` entre 0 e 10000, padrão zero.
A ordenação é `checked_at DESC, id DESC`, e `total` considera a janela completa.
Resultados persistidos entre requisições podem deslocar páginas por offset.
Consultas agregadas e último resultado usam statements distintos sob READ COMMITTED;
uma gravação concorrente pode aparecer entre eles. Não há snapshot entre chamadas.

Serviço inexistente retorna 404; UUID ou parâmetros inválidos retornam 422.
Serviço existente sem histórico retorna dados vazios, preservando HTTP 200.
Serviços inativos mantêm seus dados disponíveis para consulta.

```bash
curl 'http://localhost:8000/api/v1/services/UUID/checks?limit=20&offset=0'
curl 'http://localhost:8000/api/v1/services/UUID/metrics?start=2026-10-01T00:00:00Z&end=2026-10-02T00:00:00Z'
curl 'http://localhost:8000/api/v1/services/UUID/status'
```

## Modelo de ameaças SSRF

Entradas não confiáveis incluem URLs cadastradas e respostas DNS/HTTP. Um atacante
pode cadastrar domínio público que resolve para rede interna, alterar respostas
DNS, oferecer redirects para metadata ou enviar respostas lentas/grandes.

As defesas são:

1. Revalidar a URL do banco: apenas HTTP/HTTPS, sem credenciais e dentro das
   portas/hosts configurados pelo operador.
2. Resolver uma vez por tentativa e validar todos os IPv4/IPv6 retornados.
   Uma resposta mista com qualquer IP proibido rejeita o destino inteiro.
3. Bloquear endereços privados sem exceção explícita; sempre bloquear loopback,
   link-local, multicast, reservados, não especificados, metadata conhecida e
   IPv6 mapeado/6to4/Teredo/NAT64 conhecido.
4. Conectar um socket não bloqueante ao IP numérico aprovado usando um backend
   próprio. O transporte não chama DNS. Host e SNI usam o hostname original;
   certificados e hostname são verificados normalmente.
5. Criar uma conexão por tentativa, sem retries, redirects, proxies ambientais,
   credenciais, cookies ou reutilização entre serviços/verificações.
6. Limitar tempo, concorrência e bytes; descartar o corpo. Logs contêm IDs,
   status e durações. Mensagens persistidas são fixas; exceções, URLs, queries,
   cabeçalhos e corpos não são registrados pelo monitoramento. Logs internos
   de debug do HTTP Core são suprimidos para evitar vazamento de alvos.

Para permitir um destino interno, ambas as autorizações são necessárias:

```dotenv
SERVICE_INTERNAL_HOSTS=["api.internal"]
MONITORING_INTERNAL_NETWORKS=["10.20.30.0/24"]
```

O hostname precisa corresponder exatamente, e cada IP privado resolvido deve
pertencer à rede autorizada. Loopback e metadata continuam proibidos, inclusive
com exceções. Não use redes amplas para contornar a política. Os testes de sockets
locais injetam um backend controlado; essa substituição não existe nas variáveis
de ambiente nem no contrato da API.

Limites do modelo: aplicação e operador/ambiente são confiáveis. IPs públicos
podem atuar como proxies ou ser roteados de forma especial pela infraestrutura.
Prefixos de tradução IPv6 personalizados e serviços de metadata específicos de
provedores exigem restrições de rede adicionais. A aplicação não controla rotas,
NAT, DNS do sistema ou configuração da rede do host. Use políticas de egress para
reforçar as restrições em ambientes reais. URLs continuam armazenadas no cadastro;
evite colocar segredos em URLs. Autenticação/autorização permanecem fora do MVP.

## Validação

Os testes não fazem requisições a serviços públicos. Mocks cobrem classificação,
política DNS e rebinding; servidores locais controlados verificam o socket
efetivo, Host, SNI, certificados, ausência de segunda resolução e proxies.
Certificados temporários são gerados pelo OpenSSL e não são versionados; o alvo
Docker `test` declara essa dependência explicitamente; o alvo runtime não é alterado.

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/python -m pip check
```

Para PostgreSQL, use a sequência do README com `compose.test.yaml`. O banco tem
rede interna exclusiva, senha efêmera, tmpfs e nenhuma porta publicada. A suíte
verifica upgrade/downgrade completo e incremental `0001 → 0002 → 0001 → 0002`,
preservação do cadastro, constraints, cascata, p95 PostgreSQL e worker em processo
real. Não há retenção automática nesta sprint: acompanhe o crescimento do histórico.

Prometheus/OpenTelemetry completos e visualização ficam para a Sprint 04.
