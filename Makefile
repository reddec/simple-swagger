export VERSION ?= dev-$(shell git rev-parse --short HEAD)
export PROJECT ?= $(shell basename $(PWD))
ifndef GITHUB_REF
export PATH := $(PWD)/venv/bin:$(PATH)
endif

all: build

build: system-files python
	cd build && python3 -m build

ifdef GITHUB_REF
release-github: build
	cd build && twine upload -u __token__ dist/*
endif

clean:
	rm -rf build

dirs: clean
	mkdir -p build/$(PROJECT)

python: dirs
	cp -r $(PROJECT) build/

system-files: dirs
	cp LICENSE setup.py README.md MANIFEST.in build/

.PHONY: all build docs