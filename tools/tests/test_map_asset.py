"""The render derivative must preserve the user's hull and its highlights."""
import importlib.util
from pathlib import Path
import tempfile

from PIL import Image


def test_background_only_transparency():
    script = Path(__file__).resolve().parents[1] / "prepare_submarine_asset.py"
    assert script.exists(), "transparent derivative generator is missing"
    spec = importlib.util.spec_from_file_location("prepare_submarine_asset", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as directory:
        source, dest = Path(directory) / "source.png", Path(directory) / "derived.png"
        image = Image.new("RGB", (12, 8), "white")
        for x in range(2, 10):
            for y in range(2, 6):
                image.putpixel((x, y), (30, 30, 30))
        image.putpixel((5, 4), (255, 255, 255))
        image.save(source)
        before = source.read_bytes()
        module.prepare(source, dest)
        result = Image.open(dest)
        assert result.size == image.size
        assert result.getpixel((0, 0))[3] == 0
        assert result.getpixel((2, 2)) == (30, 30, 30, 255)
        assert result.getpixel((5, 4)) == (255, 255, 255, 255)
        assert source.read_bytes() == before
