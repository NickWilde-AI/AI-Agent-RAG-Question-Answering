from src.infra.embedding_cache import JSONEmbeddingCache


def test_embedding_cache_persists_and_invalidates_by_model(tmp_path):
    path=tmp_path/"embeddings.json"; first=JSONEmbeddingCache(str(path),max_entries=100)
    key=first.key("https://example/v1","model-a","hello")
    first.put(key,[1,2,3]); first.save()
    second=JSONEmbeddingCache(str(path),max_entries=100)
    assert second.get(key)==[1.0,2.0,3.0]
    assert second.get(second.key("https://example/v1","model-b","hello")) is None
