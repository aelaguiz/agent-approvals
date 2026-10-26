# agent-approvals: install the `approvals` command, the agent skill, and the background service.
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
UID              := $(shell id -u)
TOOL_DIRS        := $(shell for t in herdr aim uv; do p=$$(command -v $$t 2>/dev/null) && dirname "$$p"; done | awk '!seen[$$0]++' | paste -sd: -)
SERVICE_PATH     := $(HOME)/.local/bin:$(TOOL_DIRS):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

# The main workspace's service is $(LABEL); another workspace's adds its folder name: $(LABEL).<name>.
label_for = if [ "$(1)" = "$(APPROVALS_HOME)" ]; then echo $(LABEL); else echo $(LABEL).$$(basename $(1)); fi

.PHONY: install install_cli install_skill install_config install_service verify_install uninstall test test-live

install: install_cli install_skill install_config install_service
	@for home in $(HOMES); do echo "== $$home"; APPROVALS_HOME=$$home $(BIN) doctor || exit 1; done
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

install_service:
	@if [ "$$(uname)" != "Darwin" ]; then echo "The service install is macOS-only; run 'approvals listen' under your own supervisor."; exit 0; fi
	@for l in $(LEGACY_LABELS); do launchctl bootout gui/$(UID)/$$l 2>/dev/null || true; rm -f "$(HOME)/Library/LaunchAgents/$$l.plist"; done
	@mkdir -p "$(HOME)/Library/LaunchAgents"
	@for home in $(HOMES); do \
		label=$$($(call label_for,$$home)); plist="$(HOME)/Library/LaunchAgents/$$label.plist"; \
		launchctl bootout gui/$(UID)/$$label 2>/dev/null || true; \
		mkdir -p "$$home/logs"; \
		sed -e "s|@LABEL@|$$label|g" -e 's|@BIN@|$(BIN)|g' -e 's|@HOME@|$(HOME)|g' \
		    -e "s|@APPROVALS_HOME@|$$home|g" -e 's|@PATH@|$(SERVICE_PATH)|g' \
		    launchd/listener.plist.in > "$$plist"; \
		launchctl bootstrap gui/$(UID) "$$plist"; \
		echo "service $$label started"; \
	done

verify_install:
	@for dir in "$(AGENTS_SKILLS_DIR)" "$(CLAUDE_SKILLS_DIR)"; do \
		diff -rq "skills/$(SKILL)" "$$dir/$(SKILL)" >/dev/null || { echo "FAIL: $$dir/$(SKILL) is missing or differs from the repo"; exit 1; }; \
	done
	@command -v approvals >/dev/null || { echo "FAIL: approvals is not on PATH"; exit 1; }
	@for home in $(HOMES); do \
		label=$$($(call label_for,$$home)); \
		if [ "$$(uname)" = "Darwin" ]; then \
			launchctl print gui/$(UID)/$$label 2>/dev/null | grep -q 'state = running' || { echo "FAIL: service $$label is not running"; exit 1; }; \
		fi; \
		echo "== $$home"; APPROVALS_HOME=$$home $(BIN) doctor || exit 1; \
	done
	@echo "OK: skill, command, services and Slack access all check out"

uninstall:
	@for home in $(HOMES); do \
		label=$$($(call label_for,$$home)); \
		launchctl bootout gui/$(UID)/$$label 2>/dev/null || true; \
		rm -f "$(HOME)/Library/LaunchAgents/$$label.plist"; \
	done
	@rm -rf "$(AGENTS_SKILLS_DIR)/$(SKILL)" "$(CLAUDE_SKILLS_DIR)/$(SKILL)"
	-@uv tool uninstall approvals
	@echo "Removed the service, skill and command. Records and config remain in $(APPROVALS_HOME)."

test:
	uv run pytest -q

test-live:
	@echo "The live end-to-end scenarios use real agent sessions and Slack. See docs/testing.md."
