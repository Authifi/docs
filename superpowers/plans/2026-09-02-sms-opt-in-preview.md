# SMS Opt-In Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a non-functional Authifi SMS opt-in HTML page that meets SMS-provider review requirements and is shareable via a direct docs URL.

**Architecture:** One self-contained static HTML file under `docs/` with embedded CSS/JS. Not linked in nav. Submit is inert (no network).

**Tech Stack:** Static HTML, CSS, minimal JS; MkDocs Material site hosting.

## Global Constraints

- Work in `/Users/keats.kirsch/Documents/GitHub/authifi-docs-wt/sms-opt-in-preview` on `docs/sms-opt-in-preview`.
- Include: phone field, unchecked consent checkbox, message-type description, frequency, msg & data rates, HELP/STOP, Terms + Privacy links, submit “Yes, sign me up!”
- Consent checkbox must never be pre-checked.
- No preview banner; not in `.nav.yml`.
- Non-functional: no SMS API, storage, or real enrollment.
- Follow Authifi brand (indigo/navy); avoid AI design clichés listed in frontend rules.

---

### Task 1: Create `docs/sms-opt-in.html`

**Files:**
- Create: `docs/sms-opt-in.html`

- [ ] **Step 1: Build the page** with brand, form, disclosure, policy links, inert submit
- [ ] **Step 2: Verify** checkbox has no `checked` attribute; `mkdocs build` succeeds; page not in nav
- [ ] **Step 3: Commit, push, open PR** with the direct URL for provider sharing
