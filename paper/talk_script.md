# Talk script: explaining the TDAS project to my teacher

About 10 minutes, plus questions. Read the plain text aloud. Lines in *[brackets]* are notes for me, not for
saying. Keep the paper PDF and the website open to show the figures.

---

## 1. The idea in one sentence (about 30 seconds)

"Ma'am, my project asked a simple question. On a small computer like a Raspberry Pi, which has no fan, does
the **order** in which you run jobs change how fast they finish?"

"The idea is that if all the heavy jobs run together, the chip gets hot and slows itself down. If you mix
heavy and light jobs, maybe it stays cooler and the jobs finish faster."

---

## 2. Why this matters (about 1 minute)

"Small devices like phones and Raspberry Pis have no fan. When the chip gets too hot, it protects itself by
lowering its speed. This is called **thermal throttling**."

"So a job can take longer just because the chip is hot, even though the job itself is the same."

"Many systems care about the **slowest** jobs, not the average one. We call that **tail latency**. A common
measure is **P99**: the time below which 99 % of jobs finish. If ordering could keep the chip cooler, it might
make those slowest jobs faster. That's what I wanted to test."

---

## 3. How I set it up (about 2 minutes)

"I used a **Raspberry Pi 4**, a small single-board computer, with no fan and no heatsink."

"I made two kinds of jobs:"

- "A **HOT** job keeps all four processor cores fully busy. It does heavy matrix multiplication."
- "A **COOL** job does less work and rests in between."

"The important part: when the chip is cold, **both jobs take the same time**, about 196 milliseconds. I
checked this carefully: the difference is only 0.05 %. So if they finish at different speeds later, it can
only be because of heat, not because one job is longer."

"Then I made a list of **600 jobs: 300 HOT and 300 COOL**, and ran the same 600 jobs in four different
orders:"

1. "**Hot-first:** all the hot jobs, then all the cool ones."
2. "**Cool-first:** the opposite."
3. "**Alternating:** hot, cool, hot, cool."
4. "**Random:** shuffled."

"I ran each order **10 times**. That's 40 runs and 24,000 jobs in total, in about 3 hours. The runs were mixed
in a balanced schedule, so no order always ran at the same time of day."

"Before each run I let the chip cool down. Then I warmed it up to **77 °C**, just below the temperature where
it starts slowing down. That way every run started from the same point."

---

## 4. How I made sure the experiment was fair (about 1.5 minutes)

"I wanted this to be an honest test, so I did three things."

"**First, I wrote down the rules before running the experiment.** This is called **preregistration**. I
decided in advance exactly what I would measure, which statistical test I would use, and what result would
count as success or failure. So I could not change the rules after seeing the data."

"I split the question into small checks called **gates**, G1 to G6. If an important gate fails, the idea is
rejected."

"**Second, safety and supervision.** A separate watchdog program watched the temperature. It would stop
everything at 85 °C. I also learned something here: my first watchdog itself slowed the jobs down, because it
kept starting small programs. I found that problem, fixed it, and then rechecked the jobs."

"**Third, measuring correctly.** I found that the normal Linux setting that reports the CPU speed did **not**
show the slowdown at all. It always said 1800 MHz. So I read the real speed directly from the chip's firmware
instead."

---

## 5. What I found (about 2 minutes)

*[Show Fig. 1–2 from the paper, or the website.]*

"**Step 1: the chip has a throttling point.** At about **80.8 °C**, the chip starts lowering its speed. This
matches the official Raspberry Pi documentation, which says throttling happens between 80 and 85 °C."

"**Step 2: heat really does slow jobs down.** Below 80 °C the job time is flat, about 196 ms. Above it, jobs
take 208 to 212 ms. That's about 6–8 % slower. So heat clearly matters."

*[Show Fig. 3–4: the temperature curves.]*

"**Step 3: the order changes the temperature.** In hot-first, the chip stayed above 80.8 °C for a median of
**13.3 seconds** per run. In alternating and random it stayed above for **zero seconds**. So yes, the order
clearly changes how hot the chip gets."

*[Show Fig. 5: the P99 plot.]*

"**Step 4, the main question: did the order make the slowest jobs faster?** The answer is **no**. The P99 time
was between **201.3 and 202.1 ms** for all four orders. The difference was less than 1 millisecond, and the
statistical test said this difference could easily be chance. The average time and the number of jobs per
second didn't change either."

"So my final, pre-decided verdict is **LIKELY KILL**. Ordering changes the temperature, but it does not give a
useful speed-up in this setup."

---

## 6. Why the temperature change didn't help (about 1 minute)

"I looked into why, and kept it separate from the main result. These were extra checks, not part of the
original plan."

- "The slowdown is small at first. Jobs that started above 80.8 °C were only **0.7 % slower** in my runs. The
  big slowdown only comes after several minutes of continuous heavy load, and my runs were only 2 minutes
  long."
- "Only about **2–3 jobs out of 600** were really slow. P99 is roughly the 7th-slowest job, so a few slow jobs
  don't move it."
- "I also noticed that in the first hour of the session the board was hotter. In that period, hot-first and
  cool-first runs **were** slower, about 205–211 ms. I don't know why the board was hotter, because I didn't
  measure room temperature. That gives an idea for a future experiment, but it doesn't change my result."

---

## 7. Why a "no" answer is still useful (about 45 seconds)

"A negative result is still a real result. I showed something important: **changing the temperature does not
automatically mean better performance**. Many people assume that if a system is 'thermal-aware', it must be
faster. My experiment shows that it depends on how long the heat lasts and how many jobs it affects."

"Because I wrote the rules first, I can't be accused of choosing the result I liked."

---

## 8. Limitations, said honestly (about 45 seconds)

"There are limits to what I can claim:"

- "I used **one device**, one type of job, and one starting temperature."
- "With 10 runs per order, very small differences, under about 1 ms, could be missed. Not finding a
  difference doesn't prove there is none."
- "I didn't record room temperature, and I didn't re-confirm the cooling setup on the test day."

---

## 9. What I produced (about 30 seconds)

- "A **research paper** in IEEE format, with a PDF and a Word version, and 18 checked references."
- "A **website** that shows all the findings with charts."
- "All the **code and records**. Every number in the paper was recomputed from the raw data by a separate
  script, and the result matched exactly."

"Thank you, ma'am. I'm happy to answer questions."

---

## Possible questions and simple answers

**Q: Why the Raspberry Pi?**
"It's cheap, common in edge computing, and has no fan, so throttling happens easily and can be measured."

**Q: How do you know the jobs were really the same?**
"I tested 40 pairs before the experiment. When cold, HOT and COOL differ by only 0.05 %, so any difference later
comes from heat."

**Q: What is a 'gate'?**
"A yes/no check decided in advance. For example, G4 asks whether the order changes the temperature (yes), and G5
asks whether the order changes the slowest jobs (no)."

**Q: What does LIKELY KILL mean?**
"It's the verdict I set before the experiment. It means the physical effect is real, but it doesn't help
performance, so the idea is very likely not worth pursuing in this form."

**Q: Which statistics did you use?**
"I compared medians between orders and used a method called **bootstrap** to get confidence intervals. Because
I compared six pairs of orders, I used the **Holm correction**, so I wouldn't find a 'difference' just by
chance from testing many pairs."

**Q: Could the result be different on another device?**
"Yes, it could. That's a limitation. Longer hot periods, other devices, or real AI workloads would each need a
new experiment with new rules written first."

**Q: What would you do next?**
"Run longer hot periods, which make the slowdown bigger; control the room temperature; and run more repetitions
to catch small effects."

**Q: Did you use any AI tools?**
*[Answer honestly.]* "Yes. I used Claude, an AI assistant, to help write and test the code, run the analysis,
and draft the paper and the website. I made the design decisions and approved each step. One amendment (A5)
was approved by the assistant while I was away, and I reviewed and accepted it afterwards."

---

## Key numbers to remember

| What | Number |
|---|---|
| Throttling point (knee) | 80.8 °C |
| Job time when cold | ~196 ms |
| Job time when very hot | 208–212 ms (6–8 % slower) |
| HOT vs COOL difference when cold | 0.05 % |
| Runs | 4 orders × 10 = 40 runs, 24,000 jobs |
| Time above 80.8 °C: hot-first vs alternating | 13.3 s vs 0 s |
| P99 across all orders | 201.3–202.1 ms (under 1 ms apart) |
| Verdict | LIKELY KILL |
