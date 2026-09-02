# Design: Authifi SMS Opt-In Form Preview

**Date:** 2026-09-02  
**Repo:** Authifi docs (`authifi-docs`)  
**Worktree:** `/Users/keats.kirsch/Documents/GitHub/authifi-docs-wt/sms-opt-in-preview`  
**Branch:** `docs/sms-opt-in-preview`

## Problem

Authifi needs a non-functional SMS opt-in page preview to share with an SMS provider for compliance/review before a future live deployment.

## Goals

1. Deliver a polished, branded standalone HTML page that looks like a real Authifi.com SMS opt-in experience.
2. Include the consent, disclosure, and policy links SMS providers typically expect for review.
3. Make the page shareable via a direct/preview URL without adding it to the main docs navigation.

## Non-goals

- Functional SMS enrollment, API calls, storage, CAPTCHA, or backend validation.
- A live production signup flow.
- Preview/draft banners or “not live” callouts.
- Linking the page from `docs/.nav.yml`.

## Decisions (approved)

| Decision | Choice |
| --- | --- |
| Delivery | Standalone HTML: `docs/sms-opt-in.html` |
| Discoverability | Direct/shareable URL only; not in main nav |
| Preview banner | None |
| Fields | Phone number + explicit consent checkbox + frequency/message-type disclosure + privacy/terms links |
| Message types | Account / security alerts only (login, MFA, access notifications) |
| Submit CTA | Clear affirmative language: **“Yes, sign me up!”** |

## SMS provider requirements (binding)

The page **must** include all of the following:

1. **Phone number input field**
2. **Consent checkbox** that is **not** pre-selected — the user must actively check it
3. **Clear description** of what type of messages they will receive
4. **Message frequency** information
5. **Standard disclaimer** that message and data rates may apply
6. **HELP and STOP** instructions
7. **Links** to Terms of Service and Privacy Policy
8. **Submit button** with clear language (e.g. “Yes, sign me up!”)

**Critical:** The consent checkbox must never render checked by default (`checked` attribute forbidden; no JS that auto-checks it).

## Design

### Page composition

Single first-viewport composition:

1. **Brand:** Authifi as the hero-level signal (logo + wordmark)
2. **Headline:** SMS notifications (or equivalent)
3. **Supporting sentence:** Short explanation that Authifi can send account and security alerts by text
4. **Form:** Phone field → consent checkbox (unchecked) → submit “Yes, sign me up!”
5. **Disclosure block** (visible without digging): message types, frequency, msg & data rates, STOP/HELP, Terms + Privacy links

No cards in the hero. No stats, promo chips, or secondary marketing blocks.

### Form behavior (preview only)

- `action` omitted or `#`; submit prevented with a no-op handler (still validates checkbox/phone client-side for realism if useful)
- Phone input: `type="tel"`, `autocomplete="tel"`, placeholder example
- Consent checkbox: unchecked by default; may use HTML `required` so submit appears gated
- Submit button label: **Yes, sign me up!**
- No network requests

### Consent and disclosure copy (intent)

- Explicit opt-in to receive Authifi account/security SMS at the provided number
- **Message types (clear):** login alerts, MFA / verification codes, access and security notifications — not marketing
- **Frequency:** e.g. message frequency varies; typically a few messages per month depending on account activity (or similar accurate phrasing)
- **Rates:** “Msg & data rates may apply”
- **STOP / HELP:** Reply STOP to cancel; reply HELP for help
- **Links:** Privacy Policy and Terms of Service on `authifi.com` (prefer real public URLs if known)

### Visual direction

- Authifi-adjacent: deep indigo/navy primary, high-contrast text, purposeful fonts (not Inter/Roboto/Arial defaults)
- Atmospheric background (subtle gradient or soft pattern), not flat single-color
- Mobile-first, readable on desktop
- Avoid purple-on-white cliché, cream/terracotta cliché, broadsheet look, dark-mode-first, glow stacks, emoji

### Motion

2–3 intentional motions only (e.g. soft entrance for brand/form, focus/hover states on controls). No decorative noise.

## File plan

| Path | Change |
| --- | --- |
| `docs/sms-opt-in.html` | Create standalone page (self-contained CSS; optional small inline JS for inert submit) |
| `docs/.nav.yml` | No change (page intentionally unlisted) |
| `mkdocs.yml` `exclude_docs` | Do **not** exclude this page — it must build/deploy so the preview URL works |

Assets: reuse `docs/assets/authifi-logo.png` if suitable for the page.

## Success criteria

- Page includes every SMS-provider required element listed above.
- Consent checkbox is unchecked on load and only becomes checked via user action.
- Submit label is clear affirmative language (“Yes, sign me up!”).
- Submit does not enroll or call any service.
- Page is reachable at `/sms-opt-in/` (or `/sms-opt-in.html` depending on MkDocs HTML naming) after deploy/preview.
- Page does not appear in the main docs nav.
- No “preview” banner on the page.

## Delivery

- Branch: `docs/sms-opt-in-preview`
- PR with a short summary and the preview URL for SMS-provider sharing
