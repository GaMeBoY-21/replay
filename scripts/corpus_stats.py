"""Recompute the corpus statistics from the committed runs and write them.

    uv run python scripts/corpus_stats.py fixtures/corpus-qwen2.5-14b

tests/test_corpus.py requires each corpus's stats.json to equal what this
computes, so the published failure rate is always the committed runs' rate.
"""

from __future__ import annotations

import json
import pathlib
import sys

from replay.scenario.corpus import run_files, summarise

def main() -> int:
    CORPUS = pathlib.Path(sys.argv[1])
    stats = summarise(run_files(CORPUS))
    (CORPUS / "stats.json").write_text(json.dumps(stats, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in stats.items() if k != "rows"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
