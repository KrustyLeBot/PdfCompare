#!/usr/bin/env python3
"""Visual diff of two PDF files, displayed in a GUI viewer.

Drop two PDFs on the window (left half = before, right half = after) and the
whole document is shown as one scrollable strip:

  * "wipe" (default): both pages superposed, with a divider you drag left and
    right to wipe between before and after,
  * "overlay": identical pixels transparent, removed ink in red, added ink in
    green, modified ink in yellow, with a faint context radius around each
    change,
  * every changed area is boxed in magenta, in all views.

Pages are compared lazily by a background worker and cached, so scrolling and
jumping between pages does not recompute what is already known.

The interface follows the system language (English or French, English by
default); set PDFCOMPARE_LANG=fr or =en to force it.

Nothing is written to disk unless the save button is used.

Requires: pymupdf, pillow, numpy. Drag and drop needs tkinterdnd2 (the rest of
the viewer works without it).
"""

from __future__ import annotations

import locale
import math
import multiprocessing
import os
import queue
import sys
import threading
import tkinter as tk
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageDraw, ImageTk

try:
    import pymupdf as fitz
except ImportError:  # pymupdf < 1.24 only exposes the legacy name
    import fitz

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # drag and drop is optional
    DND_FILES = TkinterDnD = None


# --------------------------------------------------------------------------- #
# Localisation
# --------------------------------------------------------------------------- #

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Visual comparison of 2 PDFs",
        "pdf_before": "PDF 1 (before)",
        "pdf_after": "PDF 2 (after)",
        "browse": "Browse...",
        "swap": "Swap",
        "pick_before": "Select the reference PDF",
        "pick_after": "Select the PDF to compare",
        "options": "Comparison options",
        "dpi": "dpi",
        "threshold": "threshold",
        "radius": "radius (pt)",
        "min_area": "min area (px)",
        "keep_context": "context (overlay view)",
        "compare": "Compare",
        "continuous": "Continuous scrolling",
        "only_diff": "Changed pages only",
        "view": "view",
        "background": "background",
        "boxes": "boxes",
        "fit": "fit",
        "save": "Save image...",
        "view_slide": "wipe",
        "view_overlay": "overlay",
        "view_avant": "before",
        "view_apres": "after",
        "bg_damier": "checkerboard",
        "bg_noir": "black",
        "bg_blanc": "white",
        "start_hint": "Pick two PDFs, then Compare.",
        "dpi_detected": "Resolution set to {dpi} dpi (native resolution of PDF 1).",
        "scanning": "Scanning...",
        "scanning_page": "Scanning page {done}/{total}...",
        "page_status": ("Page {page}/{total} — {ratio:.3f}% of pixels differ, "
                        "{regions} area(s)"),
        "busy": "   |   computing...",
        "zoom": "zoom {auto}{value:.0f}%",
        "zoom_auto": "auto ",
        "wipe_amount": "   |   wipe {value:.0f}%",
        "legend_slide": ("Drag the handle (or use the arrow keys) to wipe between before "
                         "and after   |   magenta = changed area   |   wheel = scroll, "
                         "Ctrl+wheel = zoom"),
        "legend_overlay": ("red = removed   green = added   yellow = modified   "
                           "magenta = area   transparent = identical"),
        "missing_pdf_title": "Missing PDF",
        "missing_pdf": "Select two PDF files.",
        "not_found_title": "File not found",
        "not_found": "One of the paths does not exist.",
        "error": "Error",
        "failed": "Failed: {error}",
        "render_failed": "render failed: {error}",
        "no_diff_title": "Comparison",
        "no_diff": "No difference over {total} page(s).",
        "nothing_to_save_title": "Nothing to save",
        "nothing_to_save": "Run a comparison first.",
        "save_title": "Save the current page",
        "saved": "Image saved: {path}",
        "swapped": "PDFs swapped.",
        "drop_no_pdf": "Drag and drop: no PDF file in what was dropped.",
        "drop_partial": "{name} loaded. The {slot} PDF is still needed.",
        "slot_after": "AFTER (right)",
        "slot_before": "BEFORE (left)",
        "panel_before": "BEFORE  (PDF 1)",
        "panel_after": "AFTER  (PDF 2)",
        "panel_drop_here": "drag a PDF here",
        "panel_release": "release here",
        "panel_ok": "OK",
        "panel_hint": "Drag and drop the PDFs (left = before, right = after)",
        "panel_hint_nodnd": ("Pick the PDFs with Browse... then Compare "
                             "(drag and drop unavailable: tkinterdnd2 missing)"),
        "page_pending": "page {page} — comparing...",
        "page_short": "p{page}",
        "page_row": "page {page:>3}",
        "note_added": "page missing from PDF 1 (added)",
        "note_removed": "page missing from PDF 2 (removed)",
        "note_size": ("different page size ({w1}x{h1} vs {w2}x{h2}), "
                      "aligned top-left"),
    },
    "fr": {
        "title": "Comparaison visuelle de 2 PDF",
        "pdf_before": "PDF 1 (avant)",
        "pdf_after": "PDF 2 (apres)",
        "browse": "Parcourir...",
        "swap": "Inverser",
        "pick_before": "Selectionner le PDF de reference",
        "pick_after": "Selectionner le PDF a comparer",
        "options": "Options de comparaison",
        "dpi": "dpi",
        "threshold": "seuil",
        "radius": "rayon (pt)",
        "min_area": "aire min (px)",
        "keep_context": "contexte (vue superposition)",
        "compare": "Comparer",
        "continuous": "Defilement continu",
        "only_diff": "Pages differentes seulement",
        "view": "vue",
        "background": "fond",
        "boxes": "cadres",
        "fit": "ajuster",
        "save": "Enregistrer l'image...",
        "view_slide": "fondu",
        "view_overlay": "superposition",
        "view_avant": "avant",
        "view_apres": "apres",
        "bg_damier": "damier",
        "bg_noir": "noir",
        "bg_blanc": "blanc",
        "start_hint": "Choisir deux PDF puis Comparer.",
        "dpi_detected": "Resolution reglee sur {dpi} dpi (resolution native du PDF 1).",
        "scanning": "Analyse...",
        "scanning_page": "Analyse page {done}/{total}...",
        "page_status": ("Page {page}/{total} — {ratio:.3f}% de pixels differents, "
                        "{regions} zone(s)"),
        "busy": "   |   calcul en cours...",
        "zoom": "zoom {auto}{value:.0f}%",
        "zoom_auto": "auto ",
        "wipe_amount": "   |   fondu {value:.0f}%",
        "legend_slide": ("Glisser la poignee (ou les fleches) pour fondre avant/apres   |   "
                         "magenta = zone modifiee   |   molette = defiler, "
                         "Ctrl+molette = zoom"),
        "legend_overlay": ("rouge = supprime   vert = ajoute   jaune = modifie   "
                           "magenta = zone   transparent = identique"),
        "missing_pdf_title": "PDF manquant",
        "missing_pdf": "Selectionner deux fichiers PDF.",
        "not_found_title": "Fichier introuvable",
        "not_found": "Un des chemins n'existe pas.",
        "error": "Erreur",
        "failed": "Echec : {error}",
        "render_failed": "echec du rendu : {error}",
        "no_diff_title": "Comparaison",
        "no_diff": "Aucune difference sur {total} page(s).",
        "nothing_to_save_title": "Rien a enregistrer",
        "nothing_to_save": "Lancer d'abord une comparaison.",
        "save_title": "Enregistrer la page affichee",
        "saved": "Image enregistree : {path}",
        "swapped": "PDF inverses.",
        "drop_no_pdf": "Glisser-deposer : aucun fichier PDF dans ce qui a ete lache.",
        "drop_partial": "{name} charge. Il reste le PDF {slot} a deposer.",
        "slot_after": "APRES (droite)",
        "slot_before": "AVANT (gauche)",
        "panel_before": "AVANT  (PDF 1)",
        "panel_after": "APRES  (PDF 2)",
        "panel_drop_here": "glisser un PDF ici",
        "panel_release": "lacher ici",
        "panel_ok": "OK",
        "panel_hint": "Glisser-deposer les PDF (gauche = avant, droite = apres)",
        "panel_hint_nodnd": ("Choisir les PDF avec Parcourir... puis Comparer "
                             "(glisser-deposer indisponible : tkinterdnd2 absent)"),
        "page_pending": "page {page} — comparaison...",
        "page_short": "p{page}",
        "page_row": "page {page:>3}",
        "note_added": "page absente du PDF 1 (ajoutee)",
        "note_removed": "page absente du PDF 2 (supprimee)",
        "note_size": ("taille de page differente ({w1}x{h1} vs {w2}x{h2}), "
                      "alignement haut-gauche"),
    },
}


def detect_language() -> str:
    """System UI language, English unless it is clearly French."""
    forced = os.environ.get("PDFCOMPARE_LANG", "").strip().lower()[:2]
    if forced in STRINGS:
        return forced
    for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(name, "")
        if value[:2].lower() in STRINGS:
            return value[:2].lower()
    if sys.platform == "win32":
        try:
            import ctypes
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if langid & 0x3FF == 0x0C:  # LANG_FRENCH
                return "fr"
            return "en"
        except Exception:
            pass
    try:
        tag = locale.getlocale()[0] or locale.getdefaultlocale()[0] or ""
    except Exception:
        tag = ""
    return "fr" if tag[:2].lower() == "fr" else "en"


LANG = detect_language()


def T(key: str, **kw) -> str:
    text = STRINGS.get(LANG, STRINGS["en"]).get(key) or STRINGS["en"].get(key, key)
    return text.format(**kw) if kw else text


VIEWS = ("slide", "overlay", "avant", "apres")
BACKGROUNDS = ("damier", "noir", "blanc")
SCAN_DPI = 72               # cheap pass used to flag which pages differ
PAGE_GAP = 14               # px between pages in the scrollable strip
MEM_BUDGET = 400 * 1024**2  # cache ceiling for compared pages
HANDLE_GRAB = 22            # px around the divider that grab it instead of panning


DEFAULT_DPI = 200
DPI_LIMITS = (96, 300)  # a page at 300 dpi already weighs ~120 MB in the cache


def native_dpi(path: Path, pages: int = 3) -> int | None:
    """Effective dpi of the raster content of a PDF, if it has any.

    A scanned page is one big image placed on the page box: comparing it below
    its own resolution throws away detail and invents anti-aliasing differences,
    so the viewer defaults to that resolution. Vector-only PDFs return None.
    """
    try:
        doc = fitz.open(path)
    except Exception:
        return None
    best = 0.0
    try:
        for index in range(min(pages, doc.page_count)):
            page = doc.load_page(index)
            page_area = page.rect.width * page.rect.height
            for info in page.get_image_info():
                x0, y0, x1, y1 = info["bbox"]
                w, h = x1 - x0, y1 - y0
                if w < 20 or h < 20 or page_area <= 0:
                    continue
                if w * h < 0.25 * page_area:  # logos and stamps say nothing
                    continue
                best = max(best, info["width"] * 72.0 / w, info["height"] * 72.0 / h)
    except Exception:
        return None
    finally:
        doc.close()
    return int(round(best)) if best else None


@dataclass
class Options:
    dpi: int = DEFAULT_DPI
    # per-channel difference (0-255) above which a pixel counts as changed
    threshold: int = 20
    # context kept around each change, in points (1/72 inch), scaled to the dpi
    radius_pt: float = 10.0
    # discard changed blobs smaller than this many pixels (noise / anti-aliasing)
    min_area: int = 12
    keep_context: bool = True
    # alpha applied to the faint original content kept around a change
    context_alpha: int = 110


@dataclass
class Region:
    x0: int
    y0: int
    x1: int
    y1: int
    area: int

    def scaled(self, factor: float) -> "Region":
        return Region(int(self.x0 * factor), int(self.y0 * factor),
                      int(self.x1 * factor), int(self.y1 * factor), self.area)


@dataclass
class PageStats:
    index: int  # 0-based
    changed_px: int = 0
    total_px: int = 0
    regions: list[Region] = field(default_factory=list)
    note: str = ""

    @property
    def changed_ratio(self) -> float:
        return self.changed_px / self.total_px if self.total_px else 0.0

    @property
    def differs(self) -> bool:
        return bool(self.changed_px or self.note)


# --------------------------------------------------------------------------- #
# Rendering / mask helpers
# --------------------------------------------------------------------------- #

def render_page(doc, index: int, dpi: int) -> np.ndarray | None:
    """Render page `index` of `doc` as an RGB uint8 array, or None if absent."""
    if doc is None or index >= doc.page_count:
        return None
    pix = doc.load_page(index).get_pixmap(dpi=dpi, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8)
    arr = arr.reshape(pix.height, pix.width, pix.n)
    if pix.n == 1:
        arr = np.repeat(arr, 3, axis=2)
    return np.ascontiguousarray(arr[:, :, :3])


def pad_to(arr: np.ndarray, height: int, width: int, fill: int = 255) -> np.ndarray:
    """Top-left align `arr` in a `height` x `width` canvas filled with `fill`."""
    if arr.shape[0] == height and arr.shape[1] == width:
        return arr
    out = np.full((height, width, 3), fill, dtype=np.uint8)
    h = min(height, arr.shape[0])
    w = min(width, arr.shape[1])
    out[:h, :w] = arr[:h, :w]
    return out


def bbox_of(mask: np.ndarray, margin: int = 0) -> tuple[int, int, int, int] | None:
    rows = np.flatnonzero(mask.any(axis=1))
    if not rows.size:
        return None
    cols = np.flatnonzero(mask.any(axis=0))
    h, w = mask.shape
    return (max(0, int(cols[0]) - margin), max(0, int(rows[0]) - margin),
            min(w, int(cols[-1]) + 1 + margin), min(h, int(rows[-1]) + 1 + margin))


def _shift_or(mask: np.ndarray, k: int, axis: int) -> np.ndarray:
    """mask OR mask shifted by +/-k along `axis` (zero padded)."""
    out = mask.copy()
    if axis == 0:
        out[k:, :] |= mask[:-k, :]
        out[:-k, :] |= mask[k:, :]
    else:
        out[:, k:] |= mask[:, :-k]
        out[:, :-k] |= mask[:, k:]
    return out


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Grow a boolean mask by `radius` pixels (square structuring element).

    Separable, doubling shifts: log2(radius) OR passes per axis instead of the
    O(radius^2) per pixel a MaxFilter would cost, and only over the bounding box
    of the mask, which on a real page is a tiny fraction of it.
    """
    if radius <= 0:
        return mask
    box = bbox_of(mask, radius)
    if box is None:
        return mask
    x0, y0, x1, y1 = box
    sub = mask[y0:y1, x0:x1]
    for axis in (0, 1):
        step, left = 1, radius
        while left > 0:
            k = min(step, left, sub.shape[axis] - 1)
            if k <= 0:
                break
            sub = _shift_or(sub, k, axis)
            left -= k
            step *= 2
    out = np.zeros_like(mask)
    out[y0:y1, x0:x1] = sub
    return out


def _regions_in(mask: np.ndarray, block: int, min_area: int) -> list[Region]:
    """8-connected components of `mask`, labelled on a coarse block grid.

    Working on blocks keeps this fast and pure-numpy (no scipy) while the
    bounding boxes stay accurate to within one block, which is well below the
    context radius already added around each change.
    """
    h, w = mask.shape
    bh = (h + block - 1) // block
    bw = (w + block - 1) // block
    padded = np.zeros((bh * block, bw * block), dtype=bool)
    padded[:h, :w] = mask
    # two contiguous sums: 3x faster than reducing a 4-D view over axes (1, 3)
    rows = padded.view(np.uint8).reshape(bh, block, bw * block).sum(axis=1, dtype=np.uint16)
    counts = rows.reshape(bh, bw, block).sum(axis=2, dtype=np.int32)
    grid = counts > 0

    # Horizontal runs of set cells, found with numpy; only the runs go through
    # Python (union-find), which is orders of magnitude fewer than the cells.
    edges = np.diff(np.pad(grid, ((0, 0), (1, 1))).astype(np.int8), axis=1)
    run_y, run_x0 = np.nonzero(edges == 1)
    _, run_x1 = np.nonzero(edges == -1)  # exclusive; same row-major order as starts
    n = run_y.size
    if not n:
        return []
    csum = np.zeros((bh, bw + 1), dtype=np.int64)
    np.cumsum(counts, axis=1, out=csum[:, 1:])
    run_area = csum[run_y, run_x1] - csum[run_y, run_x0]

    ys, x0s, x1s = run_y.tolist(), run_x0.tolist(), run_x1.tolist()
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    prev_start = prev_end = cur_start = 0
    j = 0
    for i in range(n):
        if i == 0 or ys[i] != ys[i - 1]:
            if i and ys[i] == ys[i - 1] + 1:
                prev_start, prev_end = cur_start, i
            else:
                prev_start = prev_end = i
            cur_start, j = i, prev_start
        # 8-connectivity: runs touch when they overlap or meet at a corner
        while j < prev_end and x1s[j] < x0s[i]:
            j += 1
        k = j
        while k < prev_end and x0s[k] <= x1s[i]:
            ri, rk = find(i), find(k)
            if ri != rk:  # the earliest run stays root: keeps raster order
                parent[max(ri, rk)] = min(ri, rk)
            k += 1

    roots = np.fromiter((find(i) for i in range(n)), dtype=np.int64, count=n)
    _, label = np.unique(roots, return_inverse=True)
    count = int(label.max()) + 1
    bx0 = np.full(count, bw, dtype=np.int64)
    by0 = np.full(count, bh, dtype=np.int64)
    bx1 = np.zeros(count, dtype=np.int64)
    by1 = np.zeros(count, dtype=np.int64)
    area = np.zeros(count, dtype=np.int64)
    np.minimum.at(bx0, label, run_x0)
    np.minimum.at(by0, label, run_y)
    np.maximum.at(bx1, label, run_x1)
    np.maximum.at(by1, label, run_y + 1)
    np.add.at(area, label, run_area)

    regions: list[Region] = []
    for c in range(count):
        if area[c] < min_area:
            continue
        regions.append(Region(x0=int(bx0[c]) * block, y0=int(by0[c]) * block,
                              x1=min(w, int(bx1[c]) * block),
                              y1=min(h, int(by1[c]) * block), area=int(area[c])))
    return regions


def find_regions(mask: np.ndarray, block: int, min_area: int) -> list[Region]:
    box = bbox_of(mask)
    if box is None:
        return []
    x0, y0, x1, y1 = box
    regions = _regions_in(mask[y0:y1, x0:x1], block, min_area)
    for r in regions:
        r.x0 += x0
        r.x1 += x0
        r.y0 += y0
        r.y1 += y0
    regions.sort(key=lambda r: r.area, reverse=True)
    return regions


def luminance(arr: np.ndarray) -> np.ndarray:
    """Luma of RGB pixels, for an image (h, w, 3) or a pixel list (n, 3)."""
    return (
        0.299 * arr[..., 0].astype(np.float32)
        + 0.587 * arr[..., 1].astype(np.float32)
        + 0.114 * arr[..., 2].astype(np.float32)
    )


def abs_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Max per-channel |a - b|, staying in uint8 (no int16 temporaries)."""
    d = np.maximum(a, b)
    d -= np.minimum(a, b)
    # explicit pairwise max: 5x faster than d.max(axis=2) on a 3-channel axis
    return np.maximum(np.maximum(d[:, :, 0], d[:, :, 1]), d[:, :, 2])


# --------------------------------------------------------------------------- #
# Per-page comparison
# --------------------------------------------------------------------------- #

@dataclass
class PageDiff:
    a: np.ndarray          # padded RGB of PDF 1
    b: np.ndarray          # padded RGB of PDF 2
    changed: np.ndarray    # bool mask of real changes
    grown: np.ndarray      # changes + context radius
    stats: PageStats

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.changed.shape
        return (w, h)

    @property
    def nbytes(self) -> int:
        return self.a.nbytes + self.b.nbytes + self.changed.nbytes + self.grown.nbytes


def diff_page(a: np.ndarray | None, b: np.ndarray | None, opt: Options,
              index: int, dpi: int, min_area: int) -> PageDiff:
    note = ""
    if a is None:
        note = T("note_added")
    elif b is None:
        note = T("note_removed")

    height = max(x.shape[0] for x in (a, b) if x is not None)
    width = max(x.shape[1] for x in (a, b) if x is not None)
    if a is not None and b is not None and a.shape != b.shape:
        note = T("note_size", w1=a.shape[1], h1=a.shape[0],
                 w2=b.shape[1], h2=b.shape[0])

    blank = np.full((height, width, 3), 255, np.uint8)
    aa = pad_to(a, height, width) if a is not None else blank
    bb = pad_to(b, height, width) if b is not None else blank

    diff = abs_diff(aa, bb)
    if a is None or b is None:
        # Whole page is new/removed: flag every inked pixel of the page we have.
        present = aa if a is not None else bb
        diff = np.maximum(diff, (255 - luminance(present)).astype(np.uint8))

    changed = diff >= opt.threshold
    radius_px = max(0, int(round(opt.radius_pt * dpi / 72.0)))
    grown = dilate(changed, radius_px) if opt.keep_context else changed.copy()
    block = max(2, radius_px // 2 or 2)
    regions = find_regions(grown, block, min_area)

    # Keep only the changes belonging to a surviving region.
    if regions:
        keep = np.zeros_like(grown)
        for r in regions:
            keep[r.y0:r.y1, r.x0:r.x1] |= grown[r.y0:r.y1, r.x0:r.x1]
        grown = keep
        changed = changed & grown
    else:
        grown = np.zeros_like(grown)
        changed = np.zeros_like(changed)

    stats = PageStats(index=index, changed_px=int(changed.sum()),
                      total_px=height * width, regions=regions, note=note)
    return PageDiff(a=aa, b=bb, changed=changed, grown=grown, stats=stats)


def render_overlay(pd: PageDiff, opt: Options) -> tuple[Image.Image, tuple[int, int]] | None:
    """RGBA overlay of the changed areas, or None if the pages match.

    Only the bounding box of the changes is computed and returned, with its
    top-left corner in page pixels: diffs are usually a tiny fraction of a page,
    and working full-page would cost a hundred times more for nothing.

    Bounding boxes are NOT baked in; the viewer draws them as canvas items so
    they stay a constant thickness whatever the zoom.
    """
    box = bbox_of(pd.grown)
    if box is None:
        return None
    x0, y0, x1, y1 = box

    a = pd.a[y0:y1, x0:x1]
    b = pd.b[y0:y1, x0:x1]
    changed = pd.changed[y0:y1, x0:x1]
    grown = pd.grown[y0:y1, x0:x1]
    out = np.zeros((y1 - y0, x1 - x0, 4), dtype=np.uint8)

    # Colours are computed on the masked pixels only, gathered by flat index
    # and written back as packed uint32: boolean indexing of (h, w, 3) arrays
    # is several times slower, and whole-box luminance planes slower still.
    flat_a = np.ascontiguousarray(a).reshape(-1, 3)
    flat_b = np.ascontiguousarray(b).reshape(-1, 3)
    flat_out = out.view(np.uint32).reshape(-1)

    def rgba(r, g, b_, alpha):
        return (np.asarray(r, np.uint32) | (np.asarray(g, np.uint32) << 8)
                | (np.asarray(b_, np.uint32) << 16) | (np.asarray(alpha, np.uint32) << 24))

    # 1. faint context: what both files have in common inside the grown area.
    if opt.keep_context:
        idx = np.flatnonzero(grown & ~changed)
        if idx.size:
            common = np.minimum(np.take(flat_a, idx, axis=0), np.take(flat_b, idx, axis=0))
            ink = np.clip(255 - luminance(common), 0, 255)
            gray = np.clip(105 + ink * 0.6, 0, 255).astype(np.uint8)  # 0 ink = blank paper
            blue = np.minimum(gray.astype(np.int16) + 30, 255)
            flat_out[idx] = rgba(gray, gray, blue, np.where(ink > 8, opt.context_alpha, 40))

    # 2. the changes themselves.
    idx = np.flatnonzero(changed)
    if idx.size:
        ink_a = 255.0 - luminance(np.take(flat_a, idx, axis=0))
        ink_b = 255.0 - luminance(np.take(flat_b, idx, axis=0))
        # "modified": both files draw something here, only the content differs
        modified = (ink_a > 40) & (ink_b > 40)
        removed = ~modified & (ink_a > ink_b)  # ink in A, blank in B
        px = np.full(idx.size, rgba(60, 235, 90, 235), np.uint32)  # added: ink in B only
        px[removed] = rgba(255, 60, 60, 235)
        px[modified] = rgba(255, 200, 0, 240)
        flat_out[idx] = px

    return Image.fromarray(out, mode="RGBA"), (x0, y0)


def checkerboard(size: tuple[int, int], tile: int = 12) -> Image.Image:
    """Neutral pattern making transparent (= identical) areas obvious."""
    w, h = size
    odd_col = (np.arange(w) // tile) % 2 == 1
    odd_row = (np.arange(h) // tile) % 2 == 1
    grid = np.where(odd_row[:, None] ^ odd_col[None, :], 170, 210).astype(np.uint8)
    return Image.fromarray(grid, mode="L").convert("RGB")


# --------------------------------------------------------------------------- #
# Comparison session: lazy, cached, computed off the UI thread
# --------------------------------------------------------------------------- #

class Session:
    """Owns both documents and serves compared pages.

    A single worker thread (with its own document handles, since a fitz
    Document is not thread safe) computes pages in the order the viewer asks
    for them, then falls back to a prefetch list. Results are picked up by the
    UI thread in `pump()`, so no Tk call ever happens off the main thread.
    """

    def __init__(self, pdf_a: Path, pdf_b: Path, opt: Options):
        self.pdf_a = pdf_a
        self.pdf_b = pdf_b
        self.opt = opt
        self.doc_a = fitz.open(pdf_a)
        self.doc_b = fitz.open(pdf_b)
        self.page_count = max(self.doc_a.page_count, self.doc_b.page_count)
        self.geometry = self._page_geometry()
        self.geom_version = 0
        self.scan: list[PageStats] = []

        self._ready: OrderedDict[int, PageDiff] = OrderedDict()
        self._ready_bytes = 0
        self._full = False  # cache at budget: stop prefetching, it would only evict
        self._protected: set[int] = set()
        self._wanted: list[int] = []
        self._prefetch: list[int] = []
        self._inflight: int | None = None
        self._out: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._work, name="pdfdiff", daemon=True)
        self._worker.start()

    # -- geometry --------------------------------------------------------- #

    def _page_geometry(self) -> list[tuple[int, int]]:
        """Page sizes in render pixels, from the page boxes (no rasterizing)."""
        scale = self.opt.dpi / 72.0
        out = []
        for i in range(self.page_count):
            widths, heights = [], []
            for doc in (self.doc_a, self.doc_b):
                if i < doc.page_count:
                    rect = doc.load_page(i).rect
                    widths.append(rect.width)
                    heights.append(rect.height)
            out.append((max(1, int(round(max(widths) * scale))),
                        max(1, int(round(max(heights) * scale)))))
        return out

    # -- worker ----------------------------------------------------------- #

    def _next_index(self) -> int | None:
        with self._lock:
            pools = (self._wanted,) if self._full else (self._wanted, self._prefetch)
            for pool in pools:
                while pool:
                    idx = pool.pop(0)
                    if idx not in self._ready:
                        self._inflight = idx
                        return idx
            self._inflight = None
            return None

    def _work(self):
        doc_a = fitz.open(self.pdf_a)
        doc_b = fitz.open(self.pdf_b)
        try:
            while not self._stop.is_set():
                idx = self._next_index()
                if idx is None:
                    self._wake.wait(0.25)
                    self._wake.clear()
                    continue
                try:
                    pd = self.compute(idx, doc_a, doc_b)
                except Exception as exc:  # a broken page must not kill the worker
                    self._out.put((idx, None, str(exc)))
                else:
                    self._out.put((idx, pd, None))
                with self._lock:
                    self._inflight = None
        finally:
            for doc in (doc_a, doc_b):
                try:
                    doc.close()
                except Exception:
                    pass

    def compute(self, index: int, doc_a=None, doc_b=None) -> PageDiff:
        """Compare one page. Pass the worker's handles when off the UI thread."""
        doc_a = doc_a if doc_a is not None else self.doc_a
        doc_b = doc_b if doc_b is not None else self.doc_b
        return diff_page(
            render_page(doc_a, index, self.opt.dpi),
            render_page(doc_b, index, self.opt.dpi),
            self.opt, index, self.opt.dpi, self.opt.min_area,
        )

    def close(self):
        self._stop.set()
        self._wake.set()
        self._ready.clear()
        self._ready_bytes = 0
        for doc in (self.doc_a, self.doc_b):
            try:
                doc.close()
            except Exception:
                pass

    # -- scheduling ------------------------------------------------------- #

    def focus(self, indices: list[int]):
        """Declare what the viewer needs now; drops stale requests."""
        with self._lock:
            self._wanted = [i for i in indices
                            if i not in self._ready and i != self._inflight]
        self._wake.set()

    def set_prefetch(self, indices: list[int]):
        with self._lock:
            self._prefetch = list(indices)
        self._wake.set()

    def pending(self) -> bool:
        with self._lock:
            prefetching = bool(self._prefetch) and not self._full
            return bool(self._wanted or prefetching or self._inflight is not None)

    def protect(self, indices: list[int]):
        """Pages the viewer shows or asked for: never evict those to make room.

        Evicting a requested neighbour would make the viewer ask for it again
        on the next repaint, and the worker would recompute pages forever.
        """
        self._protected = set(indices)

    def get(self, index: int) -> PageDiff | None:
        pd = self._ready.get(index)
        if pd is not None:
            self._ready.move_to_end(index)
        return pd

    def get_or_compute(self, index: int) -> PageDiff:
        """Blocking variant, for the rare paths that cannot show a placeholder."""
        pd = self.get(index)
        if pd is None:
            pd = self.compute(index)
            self._store(index, pd)
        return pd

    def pump(self) -> list[int]:
        """Move finished pages into the cache. Returns the indices that landed."""
        landed = []
        while True:
            try:
                index, pd, error = self._out.get_nowait()
            except queue.Empty:
                break
            if pd is None:
                self.scan[index].note = T("render_failed", error=error)
                continue
            self._store(index, pd)
            landed.append(index)
        return landed

    def _store(self, index: int, pd: PageDiff):
        if pd.size != self.geometry[index]:  # rasterizer rounding, fix the layout
            self.geometry[index] = pd.size
            self.geom_version += 1
        old = self._ready.pop(index, None)
        if old is not None:
            self._ready_bytes -= old.nbytes
        self._ready[index] = pd
        self._ready_bytes += pd.nbytes
        # budget in real bytes: page sizes vary a lot inside one document
        for key in [k for k in self._ready if k not in self._protected]:
            if self._ready_bytes <= MEM_BUDGET:
                break
            self._ready_bytes -= self._ready.pop(key).nbytes
        self._full = self._ready_bytes > MEM_BUDGET * 0.9

    # -- low-res pass ----------------------------------------------------- #

    def run_scan(self, progress=None) -> list[PageStats]:
        """Low-res pass over every page, to know which ones differ.

        Rasterizing holds the GIL, so threads do not help: long documents are
        spread over worker processes instead (about 4x faster on 8 cores).
        """
        n = self.page_count
        self.scan = []
        workers = min(SCAN_PROCESSES, os.cpu_count() or 1, n // SCAN_PAGES_PER_PROCESS)
        results = None
        if workers >= 2:
            try:
                with ProcessPoolExecutor(workers, initializer=_scan_init,
                                         initargs=(str(self.pdf_a), str(self.pdf_b),
                                                   self.opt)) as pool:
                    results = pool.map(_scan_page, range(n), chunksize=2)
                    for i, stats in enumerate(results):
                        if progress:
                            progress(i, n)
                        self.scan.append(stats)
            except Exception:  # broken pool (frozen build quirks, memory): go serial
                self.scan = []
                results = None
        if results is None:
            _scan_init(None, None, self.opt, docs=(self.doc_a, self.doc_b))
            for i in range(n):
                if progress:
                    progress(i, n)
                self.scan.append(_scan_page(i))
        if progress:
            progress(n, n)
        return self.scan


SCAN_PROCESSES = 8          # more brings little: process start-up dominates
SCAN_PAGES_PER_PROCESS = 6  # below that, spawning costs more than it saves
_scan_state: dict = {}


def _scan_init(pdf_a, pdf_b, opt: Options, docs=None):
    """Per-process setup for the scan: open both documents once."""
    if docs is None:
        docs = (fitz.open(pdf_a), fitz.open(pdf_b))
    _scan_state.update(docs=docs, opt=opt)


def _scan_page(index: int) -> PageStats:
    doc_a, doc_b = _scan_state["docs"]
    opt = _scan_state["opt"]
    scale = (SCAN_DPI / opt.dpi) ** 2
    min_area = max(2, int(round(opt.min_area * scale)))
    return diff_page(render_page(doc_a, index, SCAN_DPI),
                     render_page(doc_b, index, SCAN_DPI),
                     opt, index, SCAN_DPI, min_area).stats


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #

ZOOM_STEPS = (0.1, 0.15, 0.25, 0.33, 0.5, 0.66, 0.8, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0)
BOX_COLOR = "#ff00dc"
CANVAS_BG = (58, 58, 58)
# Display caches are bounded in bytes, not entries: a continuous strip of small
# pages needs dozens of images on screen at once, a zoomed big page only two.
IMAGE_CACHE_BYTES = 160 * 1024**2    # page images (full size or pre-scaled)
SLICE_CACHE_BYTES = 64 * 1024**2     # scaled visible slices
OVERLAY_CACHE_BYTES = 128 * 1024**2  # flattened overlays, per background


def _lru_put(cache: OrderedDict, key, img: Image.Image, limit: int):
    """Insert as most recent, then evict the oldest entries beyond `limit` bytes."""
    cache[key] = img
    cache.move_to_end(key)
    total = sum(v.width * v.height * len(v.getbands()) for v in cache.values())
    while total > limit and len(cache) > 1:
        _, old = cache.popitem(last=False)
        total -= old.width * old.height * len(old.getbands())
PRESCALE_BELOW = 0.5   # zoom under which whole pages are pre-scaled and cached
QUALITY_DELAY = 110    # ms of calm before repainting with the good filter


class App(ttk.Frame):
    """Viewer.

    Painting is viewport-based: every repaint crops only what is on screen from
    the pages it overlaps, so cost is independent of the zoom level and of the
    document length. Pages not compared yet paint as a placeholder carrying the
    boxes found by the low-res scan, and are filled in when the worker delivers.

    While the user is scrolling, panning, zooming or wiping, frames are scaled
    with NEAREST (a few ms); a full quality repaint follows once the interaction
    stops.
    """

    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=8)
        self.master = master
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)

        self.session: Session | None = None
        self.current = 0
        self.zoom = 1.0
        self.ox = 0.0          # scroll offsets, in zoomed px
        self.oy = 0.0
        self.splits: dict[int, float] = {}  # divider per page, fraction of its width
        self._drag_page: int | None = None  # page whose divider is being dragged
        self._dragging_handle = False
        self._pan_from: tuple[float, float] | None = None
        self._drag_side: str | None = None  # half hovered by a file drag
        self._redraw_pending = False
        self._fast_paint = False
        self._quality_job: str | None = None
        self._frame: Image.Image | None = None
        self._frame_photo: ImageTk.PhotoImage | None = None
        self._frame_item: int | None = None
        self._images: OrderedDict[tuple, Image.Image] = OrderedDict()
        self._slices: OrderedDict[tuple, Image.Image] = OrderedDict()
        self._overlays: OrderedDict[tuple, Image.Image] = OrderedDict()
        self._layout_cache: tuple | None = None
        self._page_rects: list[tuple] = []  # visible pages: index, x, w, top, bottom (canvas px)
        self._list_index: list[int] = []

        self.path_a = tk.StringVar()
        self.path_b = tk.StringVar()
        self.dpi = tk.IntVar(value=DEFAULT_DPI)
        self.threshold = tk.IntVar(value=20)
        self.radius = tk.DoubleVar(value=10.0)
        self.min_area = tk.IntVar(value=12)
        self.view = tk.StringVar(value="slide")
        self.background = tk.StringVar(value="damier")
        self.boxes = tk.BooleanVar(value=True)
        self.context = tk.BooleanVar(value=True)
        self.fit = tk.BooleanVar(value=True)
        self.continuous = tk.BooleanVar(value=True)
        self.only_diff = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value=T("start_hint"))

        self._build_files()
        self._build_options()
        self._build_viewer()
        self._build_statusbar()
        self._bind_keys()
        self._setup_dnd()
        self.path_a.trace_add("write", lambda *_: self._on_before_changed())
        self.path_b.trace_add("write", lambda *_: self._schedule_redraw())
        self._schedule_redraw()
        self.after(50, self._tick)

    # -- construction ----------------------------------------------------- #

    def _build_files(self):
        box = ttk.Frame(self)
        box.grid(row=0, column=0, columnspan=2, sticky="ew")
        box.columnconfigure(1, weight=1)
        self.entries: dict[str, tk.Entry] = {}
        for row, (slot, label, var, title) in enumerate((
            ("a", T("pdf_before"), self.path_a, T("pick_before")),
            ("b", T("pdf_after"), self.path_b, T("pick_after")),
        )):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", pady=2)
            # tk.Entry rather than ttk: its background can be flashed on drop-over
            entry = tk.Entry(box, textvariable=var, relief="solid", borderwidth=1)
            entry.grid(row=row, column=1, sticky="ew", padx=6, ipady=2)
            self.entries[slot] = entry
            ttk.Button(box, text=T("browse"),
                       command=lambda v=var, t=title: self._pick(v, t)).grid(row=row, column=2)
        ttk.Button(box, text=T("swap"), width=10, command=self.on_swap).grid(
            row=0, column=3, rowspan=2, sticky="ns", padx=(6, 0))

    def _build_options(self):
        opts = ttk.LabelFrame(self, text=T("options"), padding=8)
        opts.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 6))

        def spin(label, var, lo, hi, inc, col):
            ttk.Label(opts, text=label).grid(row=0, column=col, sticky="w", padx=(0, 4))
            ttk.Spinbox(opts, from_=lo, to=hi, increment=inc, textvariable=var,
                        width=6).grid(row=0, column=col + 1, sticky="w", padx=(0, 14))

        spin(T("dpi"), self.dpi, 50, 600, 25, 0)
        spin(T("threshold"), self.threshold, 0, 255, 5, 2)
        spin(T("radius"), self.radius, 0, 100, 2, 4)
        spin(T("min_area"), self.min_area, 0, 5000, 4, 6)
        ttk.Checkbutton(opts, text=T("keep_context"),
                        variable=self.context).grid(row=0, column=8, sticky="w", padx=(0, 14))
        self.run_btn = ttk.Button(opts, text=T("compare"), command=self.on_compare)
        self.run_btn.grid(row=0, column=9, sticky="e")
        opts.columnconfigure(9, weight=1)

    def _labelled_combo(self, parent, keys, prefix, var, column):
        """Combobox showing translated labels while `var` keeps the internal key."""
        labels = {key: T(f"{prefix}{key}") for key in keys}
        display = tk.StringVar(value=labels[var.get()])
        combo = ttk.Combobox(parent, textvariable=display, values=list(labels.values()),
                             state="readonly", width=max(8, max(map(len, labels.values()))))
        combo.grid(row=0, column=column, padx=(4, 12))
        reverse = {text: key for key, text in labels.items()}
        display.trace_add("write", lambda *_: var.set(reverse.get(display.get(), var.get())))
        return combo

    def _build_viewer(self):
        left = ttk.Frame(self)
        left.grid(row=2, column=0, sticky="ns", padx=(0, 8))
        left.rowconfigure(2, weight=1)
        ttk.Checkbutton(left, text=T("continuous"), variable=self.continuous,
                        command=self.on_layout_change).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(left, text=T("only_diff"), variable=self.only_diff,
                        command=self.on_layout_change).grid(row=1, column=0, sticky="w")
        self.page_list = tk.Listbox(left, width=26, exportselection=False, activestyle="none")
        self.page_list.grid(row=2, column=0, sticky="ns")
        self.page_list.bind("<<ListboxSelect>>", self._on_list_select)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.page_list.yview)
        sb.grid(row=2, column=1, sticky="ns")
        self.page_list.configure(yscrollcommand=sb.set)

        right = ttk.Frame(self)
        right.grid(row=2, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        bar = ttk.Frame(right)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(bar, text="<", width=3, command=lambda: self.step(-1)).grid(row=0, column=0)
        ttk.Button(bar, text=">", width=3, command=lambda: self.step(1)).grid(row=0, column=1, padx=(2, 10))
        ttk.Label(bar, text=T("view")).grid(row=0, column=2)
        self._labelled_combo(bar, VIEWS, "view_", self.view, 3)
        ttk.Label(bar, text=T("background")).grid(row=0, column=4)
        self._labelled_combo(bar, BACKGROUNDS, "bg_", self.background, 5)
        ttk.Checkbutton(bar, text=T("boxes"), variable=self.boxes,
                        command=self._schedule_redraw).grid(row=0, column=6, padx=(0, 8))
        ttk.Checkbutton(bar, text=T("fit"), variable=self.fit,
                        command=self.reset_view).grid(row=0, column=7, padx=(0, 8))
        ttk.Button(bar, text="-", width=3, command=lambda: self.bump_zoom(-1)).grid(row=0, column=8)
        ttk.Button(bar, text="1:1", width=4, command=lambda: self.set_zoom(1.0)).grid(row=0, column=9, padx=2)
        ttk.Button(bar, text="+", width=3, command=lambda: self.bump_zoom(1)).grid(row=0, column=10)
        ttk.Button(bar, text=T("save"), command=self.on_save).grid(row=0, column=12, sticky="e")
        bar.columnconfigure(11, weight=1)
        self.view.trace_add("write", lambda *_: self.on_view_change())
        self.background.trace_add("write", lambda *_: self._schedule_redraw())

        wrap = ttk.Frame(right, relief="sunken", borderwidth=1)
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(wrap, background="#3a3a3a", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb = ttk.Scrollbar(wrap, orient="vertical", command=self._yview)
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.hsb = ttk.Scrollbar(wrap, orient="horizontal", command=self._xview)
        self.hsb.grid(row=1, column=0, sticky="ew")

        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Shift-MouseWheel>", self._on_shift_wheel)
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_wheel)
        self.canvas.bind("<Configure>", lambda e: self._schedule_redraw())
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<Double-Button-1>", lambda e: self.reset_view())

    def _build_statusbar(self):
        bottom = ttk.Frame(self)
        bottom.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status, anchor="w").grid(row=0, column=0, sticky="ew")
        self.bar = ttk.Progressbar(bottom, mode="determinate", length=180)
        self.bar.grid(row=0, column=1, sticky="e")
        self.legend = tk.StringVar()
        ttk.Label(bottom, textvariable=self.legend, anchor="w", foreground="#666").grid(
            row=1, column=0, columnspan=2, sticky="ew")
        self._update_legend()

    def _bind_keys(self):
        self.master.bind("<Prior>", lambda e: self.step(-1))
        self.master.bind("<Next>", lambda e: self.step(1))
        self.master.bind("<Left>", lambda e: self._on_arrow(-1))
        self.master.bind("<Right>", lambda e: self._on_arrow(1))
        self.master.bind("<Home>", lambda e: self._go_home())

    def _update_legend(self):
        self.legend.set(T("legend_slide" if self.view.get() == "slide" else "legend_overlay"))

    # -- drag and drop ---------------------------------------------------- #

    def _setup_dnd(self):
        """Accept PDFs dropped from the explorer, on the window or on a field.

        While a drag hovers the view, it splits in two: dropping on the left half
        loads the "before" PDF, on the right half the "after" one.
        """
        self.dnd_ok = False
        if TkinterDnD is None or not hasattr(self.master, "drop_target_register"):
            return
        try:
            for slot, entry in self.entries.items():
                entry.drop_target_register(DND_FILES)
                entry.dnd_bind("<<Drop>>", lambda e, s=slot: self._on_drop(e, s))
                entry.dnd_bind("<<DropEnter>>", lambda e, w=entry: self._highlight(w, True))
                entry.dnd_bind("<<DropLeave>>", lambda e, w=entry: self._highlight(w, False))
            for widget in (self.master, self.canvas):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
                widget.dnd_bind("<<DropEnter>>", self._on_drag_over)
                widget.dnd_bind("<<DropPosition>>", self._on_drag_over)
                widget.dnd_bind("<<DropLeave>>", self._on_drag_leave)
        except tk.TclError:  # tkdnd shared library missing
            return
        self.dnd_ok = True

    def _highlight(self, widget, on: bool):
        widget.configure(background="#fff8c0" if on else "white")

    def _side_from_event(self, event) -> str:
        """Which half of the view the pointer is over: 'a' (left) or 'b' (right)."""
        try:
            rel_x = event.x_root - self.canvas.winfo_rootx()
        except (AttributeError, tk.TclError):
            return "a"
        return "a" if rel_x < max(1, self.canvas.winfo_width()) / 2 else "b"

    def _on_drag_over(self, event):
        side = self._side_from_event(event)
        if side != self._drag_side:
            self._drag_side = side
            self._schedule_redraw()
        return event.action

    def _on_drag_leave(self, _event):
        if self._drag_side:
            self._drag_side = None
            self._schedule_redraw()

    def _on_drop(self, event, slot: str | None = None):
        """`slot` is forced when dropping on a field; else the half decides."""
        self._drag_side = None
        for entry in self.entries.values():
            self._highlight(entry, False)
        try:
            dropped = [Path(p) for p in self.tk.splitlist(event.data)]
        except tk.TclError:
            dropped = []
        pdfs = [p for p in dropped if p.suffix.lower() == ".pdf" and p.is_file()]
        if not pdfs:
            # a single unquoted path containing spaces got split into words
            single = Path(str(event.data).strip("{}"))
            if single.suffix.lower() == ".pdf" and single.is_file():
                pdfs = [single]
        if not pdfs:
            self._schedule_redraw()
            self.status.set(T("drop_no_pdf"))
            return

        target = slot or self._side_from_event(event)
        if len(pdfs) >= 2:
            # the file list keeps the explorer order; the side you dropped on
            # gets the first one
            self.path_a.set(str(pdfs[0]) if target == "a" else str(pdfs[1]))
            self.path_b.set(str(pdfs[1]) if target == "a" else str(pdfs[0]))
        else:
            (self.path_a if target == "a" else self.path_b).set(str(pdfs[0]))

        if self.path_a.get().strip() and self.path_b.get().strip():
            self.on_compare()
        else:
            missing = T("slot_after") if target == "a" else T("slot_before")
            self.status.set(T("drop_partial", name=Path(pdfs[0]).name, slot=missing))
            self._schedule_redraw()

    def on_swap(self):
        """Exchange the two PDFs (and redo the comparison if there is one)."""
        a, b = self.path_a.get(), self.path_b.get()
        self.path_a.set(b)
        self.path_b.set(a)
        if not (a.strip() and b.strip()):
            self.status.set(T("swapped"))
            self._schedule_redraw()
            return
        keep = (self.current, self.zoom, self.oy, self.fit.get())
        self.on_compare()
        if self.session:
            self.current, self.zoom, self.oy, fit = keep
            self.current = min(self.current, self.session.page_count - 1)
            self.fit.set(fit)
            self._layout_cache = None
            self._sync_list_selection()
            self._schedule_redraw()

    # -- files / comparison ----------------------------------------------- #

    def _pick(self, var: tk.StringVar, title: str):
        path = filedialog.askopenfilename(
            title=title, filetypes=[("PDF", "*.pdf"), ("*", "*.*")]
        )
        if path:
            var.set(path)

    def _on_before_changed(self):
        """Adopt the native resolution of the 'before' PDF when it has one."""
        self._schedule_redraw()
        path = self.path_a.get().strip()
        if not path or not Path(path).is_file():
            return
        found = native_dpi(Path(path))
        if not found:
            return
        value = max(DPI_LIMITS[0], min(DPI_LIMITS[1], found))
        if value != self.dpi.get():
            self.dpi.set(value)
            self.status.set(T("dpi_detected", dpi=value))

    def current_options(self) -> Options:
        return Options(
            dpi=self.dpi.get(), threshold=self.threshold.get(), radius_pt=self.radius.get(),
            min_area=self.min_area.get(), keep_context=self.context.get(),
        )

    def on_compare(self):
        a, b = self.path_a.get().strip(), self.path_b.get().strip()
        if not a or not b:
            messagebox.showwarning(T("missing_pdf_title"), T("missing_pdf"))
            return
        if not Path(a).is_file() or not Path(b).is_file():
            messagebox.showerror(T("not_found_title"), T("not_found"))
            return

        if self.session:
            self.session.close()
            self.session = None
        self._invalidate_caches()
        self.run_btn.state(["disabled"])
        self.status.set(T("scanning"))
        self.update_idletasks()

        def progress(done, total):
            self.bar.configure(maximum=total, value=done)
            self.status.set(T("scanning_page", done=min(done + 1, total), total=total))
            self.update_idletasks()

        try:
            session = Session(Path(a), Path(b), self.current_options())
            session.run_scan(progress)
        except Exception as exc:
            self.run_btn.state(["!disabled"])
            self.status.set(T("failed", error=exc))
            messagebox.showerror(T("error"), str(exc))
            return

        self.session = session
        self.splits = {}
        self.run_btn.state(["!disabled"])
        self.bar.configure(value=0)
        self._fill_page_list()
        diff_pages = [s.index for s in session.scan if s.differs]
        # warm the cache with the interesting pages first, then the rest
        session.set_prefetch(diff_pages + [i for i in range(session.page_count)
                                           if i not in set(diff_pages)])
        self.current = diff_pages[0] if diff_pages else 0
        self.fit.set(True)
        self.reset_view()
        self.scroll_to_page(self.current)
        if not diff_pages:
            messagebox.showinfo(T("no_diff_title"), T("no_diff", total=session.page_count))

    def _invalidate_caches(self):
        self._images.clear()
        self._slices.clear()
        self._overlays.clear()
        self._layout_cache = None

    def _fill_page_list(self):
        self.page_list.delete(0, tk.END)
        self._list_index = []
        if not self.session:
            return
        for st in self.session.scan:
            if self.only_diff.get() and not st.differs:
                continue
            mark = "*" if st.differs else " "
            label = f"{mark} {T('page_row', page=st.index + 1)}"
            if st.differs:
                label += f"  {st.changed_ratio * 100:.2f}%"
            self.page_list.insert(tk.END, label)
            self._list_index.append(st.index)
        self._sync_list_selection()

    def _sync_list_selection(self):
        if self.current in self._list_index:
            row = self._list_index.index(self.current)
            if self.page_list.curselection() != (row,):
                self.page_list.selection_clear(0, tk.END)
                self.page_list.selection_set(row)
                self.page_list.see(row)

    def _on_list_select(self, _event):
        sel = self.page_list.curselection()
        if sel and self._list_index:
            self.current = self._list_index[sel[0]]
            self.scroll_to_page(self.current)

    def on_layout_change(self):
        self._fill_page_list()
        self._layout_cache = None
        if self.session:
            self.reset_view()
            self.scroll_to_page(self.current)
        else:
            self._schedule_redraw()

    def _go_home(self):
        pages = self._layout_pages()
        if pages:
            self.scroll_to_page(pages[0])

    def step(self, delta: int):
        pages = self._layout_pages()
        if not pages:
            return
        idx = pages.index(self.current) + delta if self.current in pages else 0
        if 0 <= idx < len(pages):
            self.current = pages[idx]
            self.scroll_to_page(self.current)

    # -- worker pump ------------------------------------------------------ #

    def _tick(self):
        """Pick up finished pages and repaint when something landed."""
        if self.session:
            landed = self.session.pump()
            if landed:
                for index in landed:
                    self._drop_page_caches(index)
                self._schedule_redraw()
        self.after(50, self._tick)

    def _drop_page_caches(self, index: int):
        for cache in (self._images, self._slices, self._overlays):
            for key in [k for k in cache if k[0] == index]:
                cache.pop(key, None)

    # -- layout ----------------------------------------------------------- #

    def _layout_pages(self) -> list[int]:
        if not self.session:
            return []
        if not self.continuous.get():
            return [self.current]
        return self._list_index or list(range(self.session.page_count))

    def _layout(self):
        """(bands, max_width, total_height) in zoomed px; cached per zoom."""
        pages = self._layout_pages()
        key = (self.zoom, tuple(pages), self.session.geom_version if self.session else 0)
        if self._layout_cache and self._layout_cache[0] == key:
            return self._layout_cache[1]
        bands = []
        y = 0.0
        max_w = 1.0
        for index in pages:
            pw, ph = self.session.geometry[index]
            pw, ph = pw * self.zoom, ph * self.zoom
            bands.append((index, y, pw, ph))
            max_w = max(max_w, pw)
            y += ph + PAGE_GAP
        total_h = max(1.0, y - PAGE_GAP if bands else 1.0)
        result = (bands, max_w, total_h)
        self._layout_cache = (key, result)
        return result

    def _viewport(self) -> tuple[int, int]:
        return (max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height()))

    def _visible_bands(self):
        bands, _, _ = self._layout()
        _, vh = self._viewport()
        top, bottom = self.oy, self.oy + vh
        return [band for band in bands if band[1] < bottom and band[1] + band[3] > top]

    def reset_view(self):
        self.ox = self.oy = 0.0
        self._layout_cache = None
        if self.fit.get() and self.session:
            self.reset_view_zoom_only()
        self._schedule_redraw()

    def reset_view_zoom_only(self):
        """Recompute the fit zoom without touching the scroll position."""
        vw, vh = self._viewport()
        pages = self._layout_pages()
        if not pages:
            return
        if self.continuous.get():
            widest = max(self.session.geometry[i][0] for i in pages)
            zoom = max(0.02, (vw - 8) / widest)
        else:
            w, h = self.session.geometry[self.current]
            zoom = max(0.02, min(vw / w, vh / h))
        if abs(zoom - self.zoom) > 1e-6:
            self.zoom = zoom
            self._layout_cache = None

    def scroll_to_page(self, index: int):
        if not self.session:
            return
        self.current = index
        if not self.continuous.get():
            self._layout_cache = None
            self.oy = 0.0
        else:
            for band_index, y, _, _ in self._layout()[0]:
                if band_index == index:
                    self.oy = y
                    break
        self._sync_list_selection()
        self._schedule_redraw()

    def set_zoom(self, value: float, anchor: tuple[float, float] | None = None):
        self.fit.set(False)
        vw, vh = self._viewport()
        ax, ay = anchor if anchor else (vw / 2, vh / 2)
        px = (self.ox + ax) / self.zoom
        py = (self.oy + ay) / self.zoom  # keep the point under the anchor fixed
        self.zoom = max(0.02, min(12.0, value))
        self._layout_cache = None
        self.ox = px * self.zoom - ax
        self.oy = py * self.zoom - ay
        self._schedule_redraw(fast=True)

    def bump_zoom(self, direction: int, anchor=None):
        steps = list(ZOOM_STEPS)
        closest = min(range(len(steps)), key=lambda i: abs(steps[i] - self.zoom))
        if steps[closest] == self.zoom or direction > 0:
            target = closest + direction
        else:  # coming from a fit zoom sitting between two steps
            target = closest if steps[closest] < self.zoom else closest - 1
        self.set_zoom(steps[max(0, min(len(steps) - 1, target))], anchor)

    def on_view_change(self):
        self._update_legend()
        self._schedule_redraw()

    # -- scrolling -------------------------------------------------------- #

    def _clamp(self):
        vw, vh = self._viewport()
        _, max_w, total_h = self._layout()
        self.ox = max(0.0, min(self.ox, max(0.0, max_w - vw)))
        self.oy = max(0.0, min(self.oy, max(0.0, total_h - vh)))

    def _xview(self, *args):
        _, max_w, _ = self._layout()
        vw, _ = self._viewport()
        if args[0] == "moveto":
            self.ox = float(args[1]) * max_w
        elif args[0] == "scroll":
            self.ox += int(args[1]) * vw * (0.9 if args[2] == "pages" else 0.08)
        self._schedule_redraw(fast=True)

    def _yview(self, *args):
        _, _, total_h = self._layout()
        _, vh = self._viewport()
        if args[0] == "moveto":
            self.oy = float(args[1]) * total_h
        elif args[0] == "scroll":
            self.oy += int(args[1]) * vh * (0.9 if args[2] == "pages" else 0.08)
        self._schedule_redraw(fast=True)

    def _sync_scrollbars(self):
        vw, vh = self._viewport()
        _, max_w, total_h = self._layout()
        self.hsb.set(self.ox / max_w, min(1.0, (self.ox + vw) / max_w))
        self.vsb.set(self.oy / total_h, min(1.0, (self.oy + vh) / total_h))

    def _on_wheel(self, event):
        _, vh = self._viewport()
        self.oy -= (event.delta / 120.0) * vh * 0.15
        self._schedule_redraw(fast=True)

    def _on_shift_wheel(self, event):
        vw, _ = self._viewport()
        self.ox -= (event.delta / 120.0) * vw * 0.12
        self._schedule_redraw(fast=True)

    def _on_ctrl_wheel(self, event):
        self.bump_zoom(1 if event.delta > 0 else -1, anchor=(event.x, event.y))
        return "break"

    def _on_arrow(self, direction: int):
        if self.view.get() == "slide" and self.session:
            self.splits[self.current] = max(0.0, min(1.0, self.split(self.current) + direction * 0.02))
            self._schedule_redraw(fast=True)
        else:
            self.step(direction)

    # -- mouse: divider vs pan -------------------------------------------- #

    def split(self, index: int) -> float:
        return self.splits.get(index, 0.5)

    def _handle_page(self, x: float, y: float) -> int | None:
        """Page whose divider lies under the pointer, if any."""
        if self.view.get() != "slide" or not self.session:
            return None
        for index, page_x, pw, top, bottom in self._page_rects:
            if top <= y <= bottom and abs(x - (page_x + self.split(index) * pw)) <= HANDLE_GRAB:
                return index
        return None

    def _on_press(self, event):
        page = self._handle_page(event.x, event.y)
        if page is not None:
            self._dragging_handle = True
            self._drag_page = page
            self._set_split_from_x(event.x)
        else:
            self._pan_from = (event.x + self.ox, event.y + self.oy)

    def _on_motion(self, event):
        if self._dragging_handle:
            self._set_split_from_x(event.x)
        elif self._pan_from:
            self.ox = self._pan_from[0] - event.x
            self.oy = self._pan_from[1] - event.y
            self._schedule_redraw(fast=True)

    def _on_release(self, _event):
        self._dragging_handle = False
        self._drag_page = None
        self._pan_from = None

    def _on_hover(self, event):
        near = self._handle_page(event.x, event.y) is not None
        self.canvas.configure(cursor="sb_h_double_arrow" if near else "")

    def _set_split_from_x(self, canvas_x: float):
        rect = next((r for r in self._page_rects if r[0] == self._drag_page), None)
        if rect is None or rect[2] <= 0:
            return
        _, page_x, pw, _, _ = rect
        self.splits[self._drag_page] = max(0.0, min(1.0, (canvas_x - page_x) / pw))
        self._schedule_redraw(fast=True)

    # -- page sources ----------------------------------------------------- #

    def _overlay_source(self, index: int, pd: PageDiff) -> Image.Image:
        """Overlay flattened on the chosen background, cached per page + bg."""
        key = (index, self.background.get())
        img = self._overlays.get(key)
        if img is None:
            if self.background.get() == "damier":
                img = checkerboard(pd.size)
            else:
                fill = (0, 0, 0) if self.background.get() == "noir" else (255, 255, 255)
                img = Image.new("RGB", pd.size, fill)
            rendered = render_overlay(pd, self.session.opt)
            if rendered:
                overlay, origin = rendered
                # RGBA pasted with its own alpha as mask: blends straight onto the
                # RGB base, no full-page RGBA round trip
                img.paste(overlay, origin, overlay)
            _lru_put(self._overlays, key, img, OVERLAY_CACHE_BYTES)
        else:
            self._overlays.move_to_end(key)
        return img

    def _base_image(self, index: int, pd: PageDiff, key: str) -> Image.Image:
        if key == "a":
            return Image.fromarray(pd.a, mode="RGB")  # shares the array, no copy
        if key == "b":
            return Image.fromarray(pd.b, mode="RGB")
        return self._overlay_source(index, pd)

    def _page_image(self, index: int, pd: PageDiff, key: str,
                    display: tuple[int, int] | None) -> Image.Image:
        """Whole page as a PIL image, cached.

        Keeping PIL images (rather than arrays) lets a repaint be crop + resize
        straight from C, with no numpy copy in between. `display` asks for a copy
        pre-scaled to the on-screen size, worth it in the thumbnail regime where
        it is reused across repaints.
        """
        if display is None:  # full size: a view on the arrays or the overlay cache
            return self._base_image(index, pd, key)
        ck = (index, key, display, self.background.get() if key == "overlay" else None)
        img = self._images.get(ck)
        if img is None:
            img = self._base_image(index, pd, key)
            if display and img.size != display:
                img = img.resize(display, Image.BILINEAR, reducing_gap=2.0)
            _lru_put(self._images, ck, img, IMAGE_CACHE_BYTES)
        else:
            self._images.move_to_end(ck)
        return img

    def _slice(self, index: int, pd: PageDiff, key: str, box: tuple[int, int, int, int],
               dest_size: tuple[int, int], display: tuple[int, int] | None) -> Image.Image:
        """One visible slice of a page source, scaled to screen and cached.

        Caching per (page, source, box, size, filter) is what makes wiping cheap
        and, above all, stable: dragging the divider reuses the very same scaled
        slices, so nothing is re-sampled and the image cannot shift by a pixel.
        """
        ck = (index, key, box, dest_size, self._fast_paint,
              self.background.get() if key == "overlay" else None)
        img = self._slices.get(ck)
        if img is None:
            img = self._page_image(index, pd, key, display).crop(box)
            if img.size != dest_size:
                smooth = dest_size[0] < img.size[0] and not self._fast_paint
                img = img.resize(dest_size, Image.BILINEAR if smooth else Image.NEAREST)
            _lru_put(self._slices, ck, img, SLICE_CACHE_BYTES)
        else:
            self._slices.move_to_end(ck)
        return img

    def _source_key(self) -> str:
        return {"avant": "a", "apres": "b", "overlay": "overlay"}.get(self.view.get(), "b")

    # -- painting --------------------------------------------------------- #

    def _schedule_redraw(self, fast: bool = False):
        """Coalesce repaints so a burst of wheel/drag events paints once.

        `fast` marks an interactive frame: it is scaled with NEAREST, and a
        quality repaint is armed for when the interaction stops.
        """
        if fast:
            self._fast_paint = True
            if self._quality_job:
                self.after_cancel(self._quality_job)
            self._quality_job = self.after(QUALITY_DELAY, self._quality_repaint)
        if self._redraw_pending:
            return
        self._redraw_pending = True
        self.after_idle(self._redraw)

    def _quality_repaint(self):
        self._quality_job = None
        self._fast_paint = False
        self._schedule_redraw()

    def _ensure_frame(self, vw: int, vh: int):
        """One viewport-sized framebuffer, reused and cleared between repaints."""
        if self._frame is None or self._frame.size != (vw, vh):
            self._frame = Image.new("RGB", (vw, vh), CANVAS_BG)
            self._frame_photo = ImageTk.PhotoImage(self._frame)
            self._frame_item = None
        else:
            self._frame.paste(CANVAS_BG, (0, 0, vw, vh))

    def _blit(self, img: Image.Image, x: int, y: int):
        self._frame.paste(img, (x, y))

    def _commit_frame(self):
        """Hand the framebuffer to Tk: one paste into an existing image."""
        self._frame_photo.paste(self._frame)
        if self._frame_item is None:
            self._frame_item = self.canvas.create_image(
                0, 0, anchor="nw", image=self._frame_photo)
            self.canvas.tag_lower(self._frame_item)

    def _clear_overlay_items(self):
        """Drop every canvas item except the framebuffer image.

        Deliberately not tag-based: a create_* call missing its tag would leak
        items on every repaint, which is exactly how ghost text piled up on the
        drop panels.
        """
        for item in self.canvas.find_all():
            if item != self._frame_item:
                self.canvas.delete(item)

    def _redraw(self):
        self._redraw_pending = False
        vw, vh = self._viewport()
        self._clear_overlay_items()
        self._ensure_frame(vw, vh)
        if not self.session:
            self._commit_frame()
            self._draw_drop_panels(over_comparison=False)
            return
        if self.fit.get():
            self.reset_view_zoom_only()
        self._clamp()

        vw, vh = self._viewport()
        _, max_w, total_h = self._layout()
        pad_x = max(0.0, (vw - max_w) / 2)
        pad_y = max(0.0, (vh - total_h) / 2)
        visible = self._visible_bands()

        rects = []
        for index, ytop, pw, ph in visible:
            page_x = (max_w - pw) / 2 - self.ox + pad_x
            page_y = ytop - self.oy + pad_y
            rects.append((index, page_x, pw, max(0.0, page_y), min(float(vh), page_y + ph)))
            pd = self.session.get(index)
            if pd is None:
                self._paint_pending(index, page_x, page_y, pw, ph)
                continue
            self._paint_page(index, pd, page_x, page_y, pw, ph)
        self._page_rects = rects

        self._commit_frame()
        if self.view.get() == "slide" and visible:
            self._draw_divider()
        if self._drag_side:
            self._draw_drop_panels(over_comparison=True)

        self._request_pages(visible)
        self._sync_scrollbars()
        self._update_current_from_scroll(visible, pad_y)
        self._update_status()

    def _paint_page(self, index: int, pd: PageDiff,
                    page_x: float, page_y: float, pw: float, ph: float):
        vw, vh = self._viewport()
        # visible slice of this page, in zoomed px relative to its top-left
        vx0 = max(0.0, -page_x)
        vy0 = max(0.0, -page_y)
        vx1 = min(pw, vw - page_x)
        vy1 = min(ph, vh - page_y)
        if vx1 <= vx0 or vy1 <= vy0:
            return

        zoom = self.zoom
        # Pre-scaling the whole page only pays off in the thumbnail regime, where
        # the scaled copy is small and gets reused across repaints. Closer in,
        # scaling just the visible slice is cheaper than scaling a page we only
        # show a third of.
        if zoom < PRESCALE_BELOW:
            display = (max(1, int(round(pw))), max(1, int(round(ph))))
            scale = 1.0  # the cached copy is already at screen size
            sx0, sy0 = int(vx0), int(vy0)
            sx1, sy1 = min(display[0], math.ceil(vx1)), min(display[1], math.ceil(vy1))
        else:
            display = None
            scale = zoom
            w, h = pd.size
            sx0, sy0 = int(vx0 / zoom), int(vy0 / zoom)
            sx1 = min(w, math.ceil(vx1 / zoom) + 1)
            sy1 = min(h, math.ceil(vy1 / zoom) + 1)
        if sx1 <= sx0 or sy1 <= sy0:
            return

        box = (sx0, sy0, sx1, sy1)
        dest_size = (max(1, int(round((sx1 - sx0) * scale))),
                     max(1, int(round((sy1 - sy0) * scale))))
        dest_x = int(round(page_x + sx0 * scale))
        dest_y = int(round(page_y + sy0 * scale))

        if self.view.get() != "slide":
            self._blit(self._slice(index, pd, self._source_key(), box, dest_size, display),
                       dest_x, dest_y)
        else:
            # Both halves come from slices sharing one box and one scale, so they
            # sit on the same pixel grid; the seam is then cut on a whole
            # destination pixel and the divider is drawn exactly there.
            after = self._slice(index, pd, "b", box, dest_size, display)
            self._blit(after, dest_x, dest_y)
            cut = max(0, min(dest_size[0], int(round(page_x + self.split(index) * pw)) - dest_x))
            if cut:
                before = self._slice(index, pd, "a", box, dest_size, display)
                self._blit(before.crop((0, 0, cut, dest_size[1])), dest_x, dest_y)
        self._draw_page_frame(index, page_x, page_y, pw, ph)
        if self.boxes.get():
            self._draw_boxes(pd.stats.regions, self.zoom, page_x, page_y)

    def _paint_pending(self, index: int, page_x: float, page_y: float,
                       pw: float, ph: float):
        """Placeholder for a page the worker has not delivered yet.

        The boxes from the low-res scan are drawn right away, so the changed
        areas are visible before the full-resolution comparison lands.
        """
        self.canvas.create_rectangle(page_x, page_y, page_x + pw, page_y + ph,
                                     fill="#e8e8e8", outline="#8a8a8a", tags="vec")
        stats = self.session.scan[index]
        if pw > 90 and ph > 40:
            self.canvas.create_text(page_x + pw / 2, page_y + ph / 2,
                                    text=T("page_pending", page=index + 1),
                                    fill="#707070", font=("Segoe UI", 10), tags="vec")
        if self.boxes.get() and stats.regions:
            factor = self.session.opt.dpi / SCAN_DPI
            self._draw_boxes([r.scaled(factor) for r in stats.regions],
                             self.zoom, page_x, page_y, dash=(4, 3))
        self._draw_page_frame(index, page_x, page_y, pw, ph)

    def _draw_page_frame(self, index: int, page_x: float, page_y: float,
                         pw: float, ph: float):
        if not self.continuous.get():
            return
        self.canvas.create_rectangle(page_x, page_y, page_x + pw, page_y + ph,
                                     outline="#6f6f6f", tags="vec")
        if ph > 30:
            self.canvas.create_text(page_x + 4, page_y + 2, anchor="nw",
                                    text=T("page_short", page=index + 1),
                                    fill="#8f8f8f", font=("Segoe UI", 8), tags="vec")

    def _draw_boxes(self, regions, zoom: float, page_x: float, page_y: float, dash=None):
        for r in regions:
            self.canvas.create_rectangle(
                page_x + r.x0 * zoom, page_y + r.y0 * zoom,
                page_x + r.x1 * zoom, page_y + r.y1 * zoom,
                outline=BOX_COLOR, width=2, dash=dash,
             tags="vec")

    def _draw_divider(self):
        """One vertical divider per visible page, each with its own grab handle."""
        vw, _ = self._viewport()
        r = 15
        for index, page_x, pw, top, bottom in self._page_rects:
            x = float(int(round(page_x + self.split(index) * pw)))  # the seam pixel
            if x < 0 or x > vw or bottom <= top:
                continue
            self.canvas.create_line(x, top, x, bottom, fill="#ffffff", width=3, tags="vec")
            self.canvas.create_line(x, top, x, bottom, fill="#202020", width=1, tags="vec")
            if bottom - top < 2 * r + 4:
                continue
            cy = (top + bottom) / 2
            self.canvas.create_oval(x - r, cy - r, x + r, cy + r,
                                    fill="#f5f5f5", outline="#202020", width=1, tags="vec")
            for sign in (-1, 1):
                tip, base = x + sign * 9, x + sign * 3
                self.canvas.create_polygon(tip, cy, base, cy - 6, base, cy + 6,
                                           fill="#202020", outline="", tags="vec")

    def _request_pages(self, visible):
        """Tell the worker what to compute next: what is on screen, then around."""
        pages = self._layout_pages()
        wanted = [b[0] for b in visible]
        if pages and wanted:
            first, last = pages.index(wanted[0]), pages.index(wanted[-1])
            for offset in (1, 2, 3):
                for index in (last + offset, first - offset):
                    if 0 <= index < len(pages):
                        wanted.append(pages[index])
        self.session.protect(wanted)
        self.session.focus(wanted)

    def _update_current_from_scroll(self, visible, pad_y: float):
        """Follow the page under the middle of the viewport."""
        if not self.continuous.get() or not visible:
            return
        _, vh = self._viewport()
        middle = self.oy + vh / 2 - pad_y
        best = min(visible, key=lambda b: abs((b[1] + b[3] / 2) - middle))
        if best[0] != self.current:
            self.current = best[0]
            self._sync_list_selection()

    def _draw_drop_panels(self, over_comparison: bool):
        """Split the view in two drop targets: before on the left, after on the right.

        Shown while a drag hovers the window, and as the empty state until both
        PDFs are known, so which file is still expected is always visible.
        """
        vw, vh = self._viewport()
        if vw < 120 or vh < 90:
            return
        mid = vw / 2
        active = self._drag_side
        for side, (x0, x1) in (("a", (0, mid)), ("b", (mid, vw))):
            path = (self.path_a if side == "a" else self.path_b).get().strip()
            hot = active == side
            missing = not path
            if over_comparison:
                # dim the comparison underneath instead of hiding it
                self.canvas.create_rectangle(x0, 0, x1, vh, fill="#10171c",
                                             stipple="gray50", outline="", tags="vec")
            self.canvas.create_rectangle(
                x0 + 12, 12, x1 - 12, vh - 12,
                outline="#7fd4ff" if hot else ("#c8a24a" if missing else "#6a6a6a"),
                width=3 if hot else 1, dash=None if hot else (6, 5),
                fill="#1d3542" if hot else "", stipple="" if hot else "gray25",
             tags="vec")
            title = T("panel_before") if side == "a" else T("panel_after")
            if path:
                detail, colour = Path(path).name, "#8fdc9a"
            elif hot:
                detail, colour = T("panel_release"), "#7fd4ff"
            else:
                detail, colour = T("panel_drop_here"), "#e0bb5f"
            cy = vh / 2
            self.canvas.create_text((x0 + x1) / 2, cy - 26, text=title,
                                    fill="#e8e8e8" if hot else "#b8b8b8",
                                    font=("Segoe UI", 12, "bold"), tags="vec")
            self.canvas.create_text((x0 + x1) / 2, cy + 2, text=detail, fill=colour,
                                    font=("Segoe UI", 10), tags="vec",
                                    width=max(80, (x1 - x0) - 40))
            if path and not hot:
                self.canvas.create_text((x0 + x1) / 2, cy + 26, text=T("panel_ok"),
                                        fill="#8fdc9a", font=("Segoe UI", 9), tags="vec")
        self.canvas.create_line(mid, 12, mid, vh - 12, fill="#8a8a8a", dash=(3, 4), tags="vec")
        if not over_comparison:
            hint = T("panel_hint" if getattr(self, "dnd_ok", False) else "panel_hint_nodnd")
            self.canvas.create_text(vw / 2, vh - 30, text=hint, fill="#9a9a9a",
                                    font=("Segoe UI", 9), tags="vec")

    def _update_status(self):
        pd = self.session.get(self.current)
        stats = pd.stats if pd else self.session.scan[self.current]
        note = f"  [{stats.note}]" if stats.note else ""
        wipe = T("wipe_amount", value=self.split(self.current) * 100) if self.view.get() == "slide" else ""
        busy = T("busy") if self.session.pending() else ""
        zoom = T("zoom", auto=T("zoom_auto") if self.fit.get() else "",
                 value=self.zoom * 100)
        self.status.set(
            T("page_status", page=self.current + 1, total=self.session.page_count,
              ratio=stats.changed_ratio * 100, regions=len(stats.regions))
            + f"{note}   |   {zoom}{wipe}{busy}"
        )

    # -- export ----------------------------------------------------------- #

    def full_image(self) -> Image.Image:
        """The current view of the current page, full resolution, for saving."""
        pd = self.session.get_or_compute(self.current)
        w, h = pd.size
        img = self._page_image(self.current, pd, self._source_key(), None).copy()
        if self.view.get() == "slide":
            cut = max(0, min(w, int(round(self.split(self.current) * w))))
            if cut:
                before = self._page_image(self.current, pd, "a", None)
                img.paste(before.crop((0, 0, cut, h)), (0, 0))
        if self.boxes.get() and pd.stats.regions:
            draw = ImageDraw.Draw(img)
            pen = max(1, int(round(self.session.opt.dpi / 96.0)))
            for r in pd.stats.regions:
                draw.rectangle([r.x0, r.y0, r.x1 - 1, r.y1 - 1],
                               outline=(255, 0, 220), width=pen)
        return img

    def on_save(self):
        """Only path that touches the disk, and only on explicit request."""
        if not self.session:
            messagebox.showinfo(T("nothing_to_save_title"), T("nothing_to_save"))
            return
        path = filedialog.asksaveasfilename(
            title=T("save_title"), defaultextension=".png",
            initialfile=f"{self.view.get()}_page_{self.current + 1:03d}.png",
            filetypes=[("PNG", "*.png")],
        )
        if not path:
            return
        try:
            self.full_image().save(path)
        except Exception as exc:
            messagebox.showerror(T("error"), str(exc))
            return
        self.status.set(T("saved", path=path))

    def on_close(self):
        if self.session:
            self.session.close()
        self.master.destroy()


def resource_path(name: str) -> Path:
    """Locate a bundled resource, both from source and from a PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", None)
    return (Path(base) if base else Path(__file__).resolve().parent) / name


def apply_icon(root: tk.Tk):
    """Window and taskbar icon."""
    if sys.platform == "win32":
        try:
            import ctypes
            # without an explicit AppUserModelID, Windows pins the taskbar entry
            # to the host interpreter and shows its icon instead of ours
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "devscripts.comparepdf")
        except Exception:
            pass
    icon = resource_path("icon.ico")
    if not icon.is_file():
        return
    try:
        root.iconbitmap(default=str(icon))
    except tk.TclError:
        pass
    # iconbitmap alone leaves the window without a WM_SETICON icon in a
    # PyInstaller build, so the taskbar falls back to a blank one; loading the
    # image ourselves and calling iconphoto sets it for good.
    try:
        with Image.open(icon) as img:  # PIL picks the largest frame of the .ico
            photo = ImageTk.PhotoImage(img.convert("RGBA"))
        root.iconphoto(True, photo)
        root._icon_photo = photo  # keep a reference alive
    except Exception:
        pass


def main() -> int:
    # TkinterDnD.Tk is a plain Tk root with the tkdnd extension loaded
    try:
        root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    except tk.TclError:  # tkdnd shared library missing or broken
        root = tk.Tk()
    apply_icon(root)
    root.title(T("title"))
    root.geometry("1280x880")
    root.minsize(900, 600)
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()  # scan workers re-launch the PyInstaller exe
    sys.exit(main())
