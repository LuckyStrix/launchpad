import asyncio
import io
import struct

import httpx
import pytest
from PIL import Image

from app import printing
from app.integrations.base import IntegrationError
from app.integrations.printer import integration as printer

PAPER = printing.Paper.named("letter", dpi=100)  # small pages keep the tests fast


# --- PWG raster ---------------------------------------------------------------


def test_pwg_roundtrip_gray_and_color():
    gray = PAPER.blank()
    gray.putpixel((10, 10), 0)  # a lone pixel: the encoding's one-literal edge case
    gray.paste(0, (100, 200, 300, 260))
    color = Image.effect_noise(PAPER.px, 90).convert("RGB")  # mostly literals, every run length
    pages = printing.decode_pwg(printing.encode_pwg([gray, color], PAPER))

    assert [p["pixels"] for p in pages] == [gray.tobytes(), color.tobytes()]
    assert pages[0] | {"pixels": None} == {"dpi": (100, 100), "page_points": (612, 792), "size": (850, 1100),
                                           "bits_per_pixel": 8, "color_space": 18, "total_pages": 2,
                                           "media": "na_letter_8.5x11in", "pixels": None}
    assert (pages[1]["bits_per_pixel"], pages[1]["color_space"]) == (24, 19)


def test_encode_line_handles_every_run_shape():
    for row in [b"\x00", b"\x00\x01", b"\x05" * 300, bytes(range(256)) * 2, b"\x01\x02" + b"\x07" * 129 + b"\x03"]:
        encoded = printing._encode_line(row, 1)
        assert printing.decode_pwg(_one_line_doc(row, encoded))[0]["pixels"] == row


def _one_line_doc(row: bytes, encoded: bytes) -> bytes:
    header = bytearray(1796)
    struct.pack_into(">II", header, 372, len(row), 1)
    struct.pack_into(">I", header, 388, 8)
    return b"RaS2" + bytes(header) + b"\x00" + encoded


# --- Rendering ----------------------------------------------------------------


def test_qr_is_exactly_the_requested_size():
    page = printing.render_qr("https://example.com", 2.0, PAPER, caption="Scan me")
    left, top, right, _ = Image.eval(page, lambda v: 255 - v).getbbox()
    assert (right - left, top) == (200, 100)  # 2 in wide and 1 in from the top, at 100 dpi


@pytest.mark.parametrize("size", [0.2, 9])
def test_qr_size_limits(size):
    with pytest.raises(ValueError):
        printing.render_qr("x", size, PAPER)


def test_text_wraps_and_paginates():
    pages = printing.render_text("word " * 3000 + "\n" + "x" * 500, PAPER, font_pt=12)
    assert len(pages) > 1
    assert all(p.size == PAPER.px for p in pages)


def test_image_fits_inside_margins():
    data = io.BytesIO()
    Image.new("RGB", (4000, 1000), "red").save(data, "PNG")
    page = printing.render_image(data.getvalue(), PAPER)
    left, _, right, _ = Image.eval(page.convert("L"), lambda v: 255 - v).getbbox()
    assert (left, right) == (50, 800)  # half-inch margins


def test_bad_image_is_a_clear_error():
    with pytest.raises(ValueError, match="Couldn't read that image"):
        printing.render_image(b"not an image", PAPER)


# --- IPP ----------------------------------------------------------------------


def test_ipp_request_roundtrip():
    body = printing.build_request(
        printing.GET_PRINTER_ATTRIBUTES, 7, "ipp://p/ipp/print",
        [(printing.KEYWORD, "requested-attributes", ["printer-state", "marker-levels"])],
        [(printing.INTEGER, "copies", 2), (printing.BOOLEAN, "x-flag", True)])
    assert struct.unpack_from(">BBHI", body) == (2, 0, 0x000B, 7)
    _, attrs = printing.parse_response(body)
    assert attrs["requested-attributes"] == ["printer-state", "marker-levels"]
    assert attrs["printer-uri"] == ["ipp://p/ipp/print"]
    assert (attrs["copies"], attrs["x-flag"]) == ([2], [True])


@pytest.mark.parametrize("uri, url", [
    ("ipp://192.168.10.103/ipp/print", "http://192.168.10.103:631/ipp/print"),
    ("ipp://printer:8631/ipp/print", "http://printer:8631/ipp/print"),
    ("ipps://printer/ipp/print", "https://printer:631/ipp/print"),
    ("ipp://[fd00::5]/ipp/print", "http://[fd00::5]:631/ipp/print"),
])
def test_ipp_uri_to_http(uri, url):
    assert printing.http_url(uri) == url


def test_bad_printer_uri():
    with pytest.raises(ValueError):
        printing.http_url("192.168.10.103")


# --- The integration, against a fake printer ------------------------------------


def _reply(status: int, attrs: list[tuple[int, str, list]]) -> bytes:
    out = bytearray(struct.pack(">BBHI", 2, 0, status, 1)) + b"\x04"
    for tag, name, values in attrs:
        out += printing.attribute(tag, name, values)
    return bytes(out) + b"\x03"


PRINTER_ATTRS = [
    (printing.TEXT, "printer-make-and-model", ["Canon TR4700 series"]),
    (printing.ENUM, "printer-state", [3]),
    (printing.KEYWORD, "printer-state-reasons", ["none"]),
    (printing.BOOLEAN, "printer-is-accepting-jobs", [True]),
    (printing.KEYWORD, "media-ready", ["na_letter_8.5x11in"]),
    (printing.NAME, "marker-names", ["Color", "Black"]),
    (printing.INTEGER, "marker-levels", [90, 12]),
    (printing.MIME, "document-format-supported", ["image/jpeg", "image/pwg-raster"]),
    (printing.KEYWORD, "pwg-raster-document-type-supported", ["srgb_8", "sgray_8"]),
]


@pytest.fixture
def fake_printer(client, monkeypatch, mock_http):
    monkeypatch.setenv("PRINTER_URI", "ipp://192.168.10.103/ipp/print")
    monkeypatch.setenv("PRINTER_DPI", "100")
    jobs: list[bytes] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "files.test":
            png = io.BytesIO()
            Image.new("RGB", (300, 200), "blue").save(png, "PNG")
            return httpx.Response(200, content=png.getvalue(), headers={"content-type": "image/png"})
        assert str(request.url) == "http://192.168.10.103:631/ipp/print"
        body = request.content
        operation = struct.unpack_from(">H", body, 2)[0]
        if operation == printing.GET_PRINTER_ATTRIBUTES:
            # 100 dpi so the tests' small pages are allowed
            res = struct.pack(">iib", 100, 100, 3)
            name = b"pwg-raster-document-resolution-supported"
            extra = bytes([printing.RESOLUTION]) + struct.pack(">H", len(name)) + name + struct.pack(">H", 9) + res
            reply = _reply(0, PRINTER_ATTRS)
            return httpx.Response(200, content=reply[:-1] + extra + b"\x03")
        jobs.append(body)
        return httpx.Response(200, content=_reply(0, [(printing.INTEGER, "job-id", [42]),
                                                      (printing.ENUM, "job-state", [5])]))

    mock_http(handle)
    return jobs


def _document(job: bytes) -> list[dict]:
    return printing.decode_pwg(job[job.index(b"RaS2"):])


def test_tile_shows_state_and_flags_low_ink(fake_printer, client):
    tile = asyncio.run(printer.fetch({}))
    assert (tile.status, tile.value, tile.detail) == ("warn", "Idle", "Canon TR4700 series")
    assert [(i.label, i.detail, i.status) for i in tile.items] == [("Color", "90%", "ok"), ("Black", "12%", "warn")]


def test_status_tool(fake_printer):
    status = asyncio.run(printer.status())
    assert status["state"] == "idle"
    assert status["supplies"] == [{"name": "Color", "percent": 90}, {"name": "Black", "percent": 12}]


def test_print_qr_sends_one_exact_page_per_copy(fake_printer):
    result = asyncio.run(printer.print_qr("https://example.com", size_inches=3, copies=2))
    assert result == {"job_id": 42, "state": "processing", "pages": 2, "printer": "Canon TR4700 series"}
    [job] = fake_printer
    pages = _document(job)
    assert len(pages) == 2 and pages[0]["pixels"] == pages[1]["pixels"]
    page = Image.frombytes("L", pages[0]["size"], pages[0]["pixels"])
    left, _, right, _ = Image.eval(page, lambda v: 255 - v).getbbox()
    assert right - left == 300  # 3 in at 100 dpi
    _, attrs = printing.parse_response(job[:job.index(b"RaS2")])
    assert attrs["document-format"] == ["image/pwg-raster"]
    assert attrs["print-color-mode"] == ["monochrome"]


def test_print_text_and_url(fake_printer):
    asyncio.run(printer.print_text("Shopping list\n- eggs\n- milk"))
    asyncio.run(printer.print_url("https://files.test/photo.png"))
    text_job, photo_job = fake_printer
    assert _document(text_job)[0]["bits_per_pixel"] == 8
    assert _document(photo_job)[0]["bits_per_pixel"] == 24
    _, attrs = printing.parse_response(photo_job[:photo_job.index(b"RaS2")])
    assert attrs["print-color-mode"] == ["color"]


@pytest.mark.parametrize("call, message", [
    (lambda: printer.print_qr("x", copies=50), "copies"),
    (lambda: printer.print_text("hi", font_size=200), "font_size"),
    (lambda: printer.print_url("file:///etc/passwd"), "http"),
    (lambda: printer.print_qr("x", size_inches=20), "QR size"),
])
def test_bad_requests_are_explained(fake_printer, call, message):
    with pytest.raises(IntegrationError, match=message):
        asyncio.run(call())
    assert fake_printer == []


def test_offline_printer_is_explained(client, monkeypatch, mock_http):
    monkeypatch.setenv("PRINTER_URI", "ipp://192.168.10.103/ipp/print")

    def offline(request):
        raise httpx.ConnectTimeout("timed out")

    mock_http(offline)
    with pytest.raises(IntegrationError, match="Couldn't reach the printer"):
        asyncio.run(printer.status())


def test_printing_needs_approval_and_status_does_not(client, monkeypatch):
    monkeypatch.setenv("PRINTER_URI", "ipp://p/ipp/print")
    tools = {fn.__name__: fn._launchpad_tool["write"] for fn in printer.tools()}
    assert tools == {"status": False, "print_qr": True, "print_text": True, "print_url": True}


# --- Review fixes ---------------------------------------------------------------


def test_transparent_png_prints_on_white():
    data = io.BytesIO()
    Image.new("RGBA", (40, 40), (0, 0, 0, 0)).save(data, "PNG")  # fully transparent "black"
    page = printing.render_image(data.getvalue(), PAPER)
    assert page.getpixel((PAPER.px[0] // 2, PAPER.px[1] // 2)) == (255, 255, 255)


def test_too_much_qr_data_is_a_clear_error():
    with pytest.raises(ValueError, match="too much data"):
        printing.render_qr("x" * 5000, 7.0, PAPER)


def test_long_unbroken_text_is_fast_and_kept():
    blob = "A" * 20000
    pages = printing.render_text(blob, PAPER)
    assert len(pages) >= 1


def test_truncated_or_odd_replies():
    with pytest.raises(printing.IPPError):
        printing.parse_response(b"\x02\x00\x00\x00\x00\x00\x00\x01\x04\x21\x00")
    # marker-levels with an out-of-band "unknown" value, and a bad resolution
    body = bytearray(struct.pack(">BBHI", 2, 0, 0, 1)) + b"\x04"
    body += printing.attribute(printing.NAME, "marker-names", ["Black"])
    body += bytes([0x12]) + struct.pack(">H", 13) + b"marker-levels" + struct.pack(">H", 0)
    body += bytes([printing.RESOLUTION]) + struct.pack(">H", 4) + b"res!" + struct.pack(">H", 2) + b"xx"
    _, attrs = printing.parse_response(bytes(body) + b"\x03")
    described = printing.describe_printer(attrs)
    assert described["supplies"] == [{"name": "Black", "percent": None}]


def test_pdf_pages_stream_one_at_a_time():
    pdf = io.BytesIO()
    Image.new("RGB", (850, 1100), "red").save(pdf, "PDF", resolution=100, save_all=True,
                                              append_images=[Image.new("RGB", (850, 1100), "blue")])
    assert printing.pdf_page_count(pdf.getvalue()) == 2
    pages = printing.render_pdf(pdf.getvalue(), PAPER)
    first = next(pages)
    assert first.size == PAPER.px and first.getpixel((425, 550))[0] > 200


def test_copies_reuse_the_encoded_page_and_count_every_page(fake_printer):
    asyncio.run(printer.print_text("Hello", copies=3))
    pages = _document(fake_printer[0])
    assert len(pages) == 3 and {p["total_pages"] for p in pages} == {3}


def test_blank_paper_settings_fall_back_to_defaults(fake_printer, monkeypatch):
    monkeypatch.setenv("PRINTER_PAPER", "")
    assert printer._paper.media == "na_letter_8.5x11in"
    monkeypatch.setenv("PRINTER_DPI", "")
    assert printer._paper.dpi == 600
