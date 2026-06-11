# Controlled Workflow for Writing High-Quality Scientific Papers with AI Assistance

Version: 2026-06-10
Purpose: controlled preparation of scientific manuscripts, conference papers, research proposals, and supervisor-review drafts with AI assistance.
Primary use case: engineering and simulation papers with software artifacts, experiments, figures, tables, reproducibility requirements, and supervisor or co-author review.

## 0. Source alignment

This workflow is built around four constraints.

1. A scientific paper is not a free text artifact. It is the final compact representation of a verified research chain.
2. The paper must follow the academic chain: context, contradiction, problem, gap, goal, hypothesis or research question, objectives, method, evidence, novelty, limitations, conclusion.
3. The paper must follow the engineering chain: requirements, design alternatives, selected architecture, implementation boundary, test program, results, verification, reproducibility artifacts.
4. AI is allowed only as a controlled assistant. AI is not the source of facts, results, citations, figures, novelty, or final decisions.

The workflow integrates three principles:

1. The manuscript is generated from controlled source files.
2. The introduction and conclusion must correspond to each other.
3. The body of the paper must contain proof, not narrative decoration.

For a master's-level or research-grade work, the manuscript must not only show that something was implemented. It must show why the selected approach is justified against alternatives and why the evidence supports the chosen interpretation.

---

## 1. Core principle

AI is not used to generate a scientific paper from scratch.

AI is used as a controlled assistant for:

* structuring the scientific story;
* checking terminology;
* rewriting sections;
* reviewing claims;
* checking consistency;
* identifying weak arguments;
* improving readability;
* detecting unsupported statements;
* auditing figures, tables, citations, metrics, and reproducibility artifacts.

The author remains responsible for:

* facts;
* numerical results;
* claims;
* terminology;
* interpretation;
* literature positioning;
* final manuscript decisions;
* research ethics;
* venue compliance;
* supervisor and co-author alignment.

The writing process starts from verified facts, traceable results, stable terminology, a defensible scientific story, and a documented research design.

No AI-generated text is inserted into the manuscript without checking against the control files.

---

## 2. Paper logic before manuscript text

### 2.1 Academic chain

Before any section drafting, the author writes the paper logic in the following sequence:

1. Context: what technical or scientific area is being addressed.
2. Practical relevance: why the problem matters now.
3. Scientific or engineering contradiction: what mismatch exists between the desired state and the current state.
4. Research problem: what unresolved question follows from the contradiction.
5. Research gap: what prior work does not cover.
6. Object of study: what system, process, or phenomenon is studied.
7. Subject of study: which property, relation, metric, mechanism, or aspect of the object is studied.
8. Goal: what result the paper aims to obtain.
9. Hypothesis or research question: what is being tested or answered.
10. Objectives: the concrete steps needed to reach the goal.
11. Method: how the objectives are addressed.
12. Evidence: what data, experiments, simulations, figures, tables, or artifacts support the claims.
13. Novelty: what is new relative to prior work.
14. Limitations: what the work does not prove.
15. Conclusion: what is supported by the evidence.

If this chain is not clear, drafting stops.

### 2.2 Five-sentence scientific story

Before writing any section, define the paper in five sentences.

1. What system, method, or phenomenon is studied?
2. Why is the problem important?
3. What is missing in existing studies?
4. What does this paper contribute?
5. What evidence supports the contribution?

The paper can move to drafting only after these five sentences are clear without internal project terms.

### 2.3 Introduction to conclusion alignment

The Introduction must define the problem, gap, goal, contribution, and evidence promised by the paper.

The Conclusion must answer only what was promised in the Introduction and supported in the body.

The Conclusion must not introduce:

* new claims;
* new numbers;
* new terms;
* new citations;
* new limitations;
* stronger novelty than the Introduction.

### 2.4 Master's-level contribution rule

A bachelor's-level manuscript may demonstrate implementation ability.

A master's-level or research-grade manuscript must also demonstrate selection ability:

* alternatives were considered;
* the selected method is justified;
* the criteria for selection are explicit;
* the evaluation method is reproducible;
* the limitations of the selected method are stated.

For engineering papers, this rule is implemented through `DESIGN_DECISIONS.md`.

---

## 3. Source files first, manuscript second

The manuscript is a generated artifact.

The control files are the source of truth.

If a number, term, claim, limitation, novelty statement, figure, table, citation, artifact statement, design decision, or experimental assumption changes in the manuscript, the corresponding control file must be updated first.

The manuscript must never contain:

* a numerical value absent from `RESULTS_PROVENANCE.md`;
* a technical term absent from `TERMS.md`;
* a scientific claim absent from `CLAIMS.md`;
* a novelty statement absent from `NOVELTY.md`;
* a limitation absent from `LIMITATIONS.md`;
* a figure or table absent from `FIGURE_TABLE_PLAN.md`;
* a planned citation absent from `REFERENCES_PLAN.md`;
* a design-choice claim absent from `DESIGN_DECISIONS.md`;
* a method or experiment not covered by `RESEARCH_DESIGN.md` or `EXPERIMENTAL_PROTOCOL.md`;
* a data/code availability statement inconsistent with `ARTIFACTS.md` and `VENUE_REQUIREMENTS.md`.

The workflow requires traceability, not long documentation.

For a 6-page conference paper, each control file should normally fit within 0.5 to 2 pages unless the venue, supervisor, co-authors, or project requirements demand more detail.

Control files may be short if they clearly record the required decisions.

---

## 4. Workflow modes

### 4.1 Full mode

Used for:

* journal papers;
* IEEE or ACM conference papers;
* supervisor review;
* papers with numerical experiments;
* papers with figures, tables, code, datasets, or reproducibility claims;
* papers with implementation-based novelty;
* grant-related papers;
* papers with several co-authors.

Required before drafting:

* `VENUE_REQUIREMENTS.md`
* `PROJECT_CONTEXT.md`
* `PAPER_PROPOSAL.md`
* `RESEARCH_DESIGN.md`
* `FACTS.md`
* `RESULTS_PROVENANCE.md`
* `EXPERIMENTAL_PROTOCOL.md`
* `DESIGN_DECISIONS.md`
* `TERMS.md`
* `LITERATURE_MATRIX.md`
* `REFERENCES_PLAN.md`
* `FIGURE_TABLE_PLAN.md`
* `CLAIMS.md`
* `NOVELTY.md`
* `LIMITATIONS.md`
* `STYLE.md`
* `OWNER_MATRIX.md`

Required before supervisor review or submission:

* `REFERENCES_AUDIT.md`
* `AI_USE.md`
* `CHANGELOG.md`
* `ARTIFACTS.md`
* `INTRO_CONCLUSION_MAP.md`

### 4.2 Standard mode

Used for:

* 4 to 6 page conference papers;
* compact workshop papers;
* papers with limited but real numerical evidence;
* supervisor review drafts where the core evidence is already stable.

Required before drafting:

* `VENUE_REQUIREMENTS.md`
* `PAPER_PROPOSAL.md`
* `RESEARCH_DESIGN.md`
* `FACTS.md`
* `RESULTS_PROVENANCE.md`
* `TERMS.md`
* `LITERATURE_MATRIX.md`
* `REFERENCES_PLAN.md`
* `FIGURE_TABLE_PLAN.md`
* `CLAIMS.md`
* `NOVELTY.md`
* `LIMITATIONS.md`
* `STYLE.md`
* `AI_USE.md`

Optional but recommended:

* `PROJECT_CONTEXT.md`
* `DESIGN_DECISIONS.md`
* `EXPERIMENTAL_PROTOCOL.md`
* `ARTIFACTS.md`
* `CHANGELOG.md`
* `OWNER_MATRIX.md`

### 4.3 Minimal mode

Used for:

* short abstracts;
* early drafts;
* 2 to 4 page papers without detailed numerical experiments;
* internal concept notes;
* first supervisor discussion drafts.

Required before drafting:

* `VENUE_REQUIREMENTS.md`
* `PAPER_PROPOSAL.md`
* `FACTS.md`
* `TERMS.md`
* `CLAIMS.md`
* `LIMITATIONS.md`
* `STYLE.md`
* `AI_USE.md`, with Draft or Checked status.

Optional in Minimal mode:

* `RESULTS_PROVENANCE.md`, if numerical values appear;
* `LITERATURE_MATRIX.md`, if related work is included;
* `REFERENCES_PLAN.md`, if citations are used;
* `FIGURE_TABLE_PLAN.md`, if figures or tables appear;
* `NOVELTY.md`, if novelty is claimed;
* `CHANGELOG.md`, if several reviewed versions are expected.

### 4.4 Mode selection rule

Use Full mode if the manuscript contains any of the following:

* experimental results;
* numerical comparison;
* simulation outputs;
* figures or tables used as evidence;
* reproducibility claims;
* novelty claims based on implementation or experiments;
* code, dataset, or tool availability statement;
* target submission to a conference or journal;
* grant-related claims;
* supervisor or co-author review before submission.

---

## 5. Metadata block for every control file

Every control file starts with the following metadata block:

```markdown
# FILE_NAME.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:
```

Definitions:

* `Status`: Draft, Checked, Locked, or Revised.
* `Owner`: person responsible for keeping the file correct.
* `Last checked`: date of the last human check in `YYYY-MM-DD` format.
* `Applies to manuscript version`: manuscript version covered by the file.
* `Related manuscript sections`: sections affected by the file.
* `Related artifacts`: repositories, logs, scripts, figures, tables, datasets, notes, or supervisor comments affected by the file.

Example:

```markdown
# RESULTS_PROVENANCE.md

Status: Locked
Owner: Andrey Fizulin
Last checked: 2026-06-10
Applies to manuscript version: v3
Related manuscript sections: Abstract, Results, Discussion, Tables I-II
Related artifacts: repository commit ..., results/run_..., scripts/analyze.py
```

---

## 6. Control file statuses

Each control file has one of four statuses.

### Draft

The file is incomplete and unstable.

The manuscript must not be drafted from Draft files except for exploratory notes.

### Checked

The file has been reviewed by the owner.

The content is usable for planning, but not yet stable for final drafting.

### Locked

The file is approved for drafting or review.

AI models may use the file as a stable source.

### Revised

The file changed after manuscript drafting.

Any manuscript section affected by the Revised file requires re-audit.

### Status rule before drafting

Before section-level rewriting in Full mode, the following files must be Locked:

* `VENUE_REQUIREMENTS.md`
* `PROJECT_CONTEXT.md`
* `PAPER_PROPOSAL.md`
* `RESEARCH_DESIGN.md`
* `FACTS.md`
* `RESULTS_PROVENANCE.md`
* `EXPERIMENTAL_PROTOCOL.md`
* `DESIGN_DECISIONS.md`
* `TERMS.md`
* `LITERATURE_MATRIX.md`
* `REFERENCES_PLAN.md`
* `FIGURE_TABLE_PLAN.md`
* `CLAIMS.md`
* `NOVELTY.md`
* `LIMITATIONS.md`
* `STYLE.md`

### Special status rule for `REFERENCES_AUDIT.md`

`REFERENCES_AUDIT.md` cannot be Locked before the manuscript is assembled.

Before drafting:

* not required;
* or Draft, if preliminary checks are recorded.

Before supervisor review:

* Checked or Locked.

Before submission:

* Locked and consistent with the final manuscript.

### Special status rule for `AI_USE.md`

`AI_USE.md` is updated after every substantial AI-assisted operation.

Before drafting:

* Draft or Checked.

Before supervisor review:

* Locked.

Before submission:

* Locked and consistent with `VENUE_REQUIREMENTS.md`.

### Special status rule for `CHANGELOG.md`

`CHANGELOG.md` is updated after every substantial scientific, structural, or evidential manuscript change.

Before drafting:

* optional or Draft.

Before supervisor review:

* Checked.

Before submission:

* Locked, if required by the project or co-authors.

---

## 7. Control file conflict rule

If two control files conflict, drafting stops until the conflict is resolved.

The stricter interpretation prevails temporarily.

Priority order:

1. `VENUE_REQUIREMENTS.md`
2. `PROJECT_CONTEXT.md`
3. `RESULTS_PROVENANCE.md`
4. `EXPERIMENTAL_PROTOCOL.md`
5. `LIMITATIONS.md`
6. `CLAIMS.md`
7. `NOVELTY.md`
8. `RESEARCH_DESIGN.md`
9. `DESIGN_DECISIONS.md`
10. `TERMS.md`
11. `STYLE.md`
12. manuscript text

Rules:

* `VENUE_REQUIREMENTS.md` is the authority for page limit, format, anonymization, citation style, AI disclosure, and data/code availability.
* `PROJECT_CONTEXT.md` is the authority for project scope, permitted disclosure, grant-related constraints, and supervisor-defined boundaries.
* `RESULTS_PROVENANCE.md` is the authority for numerical values.
* `EXPERIMENTAL_PROTOCOL.md` is the authority for what was tested and how.
* `LIMITATIONS.md` restricts claims. It must not be weakened to fit an overstated claim.
* If `NOVELTY.md` claims more than `CLAIMS.md` and `LIMITATIONS.md` support, `NOVELTY.md` must be narrowed.
* If the manuscript contains stronger wording than the control files allow, the manuscript text must be corrected.

---

## 8. Required control files

### 8.1 `VENUE_REQUIREMENTS.md`

Contains submission constraints:

* venue name;
* paper type;
* page limit;
* template;
* required sections;
* citation style;
* double-blind or single-blind rules;
* AI-use disclosure policy;
* data/code availability policy;
* deadline;
* topic fit;
* explicit scope restrictions;
* formatting constraints;
* figure and table constraints;
* supplementary material rules.

No manuscript drafting starts before the target venue requirements are recorded.

Template:

```markdown
# VENUE_REQUIREMENTS.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

Venue:
- ...

Paper type:
- ...

Page limit:
- ...

Template:
- ...

Required sections:
- ...

Review mode:
- single-blind / double-blind / unclear

Citation style:
- ...

AI-use disclosure policy:
- required / not required / unclear

Data/code availability policy:
- required / optional / not specified

Data/code availability statement:
- public / private / available on request / unavailable due to restrictions

Topic fit:
- ...

Scope restrictions:
- ...

Formatting constraints:
- ...

Figure and table constraints:
- ...

Supplementary material rules:
- ...
```

### 8.2 `PROJECT_CONTEXT.md`

Contains project and supervisor constraints that affect the manuscript.

Use this file when the paper is connected to a laboratory project, grant, repository, supervisor review, industrial partner, or restricted artifact.

Template:

```markdown
# PROJECT_CONTEXT.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

Project:
- ...

Supervisor or project lead:
- ...

Permitted scope:
- ...

Restricted scope:
- ...

Required project terminology:
- ...

Project artifacts that may be cited:
- ...

Project artifacts that must not be cited:
- ...

Related project tasks:
- ...

Disclosure restrictions:
- ...

Supervisor comments:
- ...

Co-author constraints:
- ...
```

For BAS-related papers, this file should record whether the paper concerns:

* ArduPilot or ArduCopter;
* Gazebo;
* AirSim or Cosys-AirSim;
* MAVROS;
* MAVLink;
* ns-3;
* Sionna RT;
* communication channels for control and video;
* LoRa, LoRaWAN, Wi-Fi, TCP/IP, serial links;
* realistic or real map construction;
* large-area map handling;
* manual control from a ground control station;
* cyberattack models, if included;
* parallel simulation, if included.

### 8.3 `PAPER_PROPOSAL.md`

This file is the compact research proposal for the manuscript.

It must exist before drafting. It prevents the paper from becoming a repository report or a disconnected collection of results.

Template:

```markdown
# PAPER_PROPOSAL.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Working title

- ...

## Context

- ...

## Practical relevance

- ...

## Contradiction

- Desired state:
- Current state:
- Gap between them:

## Research problem

- ...

## Research question

- ...

## Goal

- ...

## Hypothesis, if used

- ...

## Objectives

1. ...
2. ...
3. ...
4. ...
5. ...

## Expected contribution

- ...

## Evidence available

- ...

## Expected limitations

- ...

## Target reader

- ...

## Five-sentence scientific story

1. ...
2. ...
3. ...
4. ...
5. ...
```

### 8.4 `RESEARCH_DESIGN.md`

Defines the research object, subject, variables, method, scope, and delimitations.

Template:

```markdown
# RESEARCH_DESIGN.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Object of study

- ...

## Subject of study

- ...

## Independent variables

- ...

## Dependent variables

- ...

## Control variables

- ...

## Moderators or mediators, if any

- ...

## Research type

- experimental / simulation-based / design-science / comparative / exploratory / validation / survey / other

## Methodological basis

- ...

## Methods used

- literature analysis:
- modeling:
- simulation:
- experiment:
- measurement:
- comparison:
- statistical analysis:
- other:

## Scope

- ...

## Delimitations

- ...

## Assumptions

- ...

## Threats to validity

- internal:
- external:
- construct:
- conclusion:

## Why this design answers the research question

- ...
```

### 8.5 `FACTS.md`

Contains only verified high-level facts.

It may include:

* implemented system components;
* experimental conditions;
* number of runs;
* metric definitions;
* verified result summaries;
* artifact locations;
* repository commits if applicable;
* known limitations.

`FACTS.md` is not the primary source for numerical values.

All manuscript-level numerical values must be copied from `RESULTS_PROVENANCE.md`.

Template:

```markdown
# FACTS.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## System components

- ...

## Experimental conditions

- ...

## Metric definitions

- ...

## Verified results summary

- ...

## Known limitations

- ...
```

### 8.6 `RESULTS_PROVENANCE.md`

Contains the provenance of every numerical result:

* metric name;
* reported value;
* experiment ID;
* run IDs;
* input files;
* output files;
* postprocessing script;
* repository commit;
* command used to generate the result;
* figure or table where the value appears;
* data/code availability status.

No numerical value may appear in the manuscript unless it is traceable through this file.

Template:

```markdown
# RESULTS_PROVENANCE.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Result ID: R-001

Metric name:
- ...

Reported value:
- ...

Experiment ID:
- ...

Run IDs:
- ...

Input files:
- ...

Output files:
- ...

Postprocessing script:
- ...

Repository commit:
- ...

Command:
- ...

Appears in:
- Abstract
- Results
- Table ...

Data/code availability:
- public / private / available on request / unavailable due to restrictions

Allowed interpretation:
- ...

Forbidden interpretation:
- ...
```

### 8.7 `EXPERIMENTAL_PROTOCOL.md`

Defines how the evidence was produced.

This file corresponds to the engineering requirement that a work with software and simulation must have a program and method of testing, not only a results section.

Template:

```markdown
# EXPERIMENTAL_PROTOCOL.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Experiment ID

- ...

## Purpose

- ...

## Tested hypothesis or research question

- ...

## Scenario

- ...

## System configuration

- hardware:
- OS:
- simulator versions:
- repository commit:
- configuration files:

## Input data

- ...

## Variables

- independent:
- dependent:
- controlled:

## Experimental matrix

- ...

## Run plan

- number of runs:
- seeds:
- repetitions:
- stopping conditions:

## Metrics

- metric:
- definition:
- unit:
- calculation method:

## Logging

- log files:
- fields:
- sampling rate:

## Postprocessing

- scripts:
- commands:
- output files:

## Acceptance criteria

- ...

## Known limitations

- ...

## Reproducibility notes

- ...
```

### 8.8 `DESIGN_DECISIONS.md`

Records the justification of selected engineering solutions.

This file is essential when the contribution depends on choosing between tools, architectures, simulation components, data flows, or communication models.

Template:

```markdown
# DESIGN_DECISIONS.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Decision ID: D-001

Decision:
- ...

Alternatives considered:
- ...

Selection criteria:
- fidelity:
- reproducibility:
- integration complexity:
- computational cost:
- availability:
- relevance to the research question:

Selected option:
- ...

Reason:
- ...

Evidence:
- ...

Limitations:
- ...

Manuscript sections where used:
- ...

Safe wording:
- ...
```

### 8.9 `ARTIFACTS.md`

Contains reproducibility artifacts:

* repository URL;
* commit;
* scripts;
* input data;
* output logs;
* figures;
* tables;
* environment description;
* unavailable artifacts and reason;
* data/code availability statement.

Template:

```markdown
# ARTIFACTS.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

Repository URL:
- ...

Commit:
- ...

Scripts:
- ...

Input data:
- ...

Output logs:
- ...

Figures:
- ...

Tables:
- ...

Environment:
- OS:
- simulator versions:
- Python version:
- compiler:
- key libraries:

Unavailable artifacts:
- artifact:
- reason:

Data/code availability statement:
- public / private / available on request / unavailable due to restrictions
```

### 8.10 `TERMS.md`

Contains the approved terminology:

* preferred term;
* rejected synonyms;
* first-use definition;
* abbreviation;
* source or literature basis for the term;
* where the term must be used.

The same object must have the same term throughout the paper.

Template:

```markdown
# TERMS.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Term: ...

Preferred term:
- ...

Rejected synonyms:
- ...

First-use definition:
- ...

Abbreviation:
- ...

Literature or documentation basis:
- ...

Use in:
- Abstract
- Introduction
- Method
- Results
- Discussion

Forbidden use:
- ...
```

### 8.11 `LITERATURE_MATRIX.md`

Contains the literature basis for terminology, gap, methodology, and novelty.

Template:

```markdown
# LITERATURE_MATRIX.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Source: ...

Citation key:
- ...

Full reference:
- ...

Source type:
- peer-reviewed paper / standard / documentation / dataset / preprint / book / technical report

Problem addressed:
- ...

Tools or methods:
- ...

Evidence type:
- experiment / simulation / implementation / survey / theory / benchmark

Reported limitations:
- ...

Relation to this paper:
- background / baseline / gap evidence / terminology / method comparison / excluded alternative

Terms adopted:
- ...

Terms rejected or avoided:
- ...

Claim supported:
- ...

Planned citation section:
- ...
```

For grant-related analytical reviews, the matrix must cover the required review horizon and publication count. If the project requirement says that the review must cover at least the last 5 years and at least 50 publications, record that requirement in `PROJECT_CONTEXT.md` and track progress in `LITERATURE_MATRIX.md`.

### 8.12 `REFERENCES_PLAN.md`

Contains planned sources before drafting.

Template:

```markdown
# REFERENCES_PLAN.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Source: ...

Citation key:
- ...

Full reference:
- ...

Planned manuscript section:
- ...

Statement supported by the source:
- ...

Source type:
- peer-reviewed paper / standard / documentation / dataset / preprint / technical report / book

DOI or stable identifier:
- ...

Reason for inclusion:
- ...

Replacement source, if weak:
- ...
```

### 8.13 `REFERENCES_AUDIT.md`

Checks final citation and reference correctness after the manuscript is assembled.

Template:

```markdown
# REFERENCES_AUDIT.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Citation consistency

- Every in-text citation appears in the reference list: yes / no
- Every reference is cited in the text: yes / no
- Citation order is correct: yes / no
- DOI is present where available: yes / no
- Venue names are consistent: yes / no
- References follow the target style: yes / no
- No duplicate references: yes / no
- No raw URLs remain unless allowed: yes / no
- Preprints and peer-reviewed versions are not mixed without explanation: yes / no

## Issues

- ...
```

### 8.14 `FIGURE_TABLE_PLAN.md`

Every figure and table must be planned before it appears in the manuscript.

Template:

```markdown
# FIGURE_TABLE_PLAN.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Fig. 1

ID:
- Fig. 1

Title:
- ...

Role:
- supports a claim / explains method / defines setup / reports result

Claim supported:
- ...

Input data:
- ...

Metric shown:
- ...

Section where cited:
- ...

Required interpretation:
- ...

Forbidden interpretation:
- ...

Essential or optional:
- essential / optional

Caption requirements:
- ...
```

No figure or table may appear in the manuscript unless it supports a claim, explains the method, defines the setup, or reports a result.

### 8.15 `CLAIMS.md`

Contains the claim-evidence matrix.

Template:

```markdown
# CLAIMS.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Claim ID: C-001

Claim:
- ...

Evidence:
- ...

Metrics:
- ...

Figure or table:
- ...

Supporting citation:
- ...

Limitation:
- ...

Safe wording:
- ...

Forbidden stronger wording:
- ...
```

Every claim in the manuscript must be supported by evidence.

### 8.16 `NOVELTY.md`

Contains the novelty justification.

Template:

```markdown
# NOVELTY.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

Prior studies provide:
- ...

Remaining gap:
- ...

This paper adds:
- ...

Why this is not only implementation work:
- ...

Evidence:
- ...

Difference from closest related work:
- ...

Limitations:
- ...

Safe novelty wording:
- ...

Forbidden novelty wording:
- ...
```

Novelty must be consistent with:

* `LITERATURE_MATRIX.md`;
* `REFERENCES_PLAN.md`;
* `CLAIMS.md`;
* `LIMITATIONS.md`;
* `FIGURE_TABLE_PLAN.md`;
* `DESIGN_DECISIONS.md`.

### 8.17 `LIMITATIONS.md`

Contains explicit boundaries of the work.

Template:

```markdown
# LIMITATIONS.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Scope

The work verifies:
- ...

The work does not verify:
- ...

Heuristic components:
- ...

Scenario-specific assumptions:
- ...

Non-validated components:
- ...

Unsupported interpretations:
- ...

Implications for future work:
- ...
```

### 8.18 `STYLE.md`

Contains writing rules.

Template:

```markdown
# STYLE.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Approved terms

Use:
- ...

Avoid:
- ...

## Style rules

- Define abbreviations before use.
- Avoid non-standard terms and direct translations.
- Avoid excessive hyphenated compounds.
- Use present tense for general facts and paper contributions.
- Use past tense only for completed experimental actions where required.
- Avoid internal project labels in Abstract, Introduction, and Conclusion.
- Report metrics with consistent units.
- Keep the text understandable for a reader outside the narrow project area.
- Do not introduce a term before explaining it.
- Do not use multiple synonyms for the same object.
- Avoid repository language unless the repository is the explicit object of the paper.

## Section-specific style

Abstract:
- ...

Introduction:
- ...

Method:
- ...

Results:
- ...

Discussion:
- ...

Conclusion:
- ...
```

### 8.19 `AI_USE.md`

Records the use of AI tools.

Template:

```markdown
# AI_USE.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Tools used

- ...

## Sections affected

- section:
- AI task type:
- human verification:

## Human verification

The author verified:
- numerical results;
- citations;
- claims;
- terminology;
- final manuscript decisions.

## Confirmation

No data, citations, experiments, figures, tables, or numerical results were generated by AI.

## Venue disclosure requirement

- required / not required / unclear

## Disclosure text

- ...
```

### 8.20 `CHANGELOG.md`

Records all substantial manuscript changes.

Template:

```markdown
# CHANGELOG.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Version ...

Changed section:
- ...

Reason:
- ...

Affected claims:
- ...

Affected figures or tables:
- ...

Affected citations:
- ...

Affected control files:
- ...

Reviewer or supervisor comment addressed:
- ...
```

Minor grammar edits do not need to be recorded.

### 8.21 `OWNER_MATRIX.md`

Defines responsibility for each file, artifact, and manuscript section.

This file is required for co-authored papers and supervisor-review drafts.

Template:

```markdown
# OWNER_MATRIX.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

## Control file owners

| File | Owner | Reviewer | Approval required before drafting |
|---|---|---|---|
| VENUE_REQUIREMENTS.md | ... | ... | yes / no |
| PROJECT_CONTEXT.md | ... | ... | yes / no |
| RESULTS_PROVENANCE.md | ... | ... | yes / no |
| LITERATURE_MATRIX.md | ... | ... | yes / no |
| REFERENCES_PLAN.md | ... | ... | yes / no |
| CLAIMS.md | ... | ... | yes / no |
| NOVELTY.md | ... | ... | yes / no |
| LIMITATIONS.md | ... | ... | yes / no |
| ARTIFACTS.md | ... | ... | yes / no |

## Manuscript section owners

| Section | Owner | Reviewer | Status |
|---|---|---|---|
| Abstract | ... | ... | Draft / Checked / Locked |
| Introduction | ... | ... | Draft / Checked / Locked |
| Related Work | ... | ... | Draft / Checked / Locked |
| Method | ... | ... | Draft / Checked / Locked |
| Results | ... | ... | Draft / Checked / Locked |
| Discussion | ... | ... | Draft / Checked / Locked |
| Conclusion | ... | ... | Draft / Checked / Locked |
```

### 8.22 `INTRO_CONCLUSION_MAP.md`

Checks that the conclusion answers the introduction.

Template:

```markdown
# INTRO_CONCLUSION_MAP.md

Status:
Owner:
Last checked:
Applies to manuscript version:
Related manuscript sections:
Related artifacts:

| Introduction element | Where stated in Introduction | Evidence in body | Corresponding conclusion sentence | Status |
|---|---|---|---|---|
| Problem | ... | ... | ... | ok / mismatch |
| Gap | ... | ... | ... | ok / mismatch |
| Goal | ... | ... | ... | ok / mismatch |
| Contribution 1 | ... | ... | ... | ok / mismatch |
| Contribution 2 | ... | ... | ... | ok / mismatch |
| Limitation | ... | ... | ... | ok / mismatch |

Required fixes:
- ...
```

---

## 9. Workflow stages

### Stage 1: Project, venue, and disclosure check

Before any scientific planning, record:

* target venue;
* paper type;
* page limit;
* review mode;
* AI-use disclosure requirement;
* data/code availability policy;
* project disclosure restrictions;
* supervisor constraints;
* co-author constraints.

Outputs:

* completed `VENUE_REQUIREMENTS.md`;
* completed `PROJECT_CONTEXT.md`, if applicable;
* selected workflow mode;
* initial section structure.

### Stage 2: Paper proposal gate

Create `PAPER_PROPOSAL.md`.

Check that the paper has:

* context;
* contradiction;
* problem;
* research question;
* goal;
* hypothesis, if used;
* 4 to 5 objectives;
* evidence;
* expected novelty;
* expected limitations;
* five-sentence scientific story.

Drafting is not allowed until the proposal is coherent.

### Stage 3: Research design gate

Create `RESEARCH_DESIGN.md`.

Check that:

* object and subject are distinct;
* variables are defined where relevant;
* methods correspond to objectives;
* scope and delimitations are explicit;
* assumptions are stated;
* threats to validity are recognized.

If the paper is experimental or simulation-based, create `EXPERIMENTAL_PROTOCOL.md`.

### Stage 4: Evidence package creation

Before drafting Results, prepare:

* `FACTS.md`;
* `RESULTS_PROVENANCE.md`;
* `ARTIFACTS.md`;
* `EXPERIMENTAL_PROTOCOL.md`;
* output logs;
* figures and tables;
* postprocessing scripts;
* reproducibility notes.

No numerical result enters the manuscript without provenance.

### Stage 5: Literature and terminology verification

For every important term:

1. search peer-reviewed literature and official documentation;
2. check whether the term is standard;
3. reject direct calques if they are not used in the literature;
4. select the clearest term;
5. add it to `TERMS.md`;
6. record supporting sources in `LITERATURE_MATRIX.md`;
7. add planned citations to `REFERENCES_PLAN.md`.

Questions for each term:

* Is this term standard?
* Is it clear to a reader outside the project?
* Is it used consistently in the literature?
* Is it a direct translation from another language?
* Is there a simpler term?
* Does the term require a definition?
* Is the same object called differently elsewhere in the paper?

No section is rewritten until the terminology is stable.

### Stage 6: Related work as gap construction

Related Work must not be a list of tools.

For each source group, define:

* what prior work provides;
* what method or tool it uses;
* what evidence it reports;
* what limitation remains;
* how this paper differs;
* how the citation supports the gap.

Outputs:

* completed `LITERATURE_MATRIX.md`;
* completed `REFERENCES_PLAN.md`;
* narrowed `NOVELTY.md`.

### Stage 7: Design decision justification

Create `DESIGN_DECISIONS.md` when the paper includes engineering choices.

For each major choice, record:

* alternatives;
* selection criteria;
* selected option;
* reason;
* evidence;
* limitation.

Examples:

* Gazebo vs AirSim vs Unreal-based environment;
* ns-3 vs simplified network model;
* Sionna RT vs heuristic signal-loss model;
* MAVLink integration path;
* control channel and video channel separation;
* use of recorded data vs synthetic data;
* map scale and tiling method;
* real-time vs offline simulation.

### Stage 8: Outline with claims, evidence, figures, tables, and citations

Prepare a section-level outline.

For each section, define:

* purpose of the section;
* reader question answered by the section;
* claims made in the section;
* evidence used in the section;
* figures and tables cited in the section;
* citations used in the section;
* limitations relevant to the section;
* terms introduced in the section.

A term cannot appear in Results if it was never introduced in Introduction, Related Work, Method, or Metrics.

Every figure and table must be added to `FIGURE_TABLE_PLAN.md` before appearing in the manuscript.

Every planned citation must be added to `REFERENCES_PLAN.md` before appearing in the manuscript.

### Stage 9: Section planning

For each section, ask the strategy model:

```text
Plan this section.

Use only the approved control files:
- VENUE_REQUIREMENTS.md
- PROJECT_CONTEXT.md
- PAPER_PROPOSAL.md
- RESEARCH_DESIGN.md
- FACTS.md
- RESULTS_PROVENANCE.md
- EXPERIMENTAL_PROTOCOL.md
- DESIGN_DECISIONS.md
- ARTIFACTS.md
- TERMS.md
- LITERATURE_MATRIX.md
- REFERENCES_PLAN.md
- FIGURE_TABLE_PLAN.md
- CLAIMS.md
- NOVELTY.md
- LIMITATIONS.md
- STYLE.md

Return:
1. section purpose;
2. reader question answered by the section;
3. claims allowed in the section;
4. evidence used in the section;
5. terms that must be introduced;
6. terms that must be avoided;
7. figures and tables to cite;
8. citations to use;
9. limitations to mention;
10. risks for reviewer.
```

### Stage 10: Section drafting

Draft the paper section by section.

For each section:

1. provide AI with the required control files;
2. request rewriting only within approved terminology;
3. prohibit new results and new claims;
4. require definitions before abbreviations;
5. require clear transitions between paragraphs;
6. check that every paragraph has one function;
7. preserve all numerical results exactly;
8. check every numerical value against `RESULTS_PROVENANCE.md`;
9. check every figure and table against `FIGURE_TABLE_PLAN.md`;
10. check citations against `LITERATURE_MATRIX.md` and `REFERENCES_PLAN.md`;
11. update `AI_USE.md` after AI-assisted drafting.

Prompt template:

```text
You are rewriting one section of a scientific paper.

Use only the approved control files provided below:
- VENUE_REQUIREMENTS.md
- PROJECT_CONTEXT.md
- PAPER_PROPOSAL.md
- RESEARCH_DESIGN.md
- FACTS.md
- RESULTS_PROVENANCE.md
- EXPERIMENTAL_PROTOCOL.md
- DESIGN_DECISIONS.md
- ARTIFACTS.md
- TERMS.md
- LITERATURE_MATRIX.md
- REFERENCES_PLAN.md
- FIGURE_TABLE_PLAN.md
- CLAIMS.md
- NOVELTY.md
- LIMITATIONS.md
- STYLE.md

Task:
Rewrite the section below.

Rules:
1. Do not invent results.
2. Do not introduce new claims.
3. Do not change numerical values.
4. Use only approved terminology from TERMS.md.
5. Define abbreviations before use.
6. Keep the section understandable for a technical reader outside this narrow project.
7. Avoid internal repository language.
8. Avoid excessive hyphenated compounds.
9. Report metrics with consistent units.
10. If a sentence requires hidden context, rewrite it or flag it.
11. Do not add figures, tables, or citations absent from the control files.
12. Do not strengthen novelty, contribution, or conclusion wording.

Return:
- rewritten section;
- list of changed terms;
- list of claims used;
- list of numerical values and provenance entries;
- list of figures and tables used;
- list of citations used;
- unresolved issues.
```

### Stage 11: Reader clarity pass

After each section is drafted, run a clarity check from the perspective of a scientist from another technical field.

The reviewer must identify:

* undefined terms;
* hidden context;
* overloaded sentences;
* excessive internal detail;
* places where the result appears before the method is understandable;
* places where the paper assumes repository knowledge;
* tools listed before the problem is explained.

Prompt template:

```text
Read this section as a scientist from another technical field who sees this topic for the first time.

Mark every sentence that assumes hidden context.

For each issue, provide:
1. exact sentence;
2. what is unclear;
3. what prior explanation is missing;
4. whether the sentence should be simplified, moved later, or removed;
5. clearer replacement text.

Be strict. Do not praise the text. Identify all places where the manuscript sounds like internal project documentation rather than a scientific paper.
```

### Stage 12: Claim-evidence audit

Run a claim-evidence audit after the full draft is assembled.

For every claim, verify:

* where it appears;
* what evidence supports it;
* whether the evidence is sufficient;
* whether the claim is overstated;
* whether a safer formulation is required;
* whether the claim is repeated consistently in Abstract, Introduction, Discussion, and Conclusion.

Claims without evidence are deleted or rewritten as limitations or future work.

Prompt template:

```text
Build a claim-evidence matrix for the manuscript.

For every scientific claim, identify:
1. exact claim;
2. section where it appears;
3. evidence in the paper;
4. supporting metric;
5. supporting figure or table;
6. supporting citation, if any;
7. whether the evidence is sufficient;
8. whether the claim is overstated;
9. safer wording if needed.

Do not invent evidence. If evidence is absent, mark the claim as unsupported.
```

### Stage 13: Results provenance audit

Before finalizing Results, Discussion, Abstract, and Conclusion, check all numerical values.

For every number in the manuscript:

* locate the corresponding entry in `RESULTS_PROVENANCE.md`;
* verify the value;
* verify the unit;
* verify the figure or table reference;
* verify that the interpretation does not exceed the metric.

Prompt template:

```text
Audit all numerical results in this manuscript.

For each numerical value:
1. quote the value;
2. identify the section where it appears;
3. identify the metric;
4. identify the required provenance entry;
5. check whether the unit is consistent;
6. check whether the value appears in the correct figure or table;
7. check whether the interpretation is supported by the metric.

If a number has no provenance entry, mark it as not allowed in the manuscript.
```

### Stage 14: Figure and table audit

For every figure and table:

* identify the claim it supports;
* verify the input data;
* verify the metric shown;
* verify the first citation in the manuscript;
* verify the interpretation;
* check whether the manuscript over-interprets the figure or table;
* decide whether it is essential or optional.

Prompt template:

```text
Audit all figures and tables in the manuscript.

Use FIGURE_TABLE_PLAN.md.

For each figure or table:
1. identify its role;
2. identify the claim it supports;
3. verify where it is cited;
4. verify whether the caption is sufficient;
5. check whether the interpretation in the text is allowed;
6. identify forbidden interpretations if they appear;
7. decide whether the figure or table is essential or should be removed.

Return only issues and required corrections.
```

### Stage 15: Terminology and metric consistency audit

Check the full paper for:

* inconsistent terms;
* undefined abbreviations;
* mixed metric units;
* inconsistent tense;
* duplicated explanations;
* figure and table references;
* citation order;
* mismatch between Abstract, Introduction, Results, Discussion, and Conclusion.

No final proofreading is allowed before this audit is complete.

Prompt template:

```text
Perform a full terminology and metric consistency audit of the paper.

Check:
1. whether the same object is always named with the same term;
2. whether all abbreviations are defined at first use;
3. whether all metrics use consistent units;
4. whether each figure and table is cited before or near its discussion;
5. whether Abstract, Introduction, Results, Discussion, and Conclusion make the same claims;
6. whether any claim is stronger than the evidence;
7. whether any internal project label appears in high-level sections without explanation;
8. whether any tool name is introduced before the problem and method require it.

Return only issues and required fixes.
```

### Stage 16: References audit

After the manuscript is assembled, create or update `REFERENCES_AUDIT.md`.

The audit must verify:

* every in-text citation appears in the reference list;
* every reference is cited in the text;
* citation order is correct;
* DOI is present where available;
* venue names are consistent;
* references follow the target style;
* no raw URLs remain unless allowed;
* no duplicate references;
* preprints and peer-reviewed versions are not mixed without explanation;
* citation numbering matches final manuscript order;
* citations support the statements they are attached to.

### Stage 17: Novelty audit

Run a novelty audit before supervisor review.

Check:

* what prior studies already provide;
* what remains unresolved;
* whether the manuscript clearly states the unresolved gap;
* whether the contribution is more than implementation work;
* what evidence supports the novelty;
* whether the novelty claim is overstated;
* whether limitations restrict the novelty properly;
* whether design decisions are justified against alternatives.

Prompt template:

```text
Audit the novelty claim of this paper.

Use NOVELTY.md, CLAIMS.md, LIMITATIONS.md, LITERATURE_MATRIX.md, REFERENCES_PLAN.md, DESIGN_DECISIONS.md, and FIGURE_TABLE_PLAN.md.

Check:
1. what prior work already provides;
2. what gap remains;
3. what the paper adds;
4. whether the contribution is more than implementation work;
5. what evidence supports the novelty;
6. whether the novelty claim is too broad;
7. whether the limitations properly restrict the claim;
8. whether the Introduction and Conclusion state the same novelty level.

Return only issues and required corrections.
```

### Stage 18: Artifact and availability audit

Before supervisor review or submission, create or update `ARTIFACTS.md`.

Check:

* repository URL;
* commit;
* scripts;
* input data;
* output logs;
* figures and tables;
* environment description;
* unavailable artifacts and reasons;
* data/code availability statement;
* consistency with `VENUE_REQUIREMENTS.md`.

### Stage 19: AI-use audit

Before supervisor review or submission, update `AI_USE.md`.

Check:

* AI tools used;
* sections affected;
* task type for each section;
* human verification performed;
* whether any data, citations, or numerical results were generated by AI;
* venue disclosure requirement;
* final disclosure text if required.

### Stage 20: Introduction to conclusion audit

Create `INTRO_CONCLUSION_MAP.md`.

For every problem, gap, contribution, and limitation in the Introduction, verify that:

* the body provides evidence;
* the Discussion interprets it within limitations;
* the Conclusion answers it without overclaiming.

### Stage 21: Adversarial review

Before final human polishing, run a hostile review.

The reviewer must search for:

* unclear contribution;
* weak novelty;
* unsupported claims;
* hidden assumptions;
* unclear metrics;
* missing baselines;
* non-standard terminology;
* unclear experimental setup;
* overclaiming;
* mismatch between results and conclusions;
* grammar and style issues that reduce credibility.

Prompt template:

```text
Act as a strict technical reviewer.

Review this paper for:
1. unclear contribution;
2. weak novelty;
3. unsupported claims;
4. hidden assumptions;
5. unclear metrics;
6. missing baselines;
7. non-standard terminology;
8. unclear experimental setup;
9. overclaiming;
10. mismatch between results and conclusions;
11. grammar and style issues that reduce credibility.

For each issue, provide:
- exact location;
- why it is a problem;
- likely reviewer reaction;
- required fix;
- safer replacement wording if applicable.

Do not praise the paper. Do not invent new results.
```

### Stage 22: Final human pass

The author performs the final pass.

Check:

* all facts;
* all numerical values;
* all claims;
* all citations;
* all figures and tables;
* all limitations;
* final venue compliance;
* final AI disclosure;
* final data/code availability statement;
* final co-author or supervisor comments.

Only the human author decides what enters the final manuscript.

---

## 10. AI role separation

AI tools are assigned by function, not by unrestricted rewriting.

### Strategy model

Used for:

* scientific story;
* novelty framing;
* claim-evidence matrix;
* hostile review;
* methodology critique;
* interpretation of supervisor or reviewer comments.

The strategy model does not rewrite full sections unless the claims, terms, and limitations are already locked.

### Rewriting model

Used for:

* section-level rewriting;
* paragraph transitions;
* tone consistency;
* English polishing;
* reducing overloaded sentences;
* improving readability for non-specialist technical readers.

The rewriting model must use only approved terminology from `TERMS.md` and must not introduce new claims or numerical results.

### Audit model

Used for:

* terminology consistency;
* citation order;
* figure and table reference check;
* unsupported claim detection;
* limitation check;
* metric and unit consistency;
* comparison between Abstract, Introduction, Results, Discussion, and Conclusion.

The audit model reports issues and required corrections. It does not rewrite the manuscript unless explicitly instructed.

### Cross-model control rule

The same section must not be rewritten by another model unless the relevant control files are Locked.

If a second model rewrites a section, the result must pass a consistency audit before being inserted into the manuscript.

One model may write, another may critique, and a third may audit. No model is allowed to freely rewrite the whole manuscript without the locked control files.

---

## 11. Manuscript section rules

### Abstract

The Abstract must contain:

* problem;
* method;
* experimental scope;
* main quantitative results, if any;
* main conclusion.

The Abstract must not contain:

* undefined abbreviations;
* internal experiment labels;
* repository-specific terms;
* excessive implementation detail;
* claims not repeated and supported in the main text;
* novelty stronger than `NOVELTY.md`.

### Introduction

The Introduction must explain:

* practical and scientific context;
* contradiction;
* research problem;
* why existing approaches are insufficient;
* research gap;
* object and subject, if appropriate for the genre;
* goal;
* contribution;
* evidence;
* paper structure, if required.

The Introduction must explain the problem before listing tools.

### Related Work

Related Work must not be a list of tools.

It must explain:

* what prior work provides;
* how it relates to the paper;
* what remains unresolved;
* how the current paper differs;
* what terminology is adopted;
* what terminology or framing is rejected.

Every cited group of studies must serve the gap argument.

### Method

The Method must define:

* system architecture;
* simulation components;
* data flows;
* design decisions;
* experimental scenario;
* variables;
* experimental matrix;
* metrics;
* logging;
* postprocessing;
* reproducibility artifacts.

Every metric used in Results must be defined before Results.

### Results

Results must report observations, not speculation.

Each result must include:

* experiment condition;
* metric;
* value;
* unit;
* figure or table reference;
* short interpretation limited by evidence.

### Discussion

Discussion must separate:

* what the results mean;
* why they matter;
* what they do not prove;
* how they relate to prior work;
* what limitations affect interpretation;
* what design decisions shaped the results.

### Limitations

Limitations must explicitly state:

* model assumptions;
* scope restrictions;
* non-validated components;
* scenario dependence;
* measurement limitations;
* unavailable artifacts;
* generalization boundaries.

### Conclusion

Conclusion must repeat only supported claims.

It must not introduce:

* new results;
* new terms;
* new limitations;
* new literature;
* stronger novelty claims than the Introduction.

---

## 12. BAS and digital-twin paper addendum

Use this section for papers connected to unmanned aerial systems, digital twins, communication simulation, and the BAS project.

### 12.1 Required project checks

Before drafting, record whether the paper concerns:

* simulation environment development;
* manual control of at least one unmanned aerial vehicle;
* communication channels for control and video;
* ArduPilot or ArduCopter;
* Gazebo;
* AirSim or Cosys-AirSim;
* MAVROS;
* MAVLink;
* ns-3;
* Sionna RT;
* LoRa, LoRaWAN, Wi-Fi, TCP/IP, or serial links;
* realistic map construction;
* large-area map handling;
* radio attenuation and reflection modeling;
* error rate, error distribution, throughput, latency, or packet-delivery metrics;
* optional cyberattack models;
* optional parallel simulation.

### 12.2 BAS paper minimum method description

A BAS-related Method section must identify:

1. flight-control component;
2. physics or vehicle-dynamics simulator;
3. environment and sensor simulator;
4. communication layer;
5. control channel;
6. payload or video channel;
7. logging points;
8. metrics;
9. scenario boundary;
10. artifacts needed for reproducibility.

### 12.3 BAS paper forbidden overclaims

Do not claim:

* operational readiness unless field validation supports it;
* real-world safety unless real-world testing supports it;
* full digital twin fidelity unless physical validation supports it;
* general communication robustness from one scenario;
* validated radio propagation from a heuristic signal-loss function;
* scalability beyond tested map size or number of agents;
* full UAS traffic-management capability from a single-vehicle prototype.

### 12.4 BAS paper safe contribution types

Acceptable contribution types include:

* integration architecture;
* reproducible simulation pipeline;
* test methodology;
* controlled impairment experiment;
* metric definition;
* traceable software artifact;
* comparison of design alternatives;
* validated or partially validated component;
* limitation-aware engineering prototype;
* literature-supported gap analysis.

---

## 13. Submission readiness checklist

A paper is not ready for supervisor review or submission until:

* `VENUE_REQUIREMENTS.md` is Locked;
* `PROJECT_CONTEXT.md` is Locked, if applicable;
* workflow mode is selected;
* `PAPER_PROPOSAL.md` is Locked;
* object, subject, goal, research question, and objectives are clear;
* Abstract is understandable without project context;
* Introduction explains the problem before tools;
* Introduction and Conclusion correspond;
* Related Work explains why each cited group of papers is relevant;
* Method defines the experimental chain;
* Results report trends without overclaiming;
* Discussion separates interpretation from evidence;
* Limitations explicitly restrict the scope;
* Conclusion repeats only supported claims;
* every numerical value has an entry in `RESULTS_PROVENANCE.md`;
* every important term appears in `TERMS.md`;
* every claim appears in `CLAIMS.md`;
* every novelty statement is supported by `NOVELTY.md`;
* every major design choice is justified in `DESIGN_DECISIONS.md`;
* every important source is represented in `LITERATURE_MATRIX.md`;
* every planned citation is represented in `REFERENCES_PLAN.md`;
* final citations and references are checked in `REFERENCES_AUDIT.md`;
* every figure and table is planned in `FIGURE_TABLE_PLAN.md`;
* artifacts and data/code availability are recorded in `ARTIFACTS.md`;
* AI use is recorded in `AI_USE.md`;
* all critical and major hostile-review comments are fixed;
* `CHANGELOG.md` records all substantial changes;
* owners and reviewers are recorded in `OWNER_MATRIX.md`, if co-authors are involved.

---

## 14. Stop rule

Stop drafting when:

* all control files required for the selected mode are Locked;
* the manuscript answers the research question;
* every claim is supported;
* every number is traceable;
* every figure and table has a role;
* limitations are explicit;
* the manuscript fits the page limit;
* venue requirements are satisfied;
* only minor style issues remain.

Further edits are allowed only for:

* venue formatting;
* grammar;
* citation order;
* page-limit compression;
* supervisor or reviewer comments.

AI polishing must not become an infinite rewriting loop.

---

## 15. Final rule

Do not ask AI:

```text
Write a paper based on my repository.
```

Ask AI:

```text
Here are verified facts, result provenance, experimental protocol, artifacts, approved terms, literature matrix, references plan, figure/table plan, supported claims, novelty boundaries, venue requirements, project constraints, design decisions, limitations, and style rules.

Rewrite only this section.

Do not add new results.
Do not change terminology.
Do not introduce unsupported claims.
Do not add citations, figures, or tables absent from the control files.
Do not strengthen novelty or conclusion wording.
Flag anything unclear to a reader without project context.
```

The goal is not to make AI write more text.

The goal is to keep the manuscript inside a controlled scientific process: venue constraints, project constraints, research proposal logic, facts, provenance, artifacts, terminology, literature, references, figures, tables, design decisions, claims, novelty, limitations, AI-use transparency, audit, and final human responsibility.
