"""Deterministic ETA tests for adaptive German audiobook production."""
from __future__ import annotations

from tts_audiobook_tool.web_studio.progress_estimate import remaining_seconds, export_remaining_seconds


def test_eta_waits_for_three_actual_completed_segments():
    assert remaining_seconds([], 1000) is None
    assert remaining_seconds([(100, 4)], 1000) is None
    assert remaining_seconds([(100, 4), (100, 5)], 1000) is None


def test_eta_tracks_latest_segment_speed_and_remaining_characters():
    samples = [(100, 4), (100, 5), (100, 6)]
    assert remaining_seconds(samples, 400) == 20
    faster = [(100, 4), (100, 5), (100, 6), (100, 2), (100, 2), (100, 2)]
    assert remaining_seconds(faster, 400) == 12
    assert remaining_seconds(faster, 0) == 0


def test_eta_uses_median_to_limit_outliers():
    samples = [(100, 5), (100, 5), (100, 500), (100, 5), (100, 5)]
    assert remaining_seconds(samples, 1000) == 50


def test_eta_rejects_corrupt_metrics_without_false_certainty():
    assert remaining_seconds([(10, -1), (0, 2), (20, float("nan"))], 500) is None
    assert remaining_seconds([(10, 4), (10, 4), (10, float("inf"))], 500) is None


def test_eta_handles_checkpoint_resume_and_rolling_window():
    old = [(100, 100)] * 15
    current = [(100, 5)] * 8
    assert remaining_seconds(old + current, 1000) == 50



def test_export_eta_requires_four_real_checkpoints():
    points = [(25, 4.0), (50, 8.0), (75, 12.0), (100, 16.0)]
    assert export_remaining_seconds(points[:1], 25, 200) is None
    assert export_remaining_seconds(points[:3], 75, 200) is None
    assert export_remaining_seconds(points, 100, 200) == 16


def test_export_eta_adapts_to_recent_real_processing_speed():
    points = [(25, 25.0), (50, 50.0), (75, 75.0),
              (100, 76.0), (125, 77.0), (150, 78.0),
              (175, 79.0)]
    assert export_remaining_seconds(points, 175, 200) == 1
    assert export_remaining_seconds(points, 200, 200) is None


def test_export_eta_rejects_impossible_progress_or_time():
    assert export_remaining_seconds([(25, 1), (50, 2), (50, 3), (100, 4)], 100, 200) is None
    assert export_remaining_seconds([(25, 1), (50, 2), (75, float("nan")), (100, 4)], 100, 200) is None
    assert export_remaining_seconds([(25, 1), (50, 2), (75, 3), (100, 4)], 10, 200) is None
    assert export_remaining_seconds([], 100, 200) is None
