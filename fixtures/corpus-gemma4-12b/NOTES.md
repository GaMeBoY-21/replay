# gemma4:12b — archived corpus

Fifteen runs of the invoice task, recorded against `gemma4:12b` on Ollama 0.30.10
at a 32,768-token context window. Fourteen finished; `corpus-14` was stopped
partway through and exported as far as it got. Statistics are in `stats.json`,
recomputed from the runs by `scripts/corpus_stats.py`.

This corpus is archived evidence, not a fixture the demo runs from. It
establishes that this model cannot operate the scenario's tools:

- 9 wrong, 2 right, 4 failed — but the nine wrong runs never recorded a currency.
  Each called `convert_invoice_total`, got the error that no currency was
  recorded, retried, and then asserted "41,000 USD" in prose. There is no state
  write for a trace to land on.
- Only the two right runs recorded a currency at all (INR, both times).
- Nine of fifteen runs never called `lookup_vendor`.
- Two runs (`corpus-04`, `corpus-13`) ran into the 80-effect depth ceiling.

The runs are kept exactly as recorded. None is repaired or removed.

## corpus-09: an answer that is not an answer

Gemma leaked its reasoning channel into the text stream twice early in this run —
step 3 begins `thought\nThe user wants to reconcile…`, and step 5 is the bare
fragment `thought\n<channel|>` — and its final response, at step 45, carried no
text at all (28 output tokens, none of them visible).

The corpus statistics take a run's answer to be the last model response that
had any text, so they report `thought\n<channel|>`, from step 5, as corpus-09's
answer. Replaying the run reports what the agent actually ended with: an empty
answer. Both readings come from the same log; they differ because one falls back
and the other does not. The run's outcome is `failed` either way.

`tests/test_corpus.py` pins both values, so the discrepancy cannot disappear or
change without a test saying so.
