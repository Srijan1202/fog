# Reproducing the paper's numbers and figures

Everything in the paper is recomputed from the archived raw records. Nothing here runs a workload or touches
the device.

## Inputs

- **Evidence folder** `exp1-rerun-prep/` of the TDAS evidence store, with `a3/`, `a4/` and `a7/`. Check its
  integrity first. Expected: every data archive passes. In `a3/`, 4 helper-script entries fail, as noted in
  `evidence_audit.md`.

  ```
  cd <EV>/a7 && sha256sum -c SHA256SUMS
  cd <EV>/a4 && sha256sum -c SHA256SUMS
  cd <EV>/a3 && sha256sum -c SHA256SUMS
  ```
- **Code repository** at the commit that contains `paper/`, on branch `exp1-rerun-prep`. The analysis imports
  the committed `analyze.py` to compare its results with the official pipeline.

## Software

- **Python:** 3.11 or later (used: 3.13.14 on Windows).
- **Packages:** numpy (used 2.5.4) and matplotlib (used 3.11.2). No other packages are needed.
- **Set-up:**

  ```
  python -m venv paperenv
  paperenv/bin/pip install numpy matplotlib          # Windows: paperenv\Scripts\pip
  ```

## Commands

1. **Reproduce the statistics.** Part A reproduces the official values; Part B is the exploratory analysis.

   ```
   paperenv/bin/python -B paper/analysis/reproduce_stats.py <EV> <REPO> paper/analysis/verified_numbers.json
   ```
   - **Outputs:** `verified_numbers.json` and `verified_numbers.log`. The script also writes
     `paper/analysis/_gates_input/` (copies of the three meta files that `analyze.main()` reads).
   - **Expected result:** the log reports G1–G4 PASS and G5/G5b FAIL, with "all match the record", "matches
     record: True", "identical to analyze.boot_diff: True", and the verdict `LIKELY_KILL`.
   - **Runtime:** a few minutes, dominated by the bootstraps.
2. **Figures:**

   ```
   paperenv/bin/python -B paper/analysis/make_figures.py <EV> paper/figures
   ```
   - **Outputs:** `fig1`–`fig7`, each as PDF and PNG.
3. **PDF.** Requires a LaTeX distribution with `IEEEtran.cls`; it was built with MiKTeX. Run this in `paper/`:

   ```
   pdflatex paper.tex && pdflatex paper.tex
   ```

## The official pipeline (for comparison)

The gates as computed during the project come from `<REPO>/analyze.py`, run on a folder holding the three meta
files. `reproduce_stats.py` does the same in step 1 (`official_verdict`).

## Device environment

The device environment is in `paper/analysis/pi_environment.txt`. It is a single read-only query of the
device, made on the same boot as the Experiment 3 session. The device's network address is not recorded.

## Limitations of reproduction

- **Statistics:** the reproduction verifies the analysis of recorded data. It cannot re-create the physical
  conditions (ambient temperature, cooling, device unit).
- **Bootstraps:** the bootstrap results are deterministic because the seeds are fixed (seed 0, as
  preregistered; seed 1 for the exploratory within-sequence bootstrap).
- **Re-running the experiments:** this would need the device, the hash-pinned launchers in `<REPO>/tools/`,
  and the supervision rules in the amendments. It is outside the scope of this paper package.
