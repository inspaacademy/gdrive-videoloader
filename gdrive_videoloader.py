from urllib.parse import unquote
import requests
import argparse
import sys
from tqdm import tqdm
import os
import re
import threading
import math
import shutil
import json
from http.cookiejar import MozillaCookieJar
from requests.cookies import RequestsCookieJar
import tempfile

thread_errors = []

def load_cookies_from_file(cookies_file: str):
    """Load cookies from a Netscape cookies.txt or JSON export file."""
    if not os.path.exists(cookies_file):
        raise FileNotFoundError(f"Cookies file not found: {cookies_file}")

    with open(cookies_file, 'r', encoding='utf-8') as f:
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

    generated_cookie_file = None
    if not stripped.startswith('# Netscape HTTP Cookie File') and not stripped.startswith('# HTTP Cookie File'):
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
        temp_file.write('# Netscape HTTP Cookie File\n')
        temp_file.write('# https://curl.haxx.se/rfc/cookie_spec.html\n')
        temp_file.write('# This is a generated file! Do not edit.\n\n')
        temp_file.write(content)
        temp_file.close()
        generated_cookie_file = temp_file.name
        cookies_file = generated_cookie_file

    cookie_jar = MozillaCookieJar(cookies_file)
    try:
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
    finally:
        if generated_cookie_file and os.path.exists(generated_cookie_file):
            os.remove(generated_cookie_file)

    return cookie_jar

def get_cookies_session(cookies_file: str = None) -> requests.Session:
    """Create a requests session with optional cookies loaded from file."""
    session = requests.Session()

    if cookies_file:
        cookie_jar = load_cookies_from_file(cookies_file)
        session.cookies = cookie_jar

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

def get_file_size(url: str, cookies: dict) -> int:
    """Gets the total file size via a HEAD request."""
    response = requests.head(url, cookies=cookies, allow_redirects=True)
    size = int(response.headers.get('content-length', 0))
    return size

def download_part(url: str, cookies: dict, thread_lock, start: int, end: int, part_num: int, part_filename: str, chunk_size: int, pbar: tqdm, gpbar: tqdm, verbose: bool) -> None:
    """Downloads a specific byte range of the file and writes it to a part file."""
    headers = {'Range': f'bytes={start}-{end}'}

    # Support resuming individual parts
    downloaded = 0
    if os.path.exists(part_filename):
        downloaded = os.path.getsize(part_filename)
        if downloaded > 0:
            headers['Range'] = f'bytes={start + downloaded}-{end}'

            # Update Progress
            with thread_lock:
                gpbar.update(downloaded)
                pbar.update(downloaded)
            
            if verbose:
                print(f"[INFO] Resuming part {part_filename} from byte {start + downloaded}")

    # Check Part already fully downloaded
    if downloaded >= (end - start + 1):
        return
        
    s = requests.Session()
    response = s.get(url, stream=True, cookies=cookies, headers=headers)
    if response.status_code not in (200, 206):
        raise Exception(f"[ERROR] Failed to download part {part_filename}, status: {response.status_code}")
    
    file_mode = 'ab' if os.path.exists(part_filename) and os.path.getsize(part_filename) > 0 else 'wb'
    with open(part_filename, file_mode) as f:
        for chunk in response.iter_content(chunk_size=chunk_size):
            f.write(chunk)
            with thread_lock:
                gpbar.update(len(chunk))
                pbar.update(len(chunk))
            downloaded += len(chunk)

            # Check Part fully downloaded
            if downloaded >= (end - start + 1):
                break

def download_part_wrapper(*args):
    try:
        download_part(*args)
    except Exception as e:
        thread_errors.append(e)

def merge_parts(part_files: list[str], output_filename: str, verbose: bool) -> None:
    """Merges all part files into the final output file."""
    if verbose:
        print(f"[INFO] Merging {len(part_files)} parts into {output_filename}")

    missing = [pf for pf in part_files if not os.path.exists(pf)]
    if missing:
        print(f"[ERROR] Missing parts: {missing}")
        return

    with open(output_filename, 'wb') as outfile:
        for part_file in part_files:
            if verbose:
                print("Merging " + part_file)
            with open(part_file, 'rb') as pf:
                shutil.copyfileobj(pf, outfile)
    
    for part_file in part_files: # Cleanup
        os.remove(part_file)

    if verbose:
        print(f"[INFO] Merge complete. Cleaned up part files.")

def download_file(url: str, cookies: dict, filename: str, chunk_size: int, num_threads: int, verbose: bool) -> None:
    """Downloads the file using multiple threads, each handling a byte-range segment."""

    total_size = get_file_size(url, cookies)
    if num_threads == 1:
        download_single_threaded(url, cookies, filename, chunk_size, verbose)
        return
    if total_size == 0:
        print("[WARN] Could not determine file size. Falling back to single-threaded download.")
        download_single_threaded(url, cookies, filename, chunk_size, verbose)
        return

    if verbose:
        print(f"[INFO] Total file size: {total_size} bytes")
        print(f"[INFO] Downloading with {num_threads} threads")

    part_size = math.ceil(total_size / num_threads)
    part_files = []
    threads = []

    gpBar = tqdm(
        unit='B', unit_scale=True,
        desc="Download Progress",
        total=total_size,
        position=0
    )

    pbars = [
        tqdm(
            unit='B', unit_scale=True,
            desc="Downloading Part " + str(i+1),
            total=min((i * part_size) + part_size - 1, total_size - 1) - (i * part_size) + 1,
            position=i+1
        )
        for i in range(num_threads)
    ]

    thread_lock = threading.Lock()

    for i in range(num_threads):
        start = i * part_size
        end = min(start + part_size - 1, total_size - 1)
        part_filename = f"{filename}.part{i}"
        part_files.append(part_filename)

        t = threading.Thread(
            target=download_part_wrapper,
            args=(url, cookies, thread_lock, start, end, i, part_filename, chunk_size, pbars[i], gpBar, verbose),
            daemon=True
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    gpBar.close()
    for pbar in pbars:
        pbar.close()
    
    if(len(thread_errors) > 0):
        print(f"[ERROR] One of the parts failed. Check the console for details. Exiting...")
        return

    # Verify all parts downloaded correctly
    downloaded_total = sum(os.path.getsize(pf) for pf in part_files if os.path.exists(pf))
    if downloaded_total < total_size:
        print(f"[ERROR] Download incomplete: got {downloaded_total}/{total_size} bytes.")
        return
    

    merge_parts(part_files, filename, verbose)
    print(f"\n{filename} downloaded successfully.")

def download_single_threaded(url: str, cookies: dict, filename: str, chunk_size: int, verbose: bool) -> None:
    """Fallback single-threaded download (original behavior)."""
    headers = {}
    file_mode = 'wb'
    downloaded_size = 0

    if os.path.exists(filename):
        downloaded_size = os.path.getsize(filename)
        headers['Range'] = f"bytes={downloaded_size}-"
        file_mode = 'ab'

    if verbose:
        print(f"[INFO] Starting single-threaded download from {url}")

    response = requests.get(url, stream=True, cookies=cookies, headers=headers)
    if response.status_code in (200, 206):  # 200 for new downloads, 206 for partial content
        total_size = int(response.headers.get('content-length', 0)) + downloaded_size
        with open(filename, file_mode) as file:
            with tqdm(total=total_size, initial=downloaded_size, unit='B', unit_scale=True, desc=filename, file=sys.stdout) as pbar:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        file.write(chunk)
                        pbar.update(len(chunk))
        print(f"\n{filename} downloaded successfully.")
    else:
        print(f"Error downloading {filename}, status code: {response.status_code}")

def main(video_id_or_url: str, output_file: str = None, chunk_size: int = 1024, num_threads: int = 4, verbose: bool = False, cookies_file: str = None) -> None:
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
    cookies = session.cookies.get_dict()

    video, title = get_video_url(page_content, verbose)

    filename = output_file if output_file else title

    # Remove invalid characters (Windows + Linux)
    valid_filename = re.sub(r'[<>:"/\\|?*\x00-\x1F]', '', filename)
    # Remove trailing spaces or dots (Windows restriction)
    valid_filename = re.sub(r'[. ]+$', '', valid_filename)
    
    if video:
        download_file(video, cookies, valid_filename, chunk_size, num_threads, verbose)
    else:
        print("Unable to retrieve the video URL. Ensure the video ID is correct and accessible.")
        if not cookies_file:
            print("Tip: For private files, use --cookies to provide a cookies.txt file with your Google login session.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Script to download videos from Google Drive.")
    parser.add_argument("video_id", type=str, help="The video ID from Google Drive or a full Google Drive URL (e.g., 'abc-Qt12kjmS21kjDm2kjd' or 'https://drive.google.com/file/d/ID/view').")
    parser.add_argument("-o", "--output", type=str, help="Optional output file name for the downloaded video (default: video name in gdrive).")
    parser.add_argument("-c", "--chunk_size", type=int, default=1024, help="Optional chunk size (in bytes) for downloading the video. Default is 1024 bytes.")
    parser.add_argument("-t", "--threads", type=int, default=4, choices=range(1, 17), help="Number of parallel download threads (1-16). Default is 4.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose mode.")
    parser.add_argument("--cookies", type=str, help="Path to a Netscape cookies.txt file or JSON cookie export for private Google Drive files.")
    parser.add_argument("--version", action="version", version="%(prog)s 1.1.0")

    args = parser.parse_args()
    main(args.video_id, args.output, args.chunk_size, args.threads, args.verbose, args.cookies)
