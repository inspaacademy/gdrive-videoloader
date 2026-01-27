# GDrive VideoLoader

**GDrive VideoLoader** is a Python-based tool to download videos from Google Drive effortlessly, **including those marked as _view-only_** (no download option). It supports resumable downloads, progress bars, and various customization options for video fetching and downloading.

## Features

- Download videos even if marked as *view-only* (without a download button)
- Access private files using browser cookies (cookies.txt / JSON export)
- Supports resumable downloads (continue from where it stopped)
- Displays a progress bar for ongoing downloads
- Allows custom chunk sizes for downloading
- Optionally specify a custom output file name
- Verbose mode for detailed logs during execution

## Installation

### Prerequisites

- Python 3.7+
- Pip (Python package manager)

### Dependencies

Install the required Python packages using the following command:

```bash
pip install -r requirements.txt
```

## Usage

### Basic Command

To download a video, you can provide either the Google Drive file ID or a full Google Drive URL:

**Using a file ID:**
```bash
python gdrive_videoloader.py <video_id>
```

**Using a full Google Drive URL:**
```bash
python gdrive_videoloader.py https://drive.google.com/file/d/<video_id>/view
```

The script will automatically extract the file ID from the URL if you provide a full link.

### Options

| Parameter                | Description                                                       | Default Value         |
|--------------------------|-------------------------------------------------------------------|-----------------------|
| `<video_id>`             | The video ID from Google Drive or a full Google Drive URL (required). The script automatically extracts the ID from URLs like `https://drive.google.com/file/d/ID/view`. | N/A                   |
| `-o`, `--output`         | Custom output file name for the downloaded video.                | Video name in GDrive  |
| `-c`, `--chunk_size`     | Chunk size (in bytes) for downloading the video.                 | 1MB (1048576 bytes)   |
| `-t`, `--threads`        | Number of parallel threads for downloading.                      | 16                    |
| `-v`, `--verbose`        | Enable verbose mode for detailed logs.                           | Disabled              |
| `--cookies`              | Path to a cookies.txt file or JSON cookie export for private files.| N/A                  |
| `--version`              | Display the script version.                                      | N/A                   |
| `-h`, `--help`           | Display the help message.                                        | N/A                   |

### Private Files (cookies.txt / JSON export)

If a file is private or restricted, export your browser cookies and pass them with `--cookies`.

**Examples:**

```bash
python gdrive_videoloader.py --cookies cookies.txt https://drive.google.com/file/d/<video_id>/view
```

The script accepts:

- **Netscape cookies.txt** format (from extensions like “cookies.txt”)
- **JSON cookie exports** (from extensions like “Get cookies.txt LOCALLY”)
- **Cookie-Editor** extension (Chrome): https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm?hl=en

Make sure you are logged in to the Google account that has access to the file before exporting cookies.

## TODO

### Features
- Add support for downloading subtitles.
- Add support for multiple downloads (list or file of video IDs).
- Allow selection of video quality.
- Implement temporary file naming during download.

### UX
- Safely handle interruptions (KeyboardInterrupt).
- Display custom error messages based on request responses.

### Performance
- [x] Implement parallel downloads to speed up the process.

### Organization
- Modularize the project into separate files (`downloader.py`, `cli.py`, `utils.py`).
- Add logging support using the `logging` module.
- Validate output file names for compatibility with the operating system.

### Code Quality
- Create automated tests for core functions.
- Add detailed documentation using `pdoc` or `Sphinx`.

## Contributing
Contributions are always welcome! If you have suggestions for improving the script or adding new features, feel free to fork the repository and submit a pull request.

## License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
