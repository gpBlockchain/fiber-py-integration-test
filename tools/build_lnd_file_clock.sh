#!/usr/bin/env bash
# Build a test-only LND binary whose NewDefaultClock reads ClusterClock's
# +N timestamp file. This is not an upstream LND patch and must not be
# published to lightningnetwork/lnd. Official CCH tests keep the stock LND
# binary when ClusterClock is off.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:-${FIBER_TEST_LND_SRC:-/tmp/lnd-v0.20.0-beta.rc1}}"
DEST="${2:-${ROOT}/download/lnd-file-clock}"
TAG="${LND_FILE_CLOCK_TAG:-v0.20.0-beta.rc1}"
CLOCK_SRC="${ROOT}/tools/lnd_file_offset_clock"

if [[ ! -d "${SRC}" ]]; then
  echo "Cloning LND ${TAG} into ${SRC}" >&2
  git clone --depth 1 --branch "${TAG}" --recurse-submodules \
    https://github.com/lightningnetwork/lnd.git "${SRC}"
fi

if [[ ! -d "${SRC}/clock" ]]; then
  echo "LND checkout at ${SRC} has no clock/ package" >&2
  exit 1
fi

cp "${CLOCK_SRC}/default_clock.go" "${SRC}/clock/default_clock.go"
cp "${CLOCK_SRC}/file_offset_clock.go" "${SRC}/clock/file_offset_clock.go"
cp "${CLOCK_SRC}/file_offset_clock_test.go" "${SRC}/clock/file_offset_clock_test.go"
# Official addinvoice.go uses time.Now() for the invoice timestamp.
if ! grep -q 'clock.NewDefaultClock().Now()' "${SRC}/lnrpc/invoicesrpc/addinvoice.go"; then
  (cd "${SRC}" && patch -p1 < "${CLOCK_SRC}/addinvoice.go.patch")
fi

# Main module depends on clock@v1.1.1 from the module cache, not ./clock.
(
  cd "${SRC}"
  go mod edit -replace github.com/lightningnetwork/lnd/clock=./clock
)

(
  cd "${SRC}/clock"
  go test ./...
)

mkdir -p "${DEST}"
(
  cd "${SRC}"
  CGO_ENABLED=0 go build -trimpath \
    -ldflags="-s -w -X github.com/lightningnetwork/lnd/build.Commit=${TAG}-file-offset-clock" \
    -o "${DEST}/lnd" ./cmd/lnd
  CGO_ENABLED=0 go build -trimpath \
    -ldflags="-s -w -X github.com/lightningnetwork/lnd/build.Commit=${TAG}-file-offset-clock" \
    -o "${DEST}/lncli" ./cmd/lncli
)

printf 'file-offset-clock\ntag=%s\n' "${TAG}" > "${DEST}/FEATURES"
echo "Built file-offset-clock LND at ${DEST}" >&2
