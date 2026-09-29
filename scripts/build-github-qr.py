"""Create a scannable QR link to the public Codex Whip repository."""

from pathlib import Path

import qrcode
from qrcode.constants import ERROR_CORRECT_H


REPOSITORY_URL = "https://github.com/2050246567zb-coder/codex-whip-desktop/releases"
OUTPUT = Path(__file__).resolve().parents[1] / "design" / "github-qr.png"


def main() -> None:
    code = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=16, border=4)
    code.add_data(REPOSITORY_URL)
    code.make(fit=True)
    code.make_image(fill_color="#19191B", back_color="white").save(OUTPUT)


if __name__ == "__main__":
    main()
