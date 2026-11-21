.PHONY: build up down test lint

build:
	docker compose build

up:
	docker compose up --build

down:
	docker compose down

test:
	python -m pytest -vv

lint:
	python -m compileall -q app tests
