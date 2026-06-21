import json

from src.eval_metrics import mrr_at_k, recall_at_k
from src.gold_dataset import GoldReviewStore


def sample(query="产品A的数值是多少？",page="p1"):
    return {"query":query,"gold_answer":"42","gold_pages":[page],"gold_branch":"chart_qa","category":"图表",
            "source_files":["a.pdf"],"page_nos":[1],"image_paths":["/tmp/a.png"],"model_verified":True,"model_reason":"直接可证"}


def test_review_store_is_idempotent_and_only_accepted_exports(tmp_path):
    store=GoldReviewStore(str(tmp_path/"review.db")); cid=store.upsert_candidate(sample()); assert store.upsert_candidate(sample())==cid
    assert store.stats()["total"]==1
    store.update_review(cid,"accepted",gold_answer="约42",reviewer_note="人工核对")
    exported=store.export_rows("accepted")
    assert len(exported)==1 and exported[0]["gold_answer"]=="约42"
    assert store.export_rows("pending")==[]


def test_rejected_candidate_never_appears_in_accepted_export(tmp_path):
    store=GoldReviewStore(str(tmp_path/"review.db")); cid=store.upsert_candidate(sample())
    store.update_review(cid,"rejected",reviewer_note="问题缺少主体")
    assert store.export_rows("accepted")==[]
    assert store.export_rows("rejected")[0]["id"]==cid


def test_page_run_checkpoint_persists(tmp_path):
    store=GoldReviewStore(str(tmp_path/"review.db")); store.mark_run("single:p1","completed")
    assert "single:p1" in store.completed_runs()


def test_gold_retrieval_metrics():
    ranked=["noise","gold","other"]
    assert mrr_at_k(ranked,["gold"],10)==0.5
    assert recall_at_k(ranked,["gold"],1)==0.0
    assert recall_at_k(ranked,["gold"],3)==1.0
