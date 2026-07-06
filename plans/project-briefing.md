# Project Briefing — PaddleOCR Document Processing Pipeline

> **Purpose of this file:** high-context handoff so any future LLM session can
> resume immediately with full alignment. Reflects the actual on-disk state as of
> **2026-07-06**. This is a briefing/context document, not an implementation plan.
>
> **What changed since 2026-07-03:**
> 1. The **Option B post-OCR logical-document segmentation** stage was
>    implemented and validated (see §8). PaddleOCR now saves native artifacts
>    into a `raw_<stem>/` subfolder, and a new pipeline layer classifies each
>    page by YAML rules, groups pages into logical documents, copies per-document
>    artifacts, and emits ADK-oriented manifests (`source_manifest.json`,
>    per-document `llm_ready.json`). Reference: `plans/plan_opcao_B.md`.
> 2. A **PaddleOCR + Tesseract per-box OCR fusion** stage was implemented and
>    validated (see §9). Before the text index, each page is rendered to a clean
>    `page_images/page_XXX.png`, PaddleOCR boxes are cropped from it, Tesseract
>    OCRs each crop, and results are fused per box (highest confidence wins). The
>    `page_text_indexer` now prefers the fused result. Reference:
>    `.kilo/plans/1783345614893-paddleocr-tesseract-fusion.md`.

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
  `output/<input_folder>/<file_stem>/raw_<file_stem>/` (raw artifacts are now
  isolated in a `raw_` subfolder; see §8).
- Writes a per-folder log in `logs/` and a per-folder, ADK-friendly
  `processing_summary.json` in `output/<input_folder>/` (now including
  `documents_detected` per file).
- **Fuses PaddleOCR with Tesseract per box (see §9):** each page is rendered to
  a clean `page_images/page_XXX.png`, PaddleOCR boxes are cropped from it,
  Tesseract OCRs each crop, and the two results are fused per box (the string
  with the highest normalized confidence is selected; alternatives/conflicts are
  preserved). Optional and config-gated; when Tesseract is unavailable the
  pipeline falls back to PaddleOCR-only with zero regression.
- **Segments each OCR'd file into logical documents (Option B):** a file may
  contain several logical documents across different pages; the pipeline
  classifies each page by rules loaded from `config/document_rules.yaml`, groups
  consecutive pages into logical documents, copies per-page artifacts into
  `documents/<NNN_tipo>/`, and generates per-document `document_metadata.json` +
  `llm_ready.json` plus a per-file `source_manifest.json`.
- Isolates per-file failures (full traceback logged + recorded), never aborting
  the batch. Segmentation and Tesseract-fusion failures are isolated from OCR
  success too (per-page/per-box isolation for fusion).
- Supports skip-existing (default) or overwrite, and selective execution of a
  single folder for fast testing.
- Leaves clean extension points for future PDF/image preprocessing and for
  **ADK (Agent Developer Kit)** consumption of `source_manifest.json` /
  `llm_ready.json` (the primary ADK entry point is now `source_manifest.json`).

---

## 2. Current Progress

**Status: v1 OCR pipeline + Option B segmentation + PaddleOCR/Tesseract fusion
are implemented and validated.** All modules exist and are byte-compilable. Real
OCR runs have succeeded on GPU (v1). The Option B segmentation stage was
validated end-to-end against real OCR artifacts (dry-run over an existing 2-page
output) and with targeted unit checks (normalization, sanitization, artigo_299
phrase detection, continuation rule, A→B→A grouping). The Tesseract fusion stage
was validated end-to-end offline over a real 1-page OCR output (69 boxes: 58
selected from PaddleOCR, 11 from Tesseract, 0 conflicts), plus unit checks for
the validator (direct/scaled/reject), the fusion rules (empty/tie/conflict), and
the no-Tesseract fallback. Tesseract 5.3.4 (`por`+`eng`) is present on this
machine. Neither stage has **yet been run in a full GPU batch** on pending
folders.

### v1 OCR — folders processed successfully (all `status: completed`, 0 failures)
| Folder | Files | Notes (source types exercised) |
|--------|-------|--------------------------------|
| `PI 283055` | 5/5 processed | all `original` PDFs |
| `PI 289211` | 4/4 processed | all `zip_extracted` (validates zip path) |
| `PI 293267` | 6/6 processed | mix of `original`, `converted` (.doc + .docx via LibreOffice) |

> **Note:** these outputs predate Option B and use the OLD flat layout
> (artifacts directly under `output/<folder>/<stem>/`, no `raw_` subfolder, no
> `documents/` or manifests). Per the segmentation decision, **segmentation runs
> only on new runs** (new files or `--overwrite`); these will not be
> retro-segmented automatically. Re-run with `--overwrite` to produce the new
> layout for them.

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

A fresh run on any pending folder will use the new `raw_` + segmentation layout.

### Logs present
`logs/PI_283055_03072026.log`, `logs/PI_289211_03072026.log`,
`logs/PI_293267_03072026.log`, `logs/_run_03072026.log` (bootstrap logger).

### Version control
The repo is now a git repository (was not in the 2026-07-03 briefing). The
segmentation and Tesseract-fusion modules plus config changes are currently
uncommitted/untracked working-tree changes.

---

## 3. Technical Stack (verified on this machine)

- **OS / HW:** Linux (Pop!_OS 24.04), NVIDIA **GeForce RTX 3060**.
- **Python:** 3.11.15 via conda env **`paddleocr`**
  (`/home/vini/miniconda3/envs/paddleocr/bin/python3`).
- **paddleocr `3.7.0`**, **paddlex `3.7.2`**, **paddlepaddle-gpu `3.3.0`**.
- **PyYAML** for config.
- **Tesseract fusion deps (pip):** `PyMuPDF` (fitz, PDF→image render),
  `pytesseract`, `opencv-python`, `Pillow`, `numpy` — all present in the env.
- **LibreOffice** present at `/usr/bin/soffice` and `/usr/bin/libreoffice`
  (system dependency for document conversion; not a pip package).
- **Tesseract OCR** `5.3.4` at `/usr/bin/tesseract`, languages `por`+`eng`
  (system dependency for the fusion stage; not a pip package). If missing, the
  fusion stage is skipped and the pipeline falls back to PaddleOCR-only.
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
├─ output/<folder>/                 # processing_summary.json (now incl. documents_detected + fusion stats)
│  └─ <file_stem>/                  # per-file output dir
│     ├─ raw_<file_stem>/           # NATIVE OCR artifacts (PaddleOCR save_all target)
│     ├─ page_images/page_XXX.png   # clean per-page render (Tesseract crop source; see §9)
│     ├─ tesseract/page_XXX/        # per-box crops + box_XXX.json + overall_ocr_res_tesseract.json
│     ├─ fusion/page_XXX_overall_ocr_res_fused.json  # PaddleOCR x Tesseract per-box fusion
│     ├─ documents/<NNN_tipo>/      # logical documents: pages/page_NNN/ + metadata + llm_ready
│     ├─ page_inventory.json        # raw artifacts grouped per page (+ fused_json/tesseract_json)
│     ├─ page_text_index.json       # per-page text (+ source + fusion_status per line)
│     ├─ page_classification.json   # per-page rule classification
│     ├─ document_groups.json       # page -> logical-document grouping
│     └─ source_manifest.json       # PRIMARY ADK entry point (+ ocr_sources/artifacts/fusion_summary)
├─ output_pdf/PI283055/             # OLD manual experiment — leave untouched, not part of spec
├─ logs/<folder>_<DDMMYYYY>.log     # one per analyzed folder + _run bootstrap log
├─ config/
│  ├─ config.yaml                   # general/default config (process_all_folders: true)
│  ├─ test_config.yaml              # single-folder test (target_folder: "PI 292174")
│  └─ document_rules.yaml           # NEW: YAML classification rules (thresholds + doc types)
├─ examples/                        # NEW: reference-example dirs per document type
│  ├─ nota_fiscal/  pedido_insercao/  autorizacao_veiculacao/
│  └─ comprovante_veiculacao/  artigo_299/   (each has a README.md placeholder)
├─ src/
│  ├─ __init__.py        exceptions.py     config.py
│  ├─ logger_setup.py    file_scanner.py   zip_handler.py
│  ├─ document_converter.py            ocr_runner.py     preprocessing.py
│  ├─ output_manager.py  summary_writer.py pipeline.py
│  ├─ document_rules.py                # NEW: rules loader/model
│  ├─ page_inventory.py  page_text_indexer.py  page_classifier.py   # NEW: segmentation stages
│  ├─ document_segmenter.py            document_artifact_organizer.py # NEW
│  ├─ llm_ready_writer.py              manifest_writer.py             # NEW
│  ├─ segmentation.py                  # NEW: post-OCR segmentation + fusion orchestrator
│  ├─ page_image_renderer.py  page_image_validator.py               # NEW: fusion stage (§9)
│  ├─ tesseract_box_extractor.py  box_cropper.py  tesseract_runner.py # NEW: fusion stage
│  ├─ tesseract_result_writer.py  ocr_fusion.py  fusion_summary_writer.py # NEW: fusion stage
│  └─ utils/
│     ├─ __init__.py  text_normalizer.py  path_utils.py              # NEW
├─ main.py               requirements.txt  README.md
├─ plans/ocr-pipeline-v1.md          # the original v1 implementation plan
└─ plans/plan_opcao_B.md             # the Option B segmentation plan (this stage)
```

### Module responsibilities (as built)
- **`config.py`** — `AppConfig` + `PaddleOcrConfig` dataclasses; `load_config()`
  (defaults ← YAML), `apply_overrides()` (← CLI), `validate()`. Path resolution
  helpers; `resolved_device()`; skip/overwrite mutual-exclusion check. **New:**
  `enable_segmentation` (default `True`) and `document_rules_path` (default
  `config/document_rules.yaml`) with `document_rules_path_resolved`; `validate()`
  now checks the rules file exists when segmentation is enabled. **Fusion:** new
  nested dataclasses `PageImagesConfig`, `TesseractConfig`, `FusionConfig` (each
  parsed from its own YAML section via `_sub_dataclass_from_dict`, which rejects
  unknown keys).
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
  pipeline rejects (version-tolerant); `run(input, output_dir)` iterates pages
  and `save_all()`. Unchanged behavior; callers now pass the `raw_<stem>` dir.
- **`output_manager.py`** — resolves `output/<folder>/<file_stem>/`; `should_process()`
  applies skip/overwrite; disambiguates stem collisions with a relative-path suffix.
  **New:** `raw_output_dir(output_dir)` → `<output_dir>/raw_<sanitized_stem>` and
  `documents_dir(output_dir)` → `<output_dir>/documents`.
- **`summary_writer.py`** — `FileRecord` + `FolderSummary`; computes totals and
  folder status (`completed` / `completed_with_errors` / `failed`); writes JSON.
  **New `FileRecord` fields:** `raw_output_folder`, `segmentation_status`
  (`ok`/`failed`/`skipped`), `segmentation_error`, `documents_detected` (list),
  plus fusion fields `tesseract_run`, `fusion_enabled`, `fusion_folder`,
  `fusion_stats` (per-file aggregate counters).
- **`preprocessing.py`** — no-op hooks (`preprocess_input`, `pdf_to_images`,
  `deskew`, `binarize`, `enhance_contrast`, `denoise`, `resize`, `select_pages`).
- **`pipeline.py`** — `process_folder(..., document_rules=None)` orchestrates
  scan → zip → per-item (convert → resolve output → skip/overwrite → preprocess →
  **OCR into `raw_<stem>/`** → **segmentation (incl. Tesseract fusion)**) with
  per-file `try/except` (traceback logged + recorded), then writes summary.
  `segment_file(...)` receives `ocr_input` (the file actually fed to PaddleOCR,
  post office-doc conversion) and the `page_images`/`tesseract`/`fusion` configs;
  its result (status/error/`documents_detected` + fusion stats) is attached to
  the `FileRecord`. Segmentation/fusion failures never flip OCR success.
- **`main.py`** — argparse (`--config`, `--target-folder`, `--process-all`,
  `--device`, `--overwrite`, `--skip-existing`, `--log-level`), builds one shared
  `OcrRunner`, **loads `document_rules` once** (when segmentation enabled) and
  passes them into `process_folder`, iterates folders; exit codes 0/1/2/3.

### New segmentation modules (Option B — see §8)
- **`document_rules.py`** — `load_document_rules()` parses/validates
  `config/document_rules.yaml` into `DocumentRules`/`DocumentType`/`Thresholds`
  dataclasses; terms are pre-normalized. Rules are **never hard-coded** in Python.
- **`utils/text_normalizer.py`** — `normalize()` (uppercase, strip accents,
  collapse whitespace, drop matching-irrelevant punctuation), `normalize_lines()`,
  `normalize_terms()`. Original text is preserved by callers.
- **`utils/path_utils.py`** — sanitized naming: `sanitize_component()` (preserves
  case), `sanitize_type()` (lower-cased), `raw_folder_name()`,
  `document_folder_name()` (`NNN_tipo`), `page_folder_name()` (`page_NNN`).
- **`page_inventory.py`** — `build_inventory()` scans `raw_<stem>/`, finds
  `*_res.json`, resolves each `page_index` (JSON field → filename `_N_` pattern →
  `unknown_page_index` + log), associates sibling artifacts → `page_inventory.json`.
- **`page_text_indexer.py`** — `build_text_index()` builds `raw_text_lines`,
  `normalized_text`, `low_confidence_lines` → `page_text_index.json`. **Now
  source-aware:** picks the best available source per page in priority order
  (1) fused JSON, (2) PaddleOCR `overall_ocr_res`, (3) Tesseract result,
  (4) `parsing_res_list`, (5) `table_res_list[].pred_html`, (6) markdown. Each
  line carries `source` and `fusion_status`. Defaults to PaddleOCR when no
  fusion is present (unchanged v1 behavior).
- **`page_classifier.py`** — `classify_pages()` scores each page against the
  rules (strong/weak term weights → `confidence = points/max_points`), applies
  thresholds (`classified`/`needs_review`/`unknown`) → `page_classification.json`.
- **`document_segmenter.py`** — `segment_documents()` applies the continuation
  rule and groups consecutive same-type pages into logical documents; `unknown`
  pages become their own documents; type changes start a new document →
  `document_groups.json`.
- **`document_artifact_organizer.py`** — `organize_documents()` creates
  `documents/<NNN_tipo>/pages/page_NNN/` and **copies** (never moves) per-page
  artifacts (`*_res.json`, `*.md`, layout/overall images, tables, `.docx`).
- **`llm_ready_writer.py`** — `write_document_files()` writes per-document
  `document_metadata.json` and `llm_ready.json` (text lines, artifact refs
  relative to the doc folder, `instructions_for_llm`, matched terms).
- **`manifest_writer.py`** — `write_source_manifest()` writes the per-file
  `source_manifest.json` (the primary ADK entry point). **New optional kwargs:**
  `ocr_sources` (primary/secondary/fusion_enabled/fusion_strategy), `artifacts`
  (raw/page_images/tesseract/fusion folders), and `fusion_summary`.
- **`segmentation.py`** — `segment_file()` orchestrates inventory →
  **[Tesseract fusion]** → text index → classify → segment → organize →
  metadata/llm_ready → manifest; fully defensive (never raises to the caller);
  returns a `SegmentationResult` (now including fusion flags/folders/summary).
  The fusion stage (`_run_fusion_stage`) runs after `build_inventory` and before
  `build_text_index`, annotating each inventory page with `fused_json` /
  `tesseract_json`; it is skipped entirely when disabled/unavailable.

### New Tesseract-fusion modules (see §9)
- **`page_image_renderer.py`** — `render_pages()` renders clean
  `page_images/page_XXX.png` from the file actually fed to OCR (`ocr_input`,
  fallback: `_res.json` `input_path`); PDFs render at the exact `_res.json`
  width/height when known (via PyMuPDF); images normalized as `page_000`. Never
  reads PaddleOCR diagnostic/annotated artifacts. Per-page failure isolation.
- **`page_image_validator.py`** — `validate_base_image()` returns
  `direct`/`scaled`/`reject`; rejects likely diagnostic panels
  (`img_w >= json_w * suspicious_panel_ratio`, default 2.5) with a clear message.
- **`tesseract_box_extractor.py`** — `extract_boxes()` reads PaddleOCR
  `overall_ocr_res.{rec_texts,rec_scores,rec_boxes,rec_polys}`, preserving
  `box_index` and page dimensions.
- **`box_cropper.py`** — `crop_boxes()` crops each `rec_box` from the clean page
  image (padding 0 by default; only >0 with `allow_padding_for_debug`), applies
  scale when `mode=="scaled"`, clamps to bounds; saves `tesseract/page_XXX/box_XXX.png`.
- **`tesseract_runner.py`** — thin `pytesseract` wrapper; `is_available()`
  (cached) and `ocr_crop()` (normalizes confidence 0-100 → 0.0-1.0; `-1`/invalid
  → 0.0; never raises).
- **`tesseract_result_writer.py`** — `process_page()` ties extractor → cropper →
  runner and writes `overall_ocr_res_tesseract.json` (preserving PaddleOCR
  boxes/polys) plus optional per-box `box_XXX.json`.
- **`ocr_fusion.py`** — `fuse_page()` selects the higher-confidence string per
  box (rules: no empty; empty→other; tie<`tie_margin`→`tie_breaker`; divergent
  text + close confidence → `conflict_needs_review`); keeps alternatives in
  `fusion_items`; writes `fusion/page_XXX_overall_ocr_res_fused.json`.
- **`fusion_summary_writer.py`** — `aggregate_fusion_summary()` sums per-page
  counters (total/selected_from_*/conflicts/empty_*/pages_processed/failed).

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

### Option B segmentation decisions (2026-07-06)
11. **Raw artifacts isolated in `raw_<stem>/`:** OCR `save_all()` now targets
    `output/<folder>/<stem>/raw_<stem>/`, keeping native artifacts separate from
    pipeline-organized outputs. The raw folder is treated as read-only by the
    segmentation stage.
12. **Minimal unit = page; no intra-page splitting** in this version.
13. **Rule-based classification driven by YAML** (`config/document_rules.yaml`),
    never hard-coded. Types: `nota_fiscal`, `pedido_insercao`,
    `autorizacao_veiculacao`, `comprovante_veiculacao`, `artigo_299`, `unknown`.
    `artigo_299` scores declaratory phrases (e.g. "DECLARO SOB AS PENAS DA LEI"),
    not only the title.
14. **Continuation rule:** a low-confidence page following a classified document,
    with no strong evidence of another type, weak-term compatibility, and
    `confidence >= continuation_min_confidence`, attaches to the previous document
    and marks the whole document `needs_review`.
15. **Grouping semantics:** consecutive same-type pages form one document; a type
    change starts a new document (so A→B→A yields three documents); `unknown`
    pages are their own documents.
16. **Artifacts are copied, not moved** into `documents/`, preserving `raw_`.
17. **`needs_review` documents are still produced**, only flagged.
18. **Segmentation runs on new runs only:** existing flat outputs are not
    retro-segmented; use `--overwrite` (or new files) to get the new layout.
19. **`source_manifest.json` is the primary ADK entry point;** `processing_summary.json`
    additionally carries `documents_detected` per file.
20. **Failure isolation extended to segmentation:** a segmentation failure is
    logged + recorded (`segmentation_status=failed`) without failing OCR or the batch.

### PaddleOCR + Tesseract fusion decisions (2026-07-06)
21. **Render source = the file actually fed to OCR (`ocr_input`):** the converted
    PDF for office docs, or the original image/PDF; fallback to the `_res.json`
    `input_path`. Never a PaddleOCR diagnostic/annotated artifact.
22. **Clean per-page image is the ONLY crop source** (`page_images/page_XXX.png`).
    Explicitly forbidden as crop sources: `*_preprocessed_img.png` (triple
    panel), `*_overall_ocr_res.png`, `*_layout_det_res.png`,
    `*_layout_order_res.png`, `*_region_det_res.png`, `*_table_cell_img.png`.
23. **Render to the exact `_res.json` width/height** when known (no box scaling
    needed); `render_dpi` is only a fallback. Size mismatch → scale boxes;
    suspicious panel (`img_w >= json_w * 2.5`) → reject that page's crops.
24. **Fusion orchestrated inside `segment_file`**, after inventory and before the
    text index; per-page and per-box failure isolation; a global try/except
    guards the whole stage.
25. **Fusion selection = highest normalized confidence per box**; empties never
    win; ties within `tie_margin` go to `tie_breaker` (default `paddleocr`);
    divergent text with close confidence is flagged `conflict_needs_review` but a
    string is still chosen. `rec_texts` holds only the selected string;
    alternatives + metadata live in `fusion_items`. Box geometry always comes
    from PaddleOCR (Tesseract OCRs those exact regions).
26. **No-Tesseract / disabled fallback = PaddleOCR-only, zero regression:** no
    `page_images/`, `tesseract/`, or `fusion/` folders are created; the text
    index uses PaddleOCR; `source_manifest.json` records `fusion_enabled=false`.
27. **`page_text_indexer` prefers the fused result** as its primary source (see
    the 6-level priority in §9), tagging each line with `source`/`fusion_status`.

---

## 6. Pending Tasks / Next Steps

- **Run a full GPU batch with segmentation + fusion enabled.** Both stages have
  been validated by dry-run + unit checks only; they have not yet run through a
  live GPU OCR + segmentation + Tesseract-fusion batch. Process the pending
  folders (`PI 289211 - TV CARTOON NETWORK`, `PI 292174`, `PI 292850 - RÁDIO
  VERTSUL FM 93,5`, `PI 294215 - TV REDE ESTAÇÃO`, `PI 294225 - TV MILL`,
  `PI 295870 - CATURITÉ AM`) via a full run (`--process-all` / `config.yaml`) and
  inspect the generated manifests, `page_images/`, `tesseract/`, and `fusion/`.
- **Tune the fusion parameters** (`tie_margin`, `tie_breaker`, `psm`/`oem`,
  `language`, `conflict_text_similarity_max`) against real multi-document files;
  check the `fusion_summary` counters and per-box conflicts.
- **Re-run already-processed folders with `--overwrite`** to migrate their OLD
  flat outputs to the new `raw_` + `documents/` + manifest layout (they are not
  retro-segmented automatically).
- **Tune `config/document_rules.yaml`** using real outputs and the `examples/`
  reference dirs; the v1 scoring is intentionally simple/explainable and expected
  to need threshold/term tuning per document type.
- **Commit the segmentation + fusion work.** New/modified files are currently
  uncommitted working-tree changes (repo is now under git).
- **Investigate potential duplicate/confusing input state:** `inputs/checking/`
  contains both `PI 289211` and `PI 289211 - TV CARTOON NETWORK`. The processed
  `PI 289211` was fed from a zip; confirm whether these are duplicates or distinct
  jobs before a full batch run, to avoid redundant processing.
- **Optional cleanup:** `output_pdf/` and the `misc/GPT_CHECKING-*` artifacts are
  pre-existing/manual and outside the spec; decide whether to keep or remove.
- **Future (explicitly deferred):** implement preprocessing hooks (PDF→image,
  deskew, binarization, contrast, denoise, page selection, pipeline fallback);
  populate `examples/` with real reference samples; build the actual ADK agent
  that consumes `source_manifest.json` / `llm_ready.json`; consider an optional
  standalone entrypoint to segment already-OCR'd folders without re-running OCR.
- **Optional hardening:** automated tests around scanner/zip/summary logic and
  the new segmentation stages; potential concurrency for LibreOffice conversion
  vs. OCR.

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
- **Tesseract must be on PATH** for the fusion stage (`tesseract` binary +
  `por`/`eng` language packs). If absent (or `tesseract.enabled`/`fusion.enabled`
  is false), the fusion stage is skipped and the pipeline uses PaddleOCR-only —
  no batch failure, no fusion artifacts.
- **First OCR run downloads models** (adds minutes); subsequent runs are fast
  (observed: ~1.3–3s per typical page; a 154s file was a large multi-page PDF).
- **Do not blindly assume PaddleOCR API signatures** if the installed version
  changes; the runner already tolerates unknown kwargs, but verify behavior.
- **Skip-existing is default:** re-running a completed folder will skip files
  whose output dir already exists/non-empty. Use `--overwrite` to force
  reprocessing. **Segmentation runs only when OCR runs for a file**, so skipped
  files are neither re-OCR'd nor (re-)segmented; `--overwrite` triggers both.
- **Segmentation is config-gated:** `enable_segmentation` (default `true`) and
  `document_rules_path` in the YAML config. If the rules file is missing while
  segmentation is enabled, `config.validate()` fails fast with a `ConfigError`.
- **Fusion is config-gated** via three YAML sections: `page_images`, `tesseract`,
  `fusion` (all `enabled: true` by default). Fusion runs only when all three are
  enabled AND the Tesseract binary is available; otherwise it is skipped
  silently (logged) with PaddleOCR-only fallback. Unknown keys in these sections
  raise a `ConfigError`.
- **Generated folder/file names are sanitized** (`NF 321` → `NF_321`, `raw_NF_321`;
  document-type folders lower-cased, e.g. `001_nota_fiscal`). Input-folder names
  on disk keep their spaces/accents as before (only the file stem is sanitized).

### How to run (reference)
```bash
python main.py --config config/test_config.yaml            # single folder (test)
python main.py --target-folder "PI 292174"                 # single folder via CLI
python main.py --config config/config.yaml                 # all folders
python main.py --target-folder "PI 293267" --device cpu --overwrite
```

### Key file references
- Orchestration entry: `main.py:main()` (loads `document_rules` once)
- Per-folder flow: `src/pipeline.py:process_folder()` and `_process_item()`
  (OCR into `raw_<stem>/` then `segment_file(...)`)
- OCR isolation: `src/ocr_runner.py:OcrRunner.run()` (`save_all`)
- Summary schema: `src/summary_writer.py` (`FolderSummary.to_dict()`,
  `FileRecord` now includes `documents_detected` / `raw_output_folder`)
- Segmentation + fusion orchestrator: `src/segmentation.py:segment_file()` and
  `_run_fusion_stage()`
- Rules loader/model: `src/document_rules.py:load_document_rules()`
- Rules config: `config/document_rules.yaml`
- Fusion entry points: `src/ocr_fusion.py:fuse_page()`,
  `src/page_image_renderer.py:render_pages()`,
  `src/tesseract_result_writer.py:process_page()`
- Fusion config sections: `config/config.yaml` → `page_images` / `tesseract` /
  `fusion`
- Original v1 implementation plan: `plans/ocr-pipeline-v1.md`
- Option B segmentation plan: `plans/plan_opcao_B.md`
- Tesseract fusion plan: `.kilo/plans/1783345614893-paddleocr-tesseract-fusion.md`

---

## 8. Option B — Post-OCR Logical-Document Segmentation

**Goal:** a single input file may contain several logical documents on different
pages (e.g. a nota fiscal followed by a comprovante de veiculação). After OCR,
the pipeline classifies each page and regroups them into logical documents,
producing an ADK-ready package per file. Reference: `plans/plan_opcao_B.md`.

### Data flow (per file, after OCR)
```
original file → PaddleOCR → raw_<stem>/ (native artifacts)
  → page_inventory.py      → page_inventory.json      (artifacts grouped per page)
  → page_text_indexer.py   → page_text_index.json     (text + normalized + low-conf)
  → page_classifier.py     → page_classification.json (rule scores + thresholds)
  → document_segmenter.py  → document_groups.json     (continuation + grouping)
  → document_artifact_organizer.py → documents/<NNN_tipo>/pages/page_NNN/ (COPIES)
  → llm_ready_writer.py    → documents/<NNN_tipo>/{document_metadata.json, llm_ready.json}
  → manifest_writer.py     → source_manifest.json     (PRIMARY ADK entry point)
```

### Output layout (per file)
```
output/<folder>/<file_stem>/
├─ raw_<file_stem>/                 # native OCR artifacts (untouched)
├─ documents/
│  ├─ 001_nota_fiscal/
│  │  ├─ pages/page_000/            # copied artifacts for that page
│  │  ├─ document_metadata.json
│  │  └─ llm_ready.json
│  └─ 002_comprovante_veiculacao/ ...
├─ page_inventory.json
├─ page_text_index.json
├─ page_classification.json
├─ document_groups.json
└─ source_manifest.json
```

### Classification model (v1, explainable)
- Terms live in `config/document_rules.yaml` under each type's `strong_terms` /
  `weak_terms`, plus global `weights` and `thresholds`.
- Score: `points = strong_hits*weights.strong + weak_hits*weights.weak`;
  `confidence = points / max_points_for_type`. Best-scoring type wins.
- Thresholds: `>= classified` → `classified`; `>= needs_review` → `needs_review`;
  `< unknown_below` → `unknown`. `continuation_min_confidence` gates continuation.
- Matching is done on normalized text (uppercase, accent-free, punctuation
  collapsed), so YAML terms are written without accents on purpose.

### Verified facts (PaddleOCR 3.7.0 `_res.json`)
- Top-level `page_index` is present and reliable (primary source for indexing).
- Text is under `overall_ocr_res.{rec_texts, rec_scores, rec_boxes}`.
- Artifacts are named `<stem>_<page_index><suffix>` (e.g. `NF 321_0_res.json`,
  `NF 321_0.md`, `NF 321_0_table_1.html`), which the inventory relies on.

### Validation performed (no full GPU batch yet)
- `python -m py_compile` on all modules: OK.
- Dry-run of `segment_file()` over a real 2-page OCR output: all five per-file
  JSONs generated, `documents/` populated with COPIED artifacts (raw left
  intact), `unknown` page became its own document, `needs_review` flagged.
- Unit checks: sanitization/normalization rules, `artigo_299` phrase detection,
  continuation rule, and A→B→A grouping (three documents) all pass.

### Known limitations / follow-ups
- v1 scoring is deliberately simple and will need per-type tuning against real
  data; some generic terms (e.g. `VALOR`, `DATA`) appear across multiple types.
- `examples/` dirs are placeholders (README only) pending real samples.
- Existing pre-Option-B outputs remain in the old flat layout until re-run with
  `--overwrite`.

---

## 9. PaddleOCR + Tesseract Per-Box OCR Fusion

**Goal:** improve OCR quality by cross-checking every PaddleOCR box against a
Tesseract pass on the same region, then keeping, per box, whichever string has
the higher confidence. Reference plan:
`.kilo/plans/1783345614893-paddleocr-tesseract-fusion.md`.

### Data flow (per file, between inventory and text index)
```
raw_<stem>/*_res.json (PaddleOCR boxes)
  → page_image_renderer  → page_images/page_XXX.png     (clean render from ocr_input)
  → page_image_validator → direct | scaled | reject     (vs *_res.json width/height)
  → tesseract_box_extractor → PaddleOCR boxes (index-preserving)
  → box_cropper          → tesseract/page_XXX/box_XXX.png (exact crops)
  → tesseract_runner     → per-box text + confidence (0..1)
  → tesseract_result_writer → tesseract/page_XXX/overall_ocr_res_tesseract.json
  → ocr_fusion           → fusion/page_XXX_overall_ocr_res_fused.json
  → (inventory annotated with fused_json/tesseract_json; page_text_indexer prefers fused)
```

### `page_text_indexer` source priority (per page)
1. `fusion/page_XXX_overall_ocr_res_fused.json` (fused)
2. PaddleOCR `overall_ocr_res` (from `*_res.json`) — default when no fusion
3. Tesseract `overall_ocr_res_tesseract.json`
4. `parsing_res_list` (from `*_res.json`)
5. `table_res_list[].pred_html`
6. the page's markdown file

Each emitted line carries `source`
(`fused`/`paddleocr`/`tesseract`/`parsing`/`table`/`markdown`) and
`fusion_status` (`selected`/`conflict_needs_review`/`both_empty`/`null`).

### Config (defaults; `config/config.yaml` + `config/test_config.yaml`)
```yaml
page_images:
  enabled: true
  output_folder_name: "page_images"
  render_dpi: 300                      # fallback; render targets *_res.json size
  image_format: "png"
  validate_against_paddle_json_size: true
  allow_box_scaling: true
  suspicious_panel_ratio: 2.5          # reject base images wider than json_w * ratio
tesseract:
  enabled: true
  executable_path: "tesseract"
  language: "por+eng"
  psm: 7
  oem: 1
  timeout_seconds_per_box: 10
  save_box_crops: true
  save_box_json: true
  crop_padding_px: 0                    # exact crop by default
  allow_padding_for_debug: false
fusion:
  enabled: true
  strategy: "select_highest_confidence_per_box"
  tie_margin: 0.03
  tie_breaker: "paddleocr"
  keep_alternatives: true
  mark_conflicts: true
  conflict_text_similarity_max: 0.6
```

### Fusion selection rules (`ocr_fusion._choose`)
1. Never select an empty string when the other source has text.
2. If one source is empty, select the other.
3. Otherwise select the higher normalized confidence.
4. If the confidence gap `< tie_margin`, select `tie_breaker` (default PaddleOCR).
5. If selected texts are dissimilar (`similarity <= conflict_text_similarity_max`,
   via `difflib`) while confidences are close, flag `conflict_needs_review` — a
   string is still chosen.
6. Alternatives (both sources' text/confidence) are preserved in `fusion_items`.

### Key JSON shapes
- `overall_ocr_res_tesseract.json`: `input_source="tesseract_box_ocr"`,
  `based_on{paddle_res_json,image_source}`, `rec_texts/rec_scores/rec_boxes/rec_polys`
  (boxes/polys copied from PaddleOCR), `box_results[{box_index,text,confidence,
  status,crop_path}]`.
- `..._fused.json`: `input_source="paddleocr_tesseract_fusion"`,
  `fusion_strategy`, `sources{paddleocr,tesseract}`, `rec_texts` (selected only),
  `rec_scores/rec_boxes/rec_polys`, `selected_sources[]`, `fusion_items[{box_index,
  bbox,selected_source,selected_text,selected_confidence,status,paddleocr{...},
  tesseract{...}}]`.
- `source_manifest.json` gains `ocr_sources`, `artifacts`, `fusion_summary`.
- `processing_summary.json` `FileRecord` gains `tesseract_run`, `fusion_enabled`,
  `fusion_folder`, `fusion_stats`.

### Validation performed (no full GPU batch yet)
- `python -m py_compile` on all new/changed modules + `main.py`: OK.
- End-to-end offline `segment_file()` over a real 1-page OCR output (RPS, 69
  boxes): clean render matched `_res.json` size exactly (1191x1685, `direct`
  mode, no scaling); Tesseract OCR'd all 69 boxes; fusion selected 58 from
  PaddleOCR + 11 from Tesseract, 0 conflicts; `page_text_index.json` used the
  fused source; `source_manifest.json` carried `ocr_sources`/`artifacts`/
  `fusion_summary`; `page_inventory.json` persisted `fused_json`/`tesseract_json`.
- Unit checks: validator (direct / scaled / reject-with-exact-message /
  scaling-disabled), fusion rules (empty→other, tie→tie_breaker, out-of-tie→max
  confidence, divergent+close→`conflict_needs_review`, both-empty safe).
- No-Tesseract fallback: pointing `executable_path` at a missing binary skipped
  the stage cleanly — no `page_images/`/`tesseract/`/`fusion/` created,
  `fusion_enabled=false`, text index = PaddleOCR only.

### Known limitations / follow-ups
- Not yet run in a live GPU batch; fusion parameters (`tie_margin`, `psm`/`oem`,
  `language`, `conflict_text_similarity_max`) are untuned against real data.
- Sequential (no per-box/page parallelism); Tesseract-per-box adds wall time on
  pages with many boxes.
- Single-image sources (PNG/JPG) render as `page_000` and are treated as a
  `direct` match (no `_res.json` size to validate against).
