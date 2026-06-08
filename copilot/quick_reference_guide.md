# Ask HR Policy — Quick Reference Guide

Upload this file into Copilot Studio (**Knowledge → Add knowledge → Upload
file**) so the agent can answer trivial lookups (acronyms, policy numbers,
"which policy covers X") **directly**, without calling the `askHRPolicy`
Foundry tool. For anything that asks **what a policy says, how it works,
eligibility, amounts, deadlines, process, or where a document is stored**, the
agent must call `askHRPolicy` instead.

> This guide is a routing aid only. It is **not** a substitute for the policy
> documents — never quote policy *content* from this file; call `askHRPolicy`
> for grounded, citation-backed answers.

## Acronyms

| Acronym | Meaning |
|---|---|
| PTO | Paid Time Off |
| STD | Short-Term Disability |
| BBP | Blood Borne Pathogens |
| DM | Data Management (career path) |
| AUP | Acceptable Use Policy |
| AI / LLM | Generative Artificial Intelligence / Large Language Models |
| SOP | Standard Operating Procedure |
| FS | Field Standard (procedure prefix) |

## Policy number index

Use this only to answer "what is the policy number for X" or "which policy
covers X". Do **not** summarize the policy from this table — call `askHRPolicy`.

| Policy # | Title | Area |
|---|---|---|
| 31000 | Code of Ethics and Related Matters | Ethics |
| 50410 | Hiring: Pre-employment Medical Examinations | Hiring |
| 50435 | Hiring: Rehiring of Retirees Without Advertising | Hiring |
| 50455 | Hiring: Probationary Period | Hiring |
| 50715 | Hours Worked and Pay Administration: Holiday Pay | Pay |
| 50815 | Career Path: HR Generalist | Career |
| 50855 | Career Path: Data Management (DM) | Career |
| 51350 | Types of Leave: Paid Time Off (PTO) | Leave |
| 51355 | Types of Leave: Paid Time Off (PTO) — Part-time | Leave |
| 51370 | Short-Term Disability | Leave |
| 52005 | Operational Matters: Uniform Dress Code | Dress code |
| 52010 | Operational Matters: Non-Uniform Dress Code | Dress code |
| 81100 | Information Technology Acceptable Use Policy | IT |
| 83100 | IT Information Security Policy | IT |
| 83400 | Emergency Notification System Policy | IT |
| 84100 | Computer Replacement Policy | IT |
| 85100 | Information Systems Roles and Responsibilities | IT |
| 87100 | Generative AI & Large Language Models (LLM) Policy | IT |
| 88100 | Mobile Device and Use Policy | IT |
| 101100 | Blood Borne Pathogens: Introduction | Safety |
| 101205 | Blood Borne Pathogens: Methods of Compliance | Safety |

## Policy areas (for clarifying questions)

When a request is vague, ask which of these areas it falls under before doing
anything:

- **Leave** — PTO (full-time, part-time), Short-Term Disability
- **Pay** — Holiday Pay
- **Hiring** — Pre-employment exams, rehiring retirees, probationary period
- **Career paths** — HR Generalist, Data Management
- **Dress code** — Uniform, Non-Uniform, uniform issuance SOPs
- **IT** — Acceptable Use, Information Security, AI/LLM, Mobile Device,
  Computer Replacement, Emergency Notification
- **Safety** — Blood Borne Pathogens
- **Ethics** — Code of Ethics

## Routing rules (summary)

| If the user… | Then… |
|---|---|
| greets or is vague | greet, ask which policy area — **no tool call** |
| asks an acronym's meaning | answer from **Acronyms** — **no tool call** |
| asks a policy number / which policy covers X | answer from **Policy number index** — **no tool call** |
| asks what a policy says / how it works / amounts / deadlines | call **`askHRPolicy`** |
| asks where a document is stored / its filename | call **`askHRPolicy`** (Pattern B path) |
| asks a non-HR question | decline politely — **no tool call**, no general knowledge |
