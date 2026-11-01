"""Printing to a network printer over IPP, with no CUPS or Ghostscript needed.

Pages are drawn with Pillow at the printer's resolution and sent as PWG raster
(image/pwg-raster), the format every IPP Everywhere / AirPrint printer accepts.
A PWG page carries its exact size and resolution, so a 2-inch QR code prints at
2 inches: nothing gets "fit to page".

Three layers, each usable on its own:
- render_qr / render_text / render_image / render_pdf: Pillow page images.
- encode_page / pwg_document: page images to a PWG raster document.
- build_request / parse_response / describe_printer: IPP messages.
"""

from __future__ import annotations

import io
import itertools
import struct
import textwrap
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

# --- Paper ------------------------------------------------------------------

# name -> (IPP media keyword, width in, height in)
MEDIA = {
    "letter": ("na_letter_8.5x11in", 8.5, 11.0),
    "a4": ("iso_a4_210x297mm", 210 / 25.4, 297 / 25.4),
}

MARGIN_IN = 0.5  # keep content clear of the printer's unprintable edge


@dataclass(frozen=True)
class Paper:
    media: str  # IPP keyword
    width_in: float
    height_in: float
    dpi: int

    @classmethod
    def named(cls, name: str, dpi: int) -> Paper:
        try:
            media, w, h = MEDIA[name.lower()]
        except KeyError:
            raise ValueError(f"Unknown paper size {name!r}; use one of {', '.join(MEDIA)}") from None
        if not 72 <= dpi <= 1200:
            raise ValueError("The print resolution must be between 72 and 1200 dpi")
        return cls(media, w, h, dpi)

    @property
    def px(self) -> tuple[int, int]:
        return round(self.width_in * self.dpi), round(self.height_in * self.dpi)

    def inches(self, n: float) -> int:
        return round(n * self.dpi)

    def blank(self, mode: str = "L") -> Image.Image:
        return Image.new(mode, self.px, "white")


# --- Rendering ----------------------------------------------------------------
# Renderers raise ValueError with a message meant for the user.


def _font(size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # Pillow ships a scalable default font, so the container needs no font packages.
    return ImageFont.load_default(size=size_px)


def render_qr(data: str, size_in: float, paper: Paper, caption: str = "") -> Image.Image:
    """A page with a QR code exactly `size_in` inches square, centred, 1 inch from the top."""
    import qrcode
    from qrcode.exceptions import DataOverflowError

    max_in = min(paper.width_in, paper.height_in) - 2 * MARGIN_IN
    if not 0.5 <= size_in <= max_in:
        raise ValueError(f"QR size must be between 0.5 and {max_in:.1f} inches")
    if not data:
        raise ValueError("There's nothing to put in the QR code")
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=1, border=0)
    qr.add_data(data)
    try:
        qr.make(fit=True)
    except (DataOverflowError, ValueError):  # newer qrcode reports overflow as "Invalid version"
        raise ValueError("That's too much data for one QR code") from None
    code = qr.make_image(fill_color="black", back_color="white").get_image().convert("L")
    side = paper.inches(size_in)
    if side < code.width * 3:
        raise ValueError("That's too much data for a QR code this small; make it bigger or shorten the data")
    code = code.resize((side, side), Image.NEAREST)  # nearest keeps module edges crisp

    page = paper.blank()
    top = paper.inches(1)
    page.paste(code, ((page.width - side) // 2, top))
    if caption:
        draw = ImageDraw.Draw(page)
        font = _font(paper.inches(0.18))
        y = top + side + paper.inches(0.2)
        for line in textwrap.wrap(caption, 60)[:3]:
            width = draw.textlength(line, font=font)
            draw.text(((page.width - width) / 2, y), line, font=font, fill="black")
            y += paper.inches(0.28)
    return page


def _fit_prefix(word: str, fits) -> int:
    """Length of the longest prefix of `word` that fits (at least 1), by binary search."""
    lo, hi = 1, len(word)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(word[:mid]):
            lo = mid
        else:
            hi = mid - 1
    return lo


def render_text(text: str, paper: Paper, font_pt: float = 12) -> list[Image.Image]:
    """Plain text, word-wrapped and paginated with half-inch margins."""
    if not text.strip():
        raise ValueError("There's no text to print")
    font_px = round(font_pt / 72 * paper.dpi)
    font = _font(font_px)
    line_h = round(font_px * 1.35)
    margin = paper.inches(MARGIN_IN)
    usable_w = paper.px[0] - 2 * margin
    lines_per_page = max(1, (paper.px[1] - 2 * margin) // line_h)

    measure = ImageDraw.Draw(Image.new("L", (1, 1)))

    def fits(s: str) -> bool:
        return measure.textlength(s, font=font) <= usable_w

    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}" if line else word
            if fits(candidate):
                line = candidate
                continue
            if line:
                lines.append(line)
            while not fits(word):  # a word wider than the page is broken by characters
                cut = _fit_prefix(word, fits)
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)

    pages = []
    for start in range(0, len(lines), lines_per_page):
        page = paper.blank()
        draw = ImageDraw.Draw(page)
        for i, line in enumerate(lines[start:start + lines_per_page]):
            draw.text((margin, margin + i * line_h), line, font=font, fill="black")
        pages.append(page)
    return pages


def _on_white(picture: Image.Image) -> Image.Image:
    """RGB with any transparency composited onto white paper (not onto black)."""
    picture = ImageOps.exif_transpose(picture)
    if picture.mode == "P" and "transparency" in picture.info:
        picture = picture.convert("RGBA")
    if picture.mode in ("RGBA", "LA", "PA"):
        picture = picture.convert("RGBA")
        background = Image.new("RGB", picture.size, "white")
        background.paste(picture, mask=picture.getchannel("A"))
        return background
    return picture.convert("RGB")


def render_image(data: bytes, paper: Paper) -> Image.Image:
    """A picture scaled to fit inside the margins, centred."""
    try:
        picture = _on_white(Image.open(io.BytesIO(data)))
    except Exception as e:  # Pillow raises many types for bad or unsupported files
        raise ValueError(f"Couldn't read that image: {e}") from None
    margin = paper.inches(MARGIN_IN)
    box = (paper.px[0] - 2 * margin, paper.px[1] - 2 * margin)
    picture = ImageOps.contain(picture, box, Image.LANCZOS)
    page = paper.blank("RGB")
    page.paste(picture, ((page.width - picture.width) // 2, (page.height - picture.height) // 2))
    return page


def pdf_page_count(data: bytes) -> int:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as e:
        raise ValueError(f"Couldn't read that PDF: {e}") from None
    try:
        return len(pdf)
    finally:
        pdf.close()


def render_pdf(data: bytes, paper: Paper) -> Iterator[Image.Image]:
    """Each PDF page drawn at the printer's resolution (shrunk only if bigger than the paper).
    Yields one page at a time so a long PDF never sits in memory as full-resolution images."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as e:
        raise ValueError(f"Couldn't read that PDF: {e}") from None
    try:
        for number, pdf_page in enumerate(pdf, 1):
            try:
                w_in, h_in = pdf_page.get_width() / 72, pdf_page.get_height() / 72
                scale = min(1.0, paper.width_in / w_in, paper.height_in / h_in)
                image = pdf_page.render(scale=paper.dpi / 72 * scale).to_pil().convert("RGB")
            except Exception as e:
                raise ValueError(f"Couldn't draw page {number} of that PDF: {e}") from None
            finally:
                pdf_page.close()
            page = paper.blank("RGB")
            page.paste(image, ((page.width - image.width) // 2, (page.height - image.height) // 2))
            yield page
    finally:
        pdf.close()


# --- PWG raster -------------------------------------------------------------

_HEADER_LEN = 1796


def _pwg_header(page: Image.Image, paper: Paper, total_pages: int) -> bytes:
    """One PWG 5102.4 page header. Only the fields IPP printers read are set."""
    color = page.mode == "RGB"
    bpp = 24 if color else 8
    h = bytearray(_HEADER_LEN)

    def put_str(offset: int, value: str) -> None:
        raw = value.encode()[:63]
        h[offset:offset + len(raw)] = raw

    def put_u32(offset: int, *values: int) -> None:
        struct.pack_into(f">{len(values)}I", h, offset, *values)

    put_str(0, "PwgRaster")
    put_u32(276, paper.dpi, paper.dpi)  # HWResolution
    put_u32(340, 1)  # NumCopies (copies are sent as repeated pages)
    put_u32(352, round(paper.width_in * 72), round(paper.height_in * 72))  # PageSize, points
    put_u32(372, page.width, page.height)  # cupsWidth, cupsHeight
    put_u32(384, 8, bpp, page.width * bpp // 8)  # BitsPerColor, BitsPerPixel, BytesPerLine
    put_u32(400, 19 if color else 18)  # ColorSpace: sRGB / sGray
    put_u32(420, 3 if color else 1)  # NumColors
    put_u32(452, total_pages)  # TotalPageCount
    put_u32(456, 1, 1)  # CrossFeedTransform, FeedTransform
    put_u32(480, 0xFFFFFF)  # AlternatePrimary (white)
    put_str(1732, paper.media)  # PageSizeName
    return bytes(h)


def _encode_line(row: bytes, bpp: int) -> bytes:
    """PWG run-length encoding of one row. Control byte n < 128: the next pixel repeats n+1 times.
    n > 128: 257-n literal pixels follow (2..128). A lone pixel is a run of one (0)."""
    if bpp == 1:
        pixels: Any = row
    else:
        pixels = zip(*(row[i::bpp] for i in range(bpp)), strict=True)
    out = bytearray()
    literals: list[bytes] = []

    def flush() -> None:
        for start in range(0, len(literals), 128):
            chunk = literals[start:start + 128]
            out.append(257 - len(chunk) if len(chunk) > 1 else 0)
            out.extend(b"".join(chunk))
        literals.clear()

    for value, group in itertools.groupby(pixels):  # groupby runs in C: fast even for photos
        count = sum(1 for _ in group)
        pixel = bytes([value]) if bpp == 1 else bytes(value)
        if count == 1:
            literals.append(pixel)
            continue
        flush()
        while count:
            run = min(count, 128)
            if run == 1:
                literals.append(pixel)
            else:
                out.append(run - 1)
                out.extend(pixel)
            count -= run
    flush()
    return bytes(out)


def encode_page(page: Image.Image, paper: Paper, total_pages: int) -> bytes:
    """One page (header + compressed pixels) of a PWG document. `page` is "L" or "RGB"."""
    if page.size != paper.px:
        raise ValueError("page image doesn't match the paper size")
    if page.mode not in ("L", "RGB"):
        page = page.convert("RGB")
    bpp = 3 if page.mode == "RGB" else 1
    out = bytearray(_pwg_header(page, paper, total_pages))
    raw = page.tobytes()
    stride = page.width * bpp
    y = 0
    while y < page.height:
        row = raw[y * stride:(y + 1) * stride]
        repeat = 1
        while (y + repeat < page.height and repeat < 256
               and raw[(y + repeat) * stride:(y + repeat + 1) * stride] == row):
            repeat += 1
        out.append(repeat - 1)
        out += _encode_line(row, bpp)
        y += repeat
    return bytes(out)


def pwg_document(encoded_pages: list[bytes]) -> bytes:
    return b"RaS2" + b"".join(encoded_pages)


def encode_pwg(pages: list[Image.Image], paper: Paper) -> bytes:
    """A whole PWG raster document from page images."""
    return pwg_document([encode_page(p, paper, len(pages)) for p in pages])


def decode_pwg(data: bytes) -> list[dict[str, Any]]:
    """Read back a PWG document's headers and pixels (used by the tests)."""
    if data[:4] != b"RaS2":
        raise ValueError("not PWG raster")
    pos, pages = 4, []
    while pos < len(data):
        h = data[pos:pos + _HEADER_LEN]
        pos += _HEADER_LEN
        width, height = struct.unpack_from(">II", h, 372)
        bpp = struct.unpack_from(">I", h, 388)[0] // 8
        rows: list[bytes] = []
        while len(rows) < height:
            repeat = data[pos] + 1
            pos += 1
            row = bytearray()
            while len(row) < width * bpp:
                c = data[pos]
                pos += 1
                if c < 128:
                    row += data[pos:pos + bpp] * (c + 1)
                    pos += bpp
                else:
                    count = 257 - c
                    row += data[pos:pos + count * bpp]
                    pos += count * bpp
            rows += [bytes(row)] * repeat
        pages.append({
            "dpi": struct.unpack_from(">II", h, 276),
            "page_points": struct.unpack_from(">II", h, 352),
            "size": (width, height),
            "bits_per_pixel": bpp * 8,
            "color_space": struct.unpack_from(">I", h, 400)[0],
            "total_pages": struct.unpack_from(">I", h, 452)[0],
            "media": h[1732:1796].split(b"\0")[0].decode(),
            "pixels": b"".join(rows),
        })
    return pages


# --- IPP ----------------------------------------------------------------------

# Value tags (RFC 8010 §3.5.2)
INTEGER, BOOLEAN, ENUM = 0x21, 0x22, 0x23
RESOLUTION, RANGE = 0x32, 0x33
TEXT, NAME, KEYWORD, URI, CHARSET, LANGUAGE, MIME = 0x41, 0x42, 0x44, 0x45, 0x47, 0x48, 0x49
OPERATION, JOB, END = 0x01, 0x02, 0x03

PRINT_JOB, GET_PRINTER_ATTRIBUTES = 0x0002, 0x000B

PRINTER_STATES = {3: "idle", 4: "printing", 5: "stopped"}
JOB_STATES = {3: "pending", 4: "held", 5: "processing", 6: "stopped", 7: "canceled", 8: "aborted", 9: "completed"}


class IPPError(Exception):
    pass


def http_url(printer_uri: str) -> str:
    """ipp://host/path -> http://host:631/path (ipps -> https)."""
    scheme, sep, rest = printer_uri.partition("://")
    if not sep or scheme not in ("ipp", "ipps", "http", "https"):
        raise ValueError("PRINTER_URI must look like ipp://192.168.1.50/ipp/print")
    if scheme in ("http", "https"):
        return printer_uri
    host, slash, path = rest.partition("/")
    if ":" not in host.rsplit("]", 1)[-1]:
        host += ":631"
    return f"{'https' if scheme == 'ipps' else 'http'}://{host}{slash}{path}"


def attribute(tag: int, name: str, value: Any) -> bytes:
    """One attribute, encoded. A list value becomes a 1setOf (extra values have an empty name)."""
    values = value if isinstance(value, list) else [value]
    out = bytearray()
    for i, v in enumerate(values):
        if tag in (INTEGER, ENUM):
            raw = struct.pack(">i", v)
        elif tag == BOOLEAN:
            raw = b"\x01" if v else b"\x00"
        else:
            raw = str(v).encode()
        key = name.encode() if i == 0 else b""
        out += struct.pack(">BH", tag, len(key)) + key + struct.pack(">H", len(raw)) + raw
    return bytes(out)


def build_request(operation: int, request_id: int, printer_uri: str,
                  operation_attrs: list[tuple[int, str, Any]] = (),
                  job_attrs: list[tuple[int, str, Any]] = ()) -> bytes:
    out = bytearray(struct.pack(">BBHI", 2, 0, operation, request_id))
    out.append(OPERATION)
    out += attribute(CHARSET, "attributes-charset", "utf-8")
    out += attribute(LANGUAGE, "attributes-natural-language", "en")
    out += attribute(URI, "printer-uri", printer_uri)
    for tag, name, value in operation_attrs:
        out += attribute(tag, name, value)
    if job_attrs:
        out.append(JOB)
        for tag, name, value in job_attrs:
            out += attribute(tag, name, value)
    out.append(END)
    return bytes(out)


def parse_response(data: bytes) -> tuple[int, dict[str, list[Any]]]:
    """(status code, {attribute name: [values]}), all groups merged. Raises IPPError if malformed."""
    try:
        return _parse_response(data)
    except (struct.error, IndexError, UnicodeDecodeError) as e:
        raise IPPError(f"malformed IPP reply: {e}") from None


def _parse_response(data: bytes) -> tuple[int, dict[str, list[Any]]]:
    if len(data) < 9:
        raise IPPError("The printer sent an empty or truncated reply")
    status = struct.unpack_from(">H", data, 2)[0]
    attrs: dict[str, list[Any]] = {}
    pos, name = 8, ""
    while pos < len(data):
        tag = data[pos]
        pos += 1
        if tag == END:
            break
        if tag < 0x10:  # a new attribute group
            continue
        name_len = struct.unpack_from(">H", data, pos)[0]
        pos += 2
        if name_len:
            name = data[pos:pos + name_len].decode(errors="replace")
        pos += name_len
        value_len = struct.unpack_from(">H", data, pos)[0]
        pos += 2
        raw = data[pos:pos + value_len]
        if len(raw) != value_len:
            raise IPPError("The printer's reply was cut short")
        pos += value_len
        if tag in (INTEGER, ENUM) and value_len == 4:
            value: Any = struct.unpack(">i", raw)[0]
        elif tag == BOOLEAN:
            value = raw == b"\x01"
        elif tag == RESOLUTION and value_len == 9:
            value = struct.unpack(">iib", raw)
        elif tag == RANGE and value_len == 8:
            value = struct.unpack(">ii", raw)
        elif 0x40 <= tag <= 0x5F:
            value = raw.decode(errors="replace")
        else:
            value = None  # out-of-band (unknown / no-value) or a type Launchpad doesn't use
        attrs.setdefault(name, []).append(value)
    return status, attrs


def status_ok(status: int) -> bool:
    return status < 0x0100  # successful-ok and its "ignored or substituted" variants


def describe_printer(attrs: dict[str, list[Any]]) -> dict[str, Any]:
    """The useful parts of Get-Printer-Attributes, in plain terms. Tolerates odd or missing values."""

    def one(name: str, default: Any = None) -> Any:
        value = attrs.get(name, [None])[0]
        return default if value is None else value

    def strings(name: str) -> list[str]:
        return [v for v in attrs.get(name, []) if isinstance(v, str)]

    reasons = [r for r in strings("printer-state-reasons") if r != "none"]
    levels = attrs.get("marker-levels", [])
    supplies = []
    for i, supply_name in enumerate(strings("marker-names")):
        level = levels[i] if i < len(levels) else None
        supplies.append({"name": supply_name,
                         "percent": level if isinstance(level, int) and 0 <= level <= 100 else None})
    resolutions = [r[0] for r in attrs.get("pwg-raster-document-resolution-supported", [])
                   if isinstance(r, tuple) and len(r) == 3 and r[2] == 3 and r[0] == r[1]]
    return {
        "model": one("printer-make-and-model", "Printer"),
        "state": PRINTER_STATES.get(one("printer-state"), "unknown"),
        "problems": reasons,
        "accepting_jobs": one("printer-is-accepting-jobs", True) is not False,
        "paper_loaded": strings("media-ready"),
        "supplies": supplies,
        "formats": strings("document-format-supported"),
        "resolutions": resolutions,
        "color_types": strings("pwg-raster-document-type-supported"),
    }
