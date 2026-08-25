# Baseline

Measured 2026-08-22 from 540 assistant messages of 40 words or more, across 25
sessions of one long-running project. These numbers describe one person writing
about one domain, so treat them as a starting point: `prose base` re-derives
them from your own transcripts, which is what makes them yours.

## What the corpus said

| Metric | p10 | median | p90 | max |
|---|---|---|---|---|
| Words per message | 46 | 345 | 741 | 5320 |
| Flesch-Kincaid grade | 4.6 | 6.9 | 9.2 | 14.2 |
| Reading ease | 59.8 | 71.0 | 81.8 | 97.4 |
| Median sentence, words | 11 | 15 | 20 | 39 |
| Longest sentence, words | 21 | 35 | 59 | 120 |
| Bold spans | 1 | 6 | 18 | 215 |

| Behaviour | Rate |
|---|---|
| Opens with a hedge or preamble | 2.6% |
| Contains a banned filler phrase | 1.7% |
| Long message contains a table | 73.8% |
| Long message contains headings | 87.1% |
| Contains a diagram | 0.0% |
| Contains maths notation | 0.0% |

Sentence-length distribution: 87.5% of sentences are under 30 words.

```
    0-9 w  ################# 28.0%
  10-19 w  ####################### 38.5%
  20-29 w  ############# 21.0%
  30-39 w  ##### 8.2%
  40-49 w  ## 2.6%
  50-59 w  # 0.9%
    60+ w  0.7%
```

## What the numbers ruled in and out

Reading level was never the problem. Median grade 6.9 already sits below the
ISO 24495-1 target band of 8 to 10, so a controlled-vocabulary standard such as
ASD-STE100 would optimise something that already passes. Dropped.

Filler and preamble rules were already working at 1.7% and 2.6%. Strengthening
them would spend instruction budget on a solved problem.

The table rule was already working: 73.8% of long messages carry one.

Three things had no bound at all:

1. Length. Median 345 words, p90 741, worst 5320.
2. Diagrams. Zero in 540 messages.
3. Bold. Median 6 spans, p90 18, worst 215 in a single message.

One mechanism was invisible until the long tail was read by hand. The apparent
100-word sentences were bullet lists with no terminal punctuation, each bullet
carrying several facts joined by dashes. That, not prose sentence length, is
where the density lives. `dense_bullets` in `prose score` counts it.

## Where the limits come from

| Limit | Value | Why |
|---|---|---|
| `words` | 700 | p90 was 741 and nothing bounded it |
| `fk` | 12.0 | p90 was 9.2, so this is headroom, not a squeeze |
| `longest_sentence` | 40 | 96.5% of sentences already sit below it |
| `bold` | 12 | Median 6, p90 18 |
| `dense_bullets` | 0 | The measured cause of clustering |
| `diagram_width` | 78 | Fits a standard terminal without wrapping |

The `--doc` flag lifts `words` and `bold`, because a rules file or an ADR is not
a reply.
