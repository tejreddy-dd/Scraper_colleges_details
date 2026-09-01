from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os
import json
import subprocess
from collections import Counter
from urllib.parse import urlparse
from pydantic import BaseModel
from typing import List, Dict, Any

app = FastAPI()

# Make sure data directory exists
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs("templates", exist_ok=True)

templates = Jinja2Templates(directory="templates")

class PageUpdate(BaseModel):
    url: str
    bodyText: str

class JsonUpdate(BaseModel):
    pages: List[Dict[str, Any]]


class BulkScrapeItem(BaseModel):
    collegeName: str
    seedUrls: List[str]

class BulkScrapeRequest(BaseModel):
    jobs: List[BulkScrapeItem]

class ScrapeRequest(BaseModel):
    collegeName: str
    seedUrls: List[str]

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serve the main CMS page."""
    # List available colleges
    colleges = []
    for f in os.listdir(DATA_DIR):
        if f.endswith(".json") and not f.endswith("_status.json"):
            colleges.append(f.replace(".json", ""))
    return templates.TemplateResponse("index.html", {"request": request, "colleges": colleges})

@app.get("/about", response_class=HTMLResponse)
async def read_about(request: Request):
    """Serve the About page."""
    return templates.TemplateResponse("about.html", {"request": request})

@app.get("/about.md")
async def get_about_md():
    """Serve the raw about.md file."""
    if not os.path.exists("about.md"):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse("about.md")

@app.get("/api/college/{college_name}")
async def get_college_data(college_name: str):
    """Get the full JSON data for a college."""
    file_path = os.path.join(DATA_DIR, f"{college_name}.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="College not found")
        
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

@app.post("/api/college/{college_name}")
async def update_college_data(college_name: str, data: JsonUpdate):
    """Overwrite the pages list in the college JSON file and garbage collect unreferenced documents."""
    file_path = os.path.join(DATA_DIR, f"{college_name}.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="College not found")
        
    # Read existing data to preserve metadata (downloads, dates, etc.)
    with open(file_path, "r", encoding="utf-8") as f:
        existing_data = json.load(f)
        
    # Collect all local paths that are still referenced by the NEW pages list
    referenced_local_paths = set()
    for page in data.pages:
        for link in page.get("links", []):
            if link.get("type") == "document" and link.get("localPath"):
                referenced_local_paths.add(link["localPath"])
                
    # Perform Smart Garbage Collection on the global downloads list
    new_downloads_list = []
    for download in existing_data.get("downloads", []):
        local_path = download.get("localPath")
        if local_path and local_path in referenced_local_paths:
            # Still referenced, keep it
            new_downloads_list.append(download)
        else:
            # Orphaned document! Delete from disk.
            if local_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                    print(f"🗑️ Garbage collected orphaned file: {local_path}")
                except Exception as e:
                    print(f"Failed to delete {local_path}: {e}")
                    
    # Update pages and downloads
    existing_data["pages"] = data.pages
    existing_data["downloads"] = new_downloads_list
    existing_data["totalPages"] = len(data.pages)
    existing_data["totalDownloads"] = len(new_downloads_list)
    
    # Write back to disk
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(existing_data, f, indent=2, ensure_ascii=False)
        
    return {"status": "success", "message": f"Updated {college_name}.json. Kept {len(new_downloads_list)} downloads."}

@app.post("/api/scrape")
async def start_scrape(req: ScrapeRequest):
    """Start the scraper in the background."""
    college_name = req.collegeName.strip()
    if not college_name:
        raise HTTPException(status_code=400, detail="collegeName is required")
    
    # Check if already running
    status_file = os.path.join(DATA_DIR, f"{college_name}_status.json")
    if os.path.exists(status_file):
        with open(status_file, "r") as f:
            status = json.load(f)
            if status.get("status") == "running":
                return {"status": "running", "message": "Scraper is already running for this college."}
                
    # Create input.csv
    csv_path = os.path.join(DATA_DIR, f"{college_name}_input.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("CollegeName,SeedURL\n")
        for url in req.seedUrls:
            if url.strip():
                f.write(f"{college_name},{url.strip()}\n")
                
    # Write initial status
    import time
    with open(status_file, "w") as f:
        json.dump({"status": "running", "startedAt": time.time(), "message": "Scraper starting..."}, f)
        
    # Start subprocess
    log_file = os.path.join(DATA_DIR, f"{college_name}_scrape.log")
    
    # We will use sys.executable to run python
    import sys
    with open(log_file, "w") as lf:
        subprocess.Popen(
            [sys.executable, "crawl.py", csv_path],
            stdout=lf,
            stderr=subprocess.STDOUT
        )
        
    return {"status": "started", "message": "Scraper launched successfully."}


@app.post("/api/scrape/bulk")
async def start_bulk_scrape(req: BulkScrapeRequest):
    """Start the scraper in the background for multiple colleges."""
    if not req.jobs:
        raise HTTPException(status_code=400, detail="No jobs provided")
        
    csv_path = os.path.join(DATA_DIR, "bulk_input.csv")
    import time
    timestamp = str(int(time.time()))
    csv_path = os.path.join(DATA_DIR, f"bulk_input_{timestamp}.csv")
    
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("CollegeName,SeedURL\n")
        for job in req.jobs:
            c_name = job.collegeName.strip()
            if not c_name: continue
            
            # Write initial status
            status_file = os.path.join(DATA_DIR, f"{c_name}_status.json")
            with open(status_file, "w") as sf:
                json.dump({"status": "queued", "startedAt": time.time(), "message": "Scraper queued in bulk job..."}, sf)
                
            for url in job.seedUrls:
                if url.strip():
                    f.write(f"{c_name},{url.strip()}\n")
                    
    # Start subprocess
    log_file = os.path.join(DATA_DIR, f"bulk_scrape_{timestamp}.log")
    
    import sys
    import subprocess
    with open(log_file, "w") as lf:
        subprocess.Popen(
            [sys.executable, "crawl.py", csv_path],
            stdout=lf,
            stderr=subprocess.STDOUT
        )
        
    return {"status": "started", "message": f"Bulk scraper launched for {len(req.jobs)} colleges."}

@app.get("/api/scrape-status/{college_name}")
async def get_scrape_status(college_name: str):
    """Get the current status of the scraper and latest logs."""
    status_file = os.path.join(DATA_DIR, f"{college_name}_status.json")
    log_file = os.path.join(DATA_DIR, f"{college_name}_scrape.log")
    
    status_data = {"status": "idle"}
    if os.path.exists(status_file):
        with open(status_file, "r") as f:
            status_data = json.load(f)
            
    # Read last few lines of log
    logs = []
    if os.path.exists(log_file):
        try:
            # simple tail
            with open(log_file, "r") as f:
                lines = f.readlines()
                logs = [line.strip() for line in lines[-20:]]
        except Exception:
            pass
            
    status_data["logs"] = logs
    return status_data

@app.get("/api/analytics/{college_name}")
async def get_analytics(college_name: str):
    file_path = os.path.join(DATA_DIR, f"{college_name}.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="College not found")
        
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    pages = data.get("pages", [])
    downloads = data.get("downloads", [])
    
    groups = Counter()
    depths = Counter()
    total_body_length = 0
    
    for p in pages:
        path = urlparse(p.get("url", "")).path.strip("/")
        parts = path.split("/")
        prefix = f"/{parts[0]}/" if parts and parts[0] else "/"
        groups[prefix] += 1
        depths[str(p.get("depth", 0))] += 1
        total_body_length += len(p.get("bodyText", ""))
        
    avg_body_length = total_body_length / len(pages) if pages else 0
    
    download_types = Counter()
    total_download_size = 0
    for d in downloads:
        ext = os.path.splitext(d.get("fileName", ""))[1].lower()
        if not ext: ext = "unknown"
        download_types[ext] += 1
        if d.get("localPath") and os.path.exists(d.get("localPath")):
            total_download_size += os.path.getsize(d.get("localPath"))
            
    return {
        "totalPages": len(pages),
        "totalDownloads": len(downloads),
        "crawlDurationSeconds": data.get("crawlDurationSeconds", 0),
        "urlGroups": dict(groups.most_common(15)),
        "depthDistribution": dict(depths),
        "avgBodyLength": avg_body_length,
        "totalBodyLength": total_body_length,
        "downloadTypes": dict(download_types),
        "totalDownloadSize": total_download_size
    }

def get_dir_size(path="."):
    total = 0
    with os.scandir(path) as it:
        for entry in it:
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += get_dir_size(entry.path)
    return total

@app.get("/api/storage-info")
async def get_storage_info():
    data_size = get_dir_size("data") if os.path.exists("data") else 0
    dl_size = get_dir_size("downloads") if os.path.exists("downloads") else 0
    return {
        "dataSizeBytes": data_size,
        "downloadsSizeBytes": dl_size,
        "totalBytes": data_size + dl_size
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("cms:app", host="127.0.0.1", port=8000, reload=True)
