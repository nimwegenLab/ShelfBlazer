#!/usr/bin/env bash
# Build the image for the x86_64 VM and write a transferable archive.
#   deploy/build-export.sh [tag]        -> deploy/out/shelfblazer-<tag>.tar.gz   (tag defaults to the version in app/pyproject.toml)
# On the VM:  gunzip -c shelfblazer-<tag>.tar.gz | docker load   then   SB_TAG=<tag> docker compose up -d
set -euo pipefail
cd "$(dirname "$0")/.."
TAG="${1:-$(sed -n 's/^version = "\(.*\)"/\1/p' app/pyproject.toml)}"      # default: the declared version, e.g. 0.1.0
PLATFORM="${PLATFORM:-linux/amd64}"
mkdir -p deploy/out
docker buildx build --platform "$PLATFORM" -t "shelfblazer:$TAG" --load .
docker save "shelfblazer:$TAG" | gzip > "deploy/out/shelfblazer-$TAG.tar.gz"
echo "Wrote deploy/out/shelfblazer-$TAG.tar.gz ($(du -h "deploy/out/shelfblazer-$TAG.tar.gz" | cut -f1))"
