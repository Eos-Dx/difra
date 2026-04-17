from difra.gui.matador_runtime_context import (
    DEFAULT_MATADOR_URL,
    get_runtime_matador_context,
    set_runtime_matador_context,
)


def test_runtime_context_defaults_to_production_url_when_empty():
    context = get_runtime_matador_context(None)

    assert context["token"] == ""
    assert context["matador_url"] == DEFAULT_MATADOR_URL


def test_set_runtime_context_normalizes_production_page_url():
    context = set_runtime_matador_context(
        None,
        token="Bearer abc.def.ghi",
        matador_url="https://portal.matur.co.uk/analytics/studies",
    )

    assert context["token"] == "abc.def.ghi"
    assert context["matador_url"] == DEFAULT_MATADOR_URL
