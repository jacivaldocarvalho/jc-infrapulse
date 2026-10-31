# Sprint 06 — F1A

PASS local; CI remoto NOT RUN. Branch develop, HEAD bee93e8, sem staging/commit/push.

Arquivos: `.github/workflows/ci.yml`, `security/gitleaks.toml`,
`scripts/check_secrets.py`, `scripts/check_supply_chain.py`,
`scripts/test_supply_chain.py` e este relatório.

Novo gate obrigatório `security-foundations`: Gitleaks, integridade npm e políticas
de Actions. Dez jobs anteriores estruturalmente inalterados; `ci-required` exige
também o novo job. Parser js-yaml 4.3.2 já travado, sem dependência adicional.

[Gitleaks 8.30.1](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1), Linux x64,
SHA-256 `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`.
Fixtures sintéticas positivas/negativas; checkout versionável e ancestrais do HEAD;
sem baseline/exclusões próprias ou permissões inline. Saída: regra, linha,
hash do caminho; nenhum match/token/log bruto publicado. Erros falham fechados.

FAIL/F1B_PENDING: 49 requisitos Python sem hashes; setuptools inexato/não
travado e pip implícito; quatro FROM externos sem digest. Modo `--enforce all`
retorna 1; ativo retorna 0 com auditoria explicitamente FAIL. F1B deverá também
aplicar instalação Python com verificação efetiva de hashes.

PASS: `python3 scripts/test_supply_chain.py` (25), lifecycle (22), inspetor (21);
Ruff check/formatação, actionlint, ShellCheck, whitespace incluindo arquivos novos;
35 cenários do agregador, sanitização e instalação descartável sem `.env`.

Duração local: npm ci/cache existente 3,12s; Gitleaks/download 3,64s; políticas
0,08s; regressões novas 1,26s. npm: 370 pacotes, 323MiB; overhead remoto não medido.

Entire: doctor/hooks PASS, um checkpoint anterior pendente; Brain cobre HEAD,
alterações locais ainda não indexadas. Nenhuma configuração modificada.

Riscos: falsos positivos, histórico crescente, download externo, parser transitivo.
Digest não comprova proveniência; CVEs/SBOM/atestações e imagens Compose não
verificados. Docker não executado; locks/imagens/Dockerfiles intactos.
Pronto para revisão, aguardando autorização para versionamento e validação remota.
