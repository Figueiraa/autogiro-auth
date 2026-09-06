#!/usr/bin/env bash
# Monta o diretório build/ com o código-fonte e as dependências da Lambda.
#
# A arquitetura alvo é arm64 (Graviton), por isso as wheels são baixadas para
# essa plataforma — instalar no host produziria binários x86_64 incompatíveis.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build"

rm -rf "$BUILD"
mkdir -p "$BUILD"

cp -r "$ROOT/src" "$BUILD/"

pip install \
  --requirement "$ROOT/requirements.txt" \
  --target "$BUILD" \
  --platform manylinux2014_aarch64 \
  --implementation cp \
  --python-version 3.11 \
  --only-binary=:all: \
  --upgrade

# Remove artefatos que só incham o pacote.
find "$BUILD" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$BUILD" -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true

echo "Pacote pronto em $BUILD"
