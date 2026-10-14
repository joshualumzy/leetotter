# Simulation of the 100% bar (SPEC §9, §16)

Pure code, no model calls: `uv run python -m simulation` reproduces every table below (20 seeded pretend learners per row, about 3 minutes). Memory is the real `py-fsrs` 6.3.2 with defaults, no learning steps, retention 0.9 (checked in `tests/test_simulation_memory.py`: one Good gives 2.31 days, two Goods a week apart 21.41). Weeks are rounded. "Reached" means all 150 done on your own and all 18 categories strong for both skills at the same moment, within 2 years.

## Findings

| Question | Answer |
|---|---|
| Time to 100% | Only a learner who solves about 90% of new mediums cleanly gets there: median 29 weeks at 7 a week (27-38; about 6.7 months, a little over the 6-month goal), 42 at 5, 23 at 10, 14 at 15. At 70% or 50% clean nobody reached it in 2 years at any pace, even with unlimited extra problems, though coverage alone (all 150 done) takes 56 and 73 weeks at 7 a week. |
| Do retries crowd out new problems? | No, not at 90% clean: retries are 10% of attempts (20-40% at 70% and 50%), 204 attempts to the bar, which matches the spec's 200-250 estimate. Getting all 150 done takes 29 weeks against 21 for 150 problems at 7 a week; the other 8 weeks are retries and extras. The real bottleneck is not retries but the supply of unseen problems (next row). |
| Can every category, Tries included, reach strong for both skills? | Only with enough problems beyond the 150. Transfer evidence comes only from unseen problems, and 75 of the 150 are imported (seen), so each category has about half its problems unseen, Tries about 1.5 of 3. At 90% clean, 7 a week, Tries was strong for both skills in 30% of runs with no extras, 75% with the 100 extras of the 250 list, 100% with a 300-problem pool (table H). Small categories (Two Pointers, Heap, Bit Manipulation, Tries) need the most. |
| Follow-up question rate | Ask about 60% of first attempts when complexity is already strong and about 90% when it is weak: 19/20 reached. Asking 30%/50% gave 7/20 (too little analysis evidence); asking every time gave 13/20 (every extra graded question is another chance to be marked Again). |
| 21 days against 14, 28, 35 | 14 and 21 perform alike (18/20 and 19/20 reached, median 28 and 29 weeks); 28 and 35 are slower and less reliable (15/20 and 13/20, 31 and 36 weeks). At 70% clean none of them is reachable. Recommendation: keep 21 (see below). |

## Things in the spec that look wrong or need a decision

1. **The bar is a snapshot of 36 conditions at once, and one miss drops a category.** "Latest review not Again" plus refreshes of rusty categories (which also apply to strong ones) means a learner who misses 10-30% of new problems keeps knocking a category out. At 70% clean the best snapshot was 12-13 of 18 categories strong. Either accept that the bar needs a ~90% learner, or loosen it (for example, "not Again in the last two category reviews", or no refresh of strong categories; table G shows the latter alone does not fix it, because supply is the other limit).
2. **"Modest" extras beyond the 150 is not enough.** The spec caps nothing but calls them modest. Here a 15% cap gave only 6/20 reaching the bar at 7 a week and 90% clean; uncapped with 300 available, 19/20, using about 32 extras (about 15% of 204 attempts) but needing the pool to be large for small categories. The extra 100 of the 250 list is the minimum; the pool should include the rest of NeetCode's free problems. Note the sim assumed every category has extras in proportion to its size, which is unknown for Tries.
3. **The refresh rule burns unseen problems early.** A category with one Good review has stability 2.3 days, so it is "rusty" after 2-3 days and gets another unseen problem. Early refreshes are the main consumer of the unseen supply. Consider refreshing with a done problem until the category has stability of about a week.
4. **The 21-day justification holds.** Two Goods 1, 3, 5, 7, 14 days apart give 7.3, 13.8, 18.2, 21.4, 28.8 days. 14 days is met by two Goods three days apart, so it does not demand real spacing; 28 needs a two-week gap or a third review and cost reliability here (15/20 against 19/20). A Hard first review then a Good a week later gives 11.6, so one shaky review costs another review.
5. **The pace estimate is fine for a strong learner.** 204 attempts to the bar (spec: 200-250) and about 29 weeks at 7 a week (spec: "7-8 months" is conservative; 6.7 months here). For a 70% learner the estimate is far too low: all 150 done alone takes 56 weeks.
6. Breaks: a two-week break costs about 2 weeks (31 weeks, 17/20 reached); a 3-month break costs about 12-18 weeks (41 weeks at day 56, 47 at day 150). Recovery is spaced revisiting, not a collapse. Breaks do not change the 70% result.
7. An improving learner (70% rising to 90% over 150 attempts) reaches the bar in 5/20 runs, 36 weeks; (50% to 90%) 2/20. The bar rewards being good early; a learner who is still improving through attempts 100-200 keeps using up unseen problems at the lower rate.

## Recommendation on 21 days

Keep 21. It is the shortest value that needs two reviews at least a week apart; 14 can be met by two Goods three days apart (13.8) and showed no better outcome (18/20 against 19/20), and 28 and 35 lengthened the time (31 and 36 weeks) and cut the reached share (15/20 and 13/20) without any gain in what is verified. The threshold is not what limits readiness here; supply of unseen problems and the one-miss rule are.

## Assumptions

- **Catalogue:** category sizes as given (sum 150, checked). Difficulty per category (Easy/Medium/Hard) from my recollection of the NeetCode 150 list: 28 E, 101 M, 21 H overall; Tries is M, M, H. Extras: 100 spread by category size (largest remainder), 70% Medium and 30% Hard; the 300-pool variant is the same rule. Extras never count toward coverage, only as first attempts for categories.
- **Learner:** ability is fixed unless a row says "improves". A clean solve of a new medium has the stated probability; the chance of failing is scaled by 0.6 for Easy and 1.6 for Hard. Of the non-clean solves, 20% are brute-force-only (Hard, not done), 20% are solved after questions (Hard, done), 60% need help (Again, not done). Repeats succeed with FSRS's recall estimate for that problem, with the same difficulty scaling on the failure chance, and fail into the same split. Imported problems have no FSRS state until their first graded attempt there, and then behave like a new problem. Complexity answers: Good with the clean rate (scaled by difficulty), the rest Hard (two thirds) or Again (one third), on first attempts only, never after an Again solve. A complexity question is asked with the rates in table F (default 60% and 90%).
- **Grades:** exactly SPEC §8, one rating per attempt: Good on an Easy counts as Hard for the category's `solve`. All references are assumed checked. "Strong" uses the stability stored after the last review (stability does not decay), latest review not Again, and one Good on a Medium or Hard for that skill.
- **Planner stand-in:** in order: (1) retry any 150-problem not done on your own, 3 days after its last attempt, oldest first, no cap; (2) a category whose `solve` recall is below 0.9 (or whose weak `analysis` is) gets one unseen problem, checking categories that are solid but not strong first, then an extra problem if none is left; (3) a solid category never tested on a new problem gets one; (4) otherwise new work: among categories not touched in 5 days, the one with the lowest share done, picking an imported problem (tested by the same problem) or an unseen one 50/50; (5) otherwise, a redo of the done problem with the lowest recall (counted as an "idle slot"). Extras are allowed up to 15% of attempts so far plus 2 (table A) or without cap (the rest). Mixing and no category naming are not modelled.
- **Calendar:** sessions every 2-7 days at random (mean 4.5), carrying a fractional budget so the weekly pace is exact on average; the first session is day 0; at most 2 years simulated. Breaks start at the first session after the stated day. FSRS fuzzing is off (it only jitters scheduled intervals, which nothing here uses). Time is whole days (py-fsrs rounds elapsed time to days).
- **Not modelled:** `analysis` for repeat attempts, the Tutor stating the complexity earlier (folded into the question rate), session exclusions and corrections, the 5-day mix rule's exceptions for new categories, per-problem due scheduling (retries are by the rule only), and pattern-level variations (a variation is any unseen problem in the same category). Sample size is 20 runs per row, so differences of a few runs are noise (for example 35 days looking better for an improving learner is that: the longer bar is only met after the learner has improved).

## Tables

Columns: Reached = runs that reached the bar; weeks are medians among runs that did (range in the next column); "all 150 done" and "all strong" are the first week each half of the bar held; Max cats strong = median over runs of the most categories strong at once; Real attempts, Retry share and Extras cover the whole run (2 years if the bar was never reached); Idle slots are redo-of-done-problem fillers where nothing else was allowed.

### A. Pace x clean-solve rate, SPEC as written (100 extras, capped at 15% of attempts)

| Scenario | Reached | Median wk to 100% | Range wk | Median wk all 150 done | Median wk all strong | Max cats strong at once | Real attempts | Retry share | Extras | Idle slots |
|---|---|---|---|---|---|---|---|---|---|---|
| 5/wk, 50% clean | 0/20 | - | - | 58 | - | 8 | 392 | 41% | 81 | 132 |
| 5/wk, 70% clean | 0/20 | - | - | 49 | - | 13 | 363 | 38% | 76 | 160 |
| 5/wk, 90% clean | 4/20 | 40 | 37-44 | 40 | 25 | 17 | 324 | 36% | 52 | 198 |
| 7/wk, 50% clean | 0/20 | - | - | 41 | - | 8 | 453 | 47% | 94 | 279 |
| 7/wk, 70% clean | 0/20 | - | - | 34 | - | 13 | 416 | 44% | 80 | 317 |
| 7/wk, 90% clean | 6/20 | 28 | 26-31 | 28 | 24 | 17 | 363 | 43% | 50 | 351 |
| 10/wk, 50% clean | 0/20 | - | - | 30 | - | 6 | 504 | 52% | 97 | 542 |
| 10/wk, 70% clean | 0/20 | - | - | 25 | - | 12 | 461 | 48% | 87 | 584 |
| 10/wk, 90% clean | 7/20 | 22 | 18-24 | 20 | 19 | 16 | 380 | 45% | 60 | 632 |
| 15/wk, 50% clean | 0/20 | - | - | 20 | - | 6 | 538 | 54% | 98 | 1034 |
| 15/wk, 70% clean | 0/20 | - | - | 18 | - | 11 | 476 | 50% | 88 | 1094 |
| 15/wk, 90% clean | 4/20 | 14 | 12-15 | 14 | 9 | 17 | 396 | 45% | 61 | 1160 |

### B. Pace x clean-solve rate, wide problem pool (300 extras, uncapped)

| Scenario | Reached | Median wk to 100% | Range wk | Median wk all 150 done | Median wk all strong | Max cats strong at once | Real attempts | Retry share | Extras | Idle slots |
|---|---|---|---|---|---|---|---|---|---|---|
| 5/wk, 50% clean | 0/20 | - | - | 101 | - | 4 | 519 | 18% | 278 | 4 |
| 5/wk, 70% clean | 0/20 | - | - | 79 | - | 12 | 454 | 20% | 215 | 70 |
| 5/wk, 90% clean | 15/20 | 42 | 34-59 | 41 | 31 | 18 | 215 | 11% | 42 | 5 |
| 7/wk, 50% clean | 0/20 | - | - | 73 | - | 5 | 592 | 28% | 276 | 141 |
| 7/wk, 70% clean | 0/20 | - | - | 56 | - | 12 | 532 | 30% | 212 | 200 |
| 7/wk, 90% clean | 19/20 | 29 | 27-38 | 29 | 24 | 18 | 204 | 10% | 32 | 2 |
| 10/wk, 50% clean | 0/20 | - | - | 58 | - | 4 | 668 | 35% | 287 | 380 |
| 10/wk, 70% clean | 0/20 | - | - | 37 | - | 13 | 597 | 35% | 242 | 452 |
| 10/wk, 90% clean | 20/20 | 23 | 18-33 | 20 | 18 | 18 | 206 | 13% | 30 | 23 |
| 15/wk, 50% clean | 0/20 | - | - | 40 | - | 5 | 718 | 39% | 288 | 851 |
| 15/wk, 70% clean | 0/20 | - | - | 24 | - | 12 | 642 | 39% | 246 | 930 |
| 15/wk, 90% clean | 16/20 | 14 | 12-32 | 13 | 13 | 18 | 213 | 17% | 30 | 30 |

### C. Breaks at 7/wk, wide pool

| Scenario | Reached | Median wk to 100% | Range wk | Median wk all 150 done | Median wk all strong | Max cats strong at once | Real attempts | Retry share | Extras | Idle slots |
|---|---|---|---|---|---|---|---|---|---|---|
| 70% clean, no break | 0/20 | - | - | 56 | - | 12 | 532 | 30% | 212 | 200 |
| 70% clean, 2 weeks at day 56 | 0/20 | - | - | 54 | - | 13 | 519 | 29% | 221 | 202 |
| 70% clean, 3 months at day 56 | 0/20 | - | - | 65 | - | 13 | 486 | 28% | 206 | 157 |
| 70% clean, 3 months at day 150 | 0/20 | - | - | 64 | - | 12 | 494 | 27% | 207 | 150 |
| 90% clean, no break | 19/20 | 29 | 27-38 | 29 | 24 | 18 | 204 | 10% | 32 | 2 |
| 90% clean, 2 weeks at day 56 | 17/20 | 31 | 26-45 | 30 | 24 | 18 | 209 | 11% | 36 | 6 |
| 90% clean, 3 months at day 56 | 19/20 | 41 | 38-60 | 41 | 37 | 18 | 202 | 10% | 32 | 2 |
| 90% clean, 3 months at day 150 | 16/20 | 47 | 39-70 | 44 | 35 | 18 | 235 | 14% | 52 | 22 |

### D. Learner who improves (clean rate rises linearly over the first 150 attempts), 7/wk

| Scenario | Reached | Median wk to 100% | Range wk | Median wk all 150 done | Median wk all strong | Max cats strong at once | Real attempts | Retry share | Extras | Idle slots |
|---|---|---|---|---|---|---|---|---|---|---|
| 50% to 90%, spec pool | 0/20 | - | - | 33 | - | 14 | 402 | 45% | 72 | 331 |
| 50% to 90%, wide pool | 2/20 | 39 | 38-41 | 40 | 35 | 16 | 446 | 35% | 136 | 277 |
| 70% to 90%, spec pool | 0/20 | - | - | 31 | - | 16 | 384 | 44% | 68 | 348 |
| 70% to 90%, wide pool | 5/20 | 36 | 33-39 | 34 | 29 | 17 | 412 | 35% | 111 | 304 |

### E. Strong threshold, 7/wk, wide pool

| Scenario | Reached | Median wk to 100% | Range wk | Median wk all 150 done | Median wk all strong | Max cats strong at once | Real attempts | Retry share | Extras | Idle slots |
|---|---|---|---|---|---|---|---|---|---|---|
| 14d, 90% clean | 18/20 | 28 | 26-40 | 28 | 19 | 18 | 200 | 8% | 30 | 0 |
| 14d, 70% to 90% | 7/20 | 34 | 31-44 | 34 | 26 | 17 | 396 | 35% | 92 | 288 |
| 14d, 70% clean | 0/20 | - | - | 51 | - | 14 | 527 | 30% | 222 | 207 |
| 21d, 90% clean | 19/20 | 29 | 27-38 | 29 | 24 | 18 | 204 | 10% | 32 | 2 |
| 21d, 70% to 90% | 5/20 | 36 | 33-39 | 34 | 29 | 17 | 412 | 35% | 111 | 304 |
| 21d, 70% clean | 0/20 | - | - | 56 | - | 12 | 532 | 30% | 212 | 200 |
| 28d, 90% clean | 15/20 | 31 | 28-54 | 29 | 28 | 18 | 228 | 12% | 52 | 22 |
| 28d, 70% to 90% | 5/20 | 34 | 32-38 | 34 | 33 | 17 | 410 | 36% | 109 | 304 |
| 28d, 70% clean | 0/20 | - | - | 54 | - | 12 | 514 | 31% | 212 | 217 |
| 35d, 90% clean | 13/20 | 36 | 28-45 | 29 | 32 | 18 | 251 | 16% | 62 | 44 |
| 35d, 70% to 90% | 10/20 | 39 | 33-56 | 36 | 36 | 18 | 354 | 27% | 102 | 184 |
| 35d, 70% clean | 0/20 | - | - | 54 | - | 11 | 530 | 30% | 216 | 206 |

### F. Complexity-question rate, 7/wk, 90% clean, wide pool

| Scenario | Reached | Median wk to 100% | Range wk | Median wk all 150 done | Median wk all strong | Max cats strong at once | Real attempts | Retry share | Extras | Idle slots |
|---|---|---|---|---|---|---|---|---|---|---|
| asked 30% when strong, 50% when weak | 7/20 | 36 | 30-60 | 32 | 35 | 17 | 396 | 35% | 95 | 306 |
| asked 60% when strong, 90% when weak | 19/20 | 29 | 27-38 | 29 | 24 | 18 | 204 | 10% | 32 | 2 |
| asked 100% when strong, 100% when weak | 13/20 | 30 | 26-38 | 29 | 23 | 18 | 230 | 12% | 51 | 12 |

### G. Policy variants, 7/wk, 90% clean

| Scenario | Reached | Median wk to 100% | Range wk | Median wk all 150 done | Median wk all strong | Max cats strong at once | Real attempts | Retry share | Extras | Idle slots |
|---|---|---|---|---|---|---|---|---|---|---|
| SPEC as written | 6/20 | 28 | 26-31 | 28 | 24 | 17 | 363 | 43% | 50 | 351 |
| strong categories not refreshed | 5/20 | 26 | 23-29 | 26 | 21 | 17 | 338 | 49% | 18 | 389 |
| imported solves tested by same problem first | 6/20 | 30 | 27-31 | 26 | 27 | 17 | 360 | 42% | 56 | 358 |
| no import (0 of 150 seen) | 5/20 | 29 | 23-32 | 25 | 16 | 17 | 350 | 41% | 50 | 366 |

### H. Every category, 7/wk, 90% clean, by pool of extra problems

Share of runs in which the category was strong for both skills at some point.

| Category | Size | 0 extras | 100 extras | 300 extras |
|---|---|---|---|---|
| Arrays & Hashing | 9 | 85% | 100% | 100% |
| Two Pointers | 5 | 40% | 80% | 100% |
| Sliding Window | 6 | 45% | 95% | 100% |
| Stack | 7 | 55% | 100% | 100% |
| Binary Search | 7 | 70% | 95% | 100% |
| Linked List | 11 | 85% | 100% | 100% |
| Trees | 15 | 90% | 100% | 100% |
| Heap | 7 | 35% | 90% | 100% |
| Backtracking | 9 | 65% | 100% | 100% |
| Tries | 3 | 30% | 75% | 100% |
| Graphs | 13 | 95% | 100% | 100% |
| Advanced Graphs | 6 | 60% | 90% | 100% |
| 1-D DP | 12 | 95% | 100% | 100% |
| 2-D DP | 11 | 90% | 100% | 100% |
| Greedy | 8 | 80% | 100% | 100% |
| Intervals | 6 | 45% | 95% | 100% |
| Math & Geometry | 8 | 50% | 100% | 100% |
| Bit Manipulation | 7 | 45% | 80% | 100% |
| Tries, solve only | 3 | 30% | 85% | 100% |
| Tries, analysis only | 3 | 30% | 80% | 100% |
