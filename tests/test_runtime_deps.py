from difra.runtime_deps import DEPENDENCIES, _parse_github_branch_archive


def test_runtime_dependency_specs_are_declared():
    assert DEPENDENCIES["container"].pip_spec.endswith("/container/archive/refs/heads/main.zip")
    assert DEPENDENCIES["protocol"].pip_spec.endswith("/protocol/archive/refs/heads/main.zip")
    assert DEPENDENCIES["xrdanalysis"].pip_spec.endswith(
        "/xrd-analysis/releases/download/v0.2/xrdanalysis-0.2.0-py3-none-any.whl"
    )


def test_github_branch_archive_parser_handles_xrdanalysis_spec():
    assert _parse_github_branch_archive(DEPENDENCIES["container"].pip_spec) == (
        "Eos-Dx",
        "container",
        "main",
    )
