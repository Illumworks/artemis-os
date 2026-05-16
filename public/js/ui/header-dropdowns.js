// Header dropdown menus with multi-level submenus

function closeAllDropdowns() {
  document.querySelectorAll(".header-dropdown.open").forEach((d) => d.classList.remove("open"));
  document.querySelectorAll(".header-dropdown-item.has-submenu.open").forEach((item) => {
    item.classList.remove("open");
    item.setAttribute("aria-expanded", "false");
  });
}

function closeSiblingSubmenus(item) {
  const menu = item.closest(".header-dropdown-menu");
  menu?.querySelectorAll(".header-dropdown-item.has-submenu.open").forEach((openItem) => {
    if (openItem === item) return;
    openItem.classList.remove("open");
    openItem.setAttribute("aria-expanded", "false");
  });
}

function toggleSubmenu(item, { forceOpen = false, focusFirstItem = false } = {}) {
  if (!item || !item.classList.contains("has-submenu")) return false;
  const shouldOpen = forceOpen || !item.classList.contains("open");
  closeSiblingSubmenus(item);
  item.classList.toggle("open", shouldOpen);
  item.setAttribute("aria-expanded", shouldOpen ? "true" : "false");

  if (shouldOpen && focusFirstItem) {
    const firstEnabled = item.querySelector(".header-submenu-item:not(.header-submenu-item-disabled):not([disabled])");
    firstEnabled?.focus();
  }

  return shouldOpen;
}

function initSubmenuAccessibility() {
  document.querySelectorAll(".header-dropdown-item.has-submenu").forEach((item) => {
    if (!item.hasAttribute("tabindex")) item.tabIndex = 0;
    item.setAttribute("role", "button");
    item.setAttribute("aria-haspopup", "menu");
    if (!item.hasAttribute("aria-expanded")) item.setAttribute("aria-expanded", "false");
    if (item.dataset.submenuBound === "true") return;

    item.addEventListener("click", (e) => {
      if (e.target.closest(".header-submenu-item")) return;
      e.stopPropagation();
      toggleSubmenu(item);
    });

    item.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggleSubmenu(item, { focusFirstItem: true });
        return;
      }
      if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        e.preventDefault();
        toggleSubmenu(item, { forceOpen: true, focusFirstItem: true });
        return;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        item.classList.remove("open");
        item.setAttribute("aria-expanded", "false");
      }
    });

    item.dataset.submenuBound = "true";
  });
}

// Toggle dropdown open/close
document.querySelectorAll(".header-dropdown-trigger").forEach((trigger) => {
  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    const dropdown = trigger.closest(".header-dropdown");
    if (!dropdown) return;
    const wasOpen = dropdown.classList.contains("open");

    // Close all dropdowns
    closeAllDropdowns();

    if (!wasOpen) dropdown.classList.add("open");
  });
});

// Close dropdowns on outside click
document.addEventListener("click", () => {
  closeAllDropdowns();
});

// Prevent menu clicks from closing the dropdown (except submenu item clicks)
document.querySelectorAll(".header-dropdown-menu").forEach((menu) => {
  menu.addEventListener("click", (e) => {
    if (e.target.closest("[data-preserve-dropdown='true']")) {
      e.stopPropagation();
      return;
    }
    if (!e.target.closest(".header-submenu-item") && !e.target.closest(".header-dropdown-item:not(.has-submenu)")) {
      e.stopPropagation();
    }
  });
});

// Submenu item selection — delegated so dynamically rendered model items work too
document.addEventListener("click", (e) => {
  const item = e.target.closest(".header-submenu-item");
  if (!item) return;
  if (item.disabled || item.classList.contains("header-submenu-item-disabled")) return;
  e.stopImmediatePropagation();

  const targetId = item.dataset.target;
  const value = item.dataset.value;
  const select = document.getElementById(targetId);
  if (!select) return;

  // Update hidden select and fire change event
  select.value = value;
  select.dispatchEvent(new Event("change", { bubbles: true }));

  // Update active state in submenu
  const submenu = item.closest(".header-submenu");
  submenu?.querySelectorAll(".header-submenu-item").forEach((s) => s.classList.remove("active"));
  item.classList.add("active");

  // Update display value
  const parent = item.closest(".header-dropdown-item");
  const display = parent?.querySelector(".header-dropdown-item-value");
  if (display) display.textContent = item.dataset.displayLabel || item.textContent.trim();

  // Close dropdown
  closeAllDropdowns();
});

// Tools dropdown items — close menu after click
document.querySelectorAll(".header-dropdown-item:not(.has-submenu)").forEach((item) => {
  if (item.id) {
    item.addEventListener("click", () => {
      document.querySelectorAll(".header-dropdown.open").forEach((d) => d.classList.remove("open"));
    });
  }
});

// Close on Escape
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeAllDropdowns();
  }
});

// Sync header dropdown display when hidden selects change programmatically
function syncDropdownDisplay(selectId) {
  const select = document.getElementById(selectId);
  if (!select) return;

  function sync() {
    const val = select.value;
    const items = document.querySelectorAll(`.header-submenu-item[data-target="${selectId}"]`);
    let matchedText = null;
    items.forEach((item) => {
      const isMatch = item.dataset.value === val;
      item.classList.toggle("active", isMatch);
      if (isMatch) matchedText = item.dataset.displayLabel || item.textContent.trim();
    });
    if (matchedText) {
      const parent = items[0]?.closest(".header-dropdown-item");
      const display = parent?.querySelector(".header-dropdown-item-value");
      if (display) display.textContent = matchedText;
    }
  }

  select.addEventListener("change", sync);
  // Initial sync for values restored from localStorage
  sync();
}

syncDropdownDisplay("source-select");
syncDropdownDisplay("model-select");
syncDropdownDisplay("perm-mode-select");
syncDropdownDisplay("max-turns-select");
initSubmenuAccessibility();
