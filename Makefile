.PHONY: help lint test tox docs docs-build build clean

help:  ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

lint:  ## Run linting (pre-commit).
	pre-commit run --all-files

test:  ## Run the test suite.
	pytest

tox:  ## Run the test suite against all supported Python versions.
	tox

docs:  ## Serve the docs locally with live reload.
	mkdocs serve

docs-build:  ## Build the static docs site into ./site.
	mkdocs build

build:  ## Build sdist and wheel into ./dist.
	python -m build

clean:  ## Remove build artifacts and caches.
	rm -rf build/ dist/ site/ *.egg-info pfig.egg-info
	rm -rf .pytest_cache .coverage coverage.xml htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
