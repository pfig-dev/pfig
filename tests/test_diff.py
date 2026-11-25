import pytest

from pfig import parse_pfig_source
from pfig.diff import (
    DiffChangeType,
    DiffDetail,
    DiffReport,
    NamespaceAlignment,
    _compare_providers,
    _compare_values,
    compare_configs,
    log_diff_details,
    log_diff_summary,
)
from pfig.execution import (
    FreshProviderCall,
    LiteralValue,
    ProviderMethodRef,
    ProviderRef,
    ProviderSpec,
)


def test_align_namespace_compares_references_and_recipes(tmp_path):
    included = tmp_path / "shared.pfig"
    included.write_text(
        "from collections import OrderedDict\n"
        "x = 3\n"
        "provider = OrderedDict(x=@x)\n"
        "runner = OrderedDict(items=[@provider, @provider(x=@x)], result=@x + 1)\n"
    )
    wrapped = parse_pfig_source(
        'include "shared.pfig" as base\n__entrypoint__ = @base/runner.run\n'
    )
    flat = parse_pfig_source(included.read_text() + "__entrypoint__ = @runner.run\n")
    kwargs = {
        "base_path_a": str(tmp_path / "wrapped.pfig"),
        "base_path_b": str(tmp_path / "flat.pfig"),
    }
    raw = compare_configs(wrapped, flat, alignment=None, **kwargs)
    assert "base/runner" in raw.removed
    assert "runner" in raw.added
    alignment = NamespaceAlignment.parse("base=/")
    assert not compare_configs(wrapped, flat, alignment=alignment, **kwargs).has_changes
    # Equal recipes in different files aren't enough to infer this move.
    assert compare_configs(wrapped, flat, **kwargs).has_changes

    changed = parse_pfig_source(
        included.read_text().replace("x = 3", "x = 4") + "__entrypoint__ = @runner.stop\n"
    )
    diff = compare_configs(wrapped, changed, alignment=alignment, **kwargs)
    assert not diff.added and not diff.removed
    assert set(diff.modified) == {"x", "__entrypoint__"}


def test_auto_alignment_does_not_hide_switch_between_live_entrypoints(tmp_path):
    (tmp_path / "shared.pfig").write_text(
        "from collections import OrderedDict\nrunner = OrderedDict()\n"
    )
    common = (
        'include "shared.pfig" as base\n'
        "from collections import OrderedDict\nrunner = OrderedDict()\n"
    )
    old = parse_pfig_source(common + "__entrypoint__ = @base/runner.run\n")
    new = parse_pfig_source(common + "__entrypoint__ = @runner.run\n")
    diff = compare_configs(
        old, new, base_path_a=str(tmp_path / "a.pfig"), base_path_b=str(tmp_path / "b.pfig")
    )
    assert diff.alignment is None
    assert set(diff.modified) == {"__entrypoint__"}


def test_auto_alignment_requires_shared_origin(tmp_path):
    shared = "from collections import OrderedDict\nrunner = OrderedDict()\n"
    (tmp_path / "shared.pfig").write_text(shared)
    old = parse_pfig_source('include "shared.pfig" as base\n__entrypoint__ = @base/runner.run\n')
    new = parse_pfig_source(shared + "__entrypoint__ = @runner.run\n")
    diff = compare_configs(
        old, new, base_path_a=str(tmp_path / "a.pfig"), base_path_b=str(tmp_path / "b.pfig")
    )
    assert diff.alignment is None
    assert diff.removed == ["base/runner"]
    assert diff.added == ["runner"]


def test_identity_matches_two_live_aliases_and_reports_recipe_edit(tmp_path):
    (tmp_path / "shared.pfig").write_text(
        "from collections import OrderedDict\nrunner = OrderedDict(workers=2)\n"
    )
    includes = 'include "shared.pfig" as one\ninclude "shared.pfig" as two\n'
    old = parse_pfig_source(includes + "__entrypoint__ = @one/runner.run\n")
    new = parse_pfig_source(includes + "__entrypoint__ = @two/runner.run\n")
    kwargs = {
        "base_path_a": str(tmp_path / "old.pfig"),
        "base_path_b": str(tmp_path / "new.pfig"),
    }
    assert not compare_configs(old, new, **kwargs).has_changes
    assert compare_configs(old, new, alignment=None, **kwargs).has_changes

    new = parse_pfig_source(
        includes + "from collections import OrderedDict\n"
        "override two/runner = OrderedDict(workers=8)\n__entrypoint__ = @two/runner.run\n"
    )
    delta = compare_configs(old, new, **kwargs)
    assert not delta.added and not delta.removed
    assert list(delta.modified) == ["one/runner"]


def test_identity_precedes_explicit_mapping_and_same_path(tmp_path):
    for name in ("first", "second"):
        (tmp_path / f"{name}.pfig").write_text("runner = 1\n")
    old = parse_pfig_source(
        'include "first.pfig" as left\ninclude "second.pfig" as right\nuse = @left/runner\n'
    )
    new = parse_pfig_source(
        'include "first.pfig" as right\ninclude "second.pfig" as left\nuse = @left/runner\n'
    )
    delta = compare_configs(
        old,
        new,
        base_path_a=str(tmp_path / "old.pfig"),
        base_path_b=str(tmp_path / "new.pfig"),
        alignment=NamespaceAlignment.parse("left=left"),
    )
    assert not delta.added and not delta.removed
    assert delta.modified["use"].change_type is DiffChangeType.DEPENDENCY_CHANGED


def test_align_namespace_disambiguates_base_collision(tmp_path):
    (tmp_path / "shared.pfig").write_text("x = 3\n")
    wrapped = parse_pfig_source('include "shared.pfig" as base\nx = 9\ncurrent = @x\n')
    flat = parse_pfig_source("x = 3\ncurrent = @x\n")
    diff = compare_configs(
        wrapped,
        flat,
        base_path_a=str(tmp_path / "wrapped.pfig"),
        alignment=NamespaceAlignment.parse("base=/"),
    )
    assert not diff.added
    assert diff.removed == ["x"]
    assert set(diff.modified) == {"current"}


def test_align_namespace_keeps_a_same_origin_collision_as_an_alias(tmp_path):
    (tmp_path / "shared.pfig").write_text("x = 3\n")
    (tmp_path / "wrapper.pfig").write_text('include "shared.pfig" as nested\n')
    wrapped = parse_pfig_source(
        'include "wrapper.pfig" as base\ninclude "shared.pfig" as nested\ncurrent = @nested/x\n'
    )
    flat = parse_pfig_source('include "shared.pfig" as nested\ncurrent = @nested/x\n')
    diff = compare_configs(
        wrapped,
        flat,
        base_path_a=str(tmp_path / "wrapped.pfig"),
        base_path_b=str(tmp_path / "flat.pfig"),
        alignment=NamespaceAlignment.parse("base=/"),
    )
    assert not diff.has_changes


@pytest.mark.parametrize("spec", ["base", "base=", "base=//", "base=/new", "base=new=", "=new"])
def test_align_namespace_rejects_invalid_specs(spec):
    with pytest.raises(ValueError, match="BASE=PROPOSED"):
        NamespaceAlignment.parse(spec)


class TestDiffLogic:
    def test_compare_literals_equal(self):
        """Test simple value equality."""
        v1 = LiteralValue(10)
        v2 = LiteralValue(10)
        diff = _compare_values(v1, v2)
        assert diff is None

    def test_compare_literals_modified(self):
        """Test simple value modification."""
        v1 = LiteralValue(10)
        v2 = LiteralValue(20)
        diff = _compare_values(v1, v2)

        assert diff is not None
        assert diff.change_type == DiffChangeType.MODIFIED
        assert diff.old_value == 10
        assert diff.new_value == 20

    def test_type_change(self):
        """Test changing from a value to a provider."""
        v1 = LiteralValue(10)
        v2 = ProviderSpec(callable_obj=dict, args=[], kwargs={})

        diff = _compare_values(v1, v2)
        assert diff is not None
        assert diff.change_type == DiffChangeType.TYPE_CHANGED

    def test_dependency_change(self):
        """Test detecting a change in dependencies (Spec 15.2)."""
        ref1 = ProviderRef("dataset_v1")
        ref2 = ProviderRef("dataset_v2")

        diff = _compare_values(ref1, ref2)
        assert diff is not None
        assert diff.change_type == DiffChangeType.DEPENDENCY_CHANGED
        assert diff.old_value == "dataset_v1"
        assert diff.new_value == "dataset_v2"

    def test_provider_kwarg_modification(self):
        """Test a nested change inside a provider's kwargs."""
        spec_a = ProviderSpec(
            callable_obj="ModelClass",
            args=[],
            kwargs={"layers": LiteralValue(2)},
        )

        spec_b = ProviderSpec(
            callable_obj="ModelClass", args=[], kwargs={"layers": LiteralValue(4)}
        )

        diff = _compare_values(spec_a, spec_b)

        assert diff is not None
        assert diff.change_type == DiffChangeType.MODIFIED
        assert "layers" in diff.sub_diffs
        assert diff.sub_diffs["layers"].old_value == 2
        assert diff.sub_diffs["layers"].new_value == 4

    # --- Bug #3: _compare_providers silently ignores ProviderSpec.args ---

    def test_same_positional_args_no_diff(self):
        """Identical positional args produce no diff."""
        a = ProviderSpec(callable_obj=len, args=[LiteralValue([1, 2, 3])], kwargs={})
        b = ProviderSpec(callable_obj=len, args=[LiteralValue([1, 2, 3])], kwargs={})
        assert _compare_providers(a, b) is None

    def test_changed_positional_arg_detected(self):
        """A change in a positional arg must be detected by _compare_providers."""
        a = ProviderSpec(callable_obj=len, args=[LiteralValue([1, 2, 3])], kwargs={})
        b = ProviderSpec(callable_obj=len, args=[LiteralValue([1, 2, 3, 4, 5])], kwargs={})
        diff = _compare_providers(a, b)
        assert diff is not None, (
            "Bug #3: _compare_providers ignores .args — "
            "a change in positional arguments goes undetected"
        )

    def test_added_positional_arg_detected(self):
        """Adding a positional arg must be detected."""
        a = ProviderSpec(callable_obj=min, args=[LiteralValue(1)], kwargs={})
        b = ProviderSpec(callable_obj=min, args=[LiteralValue(1), LiteralValue(2)], kwargs={})
        diff = _compare_providers(a, b)
        assert diff is not None, (
            "Bug #3: _compare_providers does not detect an added positional argument"
        )

    def test_removed_positional_arg_detected(self):
        """Removing a positional arg must be detected."""
        a = ProviderSpec(callable_obj=min, args=[LiteralValue(1), LiteralValue(2)], kwargs={})
        b = ProviderSpec(callable_obj=min, args=[LiteralValue(1)], kwargs={})
        diff = _compare_providers(a, b)
        assert diff is not None, (
            "Bug #3: _compare_providers does not detect a removed positional argument"
        )

    def test_kwarg_change_still_detected_after_args_fix(self):
        """Kwarg changes must keep working alongside any args comparison fix."""
        a = ProviderSpec(callable_obj=dict, args=[], kwargs={"key": LiteralValue("old")})
        b = ProviderSpec(callable_obj=dict, args=[], kwargs={"key": LiteralValue("new")})
        diff = _compare_providers(a, b)
        assert diff is not None
        assert diff.change_type == DiffChangeType.MODIFIED

    def test_same_args_and_kwargs_no_diff(self):
        """Identical args and kwargs together produce no diff."""
        a = ProviderSpec(callable_obj=dict, args=[LiteralValue(1)], kwargs={"k": LiteralValue("v")})
        b = ProviderSpec(callable_obj=dict, args=[LiteralValue(1)], kwargs={"k": LiteralValue("v")})
        assert _compare_providers(a, b) is None

    # --- Bug #4: ProviderMethodRef and FreshProviderCall produce coarse diffs ---

    def test_provider_method_ref_same_no_diff(self):
        """@model.predict vs @model.predict → no diff."""
        a = ProviderMethodRef(name="model", method="predict")
        b = ProviderMethodRef(name="model", method="predict")
        assert _compare_values(a, b) is None

    def test_provider_method_ref_method_change_is_dependency_changed(self):
        """@model.predict vs @model.fit must be DEPENDENCY_CHANGED, not MODIFIED.

        Bug #4: currently produces MODIFIED because there is no explicit branch for
        ProviderMethodRef in _compare_values; fix is to add one that returns
        DEPENDENCY_CHANGED when the name or method differs.
        """
        a = ProviderMethodRef(name="model", method="predict")
        b = ProviderMethodRef(name="model", method="fit")
        diff = _compare_values(a, b)
        assert diff is not None
        assert diff.change_type == DiffChangeType.DEPENDENCY_CHANGED, (
            f"Bug #4: expected DEPENDENCY_CHANGED for ProviderMethodRef method change, "
            f"got {diff.change_type}"
        )

    def test_provider_method_ref_name_change_detected(self):
        """@model_a.predict vs @model_b.predict must be detected."""
        a = ProviderMethodRef(name="model_a", method="predict")
        b = ProviderMethodRef(name="model_b", method="predict")
        assert _compare_values(a, b) is not None

    def test_fresh_call_same_no_diff(self):
        """@model(lr=0.01) vs @model(lr=0.01) → no diff."""
        a = FreshProviderCall(provider_name="model", overlay_kwargs={"lr": LiteralValue(0.01)})
        b = FreshProviderCall(provider_name="model", overlay_kwargs={"lr": LiteralValue(0.01)})
        assert _compare_values(a, b) is None

    def test_fresh_call_kwarg_change_has_sub_diffs(self):
        """@model(lr=0.01) vs @model(lr=0.1) must report which kwarg changed via sub_diffs.

        Bug #4: currently produces coarse MODIFIED with whole objects instead of
        sub_diffs keyed by kwarg name.
        """
        a = FreshProviderCall(provider_name="model", overlay_kwargs={"lr": LiteralValue(0.01)})
        b = FreshProviderCall(provider_name="model", overlay_kwargs={"lr": LiteralValue(0.1)})
        diff = _compare_values(a, b)
        assert diff is not None
        assert diff.sub_diffs, (
            "Bug #4: FreshProviderCall kwarg change should produce sub_diffs keyed "
            "by kwarg name, but sub_diffs is empty"
        )
        assert "lr" in diff.sub_diffs, (
            f"Bug #4: expected 'lr' in sub_diffs, got {list(diff.sub_diffs.keys())}"
        )

    def test_fresh_call_provider_name_change_detected(self):
        """@model_a(lr=0.01) vs @model_b(lr=0.01) must be detected."""
        a = FreshProviderCall(provider_name="model_a", overlay_kwargs={"lr": LiteralValue(0.01)})
        b = FreshProviderCall(provider_name="model_b", overlay_kwargs={"lr": LiteralValue(0.01)})
        assert _compare_values(a, b) is not None

    def test_fresh_call_added_kwarg_detected(self):
        """Adding a kwarg to a fresh call must be detected."""
        a = FreshProviderCall(provider_name="model", overlay_kwargs={})
        b = FreshProviderCall(provider_name="model", overlay_kwargs={"lr": LiteralValue(0.01)})
        assert _compare_values(a, b) is not None


class TestDiffLogging:
    def test_log_diff_summary_marks_unified_diff_lines(self, monkeypatch):
        calls = []

        def record_info(message, *args, **kwargs):
            calls.append((message, kwargs.get("extra")))

        monkeypatch.setattr("pfig.diff.logger.info", record_info)

        report = DiffReport(
            added=["new"],
            removed=["old"],
            modified={"x": DiffDetail(DiffChangeType.MODIFIED)},
        )
        log_diff_summary(report, "old.pfig", "new.pfig")

        assert ("--- a/old.pfig", {"color_role": "removed"}) in calls
        assert ("+++ b/new.pfig", {"color_role": "added"}) in calls
        assert (
            "@@ 1 insertions(+), 1 deletions(-), 1 modified(~) @@",
            {"color_role": "hunk"},
        ) in calls

    def test_log_diff_details_marks_added_and_removed_lines(self, monkeypatch):
        calls = []

        def record_info(message, *args, **kwargs):
            calls.append((message, kwargs.get("extra")))

        monkeypatch.setattr("pfig.diff.logger.info", record_info)

        report = DiffReport(
            added=["new_binding"],
            removed=["old_binding"],
            modified={"value": DiffDetail(DiffChangeType.MODIFIED, old_value=1, new_value=2)},
        )
        log_diff_details(report)

        assert ("- old_binding", {"color_role": "removed"}) in calls
        assert ("+ new_binding", {"color_role": "added"}) in calls
        assert ("- value: 1", {"color_role": "removed"}) in calls
        assert ("+ value: 2", {"color_role": "added"}) in calls
