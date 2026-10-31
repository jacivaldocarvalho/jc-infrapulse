#!/usr/bin/env bash
# Third-party artifacts are verified separately from this checkout's local package.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

case "${1:-}" in
  runtime) requirements='requirements-runtime.lock' ;;
  dev) requirements='requirements.lock' ;;
  *) printf 'Expected runtime or dev installation mode\n' >&2; exit 2 ;;
esac
project_options=()
case "${2:-}" in
  '') ;;
  --editable) project_options+=(--editable) ;;
  *) printf 'Unexpected local project installation option\n' >&2; exit 2 ;;
esac
if (( $# > 2 )); then
  printf 'Unexpected installation arguments\n' >&2
  exit 2
fi

python -m pip install --require-hashes --only-binary=:all: --force-reinstall -r requirements-build.lock
python -m pip install --require-hashes --only-binary=:all: -r "$requirements"
python -m pip install --no-index --no-deps --no-build-isolation "${project_options[@]}" .
python -m pip check
