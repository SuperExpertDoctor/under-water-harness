"""Ground-truth evidence field G(x) and search-problem definitions.

G(x) <= 0 marks the suspicious region (target-presence lobes); probing a
point evaluates true G and counts as one expensive detection action.
The belief-map DNN approximates G; its zero level set is the confidence
boundary between "possibly present" and "cleared".
"""
import numpy as np


class EvidenceField:
    """Union-of-ellipses ground truth: G = min_k g_k, g_k<=0 inside lobe k.

    lobes: list of dicts {c:(x,y), a, b, theta} — center, semi-axes, rotation.
    depth: how strongly negative inside lobes; tilt adds a mild spatial trend
    so the field is not exactly flat far away.
    """

    def __init__(self, lobes, domain=(0.0, 1.0, 0.0, 1.0), tilt=(0.0, 0.0)):
        self.lobes = [dict(l) for l in lobes]
        self.domain = domain
        self.tilt = np.asarray(tilt, dtype=float)

    def eval(self, pts):
        """True evidence function at pts (...,2) -> (...) float array."""
        pts = np.asarray(pts, dtype=float)
        g = np.full(pts.shape[:-1], np.inf)
        for l in self.lobes:
            c = np.asarray(l["c"], dtype=float)
            th = l.get("theta", 0.0)
            R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
            d = (pts - c) @ R.T
            v = (d[..., 0] / l["a"]) ** 2 + (d[..., 1] / l["b"]) ** 2 - 1.0
            g = np.minimum(g, v)
        x, y = pts[..., 0], pts[..., 1]
        g = g + self.tilt[0] * x + self.tilt[1] * y
        # squash to (-1, 1) so the regressed surface does not smear the
        # confidence boundary outward under heavy positive skew
        return np.tanh(g / 1.2)

    def drift(self, velocity, dt=1.0):
        """Move all lobe centers by velocity*dt (moving-target case)."""
        v = np.asarray(velocity, dtype=float) * dt
        for l in self.lobes:
            l["c"] = (np.asarray(l["c"], dtype=float) + v).tolist()

    def presence_mask(self, pts):
        return self.eval(pts) <= 0.0


def make_grid(domain=(0, 1, 0, 1), n=100):
    xs = np.linspace(domain[0], domain[1], n)
    ys = np.linspace(domain[2], domain[3], n)
    gx, gy = np.meshgrid(xs, ys)
    return np.stack([gx.ravel(), gy.ravel()], axis=1), (n, n)


def prior_from_gaussians(grid, comps):
    """Mixture-of-Gaussians prior density over grid points -> normalized weights."""
    w = np.zeros(len(grid))
    for c in comps:
        mu = np.asarray(c["mu"], dtype=float)
        cov = np.asarray(c["cov"], dtype=float)
        d = grid - mu
        inv = np.linalg.inv(cov)
        expo = -0.5 * np.einsum("ij,jk,ik->i", d, inv, d)
        w += c.get("w", 1.0) * np.exp(expo)
    w += 1e-6  # never exactly zero anywhere
    return w / w.sum()


def reference_pf(field, prior_w, n=400):
    """Reference P_exist by dense quadrature (not counted as probes)."""
    dom = field.domain
    xs = np.linspace(dom[0], dom[1], n)
    ys = np.linspace(dom[2], dom[3], n)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.stack([gx.ravel(), gy.ravel()], axis=1)
    w = prior_from_gaussians(pts, PRIOR_COMPS) if isinstance(prior_w, list) else None
    mask = field.eval(pts) <= 0
    if w is not None:
        return float((mask * w).sum())
    return float(mask.mean())


# --- scenario definitions -------------------------------------------------

PRIOR_COMPS = None  # set by experiments using list-based reference

STATIC_LOBES = [
    {"c": (0.68, 0.70), "a": 0.115, "b": 0.070, "theta": 0.5},
    {"c": (0.78, 0.55), "a": 0.090, "b": 0.058, "theta": -0.3},
    {"c": (0.60, 0.50), "a": 0.078, "b": 0.095, "theta": 1.1},
]

# intel-driven prior (hotspots coarser than truth, slightly biased)
DEFAULT_PRIOR = [
    {"mu": (0.70, 0.66), "cov": [[0.022, 0.004], [0.004, 0.020]], "w": 1.6},
    {"mu": (0.62, 0.50), "cov": [[0.018, -0.002], [-0.002, 0.018]], "w": 0.9},
]
