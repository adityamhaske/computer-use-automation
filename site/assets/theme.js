// The colour-theme toggle, shared by every page of this site.
//
// It was copy-pasted into the landing page and the documentation page and missing entirely from
// the design write-up, so the write-up had no toggle at all and the other two could drift apart.
// One file means one behaviour, and a reader's choice follows them across all three pages.
//
// Light is the default unconditionally: the OS preference is deliberately not consulted, so the
// site opens the same way for everyone. Dark is a choice, and it persists once made.

(function () {
  const KEY = "cua-theme";
  const SUN =
    '<circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line>' +
    '<line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>' +
    '<line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line>' +
    '<line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>' +
    '<line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>';
  const MOON = '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>';

  function read() {
    // Private browsing and blocked site data both make this throw rather than return null.
    try {
      return localStorage.getItem(KEY);
    } catch {
      return null;
    }
  }

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      /* A theme that cannot be remembered should still be applied. */
    }
    const icon = document.getElementById("themeIcon");
    if (icon) icon.innerHTML = theme === "dark" ? MOON : SUN;
  }

  apply(read() || "light");

  const toggle = document.getElementById("themeToggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme");
      apply(current === "dark" ? "light" : "dark");
    });
  }
})();
