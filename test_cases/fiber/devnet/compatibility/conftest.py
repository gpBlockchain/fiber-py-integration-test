"""Distribute compatibility cases across isolated CI jobs when requested."""

import os

import pytest


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    shard_number = os.environ.get("FIBER_COMPATIBILITY_SHARD_INDEX")
    if shard_number is None:
        return

    shard_count = int(os.environ["FIBER_COMPATIBILITY_SHARD_COUNT"])
    shard_index = int(shard_number) - 1
    if shard_count < 1 or not 0 <= shard_index < shard_count:
        raise pytest.UsageError("Invalid compatibility shard index or count")

    selected = [
        item
        for position, item in enumerate(items)
        if position % shard_count == shard_index
    ]
    deselected = [
        item
        for position, item in enumerate(items)
        if position % shard_count != shard_index
    ]
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected
