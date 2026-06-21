import json

from scripts.import_gold_json import load_document_pages


def test_load_document_pages_requires_unique_document(tmp_path):
    index=tmp_path/"pages.json"
    index.write_text(json.dumps([{"page_id":"d_p1","doc_id":"d","doc_type":"ppt","language":"zh","content":"x","page_no":1,"source_file":"/docs/目标文档.pdf"}]),encoding="utf-8")
    pages=load_document_pages(index,"目标文档")
    assert pages[1].page_id=="d_p1"
