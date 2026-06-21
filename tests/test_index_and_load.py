import json
import subprocess
import sys
import os
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

from scripts.load_test import parse_stages, percentile
from src.infra.pdf_ingest import ingest_pdf_with_pymupdf


def test_parse_stages_and_percentile():
    assert parse_stages("2:1,10:0.5") == [(2,1.0),(10,0.5)]
    assert percentile([1,2,3,4],.5) == 2.5
    with pytest.raises(ValueError): parse_stages("0:1")


def test_lightweight_incremental_pdf_build_skips_images_and_second_parse(tmp_path):
    docs=tmp_path/"docs"; docs.mkdir()
    pdf=fitz.open(); page=pdf.new_page(); page.insert_text((72,72),"fast incremental index")
    pdf.save(str(docs/"sample.pdf")); pdf.close()
    pages=tmp_path/"pages.json"; manifest=tmp_path/"manifest.json"; images=tmp_path/"images"
    command=[sys.executable,"scripts/build_index_incremental.py","--input-dir",str(docs),"--output-pages",str(pages),"--manifest",str(manifest),"--image-dir",str(images),"--skip-page-images","--no-progress"]
    first=subprocess.run(command,capture_output=True,text=True,check=True)
    assert "重建文档 : 1" in first.stdout
    payload=json.loads(pages.read_text(encoding="utf-8"))
    assert len(payload)==1 and payload[0]["image_path"] is None
    assert not list(images.glob("*.png"))
    second=subprocess.run(command,capture_output=True,text=True,check=True)
    assert "重建文档 : 0  |  跳过 : 1" in second.stdout
    saved=json.loads(manifest.read_text(encoding="utf-8"))
    assert list(saved)==["sample.pdf"]
    assert len(saved["sample.pdf"]["sha256"])==64
    # 仅更新时间、内容不变时仍应命中 SHA-256 并跳过。
    stat=(docs/"sample.pdf").stat(); os.utime(docs/"sample.pdf",(stat.st_atime,stat.st_mtime+10))
    touched=subprocess.run(command,capture_output=True,text=True,check=True)
    assert "重建文档 : 0  |  跳过 : 1" in touched.stdout


def test_qwen_vision_parser_enriches_page_and_keeps_local_text(tmp_path,monkeypatch):
    source=tmp_path/"visual.pdf"; images=tmp_path/"images"
    pdf=fitz.open(); page=pdf.new_page(); page.insert_text((72,72),"local text")
    pdf.save(str(source)); pdf.close()

    class FakeParser:
        enabled=True
        def parse(self,image_path,local_text=""):
            assert Path(image_path).exists() and "local text" in local_text
            return "# 视觉标题\n采购单号：PO-123"

    settings=SimpleNamespace(
        vision_parse_mode="auto",vision_min_text_chars=200,vision_drawing_threshold=12,
        vision_parser_workers=2,vision_parser_model="qwen-vl-test",
    )
    monkeypatch.setattr("src.infra.pdf_ingest.QwenVisionPageParser",FakeParser)
    monkeypatch.setattr("src.infra.pdf_ingest.SETTINGS",settings)
    pages=ingest_pdf_with_pymupdf(str(source),"doc",image_output_dir=str(images),dpi=72)
    assert "local text" in pages[0].content and "PO-123" in pages[0].content
    assert pages[0].metadata["vision_parse_status"] == "success"


def test_qwen_auth_failure_stops_following_page_calls(tmp_path,monkeypatch):
    source=tmp_path/"two-pages.pdf"; images=tmp_path/"images"
    pdf=fitz.open()
    for _ in range(2): pdf.new_page()
    pdf.save(str(source)); pdf.close()
    calls=[]
    class FailedParser:
        enabled=True
        def parse(self,image_path,local_text=""):
            calls.append(image_path); raise RuntimeError("invalid key")
    settings=SimpleNamespace(
        vision_parse_mode="all",vision_min_text_chars=200,vision_drawing_threshold=12,
        vision_parser_workers=2,vision_parser_model="qwen-vl-test",
    )
    monkeypatch.setattr("src.infra.pdf_ingest.QwenVisionPageParser",FailedParser)
    monkeypatch.setattr("src.infra.pdf_ingest.SETTINGS",settings)
    pages=ingest_pdf_with_pymupdf(str(source),"doc",image_output_dir=str(images),dpi=72)
    assert len(calls)==1
    assert [p.metadata["vision_parse_status"] for p in pages] == ["fallback","fallback"]
