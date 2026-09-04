(function () {
  "use strict";

  if (!navigator.modelContext || typeof navigator.modelContext.registerTool !== "function") {
    return;
  }

  function getSearchInput() {
    return document.querySelector('input[type="search"], input[data-md-component="search-query"]');
  }

  function getSectionLinks() {
    const primary = Array.from(
      document.querySelectorAll(".md-nav--primary > .md-nav__list > .md-nav__item > .md-nav__link")
    );
    return primary.length > 0 ? primary : Array.from(document.querySelectorAll(".md-tabs__link"));
  }

  const controller = new AbortController();
  const signal = controller.signal;

  // Public pages render a header without the search control and without the
  // primary navigation, so each tool is registered only where the page can
  // actually answer it. Registering unconditionally would hand an agent a
  // search tool whose only possible reply is that the input does not exist.
  if (getSearchInput()) {
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
  }

  if (getSectionLinks().length > 0) {
    navigator.modelContext.registerTool(
      {
        name: "list_sections",
        description: "List top-level documentation sections from the site navigation.",
        inputSchema: {
          type: "object",
          properties: {},
        },
        execute: async function () {
          return {
            sections: getSectionLinks().map(function (link) {
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
  }

  window.addEventListener(
    "pagehide",
    function () {
      controller.abort();
    },
    { once: true }
  );
})();
