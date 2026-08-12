SHELL := /usr/bin/env bash
TESTBED ?= testbed.yaml
CONFIG_DIR ?=
NAME ?= local
FROM ?= full-core-cat-transition
DEVICE ?= gpu
SCENARIO ?= fixtures/full-core/scenarios/full-core-cat-transition.yaml

.PHONY: help help-advanced help-dev help-all experiment-validate experiment-start experiment-status experiment-stop \
	config-create config-validate dataset-validate dataset-load reset-show reset test test-containers \
	preflight config-check config-render config-contract-smoke network-config-smoke dataset-generate dataset-check dataset-show \
	dataset-smoke dataset-stage-plan dataset-stage ml-compose-check ml-cpu-smoke ml-lifecycle-smoke \
	ml-start ml-status ml-stop vm-up vm-status vm-halt \
	services-start services-status services-stop subscriptions-start \
	subscriptions-status subscriptions-stop subscriber-data-validate \
	subscriber-data-plan subscriber-data-apply subscriber-data-show \
	subscriber-data-clear experiment-reset-plan experiment-reset \
	experiment-reset-verify observe logs

help:
	@echo "5G NWDAF Infrastructure"
	@echo ""
	@echo "Experiment lifecycle"
	@echo "  make experiment-validate CONFIG_DIR=...  Validate prerequisites and inputs without changing state"
	@echo "  make vm-up                               Create or start the three VMs"
	@echo "  make experiment-start CONFIG_DIR=...     Start Guest, ML, consumer, and subscriptions"
	@echo "  make experiment-status CONFIG_DIR=...    Show the complete experiment state"
	@echo "  make logs                                Follow experiment logs"
	@echo "  make experiment-stop                     Stop processes but retain state and VMs"
	@echo "  make vm-halt                             Gracefully power off the VMs"
	@echo ""
	@echo "More commands: make help-advanced | help-dev | help-all"

help-advanced:
	@echo "5G NWDAF Infrastructure — advanced operations"
	@echo ""
	@echo "Configuration and datasets"
	@echo "  make config-create NAME=... [FROM=full-core-cat-transition|fl-closure-smoke] [DEVICE=gpu|cpu]"
	@echo "  make config-validate CONFIG_DIR=..."
	@echo "  make dataset-generate | dataset-validate | dataset-show | dataset-load CONFIG_DIR=..."
	@echo ""
	@echo "Independent execution domains"
	@echo "  make services-start CONFIG_DIR=... | services-status | services-stop"
	@echo "  make ml-start CONFIG_DIR=... | ml-status | ml-stop"
	@echo "  make subscriptions-start | subscriptions-status | subscriptions-stop"
	@echo ""
	@echo "Subscriber and retained experiment state"
	@echo "  make subscriber-data-show | subscriber-data-apply | subscriber-data-clear CONFIG_DIR=..."
	@echo "  make reset-show CONFIG_DIR=..."
	@echo "  make reset CONFIG_DIR=... RESET_CONFIRM=<scenario>"

help-dev:
	@echo "5G NWDAF Infrastructure — repository tests"
	@echo "  make test             Run static and host-only repository checks"
	@echo "  make test-containers  Run the disposable five-container CPU lifecycle test"

help-all: help
	@echo ""
	@$(MAKE) --no-print-directory help-advanced
	@echo ""
	@$(MAKE) --no-print-directory help-dev

experiment-validate:
	@scripts/host/experiment-validate.sh "$(TESTBED)" "$(CONFIG_DIR)"

experiment-start:
	@scripts/host/experiment-start.sh "$(TESTBED)" "$(CONFIG_DIR)"

experiment-status:
	@scripts/host/experiment-status.sh "$(TESTBED)" "$(CONFIG_DIR)"

experiment-stop:
	@scripts/host/experiment-stop.sh

config-create:
	@case "$(FROM)" in full-core-cat-transition|fl-closure-smoke) ;; *) echo "FROM must be full-core-cat-transition or fl-closure-smoke" >&2; exit 2;; esac
	@case "$(DEVICE)" in gpu|cpu) ;; *) echo "DEVICE must be gpu or cpu" >&2; exit 2;; esac
	@python3 scripts/host/config-render.py --testbed "$(TESTBED)" --name "$(NAME)" \
		--scenario "fixtures/full-core/scenarios/$(FROM).yaml" --output-root config/local --ml-device "$(DEVICE)"

config-validate: config-check

dataset-validate: dataset-check

dataset-load: dataset-stage

reset-show: experiment-reset-plan

reset:
	@RESET_CONFIRM="$(RESET_CONFIRM)" scripts/host/experiment-reset.sh apply "$(TESTBED)" "$(CONFIG_DIR)"
	@scripts/host/experiment-reset.sh verify "$(TESTBED)" "$(CONFIG_DIR)"

test:
	@scripts/host/repository-test.sh "$(TESTBED)" "$(CONFIG_DIR)"

test-containers:
	@scripts/host/ml-lifecycle-smoke.sh

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
	@scripts/host/ml-lifecycle-smoke.sh

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
