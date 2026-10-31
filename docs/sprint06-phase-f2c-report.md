# Sprint 06 — F2C: correções da revisão

Decisão local: READY_FOR_REVIEW; aprovação remota NOT RUN.

Arquivos: `.github/workflows/ci.yml`, `scripts/validate-images.py`,
`scripts/vulnerability_ci.py`, `scripts/audit_vulnerabilities.py`,
`scripts/test_image_validation.py`, `scripts/test_supply_chain.py`,
`scripts/test_vulnerability_ci.py`, `scripts/test_vulnerability_audit.py`
e este relatório. Dependências, scanners, imagens e políticas F1 intactos.

Fix 1: exportação/empacotamento e upload são steps separados, exclusivamente
informativos, com `continue-on-error`. Outputs usam `outcome`, anterior à
conversão de `conclusion`; falhas chegam ao vulnerability-audit como SCANNER_ERROR.
Validação Docker permanece bloqueante; containers são limpos, tags
retidas após sucesso e removidas pelo cleanup obrigatório `always()`.
As 11 dependências de ci-required e os 13 jobs permanecem.

Download usa pattern exato desta execução e digest-mismatch=error.
Artefato ausente: NOT_RUN/3; artefato recebido sem manifesto, SHA inválido ou
falha de exportação/upload/transporte: SCANNER_ERROR/2. Nenhum vira PASS.
JSON sanitizados mantêm commit/run/attempt/scanners; retenção JSON sete dias,
imagens um dia. FINDINGS continua informativo, sem baseline/exceções aprovados.

Fix 2: Vulnerabilities presente exige lista; {}, false, 0, string vazia, null
e demais tipos falham mesmo com inventário positivo e exit zero. [] e campo
omitido permanecem legítimos. Red-green comprovou dez casos de normalização
inválida e oito casos de handoff antes dos fixes.

Validação: 145 testes de imagens, lifecycle, F1/F2A/F2B/F2C, Ruff, actionlint,
ShellCheck, Gitleaks, whitespace e F1. Consultadas documentação de
[steps/outcome/continue-on-error](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#steps-context) e [fonte fixada](https://raw.githubusercontent.com/actions/download-artifact/9000827ccba6bdab643e8b6fd33ac0654aef8333/src/download-artifact.ts). Fixtures locais não
substituem execução GitHub Actions.

Riscos: APIs/bases mutáveis, tráfego/armazenamento dos arquivos Docker e
cancelamento/timeouts do runner. 422 findings, incluindo 345 pendentes,
preservados sem aprovação; contexto F2B invalidado.

Git develop/1288ad74, staging vazio. Entire doctor/hooks OK, um checkpoint
pendente; Brain cobre HEAD, worktree não indexada. Sem Docker, scans completos,
staging, commit, push, deploy ou F3.
