# MCP Validation Tool - Development Makefile
#
# This Makefile provides convenient shortcuts for common development tasks.
# All commands use uv for dependency management and execution.

.PHONY: help install test format lint check clean dev-setup pre-commit debug-test

# Default target
help:
	@echo "MCP Validation Tool - Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  install     Install dependencies including dev extras"
	@echo "  dev-setup   Complete development environment setup"
	@echo ""
	@echo "Code Quality:"
	@echo "  format      Format code with Black"
	@echo "  lint        Check code with Ruff (no fixes)"
	@echo "  lint-fix    Check and fix code issues with Ruff"
	@echo "  check       Run format check without making changes"
	@echo ""
	@echo "Testing:"
	@echo "  test        Run all tests"
	@echo "  test-cov    Run tests with coverage report"
	@echo "  test-fast   Run tests with fail-fast (-x flag)"
	@echo "  debug-test  Run tests with debug output"
	@echo ""
	@echo "Workflows:"
	@echo "  pre-commit  Run full pre-commit workflow (format, lint, test)"
	@echo "  ci          Run CI-like checks (check format, lint, test)"
	@echo ""
	@echo "Utilities:"
	@echo "  clean       Clean up cache and temporary files"
	@echo ""

# Installation and setup
install:
	@echo "Installing dependencies with dev extras..."
	uv sync --extra dev

dev-setup: install
	@echo "Development environment setup complete!"
	@echo "Run 'make help' to see available commands."

# Code formatting
format:
	@echo "Formatting code with Black..."
	uv run --extra dev black mcp_validation/

check:
	@echo "Checking code formatting (no changes)..."
	uv run --extra dev black --check mcp_validation/

# Linting
lint:
	@echo "Linting code with Ruff..."
	uv run --extra dev ruff check mcp_validation/

lint-fix:
	@echo "Linting and fixing code with Ruff..."
	uv run --extra dev ruff check --fix mcp_validation/

# Testing
test:
	@echo "Running tests..."
	uv run --extra dev pytest tests/ -v

test-cov:
	@echo "Running tests with coverage..."
	uv run --extra dev pytest tests/ --cov=mcp_validation --cov-report=term-missing

test-fast:
	@echo "Running tests with fail-fast..."
	uv run --extra dev pytest tests/ -x

debug-test:
	@echo "Running tests with debug output..."
	uv run --extra dev pytest tests/ -v -s

# Pre-commit workflow
pre-commit:
	@echo "Running pre-commit workflow..."
	@$(MAKE) format
	@$(MAKE) lint-fix
	@$(MAKE) test
	@echo "Pre-commit workflow completed successfully!"

# CI-like checks (no automatic fixes)
ci:
	@echo "Running CI checks..."
	@$(MAKE) check
	@$(MAKE) lint
	@$(MAKE) test
	@echo "All CI checks passed!"

# Cleanup
clean:
	@echo "Cleaning up cache and temporary files..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	@echo "Cleanup completed!"