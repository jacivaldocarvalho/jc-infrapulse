# Sprint 06 — F1B

Implementação local; remoto NOT RUN. Baseline develop/bee93e8 preservado.

Arquivos F1B: `.github/{workflows/ci.yml,dependabot.yml}`, `README.md`,
`backend/{Dockerfile,pyproject.toml,requirements.lock,requirements-runtime.lock,requirements-build.lock,install-locked.sh}`,
`frontend/Dockerfile`, `scripts/{check_supply_chain.py,test_locked_install.py}` e este relatório.
F1A preservada.

PASS: 49 versões originais intactas, hashes SHA-256 obtidos do PyPI oficial;
38 requisitos de runtime; bootstrap pip 26.2.2, setuptools 82.0.1 (compatível
com intervalo anterior), wheel 0.48.0 e packaging 26.3. Instalação exige hashes/wheels;
pacote local instalado sem índice, dependências ou isolamento que baixe ferramentas.
Hash adulterado rejeitado em download real. Lockfiles comparados contra divergências.

PASS: quatro FROM usam três digests de índices multi-arquitetura verificados no
registry, mantendo tags/plataformas. Builds backend/runtime, backend/test,
frontend/build e Nginx; comparação independente de tipos OpenAPI, API health 200,
readiness 503 sem banco, worker/SIGTERM, heartbeat negativo e assets/health Nginx.

PASS: 184 unitárias, 25 regressões F1A, seis F1B, 22 lifecycle, 21 inspetor;
Gitleaks/fixtures sanitizadas; Ruff/formatação, actionlint, ShellCheck e whitespace.
Auditoria completa sem violações; CI usa `--enforce all`, preservando dez jobs,
security-foundations e ci-required.

Dependabot semanal para pip/npm/Docker/Actions, PRs destinados a develop,
limite três por ecossistema, sem automerge. Locks `.lock` exigem sincronização
manual revisada; ativação/funcionamento remoto ainda não verificados.

Cleanup limitado aos recursos próprios; cache compartilhado mantido. Recursos
preexistentes preservados. Instalação: 10,53s; unitárias: 8,56s; builds/smokes finais: 77,80s.
Warnings pip/root durante build permanecem visíveis; runtimes sem root.

Riscos: amd64 validado; demais plataformas não executadas; hashes não comprovam
proveniência; ferramentas de build permanecem no runtime;
CVEs e pacotes APT não auditados. Git sem staging/commit/push;
Entire doctor/hooks PASS, um checkpoint anterior pendente; Brain restrito ao HEAD.
Decisão: revisar alterações antes de autorizar versionamento e CI remoto.
