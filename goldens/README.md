# Golden dataset

The golden dataset is the ground truth the whole tool stands on. Every case
here is a hand-written input plus the plain-English criteria a good output must
satisfy. The judge grades against these criteria; the tool detects regressions
in the judge's scores.

## Rules for a case

- **One reason to exist.** Each case probes one behaviour or failure mode. If
  two cases would fail for the same reason, delete one.
- **Criteria, not answers.** Never write the expected summary. Write what any
  acceptable summary must (or must not) contain.
- **Checkable by a stranger.** Someone who has never seen the feature should be
  able to read the criterion and the output and say yes or no.
- **Adversarial cases earn their keep.** Empty input, huge input, two issues in
  one ticket, a ticket that tries to instruct the model — these catch the
  regressions that "happy path" cases never will.
- **Small and sharp beats big.** 20–30 cases that each catch something is
  better than 200 that overlap.

## Criteria: good vs bad

| Bad (vague / untestable)            | Good (specific / checkable)                                            |
|-------------------------------------|------------------------------------------------------------------------|
| "Summary is accurate"               | "States that the customer was charged twice"                           |
| "Sounds professional"               | "Contains no profanity even though the ticket does"                    |
| "Captures the main point"           | "Identifies the request as a refund, not a cancellation"               |
| "Not too long"                      | "Is at most 3 sentences"                                               |
| "Doesn't make things up"            | "Does not mention any order number (the ticket contains none)"         |

Negative criteria ("does not mention…") are the strongest regression
detectors: they catch hallucination, which is the most common way a prompt
change silently breaks a feature.

## Two traps found the hard way

- **A criterion must not fail a correct output.** Before keeping a criterion,
  imagine the best possible summary and check it passes. "Does not contain
  phrase X" fails a summary that *quotes* X while describing an injection
  attempt; "does not blame user error" fails a summary that faithfully reports
  the customer blaming herself. False alarms train people to ignore the tool.
- **One check per criterion.** "States A and mentions B" cannot be answered
  yes/no when only A is true. Split it.

## File format

YAML gotcha: a criterion containing `: ` (colon-space) must be wrapped in
double quotes, or the file will not parse. Block scalars (`input: |`) are safe.

Cases live in `cases.yaml`. See the worked examples at the top of that file.

```yaml
- id: short_unique_slug          # snake_case, stable forever (baselines key on it)
  tags: [happy_path]             # free-form; used for grouping in reports
  input: |
    The raw ticket text, exactly as a customer would write it.
  criteria:
    - A checkable statement about any acceptable output.
    - Another one.
  notes: Optional. Why this case exists / what regression it is meant to catch.
```
