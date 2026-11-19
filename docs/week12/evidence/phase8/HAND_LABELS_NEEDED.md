# Hand labels needed from you (Q7): 23 items

The brief requires the judge to be re-checked against **hand labels on the integrated system's outputs**, and forbids me from labelling or relabelling to reach 85%. Only you can supply them. Until you do, judge agreement is reported as **NOT MEASURED** (denominator 0), not as a number.

## What to do
1. Open `run_final/hand_label_sheet.json`. Each of the 23 items has the question, the caller's version, **the answer a user would have seen**, and **the context the assistant was shown**. The labelling rule is printed at the top (`how_to_label`).
2. Copy `run_final/hand_labels_template.json` and set each `"label"` to `"PASS"` or `"FAIL"` (an optional one-line `"reason"` helps). Leave `null` to skip an item; it is not counted.
3. **Do not open `private_do_not_read_before_labelling/`** (the judge's verdicts, and which items I constructed) until you have finished.
4. Compute the agreement (or just send me the filled file):
```
cd developer-rag
..\.venv\Scripts\python -m sdk_engine eval agreement ^
  --run ..\docs\week12\evidence\phase8\run_final --labels <your filled file> ^
  --verdicts ..\docs\week12\evidence\phase8\private_do_not_read_before_labelling\judge_verdicts.json ^
  --key ..\docs\week12\evidence\phase8\private_do_not_read_before_labelling\label_key.json
```

## What is in the 23 items
* **10 are the live model's real answers** from the final suite run.
* **13 are answers I deliberately corrupted** (a swapped fact, a false "I don't know", an answer to a different question, a one-sided comparison, an invented figure). They exist because every real answer passed the deterministic checks, and agreement on an all-PASS sample would say nothing about whether the judge can catch a wrong answer.
* You are not told which is which; label each on its merits. **If you judge one of my "bad" answers acceptable, your label stands**; the report will only show how often that happened.

## How to read the result
It reports `n` (items you labelled), `agree`, the percentage, whether it is at least 85%, and a warning if `n` is below 20. It also lists every disagreement. I do not change a label, drop a disagreement, or re-run the judge to improve the number.
