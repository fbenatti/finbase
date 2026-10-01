from app.services.reprocess_memory import MemorySnapshot, memory_pressure_reason, memory_snapshot


def test_high_process_rss_is_not_a_quota_when_memory_headroom_is_available():
    assert memory_pressure_reason(
        snapshot=MemorySnapshot(total_bytes=64 << 30, available_bytes=32 << 30, source="host"),
        reserve_ratio=0.10,
    ) is None


def test_low_available_memory_triggers_scaled_pressure_guard():
    reason = memory_pressure_reason(
        snapshot=MemorySnapshot(total_bytes=64 << 30, available_bytes=5 << 30, source="host"),
        reserve_ratio=0.10,
    )
    assert reason and "Pressão de memória" in reason


def test_explicit_emergency_limit_remains_available():
    reason = memory_pressure_reason(
        snapshot=MemorySnapshot(total_bytes=64 << 30, available_bytes=5 << 30, source="host"),
        reserve_ratio=0.01,
        hard_limit_mb=6144,
    )
    assert reason and "limite emergencial" in reason


def test_cgroup_v2_uses_effective_headroom_after_reclaimable_cache(tmp_path):
    (tmp_path / "memory.max").write_text(str(10 << 30))
    (tmp_path / "memory.current").write_text(str(9 << 30))
    (tmp_path / "memory.stat").write_text(f"inactive_file {2 << 30}\n")
    snap = memory_snapshot(tmp_path)
    assert snap.source == "cgroup-v2"
    assert snap.available_bytes == 3 << 30


def test_cgroup_v1_uses_effective_headroom_after_reclaimable_cache(tmp_path):
    (tmp_path / "memory.limit_in_bytes").write_text(str(10 << 30))
    (tmp_path / "memory.usage_in_bytes").write_text(str(9 << 30))
    (tmp_path / "memory.stat").write_text(f"total_inactive_file {2 << 30}\n")
    snap = memory_snapshot(tmp_path)
    assert snap.source == "cgroup-v1"
    assert snap.available_bytes == 3 << 30
