# Hypeddit unlock live check (owner-run)

An unlock is a write to Hypeddit, so it never runs in CI or in the default test
suite. Run this by hand once after changing the desktop flow, and again when
Hypeddit changes theirs.

## What to check

1. Start the crate browser with a log file:

   ```bash
   dj-digger --log-file ~/dj-digger-hypeddit.log <playlist with hypeddit gates>
   ```

2. Press `w` on one gate that only declares click-through steps (for example
   `corruptedmind/makeba`, whose page is the offline fixture) and on one that
   declares an `sp` step.
3. In the log, find the `/gate/download/ul` attempts and note, per gate:
   - the `is_skippable` value sent on the first attempt and whether a second
     attempt with `1` was needed;
   - the `steps` string and, if the page had `steps_select`, which alternative
     was chosen;
   - the reply (`download_status`, or CAPTCHA / rejection) and whether the file
     arrived.
4. If a gate still refuses both attempts, save its page as a fixture (see
   below) and open the browser fallback to see which step it wants.

## Capturing a fixture

In a private window, logged out of everything, open the gate and copy
`document.documentElement.outerHTML` from the console into
`tests/fixtures/hypeddit_real_gate_<slug>.html`. Then:

- replace the `csrf-token` meta content with `fixture-csrf`;
- drop every `<script src=...>` tag, keep inline scripts (`jsonGateData`,
  `nwSteps`, `steps_select`);
- make sure this finds nothing:

  ```bash
  grep -inE "set-cookie|@[a-z0-9-]+\.[a-z]{2,}|oauth|Bearer" tests/fixtures/hypeddit_real_gate_*.html
  ```
