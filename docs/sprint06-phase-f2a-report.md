# Sprint 06 — F2A

Baseline develop/1288ad74 preservado. Implementação local informativa.
Arquivos: `scripts/audit_vulnerabilities.py`, `scripts/test_vulnerability_audit.py`,
`security/vulnerability-scanners.json` e este relatório.

Execução: `python3 scripts/audit_vulnerabilities.py --pip-audit "$TOOLS/scanner-venv/bin/pip-audit" --trivy "$TOOLS/trivy" --build-images --output-dir "$REPORT_DIR"`.
Scanners isolados: pip-audit 2.10.1, npm 11.16.0, Trivy 0.76.1;
Trivy com hashes de arquivo/executável verificados.

Resultado final: FINDINGS nos quatro alvos, sem erros operacionais:

| Alvo | Cobertura | Ocorrências |
|---|---|---:|
| Python | 52 requisitos únicos, incluindo build | 2 |
| npm | 395 dependências; 14 pacotes vulneráveis | 15 |
| Backend | 87 pacotes Debian; 74 bibliotecas | 176 |
| Nginx | 71 pacotes Alpine | 229 |

Total: 422 ocorrências, não CVEs únicos; 1 CRITICAL, 95 HIGH, 143 MEDIUM,
15 MODERATE, 159 LOW, 9 UNKNOWN. Crítico: brotli-libs/CVE-2026-111497,
1.2.0-r0 → 1.2.1-r0. Setuptools: CVE-2026-59890/GHSA-h35f-9h28-mq5c
(83.0.0), GHSA-xmj3-9gf7-h52w (84.1.0).

Bases: PyPI/Python Advisory Database e npm, APIs sem revisão congelada;
Trivy DB v2/ghcr.io, atualizada 2026-10-31T01:14:32Z, SHA-256 nos JSON.
Quatro relatórios sanitizados e resumo fora do Git; IDs/aliases, severidades,
correções, hashes de entrada e tempos preservados.

Estados: CLEAN/FINDINGS informativos (0), ERROR (2), NOT_RUN/INCOMPLETE (3).
Preliminares ERROR preservadas; suporte BIT/TEMP testado.

PASS: 22 testes novos, 74 regressões existentes, Ruff, actionlint, ShellCheck,
whitespace, Gitleaks e políticas F1. Cleanup próprio; recursos preexistentes preservados.

Custo: 58,1s; CPU-filhos 40,7s; RSS-filhos 506MiB; cache temporário 1,61GiB,
removido; daemon/cache Docker excluídos dessas métricas.

Limitações: severidade Python UNKNOWN; aplicabilidade/backports não triados;
sugestões npm podem envolver downgrades/majors. Sem CI remoto ou correções F2B/F3.
Git sem staging/commit/push; Entire doctor/hooks PASS, checkpoint pendente;
Brain indexa somente HEAD.
