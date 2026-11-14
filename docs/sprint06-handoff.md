# Sprint 06 — handoff F3C.3.2 e F3C.4

## Atualização de retomada — 2026-11-14

- PRs #11–#14 foram confirmados como mesclados; os quatro CI pós-merge em `develop` concluíram com sucesso. O run do PR #14 é [43645063533](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/43645063533).
- F3C.4.2B–D estão concluídas. O contrato v1 e os testes negativos mantêm `COVERAGE_PARTIAL` e rejeitam `COMPLETE` sem prova independente.
- Nesta retomada, o leitor passou a aceitar os cabeçalhos GNU e PAX local `path`/`linkpath` observados nos archives reais, com limites de 128 MiB por camada, 256 MiB agregados, 1 MiB por cabeçalho PAX e 100.000 entradas. Gzip, PAX global e extensões não observadas continuam rejeitados.
- Os dois archives preservados do run `41238810850` foram verificados pelos hashes do handoff, IDs dos configs e DiffIDs; o parser leu todas as 24 camadas. Detalhes e limites em [integração F3C.3.2](sprint06-f3c3-reconciliation.md).
- A integração direta do verificador F3C.3.2 com a reconciliação está implementada e validada localmente nesta branch isolada, mas ainda não foi integrada ao GitHub nem executada no CI remoto. O pacote histórico não preservou os bundles Cosign descartáveis; o CI atual testa assinaturas reais com fixture descartável.
- Retenção atual: handoff de imagens 1 dia, relatórios sanitizados 7 dias e evidência CI sem assinatura 30 dias. Os bundles temporários F3C.3.2 não são publicados.

**Registro em 2026-11-01, UTC.** Reconfirmar referências e retenção na retomada; os estados remotos podem mudar.

## Resumo executivo

- F3C.3.2 teve validação real aprovada e foi sincronizada para `develop` pelo [PR #9](https://github.com/jacivaldocarvalho/jc-infrapulse/pull/9), merge `de6f39aee8addb1d99fd161f96e6dae222ad56fd`.
- O [CI pós-merge 41353516492](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41353516492) terminou com sucesso, inclusive `security-foundations` e `ci-required`.
- F3C.4.1A e F3C.4.2A–D estão concluídas. F3C.4.3–4.4 seguem os critérios de cobertura e integração descritos abaixo e na atualização de retomada.
- A cobertura das imagens continua `COVERAGE_PARTIAL`; nenhuma igualdade, assinatura ou leitura bem-sucedida promove cobertura automaticamente.

## Estado por fase

| Fase | Estado e evidência |
| --- | --- |
| F3C.3.2 | Concluída: [run real 41344302188, attempt 1](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41344302188), `main` `350a404019ae1667a9e5805fe6b225b461401f7d`; integração em `develop` pelo PR #9. |
| F3C.4.1A | Concluída: contrato v1 no commit `ff07878b53ab0bb10cd05fbf9c04abb1675fa1c1`; 13 testes. |
| F3C.4.2A | Concluída: leitor virtual USTAR/GNU e PAX restrito; limites ampliados validados com as imagens preservadas e regressões negativas no job obrigatório. |
| F3C.4.2B–D | Concluídas pelos PRs #11–#13 em 2026-11-14; CI pós-merge dos três PRs passou. |
| F3C.4.3 | Contrato v1 e gates negativos integrados; cobertura permanece `COVERAGE_PARTIAL`. |
| F3C.4.4 | PR #14 integrou verificação de manifesto/SBOM assinados à reconciliação. A ligação direta aos bundles completos F3C.3.2 está em validação isolada nesta retomada. |

## Arquitetura e decisões de segurança

- A evidência F3C.2 cobre locks Python/npm e apenas as imagens `backend-image` e `nginx-image`. A comparação F3B.1 regenera o inventário com Trivy fixado e compara componentes e relações. Isso não mede a completude da detecção. Ver [F3C.2](ci-evidence-f3c2.md) e [F3B.1](provenance-f3b1.md).
- Exigir IDs completos e nomes lógicos de fontes confiáveis independentes. Um ID lido apenas de um archive ou manifesto não assinado comprova consistência, não autenticidade da origem. Preservar hashes, pinagens, comparação integral dos statements e fail-closed.
- Assinaturas Cosign e política de confiança verificadas são evidências de integridade e de autorização da chave, não de builder autenticado, geração reproduzível ou nível SLSA. A validação real usou chaves descartáveis; não usar chaves operacionais em testes.
- Os archives originais e canonicalizados têm hashes distintos. O [canonicalizer](oci-canonical-exporter.md) suporta perfis OCI restritos, preserva os registros alcançáveis e remove somente metadados legados reconhecidos; preservar o original e o recibo da transformação, sem fallback silencioso.
- O workflow F3C.3.2 integrado mantém `workflow_dispatch` e job condicionado à `main`; o validador é pinado em `3daeae2b308896edea3a88a0a43e1063d06c1768`. Sua exceção de `actions: read` é limitada ao caminho exato, gatilho manual, único job e condição de `main`. Consulte o [workflow integrado](https://github.com/jacivaldocarvalho/jc-infrapulse/blob/de6f39aee8addb1d99fd161f96e6dae222ad56fd/.github/workflows/f3c3-real-validation.yml).
- O [contrato v1](../security/image-coverage-v1.schema.json) valida estrutura; [Python](../scripts/image_coverage_contract.py) aplica estados e IDs esperados, rejeitando `COMPLETE`. Ele não verifica bytes nem autentica as evidências. Não confundir os estados do contrato com o `COVERAGE_PARTIAL` existente.
- O [leitor virtual](../scripts/oci_virtual_layers.py) recebe bytes já alocados e descritores que o chamador deve verificar independentemente. Limites: 64 camadas, 128 MiB/camada, 256 MiB totais, 100.000 entradas e 1 MiB por cabeçalho PAX. Aceita USTAR, cabeçalhos GNU observados e PAX local somente para `path`/`linkpath`; mantém gzip, PAX global e extensões desconhecidas rejeitados. Symlinks são registrados sem resolução no host; hardlinks precisam apontar para arquivo regular já conhecido. A reconstrução permanece virtual e `COVERAGE_PARTIAL`.

## Evidências e limitações

- No run real, passaram as oito fases `checkout`, `tools`, `extract`, `manifests`, `canonicalize`, `docker_import`, `sign_verify` e `negative_checks`; proveniências Python, npm, backend e nginx passaram na assinatura e verificação. Cosign v3.1.3 foi verificado contra o SHA-256 fixado. Passaram testes negativos de payload, bundle, chave, fingerprint e SBOM; cleanup concluiu.
- Os três ZIPs originais do [run 41238810850, attempt 1](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41238810850), commit `9f237be45ccd6e7977f50f85d37ebcc32d81a2bb`, foram preservados com manifesto em `/home/juninhocarvalho/projetos/infrapulse-evidence-archive/41238810850-attempt-1/`. Diretório `700`, arquivos `600`; hashes e integridade ZIP foram verificados. O manifesto distingue os originais de derivados. Esses hashes não autenticam independentemente o produtor; o armazenamento é local, fora do Git.
- O artifact remoto de imagens deixou de aparecer na listagem após a expiração prevista em 2026-11-01 20:28:10 UTC. A auditoria expira em 2026-11-07 20:29:40 UTC e `ci-evidence` em 2026-11-30 20:29:15 UTC; confirmar retenção antes de depender deles.
- O CI F3C.4 anterior [41349121375](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41349121375) passou, com 13 testes do contrato e 10 do parser. O CI pós-merge também passou. Aviso de Node.js 20 nas actions é dívida técnica separada; não alterar pins sem revisão.
- Em 2026-11-14, os dois archives Docker preservados do run `41238810850` foram validados sem carregá-los no daemon: hashes do handoff e IDs de configuração conferidos; 12 camadas e DiffIDs por imagem; parser virtual percorreu 224.294.912 bytes/11.779 entradas do backend e 56.756.736 bytes/2.291 entradas do nginx. Os archives continham cabeçalhos GNU e PAX `path`/`linkpath`. Isso valida esse par de imagens/plataforma e os formatos observados; não demonstra completude de SBOM nem autentica o builder.

## Snapshot histórico de worktrees, referências e Entire — 2026-11-01

- Worktree principal `/home/juninhocarvalho/projects/jc-infrapulse`: branch `develop`, HEAD local `86b80d70f5f7b3966de53994ae9a3924b525abee`. Na consulta, `origin/develop` local ainda apontava para esse SHA, enquanto `develop` remoto apontava para `de6f39a`. Não presumir que a referência local está atualizada.
- Há cinco modificações rastreadas preexistentes: `scripts/ci_evidence_signature.py`, `scripts/provenance.py`, `scripts/provenance_signature.py`, `scripts/test_ci_evidence_signature.py` e `scripts/test_provenance.py`. As versões na worktree coincidiram com o conteúdo correspondente do commit `3daeae2`; não são commits de `develop`.
- Há quatro arquivos não rastreados preexistentes: `.github/workflows/f3c3-real-validation.yml`, `docs/f3c3-real-validation.md`, `scripts/ci_f3c3_validation.py` e `scripts/test_ci_f3c3_validation.py`. Suas versões locais diferem das integradas na main. Uma atualização direta pode colidir com eles.
- Worktree de integração `/tmp/infrapulse-f3c3-sync-develop`: branch `fix/f3c3-sync-develop`, HEAD `2945962f5b9ee50982a3c8e1c1d6ec3627d6a9fd`, limpa no Git; `frontend/node_modules` está ignorado e foi instalado para testes. A branch remota foi preservada. Existem outras worktrees antigas; não removê-las sem revisão e autorização.
- Entire habilitado; na última consulta, nenhum checkpoint aguardava envio. `Entire Gates` do PR #9 apareceu `SUCCESS` no preflight, depois `FAILURE` com “1 commit behind” e posteriormente `NEUTRAL`, sem trail aberto. O head do PR é ancestral direto do merge, mas a causa temporal da mudança do check não foi comprovada. O doctor também reportou sessão anterior encerrada `01a19295-64a8-7eb2-a0b0-e905463e0fc1`, com dois turnos não condensados; nenhum reparo foi autorizado.

## Fechamento restante

1. **F3C.4.2B–D:** concluídas e mescladas nos PRs #11–#13; os respectivos CIs pós-merge passaram.
2. **F3C.4.3:** contrato e regressões negativas estão no CI obrigatório. A política conserva `COVERAGE_PARTIAL` porque os dados não provam completude.
3. **F3C.4.4:** PR #14 integrou verificação de manifest/SBOM assinados. A branch desta retomada adiciona o vínculo com os bundles F3C.3.2 completos e cobre o snapshot de bytes verificados. Falta publicar essa branch, abrir/revisar o PR e obter CI pós-merge.
4. **Bundles históricos:** os bundles Cosign usados no run real não foram preservados; não é possível repetir a assinatura histórica. A CI existente valida assinaturas reais com chaves descartáveis e cenários negativos. O resultado das imagens reais preservadas valida parser e inventário, mas não autentica o builder.
5. **Retenção operacional:** handoff de imagem 1 dia, evidência CI sem assinatura 30 dias e relatórios sanitizados 7 dias. Bundles descartáveis não são publicados. Essa política limita a janela para repetir validações que dependem do handoff.

**Critério de encerramento:** integrar o commit local de fechamento por PR e confirmar sucesso do CI pós-merge. Não promover cobertura para `COMPLETE` sem evidência independente adicional.

## Plano de retomada original — supersedido pela atualização de 2026-11-14

1. Reler `AGENTS.md` e `.entire/agent-guide.md`; consultar o estado atual antes de pressupor que este registro continua atualizado.
2. Examinar a worktree principal e suas alterações sem descartá-las. Para sincronizar com `develop` remoto, primeiro planejar uma worktree/branch separada e comparar os quatro arquivos não rastreados e cinco modificados; não fazer pull direto sobre a worktree atual. Obter autorização para atualização de refs ou criação de worktree.
3. Revisar o escopo e a semântica OCI de 4.2B com exemplos adversariais. Implementar em pequenos incrementos somente após autorização; manter a worktree original e evidências privadas preservadas.
4. Revisar a sessão Entire não condensada em tarefa separada. Não executar `entire doctor --force`, instalar/desabilitar hooks ou alterar checkpoints sem autorização específica.

Diagnósticos de leitura sugeridos, a partir da worktree escolhida:

```sh
git status --short --branch
git worktree list --porcelain
git ls-remote origin refs/heads/main refs/heads/develop
gh pr view 9 --repo jacivaldocarvalho/jc-infrapulse
gh run view 41353516492 --repo jacivaldocarvalho/jc-infrapulse
entire status
entire checkpoint list --outgoing
```

Validação local sugerida **na worktree de integração**, após confirmar dependências e sem Docker local:

```sh
python3 -B scripts/test_ci_f3c3_validation.py
python3 -B scripts/test_supply_chain.py
python3 -B scripts/test_locked_install.py
python3 -B scripts/test_image_coverage_contract.py
python3 -B scripts/test_oci_virtual_layers.py
git diff --check
```

Os testes de políticas usam `frontend/node_modules/js-yaml` na versão do lock; uma nova worktree pode precisar de instalação isolada, sem lifecycle scripts. Testes podem criar arquivos temporários. Confirmar ferramentas disponíveis e rodar Ruff e actionlint quando aplicável.

## Critérios gerais de aprovação e riscos

- Preservar CI obrigatório, permissions mínimas, pins de actions/ferramentas e os controles F3B.1/F3C.3.2. Alterações de política devem ser específicas e ter regressões positivas e negativas.
- Cobertura deve declarar universo, plataforma, fontes independentes, exclusões e lacunas. `COMPLETE` só pode ser considerado após prova independente para escopo limitado e revisão do contrato; sucesso do Trivy ou comparação com ele mesmo não bastam.
- Falhas de parser, identidades, hashes, limites, ferramentas ou formatos não suportados não podem ser convertidas em sucesso. Testar caminhos, colisões, TAR ambíguo, symlinks, hardlinks, whiteouts e limites antes de usar dados reais.
- Não executar pull, push, commit, merge, rebase, reset, stash, Docker local, dispatch, publicação de artifacts, limpeza de worktrees ou operações manuais Entire sem autorização aplicável. Nunca usar chaves operacionais em testes.

## Prompt inicial sugerido

> Leia `AGENTS.md`, `.entire/agent-guide.md` e `docs/sprint06-handoff.md`. Confirme em modo somente leitura as referências remotas, as duas worktrees, as alterações preexistentes e o estado Entire. Proponha um plano isolado para F3C.4.2B com whiteouts, diretórios opacos e hardlinks, mantendo `COVERAGE_PARTIAL`. Não sincronize, implemente, execute Docker nem altere checkpoints até receber autorização explícita.
