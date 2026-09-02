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

## Design

### Page composition

Single first-viewport composition:

1. **Brand:** Authifi as the hero-level signal (logo + wordmark)
2. **Headline:** SMS notifications (or equivalent)
3. **Supporting sentence:** Short explanation that Authifi can send account and security alerts by text
4. **Form:** Phone field + consent checkbox + submit
5. **Disclosure block:** Frequency, rates, STOP/HELP, links to Privacy Policy and Terms

No cards in the hero. No stats, promo chips, or secondary marketing blocks.

### Form behavior (preview only)

- `action` omitted or `#`; `submit` prevented with a no-op handler (or button `type="button"` with inert confirm styling)
- Phone input: `type="tel"`, `autocomplete="tel"`, placeholder example
- Consent checkbox required for visual completeness (HTML `required` acceptable even if submit is inert)
- No network requests

### Consent and disclosure copy (intent)

- Explicit opt-in to receive Authifi account/security SMS at the provided number
- Message types: login alerts, MFA, access/security notifications — not marketing
- Frequency: as needed for account activity (or similar accurate phrasing)
- “Msg & data rates may apply”
- Reply STOP to cancel, HELP for help
- Links: Privacy Policy and Terms of Service on `authifi.com` (use real public URLs if known; otherwise `https://authifi.com` paths that match the public site)

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

- Page renders as a branded SMS opt-in with phone, consent, disclosure, and policy links.
- Submit does not enroll or call any service.
- Page is reachable at `/sms-opt-in/` (or `/sms-opt-in.html` depending on MkDocs HTML naming) after deploy/preview.
- Page does not appear in the main docs nav.
- No “preview” banner on the page.

## Delivery

- Branch: `docs/sms-opt-in-preview`
- PR with a short summary and the preview URL for SMS-provider sharing
