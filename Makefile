.PHONY: dev format lint typecheck test clean

# Install the project + dev tools into the uv-managed environment.
dev:
	uv sync

# Auto-format (isort then black), matching fleet-sdk's line-length 88.
format:
	uv run isort pm tests
	uv run black pm tests

# Lint without modifying: ruff + black --check.
lint:
	uv run ruff check pm tests
	uv run black --check pm tests

# Static type checking (mypy --strict over the pm package).
typecheck:
	uv run mypy

# Run the test suite.
test:
	uv run pytest

# Remove build/test caches and simulation run artifacts.
clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
