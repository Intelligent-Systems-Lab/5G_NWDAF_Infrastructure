SHELL := /usr/bin/env bash
TESTBED ?= testbed.yaml
CONFIG_DIR ?=
NAME ?= local
SCENARIO ?= fixtures/full-core/scenarios/full-core-cat-transition.yaml

.PHONY: help preflight config-check config-render config-contract-smoke network-config-smoke dataset-generate dataset-check dataset-show \
	dataset-smoke dataset-stage-plan dataset-stage ml-compose-check ml-cpu-smoke ml-lifecycle-smoke \
	ml-start ml-status ml-stop vm-up vm-status vm-halt \
	services-start services-status services-stop subscriptions-start \
	subscriptions-status subscriptions-stop subscriber-data-validate \
	subscriber-data-plan subscriber-data-apply subscriber-data-show \
	subscriber-data-clear experiment-reset-plan experiment-reset \
	experiment-reset-verify observe logs

help:
	@echo "5G NWDAF Infrastructure"
	@echo "  make preflight"
	@echo "  make config-check [TESTBED=...] [CONFIG_DIR=...]"
	@echo "  make config-render NAME=... [SCENARIO=...] [TESTBED=...]"
	@echo "  make config-contract-smoke [CONFIG_DIR=...]"
	@echo "  make network-config-smoke"
	@echo "  make dataset-generate | dataset-check | dataset-show | dataset-smoke"
	@echo "  make dataset-stage-plan | dataset-stage"
	@echo "  make ml-compose-check | ml-cpu-smoke | ml-lifecycle-smoke"
	@echo "  make ml-start | ml-status | ml-stop"
	@echo "  make vm-up | vm-status | vm-halt"
	@echo "  make services-start | services-status | services-stop"
	@echo "  make subscriber-data-validate | subscriber-data-plan | subscriber-data-apply | subscriber-data-show | subscriber-data-clear"
	@echo "  make subscriptions-start | subscriptions-status | subscriptions-stop"
	@echo "  make experiment-reset-plan | experiment-reset RESET_CONFIRM=<scenario> | experiment-reset-verify"
	@echo "  make observe | logs"

preflight:
	@scripts/host/preflight.sh "$(TESTBED)" "$(CONFIG_DIR)"

config-check:
	@python3 scripts/host/config-check.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)")

config-render:
	@python3 scripts/host/config-render.py --testbed "$(TESTBED)" --name "$(NAME)" --scenario "$(SCENARIO)"

config-contract-smoke:
	@python3 scripts/host/config-contract-smoke.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)")

network-config-smoke:
	@python3 scripts/host/network-config-smoke.py

dataset-generate:
	@python3 scripts/host/dataset.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)") generate

dataset-check:
	@python3 scripts/host/dataset.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)") check

dataset-show:
	@python3 scripts/host/dataset.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)") show

dataset-smoke:
	@scripts/host/dataset-smoke.sh "$(TESTBED)" "$(CONFIG_DIR)"

dataset-stage-plan:
	@scripts/host/dataset-stage.sh plan "$(TESTBED)" "$(CONFIG_DIR)"

dataset-stage:
	@scripts/host/dataset-stage.sh apply "$(TESTBED)" "$(CONFIG_DIR)"

ml-compose-check:
	@python3 scripts/host/ml-compose-check.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)")
	@python3 scripts/host/ml-compose-check.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)") --mode cpu-smoke

ml-cpu-smoke:
	@scripts/host/ml-cpu-smoke.sh

ml-lifecycle-smoke:
	@scripts/host/ml-lifecycle-smoke.sh

ml-start:
	@scripts/host/ml-start.sh "$(TESTBED)" "$(CONFIG_DIR)"

ml-status:
	@scripts/host/ml-status.sh

ml-stop:
	@scripts/host/ml-stop.sh

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

subscriber-data-validate:
	@scripts/host/subscriber-data.sh validate "$(TESTBED)" "$(CONFIG_DIR)"

subscriber-data-plan:
	@scripts/host/subscriber-data.sh plan "$(TESTBED)" "$(CONFIG_DIR)"

subscriber-data-apply:
	@scripts/host/subscriber-data.sh apply "$(TESTBED)" "$(CONFIG_DIR)"

subscriber-data-show:
	@scripts/host/subscriber-data.sh show "$(TESTBED)" "$(CONFIG_DIR)"

subscriber-data-clear:
	@scripts/host/subscriber-data.sh clear "$(TESTBED)" "$(CONFIG_DIR)"

subscriptions-start:
	@scripts/host/subscriptions-start.sh

subscriptions-status:
	@scripts/host/subscriptions-status.sh

subscriptions-stop:
	@scripts/host/subscriptions-stop.sh

experiment-reset-plan:
	@scripts/host/experiment-reset.sh plan "$(TESTBED)" "$(CONFIG_DIR)"

experiment-reset:
	@RESET_CONFIRM="$(RESET_CONFIRM)" scripts/host/experiment-reset.sh apply "$(TESTBED)" "$(CONFIG_DIR)"

experiment-reset-verify:
	@scripts/host/experiment-reset.sh verify "$(TESTBED)" "$(CONFIG_DIR)"

observe:
	@scripts/host/observe.sh

logs:
	@scripts/host/logs.sh
