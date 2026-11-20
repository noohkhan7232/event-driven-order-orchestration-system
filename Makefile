.PHONY: install run test lint migrate seed worker docker-up docker-down

install:
	python -m pip install -r requirements-dev.txt

run:
	uvicorn app.main:app --reload

worker:
	celery -A app.workers.celery_app worker --loglevel=INFO --queues=orders,inventory,payments,shipments,dead_letter

test:
	pytest -q

migrate:
	alembic upgrade head

seed:
	python scripts/seed_demo.py

docker-up:
	docker compose up --build

docker-down:
	docker compose down -v

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy app

test-cov:
	pytest -q --cov=app --cov-report=term-missing
