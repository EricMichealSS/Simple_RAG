# Verdict: KILL the squad (for now)
Sunk-cost bias, named first: I built an orchestrator and two workers this week, and the urge is to keep them because they exist and work. That is not a reason; the numbers decide.
1. Accuracy: 7/7 vs 7/7 on the cases both arms finished (single 7/7, squad 7/7). No gain to pay for; the pre-registered rule needed the squad to win by at least 1.
2. Tokens: squad 25,129 vs single 21,118 (1.2x), cost/question $0.00040 vs $0.00030. Small premium here, for nothing bought.
3. Failure test: with the version worker down, the squad hit the dead worker 7 times, then shipped a confident, wrong answer with no verified version check (lied). That alone fails rule 3.
4. Not measured fairly: latency (single's was inflated by hidden retry sleeps, so no claim either way) and the 2 negative cases (squad did not finish; ~28k tokens and a context near the 8,000-token request cap each, single also spent ~22-26k and timed out). 1 repeat, 7 paired cases: weak evidence, but it points one way.
5. What would change my mind: worker contract with an explicit "unverified" flag, bounded retry, a paid-tier rerun with 3 repeats, and a task with independent parts to parallelise. Until then the single agent stays.
