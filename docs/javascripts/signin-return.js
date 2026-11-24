(function () {
  "use strict";

  // The header link is rendered with the page path only. A browser never
  // sends the fragment, so the section a reader had open is lost unless it
  // is copied into the `next` query value before the request.
  var link = document.querySelector("a.md-header__signin");
  if (!link) {
    return;
  }

  var href = link.getAttribute("href");
  if (!href) {
    return;
  }

  var baseUrl = new URL(href, window.location.origin);
  var baseNext = baseUrl.searchParams.get("next");
  if (!baseNext || baseNext.indexOf("#") !== -1) {
    return;
  }

  // Re-applying always starts from this path, so moving to another section
  // replaces the fragment instead of stacking a second one onto `next`.
  link.setAttribute("data-authifi-next", baseNext);

  function headingHash() {
    var hash = window.location.hash;
    // `$` matches before a trailing newline, which is not a heading id and
    // which the server rejects outright. The length check is what excludes it.
    var match = /^#([A-Za-z0-9][A-Za-z0-9_-]*)$/.exec(hash);
    if (!match || match[1].length + 1 !== hash.length) {
      return "";
    }
    return hash;
  }

  function apply() {
    var hash = headingHash();
    if (!hash) {
      link.setAttribute("href", href);
      return;
    }
    var url = new URL(baseUrl.pathname, window.location.origin);
    url.searchParams.set("next", link.getAttribute("data-authifi-next") + hash);
    link.setAttribute("href", url.pathname + url.search);
  }

  link.addEventListener("click", apply);
  window.addEventListener("hashchange", apply);
  apply();
})();
