# Lunapp

Free, ad-free community events app for Lunenburg, Nova Scotia. Shows music, theatre, arts, and cultural events for the next two weeks.

**Live app:** https://lunenburg.fingerpost.ca/ — also linked from the [fingerpost.ca](https://fingerpost.ca/) landing page.

## How it works

A Python scraper runs twice daily via GitHub Actions, pulls events from local venue websites, and writes `events.json`. The static frontend reads the JSON and renders the event list. The app is a PWA — installable on phones and works offline.

## Sources

**Active:**

- Old Confidence Lodge
- LAMP (Lunenburg Academy of Music Performance)
- Lunenburg Opera House
- Musique Royale
- Lunenburg School of the Arts
- Lunenburg Heritage Society

**Planned:**

- Symphony Nova Scotia
- Boxwood Festival
- Folk Harbour Festival
- Fisheries Museum of the Atlantic
- St. John's Anglican Church

---

The watercolor banner is an original drawing by the developer.

Made with ♥ in Lunenburg
