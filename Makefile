SHELL := /usr/bin/env bash
TESTBED ?= testbed.yaml
CONFIG_DIR ?=
NAME ?= local

.PHONY: help preflight config-check config-render ml-compose-check ml-cpu-smoke vm-up vm-status vm-halt \
	services-start services-status services-stop subscriptions-start \
	subscriptions-status subscriptions-stop observe logs

help:
	@echo "5G NWDAF Infrastructure"
	@echo "  make preflight"
	@echo "  make config-check [TESTBED=...] [CONFIG_DIR=...]"
	@echo "  make config-render NAME=... [TESTBED=...]"
	@echo "  make ml-compose-check | ml-cpu-smoke"
	@echo "  make vm-up | vm-status | vm-halt"
	@echo "  make services-start | services-status | services-stop"
	@echo "  make subscriptions-start | subscriptions-status | subscriptions-stop"
	@echo "  make observe | logs"

preflight:
	@scripts/host/preflight.sh "$(TESTBED)" "$(CONFIG_DIR)"

config-check:
	@python3 scripts/host/config-check.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)")

config-render:
	@python3 scripts/host/config-render.py --testbed "$(TESTBED)" --name "$(NAME)"

ml-compose-check:
	@python3 scripts/host/ml-compose-check.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)")
	@python3 scripts/host/ml-compose-check.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)") --mode cpu-smoke

ml-cpu-smoke:
	@scripts/host/ml-cpu-smoke.sh

vm-up:
	@TESTBED="$(TESTBED)" vagrant up

vm-status:
	@TESTBED="$(TESTBED)" vagrant status

vm-halt:
	@TESTBED="$(TESTBED)" vagrant halt

services-start:
	@scripts/host/services-start.sh "$(TESTBED)" "$(CONFIG_DIR)"

services-status:
	@scripts/host/services-status.sh

services-stop:
	@scripts/host/services-stop.sh

subscriptions-start:
	@scripts/host/subscriptions-start.sh

subscriptions-status:
	@scripts/host/subscriptions-status.sh

subscriptions-stop:
	@scripts/host/subscriptions-stop.sh

observe:
	@scripts/host/observe.sh

logs:
	@scripts/host/logs.sh
