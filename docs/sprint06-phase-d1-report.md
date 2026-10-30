# Sprint 06 — Fase D1: PostgreSQL Integration CI

Data: 2026-10-30. Status: implementação e validação locais concluídas; aguardando
revisão. Execução desta fase no GitHub Actions: **NOT RUN**. Sem commit ou push.

## Inspeção e escopo

Branch `develop`, baseline e HEAD preservados em
`545ade7d92e632850b8b7ed9e35ed64ffcb8e486`; working tree inicialmente limpa.
Foram lidos `AGENTS.md`, `.entire/agent-guide.md`, workflow, `compose.test.yaml`,
Dockerfile e dockerignore do backend, fixtures, testes PostgreSQL/worker, migrações,
configuração Alembic, política SSRF e comandos das Sprints 03–05.

Entire Brain foi consultado. O brief retornou o invariante de preservar transporte
SSRF e isolamento PostgreSQL, mas reportou índice atrasado em relação ao HEAD.
As conclusões foram verificadas no código atual. Não houve refresh, reparo ou
mudança de configuração do Entire.

Arquivos desta implementação:

- Modificado `.github/workflows/ci.yml`.
- Criado `docs/sprint06-phase-d1-report.md`.

Nenhuma alteração no Compose, Dockerfile, dependências, migrações, aplicação,
worker, política SSRF ou testes existentes. Os seis jobs anteriores e o código do
agregador foram comparados estruturalmente com o baseline e permaneceram intactos;
o agregador recebeu somente a nova dependência.

## Job backend-postgres

Runner `ubuntu-24.04`; timeout do job: 25 minutos. Não depende de outros jobs.
Checkout com o mesmo SHA já aprovado e `persist-credentials: false`.
Diretório dos comandos explicitamente definido como raiz do checkout.

Etapas:

1. Usa o projeto `infrapulse-ci-postgres-${{ github.run_id }}-${{ github.run_attempt }}`.
   Valida o formato e recusa containers, redes ou volumes existentes com esse label
   de projeto. Falhas ao consultar o daemon também interrompem o preflight.
2. Gera senha descartável com `openssl rand -hex 32`; registra a máscara do GitHub
   antes de exportá-la por `GITHUB_ENV`. Não usa secrets de produção nem imprime
   dumps de ambiente/configuração resolvida. A saída `ready=true` habilita cleanup
   somente após o preflight bem-sucedido.
3. Valida o Compose existente com `config --quiet`.
4. Constrói `tests` com o target existente do Dockerfile; timeout de 15 minutos.
5. Executa `up --abort-on-container-exit --exit-code-from tests`; timeout de cinco
   minutos. O CMD existente executa pytest serialmente, sem xdist/paralelismo.
6. Cleanup com `always()` e condição de ownership `ready == 'true'`, timeout de
   dois minutos, limitado ao arquivo e projeto reservados. Remove containers,
   rede e eventuais volumes desse projeto. Não executa prune ou remoção global.

Todos os comandos Compose incluem `--env-file /dev/null`, `-f compose.test.yaml`
e `-p "$POSTGRES_TEST_PROJECT"`. Assim, não usam o Compose principal, seu `.env`
ou seu nome de projeto. `--exit-code-from tests` preserva o resultado do pytest;
cleanup em passo separado não transforma falha anterior em sucesso. Também não
há wrapper, pipeline, `continue-on-error` ou `|| true` sobre o comando de testes.

## Isolamento e segurança

Reaproveitado integralmente `compose.test.yaml`:

- Imagem `postgres:17-bookworm`; o teste consulta a versão real do servidor.
- Banco e usuário `infrapulse_test`, host interno `test-db`.
- Dados em tmpfs em `/var/lib/postgresql/data`; sem volume persistente de dados.
- Uma rede `${projeto}_test` com `internal: true`; sem redes externas/host.
- Nenhuma publicação de portas, inclusive PostgreSQL 5432.
- Sem containers privilegiados; a imagem de testes executa como usuário `app`.
- Senha somente no ambiente transitório; nenhum upload de volumes, banco ou logs.

Não foi adicionado service container. Não houve alteração de firewall, Docker
daemon, redes de desenvolvimento ou configuração do GitHub. Downloads de pacotes
ocorrem no build existente; a execução da suíte usa a rede interna descartável.
As permissões globais `contents: read`, triggers, concurrency e demais proteções
do workflow anterior foram preservadas. Não foram adicionadas actions ou dependências.

O teste do worker autoriza somente seu próprio IP de container `/32`, a porta
efêmera do servidor controlado e o host exato. Não foram configuradas permissões
para redes privadas amplas do runner. As políticas de credenciais em URLs, DNS,
IP, metadata, TLS, proxies e redirects permanecem inalteradas e são exercitadas
pela suíte completa de SSRF/TLS existente.

## Seleção, Alembic e worker

O job roda a suíte completa, conforme os comandos históricos do README e Sprints
03–05, e não apenas `-m integration`. Isso também exercita CRUD e monitoramento
contra PostgreSQL através de `registry_engine`, além das regressões SSRF/TLS.

Resultado final: **197 casos coletados, 195 aprovados e 2 omitidos**.
Os únicos skips são:

- `test_observability_stack.py::test_real_observability_stack`: exige a stack real.
- `test_observability_stack.py::test_collector_outage_real_processes`: exige
  Collector intencionalmente parado.

Os 11 casos específicos de PostgreSQL, omitidos na suíte local sem banco, foram
executados aqui. Não foi ativada a stack de observabilidade nem iniciada a D2.

Evidências dos testes existentes aprovados:

- `postgres_engine` exige `INFRAPULSE_RUN_INTEGRATION=1` e recusa host, banco ou
  usuário fora da tupla dedicada. Executa upgrade até head, downgrade até base,
  confirma remoção das tabelas, upgrade até `0001`, insere sentinela, avança até
  head e comprova sua preservação; regride até `0001`, confirma remoção de
  check_results, reaplica head e executa `alembic command.check`.
- `test_migration_and_postgres_persistence` verifica `SHOW server_version_num`
  com major efetivamente igual a **17**, revisão **0002**, defaults, UTC e
  persistência entre sessões. O servidor local observado foi **PostgreSQL 17.11**.
- Testes de monitoramento verificam persistência, timezone, índices, constraints,
  histórico, métricas e cascata no PostgreSQL real.
- `test_standalone_worker_real_http_and_graceful_signal` inicia `python -m
  app.worker` como subprocesso real, recebe HTTP no servidor controlado, confirma
  um único resultado persistido, executa healthcheck do heartbeat, envia SIGTERM,
  exige retorno zero, logs worker_started/worker_stopped e estado persistido UP.

Nenhuma migração ou consulta de dados foi dirigida ao banco de desenvolvimento.

## Comandos executados e resultados

Inventário inicial por leitura: `docker version`, `docker compose version`,
`docker ps`, `docker network ls`, `docker volume ls` e `docker inspect` com campos
específicos, sem `.Config.Env`. Ferramentas locais: Docker 29.9.1, Compose 5.6.0,
actionlint 1.7.12, Bash/ShellCheck e OpenSSL disponíveis.

Foi criada uma cópia limpa com `git archive HEAD compose.test.yaml backend`, em
`/tmp/infrapulse-s06d1-clean`. O helper temporário extraiu os passos do workflow e
os executou com Bash `-e`/`pipefail`, em projeto vazio e com senha nova. Equivalentes:

```bash
# POSTGRES_TEST_PROJECT: nome exclusivo; senha gerada, nunca literal.
docker compose --env-file /dev/null -f compose.test.yaml -p "$POSTGRES_TEST_PROJECT" config --quiet
docker compose --env-file /dev/null -f compose.test.yaml -p "$POSTGRES_TEST_PROJECT" build tests
docker compose --env-file /dev/null -f compose.test.yaml -p "$POSTGRES_TEST_PROJECT" up --abort-on-container-exit --exit-code-from tests
# Somente após confirmar que o projeto é exclusivamente descartável:
docker compose --env-file /dev/null -f compose.test.yaml -p "$POSTGRES_TEST_PROJECT" down --volumes --remove-orphans --timeout 30
```

Projeto da validação final: `infrapulse-ci-postgres-1793361568-317229`.

| Verificação | Resultado | Evidência |
| --- | --- | --- |
| Baseline, escopo e jobs anteriores | PASS | HEAD esperado, árvore inicial limpa, jobs anteriores intactos |
| Configuração Compose | PASS | config --quiet e inspeção JSON somente em memória |
| Build da imagem existente | PASS | Target test construído, sem modificar Dockerfile |
| Startup e saúde do PostgreSQL | PASS | Dependência healthy satisfeita; PostgreSQL 17.11 observado |
| Suíte serial | PASS | 195 passed, 2 skipped em **13,40 s**, código 0 |
| Migrações/Alembic/major 17 | PASS | Fixture completa e teste de persistência/versionamento aprovados |
| Worker/HTTP/persistência/heartbeat/SIGTERM | PASS | Teste do processo real aprovado |
| SSRF/TLS | PASS | Casos existentes executados; política sem alterações |
| Isolamento real | PASS | Sem port bindings ou modo privileged; rede própria internal; HostConfig.Tmpfs confirmado |
| Cleanup | PASS | Código 0; nenhum container, rede ou volume do projeto restante |
| Preservação do ambiente existente | PASS | Snapshots antes/depois idênticos para cinco containers, sete redes e quatro volumes |
| Preflight com recursos existentes | PASS | Simulação interrompe antes de habilitar cleanup |
| Preflight com daemon indisponível | PASS | Simulação interrompe antes de habilitar cleanup |
| Código de falha dos testes | PASS | Passo exato preserva código simulado 17 |
| ci-required | PASS | 16.384 combinações, somente sete sucessos aprovam |
| actionlint, bash -n e ShellCheck | PASS | Workflow final e todos os blocos run aprovados |
| git diff --check e relatório não rastreado | PASS | Sem erros de whitespace |
| GitHub Actions desta fase | NOT RUN | Sem commit/push autorizado |
| D2 / stack de observabilidade | NOT RUN | Fora do escopo; dois skips esperados |

O Dockerfile existente emite aviso de pip como root durante o build; o processo
de testes roda como app. Os logs PostgreSQL apresentam erros de constraints
provocados pelos testes negativos e a mensagem de término do logical replication
launcher no shutdown. Não foram suprimidos; a suíte encerrou com código zero.

A primeira execução também aprovou 195 testes/2 skips, mas o helper de inspeção
falhou ao procurar tmpfs em `.Mounts` após o término do banco. O cleanup executou
e preservou o ambiente existente. Foi corrigida somente a checagem temporária para
`.HostConfig.Tmpfs`; a execução final repetiu a suíte e passou integralmente.
Não há FAIL ou BLOCKED pendente no workflow implementado.

## Evidências do cleanup e agregador

Os containers, rede e volumes foram consultados antes de qualquer criação.
O projeto foi confirmado vazio e distinto de `jc-infrapulse`. Antes do down com
`--volumes`, foram inspecionados seus dois containers e sua única rede, confirmando
ownership pelo projeto e ausência de volumes persistentes/compartilhados.

Após cleanup, as consultas por label do projeto não retornaram containers, redes
ou volumes. Snapshots dos recursos preexistentes permaneceram idênticos: IDs,
nomes, StartedAt, RestartCount, estado e portas dos containers; IDs das redes e
nomes dos volumes. Frontend, API e PostgreSQL locais não foram reiniciados.
Os arquivos temporários de senha usados pelo helper foram removidos; logs locais
substituem o valor efêmero por marcador. Nenhum artefato foi adicionado ao Git.
Imagens locais de teste e cache de build permanecem disponíveis; não houve push
de imagem, prune ou remoção de imagens/volumes preexistentes.

`ci-required` mantém seu nome e `always()`, passando a depender dos sete jobs:
backend-quality, backend-unit, api-contract, frontend-quality, frontend-tests,
frontend-build e backend-postgres. O bloco exato do agregador foi executado em
**16.384 combinações** de success/failure/cancelled/skipped: somente todos em
success aprovam, incluindo a exigência para backend-postgres. Cleanup bem-sucedido
não mascara um passo anterior com falha nem transforma cancelamento em sucesso.

Evidências locais fora do Git:

- `/tmp/infrapulse-s06d1-clean/evidence/summary.json`.
- `/tmp/infrapulse-s06d1-clean/evidence/before.json` e `after.json`.
- `/tmp/infrapulse-s06d1-clean/evidence/runtime-isolation.json`.
- Logs dos passos em `/tmp/infrapulse-s06d1-clean/evidence/*.log`.
- Helper temporário `/tmp/infrapulse-s06d1-validate.py`.

## Limitações, Git, Entire e recomendação

Esta execução foi local, não no GitHub Actions. O cancelamento global/forçado de
um runner pode impedir cleanup; `always()` cobre o fluxo normal da plataforma,
não perda do runner. Há timeout por build/testes/cleanup para reservar tempo dentro
do timeout global. Preflight não é um lock distribuído; o run ID e a tentativa
produzem nomes exclusivos para este job.

Tags existentes de PostgreSQL/Python/OS continuam mutáveis, e não foi antecipada
uma fase de scanning ou supply chain. Nenhum pin de action foi alterado; o checkout
herdado pode repetir o warning de Node.js 20 já documentado nas fases anteriores.
Não foi adicionado upload de JUnit ou dados PostgreSQL; os resultados ficam no log.

Branch `develop` e HEAD original preservados. Somente workflow e este relatório
alterados, sem staging, commit ou push. Entire permanece habilitado; a consulta
final indicou um checkpoint pendente, associado ao commit anterior da Fase C,
sem erro de sincronização informado. Nenhum checkpoint manual, reparo, mudança de
hooks/configuração ou sincronização foi executado nesta fase.

Recomendação: pronta para revisão local e posterior autorização de versionamento
e validação remota. Não considerar D1 definitivamente aprovada até o novo job e o
agregador passarem no GitHub Actions. Não foi iniciada a Fase D2.

Conventional Commit sugerido, sem executar:
`ci(backend): add isolated PostgreSQL integration gate`.
