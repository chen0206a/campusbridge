# Historical experiments

This directory contains unchanged public copies of original CampusBridge Stage 1–6 artifacts: synthetic cases, frozen packets, model outputs, traces, evaluations and safe report records. [ARCHIVE_INDEX.json](ARCHIVE_INDEX.json) maps each original relative path to its public copy and original byte SHA-256.

Private workstation records, source snapshots and temporary logs remain in the original local archive. They are listed by exclusion reason in the index; original local artifacts were not rewritten. Archived model scores and raw outputs are copied byte-for-byte.

```bash
python scripts/verify_archive.py
```

The verifier compares every copied file against its original checksum. This is independent of the current runtime source binding. To reproduce behavior using the current checkout, create a new packet and a new run directory; see [EXPERIMENTS.md](../docs/EXPERIMENTS.md).

## Main records

| Experiment | Original public evaluation |
|---|---|
| Stage 4 baseline, 26/30 | [evaluation.json](archive/stage4/20261012-deepseek-v1/eval-30/evaluation.json) |
| Stage 5 fixed30, 28/30 | [evaluation.json](archive/stage5/20261012-precision-v1/real30/evaluation.json) |
| Stage 5 boundary12, 10/12 | [evaluation.json](archive/stage5/20261012-precision-v1/real12/evaluation.json) |
| Stage 6 Sequential fixed30 | [evaluation.json](archive/stage6/20261013-dynamic-v1/real-sequential-30/evaluation.json) |
| Stage 6 Dynamic fixed30 | [evaluation.json](archive/stage6/20261013-dynamic-v1/real-dynamic-30/evaluation.json) |
| Stage 6 Sequential boundary12 | [evaluation.json](archive/stage6/20261013-dynamic-v1/real-sequential-12/evaluation.json) |
| Stage 6 Dynamic boundary12 | [evaluation.json](archive/stage6/20261013-dynamic-v1/real-dynamic-12/evaluation.json) |

Early smoke failures, Mock runs and fault-injection outputs are retained alongside successful experiments. `controlled_injection` labels separate synthetic port faults from real provider outputs.
