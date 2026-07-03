# PaddleOCR Document Processing Pipeline (v1)

First functional version of an OCR routine built on **PaddleOCR** with **layout
analysis** (`PPStructureV3`). It scans subfolders of `inputs/checking/`,
processes supported documents (images, PDFs, and office documents converted to
PDF), extracts `.zip` archives, and writes PaddleOCR's native outputs plus a
structured, ADK-friendly `processing_summary.json` per folder.

## Features

- Recursive scan of each subfolder under `inputs/checking/` (files directly in
  `inputs/checking/` are never processed).
- Supported formats: `.png`, `.jpg`, `.jpeg`, `.pdf`, `.doc`, `.docx`, `.odt`.
- `.doc` / `.docx` / `.odt` are converted to PDF (LibreOffice headless) in an
  isolated step before OCR.
- `.zip` archives are extracted to a controlled area and analyzed recursively
  (including nested folders and nested zips).
- PDFs are sent directly to PaddleOCR; multi-page PDFs produce a consolidated
  per-file result following PaddleOCR's native behavior.
- Native PaddleOCR outputs are preserved (`*_res.json`, `*.md`,
  `*_layout_det_res.png`, `imgs/`, ...).
- Per-folder logging in `logs/`, per-folder `processing_summary.json` in
  `output/`.
- Failures on one file never stop the run; full tracebacks are logged and
  recorded.
- Skip-existing (default) or overwrite behavior, configurable.
- Selective execution of a single folder for fast testing.
- Clear extension points for future PDF/image preprocessing.

## Requirements

- Python 3.11 (developed on a conda env named `paddleocr`).
- `paddleocr==3.7.0`, `paddlepaddle-gpu==3.3.0` (or `paddlepaddle` for CPU),
  `PyYAML`.
- **LibreOffice** (`soffice`) on `PATH` for office-document conversion.

```bash
pip install -r requirements.txt
# CPU-only machines: replace paddlepaddle-gpu with paddlepaddle in requirements.
sudo apt install libreoffice   # Debian/Ubuntu/Pop!_OS
```

## Project structure

```
PaddleOCR/
├─ inputs/checking/        # input: one subfolder per job
├─ misc/
│  ├─ extracted/           # controlled zip extraction area
│  └─ converted/           # doc/docx/odt -> pdf output area
├─ output/                 # results: output/<folder>/<file>/ + processing_summary.json
├─ logs/                   # one log per analyzed folder
├─ config/
│  ├─ config.yaml          # general configuration
│  └─ test_config.yaml     # single-folder test configuration
├─ src/                    # modular implementation
├─ main.py                 # entry point
├─ requirements.txt
└─ README.md
```

## Usage

Process all subfolders (uses defaults / `config/config.yaml` if you pass it):

```bash
python main.py --config config/config.yaml
```

Process a single folder (quote names with spaces):

```bash
python main.py --target-folder "PI 292174"
```

Use the test config (single folder):

```bash
python main.py --config config/test_config.yaml
```

Force CPU and overwrite existing outputs:

```bash
python main.py --target-folder "PI 293267" --device cpu --overwrite
```

### CLI options

| Option | Description |
|--------|-------------|
| `--config PATH` | YAML config file (defaults used if omitted). |
| `--target-folder NAME` | Process only this subfolder of `inputs/checking`. |
| `--process-all` | Process all subfolders (overrides config). |
| `--device DEV` | `gpu:0`, `gpu`, or `cpu`. |
| `--overwrite` | Overwrite existing outputs instead of skipping. |
| `--skip-existing` | Skip files whose output already exists (default). |
| `--log-level LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`. |

Precedence: dataclass defaults → YAML file → CLI arguments.

## Selective-execution rules

1. If `target_folder` is set, only that folder is processed.
2. If `target_folder` is not set and `process_all_folders=true`, all subfolders
   are processed.
3. If `target_folder` does not exist, a clear error is raised and the run stops.
4. Files directly inside `inputs/checking/` are never processed.
5. Selective runs use the exact same output/log layout as full runs.

## Output layout

```
output/
└─ PI 292174/
   ├─ processing_summary.json
   ├─ <file_stem>/
   │  ├─ <file>_0_res.json
   │  ├─ <file>_0.md
   │  ├─ <file>_0_layout_det_res.png
   │  └─ imgs/...
   └─ ...
```

### `processing_summary.json`

Per analyzed folder. Designed for later consumption by an ADK agent: it records
each file's provenance (`original` / `zip_extracted` / `converted`), the OCR
output folder, status (`processed` / `skipped` / `failed`), timings, and full
errors.

```json
{
  "input_folder": "inputs/checking/PI 292174",
  "output_folder": "output/PI 292174",
  "log_file": "logs/PI_292174_03072026.log",
  "execution_datetime": "...",
  "status": "completed_with_errors",
  "total_files_found": 10,
  "total_files_processed": 8,
  "total_files_skipped": 1,
  "total_files_failed": 1,
  "files": [
    {
      "original_path": "...",
      "source_type": "original",
      "zip_source": null,
      "converted_file_path": null,
      "file_name": "contrato.pdf",
      "file_extension": ".pdf",
      "output_folder": "output/PI 292174/contrato",
      "status": "processed",
      "error": null,
      "started_at": "...",
      "finished_at": "...",
      "duration_seconds": 0.0
    }
  ]
}
```

## Logs

- One log file per analyzed folder: `logs/<folder>_<DDMMYYYY>.log`.
- Logs are never written under `output/`.
- Includes start/end, config used, files found/processed/skipped/ignored,
  extracted and converted files, per-file duration, total folder time, and full
  tracebacks on error.

## Configuration reference

See `config/config.yaml` for the full, commented set of options: paths, device,
skip/overwrite behavior, accepted extensions, and PaddleOCR pipeline parameters
(pipeline choice, language, layout/table/formula/seal/orientation flags, etc.).

## Extensibility

- **OCR pipeline** is isolated in `src/ocr_runner.py`; switch between
  `PPStructureV3` and `PaddleOCRVL` via `paddleocr.pipeline` in config.
- **Document conversion** strategy is isolated in `src/document_converter.py`
  (LibreOffice by default; swappable).
- **Preprocessing** hooks live in `src/preprocessing.py` (v1: no-ops) for future
  PDF→image, deskew, binarization, denoising, page selection, etc.
```
