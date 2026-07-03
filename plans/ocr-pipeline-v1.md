# Plano — Rotina de OCRização com PaddleOCR (v1)

## 1. Contexto verificado do ambiente

Levantamento feito no ambiente real (não são suposições):

| Item | Valor confirmado |
|------|------------------|
| Python | 3.11.15 (conda env `paddleocr`) |
| paddleocr | **3.7.0** |
| paddlex | 3.7.2 |
| paddlepaddle-gpu | 3.3.0 |
| GPU | NVIDIA RTX 3060 (driver 580.119.02) |
| LibreOffice | `/usr/bin/soffice` e `/usr/bin/libreoffice` disponíveis |
| Pipelines disponíveis | `PPStructureV3`, `PaddleOCRVL`, `PaddleOCR`, `PPChatOCRv4Doc`, etc. |

> Nota: o usuário pediu para salvar em `plans/`, mas as permissões deste ambiente só liberam
> escrita de planos em caminhos específicos. Este plano foi salvo em
> `~/.local/share/kilo/plans/ocr-pipeline-v1.md`. Posso copiá-lo para `PaddleOCR/plans/`
> ao entrar no modo Code.

### API confirmada do PaddleOCR 3.7.0
- **`PPStructureV3`** é o pipeline correto para **análise de layout** (layout detection + OCR + tabelas + fórmulas + selos). É o que gera exatamente o output nativo já visto em `output_pdf/PI283055/`:
  - `<nome>_<page>_res.json`, `<nome>_<page>.md`, `<nome>_<page>_layout_det_res.png`, pasta `imgs/`.
- Construtor aceita `device` via kwargs (`device="gpu:0"` ou `"cpu"`); por padrão usa GPU 0 se disponível, senão CPU.
- Aceita `lang`, `ocr_version`, e flags `use_doc_orientation_classify`, `use_doc_unwarping`, `use_textline_orientation`, `use_seal_recognition`, `use_table_recognition`, `use_formula_recognition`, `use_chart_recognition`, `use_region_detection`, `enable_hpi`, `precision`, `cpu_threads`.
- `.predict(input)` retorna um **iterável** de resultados (um por página do PDF/imagem).
- Cada resultado tem `save_all(save_path=...)`, `save_to_json`, `save_to_img`, e o dict `res`. Usaremos `save_all` para preservar TODOS os artefatos nativos.
- `PaddleOCRVL` também existe (pipeline VL) — a camada de OCR será desenhada para permitir troca de pipeline via config (`pipeline: "PPStructureV3" | "PaddleOCRVL"`), mas **v1 usa PPStructureV3** como default.
- `PDF multipágina`: o `.predict` já itera páginas; salvaremos todas no mesmo diretório do arquivo (comportamento nativo), consolidando por arquivo.

### Conversão de documentos
- `doc2md_supported_formats()` = `['docx','pptx','xlsx']` — **não** cobre `.doc` nem `.odt`, e gera markdown (não passa por layout OCR). Portanto **não** usaremos `doc2md`.
- Estratégia v1: **LibreOffice headless** (`soffice --headless --convert-to pdf`) para `.doc/.docx/.odt` → PDF, salvando em `misc/converted/...`, depois OCR do PDF. Camada isolada (`document_converter.py`) para trocar a estratégia no futuro.

### Observações sobre o estado atual
- `main.py` está vazio (0 bytes).
- Já existem PDFs reais em `inputs/checking/` sob pastas com **espaços e acentos** (ex.: `PI 295870 - CATURITÉ AM`, `PI 292850 - RÁDIO VERTSUL FM 93,5`). Há também `.doc`/`.docx` reais em `PI 293267`. Há um arquivo solto `inputs/testocr.png` (fora de `checking/`, será ignorado — correto).
- `misc/` já contém um zip e uma extração manual — nossa rotina usará subpastas próprias (`misc/extracted/`, `misc/converted/`) para não colidir.
- `output_pdf/PI283055/` é um teste manual anterior; **não** será tocado. O output oficial vai para `output/`.
- **Importante:** nomes de pasta com espaços exigem lidar com `target_folder` que contém espaços; o slug de output preservará o nome original da pasta de input conforme requisito 12.

---

## 2. Estrutura final de arquivos a criar

```
PaddleOCR/
├─ inputs/checking/            (já existe)
├─ misc/
│  ├─ extracted/               (criado em runtime)
│  └─ converted/               (criado em runtime)
├─ output/                     (já existe)
├─ logs/                       (criado em runtime)
├─ config/
│  ├─ config.yaml              (config geral / default)
│  └─ test_config.yaml         (config de teste com target_folder)
├─ src/
│  ├─ __init__.py
│  ├─ config.py                (dataclass + loader/validador YAML)
│  ├─ logger_setup.py          (logger por pasta em logs/)
│  ├─ file_scanner.py          (descoberta de subpastas e arquivos)
│  ├─ zip_handler.py           (extração controlada de .zip)
│  ├─ document_converter.py    (doc/docx/odt -> pdf via LibreOffice)
│  ├─ ocr_runner.py            (wrapper PPStructureV3 / troca de pipeline)
│  ├─ output_manager.py        (paths de output, skip/overwrite)
│  ├─ summary_writer.py        (processing_summary.json)
│  ├─ preprocessing.py         (pontos de extensão vazios p/ PDF)
│  ├─ pipeline.py              (orquestrador de uma pasta)
│  └─ exceptions.py            (exceções específicas)
├─ main.py                     (entry point + argparse)
├─ requirements.txt
└─ README.md
```

`output_pdf/` existente será deixado intacto (não faz parte da spec).

---

## 3. Responsabilidade de cada módulo

- **`config.py`**: `@dataclass AppConfig` com todos os campos configuráveis (paths, `use_gpu`, `device`, `accepted_extensions`, `skip_existing`, `overwrite_existing`, `target_folder`, `process_all_folders`, `log_level`, e um sub-bloco `paddleocr` com `pipeline`, `lang`, `ocr_version`, flags de layout/tabela/fórmula/selo/orientação, `enable_hpi`, `cpu_threads`). Funções `load_config(path)` (lê YAML) e `validate()` (checa existência de paths, coerência gpu/device, extensões válidas). Defaults sensatos para rodar sem YAML. CLI sobrepõe YAML.
- **`logger_setup.py`**: `get_folder_logger(folder_name, logs_root, level)` → cria `logs/<folder>_<DDMMYYYY>.log`, handler de arquivo + console opcional. Retorna logger isolado por pasta. Formata com timestamp, nível, módulo.
- **`file_scanner.py`**:
  - `list_target_subfolders(input_root, target_folder, process_all)` → lista subpastas diretas de `inputs/checking/` (ou só a `target_folder`); erro claro se `target_folder` não existir.
  - `scan_folder(subfolder, accepted_exts)` → varre **recursivamente**, retorna listas: `supported_files`, `zip_files`, `unsupported_files` (para log). Arquivos soltos direto em `checking/` nunca entram.
- **`zip_handler.py`**: `extract_zip(zip_path, dest_root, folder_name, logger)` → extrai para `misc/extracted/<folder_name>/<zip_stem>/`, evita sobrescrever (limpa/recria de forma controlada), varre recursivamente arquivos internos (inclui subpastas internas), retorna metadados de origem (`zip_source`). Zips aninhados: tratados recursivamente.
- **`document_converter.py`**: `DocumentConverter` com estratégia `LibreOfficeConverter`. `convert_to_pdf(src, dest_root, folder_name, logger)` → `misc/converted/<folder_name>/<file_stem>/<file>.pdf` via `soffice --headless --convert-to pdf --outdir`. Detecta ausência do LibreOffice e levanta `ConversionError` detalhado (arquivo continua registrado como failed, execução segue). Interface preparada para trocar estratégia.
- **`ocr_runner.py`**: `OcrRunner` — instancia o pipeline **uma vez** (custo alto) a partir da config; método `run(input_path, output_dir)` chama `.predict()`, itera resultados e chama `save_all(save_path=output_dir)` para cada, preservando artefatos nativos. `_build_pipeline()` seleciona `PPStructureV3` (default) ou `PaddleOCRVL` conforme `config.paddleocr.pipeline`. Isola 100% das chamadas ao PaddleOCR.
- **`output_manager.py`**: monta `output/<folder>/<file_stem>/`; `should_process(output_dir)` aplica `skip_existing`/`overwrite_existing` (skip se pasta existe e tem conteúdo; overwrite limpa antes). Colisão de nomes de arquivo iguais em subpastas diferentes: resolvida com sufixo derivado do caminho relativo para não sobrescrever.
- **`summary_writer.py`**: acumula `FileRecord`s e escreve `output/<folder>/processing_summary.json` no formato exato da spec (com `source_type`, `zip_source`, `converted_file_path`, timings, status, error). Status da pasta: `completed` / `completed_with_errors` / `failed`.
- **`preprocessing.py`**: hooks vazios documentados (`preprocess_pdf`, `pdf_to_images`, `deskew`, `binarize`, etc.) retornando o input inalterado — pontos de extensão, sem implementação (conforme requisito).
- **`pipeline.py`**: `process_folder(subfolder, config, ...)` orquestra: logger → scan → para cada arquivo (converter se doc/odt, extrair se zip, checar skip/overwrite, rodar OCR, medir tempo, registrar) com `try/except` por arquivo (traceback completo no log + record de erro), sem abortar. Escreve summary no fim.
- **`exceptions.py`**: `OcrPipelineError`, `ConversionError`, `ExtractionError`, `TargetFolderNotFound`, `UnsupportedFormatError`.
- **`main.py`**: argparse (`--config`, `--target-folder`, `--process-all`, `--device`, `--overwrite`, `--log-level`), carrega config, aplica overrides de CLI, resolve subpastas, chama `process_folder` para cada uma. Sem lógica de negócio.

---

## 4. Fluxo de execução (por pasta)

1. Cria logger `logs/<folder>_<DDMMYYYY>.log`; loga início + config usada.
2. `file_scanner.scan_folder` (recursivo) → supported / zip / unsupported.
3. Para cada zip: `zip_handler.extract_zip` → adiciona arquivos suportados extraídos à fila (com `source_type=zip_extracted`, `zip_source=<zip>`), loga ignorados.
4. Para cada arquivo da fila:
   - Se `.doc/.docx/.odt` → `document_converter` → PDF em `misc/converted/...` (`source_type=converted`); falha → record failed, continua.
   - Define `output/<folder>/<file_stem>/`; aplica skip/overwrite.
   - `OcrRunner.run(pdf_or_image, output_dir)` (PDFs vão direto; imagens direto).
   - Mede `duration_seconds`, registra started/finished, status.
   - `except Exception` → loga traceback completo, record `failed`, segue.
5. `summary_writer` grava `processing_summary.json`.
6. Loga fim + tempo total da pasta.

---

## 5. Exemplos de configuração

### `config/config.yaml` (geral)
```yaml
input_root: "inputs/checking"
output_root: "output"
logs_root: "logs"
misc_root: "misc"
extracted_subdir: "extracted"
converted_subdir: "converted"
target_folder: null
process_all_folders: true
skip_existing: true
overwrite_existing: false
use_gpu: true
device: "gpu:0"
log_level: "INFO"
log_to_console: true
accepted_extensions: [".png", ".jpg", ".jpeg", ".pdf", ".doc", ".docx", ".odt"]
document_convert_extensions: [".doc", ".docx", ".odt"]
paddleocr:
  pipeline: "PPStructureV3"
  lang: null
  ocr_version: null
  use_doc_orientation_classify: true
  use_doc_unwarping: false
  use_textline_orientation: true
  use_table_recognition: true
  use_formula_recognition: true
  use_seal_recognition: true
  use_chart_recognition: false
  enable_hpi: false
  cpu_threads: 8
```

### `config/test_config.yaml` (teste — uma pasta)
```yaml
input_root: "inputs/checking"
output_root: "output"
logs_root: "logs"
misc_root: "misc"
target_folder: "PI 292174"
process_all_folders: false
skip_existing: true
overwrite_existing: false
use_gpu: true
device: "gpu:0"
accepted_extensions: [".png", ".jpg", ".jpeg", ".pdf", ".doc", ".docx", ".odt"]
paddleocr:
  pipeline: "PPStructureV3"
```

---

## 6. Formas de execução

```bash
# Todas as pastas (usa config.yaml)
python main.py

# Só uma pasta via CLI (note as aspas por causa do espaço)
python main.py --target-folder "PI 292174"

# Via config de teste
python main.py --config config/test_config.yaml

# Forçar CPU e overwrite
python main.py --target-folder "PI 293267" --device cpu --overwrite
```

Regras seletivas: target definido → só ela; sem target + `process_all=true` → todas; target inexistente → erro claro (`TargetFolderNotFound`) e encerra; nunca processa arquivos soltos direto em `checking/`.

---

## 7. Exemplo de output esperado

```
output/
└─ PI 292174/
   ├─ processing_summary.json
   ├─ RPS_20240630_1432882_3194576_300000002242781/
   │  ├─ ..._0_res.json
   │  ├─ ..._0.md
   │  ├─ ..._0_layout_det_res.png
   │  └─ imgs/...
   └─ NFSe_00374020_01100653/
      └─ ...
```
`processing_summary.json` no formato exato da spec (input_folder, output_folder, log_file, execution_datetime, status, totais, lista `files` com source_type/zip_source/converted_file_path/timings/error).

---

## 8. requirements.txt (documental)
```
paddleocr==3.7.0
paddlepaddle-gpu==3.3.0   # CPU: paddlepaddle
PyYAML
```
(LibreOffice é dependência de sistema, não pip — documentada no README.)

---

## 9. Riscos / decisões

- **`device`**: passado como kwarg ao pipeline; se `use_gpu=false` → `"cpu"`. Auto-detecção nativa como fallback.
- **Custo de inicialização do pipeline**: instanciado 1x por execução e reutilizado entre pastas/arquivos.
- **Nomes com espaço/acento**: paths tratados com `pathlib`; CLI exige aspas.
- **`.doc`/`.odt`**: dependem 100% do LibreOffice; ausência → erro detalhado por arquivo, sem abortar.
- **Colisão de nomes** em subpastas → sufixo por caminho relativo.
- **Não implementado nesta v1** (só hooks): pré-processamento de PDF/imagem, integração ADK (apenas o JSON já é ADK-friendly).

---

## 10. Ordem de implementação (após trocar para modo Code)

1. `src/__init__.py`, `exceptions.py`, `config.py`
2. `config/config.yaml`, `config/test_config.yaml`
3. `logger_setup.py`
4. `file_scanner.py`, `zip_handler.py`
5. `document_converter.py`
6. `ocr_runner.py`, `preprocessing.py`
7. `output_manager.py`, `summary_writer.py`
8. `pipeline.py`
9. `main.py`
10. `requirements.txt`, `README.md`
11. Smoke test: `python main.py --config config/test_config.yaml` numa pasta pequena.
