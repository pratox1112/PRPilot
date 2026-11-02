# PRPilot dataset — data card

One row per merged pull request, with the inputs a triage model may see (only what existed
when the PR was opened) and targets taken from what maintainers did. No manual annotation.
Every number below comes from a file in `results/` recorded on a clean tree at the commit shown.

**Frozen test split** (2026-11-02, before any training or prompt tuning):
`rows_hash sha256:e5f4ddc9205b06e90dcacf53ff53d8bd1383e562717eca3e8168fa4fe42abdd8`,
9,573 PRs. Also in [config/frozen_test.toml](config/frozen_test.toml); the build refuses to
write a different test split.

## Build

All commands are read-only against GitHub, cached under `data/raw/` (gitignored) and resumable.

| Step | Command (`uv run [--group data] python -m prpilot_data ...`) | Record | Commit |
|---|---|---|---|
| A. Find PRs | `discover` | `results/data_discover.json` | `1c1a675` |
| A′. Open-time heads from GH Archive (2025-10..2026-03) | `gharchive-post-trim --budget-gib 700` | `results/data_gharchive_post_trim.json` | `65a3c54` |
| B. PR history (GraphQL) | `timeline`, then `complete` | `results/data_timeline.json`, `results/data_timeline_complete.json` | `2e30e0d`, `7a3b0f1` |
| C. Open-time diffs | `diffs-validate`, then `diffs` | `results/data_diffs_validation.json`, `results/data_diffs.json` | `57d96d8`, `4c4a6ce` |
| D. Ground truth | `truth` | `results/data_truth.json` | `fc9e735` |
| E. Dataset and splits | `dataset` | `results/data_dataset.json` | `6f74e6f` |
| Spot check (owner, 40 PRs) | `spot-check` | `data/interim/spot_check.md` (not a result) | `65a3c54` |

Output: `data/processed/prpilot_{train,val,test}.parquet` (gitignored; schema in
`prpilot_data/dataset.py`).

## Sources

| Repo | License | Role | PRs |
|---|---|---|---|
| kubernetes/kubernetes | Apache-2.0 | train / val / test by time | 9,263 |
| cilium/cilium | Apache-2.0 | train / val / test by time | 9,397 |
| pandas-dev/pandas | BSD-3-Clause | train / val / test by time | 6,582 |
| numpy/numpy | BSD-3-Clause | **held out: test only** | 3,603 |

Selection (Phase 0, task 0.4; reasons per repo in [config/repos.toml](config/repos.toml)):
permissive license, thousands of merged PRs, a type label taxonomy applied mostly by people.
Dropped: matplotlib (license), scikit-learn, prometheus, containerd (type labels mostly
bot-applied or missing), electron, angular (no PR type labels).

## Filters

- PRs **opened** 2023-01-01 to 2026-03-31 and **merged** (decision 1).
- **Default branch only** (decision 2): no backports. GitHub search's `base:main` also matches
  `maintenance/*` branches, so each PR's `baseRefName` is checked exactly; this dropped 835
  numpy backport PRs.
- **Bot-authored PRs are kept** with `author_is_bot` (decision 4): 1,554 (train 635, val 195,
  test 724).
- **Duplicates:** PRs with the same title (case and spacing ignored) and the same file set in
  more than one split. 27 groups, almost all recurring cilium PRs ("README: Update releases",
  renovate updates, "Update USERS.md"); copies outside the newest split were dropped (train
  63, val 44). The test split is never reduced. 71 duplicate groups inside one split are kept.

## Inputs (only what existed at opening, §4.4)

| Field | Source |
|---|---|
| `title` | Title at opening: the earliest `RenamedTitleEvent.previousTitle`, else the current title. |
| `body` | Body at opening: the oldest entry of the edit history, else the current body. **Slash-command lines removed** (`/kind bug` names the type, `/cc @x` names a reviewer). `Fixes #N` is kept (decision 5). |
| `author_association` | GitHub's **current** value (see limitations). |
| `base_branch` | `baseRefName`. |
| `changed_files` | From the full open-time diff. |
| `diff` | `compare/<base at open>...<head at open>`, cut to **6,000 tokens** of the student tokenizer (`Qwen/Qwen3-1.7B` @ `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`, decision 7). |

**Open-time reconstruction, validated against GH Archive `opened` payloads** (554 PRs,
`results/data_open_time_validation.json` @ `b42ff98`): title 100% (475/475), body 100%
(475/475), head commit 83.4% (462/554).

**Diff:**
- *Head at opening:* the GH Archive head where it exists (2025-10 to 2026-03). Otherwise the
  last commit committed before opening. For force-pushed PRs, the commit before the first
  force push.
- *Base at opening:* the GH Archive base, or else the default branch's tip at opening time,
  read from its first-parent history.
- *Checked against GH Archive* on the post-trim PRs (`results/data_diffs_validation.json`).
  Heads: 1,244 of 1,466 agree. Bases: 197 of 200 sampled give a byte-identical diff. This
  check ran before the default-branch filter, so its 1,466 PRs include up to 28 numpy
  maintenance-branch PRs.
- *Fallback:* when the head can't be resolved or the compare fails, the PR's **final** diff is
  used, and the row is flagged (`diff_source = final_pr_diff`). When no diff can be fetched
  at all (`diff_source = unavailable`), the row has a file list only.

**Truncation policy** (`prpilot_data/inputs.py`): diffs over budget become an outline of
every `diff --git` header and `@@` hunk header first, then as many file bodies as fit, in
order, then a truncation mark. Truncated: train 2,524, val 654, test 1,445.

## Targets (all automatic, §4.3)

- **Labels:** a label counts only if a person applied it and it was still on the PR.
  - *Bots:* accounts of type `Bot`, logins ending in `[bot]`, and the per-repo lists in
    `repos.toml`.
  - *Slash commands:* on kubernetes, `k8s-ci-robot` applies labels for a person's `/kind`
    or `/label` comment, and the label is credited to that person (22,495 labels). The other
    three repos have no command bots.
  - *Removals:* labels removed before merge are not targets.
  - *After merge:* labels added after the merge are kept and marked.
- **Type:** human labels mapped through [config/type_mappings.toml](config/type_mappings.toml)
  (hash `sha256:1817bb63…`).
  - *Primary type:* the first match in the priority order bug > feature > dependency >
    ci/build > test > docs > refactor > other.
  - *No mapped label means no type.* There's no default "other".
  - *cilium `release-note/misc`* is not mapped (decision 3).
- **Reviewers:** people who submitted a review before the merge, plus people whose review was
  requested by a person. Never the author or a bot. Excluded:
  - *Bot requests:* requests made by a bot, unless a person asked for them with
    `/cc @login` (kubernetes).
  - *CODEOWNERS:* requests whose actor is the PR author, made within **2 minutes** of opening
    or of being marked ready for review. GitHub records CODEOWNERS auto-requests with the
    author as actor. Excluded: cilium 7,350 at opening + 7,682 at ready; pandas 1,541 + 670;
    numpy 119 + 16; kubernetes 16 + 16.
  - *Team requests,* which the token can't resolve to people (cilium 26,252).

  `reviewer_evidence` says why each reviewer counts (`reviewed`, `human_request`,
  `cc_command`).
- **Issues:** the PR's closing issue references with their repository, and whether the
  open-time title or body already names the issue (`mentioned_at_open`, decision 5).
- **Risk (reverted):** the PR was later reverted, from the Phase 0 revert scan
  (`results/feasibility_reverts.json`, reverts merged up to 2026-10-26).
  - *Detection:* a merged PR whose title starts with "revert", linked by `Reverts #N`, by
    `This reverts commit <sha>`, or by a referenced PR whose title matches.
  - *Not scored:* task 0.5 found too few positives for a metric, so risk is reported, not
    scored.

## Splits (by opening date; `results/data_dataset.json`)

| Split | Rule | cilium | kubernetes | pandas | numpy | Total |
|---|---|---|---|---|---|---|
| train | opened ≤ 2024-12-31 | 5,407 | 5,698 | 4,705 | — | **15,810** |
| val | 2025-01-01 .. 2025-06-30 | 1,645 | 1,321 | 389 | — | **3,355** |
| test | 2025-07-01 .. 2026-03-31, plus all of numpy | 2,263 | 2,239 | 1,468 | 3,603 | **9,573** |

pandas val is small because pandas merged few PRs in early 2025: 43–100 per month
(`results/data_discover.json`).

### Targets per split

| | train | val | test |
|---|---|---|---|
| With a type | 12,246 | 2,435 | 5,562 |
| With a reviewer | 14,253 | 3,113 | 7,250 |
| … of which reviewed (not only requested) | 13,896 | 3,016 | 7,101 |
| With a closing issue | 3,405 | 720 | 2,190 |
| Reverted (rate) | 120 (0.76%) | 18 (0.54%) | 40 (0.42%) |

### Primary type per split

| Type | train | val | test |
|---|---|---|---|
| refactor | 3,500 | 716 | 1,405 |
| bug | 2,307 | 450 | 1,089 |
| feature | 2,071 | 536 | 890 |
| docs | 1,795 | 249 | 719 |
| ci/build | 1,136 | 295 | 604 |
| test | 816 | 151 | 391 |
| other | 566 | 19 | 416 |
| dependency | 55 | 19 | 48 |
| none | 3,564 | 920 | 4,011 |

The classes are imbalanced, and they differ by repo: kubernetes is mostly refactor
(`kind/cleanup`), pandas mostly docs, and numpy has few type labels (1,134 of 3,603 PRs).
`dependency` is nearly empty because dependency bots label their own PRs. So evaluation
reports per-class and per-repo results (macro-F1), not only accuracy. Per-repo distributions
are in `results/data_truth.json`; they were counted before duplicate removal.

## Flags

A row is `flagged` when its diff is not the open-time one, or the open-time diff is
**inflated**. Inflated means more than 3 × the merged PR's file count + 20 files, usually a
head that contained merged-in branch changes. Metrics are reported for clean and flagged
PRs separately.

| | train | val | test |
|---|---|---|---|
| Flagged | 3,444 (21.8%) | 278 (8.3%) | 794 (8.3%) |
| Final-diff fallback | 3,386 | 262 | 763 |
| No diff (file list only) | 29 | 2 | 3 |
| Inflated open-time diff | 29 | 14 | 28 |

## Hashes

| Split | rows_hash (canonical JSON) | file_sha256 (Parquet, pyarrow 25.0.1) |
|---|---|---|
| train | `sha256:3ef2e8adc06eb747feb2a515a9b74dab0a572f8e4cd454650f41a411c5273986` | `sha256:ab3b4e94983b2a02dd583fdf0b14daf96ed355b6aab6c2169c9e098dc2d0fcc2` |
| val | `sha256:65a2b30e066c1beb3bea9319e182f297f1b17cad4cbfdb25b50ede1a4a01b769` | `sha256:707a9e8db175b795522040efbd8dede6a334b77d17a25b6a880a600aea55d486` |
| test (frozen) | `sha256:e5f4ddc9205b06e90dcacf53ff53d8bd1383e562717eca3e8168fa4fe42abdd8` | `sha256:5787eaedc5f9633b80fc4ffa0cc84bff4a5d848bee9197598a7d36bc324012e7` |

Input file hashes per repo (index, timeline, diffs, truth) are in `results/data_dataset.json`
under `sources`.

## Known limitations

- **2023 heads are often unresolved.**
  - *Why:* GitHub no longer returns commits from before old force pushes.
  - *Effect:* the final-diff fallback is used, which is why train has 21.8% flagged rows.
  - *Not done:* a GH Archive query for 2023 would fix part of this (estimated ~$20–25); it
    wasn't run.
- **Head reconstruction is right about 83% of the time** outside the GH Archive months.
  About 2% of PRs get a silently wrong head (Phase 1 task 1.2).
- **`author_association` is today's value, not the one at opening.** GitHub's API has no
  history for it. Every author of a merged PR is at least `CONTRIBUTOR` now, so
  `FIRST_TIME_CONTRIBUTOR` never appears.
- **Some CODEOWNERS requests stay in.** CODEOWNERS also fires when later pushes touch owned
  files, and GitHub records no push times, so those requests still count as `human_request`
  (cilium 4,812 remain). `reviewed`-only reviewer targets avoid this.
- **Team review requests are dropped,** because they can't be resolved to people (cilium
  26,252). Reviewer targets are individuals only.
- **cilium `release-note/misc`** PRs have no type when it is their only type-like label
  (decision 3). That is one reason 4,199 cilium PRs have no type.
- **Revert labels miss** reverts pushed directly without a PR. Late-test PRs had a shorter
  time to be reverted.
- **Labels added after merge** are human targets (cilium 2,044) and mostly backport
  bookkeeping. They never map to a type.
- **GraphQL page caps:** connections that filled their first page were refetched page by
  page (`complete`). `changed_files` comes from the diff, not from GraphQL's file list.

## Spot check

On 2026-11-02 the owner reviewed 40 random train and val PRs on GitHub: cilium 14,
kubernetes 13, pandas 13, seed 0. The test split was not sampled. For each PR the owner
checked the labels and their actors, the reviewers with their evidence, and the issues. No
problems found.
