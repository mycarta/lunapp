// Lunenburg Events — Phase 1 prototype frontend

const WEEKDAY_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTH_LONG = ["January", "February", "March", "April", "May", "June",
                    "July", "August", "September", "October", "November", "December"];

const KNOWN_CATEGORIES = new Set([
  "music", "theater", "arts", "festival", "film", "social", "dance", "community"
]);

// Parse "YYYY-MM-DD" as a local-time Date (no timezone surprises).
function parseLocalDate(s) {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function startOfDay(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

function daysBetween(a, b) {
  return Math.round((startOfDay(b) - startOfDay(a)) / 86400000);
}

function isWeekend(d) {
  const day = d.getDay();
  return day === 0 || day === 6;
}

function formatRelative(diff, date) {
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  if (diff > 1 && diff < 7) return WEEKDAY_LONG[date.getDay()];
  return null;
}

function formatDate(d) {
  return `${WEEKDAY_LONG[d.getDay()]}, ${MONTH_LONG[d.getMonth()]} ${d.getDate()}`;
}

function formatDateShort(d) {
  return `${MONTH_LONG[d.getMonth()]} ${d.getDate()}`;
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "className") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else {
      node.setAttribute(k, v);
    }
  }
  for (const child of [].concat(children)) {
    if (child == null || child === false) continue;
    if (typeof child === "string") node.appendChild(document.createTextNode(child));
    else node.appendChild(child);
  }
  return node;
}

function categoryClass(category) {
  const c = (category || "").toLowerCase();
  return KNOWN_CATEGORIES.has(c) ? `category--${c}` : "category--community";
}

function categoryLabel(category) {
  if (!category) return "Event";
  return category.charAt(0).toUpperCase() + category.slice(1);
}

function renderEvent(evt) {
  const header = el("div", { className: "event__header" }, [
    el("h3", { className: "event__title" }, [
      evt.url
        ? el("a", { href: evt.url, target: "_blank", rel: "noopener noreferrer", text: evt.title })
        : document.createTextNode(evt.title)
    ]),
    el("span", {
      className: `category ${categoryClass(evt.category)}`,
      text: categoryLabel(evt.category)
    })
  ]);

  const timeParts = [];
  if (evt.time) timeParts.push(evt.time);
  if (evt.end_time) timeParts.push(`– ${evt.end_time}`);
  const timeStr = timeParts.join(" ");

  const metaChildren = [];
  if (timeStr) {
    metaChildren.push(el("span", { className: "event__time", text: timeStr }));
  }
  if (evt.venue) {
    if (metaChildren.length) metaChildren.push(el("span", { className: "dot", text: "·" }));
    metaChildren.push(el("span", { className: "event__venue", text: evt.venue }));
  }
  if (evt.location && evt.location !== evt.venue) {
    metaChildren.push(el("span", { className: "dot", text: "·" }));
    metaChildren.push(el("span", { className: "event__location", text: evt.location }));
  }
  const meta = el("p", { className: "event__meta" }, metaChildren);

  const desc = evt.description
    ? el("p", { className: "event__description", text: evt.description })
    : null;

  const links = el("div", { className: "event__links" });
  if (evt.ticket_url) {
    links.appendChild(el("a", {
      className: "event__link event__link--ticket",
      href: evt.ticket_url,
      target: "_blank",
      rel: "noopener noreferrer",
      text: "Tickets"
    }));
  }
  if (evt.ics_url) {
    links.appendChild(el("a", {
      className: "event__link",
      href: evt.ics_url,
      target: "_blank",
      rel: "noopener noreferrer",
      text: "Add to calendar"
    }));
  }
  if (evt.url && !evt.ticket_url) {
    links.appendChild(el("a", {
      className: "event__link",
      href: evt.url,
      target: "_blank",
      rel: "noopener noreferrer",
      text: "Details"
    }));
  }

  const footer = el("div", { className: "event__footer" }, [
    evt.price ? el("span", { className: "event__price", text: evt.price }) : el("span"),
    links
  ]);

  return el("article", { className: "event", "data-source": evt.source || "" },
    [header, meta, desc, footer].filter(Boolean));
}

function renderDayGroup(dateKey, events, today) {
  const date = parseLocalDate(dateKey);
  const diff = daysBetween(today, date);
  const relative = formatRelative(diff, date);

  const headingChildren = [];
  if (relative) {
    headingChildren.push(el("span", { className: "when-relative", text: relative }));
    headingChildren.push(el("span", { className: "when-date", text: formatDateShort(date) }));
  } else {
    headingChildren.push(el("span", { className: "when-relative", text: formatDate(date) }));
  }
  if (isWeekend(date) && diff >= 0 && diff <= 7 && diff > 1) {
    headingChildren.push(el("span", { className: "weekend-tag", text: "Weekend" }));
  }

  const heading = el("h2", { className: "day-group__heading" }, headingChildren);
  const groupClass = "day-group" + (diff === 0 ? " day-group--today" : "");
  const group = el("section", { className: groupClass });
  group.appendChild(heading);
  for (const evt of events) group.appendChild(renderEvent(evt));
  return group;
}

function groupByDate(events) {
  const groups = new Map();
  for (const e of events) {
    if (!groups.has(e.date)) groups.set(e.date, []);
    groups.get(e.date).push(e);
  }
  for (const list of groups.values()) {
    list.sort((a, b) => (a.time || "").localeCompare(b.time || ""));
  }
  return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

function renderEmpty(container) {
  container.appendChild(el("div", { className: "empty" }, [
    el("h2", { className: "empty__title", text: "No upcoming events" }),
    el("p", { text: "Check back soon — new events are added regularly." })
  ]));
}

function renderLastUpdated(iso) {
  if (!iso) return;
  const updated = new Date(iso);
  if (isNaN(updated)) return;
  const fmt = updated.toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit"
  });
  document.getElementById("last-updated").textContent = `Last updated ${fmt}`;
}

async function loadEvents() {
  const container = document.getElementById("events");
  const status = document.getElementById("status");
  try {
    const resp = await fetch("events.json", { cache: "no-cache" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const today = startOfDay(new Date());
    const upcoming = (data.events || [])
      .filter(e => e.date && parseLocalDate(e.date) >= today);

    status.remove();
    container.replaceChildren();

    if (upcoming.length === 0) {
      renderEmpty(container);
    } else {
      for (const [dateKey, events] of groupByDate(upcoming)) {
        container.appendChild(renderDayGroup(dateKey, events, today));
      }
    }
    renderLastUpdated(data.last_updated);
  } catch (err) {
    status.textContent = "Couldn't load events. Please try again later.";
    status.style.color = "var(--magenta)";
    console.error("Failed to load events:", err);
  }
}

if ("serviceWorker" in navigator && location.protocol !== "file:") {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch(err => {
      console.warn("SW registration failed:", err);
    });
  });
}

loadEvents();
