from urllib.parse import unquote
import requests
import argparse
import sys
from tqdm import tqdm
import os
import re
import json
from http.cookiejar import MozillaCookieJar
from requests.cookies import RequestsCookieJar
import tempfile
import concurrent.futures

def load_cookies_from_file(cookies_file: str):
    """Load cookies from a Netscape cookies.txt or JSON export file."""
    if not os.path.exists(cookies_file):
        raise FileNotFoundError(f"Cookies file not found: {cookies_file}")
    
    with open(cookies_file, 'r') as f:
        content = f.read()
    stripped = content.lstrip()
    if stripped.startswith('[') or stripped.startswith('{'):
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON cookies file: {cookies_file}") from exc
        if isinstance(data, dict) and "cookies" in data and isinstance(data["cookies"], list):
            cookies_list = data["cookies"]
        elif isinstance(data, list):
            cookies_list = data
        else:
            raise ValueError(f"Unsupported JSON cookies format: {cookies_file}")
        jar = RequestsCookieJar()
        for cookie in cookies_list:
            if not isinstance(cookie, dict):
                continue
            name = cookie.get("name")
            value = cookie.get("value")
            domain = cookie.get("domain") or ""
            path = cookie.get("path") or "/"
            expires = cookie.get("expirationDate") or cookie.get("expires")
            if not name or value is None:
                continue
            jar.set(name, value, domain=domain, path=path, expires=expires)
        return jar
    
    if not stripped.startswith('# Netscape HTTP Cookie File') and not stripped.startswith('# HTTP Cookie File'):
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
        temp_file.write('# Netscape HTTP Cookie File\n')
        temp_file.write('# https://curl.haxx.se/rfc/cookie_spec.html\n')
        temp_file.write('# This is a generated file! Do not edit.\n\n')
        temp_file.write(content)
        temp_file.close()
        cookies_file = temp_file.name
    
    cookie_jar = MozillaCookieJar(cookies_file)
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    
    return cookie_jar

def get_cookies_session(cookies_file: str = None) -> requests.Session:
    """Create a requests session with optional cookies from file."""
    session = requests.Session()
    
    if cookies_file:
        try:
            cookie_jar = load_cookies_from_file(cookies_file)
            session.cookies = cookie_jar
        except Exception as e:
            print(f"[WARNING] Failed to load cookies: {e}")
    
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    })
    
    return session

def extract_drive_id(input_str: str) -> str:
    """Extracts the Google Drive file ID from a URL or returns the input if it's already an ID."""
    pattern = r'/file/d/([a-zA-Z0-9_-]+)'
    match = re.search(pattern, input_str)
    if match:
        return match.group(1)
    return input_str

def get_video_url(page_content: str, verbose: bool) -> tuple[str, str]:
    """Extracts the video playback URL and title from the page content."""
    if verbose:
        print("[INFO] Parsing video playback URL and title.")
    contentList = page_content.split("&")
    video, title = None, None
    for content in contentList:
        if content.startswith('title=') and not title:
            title = unquote(content.split('=')[-1])
        elif "videoplayback" in content and not video:
            video = unquote(content).split("|")[-1]
        if video and title:
            break

    if verbose:
        print(f"[INFO] Video URL: {video}")
        print(f"[INFO] Video Title: {title}")
    return video, title

def download_chunk(url: str, cookies: requests.cookies.RequestsCookieJar, start: int, end: int, 
                  chunk_size: int, headers: dict) -> None:
    """Download a specific part of the file."""
    session = requests.Session()
    session.cookies = cookies
    
    range_header = headers.copy()
    range_header['Range'] = f'bytes={start}-{end}'
    
    with session.get(url, headers=range_header, stream=True) as r:
        r.raise_for_status()
        pass

    return

def download_file(url: str, session: requests.Session, filename: str, chunk_size: int, 
                 num_threads: int, verbose: bool) -> None:
    """Downloads the file with multi-threading support."""
    
    try:
        head_resp = session.head(url)
        if 'Content-Length' not in head_resp.headers:
             head_resp = session.get(url, headers={'Range': 'bytes=0-1'}, stream=True)
        
        total_size = int(head_resp.headers.get('Content-Length', 0))
    except Exception as e:
        if verbose:
            print(f"[WARN] Could not determine file size: {e}. Falling back to single thread.")
        total_size = 0

    if total_size == 0:
        if verbose: print("[INFO] Unknown size, using single thread.")
        _download_single_threaded(url, session, filename, chunk_size, verbose)
        return
    
    if os.path.exists(filename):
        if os.path.getsize(filename) == total_size:
            print(f"{filename} already exists and matches size. Skipping.")
            return
        else:
            print(f"[INFO] Overwriting existing file {filename}")
    
    with open(filename, "wb") as f:
        f.seek(total_size - 1)
        f.write(b"\0")
    
    part_size = total_size // num_threads
    ranges = []
    for i in range(num_threads):
        start = i * part_size
        end = start + part_size - 1 if i < num_threads - 1 else total_size - 1
        ranges.append((start, end))

    if verbose:
        print(f"[INFO] Downloading {filename} ({total_size} bytes) with {num_threads} threads.")

    import threading
    file_lock = threading.Lock()
    
    pbar = tqdm(total=total_size, unit='B', unit_scale=True, desc=filename)

    def _worker(start, end):
        worker_session = requests.Session()
        worker_session.cookies = session.cookies
        worker_session.headers.update(session.headers)
        
        headers = {'Range': f'bytes={start}-{end}'}
        
        try:
            with worker_session.get(url, headers=headers, stream=True) as r:
                r.raise_for_status()
                local_pos = start
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        with file_lock:
                            with open(filename, "r+b") as f:
                                f.seek(local_pos)
                                f.write(chunk)
                            pbar.update(len(chunk))
                        local_pos += len(chunk)
        except Exception as e:
            if verbose:
                print(f"[ERROR] Thread {start}-{end} failed: {e}")
            raise e

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(_worker, s, e) for s, e in ranges]
        concurrent.futures.wait(futures)
    
    pbar.close()
    print(f"\n{filename} downloaded successfully.")

def _download_single_threaded(url: str, session: requests.Session, filename: str, chunk_size: int, verbose: bool) -> None:
    headers = {}
    file_mode = 'wb'
    downloaded_size = 0
    if os.path.exists(filename):
        downloaded_size = os.path.getsize(filename)
        headers['Range'] = f"bytes={downloaded_size}-"
        file_mode = 'ab'

    response = session.get(url, stream=True, headers=headers)
    if response.status_code in (200, 206):
        total_size = int(response.headers.get('content-length', 0)) + downloaded_size
        with open(filename, file_mode) as file:
            with tqdm(total=total_size, initial=downloaded_size, unit='B', unit_scale=True, desc=filename, file=sys.stdout) as pbar:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        file.write(chunk)
                        pbar.update(len(chunk))
    else:
        print(f"Error downloading {filename}, status code: {response.status_code}")

def main(video_id_or_url: str, output_file: str = None, chunk_size: int = 1048576, 
         verbose: bool = False, cookies_file: str = None, threads: int = 8) -> None:
    """Main function to process video ID or URL and download the video file."""
    video_id = extract_drive_id(video_id_or_url)
    
    if verbose:
        print(f"[INFO] Extracted video ID: {video_id}")
        if cookies_file:
            print(f"[INFO] Using cookies from: {cookies_file}")
    session = get_cookies_session(cookies_file)
    
    drive_url = f'https://drive.google.com/u/0/get_video_info?docid={video_id}&drive_originator_app=303'
    
    if verbose:
        print(f"[INFO] Accessing {drive_url}")

    response = session.get(drive_url)
    page_content = response.text

    video, title = get_video_url(page_content, verbose)

    filename = output_file if output_file else title
    if filename:
        filename = re.sub(r'[\\/*?:"<>|]', "", filename)

    if video:
        download_file(video, session, filename, chunk_size, threads, verbose)
    else:
        print("Unable to retrieve the video URL. Ensure the video ID is correct and accessible.")
        if not cookies_file:
            print("Tip: For private files, use --cookies to provide a cookies.txt file with your Google login session.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Script to download videos from Google Drive.")
    parser.add_argument("video_id", type=str, help="The video ID from Google Drive or a full Google Drive URL.")
    parser.add_argument("-o", "--output", type=str, help="Optional output file name.")
    parser.add_argument("-c", "--chunk_size", type=int, default=1048576, help="Chunk size in bytes (default 1MB).")
    parser.add_argument("-t", "--threads", type=int, default=16, help="Number of download threads (default 16).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose mode.")
    parser.add_argument("--cookies", type=str, help="Path to a cookies.txt file.")
    parser.add_argument("--version", action="version", version="%(prog)s 1.1")

    args = parser.parse_args()
    main(args.video_id, args.output, args.chunk_size, args.verbose, args.cookies, args.threads)
