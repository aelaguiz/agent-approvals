# agent-approvals: install the `approvals` command, the agent skill, and the background service.
# MACHINE names this machine for the fleet, once: make install MACHINE=<name>.
SHELL := /bin/bash

SKILL            := approvals
AGENTS_SKILLS_DIR ?= $(HOME)/.agents/skills
CLAUDE_SKILLS_DIR ?= $(HOME)/.claude/skills
APPROVALS_HOME   ?= $(HOME)/.approvals
BIN              := $(HOME)/.local/bin/approvals
LABEL            := com.agent-approvals.listener
# One service per Slack workspace: the main folder, plus each folder inside it with its own config.json.
HOMES            := $(APPROVALS_HOME) $(patsubst %/config.json,%,$(wildcard $(APPROVALS_HOME)/*/config.json))
LEGACY_LABELS    := com.funcountry.approvals
UNIT             := agent-approvals
SYSTEMD_DIR      := $(HOME)/.config/systemd/user
UID              := $(shell id -u)
TOOL_DIRS        := $(shell for t in herdr aim uv; do p=$$(command -v $$t 2>/dev/null) && dirname "$$p"; done | awk '!seen[$$0]++' | paste -sd: -)
SERVICE_PATH     := $(HOME)/.local/bin:$(TOOL_DIRS):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

# The main workspace's service is $(LABEL); another workspace's adds its folder name: $(LABEL).<name>.
# On Linux the systemd units are $(UNIT) and $(UNIT)-<name>.
label_for = if [ "$(1)" = "$(APPROVALS_HOME)" ]; then echo $(LABEL); else echo $(LABEL).$$(basename $(1)); fi
unit_for = if [ "$(1)" = "$(APPROVALS_HOME)" ]; then echo $(UNIT); else echo $(UNIT)-$$(basename $(1)); fi
# A workspace gets a service on this machine only when this machine can post as its bot: the full service on the
# workspace's hub, delivery to this machine's own sessions elsewhere. `approvals role` says which, or why neither.
served = { role=$$(APPROVALS_HOME=$(1) $(BIN) role); case "$$role" in hub|delivery) true;; *) echo "== $(1): no service here ($$role)"; false;; esac; }

.PHONY: install install_cli install_skill install_config machine_file install_service install_systemd verify_install uninstall test test-live

install: install_cli install_skill install_config machine_file install_service
	@for home in $(HOMES); do $(call served,$$home) || continue; echo "== $$home"; APPROVALS_HOME=$$home $(BIN) doctor || exit 1; done
	@echo "Installed. Run 'make verify_install' to check everything."

install_cli:
	uv tool install --force --reinstall .

install_skill:
	@for dir in "$(AGENTS_SKILLS_DIR)" "$(CLAUDE_SKILLS_DIR)"; do \
		mkdir -p "$$dir"; \
		rm -rf "$$dir/$(SKILL)"; \
		cp -R "skills/$(SKILL)" "$$dir/$(SKILL)"; \
		find "$$dir/$(SKILL)" -name __pycache__ -type d -prune -exec rm -rf {} +; \
		echo "skill installed in $$dir/$(SKILL)"; \
	done

install_config:
	@mkdir -p "$(APPROVALS_HOME)/requests" "$(APPROVALS_HOME)/logs"
	@if [ ! -f "$(APPROVALS_HOME)/config.json" ]; then \
		cp config.example.json "$(APPROVALS_HOME)/config.json"; \
		echo "Created $(APPROVALS_HOME)/config.json from config.example.json."; \
		echo "Fill in the channel, approver and token files (see README), then run 'make install' again."; \
		exit 1; \
	fi

machine_file:
	@if [ -n "$(MACHINE)" ]; then \
		mkdir -p "$(APPROVALS_HOME)"; printf '{"name": "%s"}\n' "$(MACHINE)" > "$(APPROVALS_HOME)/machine.json"; \
		echo "this machine is $(MACHINE)"; \
	elif [ ! -f "$(APPROVALS_HOME)/machine.json" ]; then \
		echo "Say which machine this is: make install MACHINE=<name>"; exit 1; \
	fi

install_service:
	@if [ "$$(uname)" != "Darwin" ]; then $(MAKE) --no-print-directory install_systemd; exit $$?; fi
	@for l in $(LEGACY_LABELS); do launchctl bootout gui/$(UID)/$$l 2>/dev/null || true; rm -f "$(HOME)/Library/LaunchAgents/$$l.plist"; done
	@mkdir -p "$(HOME)/Library/LaunchAgents"
	@# A running service whose definition is unchanged is only restarted. Registering one afresh (bootstrap) is
	@# refused from some agent sessions, which left a workspace's service down after an install.
	@for home in $(HOMES); do \
		label=$$($(call label_for,$$home)); plist="$(HOME)/Library/LaunchAgents/$$label.plist"; \
		if ! $(call served,$$home); then launchctl bootout gui/$(UID)/$$label 2>/dev/null || true; rm -f "$$plist"; continue; fi; \
		mkdir -p "$$home/logs"; \
		new=$$(sed -e "s|@LABEL@|$$label|g" -e 's|@BIN@|$(BIN)|g' -e 's|@HOME@|$(HOME)|g' \
		    -e "s|@APPROVALS_HOME@|$$home|g" -e 's|@PATH@|$(SERVICE_PATH)|g' launchd/listener.plist.in); \
		if launchctl print gui/$(UID)/$$label >/dev/null 2>&1 && [ "$$new" = "$$(cat "$$plist" 2>/dev/null)" ]; then \
			launchctl kickstart -k gui/$(UID)/$$label || { echo "FAIL: service $$label did not restart"; exit 1; }; \
			echo "service $$label restarted"; continue; \
		fi; \
		launchctl bootout gui/$(UID)/$$label 2>/dev/null || true; \
		for i in $$(seq 50); do launchctl print gui/$(UID)/$$label >/dev/null 2>&1 || break; sleep 0.2; done; \
		printf '%s\n' "$$new" > "$$plist"; \
		launchctl bootstrap gui/$(UID) "$$plist" || { echo "FAIL: service $$label did not start; run 'launchctl bootstrap gui/$(UID) $$plist' in a plain terminal"; exit 1; }; \
		echo "service $$label started"; \
	done

install_systemd:
	@mkdir -p "$(SYSTEMD_DIR)"
	@for home in $(HOMES); do \
		unit=$$($(call unit_for,$$home)); file="$(SYSTEMD_DIR)/$$unit.service"; \
		if ! $(call served,$$home); then systemctl --user disable --now $$unit 2>/dev/null || true; rm -f "$$file"; continue; fi; \
		mkdir -p "$$home/logs"; \
		sed -e 's|@BIN@|$(BIN)|g' -e 's|@HOME@|$(HOME)|g' -e "s|@APPROVALS_HOME@|$$home|g" -e 's|@PATH@|$(SERVICE_PATH)|g' \
		    systemd/listener.service.in > "$$file"; \
		systemctl --user daemon-reload && systemctl --user enable $$unit && systemctl --user restart $$unit \
		    || { echo "FAIL: service $$unit did not start"; exit 1; }; \
		echo "service $$unit started"; \
	done

verify_install:
	@for dir in "$(AGENTS_SKILLS_DIR)" "$(CLAUDE_SKILLS_DIR)"; do \
		diff -rq "skills/$(SKILL)" "$$dir/$(SKILL)" >/dev/null || { echo "FAIL: $$dir/$(SKILL) is missing or differs from the repo"; exit 1; }; \
	done
	@command -v approvals >/dev/null || { echo "FAIL: approvals is not on PATH"; exit 1; }
	@for home in $(HOMES); do \
		$(call served,$$home) || continue; \
		label=$$($(call label_for,$$home)); unit=$$($(call unit_for,$$home)); \
		if [ "$$(uname)" = "Darwin" ]; then \
			launchctl print gui/$(UID)/$$label 2>/dev/null | grep -q 'state = running' || { echo "FAIL: service $$label is not running"; exit 1; }; \
		else \
			systemctl --user is-active --quiet $$unit || { echo "FAIL: service $$unit is not running"; exit 1; }; \
		fi; \
		echo "== $$home"; APPROVALS_HOME=$$home $(BIN) doctor || exit 1; \
	done
	@echo "OK: skill, command, services and Slack access all check out"

uninstall:
	@for home in $(HOMES); do \
		label=$$($(call label_for,$$home)); unit=$$($(call unit_for,$$home)); \
		if [ "$$(uname)" = "Darwin" ]; then \
			launchctl bootout gui/$(UID)/$$label 2>/dev/null || true; rm -f "$(HOME)/Library/LaunchAgents/$$label.plist"; \
		else \
			systemctl --user disable --now $$unit 2>/dev/null || true; rm -f "$(SYSTEMD_DIR)/$$unit.service"; \
		fi; \
	done
	@rm -rf "$(AGENTS_SKILLS_DIR)/$(SKILL)" "$(CLAUDE_SKILLS_DIR)/$(SKILL)"
	-@uv tool uninstall approvals
	@echo "Removed the service, skill and command. Records and config remain in $(APPROVALS_HOME)."

test:
	uv run pytest -q

test-live:
	@echo "The live end-to-end scenarios use real agent sessions and Slack. See docs/testing.md."
