"""Edge snapping for dragged fences: the same maths on every platform. A rect here is anything with
left, top, right and bottom attributes."""

SNAP_DISTANCE = 16  # logical pixels
SNAP_GAP = 6  # logical pixels left between fences snapped side by side


def snap_targets(r, work, others, dist, gap):
    """Lines each edge of r may snap to: the monitor's edges, plus the edges of other fences that
    are level with r (side by side with a gap, or aligned with them)."""
    lefts, rights, tops, bottoms = [work.left], [work.right], [work.top], [work.bottom]
    for o in others:
        if r.top <= o.bottom + dist and r.bottom >= o.top - dist:
            lefts += [o.right + gap, o.left]
            rights += [o.left - gap, o.right]
        if r.left <= o.right + dist and r.right >= o.left - dist:
            tops += [o.bottom + gap, o.top]
            bottoms += [o.top - gap, o.bottom]
    return lefts, rights, tops, bottoms


def snap_offset(value, targets, dist):
    """Offset to the nearest target within dist, or None."""
    best = min(targets, key=lambda t: abs(t - value))
    return best - value if abs(best - value) <= dist else None


def closest(*offsets):
    offsets = [o for o in offsets if o is not None]
    return min(offsets, key=abs) if offsets else 0


def snap_moving(r, work, others, dist, gap):
    """Shift the dragged rect so the edge nearest a snap line sits on it."""
    lefts, rights, tops, bottoms = snap_targets(r, work, others, dist, gap)
    dx = closest(snap_offset(r.left, lefts, dist), snap_offset(r.right, rights, dist))
    dy = closest(snap_offset(r.top, tops, dist), snap_offset(r.bottom, bottoms, dist))
    r.left += dx
    r.right += dx
    r.top += dy
    r.bottom += dy


def snap_sizing(r, side, work, others, dist, gap):
    """Snap only the edges being dragged; side is the WMSZ_* code from WM_SIZING."""
    lefts, rights, tops, bottoms = snap_targets(r, work, others, dist, gap)
    if side in (1, 4, 7):
        r.left += snap_offset(r.left, lefts, dist) or 0
    if side in (2, 5, 8):
        r.right += snap_offset(r.right, rights, dist) or 0
    if side in (3, 4, 5):
        r.top += snap_offset(r.top, tops, dist) or 0
    if side in (6, 7, 8):
        r.bottom += snap_offset(r.bottom, bottoms, dist) or 0
