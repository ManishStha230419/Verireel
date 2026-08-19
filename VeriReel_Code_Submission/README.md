# VeriReel

VeriReel is a local Flask application that compares two short-form videos and reports potential content reuse. Users can upload two video files or provide two public TikTok post links. Results support human review and are not automatic legal determinations.

## Features

- Four perceptual hashes: pHash, wHash, dHash, and aHash
- RGB colour, temporal, and motion signatures
- Sliding-window, mirrored-orientation, and limited time-scale alignment
- Local file and public TikTok-link input
- Downloadable PDF comparison reports
- Temporary media processing with automatic source-file deletion
- Input validation, bounded jobs, rate limits, protected results, and security-event logging

## Windows

Download and extract the repository, then double-click `start.bat`.

The first launch automatically:

1. Downloads managed Python 3.12 when a compatible Python installation is unavailable.
2. Creates an isolated `.venv` environment.
3. Installs every package in `requirements.txt`.
4. Creates `.env` from `.env.example`.
5. Verifies the installation and starts the server.
6. Opens `http://127.0.0.1:5000`.

Keep the launcher window open while using the application. Press `Ctrl+C` to stop it.

To stop VeriReel and return the folder to its small, brand-new state, double-click
`reset.bat`. It removes the downloaded Python environment, local settings, logs,
caches, and temporary job data while preserving every file needed for GitHub.
The next `start.bat` run performs the complete first-time setup again.

## Linux

Run the single launcher from the extracted project directory:

```bash
bash start.sh
```

If Python 3.11 or newer is unavailable, the launcher downloads managed Python 3.12. Automatic setup requires `curl` or `wget` and an internet connection.

Open `http://127.0.0.1:5000` if the browser does not open automatically.

To stop VeriReel and remove all generated runtime files, open another terminal in
the project directory and run:

```bash
bash reset.sh
```

The next `bash start.sh` run downloads or creates Python again, installs every
dependency, and starts a fresh copy at `http://127.0.0.1:5000`.

## Tests

After setup, run:

Windows:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Linux:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The runtime-focused suite covers the API, security boundaries, video fingerprints, TikTok URL handling, temporary cleanup, PDF reporting, and the browser contract.

## Controlled pilot validation

The repository includes a deterministic, non-sensitive evaluation harness. It
generates synthetic source clips, applies six controlled transformations,
constructs an equal number of unrelated negative pairs, runs the production
fingerprinting code at the frozen 75% threshold, compares it with the promised
single-pHash baseline, and saves every prediction and summary metric.

Windows:

```powershell
.venv\Scripts\python.exe -m evaluation.generate_controlled_dataset --force
.venv\Scripts\python.exe -m evaluation.run_benchmark
.venv\Scripts\python.exe -m evaluation.plot_results
```

Linux:

```bash
.venv/bin/python -m evaluation.generate_controlled_dataset --force
.venv/bin/python -m evaluation.run_benchmark
.venv/bin/python -m evaluation.plot_results
```

Outputs are written to `evaluation/results/`. The controlled dataset is a
proof-of-function and regression benchmark only. It does not replace external
evaluation on VCDB, CC_WEB_VIDEO, VCSL, or locally representative real-world
videos, and its results must not be described as general effectiveness.

## Configuration

Safe defaults are documented in `.env.example`. Generated environments, `.env`, logs, caches, and temporary files are excluded by `.gitignore` and must not be committed.

TikTok-link processing depends on TikTok allowing public access to each post. When TikTok blocks automated access, save both videos lawfully and use the upload option.
