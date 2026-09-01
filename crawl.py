#!/usr/bin/env python3
"""
Deep Recursive Website Scraper
===============================
Takes a single URL, recursively crawls all internal pages using BFS,
extracts body text + link names + file references from each page,
downloads PDFs/documents locally, and outputs site_data.json.

Usage:
    python crawl.py "https://vitap.ac.in/"
"""

import asyncio
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp
from bs4 import BeautifulSoup
from playwright_stealth import Stealth
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MAX_CONCURRENT_TABS = 3
PAGE_TIMEOUT_MS = 15_000
DATA_DIR = "data"
BASE_DOWNLOADS_DIR = "downloads"

# File extensions treated as downloadable documents
DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".csv", ".rtf", ".odt", ".ods", ".odp", ".txt",
}

# File extensions to skip entirely (images, media, web assets, etc.)
SKIP_EXTENSIONS = {
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tiff",
    # Video / Audio
    ".mp4", ".mp3", ".avi", ".mov", ".wmv", ".flv", ".ogg", ".wav", ".webm",
    # Fonts
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    # CSS / JS / Web assets
    ".css", ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx",
    ".map", ".min.js", ".min.css",
    ".json", ".xml", ".rss", ".atom",
    # Other web files
    ".ico", ".manifest", ".webmanifest", ".sw.js",
    ".php", ".asp", ".aspx", ".jsp",
}


# ---------------------------------------------------------------------------
# URL Helpers
# ---------------------------------------------------------------------------
def normalize_url(url: str) -> str:
    """Strip fragment, trailing slash, and normalize for deduplication."""
    parsed = urlparse(url)
    # Remove fragment
    cleaned = parsed._replace(fragment="")
    # Remove trailing slash from path (but keep "/" for root)
    path = cleaned.path.rstrip("/") or "/"
    cleaned = cleaned._replace(path=path)
    return urlunparse(cleaned).lower()


def get_extension(url: str) -> str:
    """Extract file extension from a URL path."""
    path = urlparse(url).path
    _, ext = os.path.splitext(path)
    return ext.lower()


def is_allowed_domain(url: str, allowed_domains: set) -> bool:
    """Check if url belongs to any of the allowed domains."""
    parsed = urlparse(url)
    return parsed.hostname in allowed_domains


def is_valid_http_url(url: str) -> bool:
    """Check if URL is http or https."""
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https")

# ---------------------------------------------------------------------------
# Document Downloader
# ---------------------------------------------------------------------------
async def download_file(session: aiohttp.ClientSession, url: str, link_text: str, found_on_page: str, downloads_dir: str) -> dict | None:
    """Download a document file asynchronously and return metadata."""
    try:
        parsed = urlparse(url)
        filename = os.path.basename(parsed.path)
        if not filename:
            filename = "unknown_document"

        # Ensure unique filenames
        local_path = os.path.join(downloads_dir, filename)
        counter = 1
        base, ext = os.path.splitext(filename)
        while os.path.exists(local_path):
            local_path = os.path.join(downloads_dir, f"{base}_{counter}{ext}")
            counter += 1

        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
            response.raise_for_status()
            os.makedirs(downloads_dir, exist_ok=True)
            with open(local_path, "wb") as f:
                async for chunk in response.content.iter_chunked(8192):
                    f.write(chunk)

        final_filename = os.path.basename(local_path)
        print(f"    📥 Downloaded: {final_filename}")

        return {
            "originalUrl": url,
            "localPath": local_path,
            "fileName": final_filename,
            "foundOnPage": found_on_page,
            "linkText": link_text or filename,
        }
    except Exception as e:
        print(f"    ⚠️  Failed to download {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Page Scraper
# ---------------------------------------------------------------------------
async def scrape_page(page, url: str, depth: int, allowed_domains: set, visited: set, downloads_list: list, http_session: aiohttp.ClientSession, downloads_dir: str) -> dict | None:
    """Visit a single page, extract content and links."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        
        # Give CSR frameworks like Next.js a moment to hydrate the DOM
        await page.wait_for_timeout(500)

        # Get page title
        title = await page.title()

        # Get full HTML for BeautifulSoup parsing
        html_content = await page.content()
        
        # -------------------------------------------------------------------
        # PASS 1: NAVIGATION LINKS (From Raw HTML)
        # -------------------------------------------------------------------
        raw_soup = BeautifulSoup(html_content, "html.parser")
        new_urls_to_visit = []
        
        for anchor in raw_soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            
            full_url = urljoin(url, href)
            if not is_valid_http_url(full_url):
                continue
                
            ext = get_extension(full_url)
            if ext in SKIP_EXTENSIONS or ext in DOCUMENT_EXTENSIONS:
                continue # Skip downloads/media for the BFS queue
                
            if is_allowed_domain(full_url, allowed_domains):
                normalized = normalize_url(full_url)
                if normalized not in visited:
                    new_urls_to_visit.append((full_url, depth + 1))
                    
        print(f"DEBUG: Found {len(raw_soup.find_all('a', href=True))} raw links. Queued {len(new_urls_to_visit)} for visit.")
        # -------------------------------------------------------------------
        # PASS 2: BODY TEXT & DOWNLOADS (From Cleaned HTML)
        # -------------------------------------------------------------------
        clean_soup = BeautifulSoup(html_content, "html.parser")
        
        # Generic boilerplate removal (tags)
        for tag in clean_soup.find_all(["script", "style", "noscript", "header", "footer", "nav"]):
            tag.decompose()
            
        # Generic boilerplate removal (attributes/classes)
        # We look for common patterns that indicate headers, footers, or sidebars
        blacklist_patterns = re.compile(r'\b(header|footer|nav|navbar|menu|sidebar|banner|cookie)\b', re.IGNORECASE)
        
        for el in clean_soup.find_all(True):
            if el.attrs is None:
                continue
                
            # Check ID
            el_id = el.get("id")
            if el_id and blacklist_patterns.search(el_id):
                el.decompose()
                continue
            
            # Check classes
            el_classes = el.get("class")
            if el_classes:
                class_str = " ".join(el_classes)
                if blacklist_patterns.search(class_str):
                    el.decompose()
                    continue
                    
            # Check roles
            el_role = el.get("role")
            if el_role and el_role in ["banner", "contentinfo", "navigation"]:
                el.decompose()
                continue
                
        # Generic text-based boilerplate removal (for Tailwind/React sites without semantic classes)
        # We look for typical footer/menu text inside block elements that are relatively short.
        footer_text_patterns = re.compile(r'(copyright\s*(©|c)|all rights reserved|privacy policy|quick links|terms( and | & )conditions)', re.IGNORECASE)
        
        for el in clean_soup.find_all(['div', 'section']):
            if el.attrs is None:
                continue
            text = el.get_text(separator=" ", strip=True)
            # If the block has less than 1500 chars and contains strong footer keywords, it's likely a footer
            if len(text) > 0 and len(text) < 1500 and footer_text_patterns.search(text):
                el.decompose()

        # Extract text (BeautifulSoup get_text extracts all text, including hidden tabs)
        body_text = clean_soup.get_text(separator="\n", strip=True)
        # Clean up excessive whitespace
        body_text = re.sub(r'\n{3,}', '\n\n', body_text).strip()

        # Extract document links from the CLEANED body only
        links = []
        for anchor in clean_soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue

            full_url = urljoin(url, href)
            if not is_valid_http_url(full_url):
                continue

            link_text = anchor.get_text(strip=True) or anchor.get("title", "") or anchor.get("aria-label", "")
            if not link_text:
                img = anchor.find("img")
                if img:
                    link_text = img.get("alt", "")
            link_text = link_text or os.path.basename(urlparse(full_url).path) or full_url

            ext = get_extension(full_url)

            if ext in DOCUMENT_EXTENSIONS:
                # Find the nearest previous heading (h1-h6) to map it to a section
                nearest_heading = anchor.find_previous(["h1", "h2", "h3", "h4", "h5", "h6"])
                section_heading = nearest_heading.get_text(strip=True) if nearest_heading else ""
                
                link_entry = {
                    "text": link_text,
                    "href": full_url,
                    "type": "document",
                    "sectionHeading": section_heading
                }
                # Download the document (ensure we only download it once)
                normalized = normalize_url(full_url)
                if normalized not in visited:
                    visited.add(normalized)
                    dl_result = await download_file(http_session, full_url, link_text, url, downloads_dir)
                    if dl_result:
                        downloads_list.append(dl_result)
                        link_entry["localPath"] = dl_result["localPath"]
                links.append(link_entry)
            elif ext not in SKIP_EXTENSIONS:
                links.append({
                    "text": link_text,
                    "href": full_url,
                    "type": "internal" if is_allowed_domain(full_url, allowed_domains) else "external",
                })

        return {
            "url": url,
            "title": title,
            "bodyText": body_text,
            "links": links,
            "depth": depth,
        }, new_urls_to_visit

    except Exception as e:
        import traceback
        print(f"    ❌ Error scraping {url}: {e}")
        traceback.print_exc()
        return None, []


# ---------------------------------------------------------------------------
# BFS Crawler
# ---------------------------------------------------------------------------
async def crawl(college_name: str, start_urls: list[str]):
    """Main BFS crawler. Visits all internal pages across provided URLs."""
    allowed_domains = {urlparse(u).hostname for u in start_urls if urlparse(u).hostname}
    
    downloads_dir = os.path.join(BASE_DOWNLOADS_DIR, college_name)
    os.makedirs(DATA_DIR, exist_ok=True)
    output_file = os.path.join(DATA_DIR, f"{college_name}.json")
    status_file = os.path.join(DATA_DIR, f"{college_name}_status.json")
    
    def update_status(status_msg, state="running"):
        try:
            with open(status_file, "w") as sf:
                json.dump({"status": state, "message": status_msg, "updatedAt": time.time()}, sf)
        except Exception:
            pass

    print(f"🌐 Starting deep crawl for: {college_name}")
    print(f"📌 Allowed Domains: {', '.join(allowed_domains)}")
    print(f"📂 Documents will be saved to: {downloads_dir}/")
    print(f"📄 Output will be saved to: {output_file}")
    print("=" * 60)

    visited = set()
    all_pages = []
    all_downloads = []
    
    # Load existing data to prevent duplicates
    if os.path.exists(output_file):
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
                all_pages = existing.get("pages", [])
                all_downloads = existing.get("downloads", [])
                for p in all_pages:
                    visited.add(normalize_url(p.get("url", "")))
            print(f"♻️  Loaded {len(all_pages)} existing pages to skip duplicates.")
        except Exception as e:
            print(f"⚠️  Could not load existing data: {e}")

    queue = asyncio.Queue()
    
    for url in start_urls:
        norm = normalize_url(url)
        if norm not in visited:
            visited.add(norm)
            await queue.put((url, 0))

    start_time = time.time()
    update_status("Starting browser...")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
        )

        # Block images, CSS, fonts, media at the network level for speed
        await context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "stylesheet", "font", "media")
            else route.continue_(),
        )

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_TABS)
        http_session = aiohttp.ClientSession()

        async def process_url(url: str, depth: int):
            async with semaphore:
                page = await context.new_page()
                await Stealth().apply_stealth_async(page)
                try:
                    elapsed = time.time() - start_time
                    print(f"[{len(all_pages)+1:>4} pages | {elapsed:.0f}s] Depth {depth} → {url}")

                    result, new_urls = await scrape_page(
                        page, url, depth, allowed_domains, visited, all_downloads, http_session, downloads_dir
                    )

                    if result:
                        all_pages.append(result)

                    # Add newly discovered URLs to the queue
                    for new_url, new_depth in (new_urls or []):
                        norm = normalize_url(new_url)
                        if norm not in visited:
                            visited.add(norm)
                            await queue.put((new_url, new_depth))

                finally:
                    await page.close()

        # BFS loop
        while True:
            # Gather a batch of URLs to process concurrently
            batch = []
            while not queue.empty() and len(batch) < MAX_CONCURRENT_TABS:
                try:
                    item = queue.get_nowait()
                    batch.append(item)
                except asyncio.QueueEmpty:
                    break

            if not batch:
                break

            tasks = [process_url(url, depth) for url, depth in batch]
            await asyncio.gather(*tasks)
            
            # Update status
            elapsed = time.time() - start_time
            update_status(f"Scraped {len(all_pages)} pages in {elapsed:.0f}s. Queue: {queue.qsize()}")

        await http_session.close()
        await browser.close()

    elapsed = time.time() - start_time

    # Build output
    output = {
        "collegeName": college_name,
        "startUrls": start_urls,
        "allowedDomains": list(allowed_domains),
        "crawledAt": datetime.now(timezone.utc).isoformat(),
        "crawlDurationSeconds": round(elapsed, 1),
        "totalPages": len(all_pages),
        "totalDownloads": len(all_downloads),
        "pages": all_pages,
        "downloads": all_downloads,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    update_status("Crawl complete", "complete")

    print("=" * 60)
    print(f"✅ Crawl complete for {college_name}!")
    print(f"   📄 Pages scraped: {len(all_pages)}")
    print(f"   📥 Files downloaded: {len(all_downloads)}")
    print(f"   ⏱️  Duration: {elapsed:.1f}s")
    print(f"   💾 Output: {output_file}")
    if all_downloads:
        print(f"   📂 Downloads: {downloads_dir}/")


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage: python crawl.py <input.csv>")
        print('Example: python crawl.py input.csv')
        sys.exit(1)

    csv_path = sys.argv[1]
    if not os.path.exists(csv_path):
        print(f"Error: Could not find '{csv_path}'")
        sys.exit(1)

    # Parse CSV: Group by CollegeName
    colleges = {}
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            college = row.get("CollegeName", "").strip()
            url = row.get("SeedURL", "").strip()
            
            if not college or not url:
                continue
                
            if college not in colleges:
                colleges[college] = {"urls": []}
                
            colleges[college]["urls"].append(url)
                
    for college_name, data in colleges.items():
        # Deduplicate urls
        start_urls = list(set(data["urls"]))
        
        asyncio.run(crawl(college_name, start_urls))


if __name__ == "__main__":
    main()
