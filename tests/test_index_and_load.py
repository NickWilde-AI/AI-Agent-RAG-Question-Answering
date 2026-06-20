import json
import subprocess
import sys
from pathlib import Path

import fitz
import pytest

from scripts.load_test import parse_stages, percentile


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
