Manter o PaddleOCR rodando sobre o arquivo completo, mas salvar o output bruto em uma pasta própria e, em seguida, criar uma camada de organização por documentos lógicos.

Fluxo final:

Arquivo original completo
→ PaddleOCR completo
→ output bruto em raw_<arquivo>
→ leitura dos JSONs por página
→ classificação de cada página por regras YAML
→ agrupamento em documentos lógicos
→ cópia dos artefatos de cada página para documents/
→ geração de manifestos e llm_ready.json
Estrutura final esperada

Para um arquivo:

inputs/checking/PI293267/NF 321.pdf

O output deve ficar assim:

output/PI293267/NF_321/
├─ raw_NF_321/
│  ├─ NF 321_0_res.json
│  ├─ NF 321_0.md
│  ├─ NF 321_0.docx
│  ├─ NF 321_0_layout_det_res.png
│  ├─ NF 321_0_overall_ocr_res.png
│  ├─ NF 321_0_table_1.html
│  ├─ NF 321_0_table_1.xlsx
│  ├─ NF 321_1_res.json
│  └─ ...
├─ documents/
│  ├─ 001_nota_fiscal/
│  │  ├─ pages/
│  │  │  └─ page_000/
│  │  │     ├─ NF 321_0_res.json
│  │  │     ├─ NF 321_0.md
│  │  │     ├─ NF 321_0_layout_det_res.png
│  │  │     └─ NF 321_0_overall_ocr_res.png
│  │  ├─ document_metadata.json
│  │  └─ llm_ready.json
│  ├─ 002_comprovante_veiculacao/
│  │  ├─ pages/
│  │  │  ├─ page_001/
│  │  │  └─ page_002/
│  │  ├─ document_metadata.json
│  │  └─ llm_ready.json
│  └─ 003_unknown/
│     ├─ pages/
│     ├─ document_metadata.json
│     └─ llm_ready.json
├─ page_inventory.json
├─ page_text_index.json
├─ page_classification.json
├─ document_groups.json
└─ source_manifest.json

Todos os nomes de pastas gerados pelo pipeline devem ser sanitizados:

NF 321        → NF_321
raw_NF 321    → raw_NF_321
Nota Fiscal   → nota_fiscal
Novos tipos documentais

A primeira versão deve classificar estes tipos:

nota_fiscal
pedido_insercao
autorizacao_veiculacao
comprovante_veiculacao
artigo_299
unknown

A unidade mínima será sempre página.

Não haverá divisão dentro da mesma página nesta versão.

Etapa 1 — Rodar PaddleOCR direto em raw_...

O ocr_runner.py deve deixar de salvar os artefatos diretamente em:

output/PI293267/NF_321/

e passar a salvar em:

output/PI293267/NF_321/raw_NF_321/

Ou seja:

arquivo_output_dir = output/PI293267/NF_321
raw_output_dir = output/PI293267/NF_321/raw_NF_321

O PaddleOCR deve receber raw_output_dir como pasta de saída.

Isso evita misturar arquivos brutos com os arquivos organizados pelo pipeline.

Etapa 2 — Criar inventário dos artefatos

Criar um módulo, por exemplo:

src/page_inventory.py

Responsabilidade:

- ler a pasta raw_<arquivo>
- localizar todos os *_res.json
- identificar o page_index de cada JSON
- associar os demais artefatos da mesma página
- gerar page_inventory.json

Exemplo de saída:

{
  "source_file": "inputs/checking/PI293267/NF 321.pdf",
  "raw_output_folder": "output/PI293267/NF_321/raw_NF_321",
  "pages": [
    {
      "page_index": 0,
      "page_folder": null,
      "res_json": "raw_NF_321/NF 321_0_res.json",
      "markdown": "raw_NF_321/NF 321_0.md",
      "docx": "raw_NF_321/NF 321_0.docx",
      "layout_image": "raw_NF_321/NF 321_0_layout_det_res.png",
      "overall_ocr_image": "raw_NF_321/NF 321_0_overall_ocr_res.png",
      "table_files": [
        "raw_NF_321/NF 321_0_table_1.html",
        "raw_NF_321/NF 321_0_table_1.xlsx"
      ]
    }
  ]
}

A identificação da página deve ser robusta:

1. Preferir page_index dentro do JSON.
2. Se não existir, inferir pelo padrão do nome do arquivo: _0_res.json, _1_res.json, etc.
3. Se ainda não for possível, marcar como unknown_page_index e registrar no log.
Etapa 3 — Normalizar texto por página

Criar módulo:

src/page_text_indexer.py

Responsabilidade:

- ler cada *_res.json
- extrair textos, scores e boxes
- gerar texto original por página
- gerar texto normalizado para classificação
- separar linhas de baixa confiança
- gerar page_text_index.json

Fonte principal no JSON:

overall_ocr_res.rec_texts
overall_ocr_res.rec_scores
overall_ocr_res.rec_boxes

Saída esperada:

{
  "pages": [
    {
      "page_index": 0,
      "raw_text_lines": [
        {
          "text": "NOTA FISCAL - FATURA DE SERVIÇOS DE COMUNICACO",
          "score": 0.954,
          "bbox": [734, 42, 1138, 59]
        }
      ],
      "normalized_text": "NOTA FISCAL FATURA DE SERVICOS DE COMUNICACAO",
      "low_confidence_lines": [
        {
          "text": "AE T TT TTT ETT T",
          "score": 0.29
        }
      ]
    }
  ]
}

A normalização deve:

- converter para maiúsculas;
- remover acentos;
- substituir múltiplos espaços por um espaço;
- remover pontuação desnecessária para matching;
- preservar o texto original em paralelo;
- manter scores e coordenadas.
Etapa 4 — Regras em YAML

Criar arquivo:

config/document_rules.yaml

Exemplo inicial:

thresholds:
  classified: 0.75
  needs_review: 0.45
  unknown_below: 0.45
  continuation_min_confidence: 0.35

document_types:
  nota_fiscal:
    label: "Nota fiscal"
    strong_terms:
      - "NOTA FISCAL"
      - "FATURA DE SERVICOS"
      - "MODELO 21"
      - "DATA DA EMISSAO"
      - "SERIE"
      - "NUMERO"
      - "CNPJ"
      - "CFOP"
      - "VENCIMENTO"
      - "VALOR"
    weak_terms:
      - "INSCRICAO ESTADUAL"
      - "INSCR MUNIC"
      - "SACADO"
      - "PRESTACAO DE SERVICO"

  pedido_insercao:
    label: "PI - Pedido de inserção"
    strong_terms:
      - "PEDIDO DE INSERCAO"
      - "P I"
      - "PI"
      - "NUMERO DO PI"
      - "CAMPANHA"
      - "CLIENTE"
      - "AGENCIA"
      - "VEICULO"
      - "PERIODO"
      - "INSERCOES"
      - "VALOR"
    weak_terms:
      - "PRACA"
      - "FORMATO"
      - "MIDIA"
      - "TABELA"
      - "DESCONTO"

  autorizacao_veiculacao:
    label: "AV - Autorização de veiculação"
    strong_terms:
      - "AUTORIZACAO DE VEICULACAO"
      - "AUTORIZAMOS"
      - "AV"
      - "VEICULACAO"
      - "VEICULO"
      - "CAMPANHA"
      - "CLIENTE"
      - "PERIODO"
      - "INSERCOES"
    weak_terms:
      - "MIDIA"
      - "PRACA"
      - "PROGRAMACAO"
      - "VALOR"

  comprovante_veiculacao:
    label: "Comprovante de veiculação"
    strong_terms:
      - "COMPROVANTE DE VEICULACAO"
      - "RELATORIO DE VEICULACAO"
      - "MAPA DE VEICULACAO"
      - "VEICULACAO"
      - "INSERCOES"
      - "EXIBICOES"
      - "PROGRAMA"
      - "EMISSORA"
      - "CANAL"
      - "HORARIO"
    weak_terms:
      - "DATA"
      - "PERIODO"
      - "PRACA"
      - "CLIENTE"
      - "CAMPANHA"

  artigo_299:
    label: "Artigo 299"
    strong_terms:
      - "ARTIGO 299"
      - "CODIGO PENAL"
      - "PENAS DA LEI"
      - "DECLARO"
      - "DECLARAMOS"
      - "REALIZAMOS A VEICULACAO"
      - "REALIZOU A VEICULACAO"
      - "CONFORME O PI"
      - "CONFORME PI"
      - "SOB AS PENAS DA LEI"
    weak_terms:
      - "VERACIDADE"
      - "FALSIDADE IDEOLOGICA"
      - "RESPONSABILIDADE"
      - "INFORMACOES PRESTADAS"
      - "VEICULACAO CONFORME"

Para o artigo_299, a regra não deve depender apenas do título. Ela deve pontuar bem frases típicas como:

DECLARO SOB AS PENAS DA LEI
DECLARAMOS SOB AS PENAS DA LEI
ARTIGO 299 DO CODIGO PENAL
REALIZAMOS A VEICULACAO CONFORME O PI
VEICULACAO CONFORME PI
Etapa 5 — Classificação por página

Criar módulo:

src/page_classifier.py

Responsabilidade:

- carregar config/document_rules.yaml
- calcular score por tipo documental
- escolher o melhor tipo
- aplicar thresholds
- aplicar regra de needs_review
- gerar page_classification.json

Exemplo de saída:

{
  "pages": [
    {
      "page_index": 0,
      "document_type": "nota_fiscal",
      "confidence": 0.91,
      "status": "classified",
      "method": "rules",
      "matched_terms": [
        "NOTA FISCAL",
        "FATURA DE SERVICOS",
        "MODELO 21",
        "CFOP"
      ],
      "needs_review": false
    },
    {
      "page_index": 1,
      "document_type": "comprovante_veiculacao",
      "confidence": 0.62,
      "status": "needs_review",
      "method": "rules",
      "matched_terms": [
        "VEICULACAO",
        "PROGRAMA",
        "PERIODO"
      ],
      "needs_review": true
    }
  ]
}

O score inicial pode ser simples:

strong_terms encontrados valem mais pontos.
weak_terms encontrados valem menos pontos.
confidence = pontos_do_tipo / pontos_maximos_do_tipo

Nesta primeira versão, o score não precisa ser perfeito. Ele precisa ser explicável e ajustável.

Etapa 6 — Regra de continuação

Criar no mesmo módulo ou em:

src/document_segmenter.py

Regra:

Se uma página tiver score baixo, mas:
- vier depois de uma página classificada;
- não tiver fortes evidências de outro tipo;
- tiver termos fracos compatíveis com o tipo anterior;
- tiver confidence >= continuation_min_confidence;

então classificar como continuação do documento anterior.

Exemplo:

{
  "page_index": 2,
  "document_type": "comprovante_veiculacao",
  "confidence": 0.38,
  "status": "continuation",
  "method": "rules_continuation",
  "attached_to_previous_document": true,
  "needs_review": true,
  "reason": "Página com baixa confiança própria, mas compatível com o tipo da página anterior."
}

A página de continuação deve ser agrupada ao documento anterior, mas deve marcar o documento inteiro como needs_review: true.

Etapa 7 — Agrupar documentos lógicos

Criar módulo:

src/document_segmenter.py

Responsabilidade:

- ler page_classification.json
- agrupar páginas consecutivas
- iniciar novo documento quando o tipo muda
- manter unknown como documento próprio
- respeitar páginas marcadas como continuation
- gerar document_groups.json

Exemplo:

{
  "documents": [
    {
      "document_id": "001",
      "document_type": "nota_fiscal",
      "pages": [0],
      "status": "classified",
      "needs_review": false,
      "confidence": 0.91
    },
    {
      "document_id": "002",
      "document_type": "comprovante_veiculacao",
      "pages": [1, 2],
      "status": "classified_with_continuation",
      "needs_review": true,
      "confidence": 0.62
    },
    {
      "document_id": "003",
      "document_type": "unknown",
      "pages": [3],
      "status": "unknown",
      "needs_review": true,
      "confidence": 0.22
    }
  ]
}

Se o mesmo tipo aparecer depois de outro tipo, deve gerar novo documento:

001_nota_fiscal
002_comprovante_veiculacao
003_nota_fiscal
004_comprovante_veiculacao
Etapa 8 — Copiar artefatos para documents/

Criar módulo:

src/document_artifact_organizer.py

Responsabilidade:

- criar documents/
- criar 001_tipo/
- criar pages/page_000/
- copiar os artefatos da página para a pasta correta
- manter o raw intacto

Exemplo:

documents/001_nota_fiscal/pages/page_000/
├─ NF 321_0_res.json
├─ NF 321_0.md
├─ NF 321_0.docx
├─ NF 321_0_layout_det_res.png
├─ NF 321_0_overall_ocr_res.png
├─ NF 321_0_table_1.html
└─ NF 321_0_table_1.xlsx

Importante: para a LLM da próxima etapa, copie pelo menos:

- *_res.json
- *.md
- *_layout_det_res.png
- *_overall_ocr_res.png
- tabelas .html/.xlsx se existirem
Etapa 9 — Gerar document_metadata.json

Para cada documento lógico:

documents/001_nota_fiscal/document_metadata.json

Exemplo:

{
  "document_id": "001",
  "document_type": "nota_fiscal",
  "document_label": "Nota fiscal",
  "source_file": "inputs/checking/PI293267/NF 321.pdf",
  "source_file_name": "NF 321.pdf",
  "source_pages": [0],
  "page_range": "1",
  "status": "classified",
  "needs_review": false,
  "confidence": 0.91,
  "classification_method": "rules",
  "output_folder": "output/PI293267/NF_321/documents/001_nota_fiscal"
}
Etapa 10 — Gerar llm_ready.json

Para cada documento lógico:

documents/001_nota_fiscal/llm_ready.json

Formato sugerido:

{
  "document_id": "001",
  "document_type": "nota_fiscal",
  "document_label": "Nota fiscal",
  "source_file": "NF 321.pdf",
  "source_pages": [0],
  "classification": {
    "method": "rules",
    "status": "classified",
    "confidence": 0.91,
    "needs_review": false,
    "matched_terms": [
      "NOTA FISCAL",
      "MODELO 21",
      "CFOP"
    ]
  },
  "pages": [
    {
      "page_index": 0,
      "page_number": 1,
      "text_lines": [],
      "low_confidence_lines": [],
      "artifacts": {
        "res_json": "pages/page_000/NF 321_0_res.json",
        "markdown": "pages/page_000/NF 321_0.md",
        "layout_image": "pages/page_000/NF 321_0_layout_det_res.png",
        "overall_ocr_image": "pages/page_000/NF 321_0_overall_ocr_res.png",
        "table_files": []
      }
    }
  ],
  "instructions_for_llm": [
    "Analise apenas este documento lógico.",
    "Não misture dados com outros documentos detectados no mesmo arquivo original.",
    "Use o JSON como fonte principal.",
    "Use as imagens de layout e OCR como evidência visual.",
    "Se houver divergência, baixa confiança ou campo ilegível, marque como needs_review.",
    "Não invente valores ausentes."
  ]
}

Como definido, a próxima LLM receberá:

llm_ready.json + imagens de layout/overall OCR
Etapa 11 — Gerar source_manifest.json

Arquivo:

output/PI293267/NF_321/source_manifest.json

Exemplo:

{
  "source_file": "inputs/checking/PI293267/NF 321.pdf",
  "source_file_name": "NF 321.pdf",
  "file_output_folder": "output/PI293267/NF_321",
  "raw_output_folder": "output/PI293267/NF_321/raw_NF_321",
  "split_strategy": "post_ocr_page_classification",
  "page_unit": "page",
  "total_pages": 3,
  "documents_detected": [
    {
      "document_id": "001",
      "document_type": "nota_fiscal",
      "document_label": "Nota fiscal",
      "pages": [0],
      "page_range": "1",
      "confidence": 0.91,
      "status": "classified",
      "needs_review": false,
      "output_folder": "output/PI293267/NF_321/documents/001_nota_fiscal",
      "llm_ready": "output/PI293267/NF_321/documents/001_nota_fiscal/llm_ready.json"
    },
    {
      "document_id": "002",
      "document_type": "comprovante_veiculacao",
      "document_label": "Comprovante de veiculação",
      "pages": [1, 2],
      "page_range": "2-3",
      "confidence": 0.62,
      "status": "classified_with_continuation",
      "needs_review": true,
      "output_folder": "output/PI293267/NF_321/documents/002_comprovante_veiculacao",
      "llm_ready": "output/PI293267/NF_321/documents/002_comprovante_veiculacao/llm_ready.json"
    }
  ],
  "needs_review": true
}

Esse será o principal arquivo para o ADK e para a próxima LLM.

Etapa 12 — Atualizar processing_summary.json

O resumo geral da execução deve incluir os documentos lógicos detectados.

Exemplo dentro do registro do arquivo:

{
  "file_name": "NF 321.pdf",
  "status": "processed",
  "output_folder": "output/PI293267/NF_321",
  "raw_output_folder": "output/PI293267/NF_321/raw_NF_321",
  "documents_detected": [
    {
      "document_id": "001",
      "document_type": "nota_fiscal",
      "pages": [0],
      "needs_review": false
    },
    {
      "document_id": "002",
      "document_type": "comprovante_veiculacao",
      "pages": [1, 2],
      "needs_review": true
    }
  ]
}
Nova estrutura de projeto

Sugestão de novos arquivos:

PaddleOCR/
├─ config/
│  ├─ config.yaml
│  ├─ test_config.yaml
│  └─ document_rules.yaml
├─ examples/
│  ├─ nota_fiscal/
│  ├─ pedido_insercao/
│  ├─ autorizacao_veiculacao/
│  ├─ comprovante_veiculacao/
│  └─ artigo_299/
├─ src/
│  ├─ page_inventory.py
│  ├─ page_text_indexer.py
│  ├─ page_classifier.py
│  ├─ document_segmenter.py
│  ├─ document_artifact_organizer.py
│  ├─ llm_ready_writer.py
│  ├─ manifest_writer.py
│  └─ utils/
│     ├─ text_normalizer.py
│     └─ path_utils.py
Critérios de aceite da primeira versão

A implementação estará correta se:

1. O PaddleOCR salvar o output bruto em raw_<arquivo>.
2. A pasta raw_<arquivo> for criada com nome sanitizado.
3. O pipeline gerar page_inventory.json.
4. O pipeline gerar page_text_index.json.
5. O pipeline classificar páginas nos tipos definidos.
6. As regras forem carregadas de config/document_rules.yaml.
7. Páginas unknown virarem documentos próprios.
8. Páginas de continuação forem anexadas ao documento anterior quando a regra se aplicar.
9. O pipeline gerar document_groups.json.
10. O pipeline criar documents/001_tipo/.
11. Os artefatos forem copiados, não movidos.
12. Cada documento receber document_metadata.json.
13. Cada documento receber llm_ready.json.
14. O arquivo source_manifest.json for criado.
15. O processing_summary.json incluir documents_detected.
16. Qualquer documento ou página needs_review permanecer processado, apenas sinalizado.