"""Create a transparent render derivative; never modify the supplied source PNG."""
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import binary_propagation


def prepare(source, destination):
    rgba = np.array(Image.open(source).convert("RGBA"))
    near_white = (rgba[:, :, :3].min(axis=2) >= 225)
    border = np.zeros_like(near_white)
    border[0, :] = border[-1, :] = True
    border[:, 0] = border[:, -1] = True
    background = binary_propagation(border & near_white, mask=near_white)
    rgba[background, 3] = 0
    Image.fromarray(rgba).save(destination)


if __name__ == "__main__":
    assets = Path(__file__).resolve().parents[1] / "ui/public/assets"
    prepare(assets / "submarine.png", assets / "submarine-transparent.png")
    print("Created submarine-transparent.png; original submarine.png unchanged")
