# Sprint 03 — Relatório de entrega

## Implementação

Monitoramento GET HTTP/HTTPS em worker independente da API. O checker valida DNS
e fixa o socket em um IP autorizado, preservando Host/SNI e validação TLS. Não
usa redirects, retries, proxies ambientais, autenticação ou cookies. Limita tempo,
corpo e concorrência, sem persistir corpo ou detalhes sensíveis de exceções.

O scheduler seleciona serviços ativos, impede sobreposição local e aplica
backpressure sem fila ilimitada. Operações SQLAlchemy síncronas usam threads e
sessões próprias. O worker trata SIGTERM/SIGINT, aguarda tarefas e tem heartbeat
local. O Compose mantém PostgreSQL isolado, sem novas portas publicadas.

A revisão Alembic `0002` adiciona checks com FK em cascata e índice por
serviço/horário/UUID. A revisão `0001` foi preservada. Histórico, métricas e status
são expostos em endpoints Pydantic; períodos sem dados não indicam 100% de uptime.

## Validação executada

| Verificação | Resultado final |
| --- | --- |
| `.venv/bin/pytest -q` em `backend/` | **157 passed, 11 skipped**; skips exclusivos de PostgreSQL |
| Suíte completa no `compose.test.yaml`, PostgreSQL 17 | **168 passed**, sem skips |
| SSRF, DNS rebinding e transporte HTTP/TLS local | Aprovados nas duas suítes |
| `.venv/bin/ruff check .` / equivalente pela raiz | Aprovado |
| `.venv/bin/ruff format --check .` / equivalente pela raiz | 45 arquivos formatados |
| `.venv/bin/mypy` | Sem erros em 45 arquivos |
| `python -m pip check` local e imagem de testes | Sem dependências incompatíveis |
| `docker compose --env-file backend/.env config --quiet` | Aprovado |
| Configuração do `compose.test.yaml` com senha efêmera | Aprovada |
| Build Docker da API e worker | Aprovado |
| `git diff --check` | Aprovado |

A suíte PostgreSQL executou migrações completas e incrementais, preservou um
serviço entre `0001 → 0002 → 0001 → 0002`, executou `alembic check` e verificou
constraints, índices, UTC, persistência entre sessões, cascata e p95 no banco real.
O teste de processo iniciou um worker real, persistiu uma requisição HTTP a um
servidor controlado, verificou heartbeat e encerramento bem-sucedido com SIGTERM.
Testes de scheduler verificaram o limite de concorrência, intervalos, inatividade,
ausência de sobreposição, conclusão durante shutdown e cancelamento após o prazo.

Os testes não acessaram serviços públicos de terceiros. A instalação/build usou
os registros de dependências e imagens. Banco de testes: senha gerada somente no
ambiente do processo, rede interna exclusiva, dados em tmpfs e nenhuma porta
publicada. Containers e rede desse projeto foram removidos após a execução.
Não foram executadas migrações no banco de desenvolvimento.

Uma tentativa de atualizar o pacote local sem isolamento de build falhou porque
o ambiente não tinha setuptools. A instalação com isolamento foi executada com
sucesso; o `pip check` final passou. Não há falha de validação pendente.

## Cobertura SSRF

| Controle | Evidência automatizada |
| --- | --- |
| HTTP/HTTPS, credenciais e portas | Rejeição antes de DNS/conexão |
| Loopback, privados, link-local e metadata | URLs diretas e respostas DNS proibidas |
| IPv6 e transição | Bloqueio de mapeados, 6to4 e prefixos NAT64 conhecidos |
| Todos os IPs DNS | Resposta mista público/proibido rejeitada antes do transporte |
| Rebinding | Segunda resposta proibida não altera a conexão da primeira tentativa; próxima tentativa rejeitada |
| Ausência de nova resolução | `getaddrinfo` proibido durante conexões TCP/TLS reais controladas |
| IP efetivo | Peer do socket corresponde ao endereço configurado no backend |
| Host e SNI | Servidor TLS controlado inspeciona hostname e cabeçalho recebidos |
| TLS | Certificado confiável funciona; certificado não confiável e hostname incorreto são rejeitados |
| Proxies | Variáveis HTTP/HTTPS/ALL_PROXY apontam a destino inválido sem interferir na conexão direta |
| Redirects | 302 para metadata retorna DOWN sem seguir Location |
| Cookies/credenciais | Duas requisições não enviam Cookie ou Authorization, apesar de Set-Cookie |
| Limite de resposta | Content-Length e streaming chunked excessivos são rejeitados |
| Exceção interna | Exige simultaneamente host exato e rede autorizada |
| Dados sensíveis | Mensagens fixas e logs correlacionados não incluem o token de teste |

## Arquivos criados

- `backend/alembic/versions/0002_create_check_results.py`
- `backend/app/api/routes/monitoring.py`
- `backend/app/db/check_repository.py`
- `backend/app/models/check_result.py`
- `backend/app/monitoring/__init__.py`
- `backend/app/monitoring/checker.py`
- `backend/app/monitoring/scheduler.py`
- `backend/app/monitoring/transport.py`
- `backend/app/schemas/monitoring.py`
- `backend/app/services/metrics.py`
- `backend/app/worker.py`
- `backend/tests/test_checker.py`
- `backend/tests/test_monitoring_api.py`
- `backend/tests/test_monitoring_postgres.py`
- `backend/tests/test_scheduler.py`
- `backend/tests/test_transport.py`
- `docs/monitoring.md`
- `docs/adr/0001-http-monitoring.md`
- `docs/sprint03-report.md`

## Arquivos modificados

- `README.md`
- `backend/.env.example`
- `backend/Dockerfile`
- `backend/alembic/env.py`
- `backend/app/core/config.py`
- `backend/app/core/logging.py`
- `backend/app/core/url_policy.py`
- `backend/app/main.py`
- `backend/pyproject.toml`
- `backend/requirements.lock`
- `backend/tests/conftest.py`
- `backend/tests/test_postgres.py`
- `compose.test.yaml`
- `compose.yaml`

## Decisões e limites

Decisões completas: [ADR 0001](adr/0001-http-monitoring.md).
Operação e ameaças: [monitoring.md](monitoring.md).

- Uma única réplica do worker; múltiplas réplicas precisam de coordenação distribuída.
- Sem fila durável de resultados ou garantia de exactly-once. Tentativas sem commit
  podem ser perdidas/repetidas após reinício.
- Sem retenção automática do histórico nesta sprint.
- Polling e saturação podem atrasar verificações; mudanças no intervalo podem
  aguardar o prazo em memória da tentativa anterior.
- Cancelar asyncio não interrompe threads DNS/DB já iniciadas. O banco possui
  timeouts; o resolver depende da configuração do sistema.
- O socket fixa o IP, mas não controla roteamento/NAT, prefixos de tradução
  personalizados ou intermediários públicos. Egress de infraestrutura complementa
  a política SSRF.
- Média/p95 incluem as durações de tentativas com falha. Disponibilidade mede
  proporção de checks UP, sem ponderação temporal. `down_checks` inclui todas as falhas.
- Prometheus/OpenTelemetry completos, autenticação e dashboard não fazem parte
  desta entrega.

O trabalho permaneceu na `develop`. Entire continuou habilitado; hooks e
configurações não foram modificados. Nenhum checkpoint manual, commit ou push
foi realizado.

Commit sugerido, a ser criado somente pelo fluxo autorizado do projeto:

```text
feat(monitoring): add SSRF-safe HTTP worker and reliability API
```
