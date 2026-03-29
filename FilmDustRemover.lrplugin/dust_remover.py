#!/usr/bin/env python3
"""
Film Dust Remover — dust_remover.py
Detects and removes dust spots from film scans captured on a Sony A7IV
(~33 MP, nearly full 35 mm frame coverage, ~190 px/mm).

Detection strategy
──────────────────
Dust shows up as a local dark (or bright) anomaly against a median-filtered
reference. After thresholding we run connected-component analysis and keep
only regions whose area falls within realistic dust-particle bounds — small
enough to exclude real image content, large enough to exclude film grain.

Hair detection is intentionally omitted: elongated-shape heuristics cannot
reliably distinguish a film-hair artifact from actual hair on a subject.
"""

import sys
import os
import argparse
import cv2
import numpy as np


# ─── Detection ────────────────────────────────────────────────────────────────

def detect(image_8bit: np.ndarray, sensitivity: int) -> tuple[np.ndarray, dict]:
    """
    Returns (mask, stats_dict).

    mask   — uint8 binary map of pixels to inpaint
    stats  — diagnostic counts for logging
    """
    s = sensitivity / 100.0
    h, w = image_8bit.shape[:2]

    gray = cv2.cvtColor(image_8bit, cv2.COLOR_BGR2GRAY)

    # ── 1. Light Gaussian — kills single-pixel grain noise only ───────────────
    smooth = cv2.GaussianBlur(gray, (3, 3), 0)

    # ── 2. Median reference — local tonal context ─────────────────────────────
    # Kernel must be large enough to bridge over the biggest expected dust.
    # At 33 MP / ~190 px-per-mm, a 2 mm clump is ~380 px wide, so we need
    # the kernel to be wider than that at high sensitivity.
    # We scale with image diagonal: on a 33 MP A7IV scan this gives 25–75 px;
    # on a smaller export it stays proportionally sensible.
    diag_px_ref = np.sqrt(h * w)
    med_k = int(diag_px_ref * (0.003 + s * 0.008))   # ~25–75 px at 33 MP
    if med_k % 2 == 0:
        med_k += 1
    med_k = max(15, min(med_k, 101))                  # clamp 15–101
    reference = cv2.medianBlur(smooth, med_k)

    # ── 3. Difference maps (dark anomalies and bright anomalies) ──────────────
    diff_dark   = cv2.subtract(reference, smooth)   # dust / hairs (positive film)
    diff_bright = cv2.subtract(smooth, reference)   # bright scratches / hairs

    dark_thresh   = max(12, int(48 - s * 36))   # 12–48 DN
    bright_thresh = max(18, int(65 - s * 47))   # 18–65 DN

    _, dark_mask   = cv2.threshold(diff_dark,   dark_thresh,   255, cv2.THRESH_BINARY)
    _, bright_mask = cv2.threshold(diff_bright, bright_thresh, 255, cv2.THRESH_BINARY)
    combined = cv2.bitwise_or(dark_mask, bright_mask)

    # Close tiny gaps inside a single particle / hair strand
    k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, k_close)

    # ── 4. Size-filter contours (dust only) ───────────────────────────────────
    # Resolution-aware limits for Sony A7IV (~33 MP, ~190 px/mm on 35 mm frame)
    #   0.5 mm particle  →  ~9,000 px²
    #   1.0 mm particle  →  ~28,000 px²
    #   2.0 mm clump     →  ~113,000 px²
    # Scaled by image area so the plugin adapts to any export resolution.
    img_area = h * w

    # min: 3 px² — grain is 1–2 px, we skip it entirely
    min_dust_area = 3

    # max dust: scales 0.02 %–0.30 % with sensitivity
    #   at 33 MP, s=0.5  →  ~50,000 px²  (~260 px diam, ≈1.4 mm physical)
    #   at 33 MP, s=1.0  →  ~98,000 px²  (~354 px diam, ≈1.9 mm physical)
    max_dust_area = max(500, img_area * (0.0002 + s * 0.0028))

    result = np.zeros(gray.shape, dtype=np.uint8)
    n_dust = 0
    n_skip = 0

    contours_all, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL,
                                        cv2.CHAIN_APPROX_SIMPLE)

    for cnt in contours_all:
        area = cv2.contourArea(cnt)

        if area < min_dust_area:
            n_skip += 1
            continue

        if area <= max_dust_area:
            cv2.drawContours(result, [cnt], -1, 255, -1)
            n_dust += 1
        else:
            n_skip += 1

    # ── 5. Dilate mask to fully cover edges ───────────────────────────────────
    dil = max(1, int(1 + s * 2))   # 1–3 px
    k_dil = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                       (dil * 2 + 1, dil * 2 + 1))
    result = cv2.dilate(result, k_dil)

    stats = {'dust': n_dust, 'skipped': n_skip,
             'total_candidates': len(contours_all)}
    return result, stats


# ─── Inpainting ───────────────────────────────────────────────────────────────

def inpaint_image(image_8bit: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """
    Navier-Stokes inpainting, small fixed radius.
    NS produces smoother, less streaky fills than TELEA on film grain texture.
    Radius 4 px is large enough to bridge dust/hair but small enough to
    prevent ghosting / smearing.
    """
    return cv2.inpaint(image_8bit, mask, inpaintRadius=4, flags=cv2.INPAINT_NS)


# ─── Main ─────────────────────────────────────────────────────────────────────

def process(input_path: str, output_path: str,
            sensitivity: int = 50,
            debug: bool = False) -> None:

    image = cv2.imread(input_path, cv2.IMREAD_ANYDEPTH | cv2.IMREAD_COLOR)
    if image is None:
        print(f'ERROR: Cannot read: {input_path}', file=sys.stderr)
        sys.exit(1)

    is_16bit = image.dtype == np.uint16
    print(f'INFO:    {os.path.basename(input_path)}  '
          f'{image.shape[1]}×{image.shape[0]}  '
          f'{"16-bit" if is_16bit else "8-bit"}  '
          f'sensitivity={sensitivity}')

    image_8bit = (image >> 8).astype(np.uint8) if is_16bit else image.copy()

    # ── Detect ─────────────────────────────────────────────────────────────────
    mask, stats = detect(image_8bit, sensitivity)

    dust_px = int(np.sum(mask > 0))
    pct     = dust_px / (image.shape[0] * image.shape[1]) * 100

    print(f'DETECT:  dust={stats["dust"]}  skipped={stats["skipped"]}  '
          f'mask={dust_px}px ({pct:.4f}%)')

    if pct > 2.0:
        print('WARNING: Mask >2% of image — possible over-detection. '
              'Try lowering sensitivity.', file=sys.stderr)

    if debug:
        mask_path = output_path.rsplit('.', 1)[0] + '_mask.tif'
        cv2.imwrite(mask_path, mask)
        print(f'DEBUG:   mask → {mask_path}')

    if dust_px == 0:
        print('INFO:    No artifacts detected — saving clean copy')
        cv2.imwrite(output_path, image)
        return

    # ── Inpaint ────────────────────────────────────────────────────────────────
    result_8bit = inpaint_image(image_8bit, mask)

    if is_16bit:
        # Only replace masked pixels; unmasked pixels keep full 16-bit precision
        result    = image.copy()
        mask_bool = mask > 0
        for c in range(image.shape[2]):
            result[:, :, c] = np.where(
                mask_bool,
                result_8bit[:, :, c].astype(np.uint16) * 257,
                image[:, :, c]
            )
    else:
        result = result_8bit

    ok = cv2.imwrite(output_path, result)
    if not ok:
        print(f'ERROR:   Could not write: {output_path}', file=sys.stderr)
        sys.exit(1)

    print(f'SUCCESS: {output_path}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Film Dust & Hair Remover')
    parser.add_argument('input')
    parser.add_argument('output')
    parser.add_argument('--sensitivity', type=int, default=50)
    parser.add_argument('--debug', action='store_true',
                        help='Save detection mask alongside output')
    args = parser.parse_args()

    if not (1 <= args.sensitivity <= 100):
        print('ERROR: sensitivity must be 1–100', file=sys.stderr)
        sys.exit(1)

    process(args.input, args.output, args.sensitivity, args.debug)
