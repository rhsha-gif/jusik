.PHONY: test smoke api

test:
	python -m pytest quantpilot/tests

smoke:
	python -m quantpilot.jobs.run_smoke

api:
	python -m uvicorn quantpilot.services.api.main:app --reload
