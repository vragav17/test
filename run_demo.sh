#!/usr/bin/env bash
#
# Build the full test-data set from one source video and diff every variant
# against the reference.
#
#   ./run_demo.sh sintel.mp4
#   ./run_demo.sh sintel.mp4 --explain          # add local model descriptions
#
# Produces, in ./out:
#   report_<variant>.json   the regions
#   report_<variant>.html   a self-contained report you can open or email
#
# Everything runs on this machine. The only network call is to a local Ollama
# server, and only when --explain is passed.

set -euo pipefail

SOURCE="${1:-}"
shift || true
EXTRA=("$@")

if [[ -z "$SOURCE" ]]; then
  cat >&2 <<'USAGE'
usage: ./run_demo.sh <source-video> [--explain] [--model qwen3-vl:8b]

  <source-video>  A 10-15 minute short works best. Sintel is a good choice:
                  lots of distinct shots, dialogue, music and visual action.

  No video to hand? Generate a synthetic one first:
      python make_synthetic_source.py -o sample_source.mp4
USAGE
  exit 2
fi

if [[ ! -f "$SOURCE" ]]; then
  echo "ERROR: source video not found: $SOURCE" >&2
  exit 1
fi

PYTHON="${PYTHON:-python}"
FIXTURES="${FIXTURES:-fixtures}"
OUT="${OUT:-out}"

# Every variant is compared against v_base, the 720p reference.
VARIANTS=(v_lowres v_cut v_audiodub v_replace v_reorder v_tv)

banner() {
  printf '\n\033[1m%s\033[0m\n' "$1"
  printf '%s\n' "------------------------------------------------------------------"
}

banner "1/4  Building fixtures from $SOURCE"
# Writes ground_truth.json alongside the variants, so every diff can be checked
# against the exact timecodes that were edited.
"$PYTHON" make_variants.py "$SOURCE" -o "$FIXTURES"

banner "2/4  Fingerprinting"
mkdir -p "$OUT"
for name in v_base "${VARIANTS[@]}"; do
  video="$FIXTURES/$name.mp4"
  [[ -f "$video" ]] || { echo "  skipping $name (not built)"; continue; }
  if [[ -f "$OUT/$name.json" ]]; then
    echo "  $name: already fingerprinted"
  else
    "$PYTHON" fingerprint.py "$video" -o "$OUT/$name.json"
  fi
done

banner "3/4  Diffing each variant against v_base"
for name in "${VARIANTS[@]}"; do
  [[ -f "$OUT/$name.json" ]] || continue
  "$PYTHON" diff.py "$OUT/v_base.json" "$OUT/$name.json" \
    -o "$OUT/report_$name.json" "${EXTRA[@]+"${EXTRA[@]}"}"
  "$PYTHON" report.py "$OUT/report_$name.json" -o "$OUT/report_$name.html"
done

banner "4/4  Checking against ground truth"
"$PYTHON" verify.py --source "$SOURCE" --fixtures "$FIXTURES" --out "$OUT" \
  "${EXTRA[@]+"${EXTRA[@]}"}" || true

banner "Done"
echo "Reports in $OUT/:"
for name in "${VARIANTS[@]}"; do
  [[ -f "$OUT/report_$name.html" ]] && printf '  %s\n' "$OUT/report_$name.html"
done
echo
echo "Open the multi-change one first -- it is the demo:"
echo "  open $OUT/report_v_tv.html"
