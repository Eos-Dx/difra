from __future__ import annotations

from types import SimpleNamespace

from difra.gui.main_window_ext import session_flow_actions as module


class _FakePixmap:
    def __init__(self, path: str) -> None:
        self.path = path

    def isNull(self) -> bool:
        return False


def test_prompt_and_attach_sample_image_returns_none_when_user_declines(monkeypatch):
    monkeypatch.setattr(module.QMessageBox, "question", lambda *args, **kwargs: module.QMessageBox.No)

    owner = SimpleNamespace()

    result = module.prompt_and_attach_sample_image(owner)

    assert result is None


def test_prompt_and_attach_sample_image_loads_image_and_updates_workspace(monkeypatch):
    monkeypatch.setattr(module.QMessageBox, "question", lambda *args, **kwargs: module.QMessageBox.Yes)
    monkeypatch.setattr(
        module.QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: ("/tmp/specimen.png", "Image Files"),
    )
    monkeypatch.setattr(module, "QPixmap", _FakePixmap)

    added_images = []
    image_view_calls = []
    owner = SimpleNamespace(
        config={"default_image_folder": "/tmp"},
        session_manager=SimpleNamespace(
            add_sample_image=lambda **kwargs: added_images.append(kwargs)
        ),
        image_view=SimpleNamespace(
            set_image=lambda pixmap, image_path=None: image_view_calls.append(
                (pixmap.path, image_path)
            )
        ),
        _load_image_array_from_path=lambda path: [[1, 2], [3, 4]],
        delete_all_shapes_from_table=lambda: image_view_calls.append(("shapes", None)),
        delete_all_points=lambda: image_view_calls.append(("points", None)),
        _append_session_log=lambda message: image_view_calls.append(("log", message)),
    )

    result = module.prompt_and_attach_sample_image(owner)

    assert result == "/tmp/specimen.png"
    assert added_images == [
        {
            "image_data": [[1, 2], [3, 4]],
            "image_index": 1,
            "image_type": "sample",
        }
    ]
    assert ("/tmp/specimen.png", "/tmp/specimen.png") in image_view_calls
    assert ("shapes", None) in image_view_calls
    assert ("points", None) in image_view_calls
