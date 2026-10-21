# Showcase desk-pass run log

One entry per scheduled run, appended by the run. Newest at the bottom.

## 2026-10-21 — desk pass IDN VNM THA MYS PHL SGP KHM LAO MMR ARE

Run notes / interpretations: `add_repo` tool was not available in this session; the repo was already cloned with an origin remote, so the run used that clone (push access tested only by safe_push.sh). Each country was researched by a parallel sub-agent under a shared brief restating SPEC.md; all ten files pass `check_country.py`. 117 findings total; all unreviewed.

- **IDN** (19 findings, 10 searches): Huawei core/RAN deals at XL, Telkomsel, Indosat; HMN Tech building PASELA cable for Telkom; Firmus/DayOne 360 MW Batam AI campus with Nvidia + PLN Batam power (bundle IDN-B1). **Reversal IDN-014**: West Java governor halted BDx 640 MW Jatiluhur campus over permits (2026-09-30, S2+S3, corroborated).
- **VNM** (13, 10): Huawei $23M 5G consortium contract and ZTE >$20M antenna contracts in 2025 (Reuters only, low); G42–FPT/Viet Thai data-centre framework (Feb 2026, S1+S3). **Reversal VNM-001**: Viettel excluded Huawei from 5G (2019). Many Vietnamese outlets treated as S4.
- **THA** (15, 10): True 5G RAN Ericsson+ZTE; BOI data-centre approvals (Google, GDS, DAMAC, etc.); SIAM.AI Nvidia H100 cloud; ThaiLLM. **Reversal THA-010**: government paused 49 data-centre builds and froze 117 approvals (2026-09-04, S2+S3, corroborated; pole null — projects not named).
- **MYS** (13, 10): U Mobile picked Huawei+ZTE for second 5G network; YTL Nvidia AI data centre in Johor complete; RM2bn sovereign-AI programme reportedly leaning to Huawei Ascend (Sept 2026, single anonymous report, low). **Reversal MYS-004**: MITI disowned a Huawei-Ascend sovereign AI launch (May 2025, corroborated).
- **PHL** (11, 9): PLDT/VITRO 50 MW AI-ready DC with Nvidia; DICT–Google Cloud multi-year AI deal (2026); DITO (China Telecom stake). **Reversal PHL-011**: Safe Philippines CCTV (CITCC/Huawei) shelved 2021 (single S3, low).
- **SGP** (11, 9): SEA-LION v4 national model rebuilt on Alibaba Qwen (Nov 2025, two S2, corroborated); OpenAI S$300M MOU with MDDI (May 2026, S1+S2); Google frontier-AI MOU. **Reversal SGP-010**: AI Singapore moved off Meta Llama (single S2, low).
- **KHM** (9, 10): HMN Tech-built SHV-HK cable landed July 2026 (two S2); Huawei MOUs with Cellcard and ByteDC; China-donated cameras. No reversals found.
- **LAO** (9, 10): LaoSat-1 (CAST-built, China Exim-financed, two S2); almost everything else from Lao state media (S4, low). Silicon not searched (budget). No reversals found.
- **MMR** (8, 10): Huawei safe city Naypyidaw/Mandalay; Geedge Networks censorship system (leak-based, low). **Reversals MMR-005/006/007**: Telenor exit (2022), Ooredoo exit deal (2022), SpaceX disabling ~2,500 Starlink kits at scam compounds (Oct 2025).
- **ARE** (9, 10): Stargate UAE 1 GW cluster under construction by Khazna/G42 (tier 4, corroborated); OpenAI for Countries deal; Huawei builds e& 5G. **Reversal ARE-004**: G42 agreed to strip Huawei/Chinese equipment as condition of Microsoft investment (2024, single S3, low).

Unresolved: power layer mostly not searched; operator-level vendor shares unconfirmed across the region; controllers of DayOne, AirTrunk, BDx, Huading unresolved (several `pole: null`); SGP WAICO status still rests on an S5 lead in `data/diplomatic_alignment.json`. Per-country questions are in each record's `research_gaps`.
