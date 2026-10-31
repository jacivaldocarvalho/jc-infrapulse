# Sprint 06 — Fase E: Docker builds e validação de imagens

## Inspeção anterior à implementação

Baseline: `develop`, HEAD `fd275524a8690147d77382287225c70e78e93488`,
working tree limpa. AGENTS.md, guia e Brain Entire consultados; o código atual
é a fonte de verdade. Inspecionados workflow, todos os Dockerfiles/dockerignores,
quatro Composes existentes, exportação OpenAPI, gerador TypeScript e runtime.

- API e worker usam `backend/Dockerfile`, estágio runtime. Será construído uma
  única vez, com comandos distintos nos smoke tests.
- Backend: `python:3.12-slim`, usuário `app`, porta declarada 8000, CMD uvicorn.
  O comando worker e ambos os healthchecks backend pertencem ao Compose.
- Frontend: exportação Python do checkout, Node `24.18.1-bookworm-slim`, npm ci,
  tipos gerados e build, runtime `nginxinc/nginx-unprivileged:1.29-alpine`,
  usuário 101, porta 8080 e healthcheck `/healthz`.
- O Dockerfile frontend já usa OpenAPI do próprio contexto. O novo job deverá
  comparar o schema do estágio build com `api-contract-openapi` da mesma execução,
  sem modificar os Dockerfiles ou aceitar tipos gerados localmente.
- Dockerignores excluem `.env`, dependências locais, relatórios e tipos gerados;
  COPY explícito do backend limita o conteúdo. Serão verificados também os
  arquivos e layers efetivamente produzidos, não somente os padrões de ignore.

Riscos/inconsistências documentados antes da correção: tags-base mutáveis;
backend sem HEALTHCHECK na imagem (há no Compose); ferramentas de empacotamento
e possíveis resíduos do pip na imagem backend; necessidade legítima de senha na
configuração e de PostgreSQL para readiness/heartbeat. Não foi identificada nesta
inspeção uma vulnerabilidade que justifique alterar Dockerfiles. Se a inspeção
dinâmica revelar uma configuração relevante, a proposta será apresentada antes
de modificar qualquer Dockerfile.

Estratégia: builds locais com Buildx `--load`, sem login, publicação ou deploy;
estágio frontend build temporário para verificar schema/tipos; dois runtimes.
Smoke sem rede e sem banco: API `/health` 200 e `/ready` 503; worker inicia,
healthcheck falha sem heartbeat e SIGTERM encerra graciosamente; Nginx valida
configuração, estáticos e saúde. Nenhuma migração será executada. Não substitui
os testes reais de banco e observabilidade das fases anteriores.

O estado acima foi registrado antes da implementação; os resultados executados
estão abaixo. Nenhum Dockerfile precisou ser alterado.

## Arquivos e job

- Modificado: `.github/workflows/ci.yml`.
- Criados: `compose.images.test.yaml`, `scripts/validate-images.py`,
  `scripts/test_image_validation.py` e este relatório.

`docker-build`: Ubuntu 24.04, Python 3.12, timeout de 35 minutos, etapa de
validação de 28 minutos e cleanup always de três minutos. Usa Buildx disponível
no runner, sem instalar ferramentas arbitrárias ou alterar o builder. Depende
de `api-contract`; download sem run ID alternativo consome somente o artefato
`api-contract-openapi` da execução corrente. Não há login, push ou deploy.

As actions existentes foram reutilizadas com seus SHAs completos e comentários
de versão. `permissions: contents: read`, checkout sem persistência de credenciais,
triggers e concurrency permanecem iguais. Os nove jobs anteriores não mudaram.

## Builds e contrato

Três targets são carregados localmente via `docker buildx build --load
--platform linux/amd64`: backend runtime, frontend build temporário e frontend
runtime. API e worker compartilham a imagem backend; não há segundo build para
o worker. Os estágios frontend reutilizam o cache do mesmo contexto. O estágio
build contém ferramentas de desenvolvimento por definição; ele nunca é iniciado
e é removido ao final, sem ser tratado como runtime.

Os Dockerfiles existentes permanecem como fonte da instalação: pip com
`requirements.lock`, npm ci com `package-lock.json`, exportação OpenAPI e
`npm run build` com geração de tipos. A inspeção copia o schema e o arquivo
`generated.d.ts` do container não iniciado do estágio build. Na implementação
inicial exigia tipos não vazios e igualdade JSON do schema; isso era insuficiente
para validar o conteúdo dos tipos e foi corrigido na rodada descrita ao final.
Os Dockerignores
impedem entrada dos tipos locais e de node_modules, `.venv`, `.env` e relatórios.
O `.dockerignore` da raiz é o efetivo para o frontend (contexto raiz);
`frontend/.dockerignore` não substitui esse arquivo. Backend utiliza o seu próprio
ignore. `.env.example` é admitido pelo ignore backend, mas não por seus COPYs
explícitos; não foi encontrado nos runtimes ou layers.

Na validação local foi utilizado o exportador do mesmo checkout, em uma cópia
sem `.git`, `.env` ou dependências locais. No CI, esse arquivo será fornecido pelo
job api-contract. SHA-256 do contrato local:
`fd3c9ec866c776d3ae0e6c50234015bccc6a793e3ae39362d1013029aa9e1a39`.

## Inspeção das imagens resultantes

| Item | Backend compartilhado | Frontend runtime |
| --- | --- | --- |
| OS/arquitetura | linux/amd64 | linux/amd64 |
| Usuário declarado | app; UID não zero comprovado no processo | 101 |
| Entrypoint | nenhum | /docker-entrypoint.sh |
| CMD | uvicorn app.main:app --host 0.0.0.0 --port 8000 | nginx -g daemon off; |
| Porta declarada | 8000/tcp | 8080/tcp |
| HEALTHCHECK na imagem | ausente, conforme Dockerfile; Compose operacional possui healthchecks | wget /healthz |
| Layers inspecionadas | 12 | 12 |
| Arquivos regulares no filesystem final | 7.891 | 1.009 |
| Tamanho informado pelo Docker | 287.942.518 bytes | 83.283.684 bytes |

IDs observados antes do cleanup:

- Backend: `sha256:b8e830516a8961db25a549d25db71ce610151c4fac3bbba51a2979c61442800f`.
- Frontend: `sha256:de52f1bfc3e52d53ad05076521b5af3a8a256a55bd7de38b88341bfa66d53982`.

Foram verificados configuração, histórico e filesystem exportado, além de cada
layer original (inclusive conteúdo que poderia ter sido apagado posteriormente).
Os tars são lidos em streaming, sem extração de caminhos no host. A inspeção
rejeita arquivos `.env`, diretórios de credenciais/Git/dependências locais,
testes backend no runtime, padrões de chaves privadas/tokens e escrita por outros
em inodes de aplicação/configuração. Modos de symlinks não são permissões do alvo.
Não foram encontrados secrets pelos controles implementados. Não se trata de
scanner universal de secrets nem de análise de CVEs/supply chain da fase seguinte.

Variáveis embutidas inspecionadas: somente PATH, locale/versão/fingerprint público
GPG da base Python e flags Python/pip no backend; PATH e versões Nginx/NJS/pacotes
no frontend. O relatório publica apenas nomes dessas variáveis. `GPG_KEY` é um
identificador público da base, não credencial privada. Nenhuma senha é embutida.

pytest, Ruff, mypy e httpx não estão instalados no runtime backend; `pip check`
passou. Python e ferramentas de empacotamento da imagem-base permanecem. O runtime
frontend não possui Node, Python, workspace ou node_modules. Foram encontrados
42 arquivos de resíduos de build/egg-info em `/app` no backend: oportunidade de
redução de tamanho, sem dependências dev ou credenciais identificadas. Proposta
para revisão futura: remover apenas esses resíduos após pip install ou avaliar
um estágio de empacotamento separado; não foi alterado o Dockerfile nesta fase.

Permissões: `/app` e `/app/app` root:root 0755; main.py root:root 0664.
Estáticos frontend root:root 0755/0644. A base mantém `/etc/nginx` 101:0 0775
e o nginx.conf copiado está em 0664 root:root. Isso permite escrita de configuração
num container deliberadamente executado com filesystem gravável e grupo 0; o
Compose operacional já aplica read_only e o smoke exige essa proteção. Proposta
de hardening para revisão separada: avaliar 0644 para os configs e propriedade
do diretório, validando antes a compatibilidade com o entrypoint da base.
Nenhuma mudança de permissões de produção foi realizada.

## Smoke tests e isolamento

O Compose novo não possui banco, networks, portas publicadas, volumes persistentes
ou bind mounts. Todos os containers usam network_mode none, filesystem somente
leitura, cap_drop ALL e no-new-privileges. Runtimes têm somente `/tmp` em tmpfs.
Nginx recebe um alias api em loopback para resolver o upstream estático ao iniciar;
nenhum proxy/API E2E é exercitado. Não há acesso à rede privada do runner.

As senhas são geradas em memória pelo lifecycle existente e mascaradas no CI;
somente o nome da variável é passado ao Docker, sem imprimir seu valor. Backend
usa 127.0.0.1:1, timeout de um segundo, telemetria/métricas desabilitadas
explicitamente. Não lê `.env`, não acessa o PostgreSQL de desenvolvimento e não
executa migrações. Os cenários positivos de banco/worker continuam cobertos por
D1/D2; a ausência deliberada do banco não é apresentada como readiness saudável.

- API inicia com o CMD original; `/health` retorna 200/healthy.
- `/ready` deve retornar 503/not_ready sem banco.
- Worker usa o mesmo runtime com `python -m app.worker`; startup comprovado,
  healthcheck deve retornar exatamente 1 sem heartbeat; qualquer outro status
  inesperado falha. SIGTERM encerra com código 0 e logs worker_stopped e
  scheduler_stopped. Não há supressão genérica de erros.
- Nginx inicia com entrypoint/CMD originais; nginx -t passa; index.html e seus
  assets JS/CSS são servidos; healthcheck Docker se torna healthy.
- API, worker e Nginx encerram com exit code 0. Inspeção confirma nenhuma porta,
  nenhum container privilegiado e nenhum bind mount/socket do host.

## Execução, testes e evidências anteriores à correção dos tipos

Ambiente local: Python 3.12, Buildx v0.38.0, Compose v5.6.0. Checkout temporário
`/tmp/infrapulse-s06e-j44xxe1h`, projeto `infrapulse-images-s06e-j44xxe1h`.
Antes de cada execução foi comprovada ausência de recursos e tags com esse nome.

Comandos principais executados (targets pelo validador):

```bash
backend/.venv/bin/python -B scripts/export-openapi.py > /tmp/current-openapi.json
python3 scripts/test_image_validation.py
python3 scripts/test_integration_support.py
python3 scripts/validate-images.py --schema /tmp/current-openapi.json --report /tmp/image-report.json
python3 scripts/validate-images.py --cleanup
backend/.venv/bin/ruff check --no-cache scripts
backend/.venv/bin/ruff format --check --no-cache scripts
actionlint -color .github/workflows/ci.yml
git diff --check
```

Os caminhos reais de schema/report foram os arquivos em `evidence/` do checkout
temporário, com INFRAPULSE_TEST_PROJECT e RUNNER_TEMP explícitos. `actionlint`
foi executado pelo binário existente em `/tmp/infrapulse-s06b-actionlint-feulusd_/`.
ShellCheck examinou todos os blocos shell do workflow com expressões Actions
substituídas por placeholders, além da validação de sintaxe pelo actionlint.

Durante implementação houve duas falhas locais preservadas como evidência:
cleanup inicialmente omitia o profile inspection; depois um gate de permissões
tratou symlinks 0777 como arquivos graváveis. Ambos foram corrigidos no novo
validador, sem alterar código/Dockerfiles existentes. O fallback corrigido
removeu o container/tag restantes da primeira tentativa; a segunda tentativa
comprovou cleanup também em falha. Uma execução completa final passou, incluindo
inspeção e smoke. Oito testes focados verificam credenciais, conteúdo cruzando
chunks, permissões, symlinks, certificados públicos e preservação do erro primário
17 diante de cleanup 23 (também cenários de sucesso/falha isolada).

| Validação final | Resultado |
| --- | --- |
| Dockerfiles: Buildx --check e builds reais | PASS |
| Três Dockerignores e contexto sem .env | PASS |
| Backend runtime, frontend build e frontend runtime | PASS — sem publicação |
| Schema/existência de tipos do frontend | PASS inicial — schema idêntico; comparação integral dos tipos ainda ausente |
| Configuração, rootfs, histórico e 24 layers runtime | PASS |
| API, worker, Nginx e estáticos | PASS — sem banco/rede |
| Gates unitários de inspeção/erros | PASS — oito testes |
| Lifecycle e propagação existentes | PASS — 22 testes |
| Cleanup de containers/tags/markers | PASS |
| Containers, redes e volumes preexistentes | PASS — baseline preservado |
| ci-required | PASS — 1.048.576 combinações; somente dez sucessos aprovam |
| actionlint, ShellCheck, Ruff e formatação | PASS |
| git diff --check, incluindo arquivos novos | PASS |
| Execução E no GitHub Actions | NOT RUN — sem commit/push autorizado |
| Playwright/observabilidade completos nesta rodada | NOT RUN — não necessários para este smoke |

Evidências em `evidence/`: `validation.log`, `report.json`, `openapi.json` e
`before.json`. Tentativas anteriores: `first-validation.log`, `second-validation.log`
e `recovery-cleanup.log`. O relatório final contém metadados/permissões e hash do
contrato; não contém senhas, valores de ambiente, tars ou arquivos do runtime.
O workflow publica somente esse JSON sanitizado como `docker-image-validation`,
retenção de sete dias e erro se ausente. O upload padrão exige sucesso dos passos
anteriores, inclusive cleanup; não publica resultados de validação reprovada.

## Cleanup, quality gate e limitações

O mesmo mecanismo de ownership da D2 limita down --volumes --remove-orphans ao
projeto reservado. O profile inspection é incluído tanto na criação quanto no
cleanup, mas nunca no startup dos runtimes. Um marker adicional reserva as três
tags após recusar tags existentes. Só são removidas tags com label de propriedade
correspondente. Erros secundários são registrados sem substituir uma falha primária;
sem falha primária, erros de cleanup continuam reprovando o job. Não há prune.

Confirmação final: zero containers/redes/volumes com label do projeto; três tags
e ambos os markers ausentes. IDs, StartedAt, RestartCount e estado dos cinco
containers preexistentes, além das redes e volumes, coincidem com o baseline.
Cache de build e imagens-base permanecem; não houve remoção global de imagens.

ci-required mantém nome público, always e código anterior; agora possui dez
dependências. A matriz exaustiva success/failure/cancelled/skipped reprova qualquer
falha/cancelamento/skip de docker-build, incluindo skip por falha do contrato.
Os nove jobs de validação anteriores foram comparados estruturalmente com HEAD
e estão idênticos. O agregador só teve a lista needs estendida.

Riscos residuais: tags-base mutáveis, dependência de registries para download,
ferramentas de empacotamento/resíduos backend e permissões da base Nginx descritos
acima. Builds não são declarados bit a bit reproduzíveis. Os padrões de secrets
não cobrem todos os formatos possíveis. Os healthchecks positivos com banco não
são repetidos aqui. Perda forçada do runner pode impedir finally/fallback cleanup.
O runner hospedado ainda precisa comprovar suporte ao Buildx --check/--load e
validar o job completo. Warnings herdados das actions Node 20 não foram alterados.

Git permanece em develop no baseline, com somente os cinco arquivos desta fase
pendentes, nenhum staging/commit/push. Entire habilitado, um checkpoint anterior
pendente informado por status; nenhuma sincronização, repair, checkpoint manual
ou alteração de hooks/configuração foi executada. Containers locais preservados.

Recomendação: pronta para revisão local e posterior autorização de versionamento
e validação remota dos dez gates mais agregador. Não considerar E aprovada
remotamente. Não foi iniciada a Fase F. Commit sugerido, sem executar:
`ci(docker): build inspect and smoke runtime images`.

## Correção local da comparação dos tipos OpenAPI

Defeito documentado antes da correção: `validate` comparava os schemas JSON,
mas exigia somente tamanho maior que zero de `generated.d.ts`. Um arquivo
TypeScript não vazio e divergente era aceito. A revisão anterior reproduziu
esse comportamento isolando as operações Docker com mocks; os tipos atuais
não foram demonstrados incorretos, mas faltava um gate negativo efetivo.

Baseline desta rodada: develop, HEAD
`fd275524a8690147d77382287225c70e78e93488`; os cinco arquivos da Fase E permanecem
pendentes. A correção está autorizada somente no validador, seus testes e neste
relatório. Workflow, Compose, Dockerfiles e Dockerignores serão preservados.

Gerador inspecionado: `frontend/scripts/generate-api.mjs`, comando
`npm run generate:api` (também chamado por npm run build), usando openapiTS com
JSON.parse e astToString, sem opções adicionais, mais o cabeçalho existente.
`openapi-typescript` está em 7.13.0 no package.json e no package-lock.json v3,
com integrity registrada. O job api-contract produz o schema da execução, e
docker-build o baixa pelo nome sem selecionar outro run ID.

Docker está disponível, mas as três tags da Fase E, incluindo o estágio build
com o gerador travado, foram removidas pelo cleanup comprovado anteriormente.
Os frontends runtime existentes são Nginx, sem gerador/dependências Node.
Não haverá reconstrução nesta rodada; a validação real completa deverá usar
um novo projeto descartável após autorização de revalidação/rebuild.

### Implementação da comparação independente

O schema do estágio build continua sendo comparado como JSON com o artefato
esperado. Depois, `compare_types` lê os bytes do arquivo efetivamente copiado
do build e chama `generate_reference`. Essa referência usa somente o contrato
recebido por --schema, proveniente do download da execução corrente. Não lê nem
copia o generated.d.ts do build como entrada.

A geração é executada em um novo container da imagem frontend-build já carregada
pelo job; --pull never impede buscar outra imagem implicitamente. O processo usa
usuário node, network none, filesystem somente leitura,
cap_drop ALL, no-new-privileges, limite de 64 processos, 512 MiB e uma CPU.
Somente /tmp é gravável por tmpfs de 64 MiB com noexec/nosuid. Não há publicação
de portas, bind mounts, socket Docker ou encaminhamento de variáveis de ambiente
do host. O payload contendo contrato e hashes esperados chega por stdin; não é
incluído na linha de comando nem impresso.

Dentro do container são verificados hashes SHA-256 do gerador e package-lock.json
contra o checkout atual e a versão instalada de openapi-typescript contra a versão
exata declarada/travada. O lock precisa conter integrity e o package.json precisa
declarar a mesma versão exata, sem range. O script existente é copiado para um
workspace novo em /tmp e usa, por symlink, somente as dependências instaladas por
npm ci no estágio build. O processo filho recebe exclusivamente OPENAPI_SCHEMA_FILE
apontando para uma cópia do contrato esperado. Executa o mesmo generate-api.mjs,
com opções e cabeçalho idênticos, e grava um arquivo novo nesse workspace.

O validador exige igualdade de todos os bytes, sem normalizar espaços, linhas,
ordem ou cabeçalho. Tipos ausentes, vazios ou apenas whitespace, referência vazia,
falha/timeout de geração e divergência do schema reprovam. Em sucesso, o relatório
registra types.built_sha256, types.reference_sha256, generator_version,
generator_source_sha256 e lockfile_sha256. Divergências geram diagnóstico limitado
a hashes e mensagem fixa, sem diff ou conteúdo dos arquivos.

Saída e stderr do processo de referência são capturados. Falhas imprimem somente
código/timeout e hashes dos streams; a exceção original mantém sua identidade e
código numérico, com cmd sanitizado para evitar traceback extenso ou dados de saída.
O bootstrap preserva o status numérico do gerador filho; sinais usam 128 + sinal.
O fluxo main e run_cli existente preserva a falha primária mesmo se o cleanup
também falhar. O container é --rm; seus labels de projeto/serviço/oneoff permitem
também remoção pelo cleanup com --remove-orphans em caso de interrupção.

### Regressões red-green e comparação funcional

Antes da correção foram executados os oito testes anteriores e nove regressões
novas: 17 métodos, sete falhas e um erro, incluindo aceitação de tipos divergentes
e de conteúdo diferente com o mesmo tamanho. Após a correção, os mesmos 17 testes
passaram. Foram adicionados mais quatro métodos para isolamento, versão travada,
diagnósticos, status 17 e timeout: a suíte final possui 21 testes.

| Cenário obrigatório | Resultado após correção |
| --- | --- |
| Schema e tipos corretos | PASS — igualdade integral e hashes iguais |
| Schema correto, tipos divergentes | PASS — divergência rejeitada |
| Tipos vazios ou apenas whitespace | PASS — rejeitados |
| Tipos ausentes | PASS — rejeitados |
| Falha na geração da referência | PASS — código 17 preservado |
| Referência vazia ou timeout | PASS — falha, sem sucesso acidental |
| Schema divergente | PASS — rejeitado antes da referência |
| Mesmo tamanho, conteúdo diferente | PASS — rejeitado |
| Credencial sintética em tipos/stdout/stderr | PASS — conteúdo não aparece no diagnóstico |
| Falha primária 17 e cleanup 23 | PASS — primária preservada; secundária registrada |
| Versão declarada flutuante/inconsistente | PASS — rejeitada antes de executar Docker |

Também houve validação funcional do mesmo bootstrap JavaScript fora do Docker,
em workspace temporário, com Node v24.18.1 e openapi-typescript 7.13.0 já instalados
na cópia temporária anterior; nenhuma dependência global foi instalada. O contrato
local e um arquivo gerado existente dessa cópia frontend foram usados para testar
a geração independente de referência. Isso não é apresentado como comparação
contra uma imagem Docker nesta rodada.

Resultado: 19.275 bytes em ambos os arquivos e SHA-256
`266c0ab32f554ed5d591a351c1f5c8183bc345d01af50d0361b959f666ed9c34` igual para
referência e arquivo comparado. Uma alteração de um byte, sem mudar o tamanho,
foi rejeitada. O bootstrap real também rejeitou hash de fonte e versão instalada
incompatíveis; um gerador sintético encerrado com 17 preservou 17, sem expor
seu stderr sensível. Esses testes usaram somente arquivos novos em /tmp e leitura
das dependências já existentes, sem alterar a cópia frontend original.

Hashes verificados na estratégia de referência:

- Gerador: `a29f3652005928fe95be121be7edc6d232741db21c213701057e190dc97620b3`.
- Lockfile: `bf4985cb153daee0f826e53223554511d3a155e42e32200467bfa92983eadc61`.

| Validação final desta correção | Resultado |
| --- | --- |
| Testes do inspetor/contrato/processo de referência | PASS — 21 testes |
| Lifecycle e propagação existentes | PASS — 22 testes |
| Gerador real e comparação funcional fora do Docker | PASS |
| Ruff check e formatação | PASS |
| actionlint e ShellCheck | PASS |
| git diff --check, incluindo arquivos novos | PASS |
| ci-required | PASS — dez dependências e seis cenários representativos |
| Comparação efetiva contra estágio Docker frontend | NOT RUN — imagem build removida pelo cleanup anterior |
| Reconstrução Docker e GitHub Actions | NOT RUN — sem execução/publicação autorizada nesta rodada |

Evidências: `/tmp/infrapulse-e-types-ipnfx1me/red.log`, `green.log`,
`final-tests.log`, `functional-node.json`, manifesto inicial `before.json` e
consulta Brain `brain-status.json`. A matriz do agregador inclui sucesso, vazio,
failure/cancelled/skipped de docker-build e falha do contrato com dependentes
pulados; não substitui a matriz exaustiva inicial, cujo código permanece igual.

### Limitações, estado e próxima revisão

Plano de revalidação Docker: sob nova autorização, construir os targets existentes
em projeto exclusivo, exportar/baixar o contrato atual e executar validate-images
com a comparação nova, comprovar hashes, smoke e cleanup; após revisão e autorização
de versionamento, executar os onze jobs no GitHub Actions. Não reutilizar schema,
tipos ou artefatos de outra execução como referência. O helper de referência e
seu cleanup Docker ainda precisam dessa comprovação no ambiente real.

Somente scripts/validate-images.py, scripts/test_image_validation.py e este relatório
mudaram em relação ao início desta correção. Hashes confirmam workflow e Compose
da Fase E intactos; Dockerfiles, Dockerignores, aplicação, contratos e SSRF não
foram alterados. Permanecem os cinco arquivos pendentes originais, sem staging,
commit ou push, na branch e HEAD esperados. Entire habilitado, doctor sem problemas
nos hooks ou sessões presas e um checkpoint anterior pendente; sem repair,
sincronização forçada ou checkpoint manual.

A comparação estrita pode reprovar mudanças futuras no gerador/lock por diferença
real de saída; os hashes e a versão deixam a causa verificável sem publicar conteúdo.
Os demais riscos de imagens-base, permissões e inspeção por padrões continuam
documentados acima. Recomenda-se retomar a revisão local e autorizar a revalidação
Docker antes do versionamento. Fase E continua sem aprovação remota; Fase F não
foi iniciada.

## Revalidação Docker autorizada após a correção OpenAPI

Esta seção registra a execução real posterior à correção, substituindo a limitação
Docker NOT RUN da rodada anterior. GitHub Actions continua **NOT RUN** para E.
Branch develop e HEAD `fd275524a8690147d77382287225c70e78e93488` preservados;
somente os cinco arquivos previstos da Fase E estavam pendentes no início.
Workflow e Compose foram conferidos por hash e não foram alterados nesta rodada.
Também foram inspecionadas as diferenças dos dois validadores e o lifecycle de
ownership, propagação de erros e cleanup reutilizado da D2.

### Ambiente, comandos e proveniência

Execução em cópia temporária limpa `/tmp/infrapulse-e-docker-l28po9cc`, sem .git,
.env ou dependências locais. Projeto exclusivo
`infrapulse-images-e-docker-l28po9cc`, com RUNNER_TEMP apontando para runtime/
nessa cópia. Antes de criar recursos, foram recusados conflitos de containers,
redes, volumes, markers e três tags próprias. Foram salvos snapshots dos cinco
containers, sete redes, quatro volumes e 105 entradas de imagens/tags preexistentes.

O exportador foi executado novamente com o Python do backend instalado, lendo
as fontes copiadas do checkout atual. Backend, gerador, locks, Dockerfiles,
Dockerignores e exportador coincidem com HEAD; as alterações E não modificam
contratos. Não foi usado artefato/schema de uma execução anterior. Contrato:
`fd3c9ec866c776d3ae0e6c50234015bccc6a793e3ae39362d1013029aa9e1a39` (SHA-256).

Comando do validador, com os parâmetros efetivamente utilizados:

```bash
INFRAPULSE_TEST_PROJECT=infrapulse-images-e-docker-l28po9cc \
RUNNER_TEMP=/tmp/infrapulse-e-docker-l28po9cc/runtime \
python3 evidence/run-validation.py \
  --schema /tmp/infrapulse-e-docker-l28po9cc/evidence/openapi.json \
  --report /tmp/infrapulse-e-docker-l28po9cc/evidence/report.json
```

O runner temporário em evidence/ carrega o arquivo atual validate-images.py e
chama seu main por run_cli. Acrescenta apenas observação e um caso negativo à
comparação: a função original executa primeiro o caso positivo e depois compara
uma cópia temporária dos tipos com um byte alterado. Não há mocks de operações
Docker, alteração dos arquivos do validador ou substituição da referência por
dados simulados. Erros inesperados continuam sendo propagados; somente a exceção
de divergência esperada do teste negativo é reconhecida como rejeição correta.

Builds executados pelo fluxo atual, após Buildx --check nos dois Dockerfiles:

```bash
docker buildx build --load --platform linux/amd64 --progress plain \
  --target runtime --label com.infrapulse.image-validation=infrapulse-images-e-docker-l28po9cc \
  -t infrapulse-images-e-docker-l28po9cc:backend -f backend/Dockerfile backend
docker buildx build --load --platform linux/amd64 --progress plain \
  --target build --label com.infrapulse.image-validation=infrapulse-images-e-docker-l28po9cc \
  -t infrapulse-images-e-docker-l28po9cc:frontend-build -f frontend/Dockerfile .
docker buildx build --load --platform linux/amd64 --progress plain \
  --target runtime --label com.infrapulse.image-validation=infrapulse-images-e-docker-l28po9cc \
  -t infrapulse-images-e-docker-l28po9cc:frontend -f frontend/Dockerfile .
```

Sem login, push, registry de destino ou deploy. Backend construído uma vez para
API e worker. Os estágios frontend reutilizaram cache; não foi solicitado no-cache
nem foram removidas imagens-base/cache compartilhados.

### Imagens e comparação real

| Imagem local temporária | ID observado antes da remoção | Resultado |
| --- | --- | --- |
| backend compartilhado | sha256:767b56fb75eb842680c1f14d6dc3bba00f5b2de948c96f77f5076e46d7cc559e | PASS |
| frontend-build, somente inspeção/gerador | sha256:cfdaea2b5dbeb4aea57f12099489063e88e24a342bc95be09588e3b06ea6f290 | PASS |
| frontend/Nginx runtime | sha256:550b91aefd9836c4343a0174ddcdec5a5813d83683a9e5b4ab76784441e2462d | PASS |

Os dois runtimes são linux/amd64, usuários app/101, com os CMDs/entrypoint e portas
declaradas esperados. Foram inspecionados novamente rootfs, histórico, permissões,
variáveis embutidas e 24 layers de runtime. Backend mantém 42 arquivos residuais
de empacotamento; não há pytest/Ruff/mypy/httpx instalados e pip check passou.
Frontend mantém somente runtime Nginx/estáticos, sem Node, Python ou workspace.

O generated.d.ts foi realmente copiado do container criado a partir do target
frontend-build. A referência foi gerada duas vezes em novos containers Node
(positivo e negativo), a partir do contrato esperado por stdin, com
openapi-typescript 7.13.0 e os hashes de fonte/lock já documentados. Não foi lido
ou copiado o arquivo do build como referência. Source e lock da imagem foram
validados contra o checkout; geração em workspace novo, com usuário node, sem
rede, sem mounts do host, rootfs somente leitura e escrita em tmpfs limitada.

| Evidência | Resultado |
| --- | --- |
| generated.d.ts extraído | 19.275 bytes |
| SHA-256 do arquivo do build | 266c0ab32f554ed5d591a351c1f5c8183bc345d01af50d0361b959f666ed9c34 |
| SHA-256 da referência independente | 266c0ab32f554ed5d591a351c1f5c8183bc345d01af50d0361b959f666ed9c34 |
| Comparação byte a byte | PASS — igualdade integral |
| Cópia sintética de mesmo tamanho | REJECTED — somente um byte alterado |
| SHA-256 da cópia divergente | 49d6acf69160e3268630bae8894b34ee539f86b2aa67bbe1a3a88c6896db08e1 |

O arquivo original permaneceu inalterado depois do caso negativo. O comparador
real lançou ValueError com os hashes distintos e mensagem fixa, sem expor os tipos.
Resultado final do fluxo: exit code 0 após a igualdade positiva, rejeição negativa,
inspeções, smoke e cleanup. A rejeição sintética é uma condição esperada do teste;
nenhuma falha inesperada foi convertida em sucesso. Os testes unitários voltaram
a comprovar status primário 17 preservado diante de cleanup 23 e diagnósticos seguros.

### Smoke, cleanup e controles complementares

| Verificação executada | Resultado |
| --- | --- |
| API /health | PASS — 200/healthy, processo não root |
| API /ready sem banco | PASS — exatamente 503/not_ready |
| Worker startup | PASS — worker_started |
| Worker heartbeat sem banco | PASS — healthcheck retorna exatamente 1 |
| Worker SIGTERM | PASS — worker_stopped e scheduler_stopped; exit code 0 |
| Nginx configuração | PASS — nginx -t |
| Frontend estáticos e healthcheck | PASS — index/assets servidos e healthy |
| Processos API/worker/frontend ao encerrar | PASS — exit code 0 |
| Runtime permissions/isolation | PASS — read_only, network none, sem privilégios, sem portas/binds |
| Inspeção real de runtime/layers e comparação de tipos | PASS |
| 21 testes do inspetor | PASS — 0,059 s |
| 22 testes de lifecycle/propagação | PASS — 2,369 s |
| Ruff check e format --check | PASS — nove scripts formatados |
| actionlint, ShellCheck e git diff --check | PASS |
| Dez dependências de ci-required | PASS — seis cenários representativos preservados |
| Cleanup e recursos preexistentes | PASS |
| GitHub Actions E | NOT RUN — sem versionamento nesta rodada |

Cleanup comprovado por consultas depois da execução: nenhum container, rede ou
volume com label do projeto; os dois containers de referência --rm ausentes;
três tags próprias e markers ausentes. Os cinco containers anteriores conservam
IDs, StartedAt, RestartCount e estado; as sete redes, quatro volumes e todas as
105 imagens/tags anteriores permanecem iguais. Após cleanup há novamente 105
entradas de imagens/tags. Não houve prune, mounts de volumes anteriores, migrações,
alterações de firewall ou acesso a banco local: os runtimes estavam sem rede e
usavam banco fictício em loopback:1 dentro de seus próprios namespaces.

Evidências em `/tmp/infrapulse-e-docker-l28po9cc/evidence/`:
`before-resources.json`, `after-resources.json`, `before-source.json`,
`openapi.json`, `validation.log`, `report.json`, `type-comparison.json`,
`inspector-tests.log`, `lifecycle-tests.log` e o runner de observação temporário.
Relatórios JSON sanitizados inspecionados: somente hashes/metadados e nomes de
variáveis, sem valores de ambiente, arquivos de tipos ou credenciais. Não foram
encontrados padrões de secrets pelos controles executados; isso não é uma auditoria
universal de dados sensíveis. Buildx --check informou no warnings found para ambos
os Dockerfiles. Etapas em cache não representam uma reinstalação sem cache.

### Estado e recomendação após a revalidação

Somente este relatório foi atualizado nesta rodada. Manifesto SHA-256 confirmou
que os cinco arquivos executados eram os atuais antes dessa atualização; os quatro
arquivos de workflow/Compose/validadores permanecem idênticos ao início da rodada.
Branch e HEAD preservados, nenhum staging, commit ou push. Entire doctor sem
falhas nos hooks ou sessões presas, status habilitado e um checkpoint anterior
pendente; Brain consultado somente em leitura. Não houve repair, sincronização
forçada, checkpoint manual ou mudança de configuração.

Limitações remanescentes: suporte/comportamento do runner hospedado e o workflow
E completo ainda precisam de execução real no GitHub Actions; tags-base mutáveis,
resíduos backend e permissões Nginx descritos anteriormente continuam iguais.
Cache de build foi preservado. A inspeção por padrões não equivale a scanner de
CVEs ou garantia universal de ausência de secrets. Fase F não foi iniciada.

Recomendação: revalidação Docker local satisfatória; pronta para revisão final
de versionamento e nova autorização de commit/push. Não considerar a Fase E
remotamente aprovada antes dos dez jobs de validação e ci-required passarem.
