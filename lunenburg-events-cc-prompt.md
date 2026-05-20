# Lunenburg Events — Project Brief for Claude Code

## What We're Building

A free, ad-free, community events app for the town of Lunenburg, Nova Scotia. It shows entertainment and cultural events for the next two weeks, scraped from local venue websites. Hosted as a static site (GitHub Pages), with a Python scraper running on a GitHub Actions cron.

The scope is: music, theater, arts, festivals, social/cultural events, entertainment (trivia nights, movies, sports tournaments, food trucks, etc.). NOT municipal meetings, waste schedules, council sessions, or recreation programs.

---

## Architecture

### Static site + scheduled scraper

1. **Python scraper** (runs via GitHub Actions cron, 1–2x daily):
   - One parser function per source
   - Outputs `events.json` with unified schema
   - Commits updated JSON to the repo; GitHub Pages auto-deploys

2. **Frontend** (static HTML/CSS/JS, single page):
   - Reads `events.json` and renders a 2-week chronological event list
   - Mobile-first, PWA-capable (service worker for offline, manifest for home screen install)
   - No framework needed — vanilla JS is fine

3. **GitHub Actions cron** (free tier: 2000 min/month, we'll use ~1–2%):
   - Runs the scraper
   - Commits `events.json` if changed

### Event JSON schema

```json
{
  "events": [
    {
      "title": "Gina Burgess Quintet & Natural Elements",
      "date": "2026-05-22",
      "time": "7:00 PM",
      "end_time": "9:00 PM",
      "venue": "Old Confidence Lodge",
      "location": "3831 NS-332, Riverport",
      "description": "An amazing night of fiddling, Inuit throat singing, Brazilian percussion...",
      "url": "https://www.oldconfidence.ca/events/gina-burgess-lunenburg-county",
      "ticket_url": "https://www.onstagedirect.com/buy/...",
      "price": "$25; $15 for Ages 18 & Under",
      "category": "music",
      "source": "old_confidence_lodge",
      "ics_url": "https://www.oldconfidence.ca/events/gina-burgess-lunenburg-county?format=ical"
    }
  ],
  "last_updated": "2026-05-18T10:30:00-03:00"
}
```

Categories: `music`, `theater`, `arts`, `festival`, `film`, `social`, `dance`, `community`

---

## Tier 1 Sources (scrapability assessed)

### 1. Old Confidence Lodge — EASY
- **URL:** `https://www.oldconfidence.ca/events`
- **Platform:** Squarespace
- **Structure:** Extremely clean. Each event is a repeating block with: title (H1), date/time, location with map link, Google Calendar link, per-event ICS link (`?format=ical`), description text, ticket URL.
- **Notes:** Events include past events below current ones — filter by date. Some events are at Bus Stop Theatre in Halifax, not Riverport — filter by location.

### 2. LAMP (Lunenburg Academy of Music Performance) — EASY
- **URL:** `https://www.lampns.ca/concert-schedule`
- **Platform:** Squarespace
- **Structure:** Same clean pattern as Old Confidence. Each event has title, date/time, location, ICS link, description, ticket link.
- **Notes:** Also runs free "Tunes on Tuesday" noon-hour series and "Sound & Sketch" collaborations with the School of the Arts. May have events at partner locations.

### 3. Lunenburg School of the Arts — MODERATE
- **URL:** `https://lunenburgarts.org/events/`
- **Platform:** WordPress
- **Structure:** Blog-style event posts. Dates are in the text body, not structured metadata. Need to parse date/time from post content.
- **Notes:** Hosts Musique Royale "Cookie Concerts", exhibitions, lectures, workshops. Filter for public events vs. multi-week workshop courses (the app should show concerts/lectures/openings, not 6-week pottery classes).

### 4. Musique Royale — MODERATE
- **URL:** `http://musiqueroyale.com/events/`
- **Platform:** Custom CMS
- **Structure:** Event listings with dates, venues, descriptions. Each event has its own detail page with full venue address, ticket link, and artist info. Province-wide organization.
- **Notes:** CRITICAL — must filter for Lunenburg-area venues only. Relevant venues include: Lunenburg School of the Arts, St. John's Anglican Church (Lunenburg), Central United Church (Lunenburg), Lightship Brewery (Lunenburg), Old Confidence Lodge (Riverport), St. John's Lutheran Church (Mahone Bay), and Cecilia's Retreat (Mahone Bay). Discard events in Halifax, Wolfville, Cape Breton, Bell Island, etc.

### 5. Lunenburg Opera House / Folk Harbour Society — MODERATE
- **URL (third-party events):** `https://www.folkharbour.com/other-events/`
- **URL (concert series):** `https://folkharbour.ca/concerts`
- **Platform:** WordPress
- **Structure:** Event listings, two separate pages. The concert series page lists the Folk Harbour Society's own programming; the "other events" page lists events by groups renting the Opera House venue. Concert series tickets sold via TicketPro Atlantic (lfhf.ticketpro.ca).
- **Notes:** Need to scrape both pages and deduplicate. NOTE (May 2026): folkharbour.com/other-events/ now 404s — the site was consolidated into folkharbour.ca. Parser handles this gracefully with a warning. Only the concert series page is active. The Opera House is at 290 Lincoln St, Lunenburg.

### 6. Folk Harbour Festival — EASY but SEASONAL
- **URL:** `https://folkharbour.ca/`
- **Platform:** WordPress
- **Notes:** The 2026 festival is August 6–9. Outside festival season, this source goes quiet. Lineup details appear on the concert series page above. Low-frequency scraping is fine — check weekly or when festival season approaches.

### 7. Fisheries Museum of the Atlantic — LOW PRIORITY
- **URL:** `https://fisheriesmuseum.novascotia.ca/events`
- **Platform:** Nova Scotia Museum CMS
- **Notes:** Seasonal (mid-May to mid-October). Events page is currently empty. Hosts Boxwood Festival and occasional special events. Check monthly during season. Consider scraping their Facebook page as a fallback, or just manually adding known annual events.

### 8. Canadian Dory Racing Association — SKIP SCRAPING
- **URL:** `https://www.canadiandoryracing.com/`
- **Notes:** Minimal static website. Events are primarily posted on their Facebook page. Races are seasonal (elimination races in June, International Dory Races in August). Best handled as manually seeded annual entries in the JSON rather than automated scraping.

### 9. Symphony Nova Scotia — MODERATE (venue filter required)
- **URL:** `https://symphonynovascotia.ca/concerts-and-tickets/concerts/`
- **Lunenburg venue page:** `https://symphonynovascotia.ca/concerts-and-tickets/concerts/central-united-church/`
- **Platform:** WordPress
- **Structure:** Concert listings with dates, venues, descriptions, ticket links. Province-wide orchestra.
- **Notes:** Must filter for Lunenburg-area venues only: Central United Church (136 Cumberland St, Lunenburg) and St. John's Anglican Church (64 Townsend St, Lunenburg). Lunenburg concerts are presented in partnership with Musique Royale — may duplicate events from source #4, so deduplicate. Their 2026/27 season single tickets go on sale June 10, 2026.

### 10. Boxwood Festival — EASY but SEASONAL
- **URL:** `https://boxwood.org/canada/`
- **Platform:** WooCommerce / WordPress
- **Structure:** Individual concert events sold as products (Opening Concert, Midweek Concert, Finale Concert, Waterfront Ceili). Each has date, time, price, description.
- **Notes:** The 2026 festival is July 26–31 in Lunenburg. Concerts and the Waterfront Ceili are open to the public (not just workshop registrants). Outside festival season, no events. Check monthly starting June; during festival week, include all public concerts and the free Waterfront Ceili.

### 11. Lunenburg Heritage Society — MODERATE (atom feed, date parsing needed)
- **URL:** `https://lunenburgheritagesociety.ca/pages/all-events`
- **Atom feed:** `https://lunenburgheritagesociety.ca/blogs/events.atom`
- **Platform:** Shopify
- **Structure:** The atom feed (`/blogs/events.atom`) returns 12 entries with title, link, published/updated timestamps, and full HTML body in structured XML. Basic `requests` works, no blocking. Per-article JSON endpoints (`/blogs/events/<slug>.json`) return 404 — use the atom feed instead.
- **Date parsing challenge:** The atom feed's timestamps reflect when the article was *posted*, not when the event *happens*. Event dates are in the article body as prose with inconsistent formats: "Wednesday, March 4th, 7-9pm" / "June 7, 2025, 5-7 PM" / "On July 8 at 7pm" — no standard structure. Parser needs: regex pass for common date patterns + `dateutil.parser` fallback, using the article's `published` date to disambiguate missing years (events almost always happen near publish time).
- **Venue extraction:** Also prose-based: "The Tin Roof Distillery, 15 Lincoln Street" / "Lunenburg War Memorial Arena" — heuristic-match against a known venue list.
- **Notes:** Runs the Knaut-Rhuland House Museum, Nova Scotia Folk Art Festival (August), Heritage House Tour (biennial), and Heritage Bandstand Concert Series. Feed contains many past events (2023, 2024) — filter aggressively to future-only after date parsing. Test fixtures saved in `_scrape_test/heritage_events.atom` and `_scrape_test/heritage_page.html`.

### 12. St. John's Anglican Church — LOW PRIORITY (venue)
- **URL:** `https://www.stjohnslunenburg.org/events/concerts/`
- **Platform:** WordPress with calendar plugin
- **Structure:** Calendar widget showing all church events. Concerts are mixed in with worship services, choir practices, and play groups.
- **Notes:** St. John's is primarily a venue — concerts there are hosted by Musique Royale, Symphony Nova Scotia, and other orgs, and will be captured via those sources. The church's own calendar is mostly recurring services. Low-priority scrape target; include only to catch concerts not cross-listed elsewhere. Filter out worship services, choir practices, prayer groups, and family play groups.

---

## Design Spec

### Visual identity

The app's visual identity comes from a hand-drawn watercolor + ink illustration of a Lunenburg Victorian house by the developer (a local artist). The drawing is included in the repo as the hero/banner image.

### Color palette (extracted from the watercolor)

```css
:root {
  /* Primary — from the watercolor sky */
  --sky-blue: #4E7FCC;
  --sky-light: #6B9BE8;
  
  /* Accent — from the vivid green clapboard */
  --green: #85BD5A;
  --green-dark: #639326;
  
  /* Accent — from the magenta/purple tower */
  --magenta: #6D366D;
  --lavender: #B998DD;
  
  /* Neutrals — from the ink linework */
  --ink: #1B2024;
  --ink-soft: #2E2B39;
  
  /* Background */
  --paper: #FAF8F5;  /* warm white, like watercolor paper */
  --paper-dark: #F0EDE8;
}
```

### Design direction: "Watercolor Editorial"

- **Hero banner:** The watercolor drawing, used as a header image. Cropped horizontally from the top portion (sky + turret). Below it, the app title "Lunenburg Events" in a warm serif font.
- **Event list:** Clean, minimal, on a warm white background (like watercolor paper). Each event card shows: date (prominent), title, venue, time, category tag. Tapping/clicking opens the full description and ticket link.
- **Typography:** A distinctive serif display font for the title and headings (e.g., Playfair Display, Lora, or Libre Baskerville). A clean readable sans for body text (e.g., Source Sans 3 or DM Sans). NOT Inter, NOT Roboto.
- **Category tags:** Small colored pills using the palette. Music = sky-blue, Theater = magenta, Arts = lavender, Film = ink-soft, Festival = green, Social/Community = green-dark.
- **"Happening today" highlight:** Events happening today get a subtle green left-border or background tint.
- **"This weekend" section:** Visually grouped if there are weekend events in the next 7 days.
- **Footer:** "Made with ♥ in Lunenburg" + last-updated timestamp from the JSON.
- **Mobile-first:** Designed for phones. Single column. Generous touch targets. The drawing provides all the personality — the UI itself stays out of the way.

### PWA features (same pattern as the Dandelions app)
- `manifest.json` with app name, icons, theme color
- Service worker with cache-first for static assets, network-first for `events.json`
- Installable to home screen
- Offline: shows last-cached events with a "last updated" notice
- Favicon: a small cropped detail from the watercolor drawing

---

## Project structure

```
lunenburg-events/
├── index.html          # Single-page app
├── style.css           # Styles
├── app.js              # Reads events.json, renders event list
├── events.json         # Generated by scraper, committed to repo
├── manifest.json       # PWA manifest
├── sw.js               # Service worker
├── assets/
│   ├── banner.jpeg     # The watercolor drawing
│   ├── icon-192.png    # PWA icon (cropped from drawing)
│   └── icon-512.png    # PWA icon large
├── scraper/
│   ├── scrape.py       # Main scraper entry point
│   ├── parsers/
│   │   ├── old_confidence.py
│   │   ├── lamp.py
│   │   ├── school_of_arts.py
│   │   ├── musique_royale.py
│   │   ├── opera_house.py
│   │   ├── symphony_ns.py
│   │   ├── boxwood.py
│   │   ├── heritage_society.py
│   │   ├── st_johns.py
│   │   └── fisheries_museum.py
│   └── requirements.txt  # requests, beautifulsoup4, python-dateutil
├── .github/
│   └── workflows/
│       └── scrape.yml    # GitHub Actions cron (runs scraper, commits JSON)
└── README.md
```

---

## Implementation order

### Phase 1 — Static prototype (do this first)
Build the frontend with hardcoded sample events in `events.json`. Get the design right. Deploy to GitHub Pages. This is what we're doing now.

**Complete sample events for the prototype** (verified against all source websites, May 20 2026):

```json
{
  "events": [
    {
      "title": "No Divas Allowed! — A Little Light Music",
      "date": "2026-05-20",
      "time": "7:00 PM",
      "end_time": "8:30 PM",
      "venue": "LAMP",
      "location": "97 Kaulbach St, Lunenburg",
      "description": "LAMP's 2026 Bel Canto Opera Academy explores the voice outside of operatic repertoire — through art song and musical theatre.",
      "url": "https://www.lampns.ca/concert-schedule/no-divas-allowed",
      "price": "PWYC ($15 suggested)",
      "category": "music",
      "source": "lamp"
    },
    {
      "title": "\"Beyond Heritage\" — RAIC Public Lecture by Rayleen Hill",
      "date": "2026-05-21",
      "time": "7:00 PM",
      "venue": "Lunenburg School of the Arts",
      "location": "6 Prince St, Lunenburg",
      "description": "Public lecture presented by the South Shore Network of the Royal Architectural Institute of Canada. Free event, no registration required.",
      "url": "https://lunenburgarts.org/news-events/beyond-heritage-raic-public-lecture-by-rayleen-hill-nsaa-mraic/",
      "price": "Free",
      "category": "arts",
      "source": "school_of_arts"
    },
    {
      "title": "Pretty Archie",
      "date": "2026-05-22",
      "time": "7:30 PM",
      "venue": "Lunenburg Opera House",
      "location": "290 Lincoln St, Lunenburg",
      "description": "Weekends at the Opera House concert series. Doors open at 6:45pm.",
      "url": "https://folkharbour.ca/concerts",
      "ticket_url": "https://lfhf.ticketpro.ca/en/pages/1680149062?aff=lfhf",
      "price": "$15–$55; 50% off ages 25 & under",
      "category": "music",
      "source": "opera_house"
    },
    {
      "title": "Gina Burgess Quintet & Natural Elements",
      "date": "2026-05-22",
      "time": "7:00 PM",
      "end_time": "9:00 PM",
      "venue": "Old Confidence Lodge",
      "location": "Riverport, NS",
      "description": "An amazing night of fiddling, Inuit throat singing, Brazilian percussion, and much more! ECMA-winning violinist Gina Burgess with an all-star 5-piece band, plus fiddler-violinist Hayley Ryerson with Ellen Gibling on harp.",
      "url": "https://www.oldconfidence.ca/events/gina-burgess-lunenburg-county",
      "price": "$25; $15 for Ages 18 & Under",
      "category": "music",
      "source": "old_confidence_lodge"
    },
    {
      "title": "Bel Canto Academy Concert 2026",
      "date": "2026-05-22",
      "time": "7:00 PM",
      "end_time": "8:30 PM",
      "venue": "LAMP",
      "location": "97 Kaulbach St, Lunenburg",
      "description": "An evening of Bel Canto masterpieces highlighting the beauty and nuance of the human voice. Canadian and international singers after two intensive weeks of residency.",
      "url": "https://www.lampns.ca/concert-schedule/bel-canto-2026",
      "price": "$25",
      "category": "music",
      "source": "lamp"
    },
    {
      "title": "Roxy and the Underground Soul Sound",
      "date": "2026-05-23",
      "time": "7:30 PM",
      "venue": "Lunenburg Opera House",
      "location": "290 Lincoln St, Lunenburg",
      "description": "Weekends at the Opera House concert series. Doors open at 6:45pm.",
      "url": "https://folkharbour.ca/concerts",
      "ticket_url": "https://lfhf.ticketpro.ca/en/pages/1680149064?aff=lfhf",
      "price": "$15–$55; 50% off ages 25 & under",
      "category": "music",
      "source": "opera_house"
    },
    {
      "title": "Silent Film with Live Score: Metropolis",
      "date": "2026-05-24",
      "time": "2:00 PM",
      "end_time": "4:30 PM",
      "venue": "Old Confidence Lodge",
      "location": "Riverport, NS",
      "description": "Fritz Lang's expressionist sci-fi masterpiece with a live improvised score by Halifax musicians. Saxophones, trombone, harp, bass, and drums.",
      "url": "https://www.oldconfidence.ca/events/silent-film-riverport-andrew-mackelvie",
      "price": "$20; $12 for Ages 10-18",
      "category": "film",
      "source": "old_confidence_lodge"
    },
    {
      "title": "Customer Service, Spirit of the Wildfire + Others",
      "date": "2026-05-29",
      "time": "7:00 PM",
      "end_time": "10:00 PM",
      "venue": "Old Confidence Lodge",
      "location": "Riverport, NS",
      "description": "Honest John presents: Customer Service, Spirit of the Wildfire, hi,low & Blackout. Showcasing amazing Nova Scotia talent.",
      "url": "https://www.oldconfidence.ca/events/customer-service-riverport",
      "price": "$15 advance; $20 door",
      "category": "music",
      "source": "old_confidence_lodge"
    },
    {
      "title": "OPEN MIC",
      "date": "2026-06-05",
      "time": "7:00 PM",
      "end_time": "11:00 PM",
      "venue": "Old Confidence Lodge",
      "location": "Riverport, NS",
      "description": "Creative covers and original songs hosted by Liam Britten. 7-8pm ages 18 & under, 8-10pm ages 19+.",
      "url": "https://www.oldconfidence.ca/events/june-open-mic-lunenburg",
      "price": "$10",
      "category": "music",
      "source": "old_confidence_lodge"
    },
    {
      "title": "Neon Dreams",
      "date": "2026-06-05",
      "time": "7:30 PM",
      "venue": "Lunenburg Opera House",
      "location": "290 Lincoln St, Lunenburg",
      "description": "Weekends at the Opera House concert series. Doors open at 6:45pm.",
      "url": "https://folkharbour.ca/concerts",
      "ticket_url": "https://tproatlantic.ticketpro.ca/en/pages/1690754901?aff=lfhf",
      "price": "$15–$55; 50% off ages 25 & under",
      "category": "music",
      "source": "opera_house"
    },
    {
      "title": "Duke Ellington Sacred Concert",
      "date": "2026-06-06",
      "time": "4:00 PM",
      "end_time": "7:00 PM",
      "venue": "St. John's Anglican Church",
      "location": "64 Townsend St, Lunenburg",
      "description": "Musique Royale presents one of the 20th century's most visionary musical statements — where jazz meets the sacred. Featuring soprano Frances Farrell, Nova Voce, Tuesday Night Band, and Cantabile Singers of Truro.",
      "url": "http://musiqueroyale.com/event/2026/2026-06-06-duke-ellington-sacred-concert/",
      "ticket_url": "https://www.canadahelps.org/en/charities/musique-royale-a-treasury-of-music-from-our-historic-past/events/duke-ellington-sacred-concert/",
      "price": "$30 at door; $25 advance; youth free (18 & under)",
      "category": "music",
      "source": "musique_royale"
    },
    {
      "title": "The Janzen Boys",
      "date": "2026-06-06",
      "time": "7:30 PM",
      "venue": "Lunenburg Opera House",
      "location": "290 Lincoln St, Lunenburg",
      "description": "Weekends at the Opera House concert series. Doors open at 6:45pm.",
      "url": "https://folkharbour.ca/concerts",
      "ticket_url": "https://lfhf.ticketpro.ca/en/pages/1680149065?aff=lfhf",
      "price": "$15–$55; 50% off ages 25 & under",
      "category": "music",
      "source": "opera_house"
    },
    {
      "title": "Tom Richards Trio",
      "date": "2026-06-07",
      "time": "2:00 PM",
      "venue": "Lightship Brewery",
      "location": "93 Tannery Rd, Lunenburg",
      "description": "Musique Royale presents an outdoor show. TRT takes you on a cinematic journey through vibrant landscapes, weaving stories of turmoil, love, and redemption. Jazz, cinematic, groove, and minimalist instrumental music.",
      "url": "http://musiqueroyale.com/event/2026/2026-06-07-tom-richards-trio/",
      "ticket_url": "https://www.canadahelps.org/en/charities/musique-royale-a-treasury-of-music-from-our-historic-past/events/tom-richards-trio-at-lightship-brewery",
      "price": "$25 advance; $30 at door; youth free (18 & under)",
      "category": "music",
      "source": "musique_royale"
    }
  ],
  "last_updated": "2026-05-20T12:00:00-03:00"
}
```

### Phase 2 — Scrapers
Build parsers one at a time, starting with the two Squarespace sites (Old Confidence, LAMP) since they're the easiest and most structured. Then WordPress sites (School of the Arts, Opera House). Then Musique Royale and Symphony NS (both need venue filtering). Then seasonal sources (Boxwood, Folk Harbour Festival). Test Heritage Society scrapability. Set up GitHub Actions cron.

### Phase 3 — Polish
Add remaining Tier 2 sources (to be provided later). Add PWA offline support. Add "add to calendar" links using the ICS URLs where available. Consider adding a simple category filter toggle. Implement deduplication logic for events that appear on multiple sources (e.g., Musique Royale concerts also listed on St. John's calendar and Symphony NS site).

---

## Key lessons from the Dandelions PWA (apply here)

- **Service worker caching:** Bump `CACHE_VERSION` in `sw.js` before every deploy. The SW includes `skipWaiting()` + `clients.claim()` so users get updates on their next normal refresh — no hard-refresh needed in production. During dev, enable "Update on reload" in Chrome DevTools → Application → Service Workers to avoid Ctrl+Shift+R pain. Optionally, wire the version bump into the GitHub Actions workflow using the commit SHA so it's automatic. A "new version available" toast can be added later but isn't needed for v1.
- **iOS PWA:** iOS Safari ignores the manifest `display` field. Need `<meta name="apple-mobile-web-app-capable" content="yes">` for standalone mode.
- **Keep it simple:** This is a community tool, not a SaaS product. One page, one purpose. The drawing provides personality. The UI provides utility.
