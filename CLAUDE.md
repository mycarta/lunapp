# Lunapp

## Manual seeds

- **Before any manual seed work, run `git pull --rebase origin main` first.** A GitHub Actions scraper (`.github/workflows/scrape.yml`) commits to `main` three times a day ("Update events [automated]"), so local `main` is usually behind. Pulling first avoids rebase conflicts, especially on `CACHE_VERSION` in `sw.js`.
- Seeds go in `manual_seeds.json` (there is no `events_seed.json`).
- A pre-commit hook bumps `CACHE_VERSION` in `sw.js` automatically. Don't also bump it by hand.
