.PHONY: test test-sms-gateway test-task-worker

test: test-sms-gateway test-task-worker

test-sms-gateway:
	cd sms-gateway && python3 -m pytest tests/ -v --tb=short

test-task-worker:
	cd task-worker && python3 -m pytest tests/ -v --tb=short
