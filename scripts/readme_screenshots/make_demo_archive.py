"""Build the demo archive used for the README screenshots.

Every picture is generated procedurally (gradients, ridges, a sun), so the
screenshots contain no real photos. Needs numpy and Pillow; the short video
needs ffmpeg on PATH and is skipped without it.

    python scripts/readme_screenshots/make_demo_archive.py demo.7z
"""

import io
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import py7zr
from PIL import Image, ImageFilter

W, H = 1600, 1067
rng = np.random.default_rng(7)


def lerp(a, b, t):
    return a + (b - a) * t


def sky(top, bottom, horizon=1.0):
    y = np.linspace(0, 1, H)[:, None, None] / horizon
    y = np.clip(y, 0, 1)
    return lerp(np.array(top, float), np.array(bottom, float), y) * np.ones((1, W, 1))


def ridge(base, amp, rough, seed):
    r = np.random.default_rng(seed)
    x = np.linspace(0, 1, W)
    h = np.zeros(W)
    for octave in range(1, 7):
        f = 2**octave * rough
        phase = r.uniform(0, 2 * np.pi)
        h += np.sin(x * f * np.pi + phase) / octave**1.2
    h = (h - h.min()) / (h.max() - h.min())
    return (base - amp * h) * H


def paint_layer(img, heights, colour, fade=0.0):
    yy = np.arange(H)[:, None]
    mask = yy >= heights[None, :]
    c = np.array(colour, float)
    if fade:
        depth = np.clip((yy - heights[None, :]) / (H * 0.6), 0, 1)[..., None]
        c = lerp(c, c * (1 - fade), depth)
    img[mask] = (c * np.ones((H, W, 3)))[mask]
    return img


def sun(img, cx, cy, r, colour, glow=3.0):
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    halo = np.exp(-((d / (r * glow)) ** 2))[..., None]
    img = lerp(img, np.array(colour, float), halo * 0.55)
    img[d < r] = colour
    return img


def stars(img, n, top=0.6):
    for _ in range(n):
        x, y = rng.integers(0, W), rng.integers(0, int(H * top))
        b = rng.uniform(150, 255)
        img[y, x] = np.maximum(img[y, x], b)
    return img


def finish(img, blur=0.0):
    img = np.clip(img + rng.normal(0, 3.0, img.shape), 0, 255).astype(np.uint8)
    pic = Image.fromarray(img)
    if blur:
        pic = pic.filter(ImageFilter.GaussianBlur(blur))
    buf = io.BytesIO()
    pic.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def mountains_sunset(seed=0):
    img = sky((38, 34, 92), (250, 150, 90), 0.85)
    if seed:
        img = sky((20, 40, 80), (120, 170, 210), 0.9)
    img = sun(img, W * (0.62 - seed * 0.3), H * 0.58, 70, (255, 214, 150))
    for i, (base, amp, col) in enumerate(
        [(0.62, 0.25, (120, 70, 110)), (0.72, 0.2, (80, 45, 85)), (0.84, 0.18, (40, 25, 50))]
    ):
        img = paint_layer(img, ridge(base, amp, 1.2 + i * 0.4, 10 + i + seed * 5), col)
    return finish(img, 0.6)


def ocean():
    img = sky((70, 140, 200), (210, 230, 240), 0.55)
    img = sun(img, W * 0.3, H * 0.3, 45, (255, 250, 230), 4)
    horizon = np.full(W, H * 0.55)
    img = paint_layer(img, horizon, (30, 100, 150), fade=0.5)
    yy = np.arange(H)[:, None]
    waves = (np.sin(np.linspace(0, 80, W)[None, :] + yy * 0.25) > 0.96) & (yy > H * 0.56)
    img[waves] = lerp(img[waves], np.array([220, 240, 250.0]), 0.5)
    return finish(img, 0.4)


def dunes():
    img = sky((120, 175, 225), (245, 220, 180), 0.7)
    for i, (base, amp, col) in enumerate(
        [(0.66, 0.12, (225, 170, 110)), (0.78, 0.14, (200, 135, 80)), (0.92, 0.16, (165, 100, 60))]
    ):
        img = paint_layer(img, ridge(base, amp, 0.5 + i * 0.2, 30 + i), col, fade=0.25)
    return finish(img, 1.0)


def aurora():
    img = sky((6, 10, 30), (20, 40, 70), 1.0)
    img = stars(img, 900)
    yy, xx = np.mgrid[0:H, 0:W]
    band = H * 0.35 + 80 * np.sin(xx / 220.0) + 40 * np.sin(xx / 90.0 + 1)
    glow = np.exp(-(((yy - band) / 70.0) ** 2))[..., None]
    curtain = np.clip((band[None, :] if False else band) - yy, 0, 260) / 260.0
    img = lerp(img, np.array([70, 230, 170.0]), glow * 0.8)
    img = lerp(img, np.array([120, 80, 200.0]), (curtain * glow[..., 0] * 0.6)[..., None])
    img = paint_layer(img, ridge(0.86, 0.12, 2.0, 41), (8, 12, 20))
    return finish(img, 0.8)


def fog_forest():
    img = sky((180, 195, 200), (225, 230, 228), 1.0)
    for i in range(5):
        base = 0.55 + i * 0.09
        h = ridge(base, 0.08, 6 + i * 2, 50 + i)
        teeth = (np.abs(np.sin(np.linspace(0, 140 + i * 30, W))) ** 6) * H * (0.06 + 0.02 * i)
        shade = 160 - i * 32
        img = paint_layer(img, h - teeth, (shade - 20, shade, shade - 10))
    return finish(img, 1.2)


def lake():
    img = sky((250, 190, 140), (255, 230, 200), 0.5)
    img = sun(img, W * 0.5, H * 0.42, 55, (255, 240, 210), 5)
    hills = ridge(0.5, 0.12, 1.0, 61)
    img = paint_layer(img, hills, (90, 80, 100))
    top = img[: int(H * 0.5)][::-1]
    reflect = lerp(top, np.array([60, 70, 110.0]), 0.35)
    img[int(H * 0.5) : int(H * 0.5) + top.shape[0]] = reflect[: H - int(H * 0.5)]
    return finish(img, 0.8)


def night_city():
    img = sky((10, 14, 40), (60, 40, 90), 0.8)
    img = stars(img, 300, 0.4)
    x = 0
    while x < W:
        w = int(rng.integers(40, 120))
        top = int(H * rng.uniform(0.35, 0.7))
        img[top:, x : x + w] = (18, 20, 34)
        for wy in range(top + 12, H - 10, 22):
            for wx in range(x + 8, x + w - 10, 18):
                if rng.random() < 0.35:
                    img[wy : wy + 8, wx : wx + 8] = (255, 205, 120)
        x += w + int(rng.integers(2, 10))
    return finish(img, 0.5)


def tulips():
    img = sky((140, 190, 235), (225, 235, 245), 0.45)
    yy = np.arange(H)[:, None]
    field = yy > H * 0.45
    xx = np.arange(W)[None, :]
    angle = np.arctan2(xx - W * 0.5, np.maximum(yy - H * 0.42, 1))
    stripes = np.floor(angle * 9) % 4
    colours = np.array([[230, 60, 80], [250, 200, 60], [240, 120, 170], [255, 255, 255.0]])
    rows = colours[stripes.astype(int)]
    img = np.where(field[..., None], rows * np.ones((H, W, 1)), img)
    img = paint_layer(img, ridge(0.45, 0.04, 3.0, 71), (90, 130, 80))
    img = np.where(field[..., None] & (yy > H * 0.47)[..., None], rows, img)
    return finish(img, 1.4)


SCENES = {
    "Foto's/2026-06 Dolomieten/zonsondergang_tre_cime.jpg": mountains_sunset,
    "Foto's/2026-06 Dolomieten/meer_bij_zonsopgang.jpg": lake,
    "Foto's/2026-06 Dolomieten/mist_in_het_dal.jpg": fog_forest,
    "Foto's/2026-06 Dolomieten/noorderlicht_test.jpg": aurora,
    "Foto's/2026-06 Dolomieten/kust_middag.jpg": ocean,
    "Foto's/2026-06 Dolomieten/duinen.jpg": dunes,
    "Foto's/2026-06 Dolomieten/stad_bij_nacht.jpg": night_city,
    "Foto's/2026-06 Dolomieten/blauw_uur_seceda.jpg": lambda: mountains_sunset(1),
    "Foto's/2026-04 Keukenhof/bollenvelden.jpg": tulips,
}

DOCS = {
    "Documenten/reisplanning.md": """# Reisplanning Dolomieten

| Dag | Route | Afstand |
| --- | --- | --- |
| 1 | Cortina - Tre Cime | 24 km |
| 2 | Lago di Braies | 8 km |
| 3 | Seceda | 12 km |

- Paspoort en verzekeringspas
- Powerbank, statief, ND-filters
""",
    "Documenten/notities.txt": "Wekker 04:30 voor de zonsopgang bij het meer.\n"
    "Parkeren bij Auronzo vooraf reserveren.\n",
    "Scripts/rename_photos.py": '''"""Rename camera files to date-based names."""

from datetime import datetime
from pathlib import Path


def new_name(path: Path) -> str:
    taken = datetime.fromtimestamp(path.stat().st_mtime)
    return taken.strftime("%Y-%m-%d_%H%M%S") + path.suffix.lower()


def main(folder: str) -> None:
    for photo in sorted(Path(folder).glob("*.jpg")):
        photo.rename(photo.with_name(new_name(photo)))


if __name__ == "__main__":
    main(".")
''',
}


def make_video() -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "v.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "gradients=s=1280x720:d=4:speed=0.02:c0=0x2a2160:c1=0xf29a5a:x0=0:y0=0:x1=1280:y1=720",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(out),
            ],
            check=True,
        )
        return out.read_bytes()


def build(out: str | Path) -> Path:
    files = {name: fn() for name, fn in SCENES.items()}
    files.update({k: v.encode() for k, v in DOCS.items()})
    try:
        files["Foto's/2026-06 Dolomieten/timelapse_wolken.mp4"] = make_video()
    except Exception as exc:  # noqa: BLE001
        print("no video:", exc)
    Path(out).unlink(missing_ok=True)
    with py7zr.SevenZipFile(out, "w") as z:
        for name, data in files.items():
            z.writef(io.BytesIO(data), name)
    return Path(out)


if __name__ == "__main__":
    print("wrote", build(sys.argv[1]))
