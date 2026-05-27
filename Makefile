up:
	docker compose up --build

down:
	docker compose down -v

test:
	pytest

lint:
	ruff check . && black --check . && mypy src/acme_app/domain src/acme_app/policy

eval:
	python -m acme_app.evaluation.runner --runs 3
