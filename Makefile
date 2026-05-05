.PHONY: help install install-dev test lint format clean

help:
	@echo "D.A.R.E. - Desktop Action Recording Execution"
	@echo ""
	@echo "Available targets:"
	@echo "  install       - Install the package"
	@echo "  dev           - Install in editable mode with development tools"
	@echo "  lint          - Run code quality checks"
	@echo "  format        - Format code with black/isort"
	@echo "  clean         - Remove build artifacts"

install:
	pip install -e .

dev:
	pip install -e .
	pip install black isort ruff

lint:
	ruff check dare/
	black --check dare/
	isort --check dare/

format:
	black dare/
	isort dare/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf build/ dist/ *.egg-info/ .eggs/
