from src.extraction.evidence_store import content_hash, save_evidence


def test_content_hash_is_deterministic():
    a = content_hash(b"hello menu")
    b = content_hash(b"hello menu")
    assert a == b


def test_content_hash_differs_for_different_content():
    a = content_hash(b"cocktail menu")
    b = content_hash(b"wine menu")
    assert a != b


def test_save_evidence_returns_relative_path_and_is_idempotent(tmp_path, monkeypatch):
    import src.extraction.evidence_store as store

    monkeypatch.setattr(store, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(store, "EVIDENCE_ROOT", tmp_path / "evidence")

    path1 = store.save_evidence("venue123", b"<html>menu</html>", kind="html")
    path2 = store.save_evidence("venue123", b"<html>menu</html>", kind="html")

    assert path1 == path2  # identical content -> identical path, no duplicate file
    assert (tmp_path / "evidence" / "venue123").exists()
