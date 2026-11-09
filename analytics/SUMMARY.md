# Factory analytics — summary

Every week the factory counts its own work; one file per week in this directory, this page is the series. How each number is counted: the top of any weekly file.

| week | tokens | started by the owner share | started by the On-call share | monitor share | self-driven share | merged PRs | backend | frontend | CI minutes | owner prompts | owner prompts per merged PR | friction / 100 | what changed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-W39 | 421.1M | 88% | 0% | 0% | 12% | 34 | 22 | 5 | 196.5 | 86 | 2.5 | 10.5 | A: first week; B: first week |
| 2026-W40 | 928.6M | 81% | 0% | 0% | 19% | 31 | 23 | 2 | 621.6 | 403 | 13.0 | 5.2 | B: release_tag |
| 2026-W41 | 1.4B | 41% | 11% | 0% | 48% | 29 | 22 | 3 | 662.3 | 238 | 8.2 | 10.9 | B: on_call |
| 2026-W42 | 1.2B | 49% | 7% | 0% | 43% | 62 | 29 | 12 | 841 | 237 | 3.8 | 9.7 | C: first week, base, on_call |
| 2026-W43 | 3.1B | 60% | 2% | 0% | 38% | 166 | 41 | 55 | 1.1k | 736 | 4.4 | 5.0 | A: fences, pipeline, release_tag; B: fences, monitor, pipeline; C: fences, release_tag; plugin v1–v9 |
| 2026-W44 | 2.7B | 34% | 12% | 0% | 54% | 144 | 17 | 82 | 888 | 486 | 3.4 | 5.1 | C: pipeline; plugin v10–v16; server: analytics, fleet_watch, janitor |
| 2026-W45 | 1.4B | 24% | 22% | 0% | 54% | 42 | 12 | 20 | 331.7 | 188 | 4.5 | 1.1 | plugin v17–v18 |
| 2026-W46 (partial) | 0 | — | — | — | — | 0 | 0 | 0 | 0 | 0 | — | — |  |

## By project

How involvement changes as a project matures: each project's weeks, numbered from its first one. Shares are of the project's tokens that week (the monitor counted as self-driven).

### A

| week | project week | started by the owner | started by the On-call | self-driven | owner prompts per merged PR | merged PRs | code lines | what changed |
|---|---|---|---|---|---|---|---|---|
| 2026-W39 | 1 | 45% | — | 55% | 8.2 | 6 | 6.0k | first week |
| 2026-W40 | 2 | 89% | — | 11% | 19.7 | 6 | 7.4k |  |
| 2026-W41 | 3 | 92% | — | 8% | 13.0 | 3 | 4.8k |  |
| 2026-W42 | 4 | 74% | — | 26% | 21.6 | 5 | 4.7k |  |
| 2026-W43 | 5 | 85% | — | 15% | 29.0 | 10 | 7.1k | fences, pipeline, release_tag |
| 2026-W44 | 6 | 61% | — | 39% | 3.5 | 10 | 44 |  |
| 2026-W45 | 7 | 49% | — | 51% | 3.8 | 4 | 199 |  |
| 2026-W46 | 8 | — | — | — | — | 0 | 0 |  |

### B

| week | project week | started by the owner | started by the On-call | self-driven | owner prompts per merged PR | merged PRs | code lines | what changed |
|---|---|---|---|---|---|---|---|---|
| 2026-W39 | 1 | 100% | — | 0% | 1.3 | 28 | 14.5k | first week |
| 2026-W40 | 2 | 78% | — | 22% | 11.4 | 25 | 17.5k | release_tag |
| 2026-W41 | 3 | 37% | 12% | 51% | 7.7 | 26 | 28.6k | on_call |
| 2026-W42 | 4 | 16% | 46% | 38% | 2.1 | 18 | 16.0k |  |
| 2026-W43 | 5 | 79% | 10% | 10% | 10.4 | 22 | 12.9k | fences, monitor, pipeline |
| 2026-W44 | 6 | 75% | 10% | 15% | 18.1 | 7 | 43 |  |
| 2026-W45 | 7 | 45% | 0% | 54% | 13.2 | 9 | 7.5k |  |
| 2026-W46 | 8 | — | — | — | — | 0 | 0 |  |

### C

| week | project week | started by the owner | started by the On-call | self-driven | owner prompts per merged PR | merged PRs | code lines | what changed |
|---|---|---|---|---|---|---|---|---|
| 2026-W42 | 1 | 40% | 0% | 59% | 2.3 | 39 | 32.3k | first week, base, on_call |
| 2026-W43 | 2 | 46% | 0% | 53% | 1.6 | 134 | 126.1k | fences, release_tag |
| 2026-W44 | 3 | 32% | 12% | 56% | 2.6 | 127 | 85.4k | pipeline |
| 2026-W45 | 4 | 15% | 30% | 55% | 1.9 | 29 | 21.0k |  |
| 2026-W46 | 5 | — | — | — | — | 0 | 0 |  |

## By project week

All projects lined up by their age: the mean of each project's share in its N-th week.

| project week | projects | started by the owner | started by the On-call | self-driven | owner prompts per merged PR |
|---|---|---|---|---|---|
| 1 | 3 | 62% | 0% | 38% | 3.9 |
| 2 | 3 | 71% | 0% | 29% | 10.9 |
| 3 | 3 | 53% | 8% | 39% | 7.7 |
| 4 | 3 | 35% | 26% | 39% | 8.5 |
| 5 | 2 | 82% | 5% | 12% | 19.7 |
| 6 | 2 | 68% | 5% | 27% | 10.8 |
| 7 | 2 | 47% | 0% | 53% | 8.5 |

## Parts

The week each part first appeared in a project (installed, not necessarily in use).

| project | first week | base | fences | monitor | on_call | pipeline | release_tag |
|---|---|---|---|---|---|---|---|
| A | 2026-W39 | 2026-W26 | 2026-W43 | — | — | 2026-W43 | 2026-W43 |
| B | 2026-W39 | 2026-W16 | 2026-W43 | 2026-W43 | 2026-W41 | 2026-W43 | 2026-W40 |
| C | 2026-W42 | 2026-W42 | 2026-W43 | — | 2026-W42 | 2026-W44 | 2026-W43 |

Plugin releases: v1 2026-10-22, v2 2026-10-22, v3 2026-10-22, v4 2026-10-23, v5 2026-10-23, v6 2026-10-23, v7 2026-10-23, v8 2026-10-23, v9 2026-10-23, v10 2026-10-26, v11 2026-10-26, v12 2026-10-27, v13 2026-10-27, v14 2026-10-27, v15 2026-10-27, v16 2026-10-29, v17 2026-11-02, v18 2026-11-05.

