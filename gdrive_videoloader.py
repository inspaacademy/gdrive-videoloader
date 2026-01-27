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

def download_file(url: str, session: requests.Session, filename: str, chunk_size: int, verbose: bool) -> None:
    """Downloads the file from the given URL with provided session, supports resuming."""
    headers = {}
    file_mode = 'wb'

    downloaded_size = 0
    if os.path.exists(filename):
        downloaded_size = os.path.getsize(filename)
        headers['Range'] = f"bytes={downloaded_size}-"
        file_mode = 'ab'

    if verbose:
        print(f"[INFO] Starting download from {url}")
        if downloaded_size > 0:
            print(f"[INFO] Resuming download from byte {downloaded_size}")

    response = session.get(url, stream=True, headers=headers)
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

def main(video_id_or_url: str, output_file: str = None, chunk_size: int = 1024, verbose: bool = False, cookies_file: str = None) -> None:
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
    if video:
        download_file(video, session, filename, chunk_size, verbose)
    else:
        print("Unable to retrieve the video URL. Ensure the video ID is correct and accessible.")
        if not cookies_file:
            print("Tip: For private files, use --cookies to provide a cookies.txt file with your Google login session.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Script to download videos from Google Drive.")
    parser.add_argument("video_id", type=str, help="The video ID from Google Drive or a full Google Drive URL (e.g., 'abc-Qt12kjmS21kjDm2kjd' or 'https://drive.google.com/file/d/ID/view').")
    parser.add_argument("-o", "--output", type=str, help="Optional output file name for the downloaded video (default: video name in gdrive).")
    parser.add_argument("-c", "--chunk_size", type=int, default=1024, help="Optional chunk size (in bytes) for downloading the video. Default is 1024 bytes.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose mode.")
    parser.add_argument("--cookies", type=str, help="Path to a Netscape cookies.txt file for accessing private Google Drive files.")
    parser.add_argument("--version", action="version", version="%(prog)s 1.0")

    args = parser.parse_args()
    main(args.video_id, args.output, args.chunk_size, args.verbose, args.cookies)
