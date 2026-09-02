# The statistics stage 03 uses, and why

Stage 03 answers one question: **did quality actually drop, or did the dice land
differently?** This document explains the four ideas it uses to answer that, in
the order they matter.

Everything here is implemented in `src/regression_detect/compare.py`, in
deterministic code. No model is asked for any of it.

---

## 1. One run is not evidence

The target feature is a language model at temperature 0.2. Ask it the same
question twice and you get two different summaries. The judge grades each of
them, so the same golden case can score 5/5 on Tuesday and 4/5 on Wednesday with
nothing changed in between.

That means a single run's pass rate is a **sample**, not a measurement. If the
baseline scored 97% last week and this run scores 92%, the honest first question
is not "what regressed?" but "how often does a run come out 5 points below the
last one when nothing changed?" On 67 criteria, the answer is: quite often.

A threshold on a single run — "fail if pass rate < 95%" — therefore does two bad
things at once. It fires on runs where nothing changed (false alarms, which get
the check disabled within a month) and it stays quiet on real regressions that
happen to land above the line. Neither is fixed by moving the threshold.

The fix is to keep counts, compare two sets of counts, and ask how surprising the
difference is.

---

## 2. Fisher's exact test: how surprising is this drop?

The comparison is a two-by-two table. The numbers below are **illustrative** —
invented to keep the arithmetic in this section easy to follow by hand, with 67
criteria on each side. They are not a run of this repository. The real worked
case is in [`docs/examples/regressed_comparison.json`](examples/regressed_comparison.json):
a two-run baseline of 125/134 against a candidate of 48/67, p ≈ 6.0e-05, verdict
`REGRESSION`.

|            | passed | failed | total |
|------------|-------:|-------:|------:|
| baseline   |     65 |      2 |    67 |
| candidate  |     55 |     12 |    67 |
| **total**  | **120**| **14** |**134**|

The **null hypothesis** is that both rows were produced by the same underlying
pass rate, and the difference between them is dealing.

Fisher's exact test asks: hold the row totals (67 and 67) and the column totals
(120 and 14) fixed, and imagine dealing the 120 passes out at random. How likely
is a candidate row with 55 passes *or fewer*?

Under those fixed margins, the candidate's pass count follows the
**hypergeometric distribution** — the distribution of how many red balls you draw
when you take 67 balls from an urn holding 120 red and 14 black, without
replacement. So

$$P(X = x) = \frac{\binom{120}{x}\binom{14}{67-x}}{\binom{134}{67}}$$

and for the illustrative table above the p-value is the lower tail,
$P(X \le 55) = 0.0044$.

Read that as: **if nothing had changed, a candidate row this bad would come up
about 4 times in 1000.** That is the number `alpha` (default 0.05) is compared
against.

Three things worth knowing about this choice:

- **Exact, not approximate.** The chi-squared test and the normal-approximation
  z-test are the usual tools, but both are approximations that misbehave at small
  counts and at rates near 100% — which is exactly where a healthy golden suite
  lives. Fisher's test is computed from the binomial coefficients directly, with
  `math.comb` in exact integer arithmetic, so there is no small-sample caveat and
  no numerical library to depend on.
- **One-sided on purpose.** A regression detector cares about the pass rate
  *falling*. A two-sided test would spend half its significance budget on the
  possibility that the candidate got better, which is not a thing this check
  should ever block.
- **It is a probability, not a verdict.** p = 0.0044 says the drop is hard to
  explain by chance. It says nothing about whether the drop is worth caring
  about. That is the next section.

---

## 3. `min_effect`: significance is not importance

A p-value shrinks as the sample grows. With 10,000 judged criteria, a drop from
97.0% to 96.5% is overwhelmingly significant — and completely uninteresting.
Blocking a merge on it would be a bug in the check, not a finding.

So the rule has a second half. A drop must clear `min_effect` (default 0.05, five
percentage points) **as well as** being significant at `alpha`. The two conditions
answer different questions:

| Condition | Question it answers |
|---|---|
| `p < alpha` | Is this drop bigger than run-to-run noise? |
| `drop ≥ min_effect` | Is this drop big enough to be worth a human's time? |

Requiring both is what stops the check from becoming either a nuisance or a
rubber stamp. Both numbers live in `regression.toml` where they can be reviewed
and argued about, rather than buried in a function.

---

## 4. The Wilson interval: how sure are we of each rate?

The p-value compares the two runs. The Wilson interval describes each one on its
own — it is what the report prints beside the pass rates.

The textbook interval, $\hat{p} \pm z\sqrt{\hat{p}(1-\hat{p})/n}$, breaks in the
two places this tool always operates. At 67/67 it gives a zero-width interval
(100% ± 0%, as if the next run could not possibly fail anything), and just below
that it produces upper bounds above 100%.

The Wilson score interval fixes both by solving for the rates the observation is
actually consistent with, rather than assuming the observed rate is the truth:

$$\frac{\hat{p} + \frac{z^2}{2n} \pm z\sqrt{\frac{\hat{p}(1-\hat{p})}{n} + \frac{z^2}{4n^2}}}{1 + \frac{z^2}{n}}$$

For the illustrative 65/67 that gives roughly **89.8% – 99.2%**; for 55/67,
**71.3% – 89.4%**. Those two intervals barely overlap, which is a second, visual
way of seeing what the p-value already said.

The interval is reported, never decided on. Overlapping intervals are not the same
question as a significance test, and treating them as one is a common way to miss
a real difference.

---

## 5. The hard-regression rule

The pooled test is a blunt instrument for one important case: a single criterion
that the baseline **always** passed and the candidate **now always fails**.
Across 67 criteria that is a drop of about 1.5 points — nowhere near
`min_effect`, and invisible in the pooled p-value. But "the summary no longer
ever states the refund amount" is exactly the regression this tool exists to
catch.

So a criterion is flagged as a hard regression when all four of these hold:

- the baseline judged it at least twice (`n ≥ 2`),
- the baseline passed it **every** time,
- the candidate judged it at least twice,
- the candidate failed it **every** time.

Any hard regression makes the verdict `REGRESSION` on its own. The `n ≥ 2`
requirement on both sides is what keeps this from firing on a single coin flip;
it is also why a baseline should pool at least two runs.

---

## 6. Putting it together: the decision

```
REGRESSION      if any hard regression
                or (drop ≥ min_effect and p < alpha)

INCONCLUSIVE    else if no criterion matched
                or candidate judged criteria < min_samples
                or candidate judge errors > max_judge_error_rate of its rows

NO_REGRESSION   otherwise
```

The order matters. A proven regression is reported even when the sample is thin —
the finding is that the evidence *exists*. `INCONCLUSIVE` is reserved for the case
where it does not: too few judged criteria to see a drop of the size worth
failing a build over, or so many failed judge calls that the candidate's pass
rate is a rumour rather than a measurement. `INCONCLUSIVE` is a distinct exit
code (2) precisely so that "we could not tell" never gets rounded to "fine".

Only criteria present on **both** sides are compared, matched by case id,
position **and** text. An edited criterion is a different question; comparing new
answers against old ones would look valid and mean nothing. Criteria on one side
only are listed as `unmatched` and excluded from every number — reported, never
silently dropped.

---

## 7. What this does not do

Honest limitations, in rough order of how much they should worry you.

**Criteria are not independent.** Fisher's test assumes 134 independent
observations. They are not: the 5 criteria of one case grade the *same* summary,
so if that summary is bad they tend to fail together. This makes the effective
sample size smaller than `n` suggests, and the p-value therefore somewhat
**anti-conservative** — a little more likely to call a regression than the number
implies. Two mitigations: `min_effect`, which the correlation does not touch, and
the per-case table, which makes it visible when the whole drop came from one
case. A properly clustered test (or a case-level bootstrap) would be the real
fix; that is a known gap, not an oversight.

**Small n.** With one run each side, every criterion has `n = 1`, the
hard-regression rule cannot fire, and only a very large pooled drop is
detectable. This is why `min_samples` exists and why baselines should pool runs.

**Judge noise is invisible to the test.** Every count here is the judge's
opinion, not the truth. A judge that drifts — a model update, a reworded rubric —
moves the candidate's pass rate with no change to the target at all, and the test
will faithfully report a significant drop. Nothing in stage 03 can detect this;
the defences are outside it: pinning `judge_model_id` and `judge_prompt_sha256`
in the baseline and refusing to pool runs that disagree, and `calibrate.py`,
which compares the judge against human labels. Watch its `false_pass` count.

**Judge errors are assumed to be missing at random.** Excluded rows are almost
always rate limits, which have nothing to do with the content being graded. If
they ever became content-correlated — a safety filter refusing one class of
ticket, say — the surviving sample would be biased and the pass rate would be
wrong in a way no threshold here would catch. `max_judge_error_rate` bounds the
damage rather than detecting it.

**No multiple-comparison correction.** The per-criterion table is descriptive,
for a human to read. Only the pooled test and the hard-regression rule decide
anything, so there is no family of 67 tests to correct for — but do not read the
per-criterion rows as 67 significance tests, because they are not.

**A drop is not a diagnosis.** Stage 03 says the quality fell. It does not say
the prompt is the cause; a provider-side model change produces exactly the same
signal. That is why the baseline pins the target model id, and why an
unexplained regression is worth checking against the provider's changelog before
it is blamed on the diff.
