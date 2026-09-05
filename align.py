"""Stage 5: Needleman-Wunsch global alignment over two shot sequences.

Written directly against numpy -- no bioinformatics library. Two shots are
compared by the Hamming distance between their 64-bit perceptual hashes, and
the classic global alignment recurrence decides which shots correspond, which
were dropped, and which were added.

The row recurrence is vectorised. The left-neighbour term
``H[i][j] = max(..., H[i][j-1] + gap)`` looks inherently sequential, but with a
linear gap penalty it is a running maximum: substituting ``G[j] = H[i][j] -
j*gap`` turns it into ``G[j] = max(best[j] - j*gap, G[j-1])``, which is exactly
``np.maximum.accumulate``. So each row is three vector ops rather than an inner
Python loop.
"""

from dataclasses import dataclass, field
from itertools import groupby

import numpy as np

# Scoring, per the spec.
GAP_PENALTY = -1
MATCH_MAX_DISTANCE = 10      # phash distance <= 10 -> a real match
WEAK_MAX_DISTANCE = 20       # 11..20 -> weak match, neither rewarded nor punished
MATCH_SCORE = 2
WEAK_SCORE = 0
MISMATCH_SCORE = -1

# Audio-only change: applied after alignment, never inside the scoring function.
AUDIO_CHANGE_DISTANCE = 16

HASH_BITS = 64


@dataclass
class Op:
    """One aligned position: a pair, a deletion from A, or an insertion in B."""

    type: str                 # equal | replace | delete | insert | audio_changed
    a_index: int | None
    b_index: int | None
    phash_distance: int | None = None
    ahash_distance: int | None = None
    a_cursor: int = 0         # how many A shots were consumed before this op
    b_cursor: int = 0         # how many B shots were consumed before this op


@dataclass
class Region:
    """A run of consecutive same-type ops, with real timecodes on both sides."""

    type: str
    a_start: float
    a_end: float
    b_start: float
    b_end: float
    shot_count: int
    a_indices: list = field(default_factory=list)
    b_indices: list = field(default_factory=list)


# --------------------------------------------------------------------------
# hash comparison
# --------------------------------------------------------------------------


def hashes_to_bits(hex_hashes):
    """(n,) hex strings -> (n, 64) uint8 bit matrix, most significant bit first."""
    bits = np.zeros((len(hex_hashes), HASH_BITS), dtype=np.uint8)
    for row, hex_str in enumerate(hex_hashes):
        value = int(hex_str, 16)
        for bit in range(HASH_BITS):
            bits[row, bit] = (value >> (HASH_BITS - 1 - bit)) & 1
    return bits


def hamming_matrix(a_bits, b_bits):
    """All-pairs Hamming distance between two bit matrices.

    popcount(a XOR b) == |a| + |b| - 2*(a . b) for 0/1 vectors, so the whole
    matrix is one matmul instead of an (n, m, 64) broadcast that would blow up
    on long files.
    """
    a = a_bits.astype(np.int32)
    b = b_bits.astype(np.int32)
    a_sum = a.sum(axis=1)[:, None]
    b_sum = b.sum(axis=1)[None, :]
    return a_sum + b_sum - 2 * (a @ b.T)


def score_matrix(distances):
    """Map phash distances to alignment scores."""
    return np.select(
        [distances <= MATCH_MAX_DISTANCE, distances <= WEAK_MAX_DISTANCE],
        [MATCH_SCORE, WEAK_SCORE],
        default=MISMATCH_SCORE,
    ).astype(np.int64)


# --------------------------------------------------------------------------
# the algorithm
# --------------------------------------------------------------------------


def needleman_wunsch(scores, gap=GAP_PENALTY):
    """Fill the global alignment DP matrix. Returns H with shape (n+1, m+1)."""
    n, m = scores.shape
    H = np.zeros((n + 1, m + 1), dtype=np.int64)
    H[:, 0] = np.arange(n + 1) * gap
    H[0, :] = np.arange(m + 1) * gap

    j_offsets = np.arange(m + 1) * gap          # j*gap, for the accumulate trick
    j_gap = np.arange(1, m + 1) * gap

    for i in range(1, n + 1):
        diag = H[i - 1, 0:m] + scores[i - 1, :]  # H[i-1][j-1] + s(i,j)
        up = H[i - 1, 1:m + 1] + gap             # H[i-1][j]   + gap
        best = np.maximum(diag, up)
        # Fold in the left neighbour as a running maximum.
        G = np.maximum.accumulate(
            np.concatenate(([H[i, 0]], best - j_gap))
        )
        H[i, :] = G + j_offsets
    return H


def traceback(H, scores, gap=GAP_PENALTY):
    """Walk the DP matrix back to a list of operations.

    Scores are integers, so these equality tests are exact -- no float
    tolerance needed. Ties prefer the diagonal, which keeps matched runs
    together instead of fragmenting them into gap pairs.
    """
    n, m = scores.shape
    i, j = n, m
    ops = []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and H[i, j] == H[i - 1, j - 1] + scores[i - 1, j - 1]:
            ops.append(("diag", i - 1, j - 1))
            i -= 1
            j -= 1
        elif i > 0 and H[i, j] == H[i - 1, j] + gap:
            ops.append(("delete", i - 1, None))
            i -= 1
        elif j > 0 and H[i, j] == H[i, j - 1] + gap:
            ops.append(("insert", None, j - 1))
            j -= 1
        else:
            raise RuntimeError(
                f"Traceback stuck at cell ({i}, {j}) with H={H[i, j]}. "
                f"This indicates a bug in the DP fill, not bad input."
            )
    ops.reverse()
    return ops


def align(a_shots, b_shots):
    """Align two shot lists. Returns (ops, alignment_score).

    `a_shots` / `b_shots` are the "shots" arrays from two fingerprint files.
    """
    if not a_shots or not b_shots:
        raise ValueError("Both fingerprints must contain at least one shot.")

    a_bits = hashes_to_bits([s["phash"] for s in a_shots])
    b_bits = hashes_to_bits([s["phash"] for s in b_shots])
    distances = hamming_matrix(a_bits, b_bits)
    scores = score_matrix(distances)

    H = needleman_wunsch(scores)
    raw_ops = traceback(H, scores)

    ops = []
    a_cursor = b_cursor = 0
    for kind, ai, bj in raw_ops:
        if kind == "diag":
            dist = int(distances[ai, bj])
            op = Op(
                type="equal" if dist <= MATCH_MAX_DISTANCE else "replace",
                a_index=ai,
                b_index=bj,
                phash_distance=dist,
                a_cursor=a_cursor,
                b_cursor=b_cursor,
            )
            a_cursor += 1
            b_cursor += 1
        elif kind == "delete":
            op = Op(type="delete", a_index=ai, b_index=None,
                    a_cursor=a_cursor, b_cursor=b_cursor)
            a_cursor += 1
        else:
            op = Op(type="insert", a_index=None, b_index=bj,
                    a_cursor=a_cursor, b_cursor=b_cursor)
            b_cursor += 1
        ops.append(op)

    return ops, int(H[len(a_shots), len(b_shots)])


# --------------------------------------------------------------------------
# audio-only pass
# --------------------------------------------------------------------------


def retag_audio_changes(ops, a_shots, b_shots, threshold=AUDIO_CHANGE_DISTANCE):
    """Retag `equal` pairs whose audio hashes disagree as `audio_changed`.

    Deliberately a separate pass over the aligned pairs rather than part of the
    scoring function: audio should never influence which shots are considered
    to correspond, only what we say about the ones that do.
    """
    from vdiff_common import hamming_hex

    retagged = 0
    for op in ops:
        if op.a_index is None or op.b_index is None:
            continue
        dist = hamming_hex(a_shots[op.a_index]["ahash"], b_shots[op.b_index]["ahash"])
        op.ahash_distance = dist
        if op.type == "equal" and dist > threshold:
            op.type = "audio_changed"
            retagged += 1
    return retagged


# --------------------------------------------------------------------------
# merging
# --------------------------------------------------------------------------


def merge_regions(ops, a_shots, b_shots, a_duration, b_duration):
    """Merge consecutive same-type ops into regions.

    Returns only the regions that represent a *change*: runs of `equal` are
    dropped, so two identical files produce zero regions.
    """
    regions = []
    for kind, group in groupby(ops, key=lambda op: op.type):
        if kind == "equal":
            continue  # matched picture and audio is not a change worth reporting
        regions.append(
            _build_region(list(group), a_shots, b_shots, a_duration, b_duration)
        )
    return regions


def _collapse_point(cursor, shots, duration):
    """Where a gap sits on the side that has no content for it."""
    if cursor < len(shots):
        return float(shots[cursor]["start"])
    return float(duration)


def _build_region(run, a_shots, b_shots, a_duration, b_duration):
    a_idx = [op.a_index for op in run if op.a_index is not None]
    b_idx = [op.b_index for op in run if op.b_index is not None]

    if a_idx:
        a_start = float(a_shots[a_idx[0]]["start"])
        a_end = float(a_shots[a_idx[-1]]["end"])
    else:
        # An insertion has no A footprint; mark the point it was inserted at.
        a_start = a_end = _collapse_point(run[0].a_cursor, a_shots, a_duration)

    if b_idx:
        b_start = float(b_shots[b_idx[0]]["start"])
        b_end = float(b_shots[b_idx[-1]]["end"])
    else:
        b_start = b_end = _collapse_point(run[0].b_cursor, b_shots, b_duration)

    return Region(
        type=run[0].type,
        a_start=round(a_start, 3),
        a_end=round(a_end, 3),
        b_start=round(b_start, 3),
        b_end=round(b_end, 3),
        shot_count=len(run),
        a_indices=a_idx,
        b_indices=b_idx,
    )
