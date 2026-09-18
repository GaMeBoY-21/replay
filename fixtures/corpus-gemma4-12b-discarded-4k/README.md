# Discarded: recorded with a 4,096-token context window

These eight runs were recorded against `gemma4:12b` on Ollama 0.30.10 before
anyone checked the context window. Ollama picked a VRAM-based default of 4,096
tokens; the model supports 262,144.

Two of them (`corpus-04`, `corpus-05`) ended with `stopReason: max_tokens` after
2,581 and 2,096 output tokens: prompt plus output ran past the window and the
generation was cut off, so Strands raised and the run failed. The rest came
within about a thousand tokens of the limit. A model whose conversation is being
truncated is not free to get the answer right, so none of these count.

They are kept, not deleted, because every run is kept. The corpus in
`../corpus-gemma4-12b/` was re-recorded from scratch at a 32,768-token window, under one
configuration for every run.
