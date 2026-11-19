#!/usr/bin/env bash
# Build the jemalloc-safe libfaketime used by ClusterClock (Linux CI).
# Stock distro packages deadlock with CKB/fnn jemalloc; this matches the
# pinned source and patches previously used by fiber_test_fake_time.
set -euo pipefail
dest_dir="${1:-download/libfaketime}"
mkdir -p "$dest_dir"
dest_dir="$(cd "$dest_dir" && pwd)"
src="${LIBFAKETIME_SRC:-/tmp/libfaketime-ci}"
pin="e8d8c8531d824722f23e4e4d6ba6bd85c023f8d5"
if [[ ! -d "$src/.git" ]]; then
  git clone --quiet https://github.com/wolfcw/libfaketime.git "$src"
fi
git -C "$src" fetch --quiet origin "$pin" || true
git -C "$src" checkout --quiet "$pin"
# Drop internal-call/timer hooks and 64-bit clock_gettime lookups that
# re-enter jemalloc during process init.
sed -i 's/ -DFAKE_INTERNAL_CALLS//; s/ -DFAKE_TIMERS//' "$src/src/Makefile"
sed -i -E 's/dlsym\(RTLD_NEXT, "(__clock_gettime64|clock_gettime64)"\)/NULL/' "$src/src/libfaketime.c"
make -C "$src/src" libfaketime.so.1
cp -f "$src/src/libfaketime.so.1" "$dest_dir/libfaketime.so.1"
echo "installed $dest_dir/libfaketime.so.1"
