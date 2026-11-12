"""The optional fixture patch must never alter the honest stock build."""

import hashlib
import os
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/build_attack_fnn.sh"


@pytest.mark.parametrize("reject_patch", [False, True])
def test_patch_applies_only_after_stock_build_and_is_recorded(tmp_path, reject_patch):
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    script = root / "scripts" / SCRIPT.name
    script.write_text(SCRIPT.read_text())
    build = tmp_path / "build"
    (build / ".git").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    events = tmp_path / "events"
    patch = tmp_path / "fixture.patch"
    patch.write_text("test-only fixture patch\n")
    git = bin_dir / "git"
    git.write_text(
        '#!/bin/sh\necho "git $*" >> "$EVENTS"\n'
        'if [ "$3" = "rev-parse" ]; then echo fixture-sha; fi\n'
        'if [ "$3" = "apply" ] && [ "$4" = "--check" ] '
        '&& [ "$REJECT_PATCH" = "1" ]; then exit 1; fi\n'
    )
    git.chmod(0o755)
    cargo = bin_dir / "cargo"
    cargo.write_text(
        '#!/bin/sh\necho "cargo" >> "$EVENTS"\n'
        'mkdir -p "$FIBER_BUILD_DIR/target/debug"\n'
        'printf "#!/bin/sh\\nexit 0\\n" > "$FIBER_BUILD_DIR/target/debug/fnn"\n'
    )
    cargo.chmod(0o755)
    destination = root / "download/fiber/attacker/fnn"
    provenance = root / "provenance.env"
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "EVENTS": str(events),
        "REJECT_PATCH": "1" if reject_patch else "0",
        "FIBER_BUILD_DIR": str(build),
        "ATTACK_DESTINATION": str(destination),
        "PROVENANCE_FILE": str(provenance),
        "ATTACK_FIBER_PATCH": str(patch),
    }
    result = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True
    )
    lines = events.read_text().splitlines()
    build_indices = [i for i, line in enumerate(lines) if line == "cargo"]
    patch_check = next(i for i, line in enumerate(lines) if " apply --check " in line)
    assert build_indices[0] < patch_check
    assert (root / "download/fiber/current/fnn").exists()
    if reject_patch:
        assert result.returncode != 0
        assert len(build_indices) == 1
        assert not destination.exists()
        assert not provenance.exists()
    else:
        assert result.returncode == 0, result.stderr
        patch_apply = next(
            i for i, line in enumerate(lines) if f" apply {patch}" in line
        )
        assert patch_check < patch_apply < build_indices[1]
        patch_hash = hashlib.sha256(patch.read_bytes()).hexdigest()
        assert f"attack_fixture_patch_sha256={patch_hash}" in provenance.read_text()
        assert destination.exists()
