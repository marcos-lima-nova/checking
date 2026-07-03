# Project Briefing — PaddleOCR Document Processing Pipeline

> **Purpose of this file:** high-context handoff so any future LLM session can
> resume immediately with full alignment. Reflects the actual on-disk state as of
> **2026-07-03**. This is a briefing/context document, not an implementation plan.

---

## 1. Project Objectives

Build a modular, production-oriented OCR routine on **PaddleOCR with layout
analysis** that:

- Walks each **subfolder** of `inputs/checking/` (never files directly in it).
- Recursively finds and processes supported files:
  `.png .jpg .jpeg .pdf .doc .docx .odt`.
- Converts `.doc/.docx/.odt` → PDF (isolated step) before OCR.
- Extracts `.zip` archives (recursively, including nested folders/zips) into a
  controlled area and processes their supported contents.
- Sends PDFs and images directly to PaddleOCR; multi-page PDFs produce a single
  consolidated per-file result set (native behavior).
- Preserves **all native PaddleOCR artifacts** under
  `output/<input_folder>/<file_stem>/`.
- Writes a per-folder log in `logs/` and a per-folder, ADK-friendly
  `processing_summary.json` in `output/<input_folder>/`.
- Isolates per-file failures (full traceback logged + recorded), never aborting
  the batch.
- Supports skip-existing (default) or overwrite, and selective execution of a
  single folder for fast testing.
- Leaves clean extension points for future PDF/image preprocessing and future
  **ADK (Agent Developer Kit)** consumption of the summary JSON.

---

## 2. Current Progress

**Status: v1 is fully implemented and validated end-to-end.** All modules exist
and are byte-compilable. Real OCR runs have succeeded on GPU.

### Folders processed successfully (all `status: completed`, 0 failures)
| Folder | Files | Notes (source types exercised) |
|--------|-------|--------------------------------|
| `PI 283055` | 5/5 processed | all `original` PDFs |
| `PI 289211` | 4/4 processed | all `zip_extracted` (validates zip path) |
| `PI 293267` | 6/6 processed | mix of `original`, `converted` (.doc + .docx via LibreOffice) |

Together these validate the three provenance types: `original`,
`zip_extracted`, `converted`. Native artifacts confirmed present: `*_res.json`,
`*.md`, `*_layout_det_res.png`, `*_layout_order_res.png`, `*_overall_ocr_res.png`,
`*_region_det_res.png`, `*_preprocessed_img.png`, table `*.html`/`*.xlsx`,
`*.tex`, `*.docx`, and `imgs/`.

### Folders still PENDING (present in `inputs/checking/`, no output yet)
- `PI 289211 - TV CARTOON NETWORK`
- `PI 292174`
- `PI 292850 - RÁDIO VERTSUL FM 93,5`
- `PI 294215 - TV REDE ESTAÇÃO`
- `PI 294225 - TV MILL`
- `PI 295870 - CATURITÉ AM`

### Logs present
`logs/PI_283055_03072026.log`, `logs/PI_289211_03072026.log`,
`logs/PI_293267_03072026.log`, `logs/_run_03072026.log` (bootstrap logger).

---

## 3. Technical Stack (verified on this machine)

- **OS / HW:** Linux (Pop!_OS 24.04), NVIDIA **GeForce RTX 3060**.
- **Python:** 3.11.15 via conda env **`paddleocr`**
  (`/home/vini/miniconda3/envs/paddleocr/bin/python3`).
- **paddleocr `3.7.0`**, **paddlex `3.7.2`**, **paddlepaddle-gpu `3.3.0`**.
- **PyYAML** for config.
- **LibreOffice** present at `/usr/bin/soffice` and `/usr/bin/libreoffice`
  (system dependency for document conversion; not a pip package).
- OCR pipeline in use: **`PPStructureV3`** (layout analysis). `PaddleOCRVL`
  supported as an alternative via config.

Verified PaddleOCR 3.7.0 API facts (do not re-assume blindly on version change):
- `PPStructureV3(...)` accepts `device` as a kwarg (`"gpu:0"`/`"cpu"`), plus
  `use_doc_orientation_classify`, `use_doc_unwarping`, `use_textline_orientation`,
  `use_seal_recognition`, `use_table_recognition`, `use_formula_recognition`,
  `use_chart_recognition`, `use_region_detection`, `lang`, `ocr_version`,
  `enable_hpi`, etc.
- `.predict(input)` returns an **iterable** (one result per page).
- Each result exposes `save_all(save_path=<dir>)` — used to persist all native
  artifacts.
- `doc2md_supported_formats()` = `['docx','pptx','xlsx']` only, and produces
  markdown (not layout OCR) — so it is **intentionally not used**; LibreOffice→PDF
  is the conversion strategy.

---

## 4. Repository Layout (actual)

```
PaddleOCR/
├─ inputs/checking/<subfolders>     # input jobs (also a stray inputs/testocr.png — ignored, correct)
├─ misc/
│  ├─ extracted/<folder>/<zip_stem>/   # controlled zip extraction (PI 289211 present)
│  ├─ converted/<folder>/<file_stem>/  # doc/docx/odt -> pdf (PI 293267 present)
│  ├─ GPT_CHECKING-...zip / GPT_CHECKING-.../  # pre-existing manual artifacts (not ours)
├─ output/<folder>/<file_stem>/     # native OCR artifacts + processing_summary.json
├─ output_pdf/PI283055/             # OLD manual experiment — leave untouched, not part of spec
├─ logs/<folder>_<DDMMYYYY>.log     # one per analyzed folder + _run bootstrap log
├─ config/
│  ├─ config.yaml                   # general/default config (process_all_folders: true)
│  └─ test_config.yaml              # single-folder test (target_folder: "PI 292174")
├─ src/
│  ├─ __init__.py        exceptions.py     config.py
│  ├─ logger_setup.py    file_scanner.py   zip_handler.py
│  ├─ document_converter.py            ocr_runner.py     preprocessing.py
│  ├─ output_manager.py  summary_writer.py pipeline.py
├─ main.py               requirements.txt  README.md
└─ plans/ocr-pipeline-v1.md          # the original implementation plan
```

### Module responsibilities (as built)
- **`config.py`** — `AppConfig` + `PaddleOcrConfig` dataclasses; `load_config()`
  (defaults ← YAML), `apply_overrides()` (← CLI), `validate()`. Path resolution
  helpers; `resolved_device()`; skip/overwrite mutual-exclusion check.
- **`logger_setup.py`** — `get_folder_logger()`; file `logs/<folder>_<DDMMYYYY>.log`
  (+ optional console); sanitizes folder names; exposes `logger.log_file_path`.
- **`file_scanner.py`** — `list_target_subfolders()` (selective/all + missing-target
  error), `scan_folder()` (recursive; splits supported/zip/unsupported),
  `classify_extracted_files()`.
- **`zip_handler.py`** — `extract_zip()`; extracts to `misc/extracted/...`, clears
  prior extraction, **guards against zip-slip**, recurses into nested zips.
- **`document_converter.py`** — `DocumentConverter` + `LibreOfficeConverter`
  strategy; `soffice --headless --convert-to pdf`; raises `ConversionError`
  (per-file recoverable) when unavailable/failed.
- **`ocr_runner.py`** — `OcrRunner` wraps PaddleOCR; **built once, reused**;
  `_build_kwargs()` drops `None`s; `_instantiate_robustly()` drops kwargs the
  pipeline rejects (version-tolerant); `run()` iterates pages and `save_all()`.
- **`output_manager.py`** — resolves `output/<folder>/<file_stem>/`; `should_process()`
  applies skip/overwrite; disambiguates stem collisions with a relative-path suffix.
- **`summary_writer.py`** — `FileRecord` + `FolderSummary`; computes totals and
  folder status (`completed` / `completed_with_errors` / `failed`); writes JSON.
- **`preprocessing.py`** — no-op hooks (`preprocess_input`, `pdf_to_images`,
  `deskew`, `binarize`, `enhance_contrast`, `denoise`, `resize`, `select_pages`).
- **`pipeline.py`** — `process_folder()` orchestrates scan → zip → per-item
  (convert → resolve output → skip/overwrite → preprocess → OCR) with per-file
  `try/except` (traceback logged + recorded), then writes summary.
- **`main.py`** — argparse (`--config`, `--target-folder`, `--process-all`,
  `--device`, `--overwrite`, `--skip-existing`, `--log-level`), builds one shared
  `OcrRunner`, iterates folders; exit codes 0/1/2/3.

---

## 5. Recent Architectural Decisions

1. **Layout pipeline = `PPStructureV3`** (default), pluggable to `PaddleOCRVL`
   via `paddleocr.pipeline` in config. All PaddleOCR calls isolated in
   `ocr_runner.py` so version/pipeline swaps happen in one place.
2. **Version-tolerant instantiation:** kwargs that a given pipeline/version does
   not accept are dropped and retried, rather than hard-coding a signature.
3. **Document conversion via LibreOffice→PDF** (isolated strategy), NOT
   `doc2md` (which is markdown-only and lacks `.doc/.odt`).
4. **Controlled scratch areas** under `misc/extracted/` and `misc/converted/`,
   scoped by input-folder name, to avoid mixing with originals and to prevent
   uncontrolled overwrite (prior extraction is cleared before re-extracting).
5. **Failure isolation per file** with full tracebacks in the log and structured
   `error` field in the summary.
6. **Skip-existing default**, overwrite optional; both are mutually exclusive and
   validated. Overwrite clears the target output dir before re-running.
7. **Stem-collision safety:** two files with the same stem in different subpaths
   get distinct output dirs (`<stem>__<relparent>`).
8. **Config precedence:** dataclass defaults → YAML → CLI overrides.
9. **ADK-oriented summary:** `processing_summary.json` records provenance
   (`original|zip_extracted|converted`), converted path, output folder, status,
   timings, and errors, so a future agent can locate and triage outputs.
10. **Shared, single OCR pipeline instance** across all files/folders in a run
    (model init is expensive).

---

## 6. Pending Tasks / Next Steps

- **Process remaining folders** (currently no output): `PI 289211 - TV CARTOON
  NETWORK`, `PI 292174`, `PI 292850 - RÁDIO VERTSUL FM 93,5`, `PI 294215 - TV REDE
  ESTAÇÃO`, `PI 294225 - TV MILL`, `PI 295870 - CATURITÉ AM`. A full run
  (`--process-all` / `config.yaml`) will cover them.
- **Investigate potential duplicate/confusing input state:** `inputs/checking/`
  contains both `PI 289211` and `PI 289211 - TV CARTOON NETWORK`. The processed
  `PI 289211` was fed from a zip; confirm whether these are duplicates or distinct
  jobs before a full batch run, to avoid redundant processing.
- **No git repository is initialized** yet (`git status` → not a repo). If version
  control is desired, initialize and add a `.gitignore` (exclude `output/`,
  `misc/extracted/`, `misc/converted/`, `logs/`, `__pycache__/`, model caches).
- **Optional cleanup:** `output_pdf/` and the `misc/GPT_CHECKING-*` artifacts are
  pre-existing/manual and outside the spec; decide whether to keep or remove.
- **Future (explicitly deferred in v1):** implement preprocessing hooks (PDF→image,
  deskew, binarization, contrast, denoise, page selection, pipeline fallback) and
  the actual ADK agent that consumes `processing_summary.json`.
- **Optional hardening:** automated tests around scanner/zip/summary logic;
  potential concurrency for LibreOffice conversion vs. OCR.

---

## 7. Constraints & Context for the Next AI Session

- **Environment:** always use the conda env `paddleocr`
  (`/home/vini/miniconda3/envs/paddleocr/bin/python3`). GPU device string
  `gpu:0`; use `--device cpu` on non-GPU machines and swap `paddlepaddle-gpu`
  for `paddlepaddle` in `requirements.txt`.
- **Folder names contain spaces and accents** (e.g. `PI 295870 - CATURITÉ AM`).
  Always quote `--target-folder` values. Paths are handled via `pathlib`.
- **Never process files directly in `inputs/checking/`** (e.g. the stray
  `inputs/testocr.png` must stay ignored). Only subfolders are analyzed.
- **Do NOT touch `output_pdf/`** — it is an old manual experiment, not part of the
  pipeline output contract.
- **LibreOffice must be on PATH** for `.doc/.docx/.odt`; if absent, those files
  fail per-file (recorded) without aborting the batch.
- **First OCR run downloads models** (adds minutes); subsequent runs are fast
  (observed: ~1.3–3s per typical page; a 154s file was a large multi-page PDF).
- **Do not blindly assume PaddleOCR API signatures** if the installed version
  changes; the runner already tolerates unknown kwargs, but verify behavior.
- **Skip-existing is default:** re-running a completed folder will skip files
  whose output dir already exists/non-empty. Use `--overwrite` to force
  reprocessing.

### How to run (reference)
```bash
python main.py --config config/test_config.yaml            # single folder (test)
python main.py --target-folder "PI 292174"                 # single folder via CLI
python main.py --config config/config.yaml                 # all folders
python main.py --target-folder "PI 293267" --device cpu --overwrite
```

### Key file references
- Orchestration entry: `main.py:main()`
- Per-folder flow: `src/pipeline.py:process_folder()` and `_process_item()`
- OCR isolation: `src/ocr_runner.py:OcrRunner.run()` (`save_all` at ~line 162)
- Summary schema: `src/summary_writer.py` (`FolderSummary.to_dict()`)
- Original implementation plan: `plans/ocr-pipeline-v1.md`
