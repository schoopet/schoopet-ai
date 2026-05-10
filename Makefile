.PHONY: test test-sms-gateway

test: test-sms-gateway

test-sms-gateway:
	cd sms-gateway && python3 -m pytest tests/ -v --tb=short
