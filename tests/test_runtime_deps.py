from difra.runtime_deps import DEPENDENCIES


def test_runtime_dependency_specs_are_declared():
    assert DEPENDENCIES["container"].pip_spec.endswith("/container/archive/refs/heads/main.zip")
    assert DEPENDENCIES["protocol"].pip_spec.endswith("/protocol/archive/refs/heads/main.zip")
    assert DEPENDENCIES["xrdanalysis"].pip_spec.endswith("/xrd-analysis/archive/refs/heads/dev_sad.zip")
