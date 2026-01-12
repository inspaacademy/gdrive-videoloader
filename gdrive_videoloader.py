"""
Google Drive Video Downloader

A Python script to download videos from Google Drive, including view-only videos.
Supports both interactive and command-line interfaces with authentication via cookies.
"""

from urllib.parse import unquote, urlparse, parse_qs
from typing import Optional, Union
from dataclasses import dataclass
import os as os_module
import requests
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
except ImportError:
    # Fallback for older requests versions that bundle urllib3
    from requests.packages.urllib3.util.retry import Retry  # type: ignore
import argparse
import sys
from tqdm import tqdm
import os
import json
import re
import time
import platform
import subprocess
from datetime import datetime


# ============================================================================
# CONSTANTS
# ============================================================================

# URLs
DRIVE_BASE_URL = "https://drive.google.com"
DRIVE_VIDEO_INFO_URL_TEMPLATE = "https://drive.google.com/u/0/get_video_info?docid={video_id}&drive_originator_app=303"
GOOGLE_DRIVE_URL = "https://drive.google.com"

# Chunk Sizes
DEFAULT_CHUNK_SIZE = 65536  # 64KB
MIN_CHUNK_SIZE = 1024  # 1KB
MAX_CHUNK_SIZE = 10 * 1024 * 1024  # 10MB

# Chunk Size Thresholds (for adaptive sizing)
CHUNK_SIZE_SMALL = 16 * 1024  # 16KB for files < 10MB
CHUNK_SIZE_MEDIUM = 64 * 1024  # 64KB for files < 100MB
CHUNK_SIZE_LARGE = 256 * 1024  # 256KB for files < 500MB
CHUNK_SIZE_XLARGE = 1024 * 1024  # 1MB for files >= 500MB

FILE_SIZE_THRESHOLD_SMALL = 10 * 1024 * 1024  # 10MB
FILE_SIZE_THRESHOLD_MEDIUM = 100 * 1024 * 1024  # 100MB
FILE_SIZE_THRESHOLD_LARGE = 500 * 1024 * 1024  # 500MB

# Log Levels
LOG_LEVEL_NORMAL = "normal"
LOG_LEVEL_VERBOSE = "verbose"
LOG_LEVEL_QUIET = "quiet"

# Timeouts
LOGIN_WAIT_TIMEOUT = 300  # 5 minutes
REQUEST_TIMEOUT = 30  # seconds
DOWNLOAD_TIMEOUT = 60  # seconds

# File Defaults
DEFAULT_COOKIE_FILE = "cookies.json"
DEFAULT_VIDEO_EXTENSION = ".mp4"

# Cookie Names
REQUIRED_COOKIES = ["SID", "HSID"]
OPTIONAL_COOKIES = ["SSID", "APISID", "SAPISID"]
SECURE_COOKIES = ["__Secure-1PSID", "__Secure-3PSID"]

# HTTP Status Codes
HTTP_OK = 200
HTTP_PARTIAL_CONTENT = 206
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_TOO_MANY_REQUESTS = 429
HTTP_INTERNAL_ERROR = 500
HTTP_BAD_GATEWAY = 502
HTTP_SERVICE_UNAVAILABLE = 503
HTTP_GATEWAY_TIMEOUT = 504

# UI Constants
SEPARATOR_LENGTH = 60
SEPARATOR_CHAR = "="

# Retry Configuration
MAX_RETRIES = 3
RETRY_BACKOFF_FACTOR = 1
RETRY_STATUS_CODES = [HTTP_TOO_MANY_REQUESTS, HTTP_INTERNAL_ERROR, HTTP_BAD_GATEWAY, 
                      HTTP_SERVICE_UNAVAILABLE, HTTP_GATEWAY_TIMEOUT]

# ============================================================================
# CONFIGURATION MANAGEMENT
# ============================================================================

@dataclass
class Config:
    """Configuration class for gdrive-videoloader with environment variable support.
    
    All configuration values can be overridden via environment variables.
    Environment variable names follow the pattern: GDRIVE_VIDEOLOADER_<CONSTANT_NAME>
    """
    # URLs
    drive_base_url: str = DRIVE_BASE_URL
    drive_video_info_url_template: str = DRIVE_VIDEO_INFO_URL_TEMPLATE
    google_drive_url: str = GOOGLE_DRIVE_URL
    
    # Chunk Sizes
    default_chunk_size: int = DEFAULT_CHUNK_SIZE
    min_chunk_size: int = MIN_CHUNK_SIZE
    max_chunk_size: int = MAX_CHUNK_SIZE
    
    # Log Levels
    log_level_normal: str = LOG_LEVEL_NORMAL
    log_level_verbose: str = LOG_LEVEL_VERBOSE
    log_level_quiet: str = LOG_LEVEL_QUIET
    
    # Timeouts
    login_wait_timeout: int = LOGIN_WAIT_TIMEOUT
    request_timeout: int = REQUEST_TIMEOUT
    download_timeout: int = DOWNLOAD_TIMEOUT
    
    # File Defaults
    default_cookie_file: str = DEFAULT_COOKIE_FILE
    default_video_extension: str = DEFAULT_VIDEO_EXTENSION
    
    # Retry Configuration
    max_retries: int = MAX_RETRIES
    retry_backoff_factor: int = RETRY_BACKOFF_FACTOR
    
    @classmethod
    def from_env(cls) -> 'Config':
        """Create Config instance with values from environment variables.
        
        Returns:
            Config instance with environment variable overrides applied
        """
        return cls(
            drive_base_url=os_module.getenv('GDRIVE_VIDEOLOADER_DRIVE_BASE_URL', DRIVE_BASE_URL),
            drive_video_info_url_template=os_module.getenv('GDRIVE_VIDEOLOADER_DRIVE_VIDEO_INFO_URL_TEMPLATE', DRIVE_VIDEO_INFO_URL_TEMPLATE),
            google_drive_url=os_module.getenv('GDRIVE_VIDEOLOADER_GOOGLE_DRIVE_URL', GOOGLE_DRIVE_URL),
            default_chunk_size=int(os_module.getenv('GDRIVE_VIDEOLOADER_DEFAULT_CHUNK_SIZE', str(DEFAULT_CHUNK_SIZE))),
            min_chunk_size=int(os_module.getenv('GDRIVE_VIDEOLOADER_MIN_CHUNK_SIZE', str(MIN_CHUNK_SIZE))),
            max_chunk_size=int(os_module.getenv('GDRIVE_VIDEOLOADER_MAX_CHUNK_SIZE', str(MAX_CHUNK_SIZE))),
            log_level_normal=os_module.getenv('GDRIVE_VIDEOLOADER_LOG_LEVEL_NORMAL', LOG_LEVEL_NORMAL),
            log_level_verbose=os_module.getenv('GDRIVE_VIDEOLOADER_LOG_LEVEL_VERBOSE', LOG_LEVEL_VERBOSE),
            log_level_quiet=os_module.getenv('GDRIVE_VIDEOLOADER_LOG_LEVEL_QUIET', LOG_LEVEL_QUIET),
            login_wait_timeout=int(os_module.getenv('GDRIVE_VIDEOLOADER_LOGIN_WAIT_TIMEOUT', str(LOGIN_WAIT_TIMEOUT))),
            request_timeout=int(os_module.getenv('GDRIVE_VIDEOLOADER_REQUEST_TIMEOUT', str(REQUEST_TIMEOUT))),
            download_timeout=int(os_module.getenv('GDRIVE_VIDEOLOADER_DOWNLOAD_TIMEOUT', str(DOWNLOAD_TIMEOUT))),
            default_cookie_file=os_module.getenv('GDRIVE_VIDEOLOADER_DEFAULT_COOKIE_FILE', DEFAULT_COOKIE_FILE),
            default_video_extension=os_module.getenv('GDRIVE_VIDEOLOADER_DEFAULT_VIDEO_EXTENSION', DEFAULT_VIDEO_EXTENSION),
            max_retries=int(os_module.getenv('GDRIVE_VIDEOLOADER_MAX_RETRIES', str(MAX_RETRIES))),
            retry_backoff_factor=int(os_module.getenv('GDRIVE_VIDEOLOADER_RETRY_BACKOFF_FACTOR', str(RETRY_BACKOFF_FACTOR))),
        )


# Global configuration instance (can be overridden via environment variables)
config = Config.from_env()


# Cookie information dictionary with comprehensive explanations
COOKIE_INFO = {
    "SID": {
        "name": "SID",
        "what": "Primary session identifier for your Google account",
        "why": "Google uses this to verify you're logged in and authorized",
        "how": "DevTools → Application → Cookies → google.com → Copy 'Value' of SID",
        "when": "Sent with every API request to authenticate your session"
    },
    "HSID": {
        "name": "HSID",
        "what": "Hashed version of session ID for additional security",
        "why": "Provides extra security layer for session validation",
        "how": "Same location as SID, look for 'HSID' cookie",
        "when": "Used alongside SID for secure authentication"
    },
    "SSID": {
        "name": "SSID",
        "what": "Secure session identifier for HTTPS connections",
        "why": "Ensures secure communication with Google servers",
        "how": "Found in same cookies list, look for 'SSID'",
        "when": "Used for secure API calls over HTTPS"
    },
    "APISID": {
        "name": "APISID",
        "what": "Session ID specifically for Google API requests",
        "why": "Required for accessing Google Drive API endpoints",
        "how": "Look for 'APISID' in google.com cookies",
        "when": "Used when calling get_video_info API"
    },
    "SAPISID": {
        "name": "SAPISID",
        "what": "Secure version of APISID for API authentication",
        "why": "Provides secure authentication for API calls",
        "how": "Found alongside APISID, look for 'SAPISID'",
        "when": "Used for secure API authentication"
    },
    "__Secure-1PSID": {
        "name": "__Secure-1PSID",
        "what": "Secure first-party session ID",
        "why": "Modern security cookie for enhanced protection",
        "how": "Look for cookies starting with '__Secure-' prefix",
        "when": "Used for enhanced security in modern browsers"
    },
    "__Secure-3PSID": {
        "name": "__Secure-3PSID",
        "what": "Secure third-party session ID",
        "why": "Modern security cookie for cross-site protection",
        "how": "Look for cookies starting with '__Secure-' prefix",
        "when": "Used for enhanced security in modern browsers"
    }
}


# ============================================================================
# CUSTOM EXCEPTIONS
# ============================================================================

class GDriveVideoLoaderError(Exception):
    """Base exception for all gdrive-videoloader errors."""
    pass


class VideoNotFoundError(GDriveVideoLoaderError):
    """Raised when video is not found (404)."""
    pass


class AccessDeniedError(GDriveVideoLoaderError):
    """Raised when access is denied (403)."""
    pass


class CookieError(GDriveVideoLoaderError):
    """Raised when cookie-related errors occur."""
    pass


class DownloadError(GDriveVideoLoaderError):
    """Raised when download failures occur."""
    pass


class ConfigurationError(GDriveVideoLoaderError):
    """Raised when configuration is invalid."""
    pass


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def print_section(title: str, length: int = SEPARATOR_LENGTH, char: str = SEPARATOR_CHAR) -> None:
    """Print a formatted section separator with title.
    
    Args:
        title: Section title to display
        length: Length of separator line (default: SEPARATOR_LENGTH)
        char: Character to use for separator (default: SEPARATOR_CHAR)
    """
    print("\n" + char * length)
    print(title)
    print(char * length)


def log_message(message: str, log_level: str, level: str = "INFO") -> None:
    """Log a message based on log level.
    
    Args:
        message: Message to log
        log_level: Current log level (normal, verbose, quiet)
        level: Log level prefix (INFO, ERROR, WARNING)
    """
    if is_quiet(log_level):
        return
    
    prefix = f"[{level}]"
    if log_level == LOG_LEVEL_VERBOSE:
        print(f"{prefix} {message}")
    elif level in ["ERROR", "WARNING"]:
        print(f"{prefix} {message}")


def is_verbose(log_level: str) -> bool:
    """Check if verbose logging is enabled.
    
    Args:
        log_level: Log level to check
        
    Returns:
        True if verbose mode, False otherwise
    """
    return log_level == LOG_LEVEL_VERBOSE


def is_quiet(log_level: str) -> bool:
    """Check if quiet mode is enabled.
    
    Args:
        log_level: Log level to check
        
    Returns:
        True if quiet mode, False otherwise
    """
    return log_level == LOG_LEVEL_QUIET


def validate_log_level(log_level: str) -> str:
    """Validate and normalize log level.
    
    Args:
        log_level: Log level to validate
        
    Returns:
        Normalized log level (normal, verbose, or quiet)
    """
    log_level = log_level.lower().strip()
    if log_level in [LOG_LEVEL_NORMAL, LOG_LEVEL_VERBOSE, LOG_LEVEL_QUIET]:
        return log_level
    return LOG_LEVEL_NORMAL


# ============================================================================
# VIDEO ID EXTRACTION
# ============================================================================

def extract_video_id(url: str) -> str:
    """Extract video ID from Google Drive URL or return as-is if already an ID.
    
    Args:
        url: Google Drive URL or video ID
        
    Returns:
        Extracted video ID or original input if no pattern matches
    """
    # If it's already just an ID (no slashes or dots), return it
    if '/' not in url and '.' not in url:
        return url
    
    # Pattern for /file/d/VIDEO_ID/view or /file/d/VIDEO_ID/
    match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)
    
    # Pattern for ?id=VIDEO_ID
    parsed = urlparse(url)
    if 'id' in parse_qs(parsed.query):
        return parse_qs(parsed.query)['id'][0]
    
    # If no pattern matches, return the input (might be malformed, but let main() handle it)
    return url


# ============================================================================
# COOKIE MANAGEMENT
# ============================================================================

def get_cookies_automatically(output_file: str = DEFAULT_COOKIE_FILE) -> Optional[str]:
    """Automatically get cookies by opening a browser and waiting for user to log in.
    
    Args:
        output_file: Path to save cookies file (default: DEFAULT_COOKIE_FILE)
        
    Returns:
        Path to saved cookie file, or None if extraction failed
    """
    # Import all Selenium components at the start
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.common.by import By
        selenium_available = True
    except ImportError:
        print("\n[ERROR] Selenium is not installed.")
        print("Please install it using: pip install selenium")
        print("Also make sure you have Chrome browser installed.")
        return None
    
    print_section("AUTOMATIC COOKIE EXTRACTION")
    print("\nA browser window will open. Please:")
    print("  1. Log in to your Google account if prompted")
    print("  2. Navigate to Google Drive")
    print("  3. Wait for the script to detect you're logged in")
    print("  4. The browser will close automatically\n")
    input("Press Enter to open browser...")
    
    driver = None
    try:
        # Setup Chrome options
        chrome_options = Options()
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # Try to create driver
        try:
            driver = webdriver.Chrome(options=chrome_options)
        except Exception as e:
            print(f"\n[ERROR] Could not start Chrome browser: {e}")
            print("Make sure Chrome is installed and chromedriver is available.")
            print("You can install chromedriver manually or use webdriver-manager:")
            print("  pip install webdriver-manager")
            return None
        
        # Navigate to Google Drive
        print("\nOpening Google Drive...")
        driver.get(GOOGLE_DRIVE_URL)
        
        # Wait for user to log in using optimized detection
        print("\nWaiting for you to log in...")
        print("(The script will continue once it detects you're logged in)")
        print("(Detection: checking for authentication cookies)")
        
        logged_in = False
        start_time = time.time()
        last_url = ""
        url_stable_count = 0
        
        def has_auth_cookies(drv):
            """Check if authentication cookies are present (most reliable indicator)."""
            try:
                cookies = drv.get_cookies()
                cookie_names = [c.get('name', '').upper() for c in cookies]
                # Check for key authentication cookies
                has_sid = any('SID' in name for name in cookie_names)
                has_hsid = any('HSID' in name for name in cookie_names)
                return has_sid and has_hsid
            except Exception:
                return False
        
        def count_auth_cookies(drv):
            """Count the number of authentication cookies found."""
            try:
                cookies = drv.get_cookies()
                count = 0
                for c in cookies:
                    name = c.get('name', '').upper()
                    if 'SID' in name or 'HSID' in name or 'SSID' in name or 'APISID' in name:
                        count += 1
                return count
            except Exception:
                return 0
        
        def check_login_state(drv):
            """Check multiple indicators of login state."""
            # Method 1: Check for auth cookies (most reliable)
            if has_auth_cookies(drv):
                return True
            
            # Method 2: Check URL and page content
            try:
                current_url = drv.current_url.lower()
                if "drive.google.com" in current_url:
                    # Check page source for keywords (check larger portion)
                    try:
                        page_content = drv.page_source.lower()[:100000]
                        keywords = ["my drive", "new", "upload", "shared with me", "recent", "starred", "storage"]
                        if any(keyword in page_content for keyword in keywords):
                            return True
                    except Exception:
                        pass
            except Exception:
                pass
            
            return False
        
        # Detection loop with better feedback
        check_interval = 2  # Check every 2 seconds
        last_progress = 0
        
        print("[INFO] Checking for login status...")
        while time.time() - start_time < LOGIN_WAIT_TIMEOUT:
            try:
                # Check current URL
                current_url = driver.current_url
                
                # Track URL stability (URL not changing means page has loaded)
                if current_url == last_url:
                    url_stable_count += 1
                else:
                    url_stable_count = 0
                    last_url = current_url
                
                # Check login state
                if check_login_state(driver):
                    # Wait for URL to stabilize (not changing for 2 checks = 4 seconds)
                    if url_stable_count >= 2:
                        print("[INFO] Login detected! Waiting for cookies to stabilize...")
                        time.sleep(3)  # Wait 3 seconds for cookies to fully propagate
                        logged_in = True
                        break
                
                # Show progress every 10 seconds
                elapsed = int(time.time() - start_time)
                if elapsed > 0 and elapsed % 10 == 0 and elapsed != last_progress:
                    cookie_count = count_auth_cookies(driver)
                    print(f"[INFO] Checking... ({elapsed}s elapsed, {cookie_count} auth cookies found)")
                    print(f"[INFO] URL: {current_url[:70]}...")
                    last_progress = elapsed
                
            except Exception as e:
                pass
            
            time.sleep(check_interval)
        
        if not logged_in:
            print("\n[WARNING] Login detection timeout.")
            print("[INFO] Checking cookies anyway - you may already be logged in...")
            # Final check: if we have cookies, proceed anyway
            if has_auth_cookies(driver):
                print("[INFO] Found authentication cookies - proceeding...")
                time.sleep(2)  # Brief wait for any final cookies
                logged_in = True
            else:
                print("[WARNING] No authentication cookies found. Extracting cookies anyway...")
        
        # Extract cookies from multiple Google domains to get all auth cookies
        print("\nExtracting cookies from Google domains...")
        all_cookies = {}  # Use dict to deduplicate by cookie name
        
        # List of Google domains to extract cookies from
        google_domains = [
            "https://www.google.com",
            "https://accounts.google.com",
            "https://drive.google.com"
        ]
        
        for domain_url in google_domains:
            try:
                driver.get(domain_url)
                time.sleep(1)  # Brief wait for page to load
                domain_cookies = driver.get_cookies()
                for cookie in domain_cookies:
                    cookie_name = cookie['name']
                    # Add if not already present, or update if from a more specific domain
                    if cookie_name not in all_cookies:
                        all_cookies[cookie_name] = {
                            'name': cookie_name,
                            'value': cookie['value']
                        }
                print(f"  - {domain_url}: {len(domain_cookies)} cookies")
            except Exception as e:
                print(f"  - {domain_url}: Could not access ({e})")
        
        # Filter cookies for Google domains (improved domain matching)
        relevant_cookies = list(all_cookies.values())
        
        if not relevant_cookies:
            print("[WARNING] No relevant cookies found. You may need to log in again.")
            driver.quit()
            return None
        
        # Save cookies to file
        with open(output_file, 'w') as f:
            json.dump(relevant_cookies, f, indent=2)
        
        print(f"\n[SUCCESS] Cookies saved to: {output_file}")
        print(f"Found {len(relevant_cookies)} cookies.")
        
        # Show key cookies found
        key_cookies = [c['name'] for c in relevant_cookies if any(k in c['name'].upper() for k in ['SID', 'HSID', 'SSID'])]
        if key_cookies:
            print(f"[INFO] Key auth cookies: {', '.join(key_cookies[:5])}...")
        
        return output_file
        
    except KeyboardInterrupt:
        print("\n\n[INFO] Cookie extraction cancelled by user (Ctrl+C)")
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        return None
    except Exception as e:
        print(f"\n[ERROR] An error occurred: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def show_cookie_extraction_guidelines() -> None:
    """Display step-by-step instructions for finding cookies in browser DevTools."""
    print_section("HOW TO FIND COOKIES IN YOUR BROWSER")
    print("\nStep-by-step guide:\n")
    print("1. Open Google Drive in your browser (drive.google.com)")
    print("2. Make sure you're logged in to your Google account")
    print("3. Open Developer Tools:")
    print("   - Chrome/Edge: Press F12 or Right-click → Inspect")
    print("   - Firefox: Press F12 or Right-click → Inspect Element")
    print("4. Navigate to Cookies:")
    print("   - Chrome/Edge: Click 'Application' tab → Left sidebar → 'Cookies' → 'https://www.google.com'")
    print("   - Firefox: Click 'Storage' tab → Left sidebar → 'Cookies' → 'https://www.google.com'")
    print("5. Look for the cookies listed below")
    print("6. Copy the 'Value' column for each cookie")
    print("\nYou'll need cookies from:")
    print("  - google.com (most important)")
    print("  - drive.google.com (if available)")
    print_section("", length=SEPARATOR_LENGTH)


def prompt_cookie_value(cookie_info: dict, required: bool = False) -> Optional[str]:
    """Prompt for individual cookie value with comprehensive explanation.
    
    Args:
        cookie_info: Dictionary containing cookie information
        required: Whether this cookie is required
        
    Returns:
        Cookie value if provided, None if skipped (for optional cookies)
    """
    name = cookie_info["name"]
    what = cookie_info["what"]
    why = cookie_info["why"]
    how = cookie_info["how"]
    when = cookie_info["when"]
    
    status = "REQUIRED" if required else "OPTIONAL"
    
    # Create display name
    display_name = name.replace("__Secure-", "Secure ").replace("SID", "Session ID")
    
    print_section(f"{name} ({display_name}) - {status}")
    print(f"WHAT: {what}")
    print(f"WHY:  {why}")
    print(f"HOW:  {how}")
    print(f"WHEN: {when}")
    print(SEPARATOR_CHAR * SEPARATOR_LENGTH)
    
    if required:
        while True:
            value = input(f"\nEnter {name} value: ").strip()
            if value:
                return value
            print(f"{name} is required. Please enter a value.\n")
    else:
        value = input(f"\nEnter {name} value (press Enter to skip): ").strip()
        return value if value else None


def manual_cookie_entry(output_file: str = DEFAULT_COOKIE_FILE) -> Optional[str]:
    """Guide user through manual cookie entry step-by-step with detailed explanations.
    
    Args:
        output_file: Path to save cookies file (default: DEFAULT_COOKIE_FILE)
        
    Returns:
        Path to saved cookie file, or None if entry failed
    """
    print_section("MANUAL COOKIE ENTRY")
    print("\nYou'll be prompted for each cookie individually.")
    print("Each prompt will explain what the cookie is, why it's needed,")
    print("how to find it, and when it's used.\n")
    
    # Show initial guidelines
    show_cookie_extraction_guidelines()
    
    input("Press Enter when you have DevTools open and are ready to enter cookies...")
    
    cookies = {}
    
    # Required cookies
    print_section("REQUIRED COOKIES")
    
    sid_value = prompt_cookie_value(COOKIE_INFO["SID"], required=True)
    cookies["SID"] = sid_value
    
    hsid_value = prompt_cookie_value(COOKIE_INFO["HSID"], required=True)
    cookies["HSID"] = hsid_value
    
    # Optional but recommended cookies
    print_section("OPTIONAL COOKIES (Recommended)")
    print("These cookies improve authentication reliability but can be skipped.\n")
    
    for cookie_name in OPTIONAL_COOKIES:
        if cookie_name in COOKIE_INFO:
            value = prompt_cookie_value(COOKIE_INFO[cookie_name], required=False)
            if value:
                cookies[cookie_name] = value
    
    # Ask for secure cookies
    print_section("ADDITIONAL SECURE COOKIES (Optional)")
    print("Modern browsers use these secure cookies. Add them if available.\n")
    
    for cookie_name in SECURE_COOKIES:
        if cookie_name in COOKIE_INFO:
            value = prompt_cookie_value(COOKIE_INFO[cookie_name], required=False)
            if value:
                cookies[cookie_name] = value
    
    # Option to add custom cookies
    while True:
        add_more = input("\nAdd more custom cookies? (y/n): ").strip().lower()
        if add_more in ['y', 'yes']:
            cookie_name = input("Enter cookie name (or press Enter to finish): ").strip()
            if not cookie_name:
                break
            cookie_value = input(f"Enter {cookie_name} value: ").strip()
            if cookie_value:
                cookies[cookie_name] = cookie_value
        elif add_more in ['n', 'no']:
            break
        else:
            print("Please enter 'y' for yes or 'n' for no.")
    
    # Validate required cookies
    if not cookies.get("SID") or not cookies.get("HSID"):
        print("\n[ERROR] Required cookies (SID and HSID) must be provided.")
        return None
    
    # Convert to list format for JSON
    cookie_list = [{"name": name, "value": value} for name, value in cookies.items()]
    
    # Save cookies to file
    try:
        with open(output_file, 'w') as f:
            json.dump(cookie_list, f, indent=2)
        print(f"\n[SUCCESS] Cookies saved to: {output_file}")
        print(f"Saved {len(cookie_list)} cookies.")
        return output_file
    except PermissionError:
        print(f"\n[ERROR] Permission denied: Cannot write to '{output_file}'")
        return None
    except OSError as e:
        print(f"\n[ERROR] Failed to save cookies: {e}")
        return None
    except Exception as e:
        print(f"\n[ERROR] Failed to save cookies: {e}")
        return None


def show_cookie_instructions() -> None:
    """Display instructions on how to get cookies for authentication."""
    print_section("COOKIE INSTRUCTIONS")
    print("\nWhy cookies are needed:")
    print("  Some Google Drive videos require login to view/download.")
    print("  Cookies authenticate your browser session with Google.\n")
    print("How to get cookies:")
    print("  1. Automatic (Recommended):")
    print("     - The script can open a browser and extract cookies automatically")
    print("     - Just log in when prompted\n")
    print("  2. Manual Entry:")
    print("     - Use the interactive manual entry mode")
    print("     - Follow step-by-step prompts with detailed explanations")
    print("     - Copy cookies from browser DevTools\n")
    print("  3. Manual File Method:")
    print("     - Install a browser extension:")
    print("       * Chrome/Edge: 'Get cookies.txt LOCALLY' or 'Cookie-Editor'")
    print("       * Firefox: 'Cookie-Editor'")
    print("     - Log in to Google Drive in your browser")
    print("     - Open the extension and export cookies for:")
    print("       * google.com")
    print("       * drive.google.com")
    print("     - Save as JSON format\n")
    print("Cookie file format (JSON):")
    print('  [{"name": "SID", "value": "your-value"}, ...]')
    print('  OR')
    print('  {"SID": "your-value", "HSID": "your-value", ...}')
    print_section("", length=SEPARATOR_LENGTH)


def load_cookies(cookie_file: str) -> dict:
    """Load cookies from a JSON file and convert to a dictionary.
    
    Supports multiple formats:
    - List format: [{"name": "SID", "value": "..."}, ...]
    - Dict format: {"SID": "...", "HSID": "..."}
    - Nested format (Cookie Editor): {"url": "...", "cookies": [...]}
    
    Args:
        cookie_file: Path to JSON file containing cookies
        
    Returns:
        Dictionary of cookie name-value pairs, empty dict on error
        
    Raises:
        CookieError: If cookie file cannot be read or parsed
    """
    try:
        with open(cookie_file, 'r') as f:
            data = json.load(f)

        # Handle nested format (e.g., Cookie Editor export with "cookies" key)
        if isinstance(data, dict) and 'cookies' in data:
            data = data['cookies']
        
        # If the data is a list, convert to a dictionary
        if isinstance(data, list):
            cookies = {item['name']: item['value'] for item in data if 'name' in item and 'value' in item}
        else:
            cookies = data  # Assume it's already a dictionary
        return cookies
    except FileNotFoundError:
        print(f"Error loading cookies: Cookie file '{cookie_file}' not found.")
        return {}
    except json.JSONDecodeError as e:
        print(f"Error loading cookies: Invalid JSON format in '{cookie_file}': {e}")
        return {}
    except Exception as e:
        print(f"Error loading cookies: {e}")
        return {}


# ============================================================================
# VIDEO URL EXTRACTION
# ============================================================================

def get_video_url(page_content: str, verbose: bool) -> tuple[Optional[str], Optional[str]]:
    """Extract the video playback URL and title from the page content.
    
    Args:
        page_content: Content from get_video_info API response
        verbose: Whether to print verbose information
        
    Returns:
        Tuple of (video_url, title), both may be None if not found
    """
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


# ============================================================================
# FILE SIZE AND CHUNK SIZE MANAGEMENT
# ============================================================================

def get_optimal_chunk_size(file_size: int, user_chunk_size: Optional[int] = None) -> int:
    """Determine optimal chunk size based on file size.
    
    Args:
        file_size: Size of file in bytes
        user_chunk_size: User-specified chunk size (if provided)
        
    Returns:
        Optimal chunk size in bytes
    """
    if user_chunk_size:
        return user_chunk_size
    if file_size < FILE_SIZE_THRESHOLD_SMALL:
        return CHUNK_SIZE_SMALL
    elif file_size < FILE_SIZE_THRESHOLD_MEDIUM:
        return CHUNK_SIZE_MEDIUM
    elif file_size < FILE_SIZE_THRESHOLD_LARGE:
        return CHUNK_SIZE_LARGE
    else:
        return CHUNK_SIZE_XLARGE


def get_file_size(video_url: str, cookies: dict) -> int:
    """Get file size from video URL using HEAD request.
    
    Args:
        video_url: URL of the video file
        cookies: Dictionary of cookies for authentication
        
    Returns:
        File size in bytes, 0 if unable to determine
    """
    try:
        session = requests.Session()
        response = session.head(video_url, cookies=cookies, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        session.close()
        
        content_length = response.headers.get('content-length')
        if content_length:
            return int(content_length)
    except Exception:
        pass
    return 0


# ============================================================================
# INTERACTIVE PROMPTS
# ============================================================================

def prompt_chunk_size(file_size: int) -> int:
    """Prompt user for chunk size with file-size-based suggestion.
    
    Args:
        file_size: Size of file in bytes (0 if unknown)
        
    Returns:
        Chunk size in bytes
    """
    if file_size > 0:
        suggested_size = get_optimal_chunk_size(file_size)
        size_mb = file_size / (1024 * 1024)
        suggested_kb = suggested_size // 1024
        
        print_section("CHUNK SIZE SELECTION")
        print(f"File size: {size_mb:.2f} MB")
        
        # Determine size category for display
        if file_size < FILE_SIZE_THRESHOLD_SMALL:
            size_category = "< 10MB"
        elif file_size < FILE_SIZE_THRESHOLD_MEDIUM:
            size_category = "10-100MB"
        elif file_size < FILE_SIZE_THRESHOLD_LARGE:
            size_category = "100-500MB"
        else:
            size_category = ">= 500MB"
        
        print(f"Suggested chunk size: {suggested_kb} KB (optimal for files {size_category})")
        print(SEPARATOR_CHAR * SEPARATOR_LENGTH)
        
        while True:
            use_suggested = input(f"Use suggested chunk size ({suggested_kb} KB)? (y/n, default: y): ").strip().lower()
            if not use_suggested or use_suggested in ['y', 'yes']:
                return suggested_size
            elif use_suggested in ['n', 'no']:
                break
            print("Please enter 'y' for yes or 'n' for no.\n")
        
        # User wants custom chunk size
        print("\nExamples: 64, 128, 256, 512, 1024 (for 1MB)")
        while True:
            chunk_input = input("Enter chunk size in KB (e.g., 256): ").strip()
            if not chunk_input:
                # Use adaptive (return default, will be handled by get_optimal_chunk_size)
                print("Using adaptive chunk size.\n")
                return DEFAULT_CHUNK_SIZE
            try:
                chunk_kb = int(chunk_input)
                if chunk_kb > 0:
                    print(f"Using {chunk_kb} KB chunk size.\n")
                    return chunk_kb * 1024  # Convert KB to bytes
                print("Chunk size must be greater than 0.\n")
            except ValueError:
                print("Please enter a number (e.g., 256 for 256KB).\n")
    else:
        # File size unknown, use default
        print("\nFile size unknown, using default chunk size (64 KB).")
        return DEFAULT_CHUNK_SIZE


def prompt_logging_level() -> str:
    """Prompt user for logging level: normal (default), verbose, quiet.
    
    Returns:
        Log level string (normal, verbose, or quiet)
    """
    print_section("LOGGING LEVEL")
    print("(n)ormal - Standard progress and errors")
    print("(v)erbose - Detailed information")
    print("(q)uiet - Minimal output (errors only)")
    print(SEPARATOR_CHAR * SEPARATOR_LENGTH)
    
    while True:
        level = input("Logging level (n/v/q, default: normal): ").strip().lower()
        if not level or level == 'n' or level == 'normal':
            return LOG_LEVEL_NORMAL
        elif level == 'v' or level == 'verbose':
            return LOG_LEVEL_VERBOSE
        elif level == 'q' or level == 'quiet':
            return LOG_LEVEL_QUIET
        print("Please enter 'n' for normal, 'v' for verbose, or 'q' for quiet.\n")


def prompt_download_folder() -> str:
    """Prompt user for download directory.
    
    Returns:
        Absolute path to download directory
    """
    print_section("DOWNLOAD FOLDER")
    current_dir = os.getcwd()
    print(f"Current directory: {current_dir}")
    print(SEPARATOR_CHAR * SEPARATOR_LENGTH)
    
    while True:
        folder = input("Download folder (default: current directory): ").strip()
        if not folder:
            return current_dir
        
        # Expand user home directory if ~ is used
        folder = os.path.expanduser(folder)
        
        # Convert to absolute path
        if not os.path.isabs(folder):
            folder = os.path.abspath(folder)
        
        if os.path.isdir(folder):
            return folder
        elif os.path.exists(folder):
            print(f"'{folder}' exists but is not a directory.\n")
        else:
            create = input(f"Directory '{folder}' does not exist. Create it? (y/n): ").strip().lower()
            if create in ['y', 'yes']:
                try:
                    os.makedirs(folder, exist_ok=True)
                    return folder
                except PermissionError:
                    print(f"Permission denied: Cannot create directory '{folder}'\n")
                except OSError as e:
                    print(f"Failed to create directory: {e}\n")
            else:
                print("Please enter a valid directory path.\n")


def open_in_finder(file_path: str) -> None:
    """Open file in macOS Finder.
    
    Args:
        file_path: Path to file to open in Finder
    """
    if platform.system() != 'Darwin':  # macOS
        return
    
    try:
        # Use macOS 'open' command to reveal file in Finder
        subprocess.run(['open', '-R', file_path], check=True)
        print(f"\nOpened '{file_path}' in Finder.")
    except Exception as e:
        print(f"\n[WARNING] Could not open Finder: {e}")


# ============================================================================
# DOWNLOAD FUNCTIONALITY
# ============================================================================

def download_file(url: str, cookies: dict, filename: str, chunk_size: int, log_level: str = LOG_LEVEL_NORMAL) -> None:
    """Download the file from the given URL with provided cookies, supports resuming.
    
    Args:
        url: URL of the file to download
        cookies: Dictionary of cookies for authentication
        filename: Output filename
        chunk_size: Chunk size for downloading in bytes
        log_level: Logging level (normal, verbose, quiet)
    """
    # Validate filename
    if not filename:
        if not is_quiet(log_level):
            print("\n[ERROR] Filename is required for download.")
        return
    
    verbose = is_verbose(log_level)
    
    headers = {
        'Accept-Encoding': 'gzip, deflate',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://drive.google.com/',
        'Origin': 'https://drive.google.com',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'video',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'cross-site'
    }
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
    elif log_level == LOG_LEVEL_NORMAL and downloaded_size > 0:
        print(f"[INFO] Resuming download from byte {downloaded_size}")

    # Create session with retry strategy
    session = requests.Session()
    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=RETRY_BACKOFF_FACTOR,
        status_forcelist=RETRY_STATUS_CODES,
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    retry_count = 0
    
    while retry_count < MAX_RETRIES:
        try:
            response = session.get(url, stream=True, cookies=cookies, headers=headers, timeout=DOWNLOAD_TIMEOUT)
            
            if response.status_code in (HTTP_OK, HTTP_PARTIAL_CONTENT):
                total_size = int(response.headers.get('content-length', 0)) + downloaded_size
                
                # Use adaptive chunk sizing by default
                # If chunk_size is the default, use adaptive; otherwise use user's custom size
                if chunk_size == DEFAULT_CHUNK_SIZE:
                    # Use adaptive sizing
                    optimal_chunk_size = get_optimal_chunk_size(total_size)
                    if verbose:
                        print(f"[INFO] Using adaptive chunk size: {optimal_chunk_size // 1024}KB (file size: {total_size / (1024*1024):.1f}MB)")
                else:
                    # User specified custom chunk size, use it
                    optimal_chunk_size = chunk_size
                    if verbose:
                        print(f"[INFO] Using custom chunk size: {optimal_chunk_size // 1024}KB")
                
                # Use tqdm only if not quiet mode
                if is_quiet(log_level):
                    # Quiet mode: no progress bar
                    with open(filename, file_mode) as file:
                        for chunk in response.iter_content(chunk_size=optimal_chunk_size):
                            if chunk:
                                file.write(chunk)
                else:
                    # Normal or verbose mode: show progress bar
                    with open(filename, file_mode) as file:
                        with tqdm(
                            total=total_size,
                            initial=downloaded_size,
                            unit='B',
                            unit_scale=True,
                            unit_divisor=1024,
                            desc=os.path.basename(filename),
                            file=sys.stdout,
                            disable=is_quiet(log_level),
                            bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]'
                        ) as pbar:
                            for chunk in response.iter_content(chunk_size=optimal_chunk_size):
                                if chunk:
                                    file.write(chunk)
                                    pbar.update(len(chunk))
                
                if not is_quiet(log_level):
                    print(f"\n{filename} downloaded successfully.")
                return  # Success, exit retry loop
                
            elif response.status_code == HTTP_FORBIDDEN:
                if not is_quiet(log_level):
                    print(f"\n[ERROR] Access denied (403) while downloading {filename}.")
                    print("  - Video may require authentication")
                    print("  - Cookies may have expired")
                    print("  - Your account may not have download permission")
                if verbose:
                    print(f"\n[DEBUG] Response headers: {dict(response.headers)}")
                    print(f"[DEBUG] Cookies sent: {list(cookies.keys()) if cookies else 'None'}")
                return
            elif response.status_code == HTTP_NOT_FOUND:
                if not is_quiet(log_level):
                    print(f"\n[ERROR] Video not found (404). The download URL may have expired.")
                return
            else:
                if not is_quiet(log_level):
                    print(f"\n[ERROR] Failed to download {filename}, status code: {response.status_code}")
                retry_count += 1
                if retry_count < MAX_RETRIES:
                    wait_time = 2 ** retry_count
                    if not is_quiet(log_level):
                        print(f"Retrying in {wait_time} seconds... (attempt {retry_count + 1}/{MAX_RETRIES})")
                    time.sleep(wait_time)
                else:
                    return
                    
        except requests.exceptions.Timeout:
            retry_count += 1
            if retry_count < MAX_RETRIES:
                wait_time = 2 ** retry_count
                if not is_quiet(log_level):
                    print(f"\n[WARNING] Download timeout. Retrying in {wait_time} seconds... (attempt {retry_count + 1}/{MAX_RETRIES})")
                time.sleep(wait_time)
            else:
                if not is_quiet(log_level):
                    print(f"\n[ERROR] Download timeout after {MAX_RETRIES} attempts.")
                    print("  - Check your internet connection")
                    print("  - Try again later")
                return
        except requests.exceptions.RequestException as e:
            retry_count += 1
            if retry_count < MAX_RETRIES:
                wait_time = 2 ** retry_count
                if not is_quiet(log_level):
                    print(f"\n[WARNING] Network error: {e}")
                    print(f"Retrying in {wait_time} seconds... (attempt {retry_count + 1}/{MAX_RETRIES})")
                time.sleep(wait_time)
            else:
                if not is_quiet(log_level):
                    print(f"\n[ERROR] Network error after {MAX_RETRIES} attempts: {e}")
                    print("  - Check your internet connection")
                    print("  - Verify the video URL is accessible")
                return
        except KeyboardInterrupt:
            # Handle Ctrl+C gracefully
            session.close()
            if os.path.exists(filename) and downloaded_size > 0:
                file_size = os.path.getsize(filename)
                if not is_quiet(log_level):
                    print(f"\n\n[INFO] Download interrupted by user (Ctrl+C)")
                    print(f"[INFO] Partial download saved: {filename} ({file_size / (1024*1024):.2f} MB)")
                    print(f"[INFO] You can resume the download by running the same command again.")
            else:
                if not is_quiet(log_level):
                    print(f"\n\n[INFO] Download cancelled by user (Ctrl+C)")
            return
        finally:
            session.close()


# ============================================================================
# MAIN FUNCTION
# ============================================================================

def main(video_id: str, output_file: Optional[str] = None, chunk_size: int = DEFAULT_CHUNK_SIZE, 
         verbose: bool = False, cookie_file: Optional[str] = None, download_folder: Optional[str] = None, 
         open_finder: bool = False, log_level: str = LOG_LEVEL_NORMAL) -> None:
    """Main function to process video ID and download the video file.
    
    Args:
        video_id: Google Drive video ID
        output_file: Optional output filename
        chunk_size: Chunk size for downloading in bytes
        verbose: Whether to enable verbose output (legacy parameter)
        cookie_file: Path to cookie file for authentication
        download_folder: Directory to save downloaded file
        open_finder: Whether to open file in Finder after download (macOS only)
        log_level: Logging level (normal, verbose, quiet)
    """
    drive_url = DRIVE_VIDEO_INFO_URL_TEMPLATE.format(video_id=video_id)

    # Load cookies from file if provided, else use empty dict
    cookies = load_cookies(cookie_file) if cookie_file else {}

    # Determine verbosity from log_level (if not explicitly set via verbose parameter)
    # CLI mode uses verbose flag, interactive mode uses log_level
    if log_level == LOG_LEVEL_VERBOSE or (verbose and log_level == LOG_LEVEL_NORMAL):
        verbose = True
    elif log_level == LOG_LEVEL_QUIET:
        verbose = False
    # If verbose is True from CLI, override log_level
    if verbose:
        log_level = LOG_LEVEL_VERBOSE

    if verbose:
        print(f"[INFO] Accessing {drive_url}")
        if cookies:
            print(f"[INFO] Using provided cookies: {list(cookies.keys())}")

    try:
        response = requests.get(drive_url, cookies=cookies, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.Timeout:
        print(f"\n[ERROR] Request timeout accessing video info.")
        print("  - Check your internet connection")
        print("  - Try again later")
        return
    except requests.exceptions.RequestException as e:
        print(f"\n[ERROR] Network error accessing video info: {e}")
        print("  - Check your internet connection")
        print("  - Verify the video ID is correct")
        return
    
    # Check for authentication/access errors
    if response.status_code == HTTP_FORBIDDEN:
        print("\n[ERROR] Access denied (403). Possible reasons:")
        print("  - Video requires authentication - provide cookies using --cookie-file")
        print("  - Your account doesn't have access to this video")
        print("  - Cookies may have expired - try extracting new cookies")
        if not cookie_file:
            print("\n  Tip: Use interactive mode or --get-cookies to extract cookies automatically")
        return
    
    if response.status_code == HTTP_NOT_FOUND:
        print(f"\n[ERROR] Video not found (404).")
        print("  - Check if video ID is correct")
        print("  - Video may not exist or has been deleted")
        return
    
    if response.status_code != HTTP_OK:
        print(f"\n[ERROR] Failed to access video info. Status code: {response.status_code}")
        print("  - Check if video ID is correct")
        print("  - Verify you have access to the video")
        return
    
    page_content = response.text
    response_cookies = response.cookies.get_dict()
    # Merge response cookies with provided cookies (response cookies take precedence)
    cookies.update(response_cookies)

    video, title = get_video_url(page_content, verbose)

    # Ensure filename has an extension
    # Use output_file if provided, otherwise use title (if not None/empty)
    filename = output_file if output_file else (title if title and title.strip() else None)
    # Generate default filename if both output_file and title are None/empty/whitespace
    if not filename or (isinstance(filename, str) and not filename.strip()):
        filename = f"video_{video_id}{DEFAULT_VIDEO_EXTENSION}"
        if verbose:
            print(f"[INFO] No title found, using default filename: {filename}")
    else:
        # Clean up filename (strip whitespace)
        filename = filename.strip()
        if not os.path.splitext(filename)[1]:
            filename += DEFAULT_VIDEO_EXTENSION

    # Handle download folder
    if download_folder:
        # Ensure download folder exists
        if not os.path.exists(download_folder):
            try:
                os.makedirs(download_folder, exist_ok=True)
            except PermissionError:
                if not is_quiet(log_level):
                    print(f"\n[ERROR] Permission denied: Cannot create download folder '{download_folder}'")
                return
            except OSError as e:
                if not is_quiet(log_level):
                    print(f"\n[ERROR] Failed to create download folder: {e}")
                return
        
        # Construct full path
        filename = os.path.join(download_folder, os.path.basename(filename))
        if verbose:
            print(f"[INFO] Downloading to: {download_folder}")

    if video:
        if verbose:
            print(f"[INFO] Video found. Starting download...")
        elif log_level == LOG_LEVEL_NORMAL:
            print(f"\nStarting download...")
        
        download_file(video, cookies, filename, chunk_size, log_level)
        
        # Open in Finder if requested and download was successful
        if open_finder and os.path.exists(filename):
            open_in_finder(filename)
    else:
        print("\n[ERROR] Unable to retrieve the video URL.")
        print("Possible reasons:")
        print("  - Video ID is incorrect")
        print("  - Video requires authentication (view-only videos need cookies)")
        print("  - Your account doesn't have access to this video")
        if not cookie_file:
            print("\n  Tip: For view-only videos, use:")
            print("    python gdrive_videoloader.py --get-cookies")
            print("    python gdrive_videoloader.py VIDEO_ID --cookie-file cookies.json")


# ============================================================================
# INTERACTIVE MODE
# ============================================================================

def interactive_mode() -> None:
    """Interactive mode that prompts user for video URL and authentication."""
    try:
        print_section("Google Drive Video Downloader - Interactive Mode")
        
        # Step 1: Get video URL
        while True:
            video_input = input("Enter Google Drive Video URL (or Video ID): ").strip()
            if video_input:
                video_id = extract_video_id(video_input)
                break
            print("Please enter a valid URL or Video ID.\n")
        
        # Step 2: Ask about authentication (default: yes)
        while True:
            need_auth = input("Do you need authentication? (y/n, default: y): ").strip().lower()
            if not need_auth:
                need_auth = 'y'  # Default to yes
            if need_auth in ['y', 'yes', 'n', 'no']:
                break
            print("Please enter 'y' for yes or 'n' for no.\n")
        
        cookie_file = None
        if need_auth in ['y', 'yes']:
            # Check if cookies.json already exists
            if os.path.exists(DEFAULT_COOKIE_FILE):
                print(f"\nExisting '{DEFAULT_COOKIE_FILE}' found.")
                while True:
                    reuse = input("(r)euse existing cookies or (n)ew extraction? (default: r): ").strip().lower()
                    if not reuse:
                        reuse = 'r'  # Default to reuse
                    if reuse in ['r', 'reuse']:
                        cookie_file = DEFAULT_COOKIE_FILE
                        print(f"Using existing cookies from '{DEFAULT_COOKIE_FILE}'.\n")
                        break
                    elif reuse in ['n', 'new']:
                        cookie_file = None  # Will proceed to extraction
                        break
                    print("Please enter 'r' to reuse or 'n' for new.\n")
            
            # If no existing file or user chose new extraction
            if not cookie_file:
                # Step 3: Ask cookie method (auto or manual, default: manual)
                while True:
                    cookie_method = input("Cookie method: (a)uto or (m)anual? (default: m): ").strip().lower()
                    if not cookie_method:
                        cookie_method = 'm'  # Default to manual
                    if cookie_method in ['a', 'auto', 'm', 'manual']:
                        break
                    print("Please enter 'a' for auto or 'm' for manual.\n")
                
                if cookie_method in ['a', 'auto']:
                    # Automatic cookie extraction
                    cookie_file = get_cookies_automatically(DEFAULT_COOKIE_FILE)
                    if not cookie_file:
                        print("\nAutomatic cookie extraction failed.")
                        retry_manual = input("Try manual entry? (y/n): ").strip().lower()
                        if retry_manual in ['y', 'yes']:
                            cookie_file = manual_cookie_entry(DEFAULT_COOKIE_FILE)
                        else:
                            print("Continuing without cookies. Download may fail if authentication is required.\n")
                else:
                    # Manual cookie entry
                    cookie_file = manual_cookie_entry(DEFAULT_COOKIE_FILE)
                
                # If manual entry failed or cancelled, offer file input as fallback
                if not cookie_file:
                    print("\nCookie extraction cancelled or failed.")
                    use_file = input("Do you have a cookie file to use instead? (y/n): ").strip().lower()
                    if use_file in ['y', 'yes']:
                        show_cookie_instructions()
                        while True:
                            cookie_path = input("Enter cookie file path: ").strip()
                            if not cookie_path:
                                print("Cookie file path cannot be empty. Please enter a valid path.\n")
                                continue
                            
                            if os.path.exists(cookie_path):
                                cookie_file = cookie_path
                                break
                            else:
                                retry = input(f"File '{cookie_path}' not found. Show instructions again? (y/n): ").strip().lower()
                                if retry in ['y', 'yes']:
                                    show_cookie_instructions()
                                else:
                                    print("Continuing without cookies. Download may fail if authentication is required.\n")
                                    break
                    else:
                        print("Continuing without cookies. Download may fail if authentication is required.\n")
        
        # Step 4: Get video info to determine file size
        print_section("GETTING VIDEO INFORMATION")
        print("Fetching video details...\n")
        
        drive_url = DRIVE_VIDEO_INFO_URL_TEMPLATE.format(video_id=video_id)
        temp_cookies = load_cookies(cookie_file) if cookie_file else {}
        
        file_size = 0
        try:
            response = requests.get(drive_url, cookies=temp_cookies, timeout=REQUEST_TIMEOUT)
            if response.status_code == HTTP_OK:
                page_content = response.text
                response_cookies = response.cookies.get_dict()
                temp_cookies.update(response_cookies)
                
                video_url, title = get_video_url(page_content, False)
                
                if video_url:
                    # Get file size from video URL
                    file_size = get_file_size(video_url, temp_cookies)
                    if file_size > 0:
                        size_mb = file_size / (1024 * 1024)
                        print(f"Video file size: {size_mb:.2f} MB")
                    else:
                        print("File size: Unknown (will use default chunk size)")
                else:
                    print("[WARNING] Could not retrieve video URL. Will use default options.")
            else:
                print(f"[WARNING] Could not access video info (status: {response.status_code}). Will use default options.")
        except Exception as e:
            print(f"[WARNING] Failed to get video information: {e}")
            print("Proceeding with default options...")
        
        # Step 5: Download Options
        print_section("DOWNLOAD OPTIONS")
        
        # Prompt for chunk size
        chunk_size = prompt_chunk_size(file_size)
        
        # Prompt for logging level
        log_level = prompt_logging_level()
        
        # Prompt for download folder
        download_folder = prompt_download_folder()
        
        # Prompt for open in Finder (macOS only)
        open_finder = False
        if platform.system() == 'Darwin':  # macOS
            print_section("FINDER INTEGRATION")
            while True:
                open_finder_input = input("Open downloaded file in Finder? (y/n, default: y): ").strip().lower()
                if not open_finder_input or open_finder_input in ['y', 'yes']:
                    open_finder = True
                    break
                elif open_finder_input in ['n', 'no']:
                    open_finder = False
                    break
                print("Please enter 'y' for yes or 'n' for no.\n")
        
        # Start download with all options
        print_section("STARTING DOWNLOAD")
        main(video_id, None, chunk_size, False, cookie_file, download_folder, open_finder, log_level)
    except KeyboardInterrupt:
        print("\n\n[INFO] Operation cancelled by user (Ctrl+C)")
        print("[INFO] Exiting gracefully...")
        sys.exit(0)


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description="Script to download videos from Google Drive.")
        parser.add_argument("video_id", type=str, nargs='?', help="The video ID from Google Drive (e.g., 'abc-Qt12kjmS21kjDm2kjd'). If not provided, interactive mode will start.")
        parser.add_argument("-o", "--output", type=str, help="Optional output file name for the downloaded video (default: video name in gdrive).")
        parser.add_argument("-c", "--chunk_size", type=int, default=DEFAULT_CHUNK_SIZE, help=f"Optional chunk size (in bytes) for downloading the video. Default is {DEFAULT_CHUNK_SIZE} bytes (64KB). Adaptive sizing is used for default value.")
        parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose mode.")
        parser.add_argument("--cookie-file", type=str, help="Path to JSON file containing cookies for authentication.")
        parser.add_argument("--get-cookies", type=str, nargs='?', const=DEFAULT_COOKIE_FILE, help=f"Automatically get cookies by opening browser. Optionally specify output file (default: {DEFAULT_COOKIE_FILE}).")
        parser.add_argument("--version", action="version", version="%(prog)s 1.0")

        args = parser.parse_args()
        
        # Handle --get-cookies flag (standalone cookie extraction)
        if args.get_cookies is not None:
            cookie_file = get_cookies_automatically(args.get_cookies)
            if cookie_file:
                print(f"\nCookies saved successfully to: {cookie_file}")
                print("You can now use this file with --cookie-file option.")
            sys.exit(0)
        
        # If no video_id provided, start interactive mode
        if args.video_id is None:
            interactive_mode()
        else:
            # Extract video ID from URL if a URL was provided
            video_id = extract_video_id(args.video_id)
            if args.verbose:
                print(f"[INFO] Extracted video ID: {video_id}")
            
            # Map verbose flag to log_level for CLI mode
            log_level = LOG_LEVEL_VERBOSE if args.verbose else LOG_LEVEL_NORMAL
            main(video_id, args.output, args.chunk_size, args.verbose, args.cookie_file, None, False, log_level)
    except KeyboardInterrupt:
        print("\n\n[INFO] Operation cancelled by user (Ctrl+C)")
        print("[INFO] Exiting gracefully...")
        sys.exit(0)
