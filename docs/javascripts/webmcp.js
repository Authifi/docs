(function () {
  "use strict";

  if (!navigator.modelContext || typeof navigator.modelContext.registerTool !== "function") {
    return;
  }

  const controller = new AbortController();
  const signal = controller.signal;

  function getSearchInput() {
    return document.querySelector('input[type="search"], input[data-md-component="search-query"]');
  }

  navigator.modelContext.registerTool(
    {
      name: "search_docs",
      description: "Search the Authifi documentation site using the built-in MkDocs Material search.",
      inputSchema: {
        type: "object",
        properties: {
          query: {
            type: "string",
            description: "Search terms to find relevant documentation pages.",
          },
        },
        required: ["query"],
      },
      execute: async function (args) {
        const query = args && args.query ? String(args.query).trim() : "";
        if (!query) {
          return { error: "query is required" };
        }

        const input = getSearchInput();
        if (!input) {
          return { error: "Search input not found on this page" };
        }

        input.focus();
        input.value = query;
        input.dispatchEvent(new Event("input", { bubbles: true }));

        return {
          query: query,
          message: "Search query submitted. Read visible search results from the page DOM.",
        };
      },
    },
    { signal: signal }
  );

  navigator.modelContext.registerTool(
    {
      name: "list_sections",
      description: "List top-level documentation sections from the site navigation.",
      inputSchema: {
        type: "object",
        properties: {},
      },
      execute: async function () {
        let links = Array.from(
          document.querySelectorAll(".md-nav--primary > .md-nav__list > .md-nav__item > .md-nav__link")
        );

        if (links.length === 0) {
          links = Array.from(document.querySelectorAll(".md-tabs__link"));
        }

        return {
          sections: links.map(function (link) {
            return {
              title: link.textContent.trim(),
              href: link.getAttribute("href"),
            };
          }),
        };
      },
    },
    { signal: signal }
  );

  navigator.modelContext.registerTool(
    {
      name: "get_page_markdown_url",
      description:
        "Return the current page URL and instructions for fetching a markdown representation.",
      inputSchema: {
        type: "object",
        properties: {},
      },
      execute: async function () {
        return {
          url: window.location.href,
          acceptHeader: "text/markdown",
          instructions:
            "Re-fetch this URL with Accept: text/markdown when Markdown for Agents is enabled on the zone.",
        };
      },
    },
    { signal: signal }
  );

  window.addEventListener(
    "pagehide",
    function () {
      controller.abort();
    },
    { once: true }
  );
})();
