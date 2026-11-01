"""A network printer: a status tile, and tools so Hermes can print QR codes, text,
PDFs and pictures. Printing is a write tool, so every job waits for you to approve
it in the chat drawer; the card shows the tool's arguments (the text, the QR data,
or the URL that will be downloaded).

Talks IPP straight to the printer (any IPP Everywhere / AirPrint printer), so
there's no CUPS to install. See docs/printer.md.
"""

import asyncio
import itertools
from collections.abc import Callable, Iterable
from typing import Any

import httpx
from PIL import Image

from .. import printing
from ..printing import Paper
from .base import Integration, IntegrationError, Item, TileData, http, tool

MAX_COPIES = 10
MAX_PDF_PAGES = 30
MAX_DOWNLOAD = 40 * 1024 * 1024
MAX_JOB_BYTES = 400 * 1024 * 1024  # encoded size; real documents are far smaller


class Printer(Integration):
    id = "printer"
    name = "Printer"
    description = "Status and ink levels for a network printer. Lets Hermes print."
    icon = "🖨️"
    requires = ("PRINTER_URI",)
    refresh = 60
    _request_ids = itertools.count(1)

    # --- talking to the printer -------------------------------------------

    @property
    def _uri(self) -> str:
        return self.env("PRINTER_URI")

    @property
    def _paper(self) -> Paper:
        try:
            dpi = int(self.env("PRINTER_DPI") or "600")
            return Paper.named(self.env("PRINTER_PAPER") or "letter", dpi=dpi)
        except ValueError as e:
            raise IntegrationError(f"Check PRINTER_PAPER and PRINTER_DPI: {e}") from None

    async def _ipp(self, operation: int, operation_attrs=(), job_attrs=(), document: bytes = b"",
                   timeout: float = 15) -> dict[str, list[Any]]:
        try:
            url = printing.http_url(self._uri)
        except ValueError as e:
            raise IntegrationError(str(e)) from None
        body = printing.build_request(operation, next(self._request_ids), self._uri,
                                      list(operation_attrs), list(job_attrs)) + document
        try:
            async with http(timeout=timeout) as client:
                response = await client.post(url, content=body, headers={"Content-Type": "application/ipp"})
        except httpx.HTTPError:
            raise IntegrationError(f"Couldn't reach the printer at {self._uri}. Is it on, and is its network "
                                   "reachable from Launchpad?") from None
        if response.is_error:
            raise IntegrationError(f"The printer returned HTTP {response.status_code}.")
        try:
            status, attrs = printing.parse_response(response.content)
        except printing.IPPError:
            raise IntegrationError("The printer sent a reply Launchpad couldn't read.") from None
        if not printing.status_ok(status):
            message = (attrs.get("status-message") or [None])[0] or f"IPP status 0x{status:04x}"
            raise IntegrationError(f"The printer refused the request: {message}")
        return attrs

    async def _printer(self) -> dict[str, Any]:
        attrs = await self._ipp(printing.GET_PRINTER_ATTRIBUTES, [
            (printing.KEYWORD, "requested-attributes",
             ["printer-make-and-model", "printer-state", "printer-state-reasons", "printer-is-accepting-jobs",
              "media-ready", "marker-names", "marker-levels", "document-format-supported",
              "pwg-raster-document-resolution-supported", "pwg-raster-document-type-supported"]),
        ])
        return printing.describe_printer(attrs)

    async def _send(self, render: Callable[[], Iterable[Image.Image]], page_count: int, job_name: str,
                    copies: int) -> dict[str, Any]:
        """Check the printer, then render and encode one page at a time (in a worker thread),
        and send the job. Each page is encoded once however many copies there are."""
        printer = await self._printer()
        if "image/pwg-raster" not in printer["formats"]:
            raise IntegrationError(f"{printer['model']} doesn't accept PWG raster, so Launchpad can't print to it.")
        paper = self._paper
        if printer["resolutions"] and paper.dpi not in printer["resolutions"]:
            raise IntegrationError(f"Set PRINTER_DPI to one of {printer['resolutions']}; the printer doesn't "
                                   f"do {paper.dpi} dpi.")
        if not printer["accepting_jobs"]:
            raise IntegrationError("The printer isn't accepting jobs right now.")
        types = set(printer["color_types"])
        total = page_count * copies

        def encode() -> tuple[list[bytes], bool]:
            encoded, color, size = [], False, 0
            for page in render():
                # Send only the raster types the printer lists (when it lists any).
                if page.mode == "RGB" and types and "srgb_8" not in types:
                    page = page.convert("L")
                elif page.mode == "L" and types and "sgray_8" not in types:
                    page = page.convert("RGB")
                color |= page.mode == "RGB"
                encoded.append(printing.encode_page(page, paper, total))
                size += len(encoded[-1]) * copies
                if size > MAX_JOB_BYTES:
                    raise ValueError("That job is too large to send; try fewer pages or copies")
            return encoded, color

        pages, color = await asyncio.to_thread(self._render, encode)
        attrs = await self._ipp(
            printing.PRINT_JOB,
            [(printing.NAME, "requesting-user-name", "launchpad"),
             (printing.NAME, "job-name", job_name[:60] or "Launchpad"),
             (printing.MIME, "document-format", "image/pwg-raster")],
            [(printing.KEYWORD, "media", paper.media),
             (printing.KEYWORD, "print-color-mode", "color" if color else "monochrome")],
            document=printing.pwg_document(pages * copies),  # collated copies: 1,2,3,1,2,3
            timeout=180,
        )
        job_id = (attrs.get("job-id") or [None])[0]
        state = printing.JOB_STATES.get((attrs.get("job-state") or [None])[0], "sent")
        result = {"job_id": job_id, "state": state, "pages": total, "printer": printer["model"]}
        if printer["problems"]:
            result["printer_problems"] = printer["problems"]
        return result

    @staticmethod
    def _render(fn: Callable, *args: Any) -> Any:
        """Run a renderer, turning its ValueErrors (written for the user) into IntegrationErrors."""
        try:
            return fn(*args)
        except ValueError as e:
            raise IntegrationError(str(e)) from None

    @staticmethod
    def _check_copies(copies: int) -> None:
        if not 1 <= copies <= MAX_COPIES:
            raise IntegrationError(f"copies must be between 1 and {MAX_COPIES}")

    # --- tile -------------------------------------------------------------

    async def fetch(self, config):
        printer = await self._printer()
        problems = printer["problems"]
        low = [s for s in printer["supplies"] if s["percent"] is not None and s["percent"] <= 15]
        status = "error" if printer["state"] == "stopped" else "warn" if problems or low else "ok"
        items = [Item(label=s["name"], detail="?" if s["percent"] is None else f"{s['percent']}%",
                      status="warn" if s in low else "ok") for s in printer["supplies"]]
        detail = ", ".join(problems) if problems else printer["model"]
        return TileData(status=status, value=printer["state"].capitalize(), detail=detail, items=items)

    # --- tools for Hermes -------------------------------------------------

    @tool()
    async def status(self) -> dict:
        """The printer's state, any problems (out of paper, jam, ...), paper loaded and ink levels."""
        printer = await self._printer()
        return {k: printer[k] for k in ("model", "state", "problems", "accepting_jobs", "paper_loaded", "supplies")}

    @tool(write=True)
    async def print_qr(self, data: str, size_inches: float = 2.0, caption: str = "", copies: int = 1) -> dict:
        """Print a QR code (a URL or any text) at an exact size in inches, on its own page, with an
        optional caption underneath. The printed size is exact, not scaled."""
        self._check_copies(copies)
        paper = self._paper
        page = await asyncio.to_thread(self._render, printing.render_qr, data, size_inches, paper, caption)
        return await self._send(lambda: [page], 1, f"QR {size_inches:g}in", copies)

    @tool(write=True)
    async def print_text(self, text: str, font_size: float = 12, copies: int = 1) -> dict:
        """Print plain text (notes, a list, a letter). Word-wrapped and split into pages automatically.
        font_size is in points."""
        self._check_copies(copies)
        if not 6 <= font_size <= 72:
            raise IntegrationError("font_size must be between 6 and 72 points")
        paper = self._paper
        pages = await asyncio.to_thread(self._render, printing.render_text, text, paper, font_size)
        return await self._send(lambda: pages, len(pages), text.strip().splitlines()[0], copies)

    @tool(write=True)
    async def print_url(self, url: str, copies: int = 1) -> dict:
        """Download a PDF or a picture (PNG, JPEG, ...) from an http(s) URL and print it. PDFs print at their
        own size (shrunk only if bigger than the paper); pictures are fitted to the page."""
        self._check_copies(copies)
        if not url.startswith(("http://", "https://")):
            raise IntegrationError("Only http and https URLs can be printed.")
        paper = self._paper
        data, content_type = await self._download(url)
        name = url.split("?")[0].rsplit("/", 1)[-1] or url
        if data.startswith(b"%PDF") or "pdf" in content_type:
            count = await asyncio.to_thread(self._render, printing.pdf_page_count, data)
            if count > MAX_PDF_PAGES:
                raise IntegrationError(f"That PDF has {count} pages; the limit is {MAX_PDF_PAGES}.")
            return await self._send(lambda: printing.render_pdf(data, paper), count, name, copies)
        page = await asyncio.to_thread(self._render, printing.render_image, data, paper)
        return await self._send(lambda: [page], 1, name, copies)

    async def _download(self, url: str) -> tuple[bytes, str]:
        try:
            async with http(timeout=60, follow_redirects=True) as client, client.stream("GET", url) as response:
                if response.is_error:
                    raise IntegrationError(f"Downloading that URL failed with HTTP {response.status_code}.")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data += chunk
                    if len(data) > MAX_DOWNLOAD:
                        raise IntegrationError(f"That file is over {MAX_DOWNLOAD // 2**20} MB.")
                return bytes(data), response.headers.get("content-type", "")
        except httpx.HTTPError:
            raise IntegrationError("Couldn't download that URL.") from None


integration = Printer()
