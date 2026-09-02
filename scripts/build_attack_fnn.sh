#!/usr/bin/env bash
set -euo pipefail

# Keep the honest node and the instrumented peer on one protocol baseline.
# Defaults follow branches; workflow_dispatch may override either ref.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

STOCK_FIBER_REPOSITORY="${STOCK_FIBER_REPOSITORY:-https://github.com/nervosnetwork/fiber.git}"
STOCK_FIBER_REF="${STOCK_FIBER_REF:-develop}"
ATTACK_FIBER_REPOSITORY="${ATTACK_FIBER_REPOSITORY:-https://github.com/gpBlockchain/fiber.git}"
ATTACK_FIBER_REF="${ATTACK_FIBER_REF:-p2p-tap}"
FIBER_BUILD_DIR="${FIBER_BUILD_DIR:-$ROOT_DIR/.build/fiber-attack-fnn}"
PROVENANCE_FILE="${PROVENANCE_FILE:-$ROOT_DIR/download/fiber/provenance.env}"

sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

checkout_ref() {
    local remote="$1"
    local ref="$2"

    git -C "$FIBER_BUILD_DIR" fetch --depth=50 "$remote" "$ref"
    git -C "$FIBER_BUILD_DIR" checkout --detach --force FETCH_HEAD
    git -C "$FIBER_BUILD_DIR" rev-parse HEAD
}

build_fnn() {
    local destination="$1"

    # The p2p-tap RPC registration is intentionally debug-only. Build the
    # stock binary with the same profile so behavior differs only by source.
    cargo build \
        --locked \
        --bin fnn \
        --manifest-path "$FIBER_BUILD_DIR/Cargo.toml"
    install -m 0755 "$FIBER_BUILD_DIR/target/debug/fnn" "$destination"
}

mkdir -p "$(dirname "$FIBER_BUILD_DIR")"
if [[ ! -d "$FIBER_BUILD_DIR/.git" ]]; then
    git clone --depth=50 --filter=blob:none --no-checkout \
        "$STOCK_FIBER_REPOSITORY" "$FIBER_BUILD_DIR"
fi

git -C "$FIBER_BUILD_DIR" remote set-url origin "$STOCK_FIBER_REPOSITORY"
if git -C "$FIBER_BUILD_DIR" remote get-url attack >/dev/null 2>&1; then
    git -C "$FIBER_BUILD_DIR" remote set-url attack "$ATTACK_FIBER_REPOSITORY"
else
    git -C "$FIBER_BUILD_DIR" remote add attack "$ATTACK_FIBER_REPOSITORY"
fi

mkdir -p \
    "$ROOT_DIR/download/fiber/current" \
    "$ROOT_DIR/download/fiber/attack" \
    "$(dirname "$PROVENANCE_FILE")"

stock_sha="$(checkout_ref origin "$STOCK_FIBER_REF")"
build_fnn "$ROOT_DIR/download/fiber/current/fnn"

attack_sha="$(checkout_ref attack "$ATTACK_FIBER_REF")"
if ! git -C "$FIBER_BUILD_DIR" merge-base --is-ancestor "$stock_sha" "$attack_sha"; then
    echo "attack FNN commit must descend from stock FNN commit" >&2
    exit 1
fi
build_fnn "$ROOT_DIR/download/fiber/attack/fnn"

stock_hash="$(sha256_file "$ROOT_DIR/download/fiber/current/fnn")"
attack_hash="$(sha256_file "$ROOT_DIR/download/fiber/attack/fnn")"

cat >"$PROVENANCE_FILE" <<PROVENANCE
stock_fiber_repository=$STOCK_FIBER_REPOSITORY
stock_fiber_ref=$STOCK_FIBER_REF
stock_fiber_sha=$stock_sha
stock_fnn_profile=debug
stock_fnn_sha256=$stock_hash
attack_fiber_repository=$ATTACK_FIBER_REPOSITORY
attack_fiber_ref=$ATTACK_FIBER_REF
attack_fiber_sha=$attack_sha
attack_base_verified=true
attack_fnn_profile=debug
attack_fnn_sha256=$attack_hash
PROVENANCE

cat "$PROVENANCE_FILE"
