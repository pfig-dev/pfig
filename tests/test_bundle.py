"""Tests for filesystem-only bundles, profiles and safe extraction."""

import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest

from pfig import instantiate
from pfig.bundle import (
    BUNDLE_VERSION,
    CONFIGS_DIR,
    MANIFEST_FILE,
    PROFILES_DIR,
    BundleManifest,
    _extractall_with_data_filter,
    collect_profile_bundle_info,
    compute_fs_root,
    create_bundle,
    load_bundle,
)
from pfig.execution import ASTCompiler
from pfig.includes import IncludeResolution
from pfig.parser import parse_pfig_source


def test_manifest_roundtrip(tmp_path):
    manifest = BundleManifest(
        bundle_version=BUNDLE_VERSION,
        entry="configs/main.pfig",
        files=["configs/main.pfig", "configs/lib/base.pfig"],
        profiles=["__profiles__/a.pfig", "__profiles__/b.pfig"],
    )
    data = json.loads(manifest.to_json())
    assert "mounts" not in data
    assert BundleManifest.from_json(manifest.to_json()) == manifest
    path = tmp_path / MANIFEST_FILE
    path.write_text(manifest.to_json())
    assert BundleManifest.from_file(path) == manifest


def test_manifest_profiles_default_empty():
    manifest = BundleManifest(BUNDLE_VERSION, "configs/main.pfig", ["configs/main.pfig"])
    assert manifest.profiles == []


def test_legacy_filesystem_manifest():
    manifest = BundleManifest.from_json(
        json.dumps(
            {
                "bundle_version": 1,
                "entry": "configs/main.pfig",
                "mounts": {},
                "files": ["configs/main.pfig"],
            }
        )
    )
    assert manifest.profiles == []
    assert manifest.entry == "configs/main.pfig"


def test_legacy_mounted_manifest_rejected():
    with pytest.raises(ValueError, match="Bundles with mounts are no longer supported"):
        BundleManifest.from_json(
            json.dumps(
                {
                    "bundle_version": 1,
                    "entry": "configs/main.pfig",
                    "mounts": {"lib": "__mounts__/lib"},
                    "files": ["configs/main.pfig"],
                }
            )
        )


@pytest.mark.parametrize(
    "entry,includes,root",
    [
        ("main.pfig", [], "."),
        ("main.pfig", ["utils.pfig"], "."),
        ("experiments/exp1/main.pfig", ["common/model.pfig"], "."),
        ("a/b/c/d/main.pfig", ["a/b/x/y/utils.pfig"], "a/b"),
        ("project/main.pfig", ["shared/base.pfig"], "."),
    ],
)
def test_compute_fs_root(tmp_path, entry, includes, root):
    entry_path = tmp_path / entry
    entry_path.parent.mkdir(parents=True, exist_ok=True)
    entry_path.write_text("# entry")
    origins = {}
    for name in includes:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# include")
        origins[str(path)] = IncludeResolution(path)
    assert compute_fs_root(origins, entry_path) == (tmp_path / root).resolve()


@pytest.mark.parametrize("as_tarball", [False, True])
def test_create_and_load_simple_bundle(tmp_path, as_tarball):
    entry = tmp_path / "main.pfig"
    entry.write_text("value = 42")
    output = tmp_path / "bundle"
    result = create_bundle({}, entry, output, as_tarball=as_tarball)
    loaded, manifest = load_bundle(result, extract_dir=tmp_path / "extracted")
    assert manifest.bundle_version == BUNDLE_VERSION
    assert manifest.entry == f"{CONFIGS_DIR}/main.pfig"
    assert manifest.files == [f"{CONFIGS_DIR}/main.pfig"]
    assert (loaded / manifest.entry).read_text() == entry.read_text()
    assert not (loaded / PROFILES_DIR).exists()


@pytest.mark.parametrize("as_tarball", [False, True])
def test_nested_relative_includes_survive_relocation(tmp_path, monkeypatch, as_tarball):
    source = tmp_path / "source"
    project = source / "project"
    library = source / "shared" / "lib"
    project.mkdir(parents=True)
    library.mkdir(parents=True)
    (library / "base.pfig").write_text('include "./sibling.pfig" as s\nvalue = @s/value')
    (library / "sibling.pfig").write_text("value = 42")
    entry = project / "main.pfig"
    entry.write_text('include "../shared/lib/base.pfig" as lib\nresult = @lib/value')
    compiler = ASTCompiler(base_path=str(entry))
    compiler.compile_file(parse_pfig_source(entry.read_text(), filename=str(entry)))
    result = create_bundle(compiler.file_origins, entry, tmp_path / "bundle", as_tarball=as_tarball)
    loaded, manifest = load_bundle(result, extract_dir=tmp_path / "extracted")
    assert len(manifest.files) == 3
    shutil.rmtree(source)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert instantiate("result", path=loaded / manifest.entry) == 42


@pytest.mark.parametrize("as_tarball", [False, True])
def test_profiles_and_relative_profile_includes_survive_relocation(tmp_path, as_tarball):
    source = tmp_path / "source"
    source.mkdir()
    entry = source / "main.pfig"
    entry.write_text("external value\nexternal shared/other\nresult = [@value, @shared/other]")
    profiles = source / "profiles"
    profiles.mkdir()
    shared = profiles / "shared" / "overrides.pfig"
    shared.parent.mkdir()
    shared.write_text("override other = 42")
    first = profiles / "a.pfig"
    first.write_text('include "./shared/overrides.pfig" as shared\noverride value = "A"')
    second = profiles / "b.pfig"
    second.write_text('override value = "B"')
    resolved, origins = collect_profile_bundle_info([first, second])
    assert resolved == [first.resolve(), second.resolve()]
    assert str(shared.resolve()) in origins
    result = create_bundle(
        {},
        entry,
        tmp_path / "bundle",
        as_tarball=as_tarball,
        profile_paths=resolved,
        profile_file_origins=origins,
    )
    loaded, manifest = load_bundle(result, extract_dir=tmp_path / "extracted")
    assert [Path(p).name for p in manifest.profiles] == ["a.pfig", "b.pfig"]
    assert len(list((loaded / PROFILES_DIR).rglob("*.pfig"))) == 3
    shutil.rmtree(source)
    assert instantiate(
        "result",
        path=loaded / manifest.entry,
        profile_paths=[loaded / p for p in manifest.profiles],
    ) == ["B", 42]


def test_collect_profile_bundle_info_empty_and_simple(tmp_path):
    assert collect_profile_bundle_info([]) == ([], {})
    profile = tmp_path / "env.pfig"
    profile.write_text("override x = 1")
    assert collect_profile_bundle_info([profile]) == ([profile.resolve()], {})


def test_load_missing_bundle(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_bundle(tmp_path / "missing")


def test_load_missing_manifest(tmp_path):
    with pytest.raises(ValueError, match="missing manifest"):
        load_bundle(tmp_path)


def test_load_invalid_extension(tmp_path):
    path = tmp_path / "bundle.zip"
    path.write_text("not a tarball")
    with pytest.raises(ValueError, match=".tar.gz"):
        load_bundle(path)


def test_load_unsupported_version(tmp_path):
    (tmp_path / MANIFEST_FILE).write_text(BundleManifest(999, "main.pfig", []).to_json())
    with pytest.raises(ValueError, match="Unsupported bundle version"):
        load_bundle(tmp_path)


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape.txt", tarfile.REGTYPE),
        ("configs/link", tarfile.SYMTYPE),
        ("configs/link", tarfile.LNKTYPE),
        ("configs/pipe", tarfile.FIFOTYPE),
    ],
)
def test_rejects_unsafe_tar_members(tmp_path, name, kind):
    dest = tmp_path / "out"
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        info = tarfile.TarInfo(name)
        info.type = kind
        info.linkname = "../../escape.txt"
        tar.addfile(info, fileobj=io.BytesIO(b""))
    with tarfile.open(evil, "r:gz") as tar:
        with pytest.raises(ValueError, match="Unsafe tar member"):
            _extractall_with_data_filter(tar, dest)
    assert not (tmp_path / "escape.txt").exists()


def test_extracts_safe_member(tmp_path):
    dest = tmp_path / "out"
    safe = tmp_path / "safe.tar.gz"
    payload = b"ok!\n"
    with tarfile.open(safe, "w:gz") as tar:
        info = tarfile.TarInfo("configs/hello.txt")
        info.size = len(payload)
        tar.addfile(info, fileobj=io.BytesIO(payload))
    with tarfile.open(safe, "r:gz") as tar:
        _extractall_with_data_filter(tar, dest)
    assert (dest / "configs" / "hello.txt").read_bytes() == payload
