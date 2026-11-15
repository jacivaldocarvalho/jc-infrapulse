# Sprint 06 — F3C.3.2 and F3C.4 handoff

## Resumption update — 2026-11-14

- PRs #11–#14 were confirmed merged; all four post-merge CI runs on `develop` completed successfully. The PR #14 run is [43645063533](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/43645063533).
- F3C.4.2B–D are complete. The v1 contract and negative tests retain `COVERAGE_PARTIAL` and reject `COMPLETE` without independent proof.
- In this resumption, the reader began accepting the GNU and local PAX `path`/`linkpath` headers observed in real archives, with limits of 128 MiB per layer, 256 MiB aggregate, 1 MiB per PAX header, and 100.000 entries. Gzip, global PAX, and unobserved extensions remain rejected.
- The two preserved archives from run `41238810850` were verified against handoff hashes, config IDs, and DiffIDs; the parser read all 24 layers. Details and limits are in [F3C.3.2 integration](sprint06-f3c3-reconciliation.md).
- Direct integration of the F3C.3.2 verifier with reconciliation is in [PR #15](https://github.com/jacivaldocarvalho/jc-infrapulse/pull/15); its CI run [43655776080](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/43655776080) passed, and the merge into `develop` remains pending. The historical package did not retain disposable Cosign bundles; CI tests real signatures with a disposable fixture.
- Current retention: image handoff 1 day, sanitized reports 7 days, and unsigned CI evidence 30 days. Temporary F3C.3.2 bundles are not published.

**Recorded on 2026-11-01, UTC.** Reconfirm references and retention on resumption; remote states may change.

## Executive summary

- F3C.3.2 passed real validation and was synchronized to `develop` through [PR #9](https://github.com/jacivaldocarvalho/jc-infrapulse/pull/9), merge `de6f39aee8addb1d99fd161f96e6dae222ad56fd`.
- [Post-merge CI 41353516492](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41353516492) completed successfully, including `security-foundations` and `ci-required`.
- F3C.4.1A and F3C.4.2A–D are complete. F3C.4.3–4.4 follow the coverage and integration criteria below and in the resumption update.
- Image coverage remains `COVERAGE_PARTIAL`; no match, signature, or successful read automatically promotes coverage.

## Status by phase

| Phase | Status and evidence |
| --- | --- |
| F3C.3.2 | Complete: [real run 41344302188, attempt 1](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41344302188), `main` `350a404019ae1667a9e5805fe6b225b461401f7d`; integrated into `develop` through PR #9. |
| F3C.4.1A | Complete: v1 contract in commit `ff07878b53ab0bb10cd05fbf9c04abb1675fa1c1`; 13 tests. |
| F3C.4.2A | Complete: virtual USTAR/GNU reader and restricted PAX; expanded limits validated with preserved images and negative regressions in the required job. |
| F3C.4.2B–D | Completed through PRs #11–#13 on 2026-11-14; post-merge CI for all three PRs passed. |
| F3C.4.3 | v1 contract and negative gates integrated; coverage remains `COVERAGE_PARTIAL`. |
| F3C.4.4 | PR #14 integrated signed manifest/SBOM verification with reconciliation. The link to F3C.3.2 bundles is in PR #15, with passing CI; awaiting merge. |

## Architecture and security decisions

- F3C.2 evidence covers Python/npm locks and only the `backend-image` and `nginx-image` images. F3B.1 comparison regenerates the inventory with pinned Trivy and compares components and relationships. This does not measure detection completeness. See [F3C.2](../../ci-evidence-f3c2.md) and [F3B.1](../../provenance-f3b1.md).
- Require complete IDs and logical names from independent trusted sources. An ID read only from an unsigned archive or manifest demonstrates consistency, not source authenticity. Preserve hashes, pins, full statement comparison, and fail-closed behavior.
- Verified Cosign signatures and trust policy are evidence of integrity and key authorization, not of an authenticated builder, reproducible generation, or a SLSA level. Real validation used disposable keys; do not use operational keys in tests.
- Original and canonicalized archives have different hashes. The [canonicalizer](../../oci-canonical-exporter.md) supports restricted OCI profiles, preserves reachable records, and removes only recognized legacy metadata; retain the original and transformation receipt without silent fallback.
- The integrated F3C.3.2 workflow retains `workflow_dispatch` and a job conditioned on `main`; the validator is pinned to `3daeae2b308896edea3a88a0a43e1063d06c1768`. Its `actions: read` exception is limited to the exact path, manual trigger, single job, and `main` condition. See the [integrated workflow](https://github.com/jacivaldocarvalho/jc-infrapulse/blob/de6f39aee8addb1d99fd161f96e6dae222ad56fd/.github/workflows/f3c3-real-validation.yml).
- The [v1 contract](../../../security/image-coverage-v1.schema.json) validates structure; [Python](../../../scripts/image_coverage_contract.py) enforces expected states and IDs and rejects `COMPLETE`. It does not verify bytes or authenticate evidence. Do not confuse contract states with the existing `COVERAGE_PARTIAL`.
- The [virtual reader](../../../scripts/oci_virtual_layers.py) receives already allocated bytes and descriptors that the caller must independently verify. Limits: 64 layers, 128 MiB/layer, 256 MiB total, 100.000 entries, and 1 MiB per PAX header. It accepts USTAR, observed GNU headers, and local PAX only for `path`/`linkpath`; gzip, global PAX, and unknown extensions remain rejected. Symlinks are recorded without host resolution; hardlinks must point to an already known regular file. Reconstruction remains virtual and `COVERAGE_PARTIAL`.

## Evidence and limitations

- In the real run, all eight phases passed: `checkout`, `tools`, `extract`, `manifests`, `canonicalize`, `docker_import`, `sign_verify`, and `negative_checks`; Python, npm, backend, and nginx provenance passed signing and verification. Cosign v3.1.3 was checked against its pinned SHA-256. Negative payload, bundle, key, fingerprint, and SBOM tests passed; cleanup completed.
- The three original ZIPs from [run 41238810850, attempt 1](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41238810850), commit `9f237be45ccd6e7977f50f85d37ebcc32d81a2bb`, were preserved with a manifest in `/home/juninhocarvalho/projetos/infrapulse-evidence-archive/41238810850-attempt-1/`. Directory `700`, files `600`; hashes and ZIP integrity were verified. The manifest distinguishes originals from derivatives. Those hashes do not independently authenticate the producer; storage is local, outside Git.
- The remote image artifact stopped appearing in the list after its expected expiration on 2026-11-01 20:28:10 UTC. The audit expires on 2026-11-07 20:29:40 UTC and `ci-evidence` on 2026-11-30 20:29:15 UTC; confirm retention before relying on them.
- The earlier F3C.4 CI [41349121375](https://github.com/jacivaldocarvalho/jc-infrapulse/actions/runs/41349121375) passed, with 13 contract tests and 10 parser tests. Post-merge CI also passed. The Node.js 20 warning in actions is separate technical debt; do not change pins without review.
- On 2026-11-14, the two preserved Docker archives from run `41238810850` were validated without loading them into the daemon: handoff hashes and configuration IDs matched; 12 layers and DiffIDs per image; the virtual parser traversed 224.294.912 bytes/11.779 backend entries and 56.756.736 bytes/2.291 nginx entries. The archives contained GNU and PAX `path`/`linkpath` headers. This validates that image/platform pair and observed formats; it does not demonstrate SBOM completeness or authenticate the builder.

## Historical worktree, reference, and Entire snapshot — 2026-11-01

- Main worktree `/home/juninhocarvalho/projects/jc-infrapulse`: branch `develop`, local HEAD `86b80d70f5f7b3966de53994ae9a3924b525abee`. At the time of the query, local `origin/develop` still pointed to this SHA, while remote `develop` pointed to `de6f39a`. Do not assume the local reference is current.
- Five tracked modifications predated this work: `scripts/ci_evidence_signature.py`, `scripts/provenance.py`, `scripts/provenance_signature.py`, `scripts/test_ci_evidence_signature.py`, and `scripts/test_provenance.py`. The worktree versions matched the corresponding content in commit `3daeae2`; they were not `develop` commits.
- Four untracked files predated this work: `.github/workflows/f3c3-real-validation.yml`, `docs/f3c3-real-validation.md`, `scripts/ci_f3c3_validation.py`, and `scripts/test_ci_f3c3_validation.py`. Their local versions differed from those integrated into main. A direct update could conflict with them.
- Integration worktree `/tmp/infrapulse-f3c3-sync-develop`: branch `fix/f3c3-sync-develop`, HEAD `2945962f5b9ee50982a3c8e1c1d6ec3627d6a9fd`, clean in Git; `frontend/node_modules` is ignored and was installed for tests. The remote branch was preserved. Other old worktrees exist; do not remove them without review and authorization.
- Entire was enabled; at the last query, no checkpoint was awaiting upload. `Entire Gates` for PR #9 showed `SUCCESS` during preflight, then `FAILURE` with “1 commit behind,” and later `NEUTRAL`, without an open trail. The PR head is a direct ancestor of the merge, but the timing-related cause of the check change was not established. Doctor also reported a closed prior session `01a19295-64a8-7eb2-a0b0-e905463e0fc1` with two uncompacted turns; no repair was authorized.

## Remaining closure

1. **F3C.4.2B–D:** completed and merged in PRs #11–#13; their post-merge CI runs passed.
2. **F3C.4.3:** the contract and negative regressions are in required CI. Policy retains `COVERAGE_PARTIAL` because the data does not prove completeness.
3. **F3C.4.4:** PR #14 integrated signed manifest/SBOM verification. The branch in this resumption adds the link to complete F3C.3.2 bundles and covers the verified byte snapshot. Publishing the branch, opening and reviewing the PR, and obtaining post-merge CI remain pending.
4. **Historical bundles:** Cosign bundles used in the real run were not retained, so the historical signature cannot be repeated. Existing CI validates real signatures with disposable keys and negative scenarios. Results from preserved real images validate the parser and inventory but do not authenticate the builder.
5. **Operational retention:** image handoff 1 day, unsigned CI evidence 30 days, and sanitized reports 7 days. Disposable bundles are not published. This policy limits the window for repeating validations that depend on the handoff.

**Closure criterion:** integrate the local closure commit through a PR and confirm post-merge CI success. Do not promote coverage to `COMPLETE` without additional independent evidence.

## Original resumption plan — superseded by the 2026-11-14 update

1. Reread `AGENTS.md` and `.entire/agent-guide.md`; check current state before assuming this record is still up to date.
2. Inspect the main worktree and its changes without discarding them. To synchronize with remote `develop`, first plan a separate worktree/branch and compare the four untracked and five modified files; do not pull directly into the current worktree. Obtain authorization before updating refs or creating a worktree.
3. Review 4.2B scope and OCI semantics with adversarial examples. Implement in small increments only after authorization; preserve the original worktree and private evidence.
4. Review the uncompacted Entire session in a separate task. Do not run `entire doctor --force`, install or disable hooks, or alter checkpoints without specific authorization.

Suggested read-only diagnostics from the chosen worktree:

```sh
git status --short --branch
git worktree list --porcelain
git ls-remote origin refs/heads/main refs/heads/develop
gh pr view 9 --repo jacivaldocarvalho/jc-infrapulse
gh run view 41353516492 --repo jacivaldocarvalho/jc-infrapulse
entire status
entire checkpoint list --outgoing
```

Suggested local validation **in the integration worktree**, after confirming dependencies and without local Docker:

```sh
python3 -B scripts/test_ci_f3c3_validation.py
python3 -B scripts/test_supply_chain.py
python3 -B scripts/test_locked_install.py
python3 -B scripts/test_image_coverage_contract.py
python3 -B scripts/test_oci_virtual_layers.py
git diff --check
```

Policy tests use `frontend/node_modules/js-yaml` at the locked version; a new worktree may need an isolated installation without lifecycle scripts. Tests may create temporary files. Confirm available tools and run Ruff and actionlint where applicable.

## General approval criteria and risks

- Preserve required CI, minimum permissions, action/tool pins, and F3B.1/F3C.3.2 controls. Policy changes should be specific and have positive and negative regression cases.
- Coverage must declare its universe, platform, independent sources, exclusions, and gaps. `COMPLETE` can be considered only after independent proof for a bounded scope and contract review; Trivy success or self-comparison is insufficient.
- Parser, identity, hash, limit, tool, or unsupported-format failures must not be converted into success. Test paths, collisions, ambiguous TAR, symlinks, hardlinks, whiteouts, and limits before using real data.
- Do not run pull, push, commit, merge, rebase, reset, stash, local Docker, dispatch, artifact publication, worktree cleanup, or manual Entire operations without applicable authorization. Never use operational keys in tests.

## Suggested initial prompt

> Leia `AGENTS.md`, `.entire/agent-guide.md` e `docs/sprint06-handoff.md`. Confirme em modo somente leitura as referências remotas, as duas worktrees, as alterações preexistentes e o estado Entire. Proponha um plano isolado para F3C.4.2B com whiteouts, diretórios opacos e hardlinks, mantendo `COVERAGE_PARTIAL`. Não sincronize, implemente, execute Docker nem altere checkpoints até receber autorização explícita.
