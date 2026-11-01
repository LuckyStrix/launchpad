# Printer

The **Printer** integration gives you a status tile (state, problems, ink levels) and lets Hermes print. It talks IPP straight to the printer, so it works with any IPP Everywhere / AirPrint printer, with no CUPS or drivers in the container.

## Tools Hermes gets

| Tool | What it does |
|---|---|
| `printer_status` | State, problems (out of paper, jam), paper loaded, ink levels |
| `printer_print_qr` | A QR code at an exact size in inches, with an optional caption |
| `printer_print_text` | Plain text, word-wrapped and paginated |
| `printer_print_url` | A PDF (up to 30 pages) or picture downloaded from an http(s) URL |

Every print waits for you to click **Approve** in the chat drawer. The card shows the tool's arguments: the text, the QR code's contents, or the URL. For `printer_print_url`, the file is downloaded after you approve, so only approve URLs you trust. Launchpad can reach your LAN and tailnet, so a URL can point at internal pages too. All tools take `copies` (up to 10).

Sizes are exact. Pages are drawn at the printer's resolution and sent as PWG raster, which carries its own page size, so a 2-inch QR code prints at 2 inches and is never "fit to page".

## Setup

1. **Find the printer's IPP address.** It's usually `ipp://<printer-ip>/ipp/print`. To check it:

   ```sh
   ipptool -tv ipp://<printer-ip>/ipp/print get-printer-attributes.test | grep -E 'make-and-model|pwg-raster'
   ```

   The printer must list `image/pwg-raster` among its formats. Nearly all printers made since about 2015 do.

2. **Set it in `.env`:**

   ```sh
   PRINTER_URI=ipp://192.168.10.103/ipp/print
   PRINTER_PAPER=letter   # or a4
   PRINTER_DPI=600        # one of the printer's pwg-raster resolutions
   ```

3. **Make sure Launchpad can reach the printer.**
   - **Printer on the same LAN as the Docker host:** this works as-is.
   - **Printer behind a Tailscale subnet router** (another site's network, shared over Tailscale): the sidecar has to accept subnet routes. Add `--accept-routes` to `TS_EXTRA_ARGS`, for example `TS_EXTRA_ARGS=--accept-routes`, and check the subnet route is approved in the Tailscale admin console.

4. Run `make up`, and add a **Printer** tile. Then run `/reload-mcp` in Hermes so it sees the new tools.

## Asking Hermes

- "Print a 3-inch QR code for https://example.com with the caption 'Scan me'."
- "Print my shopping list."
- "Print this PDF: https://…/form.pdf, two copies."
- "Is the printer out of ink?"
