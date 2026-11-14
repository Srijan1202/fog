# References (IEEE style), with verification records

Every entry below was checked on 2026-10-18 against at least one of the following:
- the publisher record through the DOI registry (Crossref API);
- the OpenAlex scholarly index, by DOI or by title search;
- the official web page (for vendor documentation).

The "Verified via" line records which source was used. Summaries describe each work only as far as its
verified abstract or page supports. The "Relation" line is our own reading.

Nothing in this list is unverified. A reference that could not be verified was left out instead of being cited.

---

**[1]** D. Brooks and M. Martonosi, "Dynamic thermal management for high-performance microprocessors," in
*Proc. 7th Int. Symp. High-Performance Computer Architecture (HPCA)*, 2001, pp. 171–182,
doi: 10.1109/HPCA.2001.903261.
- **Verified via:** Crossref title search (DOI, pages, container); OpenAlex abstract.
- **Problem:** processors designed for worst-case power.
- **Method:** compares DTM response mechanisms and their hardware and software implementations.
- **Finding:** DTM could let CPUs be rated for lower maximum power with little loss on typical applications.
- **Relation:** our device uses firmware DTM. We study ordering under it, not DTM design.

**[2]** K. Skadron, M. R. Stan, W. Huang, S. Velusamy, K. Sankaranarayanan, and D. Tarjan,
"Temperature-aware microarchitecture," in *Proc. 30th Annu. Int. Symp. Computer Architecture (ISCA)*, 2003,
pp. 2–13, doi: 10.1145/859618.859620.
- **Verified via:** OpenAlex (DOI). The pages are from the same paper's *ACM SIGARCH Comput. Archit. News*
  vol. 31, no. 2 record, doi: 10.1145/871656.859620.
- **Problem:** temperature-aware design.
- **Method:** the HotSpot RC thermal model and runtime temperature management (frequency scaling, migrating
  computation).
- **Finding:** activity metrics are weak temperature proxies, and sensor inaccuracy affects performance.
- **Relation:** motivates our use of the firmware clock rather than a Linux proxy.

**[3]** J. Choi, C.-Y. Cher, H. Franke, H. Hamann, A. Weger, and P. Bose, "Thermal-aware task scheduling at
the system software level," in *Proc. Int. Symp. Low Power Electronics and Design (ISLPED)*, 2007,
pp. 213–218, doi: 10.1145/1283780.1283826.
- **Verified via:** the Crossref DOI record (title, authors, container, pages 213–218, 2007) and the Stony Brook
  University research portal. Crossref spells one author "Henrdrik"; the portal and other records give
  "Hendrik".
- **Problem:** on-chip hot spots.
- **Method:** OS-level scheduling that uses temporal and spatial heat slack.
- **Relation:** an OS scheduling lever, like ours. Our outcome is tail latency, with the work held fixed.

**[4]** J. Yang, X. Zhou, M. Chrobak, Y. Zhang, and L. Jin, "Dynamic thermal management through task
scheduling," in *Proc. IEEE Int. Symp. Performance Analysis of Systems and Software (ISPASS)*, 2008,
pp. 191–201, doi: 10.1109/ISPASS.2008.4510751.
- **Verified via:** Crossref title search; OpenAlex abstract.
- **Method:** a Linux-kernel thermal-aware job scheduler that exploits differences in thermal behaviour among
  jobs.
- **Findings:** 10.5–73.6 % fewer hardware DTM events, and up to 7.6 % (average 4.1 %) higher throughput.
- **Relation:** the closest prior work. It reports throughput gains on its platform. We test tail latency on a
  passively cooled SBC, with matched-work orderings.

**[5]** M. Chrobak, C. Dürr, M. Hurand, and J. Robert, "Algorithms for temperature-aware task scheduling in
microprocessor systems," in *Algorithmic Aspects in Information and Management (AAIM)*, ser. Lecture Notes in
Computer Science. Springer, 2008, pp. 120–130, doi: 10.1007/978-3-540-68880-8_13.
- **Verified via:** Crossref (container "Lecture Notes in Computer Science; Algorithmic Aspects in Information
  and Management", pages) and OpenAlex (year 2008). The LNCS volume number was not verified and is omitted.
- **Content (preprint, arXiv:0801.4238):** models unit jobs with heat contributions, notes that temperature
  depends on execution order, proves the offline problem NP-hard and gives a 2-competitive online algorithm.
- **Relation:** the formal basis for "order changes temperature".

**[6]** A. K. Coskun, T. S. Rosing, K. A. Whisnant, and K. C. Gross, "Static and dynamic temperature-aware
scheduling for multiprocessor SoCs," *IEEE Trans. Very Large Scale Integr. (VLSI) Syst.*, vol. 16, no. 9,
pp. 1127–1140, 2008, doi: 10.1109/TVLSI.2008.2000726.
- **Verified via:** OpenAlex (DOI, volume, issue, pages, abstract).
- **Findings:** an ILP schedule reduces hot spots by 35 %, spatial gradients by 85 % and thermal cycles by 61 %.
  Dynamic OS policies reduce large thermal cycles, with negligible performance overhead.
- **Relation:** thermal objectives on multiprocessor SoCs, with no tail-latency outcome.

**[7]** O. Sahin and A. K. Coskun, "Providing sustainable performance in thermally constrained mobile devices,"
in *Proc. 14th ACM/IEEE Symp. Embedded Systems for Real-Time Multimedia (ESTIMedia)*, 2016, pp. 72–77,
doi: 10.1145/2993452.2994309.
- **Verified via:** OpenAlex (DOI, venue, pages, abstract).
- **Finding:** runtime techniques sustain acceptable QoS for up to 8× longer under thermal constraints.
- **Relation:** user-visible effects of throttling on mobile devices.

**[8]** Raspberry Pi Ltd, "Raspberry Pi computer hardware: Frequency management and thermal control,"
Raspberry Pi Documentation. [Online]. Available:
https://www.raspberrypi.com/documentation/computers/raspberry-pi.html (accessed Oct. 18, 2026).
- **Verified via:** the official page, read directly.
- **States:** "When the core temperature is between 80°C and 85°C, the Arm cores will be progressively
  throttled back. If the temperature reaches 85°C, both the Arm cores and the GPU will be throttled back."

**[9]** Raspberry Pi Ltd, "Raspberry Pi OS: vcgencmd `get_throttled`," Raspberry Pi Documentation. [Online].
Available: https://www.raspberrypi.com/documentation/computers/os.html (accessed Oct. 18, 2026).
- **Verified via:** the official page, read directly.
- **Bit meanings:**
  - bit 0: under-voltage detected;
  - bit 1: Arm frequency capped;
  - bit 2: currently throttled;
  - bit 3: soft temperature limit active;
  - bits 16–19: the same four conditions "has occurred".

**[10]** J. Dean and L. A. Barroso, "The tail at scale," *Commun. ACM*, vol. 56, no. 2, pp. 74–80, 2013,
doi: 10.1145/2408776.2408794.
- **Verified via:** the Crossref DOI record.
- **Content:** why latency variability, and tail latency in particular, governs the responsiveness of
  large-scale services.

**[11]** Y. Zhang, D. Meisner, J. Mars, and L. Tang, "Treadmill: Attributing the source of tail latency through
precise load testing and statistical inference," in *Proc. ACM/IEEE 43rd Annu. Int. Symp. Computer
Architecture (ISCA)*, 2016, pp. 456–468, doi: 10.1109/ISCA.2016.47.
- **Verified via:** OpenAlex (DOI, pages, abstract).
- **Content:** load-testing pitfalls, and quantile-regression attribution of the sources of tail latency.
  Tuning based on the attribution cut P99 by 43 %.
- **Relation:** tail measurements are methodology-sensitive.

**[12]** R. J. Hyndman and Y. Fan, "Sample quantiles in statistical packages," *Amer. Statistician*, vol. 50,
no. 4, pp. 361–365, 1996, doi: 10.1080/00031305.1996.10473566.
- **Verified via:** OpenAlex (DOI, volume, issue, first page).
- **Caveat:** the last page, 365, is from the paper's standard citation. The OpenAlex record lists only the
  first page.
- **Content:** a taxonomy of sample-quantile definitions. NumPy's default is type 7.

**[13]** B. Efron, "Bootstrap methods: Another look at the jackknife," *Ann. Statist.*, vol. 7, no. 1,
pp. 1–26, 1979, doi: 10.1214/aos/1176344552.
- **Verified via:** OpenAlex.

**[14]** S. Holm, "A simple sequentially rejective multiple test procedure," *Scand. J. Statist.*, vol. 6,
pp. 65–70, 1979. [Online]. Available: https://www.jstor.org/stable/4615733
- **Verified via:** OpenAlex (JSTOR identifier 10.2307/4615733, volume, pages). The issue number is not listed
  there and is omitted.

**[15]** N. Cliff, "Dominance statistics: Ordinal analyses to answer ordinal questions," *Psychol. Bull.*,
vol. 114, no. 3, pp. 494–509, 1993, doi: 10.1037/0033-2909.114.3.494.
- **Verified via:** OpenAlex.

**[16]** T. Hoefler and R. Belli, "Scientific benchmarking of parallel computing systems: Twelve ways to tell
the masses when reporting performance results," in *Proc. Int. Conf. High Performance Computing, Networking,
Storage and Analysis (SC)*, 2015, pp. 1–12, doi: 10.1145/2807591.2807644.
- **Verified via:** Crossref title search.

**[17]** T. Mytkowicz, A. Diwan, M. Hauswirth, and P. F. Sweeney, "Producing wrong data without doing anything
obviously wrong!," in *Proc. 14th Int. Conf. Architectural Support for Programming Languages and Operating
Systems (ASPLOS)*, 2009, pp. 265–276, doi: 10.1145/1508244.1508275.
- **Verified via:** OpenAlex.

**[18]** B. A. Nosek, C. R. Ebersole, A. C. DeHaven, and D. T. Mellor, "The preregistration revolution," *Proc.
Natl. Acad. Sci. USA*, vol. 115, no. 11, pp. 2600–2606, 2018, doi: 10.1073/pnas.1708274114.
- **Verified via:** OpenAlex.

---

## Coverage of the literature review

| Topic | References |
|---|---|
| thermal-aware scheduling and ordering | [3]–[6] |
| DVFS and DTM | [1], [2] |
| Raspberry Pi thermal behaviour (official) | [8], [9] |
| edge and mobile performance under thermal constraints | [7] |
| tail latency and percentile estimation | [10]–[12] |
| statistics | [13]–[15] |
| measurement rigour, reproducibility and preregistration | [16]–[18] |

**Gaps in this review:**
- **Raspberry Pi studies:** we found only vendor and hobbyist measurements of Pi 4 cooling and throttling, not
  peer-reviewed ones. None is cited as evidence.
- **Negative-result literature:** coverage in systems research is limited to [16]–[18].
- **Recent preprints:** recent arXiv preprints on sustained LLM inference on mobile devices were seen but not
  cited, because their peer-review status was not confirmed.

A broader search (Google Scholar, IEEE Xplore, ACM DL) is recommended before external submission.
