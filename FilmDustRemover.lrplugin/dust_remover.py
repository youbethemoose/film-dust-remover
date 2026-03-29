#!/usr/bin/env python3
"""
Film Dust Remover — dust_remover.py
Detects and removes dust from film scans captured on a Sony A7IV
(~33 MP, nearly full 35 mm frame coverage, ~190 px/mm).

Subject Protection
──────────────────
If MediaPipe is available, we detect the subject's face and body and build
a protection mask. The dust remover will NOT touch any pixel inside that
zone — preventing false positives on eyebrows, eyelashes, and hair.
If MediaPipe is not installed the plugin falls back to the standard
circularity-filtered detection with no subject protection.

Dust Detection
──────────────
1. Light Gaussian blur   — removes single-pixel grain noise
2. Median reference      — local "clean" reference scaled to image resolution
3. Difference maps       — dark and bright local anomalies
4. Size filter           — keeps only realistic dust-particle sizes
5. Circularity filter    — rejects elongated shapes (eyelashes, hair strands)
6. Protection mask       — zeroes out any detected dust inside subject regions
7. NS inpainting         — smooth 4 px radius fill, no ghosting
"""

import sys
import os
import argparse

import cv2
import numpy as np
from typing import Optional, Tuple

# ── Optional MediaPipe import ─────────────────────────────────────────────────
# mp.solutions was removed in MediaPipe 0.10.14+. We catch AttributeError so
# the plugin degrades gracefully on any version rather than crashing.
try:
    import mediapipe as mp
    _ = mp.solutions.selfie_segmentation   # probe — raises AttributeError if gone
    MP_AVAILABLE = True
except (ImportError, AttributeError):
    MP_AVAILABLE = False

# ── OpenCV Haar cascade — always available, used when MediaPipe is absent ─────
_FACE_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
)
_PROFILE_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + 'haarcascade_profileface.xml'
)


# ─── Subject Protection ───────────────────────────────────────────────────────

def _protection_via_mediapipe(image_bgr: np.ndarray,
                               image_rgb: np.ndarray) -> Optional[np.ndarray]:
    """Full-body + face-mesh protection using MediaPipe (best quality)."""
    h, w = image_bgr.shape[:2]
    protection = np.zeros((h, w), dtype=np.uint8)
    found = False

    with mp.solutions.selfie_segmentation.SelfieSegmentation(
            model_selection=1) as seg:
        result = seg.process(image_rgb)
        if result.segmentation_mask is not None:
            person = (result.segmentation_mask > 0.5).astype(np.uint8) * 255
            protection = cv2.bitwise_or(protection, person)
            found = True

    with mp.solutions.face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=6,
            refine_landmarks=True,
            min_detection_confidence=0.4) as fm:
        result = fm.process(image_rgb)
        if result.multi_face_landmarks:
            for face in result.multi_face_landmarks:
                pts = np.array([[int(lm.x * w), int(lm.y * h)]
                                for lm in face.landmark], dtype=np.int32)
                cv2.fillPoly(protection, [cv2.convexHull(pts)], 255)
            found = True

    return protection if found else None


def _protection_via_opencv(image_bgr: np.ndarray) -> Optional[np.ndarray]:
    """Face-box protection using OpenCV Haar cascades (no extra dependencies)."""
    h, w = image_bgr.shape[:2]
    protection = np.zeros((h, w), dtype=np.uint8)

    # Scale down for speed — detection works fine at ~1200px wide
    scale = min(1.0, 1200.0 / max(h, w))
    gray  = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, None, fx=scale, fy=scale)

    faces = []
    for cascade in (_FACE_CASCADE, _PROFILE_CASCADE):
        if cascade.empty():
            continue
        found = cascade.detectMultiScale(small, scaleFactor=1.1,
                                          minNeighbors=4, minSize=(30, 30))
        if len(found):
            faces.extend(found.tolist())

    if not faces:
        return None

    for (x, y, fw, fh) in faces:
        # Scale back to full resolution
        x, y, fw, fh = (int(v / scale) for v in (x, y, fw, fh))
        # Generous padding: extra room above for hair, below for neck/shoulders
        pad_x   = int(fw * 0.5)
        pad_top = int(fh * 1.0)   # hair above
        pad_bot = int(fh * 0.5)   # neck / shoulders below
        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_top)
        x2 = min(w, x + fw + pad_x)
        y2 = min(h, y + fh + pad_bot)
        cv2.rectangle(protection, (x1, y1), (x2, y2), 255, -1)

    return protection


def build_protection_mask(image_bgr: np.ndarray) -> Optional[np.ndarray]:
    """
    Returns a uint8 mask (same H×W as image) where 255 = protected (subject).
    Tries MediaPipe first (best quality), falls back to OpenCV Haar cascade.
    Returns None if no subject detected.
    """
    h, w = image_bgr.shape[:2]
    protection = None

    if MP_AVAILABLE:
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        protection = _protection_via_mediapipe(image_bgr, image_rgb)
        method = 'mediapipe'
    else:
        protection = _protection_via_opencv(image_bgr)
        method = 'opencv-cascade'

    if protection is None:
        return None

    # Dilate by ~1 % of image width for a comfortable safety margin
    margin = max(10, w // 100)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                   (margin * 2 + 1, margin * 2 + 1))
    protection = cv2.dilate(protection, k)

    pct = np.sum(protection > 0) / (h * w) * 100
    print(f'PROTECT: {method} — subject mask covers {pct:.1f}% of image')
    return protection


# ─── Detection ────────────────────────────────────────────────────────────────

def detect(image_8bit: np.ndarray, sensitivity: int,
           protection: Optional[np.ndarray]) -> Tuple[np.ndarray, dict]:
    """
    Returns (mask, stats_dict).
    mask   — uint8 binary map of pixels to inpaint
    stats  — diagnostic counts for logging
    """
    s = sensitivity / 100.0
    h, w = image_8bit.shape[:2]

    gray = cv2.cvtColor(image_8bit, cv2.COLOR_BGR2GRAY)

    # ── 1. Light Gaussian — kills single-pixel grain noise ────────────────────
    smooth = cv2.GaussianBlur(gray, (3, 3), 0)

    # ── 2. Median reference ───────────────────────────────────────────────────
    # Scaled to image diagonal so it bridges over the largest expected dust
    # at Sony A7IV resolution (~190 px/mm on 35 mm film).
    diag_px = np.sqrt(h * w)
    med_k = int(diag_px * (0.003 + s * 0.008))
    if med_k % 2 == 0:
        med_k += 1
    med_k = max(15, min(med_k, 101))
    reference = cv2.medianBlur(smooth, med_k)

    # ── 3. Difference maps ────────────────────────────────────────────────────
    diff_dark   = cv2.subtract(reference, smooth)
    diff_bright = cv2.subtract(smooth, reference)

    dark_thresh   = max(12, int(48 - s * 36))
    bright_thresh = max(18, int(65 - s * 47))

    _, dark_mask   = cv2.threshold(diff_dark,   dark_thresh,   255, cv2.THRESH_BINARY)
    _, bright_mask = cv2.threshold(diff_bright, bright_thresh, 255, cv2.THRESH_BINARY)
    combined = cv2.bitwise_or(dark_mask, bright_mask)

    # Close tiny gaps inside a single particle
    k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, k_close)

    # ── 4. Size limits (resolution-aware) ─────────────────────────────────────
    img_area      = h * w
    min_dust_area = 3
    max_dust_area = max(500, img_area * (0.0002 + s * 0.0028))

    # ── 5. Circularity threshold ───────────────────────────────────────────────
    # Dust blobs: 0.40–1.0   → kept
    # Eyelashes:  0.02–0.15  → always rejected
    # Eyebrows:   0.15–0.30  → rejected
    min_circularity = max(0.25, 0.52 - s * 0.22)

    # ── 6. Component loop ─────────────────────────────────────────────────────
    result = np.zeros(gray.shape, dtype=np.uint8)
    n_dust = 0
    n_skip = 0
    n_protected = 0

    contours_all, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL,
                                        cv2.CHAIN_APPROX_NONE)

    for cnt in contours_all:
        area = cv2.contourArea(cnt)

        if area < min_dust_area or area > max_dust_area:
            n_skip += 1
            continue

        # Shape gate
        perimeter = cv2.arcLength(cnt, True)
        circularity = (4 * np.pi * area / perimeter ** 2
                       if perimeter > 0 else 0.0)
        if circularity < min_circularity:
            n_skip += 1
            continue

        # Protection gate — skip if centroid lands inside subject mask
        if protection is not None:
            M = cv2.moments(cnt)
            if M['m00'] > 0:
                cx = int(M['m10'] / M['m00'])
                cy = int(M['m01'] / M['m00'])
                if protection[cy, cx] > 0:
                    n_protected += 1
                    continue

        cv2.drawContours(result, [cnt], -1, 255, -1)
        n_dust += 1

    # ── 7. Dilate mask to cover edges ─────────────────────────────────────────
    dil = max(1, int(1 + s * 2))
    k_dil = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                       (dil * 2 + 1, dil * 2 + 1))
    result = cv2.dilate(result, k_dil)

    # Never inpaint inside the protection zone even after dilation
    if protection is not None:
        result[protection > 0] = 0

    stats = {'dust': n_dust, 'skipped': n_skip,
             'protected': n_protected,
             'total_candidates': len(contours_all)}
    return result, stats


# ─── Inpainting ───────────────────────────────────────────────────────────────

def inpaint_image(image_8bit: np.ndarray, mask: np.ndarray) -> np.ndarray:
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
          f'sensitivity={sensitivity}  '
          f'mediapipe={"yes" if MP_AVAILABLE else "no (fallback mode)"}')

    image_8bit = (image >> 8).astype(np.uint8) if is_16bit else image.copy()

    # ── Subject protection mask ────────────────────────────────────────────────
    protection = build_protection_mask(image_8bit)
    if protection is None and MP_AVAILABLE:
        print('PROTECT: no subject detected — full image will be processed')
    elif not MP_AVAILABLE:
        print('PROTECT: MediaPipe not installed — run setup.sh to enable '
              'subject protection')

    # ── Detect ─────────────────────────────────────────────────────────────────
    mask, stats = detect(image_8bit, sensitivity, protection)

    dust_px = int(np.sum(mask > 0))
    pct     = dust_px / (image.shape[0] * image.shape[1]) * 100

    print(f'DETECT:  dust={stats["dust"]}  '
          f'protected={stats["protected"]}  '
          f'skipped={stats["skipped"]}  '
          f'mask={dust_px}px ({pct:.4f}%)')

    if pct > 2.0:
        print('WARNING: mask >2% of image — try lowering sensitivity.',
              file=sys.stderr)

    if debug:
        mask_path = output_path.rsplit('.', 1)[0] + '_mask.tif'
        cv2.imwrite(mask_path, mask)
        if protection is not None:
            prot_path = output_path.rsplit('.', 1)[0] + '_protection.tif'
            cv2.imwrite(prot_path, protection)
        print(f'DEBUG:   masks saved alongside output')

    if dust_px == 0:
        print('INFO:    No dust detected — saving clean copy')
        cv2.imwrite(output_path, image)
        return

    # ── Inpaint ────────────────────────────────────────────────────────────────
    result_8bit = inpaint_image(image_8bit, mask)

    if is_16bit:
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
    parser = argparse.ArgumentParser(description='Film Dust Remover')
    parser.add_argument('input')
    parser.add_argument('output')
    parser.add_argument('--sensitivity', type=int, default=50)
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()

    if not (1 <= args.sensitivity <= 100):
        print('ERROR: sensitivity must be 1–100', file=sys.stderr)
        sys.exit(1)

    process(args.input, args.output, args.sensitivity, args.debug)
