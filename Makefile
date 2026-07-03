.PHONY: build cli api checks lint fmt test shell clean

build:
	docker compose build

cli: build
	docker compose run --rm cli design

api: build
	docker compose up api

checks: build
	docker compose run --rm checks

lint: build
	docker compose run --rm cli bash -lc "ruff check src tests && mypy"

fmt: build
	docker compose run --rm cli bash -lc "ruff check --fix src tests && ruff format src tests"

test: build
	docker compose run --rm cli bash -lc "pytest -q"

shell: build
	docker compose run --rm cli bash

clean:
	rm -rf data/*
