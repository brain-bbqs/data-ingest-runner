#!/usr/bin/env bash
# Installs the runner's tools (Apptainer and git-annex from conda-forge via
# micromamba, DataLad, datalad-container and con-duct from PyPI) into
# ~/ember-tools, with no root needed.
#
#   setup-tools.sh            install only if ~/ember-tools is missing or incomplete
#   setup-tools.sh --rebuild  wipe ~/ember-tools and install the latest versions
#
# Inside a GitHub Actions job it also puts ~/ember-tools/bin on PATH for the
# job's later steps.
set -euo pipefail

tools="$HOME/ember-tools"
rebuild=false
case "${1:-}" in
  "") ;;
  --rebuild) rebuild=true ;;
  *) echo "usage: $0 [--rebuild]" >&2; exit 2 ;;
esac

if $rebuild || [ ! -x "$tools/bin/duct" ]; then
  # Built in place: a conda environment hardcodes its own prefix, so it
  # can't be built elsewhere and moved here.
  rm -rf "$tools"
  scratch="$(mktemp -d)"
  trap 'rm -rf "$scratch"' EXIT
  curl -fsSL -o "$scratch/micromamba" \
    https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-linux-64
  chmod +x "$scratch/micromamba"
  "$scratch/micromamba" create -y -q -r "$scratch/root" -p "$tools" \
    -c conda-forge apptainer git-annex python=3.12 pip
  "$tools/bin/pip" install -q datalad datalad-container con-duct
fi

if [ -n "${GITHUB_PATH:-}" ]; then
  echo "$tools/bin" >> "$GITHUB_PATH"
fi
