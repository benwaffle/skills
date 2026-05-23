#!/usr/bin/env python3
"""Put HTML + plain-text on the macOS pasteboard with proper UTIs.

Usage:
    uv run --with pyobjc-framework-Cocoa set-clipboard.py <html-file> <plain-file>

Writes both representations to a single NSPasteboardItem so apps that prefer
HTML (rich paste) and apps that fall back to plain text both see the right
content. Prints the resulting pasteboard types for verification.
"""
# /// script
# dependencies = ["pyobjc-framework-Cocoa"]
# ///

import sys
from pathlib import Path

import AppKit


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    html_path, plain_path = Path(sys.argv[1]), Path(sys.argv[2])
    html_bytes = html_path.read_bytes()
    plain_text = plain_path.read_text()

    pb = AppKit.NSPasteboard.generalPasteboard()
    pb.clearContents()

    item = AppKit.NSPasteboardItem.alloc().init()
    item.setData_forType_(
        AppKit.NSData.dataWithBytes_length_(html_bytes, len(html_bytes)),
        "public.html",
    )
    item.setString_forType_(plain_text, "public.utf8-plain-text")
    pb.writeObjects_([item])

    print("Pasteboard types now set:")
    for t in pb.types():
        print(f"  {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
