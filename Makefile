.PHONY: install test run

install:
	python3 -m pip install --upgrade pip
	python3 -m pip install -e .[dev]

test:
	pytest -q

run:
	uvicorn investigation_service.main:app --host 0.0.0.0 --port 8080 --reload
