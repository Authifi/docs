# Authifi Documentation

Public documentation for the Authifi identity and access management platform.

**Live Site**: [https://docs.authifi.io](https://docs.authifi.io)

## For Documentation Writers

See [CONTRIBUTING.md](CONTRIBUTING.md) for instructions on editing documentation. **No local setup is required.** Edit directly on GitHub.com or using GitHub.dev (press `.` on the repo page).

## Cloudflare Pages Setup

To deploy this documentation site:

### 1. Connect Repository to Cloudflare Pages

> **Important**: Use **Pages**, not Workers. The setup screens look similar but Pages is for static sites.

1. Log in to [Cloudflare Dashboard](https://dash.cloudflare.com)
2. Go to **Workers & Pages** in the left sidebar
3. Click **Create** button
4. Select the **Pages** tab (not Workers)
5. Click **Connect to Git**
6. Authorize GitHub and select the `AxleResearch/authifi-docs` repository
7. Configure build settings:
   - **Project name**: `authifi` (this becomes the `*.pages.dev` hostname, i.e. `authifi.pages.dev`)
   - **Production branch**: `main`
   - **Framework preset**: None
   - **Build command**: `pip install -r requirements.txt && mkdocs build`
   - **Build output directory**: `site`
8. Click **Save and Deploy**

> **Python version**: The build requires Python 3.10+ (`mkdocs-awesome-nav`). This repo pins it via a `.python-version` file (`3.12`), which Cloudflare Pages reads automatically. To override from the dashboard instead, set a `PYTHON_VERSION` environment variable under **Settings → Environment variables**.

### 2. Verify Deploy Previews

Once connected, every pull request will automatically get a preview URL. Contributors will see a comment on their PR with a link like:

```
https://abc123.authifi.pages.dev
```

### 3. Custom Domain

The production site is served at the custom domain **`docs.authifi.io`**. The
`*.pages.dev` URL keeps working and is still used for deploy previews.

#### A. Add the custom domain in Cloudflare Pages

1. Go to **Workers & Pages → `authifi` → Custom domains**
2. Click **Set up a custom domain**
3. Enter `docs.authifi.io` and click **Continue**

#### B. Configure DNS

If the `authifi.io` zone is managed in the **same Cloudflare account**, Pages
creates the DNS record automatically. **Activate** it when prompted. The
record will be:

```
Type:   CNAME
Name:   docs
Target: authifi.pages.dev
Proxy:  Proxied (orange cloud)
```

If `authifi.io` is hosted with **another DNS provider**, create the record there
manually:

```
Type:   CNAME
Name:   docs
Value:  authifi.pages.dev
```

#### C. Verify

After DNS propagates (usually a few minutes), Cloudflare provisions a TLS
certificate and the domain shows **Active**. Then:

```bash
curl -sI https://docs.authifi.io/
```

The `site_url` in `mkdocs.yml` is already set to `https://docs.authifi.io`, so
canonical links and `sitemap.xml` resolve to the custom domain after the next
build.

## Local Development

### Prerequisites

- Python 3.10+ (required by the `mkdocs-awesome-nav` plugin; the pinned version lives in `.python-version`)
- pip

### Setup

```bash
# Set up a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
python3 -m pip install -r requirements.txt

# Start development server
mkdocs serve
```

Open http://127.0.0.1:8000 to view the documentation locally.

### Build

```bash
mkdocs build
```

The static site is generated in the `site/` directory.

## Agent Readiness

This site publishes static assets for AI agent discovery and crawl policy control.

### Static assets (in repo)

| Path | Purpose |
|------|---------|
| `/robots.txt` | Crawl rules, AI bot entries, Content Signals |
| `/_headers` | Cloudflare Link headers and Content-Type overrides |
| `/.well-known/api-catalog` | RFC 9727 documentation catalog |
| `/auth.md` | Agent access instructions |
| `/.well-known/agent-skills/index.json` | Agent skills discovery index |

The MkDocs hook at `docs/hooks/agent_assets.py` generates `sitemap.xml`, copies static agent files, and updates skill SHA-256 digests on each build.

### Markdown for Agents (Cloudflare dashboard)

Markdown content negotiation requires a Cloudflare zone setting. It is not implemented in application code.

1. Log in to the [Cloudflare dashboard](https://dash.cloudflare.com)
2. Select the `authifi.io` zone (the zone that serves the custom docs domain `docs.authifi.io`)
3. Open **AI Crawl Control**
4. Enable **Markdown for Agents**

Requires Pro, Business, or Enterprise. Alternatively, enable via API:

```bash
curl -X PATCH "https://api.cloudflare.com/client/v4/zones/{zone_id}/settings/content_converter" \
  --header "Authorization: Bearer {api_token}" \
  --header "Content-Type: application/json" \
  --data '{"value":"on"}'
```

Do not enable conflicting robots.txt management in AI Crawl Control while `/robots.txt` is maintained in this repository.

### Verification

After deploy:

```bash
curl -sI https://docs.authifi.io/robots.txt
curl -sI https://docs.authifi.io/
curl -sH "Accept: application/linkset+json" https://docs.authifi.io/.well-known/api-catalog
curl -sI https://docs.authifi.io/auth.md
curl -s https://docs.authifi.io/.well-known/agent-skills/index.json
curl -sI https://docs.authifi.io/ -H "Accept: text/markdown"
```

Run a full scan:

```bash
curl -s -X POST https://isitagentready.com/api/scan \
  -H "Content-Type: application/json" \
  -d '{"url":"https://docs.authifi.io"}'
```

## Project Structure

```
authifi-docs/
├── .changeset/              # Changesets configuration
├── docs/                    # Documentation source files
│   ├── index.md            # Home page
│   ├── robots.txt          # Crawl and AI bot policy
│   ├── _headers            # Cloudflare Pages header rules
│   ├── auth.md             # Agent access instructions
│   ├── .well-known/        # Agent discovery endpoints
│   ├── hooks/              # MkDocs build hooks
│   ├── javascripts/        # Client-side scripts (WebMCP)
│   ├── authorization/      # Authorization concepts
│   ├── guides/             # Administrator guides
│   └── security/           # Security documentation
├── mkdocs.yml              # MkDocs configuration
├── package.json            # Node.js dependencies (for changesets)
├── requirements.txt        # Python dependencies
├── CONTRIBUTING.md         # Contributor guide
└── README.md               # This file
```

## Versioning with Changesets

This project uses [Changesets](https://github.com/changesets/changesets) to manage versioning and generate changelogs.

### Adding a Changeset

When you make changes that should be noted in the changelog, add a changeset:

```bash
npm run changeset:add
```

This will prompt you to:
1. Select the type of change (major, minor, or patch)
2. Write a summary of the change

The changeset file is committed with your PR and consumed when releasing.

### Version Types

- **patch**: Bug fixes, typo corrections, small updates
- **minor**: New documentation pages, significant content additions
- **major**: Major restructuring, breaking changes to URL structure

### Releasing (Automated)

When PRs with changesets are merged to `main`, a GitHub Action automatically:

1. Detects pending changesets
2. Applies version bumps directly to `package.json` and `CHANGELOG.md`
3. Commits and pushes the changes

No manual intervention required.

To manually release (if needed):

```bash
npm run version
```

### Checking Status

To see pending changesets:

```bash
npm run changeset:status
```

## Technology Stack

- **Static Site Generator**: [MkDocs](https://www.mkdocs.org/)
- **Theme**: [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/)
- **Hosting**: [Cloudflare Pages](https://pages.cloudflare.com/)
- **Features**: Full-text search, dark mode, mobile responsive, deploy previews
