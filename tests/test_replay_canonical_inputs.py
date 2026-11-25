"""Real snapshots, relocated bundles, and replay input-layer integration."""

import json
import sys
from pathlib import Path

import pytest

from pfig import ValidationError, run
from pfig.bundle import load_bundle
from pfig.replay.runner import replay_run
from pfig.replay.snapshot_loader import load_and_validate_snapshot
from pfig.replay.validate import replay_validate
from pfig.replay.workspace import ReplayWorkspace
from pfig.snapshot import compute_graph_digest, load_snapshot


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    # Keep snapshot git/patch collection outside the repository. No installation
    # or code restoration is needed: replay uses the current test interpreter.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "base.pfig").write_text(
        "external device\nrate = 1\nobj = dict(device=@device, rate=@rate)\n"
    )
    root = tmp_path / "root.pfig"
    root.write_text(
        'include "./base.pfig" as a\ninclude "./base.pfig" as b\n'
        "result = dict(left=@a/obj, right=@b/obj)\n__entrypoint__ = @result.copy\n"
    )
    profiles = [tmp_path / "first.pfig", tmp_path / "second.pfig"]
    profiles[0].write_text('override a/device = "cpu"\n')
    profiles[1].write_text('override b/device = "cuda"\n')
    result = run(
        root,
        externals={"a/rate": 2},
        profile_paths=profiles,
        snapshot_dir=tmp_path / "snapshots",
    )
    assert result["left"] is result["right"]
    assert result["left"] == {"device": "cuda", "rate": 2}

    snapshot_path = next((tmp_path / "snapshots").iterdir())
    snapshot = load_and_validate_snapshot(snapshot_path)
    ws = ReplayWorkspace(tmp_path / "relocated", snapshot, prepare_ok=True)
    ws.create_dirs()
    ws.venv_python = sys.executable
    load_bundle(snapshot.bundle_tarball, extract_dir=ws.bundle_dir)
    return ws


def test_snapshot_inputs_are_compact_and_baseline_digest_is_preserved(workspace):
    ws = workspace
    assert ws.snapshot.externals == {"b/device": "cuda", "a/rate": 2}
    recorded = ws.snapshot.raw["environment"]["externals"]
    assert recorded["b/device"]["source"].startswith("profile:")
    assert recorded["a/rate"]["source"] == "cli"

    # Preserve the pre-refactor flattened IR's lexical spelling for defaults.
    assert ws.snapshot.graph["a/obj"]["kwargs"]["device"] == {"$ref": "a/device"}
    assert ws.snapshot.graph["b/obj"]["kwargs"]["device"] == {"$ref": "b/device"}
    assert replay_validate(ws)
    baseline = json.loads((ws.artifacts_dir / "generated_ir.json").read_text())
    assert baseline == ws.snapshot.graph
    assert compute_graph_digest(baseline) == ws.snapshot.digest
    assert ws.resolved_inputs == ws.snapshot.externals


@pytest.mark.parametrize(
    ("target", "value"),
    [("a/device", "cpu"), ("b/device", "cpu"), ("a/rate", 3), ("b/rate", 3)],
)
def test_replay_subprocess_uses_the_validated_canonical_inputs(workspace, target, value):
    ws = workspace
    original_inputs = dict(ws.snapshot.externals)
    assert replay_run(ws, set_overrides={target: value}) == 0
    assert ws.snapshot.externals == original_inputs
    assert ws.resolved_inputs[target] == value
    assert len(ws.resolved_inputs) == 2

    # The subprocess makes another real snapshot through the CLI bundle path.
    replay_snapshot = load_snapshot(next((ws.artifacts_dir / "snapshot").iterdir()))
    override_graph = json.loads((ws.artifacts_dir / "override_ir.json").read_text())
    assert replay_snapshot["graph"] == override_graph
    assert replay_snapshot["meta"]["digest"] == compute_graph_digest(override_graph)
    assert {
        name: info["value"] for name, info in replay_snapshot["environment"]["externals"].items()
    } == ws.resolved_inputs


def test_replay_accepts_existing_alias_expanded_schema_2_snapshots(workspace):
    ws = workspace
    # Earlier schema-2 snapshots recorded all external aliases. Keep them readable
    # without treating a replay override as a conflicting assignment in that layer.
    raw = ws.snapshot.raw
    raw["environment"]["externals"]["a/device"] = {"value": "cuda", "source": "cli"}
    log_path = Path(ws.snapshot.snapshot_dir) / "log.json"
    log_path.write_text(json.dumps(raw))
    ws.snapshot = load_and_validate_snapshot(ws.snapshot.snapshot_dir)
    assert replay_validate(ws, set_overrides={"a/device": "mps", "b/rate": 4})
    assert ws.resolved_inputs == {"a/device": "mps", "b/rate": 4}


def test_replay_conflicts_do_not_leave_previous_validated_inputs(workspace):
    ws = workspace
    assert replay_validate(ws, set_overrides={"a/device": "cpu"})
    with pytest.raises(ValidationError, match="Conflicting runtime assignments"):
        replay_validate(ws, set_overrides={"a/device": "cpu", "b/device": "mps"})
    assert ws.validate_ok is False
    assert ws.resolved_inputs is None


def test_identical_replay_alias_assignments_coalesce(workspace):
    ws = workspace
    assert replay_validate(ws, set_overrides={"a/rate": 3, "b/rate": 3})
    assert ws.resolved_inputs == {"b/device": "cuda", "a/rate": 3}
