SHELL := /usr/bin/env bash
TESTBED ?= testbed.yaml
CONFIG_DIR ?=
NAME ?= local
DEVICE ?= gpu
WEBCONSOLE ?= false

.PHONY: help help-advanced help-dev help-all experiment-validate experiment-start experiment-status experiment-stop \
	config-create config-validate dataset-validate dataset-load reset-show reset test test-containers \
	dataset-generate dataset-show \
	ml-start ml-status ml-stop vm-up vm-status vm-halt \
	webconsole-start webconsole-status webconsole-stop \
	services-start services-status services-stop subscriptions-start \
	subscriptions-status subscriptions-stop subscriber-data-apply \
	subscriber-data-show subscriber-data-clear observe logs

help:
	@echo "5G NWDAF Infrastructure"
	@echo ""
	@echo "Experiment lifecycle"
	@echo "  make experiment-validate CONFIG_DIR=...  Diagnose prerequisites and inputs; does not gate start"
	@echo "  make vm-up                               Create or start the three VMs"
	@echo "  make experiment-start CONFIG_DIR=...     Start Guest, ML, consumer, and subscriptions"
	@echo "  make experiment-status CONFIG_DIR=...    Show the complete experiment state"
	@echo "  make logs [SERVICE=...]                  Follow selected logs; defaults to all"
	@echo "  make experiment-stop                     Stop processes but retain state and VMs"
	@echo "  make vm-halt                             Gracefully power off the VMs"
	@echo ""
	@echo "More commands: make help-advanced | help-dev | help-all"

help-advanced:
	@echo "5G NWDAF Infrastructure — advanced operations"
	@echo ""
	@echo "Configuration and datasets"
	@echo "  make config-create TESTBED=... NAME=... FROM=experiments/.../scenario.yaml"
	@echo "                     [DEVICE=gpu|cpu] [WEBCONSOLE=false|true]"
	@echo "  make config-validate CONFIG_DIR=...  Diagnose cross-config inconsistencies"
	@echo "  make dataset-generate | dataset-validate | dataset-show | dataset-load CONFIG_DIR=..."
	@echo ""
	@echo "Independent execution domains"
	@echo "  make services-start CONFIG_DIR=... | services-status | services-stop"
	@echo "  make ml-start CONFIG_DIR=... | ml-status | ml-stop"
	@echo "  make webconsole-start CONFIG_DIR=... | webconsole-status | webconsole-stop"
	@echo "  make subscriptions-start | subscriptions-status | subscriptions-stop"
	@echo "  make observe"
	@echo "  make logs [SOURCE=vm|ml|all] [VM=core|path-a|path-b|all] [SERVICE=name|glob|all]"
	@echo "            [SINCE='10 minutes ago'] [TAIL=lines|all] [FOLLOW=true|false]"
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
	@scripts/host/experiment-stop.sh "$(TESTBED)" "$(CONFIG_DIR)"

config-create:
	@test -n "$(FROM)" || { echo "FROM=<repository-relative-scenario.yaml> is required" >&2; exit 2; }
	@case "$(FROM)" in /*) echo "FROM must be relative to the repository: $(FROM)" >&2; exit 2;; *.yaml) ;; *) echo "FROM must select a scenario.yaml file: $(FROM)" >&2; exit 2;; esac
	@case "$(DEVICE)" in gpu|cpu) ;; *) echo "DEVICE must be gpu or cpu" >&2; exit 2;; esac
	@case "$(WEBCONSOLE)" in false|true) ;; *) echo "WEBCONSOLE must be false or true" >&2; exit 2;; esac
	@python3 scripts/host/config-render.py --testbed "$(TESTBED)" --name "$(NAME)" \
		--scenario "$(FROM)" --output-root config/local --ml-device "$(DEVICE)" \
		--webconsole "$(WEBCONSOLE)"

config-validate:
	@python3 scripts/host/config-check.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)")

dataset-validate:
	@python3 scripts/host/dataset.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)") check

dataset-load:
	@scripts/host/dataset-stage.sh apply "$(TESTBED)" "$(CONFIG_DIR)"

reset-show:
	@scripts/host/experiment-reset.sh plan "$(TESTBED)" "$(CONFIG_DIR)"

reset:
	@RESET_CONFIRM="$(RESET_CONFIRM)" scripts/host/experiment-reset.sh apply "$(TESTBED)" "$(CONFIG_DIR)"
	@scripts/host/experiment-reset.sh verify "$(TESTBED)" "$(CONFIG_DIR)"

test:
	@tests/repository.sh "$(TESTBED)" "$(CONFIG_DIR)"

test-containers:
	@tests/ml-container-lifecycle.sh

dataset-generate:
	@python3 scripts/host/dataset.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)") generate

dataset-show:
	@python3 scripts/host/dataset.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)") show

ml-start:
	@scripts/host/ml-start.sh "$(TESTBED)" "$(CONFIG_DIR)"

ml-status:
	@scripts/host/ml-status.sh "$(TESTBED)" "$(CONFIG_DIR)"

ml-stop:
	@scripts/host/ml-stop.sh "$(TESTBED)" "$(CONFIG_DIR)"

webconsole-start:
	@scripts/host/webconsole-start.sh "$(TESTBED)" "$(CONFIG_DIR)"

webconsole-status:
	@scripts/host/webconsole-status.sh

webconsole-stop:
	@scripts/host/webconsole-stop.sh

vm-up:
	@source scripts/host/lib.sh; TESTBED="$(TESTBED)" provider_vagrant_up

vm-status:
	@source scripts/host/lib.sh; TESTBED="$(TESTBED)" provider_vagrant status

vm-halt:
	@source scripts/host/lib.sh; TESTBED="$(TESTBED)" provider_vagrant halt

services-start:
	@scripts/host/services-start.sh "$(TESTBED)" "$(CONFIG_DIR)"

services-status:
	@scripts/host/services-status.sh "$(TESTBED)" "$(CONFIG_DIR)"

services-stop:
	@scripts/host/services-stop.sh "$(TESTBED)" "$(CONFIG_DIR)"

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

observe:
	@scripts/host/observe.sh "$(TESTBED)" "$(CONFIG_DIR)"

logs: SOURCE ?= all
logs: VM ?= all
logs: SERVICE ?= all
logs: SINCE ?= 10 minutes ago
logs: TAIL ?= all
logs: FOLLOW ?= true
logs:
	@case "$(FOLLOW)" in \
		true) follow_args=() ;; \
		false) follow_args=(--no-follow) ;; \
		*) echo "FOLLOW must be true or false" >&2; exit 2 ;; \
	esac; \
	scripts/host/logs.sh \
		--testbed "$(TESTBED)" \
		--config-dir "$(CONFIG_DIR)" \
		--source "$(SOURCE)" \
		--vm "$(VM)" \
		--service "$(SERVICE)" \
		--since "$(SINCE)" \
		--tail "$(TAIL)" \
		"$${follow_args[@]}"
