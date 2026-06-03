from __future__ import annotations

import logging
from math import ceil, floor, sqrt
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _as_rgb_array(np, rgb_value):
    return np.array(rgb_value, dtype=np.float32).reshape(1, 1, 3)


def _optional_cv2():
    try:
        import cv2  # type: ignore

        return cv2
    except Exception:
        return None


def _contrast_score(np, rgba, holder_rgb, background_rgb):
    arr = np.asarray(rgba, dtype=np.uint8)
    rgb = arr[:, :, :3].astype(np.float32)
    holder_color = _as_rgb_array(np, holder_rgb)
    background_color = _as_rgb_array(np, background_rgb)
    holder_distance = np.linalg.norm(rgb - holder_color, axis=2).astype(np.float32)
    background_distance = np.linalg.norm(rgb - background_color, axis=2).astype(
        np.float32
    )
    return background_distance - holder_distance


def _blur_score(np, score):
    cv2 = _optional_cv2()
    if cv2 is not None and hasattr(cv2, "GaussianBlur"):
        return cv2.GaussianBlur(score, (7, 7), 0)

    kernel = np.ones((5, 5), dtype=np.float32) / 25.0
    padded = np.pad(score, 2, mode="edge")
    blurred = np.empty_like(score, dtype=np.float32)
    for row in range(score.shape[0]):
        for col in range(score.shape[1]):
            window = padded[row : row + 5, col : col + 5]
            blurred[row, col] = float((window * kernel).sum())
    return blurred


def build_catch_auto_contrast_rgba(rgba, holder_rgb, background_rgb):
    try:
        import numpy as np
    except Exception:
        logger.debug("Catch auto contrast preview requires numpy", exc_info=True)
        return None

    arr = np.asarray(rgba, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return None

    score = _blur_score(np, _contrast_score(np, arr, holder_rgb, background_rgb))
    finite = np.isfinite(score)
    if not np.any(finite):
        return None

    lo = float(np.percentile(score[finite], 5))
    hi = float(np.percentile(score[finite], 95))
    if hi - lo < 1e-6:
        normalized = np.zeros_like(score, dtype=np.uint8)
    else:
        normalized = np.clip((score - lo) / (hi - lo), 0.0, 1.0)
        normalized = (normalized * 255.0).astype(np.uint8)

    rgba_out = np.zeros((arr.shape[0], arr.shape[1], 4), dtype=np.uint8)
    rgba_out[:, :, 0] = normalized
    rgba_out[:, :, 1] = normalized
    rgba_out[:, :, 2] = normalized
    rgba_out[:, :, 3] = 255

    mask = normalized >= max(128, int(np.percentile(normalized, 70)))
    edge_mask = _edge_mask(np, normalized, mask)

    rgba_out[mask, 0] = np.maximum(rgba_out[mask, 0], 235)
    rgba_out[mask, 1] = np.maximum(rgba_out[mask, 1], 215)
    rgba_out[mask, 2] = np.maximum(rgba_out[mask, 2], 80)
    rgba_out[edge_mask, 0] = 0
    rgba_out[edge_mask, 1] = 255
    rgba_out[edge_mask, 2] = 255
    rgba_out[edge_mask, 3] = 255
    return rgba_out


def _edge_mask(np, normalized, mask):
    cv2 = _optional_cv2()
    if cv2 is not None and hasattr(cv2, "Canny"):
        try:
            return cv2.Canny(normalized, 40, 120) > 0
        except Exception:
            pass
    grad_x = np.abs(np.diff(mask.astype(np.int8), axis=1, prepend=0))
    grad_y = np.abs(np.diff(mask.astype(np.int8), axis=0, prepend=0))
    return (grad_x + grad_y) > 0


def detect_catch_auto_outer_geometry(
    *,
    shape_role: str,
    holder_role: str,
    image_rect_bounds: Tuple[float, float, float, float],
    center_image_xy: Tuple[float, float],
    rgba,
    holder_rgb,
    background_rgb,
) -> Optional[dict]:
    try:
        import numpy as np
    except Exception:
        logger.debug("Catch auto requires numpy", exc_info=True)
        return None

    arr = np.asarray(rgba, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return None

    image_h, image_w = arr.shape[:2]
    rect_left, rect_top, rect_right, rect_bottom = image_rect_bounds
    left = max(0, int(floor(rect_left)))
    top = max(0, int(floor(rect_top)))
    right = min(image_w, int(ceil(rect_right)))
    bottom = min(image_h, int(ceil(rect_bottom)))
    if right - left < 12 or bottom - top < 12:
        return None

    roi_rgba = arr[top:bottom, left:right, :]
    contrast = _blur_score(
        np, _contrast_score(np, roi_rgba, holder_rgb, background_rgb)
    )
    foreground = _foreground_mask(np, contrast)
    if foreground is None:
        return None

    foreground = _clean_foreground(np, foreground)
    foreground = _centered_component(
        foreground,
        approx_x=float(center_image_xy[0] - left),
        approx_y=float(center_image_xy[1] - top),
    )

    ys, xs = np.nonzero(foreground)
    if xs.size < 20 or ys.size < 20:
        return None

    roi_h, roi_w = roi_rgba.shape[:2]
    weights = contrast[foreground] + 1.0
    local = _fit_outer_bounds(
        np,
        role=str(shape_role or "").lower(),
        holder_role=str(holder_role or "").lower(),
        xs=xs,
        ys=ys,
        weights=weights,
        foreground=foreground,
        roi_w=roi_w,
        roi_h=roi_h,
    )
    if local is None:
        return None

    outer_left, outer_top, outer_right, outer_bottom, center_x, center_y = local
    return {
        "rect_image": (
            float(outer_left + left),
            float(outer_top + top),
            float(outer_right + left),
            float(outer_bottom + top),
        ),
        "center_image": (float(center_x + left), float(center_y + top)),
    }


def _foreground_mask(np, contrast):
    positive_values = contrast[contrast > 0.0]
    if positive_values.size == 0:
        return None
    threshold = max(3.0, float(np.percentile(positive_values, 45)))
    foreground = contrast >= threshold
    if int(foreground.sum()) < 20:
        return None
    return foreground


def _clean_foreground(np, foreground):
    cv2 = _optional_cv2()
    if cv2 is None or not (
        hasattr(cv2, "morphologyEx") and hasattr(cv2, "MORPH_CLOSE")
    ):
        return foreground
    mask_u8 = foreground.astype(np.uint8) * 255
    kernel = np.ones((5, 5), dtype=np.uint8)
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, kernel)
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN, kernel)
    return mask_u8 > 0


def _centered_component(foreground, *, approx_x: float, approx_y: float):
    cv2 = _optional_cv2()
    if cv2 is None or not hasattr(cv2, "connectedComponentsWithStats"):
        return foreground

    mask_u8 = foreground.astype("uint8")
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask_u8, 8)
    best_idx = None
    best_score = None
    for idx in range(1, int(count)):
        area = float(stats[idx, cv2.CC_STAT_AREA])
        if area < 20:
            continue
        cx = float(centroids[idx][0])
        cy = float(centroids[idx][1])
        distance = ((cx - approx_x) ** 2 + (cy - approx_y) ** 2) ** 0.5
        score = area - 4.0 * distance
        if best_score is None or score > best_score:
            best_score = score
            best_idx = idx
    if best_idx is None:
        return foreground
    return labels == best_idx


def _fit_outer_bounds(
    np,
    *,
    role: str,
    holder_role: str,
    xs,
    ys,
    weights,
    foreground,
    roi_w: int,
    roi_h: int,
):
    bbox_left = float(xs.min())
    bbox_right = float(xs.max())
    bbox_top = float(ys.min())
    bbox_bottom = float(ys.max())
    bbox_width = max(10.0, bbox_right - bbox_left + 1.0)
    bbox_height = max(10.0, bbox_bottom - bbox_top + 1.0)

    if role == holder_role:
        center_x = float((xs * weights).sum() / weights.sum())
        center_y = float((ys * weights).sum() / weights.sum())
        var_x = float((((xs - center_x) ** 2) * weights).sum() / weights.sum())
        var_y = float((((ys - center_y) ** 2) * weights).sum() / weights.sum())
        fitted_width = max(10.0, min(float(roi_w), 4.0 * sqrt(max(var_x, 1.0))))
        fitted_height = max(10.0, min(float(roi_h), 4.0 * sqrt(max(var_y, 1.0))))
        outer_width = 0.7 * fitted_width + 0.3 * bbox_width
        outer_height = 0.7 * fitted_height + 0.3 * bbox_height
        return (
            center_x - outer_width / 2.0,
            center_y - outer_height / 2.0,
            center_x + outer_width / 2.0,
            center_y + outer_height / 2.0,
            center_x,
            center_y,
        )

    profile_x = foreground.astype(np.float32).mean(axis=0)
    profile_y = foreground.astype(np.float32).mean(axis=1)
    kernel = np.array([1.0, 2.0, 3.0, 2.0, 1.0], dtype=np.float32)
    kernel /= float(kernel.sum())
    profile_x = np.convolve(profile_x, kernel, mode="same")
    profile_y = np.convolve(profile_y, kernel, mode="same")
    mask_x = profile_x >= max(0.08, float(profile_x.max()) * 0.35)
    mask_y = profile_y >= max(0.08, float(profile_y.max()) * 0.35)
    if mask_x.any():
        x_idx = np.nonzero(mask_x)[0]
        outer_left = float(x_idx[0])
        outer_right = float(x_idx[-1])
    else:
        outer_left = bbox_left
        outer_right = bbox_right
    if mask_y.any():
        y_idx = np.nonzero(mask_y)[0]
        outer_top = float(y_idx[0])
        outer_bottom = float(y_idx[-1])
    else:
        outer_top = bbox_top
        outer_bottom = bbox_bottom
    return (
        outer_left,
        outer_top,
        outer_right,
        outer_bottom,
        (outer_left + outer_right) / 2.0,
        (outer_top + outer_bottom) / 2.0,
    )
