# Contributing to Authifi Documentation

Thank you for contributing to the Authifi documentation! This guide explains how to make changes to the documentation.

## Quick Start (No Setup Required)

You can edit documentation directly on GitHub without installing anything on your computer.

### Option 1: Edit a Single File on GitHub.com

This is the simplest approach for quick edits to a single file.

**Step 1: Navigate to the file**

1. Go to [github.com/AxleResearch/authifi-docs](https://github.com/AxleResearch/authifi-docs)
2. Click on the `docs` folder
3. Navigate to the file you want to edit (e.g., `guides/tenant-admin-guide.md`)

**Step 2: Enter edit mode**

1. Click the **pencil icon** in the top-right corner of the file view
2. The icon tooltip says "Edit this file"

**Step 3: Make your changes**

1. Edit the content using the GitHub editor
2. Use the **Preview** tab to see how your Markdown will render

**Step 4: Create a pull request**

1. Scroll down to the "Commit changes" section
2. Enter a short description of your change (e.g., "Fix typo in tenant admin guide")
3. Select **"Create a new branch for this commit and start a pull request"**
4. GitHub will suggest a branch name like `username-patch-1` — you can keep it or change it
5. Click **Propose changes**
6. On the next page, review your changes and click **Create pull request**

### Option 2: Use GitHub.dev (VS Code in Browser)

This is better for editing multiple files or making larger changes.

**Step 1: Open the web editor**

1. Go to [github.com/AxleResearch/authifi-docs](https://github.com/AxleResearch/authifi-docs)
2. Press the `.` key (period) on your keyboard
3. This opens a full VS Code editor in your browser at `github.dev`

**Step 2: Make your changes**

1. Use the file explorer on the left to navigate to files in the `docs/` folder
2. Edit files just like in regular VS Code
3. You can edit multiple files before committing

**Step 3: Commit your changes**

1. Click the **Source Control** icon in the left sidebar (or press `Ctrl+Shift+G`)
2. You'll see a list of changed files
3. Enter a commit message in the text box at the top
4. Click the **checkmark** to commit

**Step 4: Create a pull request**

1. After committing, click the **branch icon** in the bottom-left corner
2. Select **"Create Pull Request"**
3. Fill in the PR title and description
4. Click **Create**

### Option 3: Create a New File

To add a new documentation page:

1. Navigate to the appropriate folder (e.g., `docs/guides/`)
2. Click **Add file** → **Create new file**
3. Enter a filename ending in `.md` (e.g., `my-new-guide.md`)
4. Add your content starting with a heading: `# My New Guide`
5. Follow the same commit and PR process as Option 1

**Important**: After adding a new file, you'll also need to update `mkdocs.yml` to add it to the navigation (see [Adding New Pages](#adding-new-pages)).

---

## Viewing Preview Deployments

Every pull request automatically gets a **preview deployment** so you can see exactly how your changes will look on the live site.

### How it works

1. When you create or update a pull request, Cloudflare Pages automatically builds a preview
2. The build takes about 1-2 minutes
3. A bot will comment on your PR with the preview URL

### Finding the preview link

Look for a comment from the Cloudflare Pages bot on your pull request. It will include a link like:

```
https://abc123.authifi.pages.dev
```

You can also find the preview URL in the **Checks** section of your PR:

1. Scroll down to the checks at the bottom of your PR
2. Look for "Cloudflare Pages"
3. Click **Details** to go directly to the preview

### What to check in the preview

Before requesting a review, verify:

- [ ] Your content appears correctly formatted
- [ ] All links work (internal and external)
- [ ] Code blocks display properly
- [ ] Tables render correctly
- [ ] The navigation menu includes any new pages
- [ ] Search finds your new content (type in the search box)

### Preview vs. Production

- **Preview URL**: `https://<build-id>.authifi.pages.dev` — unique to each PR
- **Production URL**: `https://docs.authifi.io` — updated when PRs are merged to `main`

---

## Documentation Structure

```
docs/
├── index.md                 # Home page
├── authorization/           # Authorization concepts and admin roles
├── guides/                  # Step-by-step administrator guides
└── security/               # Security configuration and best practices
```

## Writing Guidelines

### File Format

- All documentation is written in **Markdown** (`.md` files)
- Use standard Markdown syntax for headings, lists, links, and code blocks

### Headings

- Use `#` for the page title (only one per page)
- Use `##` for major sections
- Use `###` for subsections
- Don't skip heading levels (e.g., don't go from `##` to `####`)

### Code Blocks

Use triple backticks with a language identifier:

````markdown
```json
{
  "example": "code"
}
```
````

### Tables

Use Markdown tables for structured information:

```markdown
| Column 1 | Column 2 | Column 3 |
| -------- | -------- | -------- |
| Value 1  | Value 2  | Value 3  |
```

### Admonitions (Notes and Warnings)

Use admonition syntax for callouts:

```markdown
!!! note
This is a note with additional information.

!!! warning
This is a warning about potential issues.

!!! tip
This is a helpful tip.
```

### Links

- **Internal links**: Use relative paths: `[Link Text](../guides/tenant-admin-guide.md)`
- **External links**: Use full URLs: `[Link Text](https://example.com)`

## Adding New Pages

1. Create a new `.md` file in the appropriate folder (`authorization/`, `guides/`, or `security/`)
2. Add a title as the first line: `# Page Title`
3. Update `mkdocs.yml` to add your page to the navigation

Example addition to `mkdocs.yml`:

```yaml
nav:
  - Guides:
      - Your New Guide: guides/your-new-guide.md
```

## Local Validation (Optional)

After completing the [local setup](README.md#local-development), run:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/mkdocs build --strict
```

Use `.venv/bin/mkdocs serve` for a browser preview. Changes to Markdown files
reload automatically.

## Need Help?

- For documentation questions, open an issue on GitHub
- For Authifi product questions, contact your administrator
