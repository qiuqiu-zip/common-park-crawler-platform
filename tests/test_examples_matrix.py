from crawler_platform.storage import FileStore
from scripts import quality_gate


def test_examples_matrix_covers_required_smoke_tags():
    result = quality_gate.validate_examples_matrix()

    assert result["status"] == "passed"
    tags = set(result["details"]["smoke_tags"])
    assert quality_gate.REQUIRED_SMOKE_TAGS.issubset(tags)
    assert result["details"]["smoke_count"] >= 10


def test_windows_safe_deep_path_file_store_regression(workspace_tmp_path):
    store = FileStore(workspace_tmp_path / "lock-regression")
    long_target = store.root / "results" / (("feature19-" + ("x" * 180)) + ".jsonl")

    with store._file_lock(long_target):
        lock_files = list((store.root / "locks").glob("*.lock"))
        assert lock_files
        assert all(len(path.name) < 120 for path in lock_files)
    health = store.check_storage()

    assert health["ok"] is True
