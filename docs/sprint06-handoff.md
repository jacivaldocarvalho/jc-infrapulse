# Sprint 06 — handoff F3C.3.2 e F3C.4

**Registro em 2026-11-01, UTC.** Reconfirmar referências e retenção na retomada; os estados remotos podem mudar.

## Resumo executivo

- F3C.3.2 teve validação real aprovada e foi sincronizada para `develop` pelo [PR #9](https://github.com/jacivaldocarvalho/jc-infrapulse/pull/9), merge `de6f39aee8addb1d99fd161f96e6dae222ad56fd`.
- O [CI pós-merge 41353516492](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41353516492) terminou com sucesso, inclusive `security-foundations` e `ci-required`.
- F3C.4.1A e F3C.4.2A estão concluídas. As etapas seguintes estão pendentes; seus escopos abaixo são propostas para revisão, não implementações concluídas.
- A cobertura das imagens continua `COVERAGE_PARTIAL`. A próxima etapa proposta é reconstrução virtual segura do rootfs, não promoção automática de cobertura.

## Estado por fase

| Fase | Estado e evidência |
| --- | --- |
| F3C.3.2 | Concluída: [run real 41344302188, attempt 1](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41344302188), `main` `350a404019ae1667a9e5805fe6b225b461401f7d`; integração em `develop` pelo PR #9. |
| F3C.4.1A | Concluída: contrato v1 no commit `ff07878b53ab0bb10cd05fbf9c04abb1675fa1c1`; 13 testes. |
| F3C.4.2A | Concluída: leitor virtual USTAR no commit `86b80d70f5f7b3966de53994ae9a3924b525abee`; 10 testes integrados ao job obrigatório. |
| F3C.4.2B–D | Pendentes: reconstrução virtual, inventário de arquivos e reconciliação proposta. |
| F3C.4.3–4.4 | Pendentes: contrato de evidências/política e integração operacional propostas; definir formalmente antes de implementar. |

## Arquitetura e decisões de segurança

- A evidência F3C.2 cobre locks Python/npm e apenas as imagens `backend-image` e `nginx-image`. A comparação F3B.1 regenera o inventário com Trivy fixado e compara componentes e relações. Isso não mede a completude da detecção. Ver [F3C.2](ci-evidence-f3c2.md) e [F3B.1](provenance-f3b1.md).
- Exigir IDs completos e nomes lógicos de fontes confiáveis independentes. Um ID lido apenas de um archive ou manifesto não assinado comprova consistência, não autenticidade da origem. Preservar hashes, pinagens, comparação integral dos statements e fail-closed.
- Assinaturas Cosign e política de confiança verificadas são evidências de integridade e de autorização da chave, não de builder autenticado, geração reproduzível ou nível SLSA. A validação real usou chaves descartáveis; não usar chaves operacionais em testes.
- Os archives originais e canonicalizados têm hashes distintos. O [canonicalizer](oci-canonical-exporter.md) suporta perfis OCI restritos, preserva os registros alcançáveis e remove somente metadados legados reconhecidos; preservar o original e o recibo da transformação, sem fallback silencioso.
- O workflow F3C.3.2 integrado mantém `workflow_dispatch` e job condicionado à `main`; o validador é pinado em `3daeae2b308896edea3a88a0a43e1063d06c1768`. Sua exceção de `actions: read` é limitada ao caminho exato, gatilho manual, único job e condição de `main`. Consulte o [workflow integrado](https://github.com/jacivaldocarvalho/jc-infrapulse/blob/de6f39aee8addb1d99fd161f96e6dae222ad56fd/.github/workflows/f3c3-real-validation.yml).
- O [contrato v1](../security/image-coverage-v1.schema.json) valida estrutura; [Python](../scripts/image_coverage_contract.py) aplica estados e IDs esperados, rejeitando `COMPLETE`. Ele não verifica bytes nem autentica as evidências. Não confundir os estados do contrato com o `COVERAGE_PARTIAL` existente.
- O [leitor virtual](../scripts/oci_virtual_layers.py) recebe bytes já alocados e descritores que o chamador deve verificar independentemente. Limites atuais: 64 camadas, 16 MiB/camada, 64 MiB totais e 10.000 entradas. Aceita USTAR não comprimido; rejeita whiteouts, hardlinks, gzip e caminhos inseguros. Não extrai, segue symlinks ou reconstrói rootfs.

## Evidências e limitações

- No run real, passaram as oito fases `checkout`, `tools`, `extract`, `manifests`, `canonicalize`, `docker_import`, `sign_verify` e `negative_checks`; proveniências Python, npm, backend e nginx passaram na assinatura e verificação. Cosign v3.1.3 foi verificado contra o SHA-256 fixado. Passaram testes negativos de payload, bundle, chave, fingerprint e SBOM; cleanup concluiu.
- Os três ZIPs originais do [run 41238810850, attempt 1](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41238810850), commit `9f237be45ccd6e7977f50f85d37ebcc32d81a2bb`, foram preservados com manifesto em `/home/juninhocarvalho/projetos/infrapulse-evidence-archive/41238810850-attempt-1/`. Diretório `700`, arquivos `600`; hashes e integridade ZIP foram verificados. O manifesto distingue os originais de derivados. Esses hashes não autenticam independentemente o produtor; o armazenamento é local, fora do Git.
- O artifact remoto de imagens deixou de aparecer na listagem após a expiração prevista em 2026-11-01 20:28:10 UTC. A auditoria expira em 2026-11-07 20:29:40 UTC e `ci-evidence` em 2026-11-30 20:29:15 UTC; confirmar retenção antes de depender deles.
- O CI F3C.4 anterior [41349121375](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41349121375) passou, com 13 testes do contrato e 10 do parser. O CI pós-merge também passou. Aviso de Node.js 20 nas actions é dívida técnica separada; não alterar pins sem revisão.

## Worktrees, referências e Entire

- Worktree principal `/home/juninhocarvalho/projects/jc-infrapulse`: branch `develop`, HEAD local `86b80d70f5f7b3966de53994ae9a3924b525abee`. Na consulta, `origin/develop` local ainda apontava para esse SHA, enquanto `develop` remoto apontava para `de6f39a`. Não presumir que a referência local está atualizada.
- Há cinco modificações rastreadas preexistentes: `scripts/ci_evidence_signature.py`, `scripts/provenance.py`, `scripts/provenance_signature.py`, `scripts/test_ci_evidence_signature.py` e `scripts/test_provenance.py`. As versões na worktree coincidiram com o conteúdo correspondente do commit `3daeae2`; não são commits de `develop`.
- Há quatro arquivos não rastreados preexistentes: `.github/workflows/f3c3-real-validation.yml`, `docs/f3c3-real-validation.md`, `scripts/ci_f3c3_validation.py` e `scripts/test_ci_f3c3_validation.py`. Suas versões locais diferem das integradas na main. Uma atualização direta pode colidir com eles.
- Worktree de integração `/tmp/infrapulse-f3c3-sync-develop`: branch `fix/f3c3-sync-develop`, HEAD `2945962f5b9ee50982a3c8e1c1d6ec3627d6a9fd`, limpa no Git; `frontend/node_modules` está ignorado e foi instalado para testes. A branch remota foi preservada. Existem outras worktrees antigas; não removê-las sem revisão e autorização.
- Entire habilitado; na última consulta, nenhum checkpoint aguardava envio. `Entire Gates` do PR #9 apareceu `SUCCESS` no preflight, depois `FAILURE` com “1 commit behind” e posteriormente `NEUTRAL`, sem trail aberto. O head do PR é ancestral direto do merge, mas a causa temporal da mudança do check não foi comprovada. O doctor também reportou sessão anterior encerrada `01a19295-64a8-7eb2-a0b0-e905463e0fc1`, com dois turnos não condensados; nenhum reparo foi autorizado.

## Pendências priorizadas e critérios

1. **F3C.4.2B — rootfs virtual.** Após validação independente de manifest, descritores e ordem das camadas, aplicar whiteouts somente às camadas inferiores e diretórios opacos antes das entradas da própria camada, independentemente da ordem TAR. Modelar substituições e hardlinks em memória; nunca resolver symlinks no host. Rejeitar referências ambíguas, escapes, ciclos e tipos não suportados. Aceite: fixtures para whiteout comum/opaco, mesma camada, diretórios aninhados, substituições, hardlinks válidos/inválidos, colisões e ordem determinística; limites agregados e fail-closed preservados.
2. **F3C.4.2C — inventário**, dependente de B. Definir como ler conteúdo verificado sob limites e registrar caminho, tipo, hash de arquivos regulares, tamanho, modo, UID/GID, links e camada de origem. Aceite: saída determinística, hashes reproduzíveis, limites e rejeição de truncamento; não divulgar conteúdo nem afirmar completude para itens ignorados.
3. **F3C.4.2D — reconciliação**, dependente de C e do contrato v1. Comparar inventários independentes de pacotes/SO e linguagens com o SBOM Trivy, registrando diferenças e itens desconhecidos. Aceite: testes de ausências, extras, versões e analisadores não suportados. Igualdade entre scanners não prova completude.
4. **F3C.4.3 — evidências e política**, proposta dependente de B–D. Versionar escopo, origens, hashes e motivos de lacuna; definir migração compatível e testes negativos. `UNKNOWN` ou `PARTIAL` enquanto faltar prova independente; não aceitar `COMPLETE` automaticamente.
5. **F3C.4.4 — integração**, proposta dependente de 4.3 e revisão de segurança. Definir execução isolada, retenção e gates de PR/push sem enfraquecer F3B.1/F3C.3.2; validar com fixtures e, quando autorizado, runner descartável. Nenhuma etapa por si só estabelece autenticidade do builder ou SLSA.

## Plano de retomada

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
