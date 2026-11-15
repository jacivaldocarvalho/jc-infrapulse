# Sprint 06 — F2B

Local, sem atualizações. Arquivos: `scripts/triage_vulnerabilities.py`,
`scripts/test_vulnerability_triage.py`,
`security/vulnerability-{policy,assessments,exceptions}.json` e este relatório.

Uso: `python3 scripts/triage_vulnerabilities.py --report "$F2A/summary.json" --output "$OUT/triage.json"`.
Reutilizados os JSON F2A; nenhum scan completo. Exportações temporárias dos
runtimes fixados forneceram metadados/ELF, sem carregar imagens ou criar containers.

Resultado FINDINGS: 422 registros únicos por scanner/pacote/versão/advisory/alvo;
420 combinações componente/advisory. Aliases e duplicatas são consolidados sem
fundir versões/alvos. Classificação separa runtime, build-only, vendorizadas,
diretas, transitivas e sistema; ferramentas presentes no runtime não viram build-only.

Prioridades: 47 ocorrências Alpine HIGH/CRITICAL corrigíveis, confirmadas contra
APK/secdb 3.23; duas urllib3 vendorizadas no pip, corrigidas em 2.8.0.
[CVE-2026-111497](https://github.com/google/brotli/releases/tag/v1.2.1):
brotli-libs 1.2.0-r0 → 1.2.1-r0; exposição HTTP não demonstrada.

Nove avaliações contextuais não aplicáveis preservam os findings: setuptools
Windows/macOS e componentes removidos do vendoring pip/msgpack.
[Vendoring oficial](https://raw.githubusercontent.com/pypa/pip/26.2.2/pyproject.toml).
19 correspondências Debian permanecem afetadas, sem correção Trixie confirmada;
fix em unstable/no-dsa não comprova backport. Outras 345 requerem triagem adicional.

Política somente proposta: bloquear novas HIGH/CRITICAL confirmadas/corrigíveis;
incertezas relevantes exigem revisão. Resultado candidato: 49 confirmadas, quatro
UNKNOWN corrigíveis pendentes. Baseline não aprovado; exceções vazias.
Exceções exigem identidade/fingerprint exatos, justificativa, responsável,
aprovador, referência e expiração até 30 dias. Contexto/versão/risco alterados
reabrem revisão; expiração nunca aprova silenciosamente.

Estados/códigos: PASS/0, FINDINGS/1, SCANNER_ERROR/2, NOT_RUN/3;
decisão proposta separada, sem ocultar backlog. Erros/ausências não admitem waiver.

PASS: 23 testes novos, 96 regressões, Ruff, actionlint, ShellCheck, Gitleaks,
whitespace e política F1. Workflow/12 jobs intactos.
Git develop/1288ad74, staging vazio. Entire doctor/hooks PASS, um checkpoint
pendente; Brain cobre HEAD.
Riscos: fontes divergentes setuptools, backports, alcance não reproduzido.
Sem staging/commit/push/deploy; F3 não iniciada.
