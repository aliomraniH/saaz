.PHONY: install migrate seed enrich embed verify refresh all clean

PIP ?= $(shell command -v uv >/dev/null && echo "uv pip" || echo "pip")

install:
	$(PIP) install -e ".[dev]"

migrate:
	@psql "$$DATABASE_URL" -f migrations/0001_init.sql

seed:
	python -m scripts.seed

enrich:
	python -m scripts.enrich

embed:
	python -m scripts.embed

verify:
	python -m scripts.verify

refresh:
	python -m scripts.refresh

check-mneme:
	python -m scripts.check_mneme

# One-shot bootstrap
all: migrate seed enrich embed verify

clean:
	rm -rf .pytest_cache .ruff_cache build dist *.egg-info
