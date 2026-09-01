# About Scraper CMS

Scraper CMS is a deep recursive website scraper and content management system designed to intelligently extract, organize, and manage website data, documents, and media.

## 🛠️ Technology Stack

**Backend**
- **Python 3.10+**: Core programming language.
- **FastAPI**: High-performance asynchronous web framework used for the CMS backend API.
- **Uvicorn**: ASGI web server implementation.
- **Playwright (Async)**: Headless browser automation for rendering JavaScript-heavy websites and Single Page Applications (SPAs).
- **BeautifulSoup4**: HTML parsing and DOM manipulation for extracting clean text and links.
- **aiohttp**: Asynchronous HTTP client used for fast, concurrent document downloading.

**Frontend**
- **Vue.js 3**: Progressive JavaScript framework used via CDN for reactive, componentless UI management.
- **Tailwind CSS**: Utility-first CSS framework for rapid UI styling and responsive design.
- **Chart.js**: Client-side charting library for the analytics dashboard.

## ✨ Features

- **Deep Recursive Crawling**: Uses Breadth-First Search (BFS) to map and crawl entire domains, constrained to specific allowed domains to prevent infinite external looping.
- **Stealth Browsing**: Employs Playwright Stealth to bypass basic anti-bot protections.
- **Smart DOM Parsing**: Intelligently identifies and strips out boilerplate content like headers, footers, sidebars, and navigation menus to ensure only the primary article/body text is saved.
- **Document Extraction**: Automatically detects links to `.pdf`, `.doc`, `.xlsx`, and other documents, downloads them locally, and maps them to the exact section and page they were found on.
- **Visual Analytics Dashboard**: Provides real-time insights into crawl depths, storage usage, page distribution across URL paths, and file type breakdowns.
- **Background Task Management**: Scrape jobs run as background subprocesses, providing live log tails and status polling without blocking the UI.
- **Trash & Restore**: Soft-deletion system for pages, with a garbage-collection mechanism that safely removes orphaned document files from the disk when changes are committed.

## ⚙️ How It Works

1. **Initialization**: You provide a `CollegeName` and a list of `SeedURLs`.
2. **Crawl Phase**: Playwright spins up multiple concurrent browser tabs. It blocks images, CSS, and fonts at the network level to massively speed up page rendering.
3. **Extraction**: For every page, it extracts the DOM. It performs two passes:
   - *Pass 1 (Navigation)*: Finds all `<a>` tags to populate the BFS queue with new internal URLs.
   - *Pass 2 (Content)*: Cleans the HTML of boilerplate, extracts the raw body text, and finds document links.
4. **Downloading**: Document links are intercepted by `aiohttp` and downloaded asynchronously to the `downloads/` folder.
5. **Storage**: Everything is compiled into a massive, highly structured JSON file stored in the `data/` directory.
6. **Management**: The FastAPI backend serves this JSON to the Vue.js frontend, allowing you to edit text, delete pages, and trigger new crawls visually.
