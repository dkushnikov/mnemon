#!/usr/bin/env bash
# stub-probe.sh — the image worker's seam (MNEMON_CAPTURE_IMAGE_CMD) for every suite that does not capture an
# image: tests/test-helper.sh exports it, so a doctor run (`mnemon doctor` runs `capture-image.py --probe`,
# capture layer §21.11) and an image dry run never reach the real tesseract, and give the same answer on a
# machine with tesseract and on one without it.
#
# `--probe …` → the `present` document of tests/fixtures/capture/reports/ocr-probe.json on stdout, exit 0.
# Any other call → exit 2 with one line on stderr: a suite that runs the image worker for real must unset
# MNEMON_CAPTURE_IMAGE_CMD for that section (tests/test_image.sh does), and one that forgot fails loudly.
fixture="$(cd "$(dirname "$0")/../reports" && pwd -P)/ocr-probe.json"
for arg in "$@"; do
  if [[ "$arg" == "--probe" ]]; then
    jq -c '.present' "$fixture" || exit 3
    exit 0
  fi
done
echo "stub-probe: only --probe is answered here (MNEMON_CAPTURE_IMAGE_CMD is the test helper's probe stub); a suite that captures an image unsets MNEMON_CAPTURE_IMAGE_CMD for that section" >&2
exit 2
