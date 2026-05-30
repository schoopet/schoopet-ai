.PHONY: test test-sms-gateway test-agent

test: test-sms-gateway test-agent

test-sms-gateway:
	cd sms-gateway && .venv/bin/python -m pytest tests/ -v --tb=short

test-agent:
	PYTHONPATH=$(PWD) agents/.venv/bin/python -m pytest agents/schoopet/tests/ -v --tb=short
