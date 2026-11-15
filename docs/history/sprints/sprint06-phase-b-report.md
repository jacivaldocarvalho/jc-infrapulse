# Sprint 06 — Fase B: Backend CI

Data: 2026-10-30. Status: implementação pronta para revisão e validação remota;
aprovação definitiva pendente de execução bem-sucedida no GitHub Actions.

## Escopo e arquivos

- Criado `.github/workflows/ci.yml`.
- Criado este relatório, `docs/sprint06-phase-b-report.md`.
- Nenhum arquivo preexistente modificado. Sem mudanças na aplicação, contratos,
  dependências, migrações, Dockerfiles, Compose ou configurações remotas.
- Frontend, PostgreSQL, Playwright, Docker scanning e supply chain security
  permanecem fora desta implementação.

## Inspeção e instalação

Foram lidos `AGENTS.md`, `.entire/agent-guide.md`, `backend/pyproject.toml`,
`backend/requirements.lock`, documentação, scripts e fontes dos testes/fixtures.
Entire Brain foi consultado com `brief` e `recall`; não retornou decisões
adicionais da Fase A específicas ao CI. As conclusões foram conferidas nas fontes.

Estado inicial: branch `develop`, HEAD
`709a4a76f6d57314426d7c92f9799d0c7cdaaaca`, working tree limpa.

A instalação segue a orientação existente do README. Em checkout limpo:

```bash
python3.12 -m venv /tmp/infrapulse-ci-venv
source /tmp/infrapulse-ci-venv/bin/activate
cd backend
python -m pip install -c requirements.lock -e '.[dev]'
```

O lock funciona como constraints para as dependências declaradas e transitivas;
o extra `dev` instala Ruff, mypy, pytest e httpx. A instalação editable evita cópias
de build no diretório examinado pelo Ruff. Nenhuma dependência nova foi adicionada.
As 49 versões do lock coincidiram com os metadados dos pacotes instalados.

Validação final em Ubuntu 24.04.5 LTS, sobre cópia dos arquivos versionados obtida
com `git archive HEAD backend scripts`, sem `.env`, usando exclusivamente o novo
ambiente `/tmp/infrapulse-s06b-final/venv`. O ambiente `backend/.venv` existente
foi consultado para versões, sem instalação ou atualização nele.

| Ferramenta | Versão local e no ambiente isolado | Configuração do CI |
| --- | --- | --- |
| Python | 3.12.3 | 3.12, patch fornecido por setup-python |
| Ruff | 0.17.3 | requirements.lock |
| mypy | 1.20.2 | requirements.lock |
| pytest | 9.2.0 | requirements.lock |

## Estrutura dos jobs

| Job / nome público | Execução | Timeout |
| --- | --- | --- |
| `backend-quality` | Instalação, Ruff, formatação, mypy, pip check, Ruff dos scripts | 15 min |
| `backend-unit` | Mesma instalação, oito módulos explícitos, JUnit e upload | 15 min |
| `ci-required` | Agrega exclusivamente os dois jobs anteriores | 5 min |

Todos usam `ubuntu-24.04`; os dois jobs de backend usam Python 3.12.
Diretório padrão dos comandos: `backend`. Os checks de `scripts/` e o agregador
sobrescrevem esse diretório para a raiz. O agregador não precisa de checkout.
A duplicação de checkout/setup/install fica restrita aos dois runners independentes;
não foi criada uma action auxiliar para essa pequena sequência.

O Ruff do backend usa `backend/pyproject.toml`. O mypy executa sem argumentos,
respeitando os alvos existentes `app`, `tests` e `alembic`, modo strict e plugin
Pydantic. Os quatro scripts Python da raiz usam os padrões atuais do Ruff na raiz,
onde não existe configuração própria. Não foram importadas as regras do backend
para os scripts nem executados scripts que invocam Docker, seed ou Playwright.
O escopo existente do mypy não inclui esses scripts.

## Seleção de testes

O comando do workflow lista nominalmente estes módulos; não depende somente de
skips ou de um filtro por marcador:

| Módulo | Casos aprovados no JUnit | Recursos utilizados |
| --- | --- | --- |
| `test_health.py` | 5 | ASGI e sessão mockada para readiness |
| `test_services.py` | 41 | ASGI e SQLite em memória |
| `test_url_policy.py` | 42 | Validação local |
| `test_checker.py` | 45 | Resolver e transporte controlados |
| `test_transport.py` | 5 | Sockets loopback, TLS e executável OpenSSL |
| `test_scheduler.py` | 6 | Stores/checkers em memória e arquivos temporários |
| `test_monitoring_api.py` | 13 | ASGI e SQLite em memória |
| `test_observability.py` | 27 | Exporters controlados, listeners/processos locais |
| **Total** | **184** | **Sem PostgreSQL ou stack externa** |

Excluídos nominalmente:

- `test_postgres.py` e `test_monitoring_postgres.py`: marcador `integration`,
  dependem de `postgres_engine`, PostgreSQL e migrações do projeto descartável.
- `test_observability_stack.py`: depende da stack real; usa skip condicional a
  `INFRAPULSE_OBSERVABILITY_INTEGRATION`, sem o marcador `integration` do banco.

`conftest.py` escolhe SQLite em `registry_engine` quando
`INFRAPULSE_RUN_INTEGRATION != 1`. O workflow fixa essa variável e
`INFRAPULSE_OBSERVABILITY_INTEGRATION` em `0`. As fixtures selecionadas fornecem
configuração de teste; a execução na cópia limpa comprovou ausência de dependência
de `.env`. Nenhuma migração foi executada.

O pytest roda diretamente em Bash com falha imediata e pipefail, sem pipelines
ou wrappers que substituam seu código de saída. Seleção vazia retorna código 5;
caminhos inexistentes também causam falha. `--strict-markers` valida os marcadores.
JUnit: `backend/test-results/backend-unit.xml`; upload com `always()`, retenção
de sete dias e erro se o arquivo não existir. Uma falha anterior do pytest continua
reprovando o job mesmo que o upload tenha sucesso.

## Agregador

`ci-required` usa `if: ${{ always() }}` no job e
`needs: [backend-quality, backend-unit]`. O passo lê `toJSON(needs)` por variável
de ambiente e exige resultado `success` para todas as dependências, com pelo
menos uma dependência. `failure`, `cancelled` e `skipped` reprovam o gate.

A condição evita que o gate seja pulado automaticamente por falha/cancelamento
de uma dependência. O cancelamento global de uma execução continua sujeito ao
agendador do GitHub; não há promessa de conclusão de um run cancelado. Na lógica
do gate, resultados cancelados nunca são aceitos como sucesso.

Próximas fases podem estender apenas a lista `needs`, mantendo o nome público e
a avaliação de todos os resultados. Nenhum job futuro foi antecipado.
A semântica de status e `always()` foi revisada na
[documentação oficial de expressões](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions).

## Comandos e evidências

Inspeção: `git status --short`, `git branch --show-current`, `git rev-parse HEAD`,
`rg --files`, `rg -n`, leituras com `cat`/`sed`, comandos `--version`,
`entire agent-help`, `entire brain brief ... --json`,
`entire brain recall ... --json`. `entire agent-help brain` não foi suportado;
as consultas Brain documentadas no guia funcionaram.

SHAs conferidos por `git ls-remote` contra as tags oficiais dos três repositórios
`actions/checkout`, `actions/setup-python` e `actions/upload-artifact`.

Os comandos de qualidade/testes foram extraídos do YAML e executados por
`python3 /tmp/infrapulse-s06b-validate.py` na cópia isolada. Equivalentes completos:

```bash
# Em backend/, com o novo ambiente isolado no PATH:
ruff check .
ruff format --check .
mypy
python -m pip check
python -m pytest -v -ra --strict-markers -m 'not integration' \
  tests/test_health.py \
  tests/test_services.py \
  tests/test_url_policy.py \
  tests/test_checker.py \
  tests/test_transport.py \
  tests/test_scheduler.py \
  tests/test_monitoring_api.py \
  tests/test_observability.py \
  --junitxml=test-results/backend-unit.xml

# Na raiz da cópia limpa:
ruff check scripts
ruff format --check scripts

# No repositório de trabalho:
git diff --check
```

| Validação local final | Resultado | Evidência |
| --- | --- | --- |
| Instalação limpa com constraints e `[dev]` | PASS | Instalação editable concluída; 49 versões conferidas |
| `ruff check .` | PASS | All checks passed |
| `ruff format --check .` | PASS | 52 arquivos já formatados |
| `mypy` | PASS | Sem problemas em 52 arquivos |
| `python -m pip check` | PASS | No broken requirements found |
| Ruff dos scripts da raiz | PASS | Lint aprovado; quatro arquivos já formatados |
| Suíte selecionada | PASS | 184 passed em 9.60s; zero failures/errors/skips |
| JUnit | PASS | XML parseado; 184 casos, zero failures/errors/skips |
| Seleção vazia | PASS | Mesmo comando acrescido de filtro sem correspondência retorna 5 |
| YAML e estrutura | PASS | PyYAML existente, asserts de triggers/permissões/jobs/needs/cache/SHAs |
| Shell | PASS | `bash -n` e ShellCheck nos dez blocos `run` |
| Agregador | PASS | Execução do bloco exato nas 16 combinações de status, mais needs vazio e futura dependência falha |
| `git diff --check` | PASS | Sem erros de whitespace |
| Validação integral com actionlint | BLOCKED | Ferramenta indisponível; não instalada |
| Execução real no GitHub Actions | NOT RUN | Pendente de revisão e publicação autorizada |
| PostgreSQL, stack, frontend, Playwright e scanners | NOT RUN | Fora da Fase B |

Evidências temporárias locais, fora do Git:

- `/tmp/infrapulse-s06b-final/evidence/summary.json`.
- `/tmp/infrapulse-s06b-final/evidence/*.log` e blocos shell validados.
- `/tmp/infrapulse-s06b-final/backend/test-results/backend-unit.xml`.
- `/tmp/infrapulse-s06b-final/backend/test-results/empty-selection.xml`.
- Helper local: `/tmp/infrapulse-s06b-validate.py`.

Ocorrências de preparação: o sandbox falhou antes de executar a inspeção, exigindo
execução com aprovação fora dele. Uma tentativa inicial sem caminho explícito
interpretou `backend[dev]` como pacote do índice; esse pacote foi desinstalado do
ambiente temporário e a tentativa descartada como validação. Uma primeira versão
do helper acrescentou o filtro negativo após uma quebra de linha e retornou 127;
o helper foi corrigido e o teste final retornou 5. Essas ocorrências não alteraram
o ambiente local existente nem o comando pytest do workflow.

## Segurança, limites e riscos residuais

- Triggers somente para PRs destinados a `develop`/`main` e pushes nessas branches;
  sem filtros de caminhos que omitam o gate obrigatório.
- `permissions: contents: read`; ambos os checkouts com `persist-credentials: false`.
- Todas as actions fixadas por SHA completo e versão legível em comentário:
  checkout v4.2.2, setup-python v5.6.0, upload-artifact v4.6.2.
- Cache pip deriva de `backend/pyproject.toml` e `backend/requirements.lock`.
- Concurrency por workflow/referência com cancelamento de execuções obsoletas.
- Sem `pull_request_target`, secrets, publicação de pacotes/imagens,
  `continue-on-error`, `|| true`, filtros de warnings ou cobertura arbitrária.
- JUnit é o único artefato enviado; não há upload de `.env`, diretórios de trabalho
  ou credenciais. O JSON de resultados é passado ao gate por variável de ambiente.
- Não foi necessário corrigir código por incompatibilidade com Ubuntu 24.04.
- PyYAML, asserts, Bash e ShellCheck não substituem a validação do schema e das
  expressões pelo GitHub. Checkout, cache, download de Python, upload, concurrency
  e agendamento/cancelamento reais ainda precisam ser exercitados remotamente.
- A lista explícita deve ser revisada quando novos módulos de teste forem criados;
  ela falha com seleção vazia, mas não inclui automaticamente módulos futuros.
- O lock existente não fixa o backend de build por versão exata nem hashes dos
  pacotes; Python 3.12 acompanha atualizações de patch e a imagem do runner evolui.
  A instalação usa a política existente, sem antecipar mudanças de supply chain.
- Sockets locais e geração de certificados dependem das condições do runner e do
  OpenSSL. Foram exercitados localmente; não equivalem a execução hospedada.

## Git e recomendação

Branch e HEAD preservados: `develop`,
`709a4a76f6d57314426d7c92f9799d0c7cdaaaca`.
Somente os dois arquivos novos desta fase ficam não rastreados para revisão.
Sem commit, push, checkpoint manual, mudança de hooks/configuração Entire,
migração, comandos Docker, alteração de containers, volumes, redes, credenciais,
branch protection, secrets ou configurações remotas.

Recomendação: pronta para revisão e posterior validação remota autorizada.
A Fase B só deve receber aprovação definitiva após sucesso do workflow no GitHub
Actions. Nenhuma fase seguinte foi iniciada.

Conventional Commit sugerido, sem executar:
`ci(backend): add quality and unit gates for sprint 06`.
