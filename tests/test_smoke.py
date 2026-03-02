import hardware.difra as difra


def test_package_imports():
    assert hasattr(difra, "__getattr__")
