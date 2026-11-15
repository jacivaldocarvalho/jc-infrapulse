# Sprint 06 — Fase D2: Playwright E2E e observabilidade CI

Data: 2026-10-30. Implementação publicada e execução remota
**41067075376: FAIL**. Correção posterior do isolamento do teste validada somente
localmente, sem staging, commit ou push nesta rodada. Uma nova execução remota
completa continua obrigatória antes da aprovação da D2. As seções anteriores à
última correção preservam o histórico das rodadas locais e da publicação.

## Baseline, inspeção e problemas encontrados

Branch `develop`, HEAD preservado em
`8598ab8a6c3090360ced5401403e37fc4872fd4d`; working tree inicialmente limpa.
Foram lidos AGENTS.md, guia Entire, workflow, validadores, Composes, dependências,
configuração Playwright, cenários E2E, fixtures e testes reais de observabilidade.
Brain foi consultado, mas seu índice está atrasado; o código atual foi a fonte de
verdade. Não houve refresh, reparo ou alteração de configuração Entire.

Problemas documentados antes das correções:

| Problema | Correção restrita à infraestrutura de testes |
| --- | --- |
| Validador E2E exigia branch develop | Removida dependência de branch/Git; validado em cópia sem .git |
| Verificação do Compose principal resolvia backend/.env | Runner valida somente seu Compose descartável com --env-file /dev/null |
| E2E exigia 8080 livre, ocupada pelo frontend local | Docker publica porta dinâmica em 127.0.0.1; runner descobre E2E_BASE_URL |
| Subnets fixas 172.30.240/241 | Removidas; Docker aloca redes; autorização de execução usa IP real do alvo /32 |
| Nomes sem preflight/ownership explícitos | Nome exclusivo, checagem de recursos e marker criado com modo exclusivo |
| Observabilidade repetia a suíte completa PostgreSQL | D1 preservada como gate separado; D2 executa somente a stack real |
| Seleção ampla de testes produzia skips | Seleção dos dois node IDs exatos; JUnit rejeita skip, falha, erro ou ausência de caso |
| Reinício do Collector sem verificar recuperação | Repete teste real completo após religar Collector; verifica shutdown/reinício do worker |
| Ausência de JUnit Playwright | Reporter JUnit adicionado, mantendo HTML, traces e screenshots |

## Arquivos

Modificados:

- `.github/workflows/ci.yml`.
- `compose.frontend.test.yaml` e `compose.observability.test.yaml`.
- `frontend/playwright.config.ts`.
- `scripts/validate-dashboard.py` e `scripts/validate-observability.sh`.
- `README.md`, `docs/frontend.md`, `docs/observability.md`.

Criados:

- `scripts/integration_support.py`: lifecycle compartilhado sem dependências novas.
- `scripts/test_integration_support.py`: 20 testes unittest após as correções,
  incluindo os oito testes de lifecycle originais, sem acessar Docker.
- `scripts/validate-observability.py`: orquestra validadores e cenários reais.
- `docs/sprint06-phase-d2-report.md`.

Não foram modificados aplicação, worker, política SSRF, migrações, Dockerfiles,
Compose de produção, dependências/locks, testes Playwright ou testes backend.
O wrapper shell de observabilidade continua disponível e delega ao runner Python.
Os sete jobs de validação anteriores foram comparados estruturalmente com HEAD
e permanecem idênticos. Só a lista needs do agregador foi estendida.

## Jobs

| Job | Ambiente e execução | Limites |
| --- | --- | --- |
| playwright-e2e | Ubuntu 24.04, Python 3.12, Node 24.18.1, npm ci, Chromium da versão Playwright travada, stack real e oito cenários | Job 35 min, browser install 5 min, integração 25 min, cleanup 3 min |
| observability-integration | Ubuntu 24.04, Python 3.12, Docker Compose, validadores, cenários normal/outage/recuperação e correlação | Job 35 min, integração 25 min, cleanup 3 min |

Playwright instala Chromium/dependências do sistema com
`npm exec --no -- playwright install --with-deps chromium`, após npm ci em frontend.
Não resolve uma versão arbitrária do Playwright. Node 24.18.1 coincide com Dockerfile
e engines existentes. Ambos os jobs executam os testes de segurança do lifecycle.
Pip não é instalado no host desses jobs; dependências backend vêm dos Dockerfiles
e constraints existentes.

O frontend E2E é servido pelo Nginx real, ligado à API e ao PostgreSQL descartável.
O Dockerfile existente exporta o OpenAPI do mesmo checkout, gera tipos e compila
o frontend. Não há cópia manual de schema nem consumo de outro commit/run.

Os testes E2E são executados sem alterações: CRUD real/worker UP; histórico,
métricas/p95/gráficos; estados vazios; filtros/paginação/erro de atualização;
allowlist do proxy e SSRF; teclado/mobile/temas/axe; duas regressões de exclusão.
As interceptações de rede que já existiam nos testes continuam limitadas à injeção
de falha/latência; nenhuma integração real foi substituída por mocks. As verificações
existentes de pageerror e de ausência de novas leituras após DELETE foram preservadas.

Observabilidade executa, em sequência:

1. Build dos serviços existentes e startup do alvo controlado.
2. Promtool check config/check rules/test rules, Collector validate e amtool check-config,
   usando as imagens e mounts do próprio projeto Compose.
3. PostgreSQL em tmpfs, migração e startup de API, worker, Collector, Tempo,
   Prometheus, Grafana, Alertmanager e destino HTTP.
4. Verificação de portas/rede e `test_real_observability_stack`, com um caso e zero skips.
5. Extrai IDs reais dos logs estruturados API/worker e confirma que existem no Tempo.
6. Para Collector e executa somente `test_collector_outage_real_processes`.
7. Religa Collector e repete o teste real completo, incluindo métricas, traces,
   queries dos dashboards/datasources e entrega do alerta de integração.
8. Para worker, exige worker_stopped/scheduler_stopped nos logs e religa com --wait.

Grafana usa usuário admin e banco/usuário PostgreSQL definidos explicitamente no
ambiente descartável. Uma execução final com valores conflitantes no ambiente
do processo comprovou que eles não são herdados pela stack.

## Isolamento e cleanup

Cada job usa `INFRAPULSE_TEST_PROJECT` com prefixo próprio, run ID e run attempt.
Localmente o runner gera sufixo aleatório, sem depender do nome da pasta/branch.
Antes de criar recursos, recusa containers, redes ou volumes com o mesmo projeto.
Um marker em RUNNER_TEMP (ou /tmp local) é criado atomicamente; não contém senha.

Senhas efêmeras são geradas em memória e mascaradas antes de comandos Docker no
GitHub. Não há `.env`, arquivo de credenciais, dump de ambiente ou config resolvida
nos logs. Os Composes usam --env-file /dev/null e nomes de projeto explícitos.

Redes são alocadas pelo Docker, sem IPAM fixo. Depois de iniciar monitoring-target,
o runner obtém seu IP e autoriza somente esse /32, hostname exato e porta prevista.
Não amplia o acesso do checker à rede privada do runner. Código SSRF não foi alterado.

E2E publica somente Nginx em loopback, com porta dinâmica, usuário 101 e filesystem
readonly. API, worker, destino e PostgreSQL não publicam portas. O banco permanece
na rede data interna; worker alcança o destino pela rede targets interna.

O override de observabilidade remove bridges de saída desnecessárias de API,
worker e Grafana. O banco usa somente internal; os serviços de telemetria usam
telemetry interna. Nenhuma porta administrativa, de métricas ou PostgreSQL é publicada.
Volumetria de telemetria é exclusivamente do projeto descartável; banco usa tmpfs.

O context manager executa down --volumes --remove-orphans em sucesso ou falha,
confirma ausência de recursos com o label do projeto e compara IDs, StartedAt,
RestartCount e estado dos containers preexistentes. SIGTERM encerra via finally.
O workflow também possui passo always() de fallback --cleanup, com o mesmo nome
de projeto e marker. Sem marker de ownership, não executa down.
Falhas de cleanup ou mudança de containers preexistentes reprovam a validação.
Não há prune, alteração de firewall, container privilegiado ou comando contra a stack local.

## Relatórios e artefatos

- `playwright-e2e-results`: frontend/test-results e frontend/playwright-report,
  com JUnit em test-results/playwright.xml; traces/screenshots em falhas e
  screenshots/timelines que os próprios cenários já produzem.
- `observability-integration-junit`: somente normal.xml, outage.xml e recovery.xml
  em RUNNER_TEMP/observability-results, obtidos dos containers de teste por docker cp.

Uploads usam always(), retenção de sete dias e erro para arquivo ausente.
Não incluem banco, volumes, marker, ambiente ou credenciais. Os arquivos finais
foram inspecionados: sem .env, symlinks ou padrões de tokens/chaves privadas.
Traces contêm somente tráfego e fixtures da stack descartável, não a interface
administrativa Grafana. A inspeção por padrões não equivale a auditoria exaustiva.

## Validação local

Cópia limpa em `/tmp/infrapulse-s06d2-clean`, criada a partir dos arquivos versionados
e das alterações locais autorizadas, sem .git, .env, node_modules ou tipos gerados.
`npm ci` instalou 370 pacotes e reportou zero vulnerabilidades na auditoria incidental.
Chromium 1248 já disponível no host foi reutilizado; não foram instalados pacotes
do sistema no ambiente local. O comando --with-deps do CI ainda requer execução remota.

| Verificação executada | Resultado |
| --- | --- |
| Ruff check/format dos sete scripts Python | PASS |
| unittest do lifecycle | PASS — 8 casos: nome, conflito, daemon, ownership, falha e preservação do erro |
| TypeScript, ESLint --max-warnings=0 e Prettier | PASS |
| Geração OpenAPI/tipos do checkout limpo | PASS |
| Build Docker Nginx/API/worker/observabilidade | PASS — Dockerfiles existentes |
| Playwright isolado | PASS — 8 casos em 28,6 s, sem falhas, erros ou skips |
| Regressões E2E de exclusão | PASS — zero leituras automáticas novas; cancelamento anterior ao DELETE observado |
| Acessibilidade/teclado/mobile/temas | PASS — cenário original e axe preservados |
| Promtool, Collector validate e amtool | PASS |
| Observabilidade normal | PASS — 1 caso, 52,11 s, zero skips |
| Collector indisponível | PASS — 1 caso, 1,09 s, zero skips; API pronta e UP persistido pelo worker |
| Recuperação do Collector | PASS — repetição do caso real, 1,73 s, zero skips |
| Correlação log/trace | PASS — IDs de API e worker encontrados no Tempo |
| Worker shutdown/restart | PASS — logs de parada e startup com --wait |
| JUnit/artefatos finais | PASS — 8 casos Playwright e três XMLs de um caso cada |
| Cleanup e containers preexistentes | PASS — recursos descartáveis ausentes; IDs/start times/restarts/estado preservados |
| Agregador | PASS — 262.144 combinações, somente nove sucessos aprovam |
| actionlint, bash -n, ShellCheck e git diff --check | PASS |
| Instalação local de pacotes OS via --with-deps | NOT RUN — host já executa Chromium; sem alteração do ambiente |
| GitHub Actions D2 | NOT RUN — não autorizado commit/push |

Não ficou verificação final em FAIL ou BLOCKED. Limitações remotas permanecem
identificadas como NOT RUN.

A primeira tentativa local executou ambos os validadores em paralelo no mesmo
daemon. O Playwright aprovou sete casos e falhou no cenário de atualização:
o trace mostrou 17 respostas net::ERR_NETWORK_CHANGED enquanto a outra stack
removia redes, além do único 503 intencional. A guarda detectou que os containers
temporários da observabilidade, presentes no snapshot inicial do E2E, desapareceram.
Ambas as stacks foram removidas. Evidências de falha foram preservadas em /tmp.
O E2E foi repetido sozinho e passou sem modificar testes, asserções ou produto.
No GitHub, os jobs usam runners separados; localmente devem rodar sequencialmente.

Houve ainda uma tentativa de npm ci no diretório raiz, recusada por ausência de
lock; foi repetida em frontend na cópia temporária. Nenhuma dependência do ambiente
local existente foi atualizada.

Comandos principais:

```bash
npm --prefix frontend ci
# No CI, dentro de frontend:
npm exec --no -- playwright install --with-deps chromium
python3 scripts/test_integration_support.py
python3 scripts/validate-dashboard.py
bash scripts/validate-observability.sh
# Fallback CI, com INFRAPULSE_TEST_PROJECT idêntico ao da execução:
python3 scripts/validate-dashboard.py --cleanup
bash scripts/validate-observability.sh --cleanup
```

Evidências locais fora do Git:

- `/tmp/infrapulse-s06d2-clean/evidence/playwright-final.log`.
- `/tmp/infrapulse-s06d2-clean/frontend/test-results/playwright.xml` e playwright-report.
- `/tmp/infrapulse-s06d2-clean/evidence/observability-final.log`.
- `/tmp/infrapulse-s06d2-clean/evidence/observability-final/{normal,outage,recovery}.xml`.
- `/tmp/infrapulse-s06d2-clean/evidence/first-test-results/`, com trace/screenshot da falha inicial.
- `/tmp/infrapulse-s06d2-final-check.py`, helper da verificação de estrutura/agregador/artefatos.

## Quality gate, segurança e riscos residuais

ci-required mantém o nome público, always() e código anterior. Exige os sete jobs
existentes mais playwright-e2e e observability-integration. O bloco exato foi
avaliado nas 262.144 combinações de success/failure/cancelled/skipped: apenas nove
sucessos aprovam. Falhas e skips das novas dependências impedem aprovação.

Permissões contents: read, triggers, concurrency, timeouts, checkout sem credenciais
persistidas e SHAs completos foram preservados. Cache npm usa package-lock.json.
Nenhuma action foi atualizada; os warnings herdados de Node.js 20 podem reaparecer.
Não há pull_request_target, secrets de produção, publicação/deploy ou supressões
para obter PASS. Avisos de pip no build, initdb e NO_COLOR/FORCE_COLOR foram
mantidos; não foram instaladas dependências novas nem desativadas validações.

O runner precisa de Compose com suporte aos tags !reset/!override dos Composes de
teste. Isso passou no Compose 5.6.0 local; execução hospedada ainda é necessária.
Perda forçada do runner pode impedir cleanup, apesar de finally e fallback always().
Builds e downloads dependem dos registros; tags Docker existentes continuam mutáveis.
Imagens/cache locais permanecem; containers, redes e volumes descartáveis foram removidos.
Validadores não devem compartilhar um daemon simultaneamente com Chromium ativo.

Branch e HEAD permanecem no baseline. As mudanças estão restritas aos 13 arquivos
listados, sem staging, commit ou push. Entire permanece habilitado e informa um
checkpoint pendente da D1 (`01M66BC9ERQK3REFAJC1FCWQ1M`), sem erro de sincronização;
nenhum checkpoint manual, reparo, alteração de hooks ou sincronização foi executado.
Frontend, API e PostgreSQL locais mantiveram seus IDs e permaneceram saudáveis.

Recomendação: pronta para revisão local e posterior autorização de versionamento
e validação remota. Não considerar D2 definitivamente aprovada antes do sucesso
dos nove gates e do agregador no GitHub Actions. Nenhuma fase posterior foi iniciada.

Conventional Commit sugerido, sem executar:
`ci(integration): add Playwright and observability gates`.

## Correção local de propagação de erros — revisão D2

Esta rodada corrige exclusivamente os validadores e seus testes. Não houve staging,
commit, push, execução de Docker nem nova execução de Playwright/observabilidade.
Os resultados de integração acima são históricos; a D2 continua sem aprovação remota.

### Defeitos, causa raiz e correção

- **A:** ambos os entrypoints chamavam `main()` diretamente. Um
  `CalledProcessError` não tratado fazia o interpretador encerrar com 1, descartando
  o código original, inclusive 17. Ambos agora usam `run_cli`, que registra o
  diagnóstico com traceback e propaga códigos de subprocesso entre 1 e 255.
- **B:** o `docker cp` no `finally` de `run_case` podia substituir a exceção do
  pytest. A coleta agora ocorre em bloco separado: sua falha é registrada e a
  exceção primária original é relançada. Sem falha primária, a falha de coleta
  continua impedindo sucesso. A mesma precedência foi aplicada ao encerramento
  do projeto: cleanup e inspeção são tentados separadamente, todos os erros
  secundários são registrados e nenhum substitui uma causa primária existente.
- O logger mascara os valores das três credenciais efêmeras geradas pelo projeto
  antes de emitir mensagens/tracebacks. A saída capturada do teste também passa
  pelo mascaramento. Não foram adicionadas dependências ou supressões de erros.

Política de saída: sucesso normal retorna 0; códigos válidos 1–255 são preservados.
Status negativos de sinais válidos são convertidos para `128 + sinal`, conforme
convenção do shell: SIGTERM (-15) resulta em 143 e SIGKILL (-9) em 137. Um
entrypoint não consegue representar diretamente o status negativo de subprocesso.
Exceções inesperadas sem código numérico e `CalledProcessError` malformados
(0 ou fora da faixa representável) resultam em 1, com diagnóstico, evitando sucesso
acidental por truncamento do status pelo sistema operacional.

Arquivos alterados nesta correção, todos já pertencentes aos 13 arquivos da D2:

- `scripts/integration_support.py`: entrypoint, mascaramento e precedência no cleanup.
- `scripts/validate-dashboard.py`: adoção do entrypoint compartilhado.
- `scripts/validate-observability.py`: entrypoint e coleta sem sobrescrever a causa primária.
- `scripts/test_integration_support.py`: regressões determinísticas sem Docker.
- `docs/sprint06-phase-d2-report.md`: este registro.

### Matriz de regressões e evidências

| Cenário | Resultado observado | Status |
| --- | --- | --- |
| Entrypoints: 0, 1, 17 e 127 | Código idêntico nos dois validadores | PASS |
| Sinais SIGTERM/SIGKILL | 143/137 nos dois validadores | PASS |
| CalledProcessError inválido: 0, 256, 512 | Falha com código 1 e diagnóstico | PASS |
| Teste 17; coleta bem-sucedida | Exceção original preservada; CLI propaga 17 | PASS |
| Teste 17; coleta 127 | Causa original preservada; coleta registrada | PASS |
| Teste passa; coleta 127 | Exceção de coleta propagada; CLI propaga 127 | PASS |
| Teste e coleta passam | Sucesso | PASS |
| Exceção inesperada na coleta, com/sem falha primária | Precedência e diagnóstico preservados | PASS |
| Primária 17, cleanup 23 e inspeção inesperada | Primária preservada; ambos os erros secundários registrados | PASS |
| Sem primária, cleanup 23 | Falha secundária propagada | PASS |
| Traceback, mensagens e credencial sintética | Diagnóstico útil preservado e credencial mascarada | PASS |

Antes da correção, os 20 testes executados produziram 18 subcasos com falha,
demonstrando os defeitos. Após a correção, os mesmos 20 testes passaram em 2,366 s:
oito testes de lifecycle existentes e 12 novos métodos de regressão com subcasos.
A comparação da AST confirmou que a classe original dos oito testes permanece
inalterada. Os entrypoints são exercitados em subprocessos Python controlados;
coleta e lifecycle usam mocks. Nenhum teste de integração existente foi alterado.

| Validação desta rodada | Resultado |
| --- | --- |
| Regressões antes da correção | FAIL esperado — 18 subcasos reproduziram os defeitos |
| Regressões e oito testes de lifecycle após correção | PASS — 20 testes |
| Ruff check e Ruff format --check em scripts | PASS |
| ShellCheck e bash -n no wrapper de observabilidade | PASS |
| actionlint no workflow atual | PASS |
| ci-required | PASS — 32 cenários com o código exato do agregador |
| git diff --check, incluindo arquivos novos | PASS |
| Repetição de Docker, Playwright e stack de observabilidade | NOT RUN — testes determinísticos suficientes para esta correção |
| GitHub Actions D2 | NOT RUN — sem autorização de versionamento nesta rodada |

Comandos de validação:

```bash
python3 scripts/test_integration_support.py
backend/.venv/bin/ruff check --no-cache scripts
backend/.venv/bin/ruff format --check --no-cache scripts
shellcheck scripts/validate-observability.sh
bash -n scripts/validate-observability.sh
/tmp/infrapulse-s06b-actionlint-feulusd_/actionlint -color .github/workflows/ci.yml
git diff --check
# Arquivos novos: git diff --no-index --check /dev/null <arquivo>
```

O workflow não foi alterado nesta rodada. A verificação adicional do agregador
confirma os nove nomes obrigatórios, `always()` e o código efetivamente usado:
sucesso completo; dependências vazias; failure/cancelled/skipped de cada job;
falha/cancelamento/skip do contrato com frontend-quality/frontend-build pulados.
Somente o cenário com todos os nove sucessos aprova. Foram 32 cenários nesta
revalidação, além da matriz exaustiva histórica documentada acima.

Evidências temporárias: `/tmp/infrapulse-s06d2-error-fix/red-tests.log`,
`green-tests.log` e `validation-summary.json` no mesmo diretório. O manifesto
`before-files.json` permite confirmar que os outros oito arquivos D2 foram
preservados nesta rodada. Esses arquivos temporários não são artefatos publicados.

Git permanece em `develop`, HEAD `8598ab8a6c3090360ced5401403e37fc4872fd4d`,
com os 13 arquivos D2 pendentes e nenhum arquivo staged. Entire foi consultado
somente em leitura: habilitado, sessão ativa, um checkpoint anterior da D1
pendente. O Brain ainda usa o seed `bf315771`, com freshness `unsafe` por atraso;
o código atual foi a fonte de verdade. Não houve repair, refresh, sincronização
forçada, checkpoint manual ou alteração de hooks/configuração.

Riscos restantes: esta rodada valida a propagação com processos controlados e
mocks, sem repetir os cenários reais de infraestrutura. O mascaramento cobre as
credenciais geradas pelo projeto; não é um sanitizador universal de saídas externas.
A execução real no GitHub Actions continua necessária. Recomendação: retomar a
revisão final antes de solicitar nova autorização de staging, commit e push.

## Revalidação sequencial para publicação controlada

Após nova autorização explícita de um único commit/push, os 13 arquivos foram
revisados novamente em `develop`, HEAD `8598ab8a6c3090360ced5401403e37fc4872fd4d`.
Os validadores já corrigidos foram executados sequencialmente, sem alterações
adicionais de implementação, na cópia temporária
`/tmp/infrapulse-s06d2-publication-7wtydhk9`, sem `.git` ou `.env`. As dependências
frontend e Chromium já instalados na cópia temporária anterior foram reutilizados;
nenhum ambiente local foi reinstalado. Um manifesto SHA-256 confirmou a identidade
dos 13 arquivos antes desta atualização documental.

| Verificação | Resultado |
| --- | --- |
| Regressões e lifecycle | PASS — 20 testes em 2,589 s |
| Ruff check e Ruff format --check em scripts | PASS — sete arquivos formatados |
| actionlint, ShellCheck e bash -n, incluindo blocos shell do workflow | PASS |
| ci-required | PASS — 262.144 combinações; somente nove sucessos aprovam |
| Sete jobs de validação anteriores, permissões, triggers, concurrency e SHAs | PASS — preservados |
| Playwright | PASS — oito cenários, 23,4 s; zero falhas, erros ou skips |
| Exclusão de serviços | PASS — duas regressões, zero novas leituras automáticas após sucesso e cancelamento da leitura pendente |
| Acessibilidade | PASS — cenário original desktop/mobile, teclado, ambos os temas e axe |
| Observabilidade normal | PASS — um teste em 50,82 s; zero skips |
| Collector indisponível | PASS — um teste em 1,07 s; zero skips |
| Recuperação do Collector | PASS — teste real repetido em 1,46 s; zero skips |
| Correlação logs/Tempo | PASS — IDs reais da API e worker encontrados no Tempo |
| Worker e scheduler | PASS — encerramento gracioso e reinício saudável |
| Exit codes dos dois validadores | PASS — ambos encerraram com zero |
| Cleanup e recursos preexistentes | PASS — containers, redes e volumes preservados |
| Artefatos locais | PASS — 13 arquivos inspecionados; JUnit com 8 + 1 + 1 + 1 casos, sem skips |
| git diff --check, incluindo os quatro arquivos novos | PASS |
| GitHub Actions D2 | NOT RUN neste registro anterior ao commit/push |

Projetos exclusivos: `infrapulse-e2e-publication-7wtydhk9` e
`infrapulse-observability-publication-7wtydhk9`. Antes de cada execução foram
confirmadas a ausência de containers/redes/volumes com o label do projeto e a
ausência de marker conflitante. E2E publicou somente Nginx em
`127.0.0.1:32770`; PostgreSQL e observabilidade não publicaram portas. O cleanup
removeu todos os recursos e markers próprios. A comparação final confirmou IDs,
StartedAt, RestartCount e estado dos cinco containers preexistentes, além das
identidades das redes e volumes. Não houve execução paralela dos validadores.

Evidências no diretório temporário acima: `evidence/playwright.log`,
`frontend/test-results/playwright.xml`, `frontend/playwright-report/`,
`evidence/observability.log`, `evidence/observability/{normal,outage,recovery}.xml`,
`evidence/results.json`, snapshots anteriores a cada integração e manifesto dos
arquivos. A inspeção dos artefatos não encontrou `.env`, symlinks ou padrões de
tokens, chaves privadas e cabeçalhos de autenticação. Essa inspeção é limitada
aos arquivos produzidos e aos padrões verificados, sem equivaler a auditoria
universal de dados sensíveis. O aviso NO_COLOR/FORCE_COLOR do Playwright permaneceu
visível; não houve `ERR_NETWORK_CHANGED` nesta execução sequencial.

Entire: `doctor` confirmou hooks íntegros e nenhuma sessão presa. `status` indica
o checkpoint D1 `01M66BC9ERQK3REFAJC1FCWQ1M` pendente de sincronização normal no
próximo push. Metadados e transcript local (282.514 bytes) estão presentes; não
foi identificado risco concreto de perda de rastreabilidade. O Brain permanece
atrasado em `bf315771`; o código atual foi a fonte de verdade. Nenhum repair,
checkpoint manual, mudança de hooks ou sincronização forçada foi executado.

`origin/develop` foi consultado e ainda corresponde ao baseline. Após a revisão
do staged diff, a publicação autorizada deve incluir somente os 13 arquivos D2,
preservando os hooks Entire. O sucesso local permite prosseguir com essa publicação;
o encerramento da D2 depende dos dez jobs no GitHub Actions. Não iniciar a Fase E.

## Correção local do isolamento do teste — após execução remota

Baseline desta rodada: `develop`, HEAD
`ef547b4ab61512974c8efb38c2026f78b58df938`, working tree inicialmente limpa.
A execução [41067075376](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41067075376)
terminou em FAIL: oito jobs aprovados, `observability-integration` e `ci-required`
reprovados. O teste real de observabilidade não iniciou no runner remoto.

Causa confirmada antes de modificar o teste: `run_entrypoint` copiava todo
`os.environ` para o subprocesso. O teste
`test_unexpected_failure_diagnostic_redacts_project_password` constrói
`IntegrationProject('e2e', [])`, mas herdava o nome
`infrapulse-observability-41067075376-1` do job. A guarda corretamente rejeitou
o prefixo incompatível com `ValueError: Unexpected disposable project name`,
antes de gerar a credencial e o diagnóstico esperado. Os dois subcasos afetados
são `validate-dashboard.py` e `validate-observability.py`, no mesmo método de teste.

Reprodução anterior à correção, sem Docker:

```bash
INFRAPULSE_TEST_PROJECT=infrapulse-observability-41067075376-1 \
GITHUB_ACTIONS=true python3 scripts/test_integration_support.py \
  EntrypointStatusTests.test_unexpected_failure_diagnostic_redacts_project_password
```

Resultado: FAIL esperado, código 1, um método com dois subcasos falhando por
ausência de `preserved diagnostic`; evidência em
`/tmp/infrapulse-s06d2-isolation-red.log`. A guarda de segurança funcionou;
o defeito está na herança de ambiente do teste, não na validação de produção.

Correção aplicada somente em `scripts/test_integration_support.py`: o helper
`run_entrypoint` passou a fornecer um dicionário de ambiente explícito com apenas
`INFRAPULSE_TEST_PROJECT=infrapulse-e2e-unit` e `GITHUB_ACTIONS=false`, em vez de
copiar o ambiente do processo pai. O interpretador é chamado pelo caminho absoluto
de `sys.executable`; esses cenários não precisam de PATH, HOME, variáveis CI ou
credenciais herdadas. CPython pode acrescentar `LC_CTYPE` na inicialização para
coerção de locale; a regressão reconhece somente essa adição do runtime.
Não há alteração global de `os.environ` na correção.

Foram adicionados dois métodos de regressão: um inspeciona o ambiente efetivo do
filho nos dois entrypoints, rejeita chaves adicionais e compara o ambiente do pai
antes/depois; outro instancia a classe real em subprocessos fora do helper
sintético e comprova que um projeto válido passa, enquanto prefixo de
observabilidade, nome sem prefixo e nome com maiúsculas continuam rejeitados.
Os 20 testes anteriores, incluindo códigos de saída, sinais, coleta, cleanup e
mascaramento de credencial gerada, permanecem preservados.

Red-green: com os dois métodos novos e antes da troca do ambiente, a suíte de
22 testes falhou em quatro subcasos no ambiente CI. Após a correção, os mesmos
22 testes passaram em cada uma das cinco configurações abaixo (110 execuções de
métodos no total), sempre sem Docker:

| Ambiente do processo de testes | Resultado após correção |
| --- | --- |
| Ambiente vazio; INFRAPULSE_TEST_PROJECT ausente | PASS — 22 testes, 2,613 s |
| Projeto válido infrapulse-e2e-parent-valid | PASS — 22 testes, 2,679 s |
| CI observabilidade: infrapulse-observability-41067075376-1 | PASS — 22 testes, 2,514 s |
| Projeto inválido jc-infrapulse | PASS — 22 testes, 2,185 s; guarda direta rejeita o nome |
| CI Playwright: infrapulse-e2e-41067075376-1 | PASS — 22 testes, 2,396 s |

Os ambientes equivalentes ao CI incluem `CI=true`, `GITHUB_ACTIONS=true`, run ID,
run attempt, RUNNER_TEMP, três senhas exclusivamente sintéticas e uma variável
sentinela. O teste confirma que nenhuma dessas entradas extras alcança o filho
sintético e que o pai permanece inalterado. O teste de mascaramento confirma
diagnóstico útil, saída não zero e ausência da credencial gerada no stderr.
O nome inválido no ambiente externo não é aceito pela classe real; o sucesso da
suíte ocorre porque somente o subprocesso sintético recebe um nome válido explícito.

Validações adicionais: Ruff check, Ruff format --check (sete scripts Python),
actionlint, ShellCheck e git diff --check: **PASS**. Workflow, produção,
`integration_support.py`, SSRF, Dockerfiles, Compose e configuração Entire não
foram alterados. Docker e nova execução GitHub Actions: **NOT RUN** nesta rodada.

Evidências red: `/tmp/infrapulse-s06d2-isolation-red.log` e
`/tmp/infrapulse-s06d2-isolation-red-suite.log`. Evidências green:
`/tmp/infrapulse-s06d2-isolation-bfn9n05s/{clean-absent,valid-project,ci-observability,invalid-project,ci-playwright}.log`
e `results.json` no mesmo diretório. A matriz foi executada com
`subprocess.run([sys.executable, 'scripts/test_integration_support.py'], env=...)`,
substituindo integralmente o ambiente de cada processo de testes, sem modificar
o ambiente global do processo que orquestra as execuções.

Estado desta rodada: branch e HEAD preservados no baseline; somente o teste e
este relatório modificados, nada staged. Entire permanece habilitado, doctor sem
sessões presas ou falhas nos hooks. `status` voltou a indicar um checkpoint D2
pendente (`01M66HBTQ3KB1YWV8QM5RY8HV9`); o push anterior havia sincronizado os
checkpoints, portanto este é o estado local observado posteriormente, sem nova
tentativa de sincronização. Não houve checkpoint manual, repair ou alterações
de hooks/configuração.

Recomendação: pronta para nova revisão e autorização de um commit separado,
seguido de validação remota completa dos dez jobs. Esta correção não aprova a D2
remotamente e não inicia a Fase E. Conventional Commit sugerido, sem executar:
`test(ci): isolate integration validator subprocess environment`.
