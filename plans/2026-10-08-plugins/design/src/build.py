"""Draw the street again from scene.py and put it back into login.html and
mobile.html, in place of the drawing they hold."""

from __future__ import annotations

import re
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here))
import scene  # noqa: E402


def put(name: str, cls: str, svg: str) -> None:
    page = here / name
    text, count = re.subn(rf'<svg class="{cls}".*?</svg>', lambda _: svg, page.read_text(), count=1, flags=re.S)
    assert count == 1, (name, cls)
    page.write_text(text)


if __name__ == "__main__":
    put("login.html", "street-art", scene.desktop())
    top, bottom = scene.phone()
    put("mobile.html", "street-top", top)
    put("mobile.html", "street-bottom", bottom)
