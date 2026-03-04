from __future__ import annotations

import io
import json
import urllib.error

import pytest

import difra.runtime_deps as runtime_deps


class _JsonResponse:
    def __init__(self, payload: str) -> None:
        self._stream = io.StringIO(payload)

    def __enter__(self):
        return self._stream

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stream.close()


def _missing_module(name: str) -> ModuleNotFoundError:
    error = ModuleNotFoundError(name)
    error.name = name
    return error


def test_runtime_dependency_specs_are_declared(monkeypatch):
    monkeypatch.delenv("DIFRA_CONTAINER_PIP_SPEC", raising=False)
    monkeypatch.delenv("DIFRA_PROTOCOL_PIP_SPEC", raising=False)
    monkeypatch.delenv("DIFRA_XRDANALYSIS_PIP_SPEC", raising=False)

    assert runtime_deps.DEPENDENCIES["container"].pip_spec.endswith(
        "/container/archive/refs/heads/main.zip"
    )
    assert runtime_deps.DEPENDENCIES["protocol"].pip_spec.endswith(
        "/protocol/archive/refs/heads/main.zip"
    )
    assert runtime_deps.DEPENDENCIES["xrdanalysis"].pip_spec.endswith(
        "/xrd-analysis/releases/download/v0.2/xrdanalysis-0.2.0-py3-none-any.whl"
    )


def test_runtime_dependency_spec_honours_env_override(monkeypatch):
    monkeypatch.setenv("DIFRA_CONTAINER_PIP_SPEC", "container==9.9.9")

    assert runtime_deps.DEPENDENCIES["container"].pip_spec == "container==9.9.9"


def test_github_branch_archive_parser_handles_archive_specs():
    assert runtime_deps._parse_github_branch_archive(
        runtime_deps.DEPENDENCIES["container"].pip_spec
    ) == ("Eos-Dx", "container", "main")
    assert runtime_deps._parse_github_branch_archive("container==1.0") is None


def test_runtime_state_path_prefers_xdg_cache_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    assert runtime_deps._runtime_state_path() == tmp_path / "difra" / "runtime_deps_state.json"


def test_runtime_state_path_defaults_to_home_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert runtime_deps._runtime_state_path() == (
        tmp_path / ".cache" / "difra" / "runtime_deps_state.json"
    )


def test_load_and_save_runtime_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    assert runtime_deps._load_runtime_state() == {}

    state_path = runtime_deps._runtime_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{not json", encoding="utf-8")
    assert runtime_deps._load_runtime_state() == {}

    state_path.write_text(json.dumps(["not-a-dict"]), encoding="utf-8")
    assert runtime_deps._load_runtime_state() == {}

    payload = {"container": {"pip_spec": "container==1.2.3"}}
    runtime_deps._save_runtime_state(payload)
    assert runtime_deps._load_runtime_state() == payload


def test_fetch_github_branch_sha_returns_remote_sha(monkeypatch):
    monkeypatch.setattr(
        runtime_deps.urllib.request,
        "urlopen",
        lambda req, timeout=0: _JsonResponse('{"object": {"sha": "abc123"}}'),
    )

    assert runtime_deps._fetch_github_branch_sha("Eos-Dx", "container", "main") == "abc123"


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("offline"),
        TimeoutError("slow"),
        _JsonResponse("not json"),
        RuntimeError("boom"),
    ],
)
def test_fetch_github_branch_sha_returns_none_on_failure(monkeypatch, failure):
    def _raise_or_return(*_args, **_kwargs):
        if isinstance(failure, _JsonResponse):
            return failure
        raise failure

    monkeypatch.setattr(runtime_deps.urllib.request, "urlopen", _raise_or_return)

    assert runtime_deps._fetch_github_branch_sha("Eos-Dx", "container", "main") is None


def test_resolve_branch_sha_uses_archive_only(monkeypatch):
    github_dep = runtime_deps.RuntimeDependency(
        name="demo",
        import_name="demo",
        env_var="DIFRA_DEMO_SPEC",
        default_spec="https://github.com/acme/demo/archive/refs/heads/dev.zip",
    )
    wheel_dep = runtime_deps.RuntimeDependency(
        name="wheel",
        import_name="wheel",
        env_var="DIFRA_WHEEL_SPEC",
        default_spec="wheel==1.0",
    )

    monkeypatch.setattr(runtime_deps, "_fetch_github_branch_sha", lambda *args: "sha-demo")

    assert runtime_deps._resolve_branch_sha(github_dep) == "sha-demo"
    assert runtime_deps._resolve_branch_sha(wheel_dep) is None


def test_can_skip_refresh_checks_cached_state(monkeypatch):
    dep = runtime_deps.RuntimeDependency(
        name="demo",
        import_name="demo",
        env_var="DIFRA_DEMO_SPEC",
        default_spec="https://github.com/acme/demo/archive/refs/heads/dev.zip",
    )
    monkeypatch.delenv("DIFRA_DEMO_SPEC", raising=False)
    monkeypatch.setattr(runtime_deps, "_import_available", lambda _name: True)
    monkeypatch.setattr(
        runtime_deps,
        "_load_runtime_state",
        lambda: {"demo": {"pip_spec": dep.pip_spec, "resolved_sha": "sha-1"}},
    )

    assert runtime_deps._can_skip_refresh(dep, "sha-1") is True
    assert runtime_deps._can_skip_refresh(dep, None) is False


def test_can_skip_refresh_rejects_missing_import_or_spec_mismatch(monkeypatch):
    dep = runtime_deps.RuntimeDependency(
        name="demo",
        import_name="demo",
        env_var="DIFRA_DEMO_SPEC",
        default_spec="demo==1.0",
    )
    monkeypatch.setattr(runtime_deps, "_load_runtime_state", lambda: {"demo": {"pip_spec": "demo==2.0"}})
    monkeypatch.setattr(runtime_deps, "_import_available", lambda _name: True)
    assert runtime_deps._can_skip_refresh(dep, None) is False

    monkeypatch.setattr(runtime_deps, "_import_available", lambda _name: False)
    monkeypatch.setattr(runtime_deps, "_load_runtime_state", lambda: {"demo": {"pip_spec": dep.pip_spec}})
    assert runtime_deps._can_skip_refresh(dep, None) is False


def test_record_resolved_state_persists_updated_payload(monkeypatch):
    saved = {}
    dep = runtime_deps.RuntimeDependency(
        name="demo",
        import_name="demo",
        env_var="DIFRA_DEMO_SPEC",
        default_spec="demo==1.0",
    )

    monkeypatch.setattr(runtime_deps, "_load_runtime_state", lambda: {"existing": {"pip_spec": "old"}})
    monkeypatch.setattr(runtime_deps, "_save_runtime_state", lambda state: saved.update(state))

    runtime_deps._record_resolved_state(dep, "sha-9")

    assert saved["existing"] == {"pip_spec": "old"}
    assert saved["demo"] == {"pip_spec": "demo==1.0", "resolved_sha": "sha-9"}


def test_import_available_uses_local_dependency_fallback(monkeypatch):
    monkeypatch.setattr(
        runtime_deps.importlib,
        "import_module",
        lambda _name: (_ for _ in ()).throw(_missing_module("missing-demo")),
    )
    monkeypatch.setattr(runtime_deps, "ensure_local_dependency", lambda _name: True)

    assert runtime_deps._import_available("missing-demo") is True


def test_ensure_dependency_rejects_unknown_dependency():
    with pytest.raises(KeyError):
        runtime_deps.ensure_dependency("unknown")


def test_ensure_dependency_skips_when_refresh_is_not_needed(monkeypatch, capsys):
    monkeypatch.setattr(runtime_deps, "_resolve_branch_sha", lambda dep: "sha-123456789abc")
    monkeypatch.setattr(runtime_deps, "_can_skip_refresh", lambda dep, sha: True)

    assert runtime_deps.ensure_dependency("container") is False
    assert "Runtime dependency unchanged; skipping refresh" in capsys.readouterr().out


def test_ensure_dependency_keeps_existing_install_when_remote_tip_is_unavailable(
    monkeypatch, capsys
):
    monkeypatch.setattr(runtime_deps, "_resolve_branch_sha", lambda dep: None)
    monkeypatch.setattr(runtime_deps, "_can_skip_refresh", lambda dep, sha: False)
    monkeypatch.setattr(runtime_deps, "_import_available", lambda _name: True)

    assert runtime_deps.ensure_dependency("protocol") is False
    assert "Could not verify remote branch tip" in capsys.readouterr().out


def test_ensure_dependency_refreshes_and_records_state(monkeypatch, capsys):
    calls = {}

    monkeypatch.setattr(runtime_deps, "_resolve_branch_sha", lambda dep: "sha-abc")
    monkeypatch.setattr(runtime_deps, "_can_skip_refresh", lambda dep, sha: False)
    monkeypatch.setattr(runtime_deps, "_import_available", lambda _name: True)
    monkeypatch.setattr(runtime_deps.subprocess, "run", lambda cmd, check: calls.setdefault("cmd", cmd))
    monkeypatch.setattr(
        runtime_deps.importlib, "invalidate_caches", lambda: calls.setdefault("invalidated", True)
    )
    monkeypatch.setattr(
        runtime_deps,
        "_record_resolved_state",
        lambda dep, sha: calls.setdefault("recorded", (dep.name, sha)),
    )

    assert runtime_deps.ensure_dependency("container", python_executable="/tmp/python") is True
    assert calls["cmd"][0] == "/tmp/python"
    assert calls["cmd"][1:4] == ["-m", "pip", "install"]
    assert calls["recorded"] == ("container", "sha-abc")
    assert calls["invalidated"] is True
    out = capsys.readouterr().out
    assert "Refreshing runtime dependency from source: container" in out
    assert "Runtime dependency ready: container" in out


def test_ensure_dependency_raises_when_import_still_fails_after_install(monkeypatch):
    monkeypatch.setattr(runtime_deps, "_resolve_branch_sha", lambda dep: "sha-abc")
    monkeypatch.setattr(runtime_deps, "_can_skip_refresh", lambda dep, sha: False)
    monkeypatch.setattr(runtime_deps, "_import_available", lambda _name: False)
    monkeypatch.setattr(runtime_deps.subprocess, "run", lambda cmd, check: None)
    monkeypatch.setattr(runtime_deps.importlib, "invalidate_caches", lambda: None)

    with pytest.raises(RuntimeError):
        runtime_deps.ensure_dependency("container")


def test_ensure_dependencies_returns_only_newly_refreshed(monkeypatch):
    monkeypatch.setattr(
        runtime_deps,
        "ensure_dependency",
        lambda name, python_executable=None: name in {"container", "xrdanalysis"},
    )

    assert runtime_deps.ensure_dependencies(["container", "protocol", "xrdanalysis"]) == [
        "container",
        "xrdanalysis",
    ]


def test_runtime_deps_main_uses_defaults_and_explicit_require(monkeypatch):
    calls = []
    monkeypatch.setattr(
        runtime_deps, "ensure_dependencies", lambda names, python_executable=None: calls.append(list(names))
    )

    assert runtime_deps.main([]) == 0
    assert runtime_deps.main(["--require", "container", "--require", "protocol"]) == 0
    assert calls == [
        ["container", "protocol", "xrdanalysis"],
        ["container", "protocol"],
    ]
