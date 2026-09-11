SHELL := /usr/bin/env bash
TESTBED ?= testbed.protocol-hierarchical.yaml
CONFIG_DIR ?=
NAME ?= protocol-hierarchical
DEVICE ?= cpu
WEBCONSOLE ?= false
FORCE ?= false

require_testbed = @source scripts/host/lib.sh; require_testbed_selection "$(TESTBED)"

.PHONY: help help-advanced help-dev help-all experiment-validate experiment-start experiment-status experiment-stop \
	config-create config-validate dataset-validate dataset-load reset-show reset test test-containers \
	dataset-generate dataset-show \
	ml-start ml-status ml-stop vm-up vm-status vm-halt \
	webconsole-start webconsole-status webconsole-stop \
	services-start services-status services-stop subscriptions-start \
	subscriptions-status subscriptions-stop subscriber-data-apply \
	subscriber-data-show subscriber-data-clear fl-collection-start fl-collection-status \
	fl-collection-stop fl-training-start fl-training-status observe logs

help:
	@echo "5G NWDAF Infrastructure"
	@echo ""
	@echo "Experiment lifecycle"
	@echo "  make experiment-validate [TESTBED=...] [CONFIG_DIR=...]  Diagnose prerequisites and inputs"
	@echo "  make vm-up [TESTBED=...]                                Create or start the selected VMs"
	@echo "  make experiment-start [TESTBED=...] [CONFIG_DIR=...]   Start the selected experiment"
	@echo "  make experiment-status [TESTBED=...] [CONFIG_DIR=...]  Show the complete experiment state"
	@echo "  make logs TESTBED=... [SERVICE=...]                  Follow selected logs; defaults to all"
	@echo "  make experiment-stop [TESTBED=...] [CONFIG_DIR=...]    Stop processes but retain state and VMs"
	@echo "  make vm-halt [TESTBED=...]                             Gracefully power off the selected VMs"
	@echo ""
	@echo "More commands: make help-advanced | help-dev | help-all"

help-advanced:
	@echo "5G NWDAF Infrastructure — advanced operations"
	@echo ""
	@echo "Configuration and datasets"
	@echo "  make config-create TESTBED=... NAME=... FROM=experiments/.../scenario.yaml"
	@echo "                     [DEVICE=gpu|cpu] [WEBCONSOLE=false|true] [FORCE=false|true]"
	@echo "  make config-validate TESTBED=... [CONFIG_DIR=...]  Diagnose cross-config inconsistencies"
	@echo "  make dataset-generate | dataset-validate | dataset-show | dataset-load TESTBED=... [CONFIG_DIR=...]"
	@echo ""
	@echo "Independent execution domains"
	@echo "  make services-start | services-status | services-stop TESTBED=... [CONFIG_DIR=...]"
	@echo "  make ml-start | ml-status | ml-stop TESTBED=... [CONFIG_DIR=...]"
	@echo "  make webconsole-start TESTBED=... [CONFIG_DIR=...] | webconsole-status | webconsole-stop"
	@echo "  make subscriptions-start | subscriptions-status | subscriptions-stop"
	@echo "  make fl-collection-start|status|stop TESTBED=... [CONFIG_DIR=...] RUN_ID=<uuid>"
	@echo "  make fl-training-start|status TESTBED=... [CONFIG_DIR=...] RUN_ID=<uuid> [MODEL_FAMILY_ID=...]"
	@echo "  make observe TESTBED=... [CONFIG_DIR=...]"
	@echo "  make logs TESTBED=... [CONFIG_DIR=...] [SOURCE=vm|ml|all] [VM=selected-machine|all] [SERVICE=name|glob|all]"
	@echo "            [SINCE='10 minutes ago'] [TAIL=lines|all] [FOLLOW=true|false]"
	@echo ""
	@echo "Subscriber and retained experiment state"
	@echo "  make subscriber-data-show | subscriber-data-apply | subscriber-data-clear TESTBED=... [CONFIG_DIR=...]"
	@echo "  make reset-show TESTBED=... [CONFIG_DIR=...]"
	@echo "  make reset TESTBED=... [CONFIG_DIR=...] RESET_CONFIRM=<scenario>"

help-dev:
	@echo "5G NWDAF Infrastructure — repository tests"
	@echo "  make test             Run static and host-only repository checks"
	@echo "  make test-containers  Run disposable Flat/HFL CPU container lifecycle tests"

help-all: help
	@echo ""
	@$(MAKE) --no-print-directory help-advanced
	@echo ""
	@$(MAKE) --no-print-directory help-dev

experiment-validate:
	$(require_testbed)
	@scripts/host/experiment-validate.sh "$(TESTBED)" "$(CONFIG_DIR)"

experiment-start:
	$(require_testbed)
	@scripts/host/experiment-start.sh "$(TESTBED)" "$(CONFIG_DIR)"

experiment-status:
	$(require_testbed)
	@RUN_ID="$(RUN_ID)" MODEL_FAMILY_ID="$(MODEL_FAMILY_ID)" \
		scripts/host/experiment-status.sh "$(TESTBED)" "$(CONFIG_DIR)"

experiment-stop:
	$(require_testbed)
	@scripts/host/experiment-stop.sh "$(TESTBED)" "$(CONFIG_DIR)"

config-create:
	$(require_testbed)
	@test -n "$(FROM)" || { echo "FROM=<repository-relative-scenario.yaml> is required" >&2; exit 2; }
	@case "$(FROM)" in /*) echo "FROM must be relative to the repository: $(FROM)" >&2; exit 2;; *.yaml) ;; *) echo "FROM must select a scenario.yaml file: $(FROM)" >&2; exit 2;; esac
	@case "$(DEVICE)" in gpu|cpu) ;; *) echo "DEVICE must be gpu or cpu" >&2; exit 2;; esac
	@case "$(WEBCONSOLE)" in false|true) ;; *) echo "WEBCONSOLE must be false or true" >&2; exit 2;; esac
	@case "$(FORCE)" in false|true) ;; *) echo "FORCE must be false or true" >&2; exit 2;; esac
	@python3 scripts/host/config-render.py --testbed "$(TESTBED)" --name "$(NAME)" \
		--scenario "$(FROM)" --output-root config/local --ml-device "$(DEVICE)" \
		--webconsole "$(WEBCONSOLE)" $(if $(filter true,$(FORCE)),--force)

config-validate:
	$(require_testbed)
	@python3 scripts/host/config-check.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)")

dataset-validate:
	$(require_testbed)
	@python3 scripts/host/dataset.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)") check

dataset-load:
	$(require_testbed)
	@scripts/host/dataset-stage.sh apply "$(TESTBED)" "$(CONFIG_DIR)"

reset-show:
	$(require_testbed)
	@scripts/host/experiment-reset.sh plan "$(TESTBED)" "$(CONFIG_DIR)"

reset:
	$(require_testbed)
	@RESET_CONFIRM="$(RESET_CONFIRM)" scripts/host/experiment-reset.sh apply "$(TESTBED)" "$(CONFIG_DIR)"
	@scripts/host/experiment-reset.sh verify "$(TESTBED)" "$(CONFIG_DIR)"

test:
	# Repository checks use the retained production definition only as a fixture;
	# this explicit fixture is not reachable from a deployment lifecycle target.
	@tests/repository.sh testbed.yaml

test-containers:
	@tests/ml-container-lifecycle.sh

dataset-generate:
	$(require_testbed)
	@python3 scripts/host/dataset.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)") generate

dataset-show:
	$(require_testbed)
	@python3 scripts/host/dataset.py --testbed "$(TESTBED)" $(if $(CONFIG_DIR),--config-dir "$(CONFIG_DIR)") show

ml-start:
	$(require_testbed)
	@scripts/host/ml-start.sh "$(TESTBED)" "$(CONFIG_DIR)"

ml-status:
	$(require_testbed)
	@scripts/host/ml-status.sh "$(TESTBED)" "$(CONFIG_DIR)"

ml-stop:
	$(require_testbed)
	@scripts/host/ml-stop.sh "$(TESTBED)" "$(CONFIG_DIR)"

webconsole-start:
	$(require_testbed)
	@scripts/host/webconsole-start.sh "$(TESTBED)" "$(CONFIG_DIR)"

webconsole-status:
	@scripts/host/webconsole-status.sh

webconsole-stop:
	@scripts/host/webconsole-stop.sh

vm-up:
	@source scripts/host/lib.sh; require_testbed_selection "$(TESTBED)"; TESTBED="$(TESTBED)" provider_vagrant_up

vm-status:
	@source scripts/host/lib.sh; require_testbed_selection "$(TESTBED)"; TESTBED="$(TESTBED)" provider_runtime_state_records

vm-halt:
	@source scripts/host/lib.sh; require_testbed_selection "$(TESTBED)"; TESTBED="$(TESTBED)" provider_vagrant_halt

services-start:
	$(require_testbed)
	@scripts/host/services-start.sh "$(TESTBED)" "$(CONFIG_DIR)"

services-status:
	$(require_testbed)
	@scripts/host/services-status.sh "$(TESTBED)" "$(CONFIG_DIR)"

services-stop:
	$(require_testbed)
	@scripts/host/services-stop.sh "$(TESTBED)" "$(CONFIG_DIR)"

subscriber-data-apply:
	$(require_testbed)
	@scripts/host/subscriber-data.sh apply "$(TESTBED)" "$(CONFIG_DIR)"

subscriber-data-show:
	$(require_testbed)
	@scripts/host/subscriber-data.sh show "$(TESTBED)" "$(CONFIG_DIR)"

subscriber-data-clear:
	$(require_testbed)
	@scripts/host/subscriber-data.sh clear "$(TESTBED)" "$(CONFIG_DIR)"

subscriptions-start:
	@scripts/host/subscriptions-start.sh

subscriptions-status:
	@scripts/host/subscriptions-status.sh

subscriptions-stop:
	@scripts/host/subscriptions-stop.sh

fl-collection-start:
	$(require_testbed)
	@python3 scripts/host/fl-control.py collection-start --testbed "$(TESTBED)" --config-dir "$(CONFIG_DIR)" --run-id "$(RUN_ID)"

fl-collection-status:
	$(require_testbed)
	@python3 scripts/host/fl-control.py collection-status --testbed "$(TESTBED)" --config-dir "$(CONFIG_DIR)" --run-id "$(RUN_ID)"

fl-collection-stop:
	$(require_testbed)
	@python3 scripts/host/fl-control.py collection-stop --testbed "$(TESTBED)" --config-dir "$(CONFIG_DIR)" --run-id "$(RUN_ID)"

fl-training-start:
	$(require_testbed)
	@python3 scripts/host/fl-control.py training-start --testbed "$(TESTBED)" --config-dir "$(CONFIG_DIR)" --run-id "$(RUN_ID)" $(if $(MODEL_FAMILY_ID),--model-family-id "$(MODEL_FAMILY_ID)")

fl-training-status:
	$(require_testbed)
	@python3 scripts/host/fl-control.py training-status --testbed "$(TESTBED)" --config-dir "$(CONFIG_DIR)" --run-id "$(RUN_ID)" $(if $(MODEL_FAMILY_ID),--model-family-id "$(MODEL_FAMILY_ID)")

observe:
	$(require_testbed)
	@scripts/host/observe.sh "$(TESTBED)" "$(CONFIG_DIR)"

logs: SOURCE ?= all
logs: VM ?= all
logs: SERVICE ?= all
logs: SINCE ?= 10 minutes ago
logs: TAIL ?= all
logs: FOLLOW ?= true
logs:
	$(require_testbed)
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
