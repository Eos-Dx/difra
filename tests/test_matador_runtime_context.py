from types import SimpleNamespace

import difra.gui.matador_runtime_context as runtime_context


class _FakeSettings:
    values = {}

    def __init__(self, *_args, **_kwargs):
        pass

    def value(self, key, default=None, type=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def remove(self, key):
        self.values.pop(key, None)

    def sync(self):
        return None


def test_runtime_context_uses_configured_default_url(monkeypatch):
    _FakeSettings.values = {}
    monkeypatch.setattr(runtime_context, "QSettings", _FakeSettings)
    owner = SimpleNamespace(config={"matador_url": "https://portal.matur.co.uk"})

    context = runtime_context.get_runtime_matador_context(owner)

    assert context["token"] == ""
    assert context["matador_url"] == "https://portal.matur.co.uk"


def test_set_runtime_context_normalizes_url_and_persists_setting(monkeypatch):
    _FakeSettings.values = {}
    monkeypatch.setattr(runtime_context, "QSettings", _FakeSettings)

    context = runtime_context.set_runtime_matador_context(
        None,
        token="Bearer abc.def.ghi",
        matador_url="https://portal.matur.co.uk/analytics/studies",
    )

    assert context["token"] == "abc.def.ghi"
    assert context["matador_url"] == "https://portal.matur.co.uk"
    assert _FakeSettings.values["matador/url"] == "https://portal.matur.co.uk"
