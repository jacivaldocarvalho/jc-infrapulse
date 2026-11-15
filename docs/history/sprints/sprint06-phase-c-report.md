# Sprint 06 — Fase C: Frontend CI e contrato OpenAPI

Data: 2026-10-30. Status: implementação local concluída, aguardando revisão.
GitHub Actions da Fase C: **NOT RUN**. Sem commit ou push nesta fase.

## Escopo e estado inicial

Branch `develop`, HEAD inicial e final esperado
`bf315771bcf9d6c18a833bc0922aabe1114fac89`; working tree inicialmente limpa.
Foram lidos `AGENTS.md`, `.entire/agent-guide.md`, workflow, configurações do
frontend, lock, scripts de geração e testes de regressão. Entire Brain foi
consultado com `brief` e `recall`; a decisão de gerar tipos do checkout atual foi
conferida nas fontes e na documentação existente.

Arquivos desta fase:

- Modificado `.github/workflows/ci.yml`.
- Criado `docs/sprint06-phase-c-report.md`.

Não houve alterações em dependências/locks, contratos públicos, lógica de negócio,
worker, proteção SSRF, testes existentes, migrações, Dockerfiles ou Compose.
Os jobs `backend-quality` e `backend-unit` permaneceram estruturalmente idênticos
ao baseline; isso foi conferido comparando o YAML com o conteúdo de `HEAD`.

## Jobs e dependências

| Job adicionado | Dependências | Função |
| --- | --- | --- |
| `api-contract` | Nenhuma | Instala backend, exporta e valida OpenAPI, envia JSON |
| `frontend-quality` | `api-contract` | Instala frontend, baixa contrato, gera tipos, verifica qualidade |
| `frontend-tests` | Nenhuma | Vitest/Testing Library/MSW e relatório JUnit |
| `frontend-build` | `api-contract` | Instala frontend, baixa contrato, gera tipos e build, confere saída |

Os quatro jobs usam `ubuntu-24.04` e timeout de 15 minutos. `api-contract` usa
Python 3.12. Os três jobs frontend usam Node **24.18.1**, igual ao Dockerfile,
compatível com `engines.node: >=24.15.0 <25` no package.json/lock.
Ambiente local verificado: Python 3.12.3, Node 24.18.1, npm 11.16.0.

O diretório padrão global `backend` foi preservado. Os jobs frontend definem
`defaults.run.working-directory: frontend`; a exportação usa explicitamente a raiz.
O nome público `ci-required` permanece estável, com timeout de cinco minutos.

## Contrato e artefatos

1. Instalação do backend: `python -m pip install -c requirements.lock -e .` em
   `backend/`, sem o extra de desenvolvimento desnecessário para exportar o schema.
2. `python -B scripts/export-openapi.py` exporta `app.openapi()` do checkout atual,
   sem servidor, lifespan, sessão SQL ou banco. Não lê `backend/.env` nesse fluxo.
3. `fastapi.openapi.models.OpenAPI.model_validate_json` valida a estrutura usando
   uma dependência existente. O gate também exige OpenAPI 3, paths e component
   schemas não vazios. Não foi instalado outro validador.
4. O arquivo `$RUNNER_TEMP/api-contract/openapi.json` é enviado como artefato
   **`api-contract-openapi`**, com nome estável e retenção de sete dias.
5. Qualidade e build baixam esse nome usando o contexto padrão da action:
   mesmo repositório e **mesma execução**, sem token explícito ou seleção de outro
   run. O contrato não entra em cache. Um checkout novo sempre o exporta novamente.
6. `OPENAPI_SCHEMA_FILE` é definido nos passos consumidores; assim, o gerador
   existente lê o arquivo compartilhado. Contrato ausente causa erro, sem fallback
   para um schema antigo nem tentativa de iniciar API.

O job de qualidade executa `npm run generate:api`, `npm run typecheck`,
`npm run lint -- --max-warnings=0` e `npm run format:check`.
O build usa `npm run build`, que já regenera os tipos, roda typecheck e executa Vite.
Depois exige `dist/index.html`, JavaScript e CSS não vazios em `dist/assets`.
O build permanece no runner: não há upload de dist, publicação ou deploy.

`frontend-tests` executa o comando existente com reporters nativos:

```bash
npm test -- --reporter=default --reporter=junit --outputFile=test-results/frontend-unit.xml
```

O padrão de inclusão do Vitest permanece `src/**/*.test.{ts,tsx}`. Playwright/E2E
não é executado. Os testes usam jsdom, Testing Library e MSW; os imports de tipos
são apagados na transformação e não exigem arquivo gerado previamente.
O pytest da Fase B e os scripts de negócio não foram modificados.

O JUnit é enviado como **`frontend-unit-junit`**, com `always()`, retenção de sete
dias e erro para arquivo ausente. O Vitest é invocado diretamente pelo npm,
preservando falhas. Nenhuma opção permite suíte vazia nem mascara erros.

## Quality gate

`ci-required` exige exatamente:

- `backend-quality`
- `backend-unit`
- `api-contract`
- `frontend-quality`
- `frontend-tests`
- `frontend-build`

O job mantém `if: ${{ always() }}` e o código aprovado na Fase B: exige resultado
`success` de todas as entradas de `needs`, com pelo menos uma entrada.
Falhas, cancelamentos e skips nunca são interpretados como aprovação.

Foi executado o bloco Python exato do agregador nas **4.096 combinações** de
`success`, `failure`, `cancelled` e `skipped` para os seis jobs: somente a combinação
com todos em sucesso aprovou. O bloco shell completo também foi executado em três
cenários: contrato `failure`, `cancelled` ou `skipped`, com `frontend-quality` e
`frontend-build` em `skipped`. Todos retornaram código 1.

Esses testes validam a lógica local. Agendamento e cancelamento global de runs
continuam sujeitos ao GitHub; não se afirma que um run cancelado sempre conclua
o agregador. Esta fase ainda precisa exercitar seu grafo real na plataforma.

## Validação local executada

Foi criada uma cópia limpa com `git archive HEAD frontend backend scripts` em
`/tmp/infrapulse-s06c-clean`, sem `.env`, node_modules, dist ou tipos gerados.
O novo ambiente Python e o novo node_modules ficaram exclusivamente nessa cópia.
Ambientes locais existentes e containers não foram modificados.

Os passos `run` novos foram extraídos do workflow por um helper temporário e
executados com Bash, `-e` e `pipefail`. O Vitest rodou **antes** da exportação e da
geração de tipos, comprovando sua independência do contrato e do backend instalado.

| Verificação | Resultado | Evidência |
| --- | --- | --- |
| Instalação backend somente com dependências declaradas | PASS | Instalação editable com constraints concluída |
| `npm ci` em checkout limpo | PASS | 370 pacotes adicionados; auditoria incidental reportou zero vulnerabilidades |
| Exportação e validação OpenAPI | PASS | OpenAPI 3.1.0, 8 paths, 14 schemas, 12.528 bytes |
| Rejeição de contrato inválido/vazio | PASS | O validador real retorna erro em ambos os casos |
| Rejeição de arquivo de contrato ausente | PASS | Gerador retorna erro, sem fallback |
| `npm run generate:api` | PASS | Tipos gerados do arquivo compartilhado |
| `npm run typecheck` | PASS | TypeScript 5.9.3 sem erros |
| `npm run lint -- --max-warnings=0` | PASS | ESLint 10.13.0, zero warnings |
| `npm run format:check` | PASS | Prettier 3.9.10 aprovou os arquivos |
| Vitest 5.0.5 | PASS | 26 testes em 10 arquivos, em 4,27 s |
| JUnit frontend | PASS | 26 casos; zero failures, errors ou skips; XML parseado |
| Regressões de exclusão | PASS | Cinco casos de DeleteService.race e dois de deletion-state |
| `npm run build` | PASS | Vite 8.3.5; 1.689 módulos transformados, build em 750 ms |
| Verificação da saída de produção | PASS | index.html, JS e CSS não vazios; 12 arquivos no dist |
| Agregador | PASS | 4.096 combinações e três cenários shell com skips dependentes |
| Estrutura e segurança YAML | PASS | Permissões, SHAs, caches, diretórios, needs, timeouts e artefatos conferidos |
| Shell | PASS | `bash -n` e ShellCheck em todos os blocos run |
| actionlint 1.7.12 | PASS | Sem diagnósticos no workflow final |
| `git diff --check`, incluindo relatório não rastreado | PASS | Sem erros de whitespace |
| Backend quality/unit nesta fase | NOT RUN | Jobs intactos; validação remota aprovada no baseline da Fase B |
| GitHub Actions da Fase C | NOT RUN | Sem commit/push autorizado |
| Playwright, PostgreSQL e stack de observabilidade | NOT RUN | Fora do escopo desta implementação |

Nenhuma verificação local final ficou em FAIL ou BLOCKED. Não foi necessário
alterar configuração do frontend ou instalar novas ferramentas npm.
O aviso do npm sobre versão 12.2.1 disponível foi registrado, sem atualizar o npm.

Regressões existentes cobertas: nenhuma consulta de status após DELETE durante
revalidação lenta; cancelamento de consulta pendente antes do DELETE; pausa de
polling/manual refresh sem interromper outros serviços; cancelamento do diálogo;
falha do DELETE com retomada e retry; navegação antes da revalidação com nova visita
recebendo 404; invariantes de cache/paginação/filtros e isolamento entre QueryClients.

Evidências temporárias, fora do Git:

- `/tmp/infrapulse-s06c-clean/evidence/summary.json` e logs dos passos.
- `/tmp/infrapulse-s06c-clean/evidence/deletion-regressions.json`.
- `/tmp/infrapulse-s06c-clean/frontend/test-results/frontend-unit.xml`.
- `/tmp/infrapulse-s06c-clean/runner-temp/api-contract/openapi.json`.
- `/tmp/infrapulse-s06c-clean/frontend/dist/`.
- Helper `/tmp/infrapulse-s06c-validate.py`.

Durante a preparação, o actionlint identificou uso inválido de `runner.temp` no
env do job. A variável foi movida para env dos passos, onde o contexto é permitido;
o actionlint final passou. Uma primeira instalação isolada usou o caminho de
constraints relativo ao diretório errado; foi repetida corretamente e concluída.

## Segurança, actions e riscos residuais

Permissões globais `contents: read`, checkout sem credenciais persistidas,
concurrency, triggers e SHAs da Fase B foram preservados. Todos os novos usos de
actions têm SHA completo, versão legível e timeout no job. Cache npm deriva somente
do `frontend/package-lock.json`; cache pip considera pyproject e requirements.lock.
Não há `pull_request_target`, secrets de produção, supressão ampla, continue-on-error
ou gates com `|| true`.

Novas actions, conferidas nas tags e nos manifests oficiais:

- [setup-node v7.1.0](https://github.com/actions/setup-node/tree/v7.1.0), SHA
  `949feb2413d6458794dcd2491c4babbbce0c15c1`, runtime Node.js 24.
- [download-artifact v8.0.2](https://github.com/actions/download-artifact/tree/v8.0.2), SHA
  `9000827ccba6bdab643e8b6fd33ac0654aef8333`, runtime Node.js 24; consumo padrão
  restrito ao run atual e falha padrão para divergência de digest.

A [execução aprovada da Fase B](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41048526460)
registrou warnings de depreciação Node.js 20 para checkout v4.2.2, setup-python
v5.6.0 e upload-artifact v4.6.2, forçadas pelo runner a Node.js 24 e concluídas com
sucesso. Seus SHAs não foram atualizados nesta fase: não houve necessidade observada
de compatibilidade. A manutenção desses pins permanece recomendada em alteração
separada e autorizada; novos jobs que os usam podem repetir os avisos.

Foram inspecionados o JSON, o JUnit e os 12 arquivos do build: sem `.env`, symlinks,
padrões de tokens/chaves privadas, URLs com credenciais ou configuração de senha
do backend. Os uploads apontam exclusivamente para um JSON ou XML, sem globs,
árvores inteiras ou diretórios de ambiente. JUnit contém nomes dos testes e
metadados normais do executor, como hostname; não inclui o ambiente do processo.
A inspeção por padrões não constitui uma auditoria exaustiva de dados sensíveis.

A validação Pydantic e a geração TypeScript não substituem uma suíte de testes de
compatibilidade semântica da API. Os schemas continuam originados da aplicação.
O lock existente do backend não fixa o build backend por versão exata ou hashes;
essa política não foi ampliada. O npm usa o lock existente sem resolver versões
arbitrárias; a auditoria incidental não substitui uma fase dedicada de segurança.

Upload/download reais, caches e o grafo dos novos jobs ainda precisam ser verificados
no GitHub Actions. Nenhuma validação local deste relatório equivale a esse sucesso.

## Git, Entire e recomendação

Branch `develop`, HEAD preservado em
`bf315771bcf9d6c18a833bc0922aabe1114fac89`. Mudanças locais restritas ao workflow
e a este relatório, sem staging, commit ou push. Entire permanece habilitado;
hooks/configuração não foram alterados e nenhum checkpoint manual foi criado.
Na inspeção final, Entire indicou um checkpoint pendente de sincronização:
`01M6694STZ5570RCCN602RFZ96`, o mesmo ID registrado no commit anterior da Fase B,
sem erro de sincronização informado. Nenhum envio ou reparo foi executado.
Sem deploy, migrações, comandos Docker, publicação de imagens ou alteração remota.

Recomendação: pronta para revisão local e posterior autorização para versionamento
e validação remota. Não considerar a Fase C definitivamente aprovada antes da
execução bem-sucedida no GitHub Actions. Nenhuma fase posterior foi iniciada.

Conventional Commit sugerido, sem executar:
`ci(frontend): add quality tests build and shared OpenAPI contract gates`.
