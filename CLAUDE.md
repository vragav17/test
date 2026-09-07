# Vidiff — working notes

## Pull requests

**After every push to `claude/*`, make sure an open PR covers the branch.**

Do not wait to be asked. The sequence is:

1. `git push -u origin <branch>`
2. `git fetch origin main`
3. Check whether the previous PR head is already an ancestor of `origin/main`:
   - **Still open** → the push landed on it. Say so; do not open a second PR.
   - **Merged or closed** → open a new PR for the unmerged commits immediately.

This matters because PRs on this repo are often merged within a minute of being
opened. Any commit pushed after that merge is referenced by nothing, and stays
invisible until someone opens a new PR. That has happened three times.

**Check the merge style before opening.** Every merge so far has been a true
merge commit, which keeps the merge-base clean so a new PR shows only its own
commits. If the repo ever switches to squash-merge, the branch must be rebased
onto `origin/main` first, or already-merged content is re-applied as a
duplicate diff:

```sh
git merge-base --is-ancestor <last-pr-head> origin/main   # true merge?
git log --oneline origin/main..HEAD                       # what is unmerged
git diff --stat origin/main...HEAD                        # what the PR will show
```

**Keep the PR title honest.** A PR that grows past its original scope needs its
title and description updated — a reviewer should not open "fix a typo" and
find four features.

## Verifying changes

`python verify.py` runs seven acceptance checks against `fixtures/ground_truth.json`.
Run it after any change to the pipeline. Check 5 needs a running Ollama and
reports `SKIP` without one; everything else must pass.

Pushing UI changes also means `npm --prefix frontend run build` — `app.py` serves
`frontend/dist`, which is not committed.

## Things measured, not assumed

- `phash` is greyscale and low-frequency. Colour grade, hue, saturation, blur
  and greyscale conversion all measure **0** distance — invisible by design.
  HDR vs SDR measures 6/64. Use `hflip` (28) or a 30% punch-in (28) when a
  fixture needs to register as `replace`.
- Audio clips are MP3, not AAC. Open-source Chromium builds ship without AAC
  and fail with `DEMUXER_ERROR_NO_SUPPORTED_STREAMS`.
- The technical probe reads the **source**, never the 480p proxy — the proxy is
  8-bit BT.709 and would report every HDR master as SDR.
