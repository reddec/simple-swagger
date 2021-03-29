export VERSION ?= dev-$(shell git rev-parse --short HEAD)
export PROJECT ?= $(basename $(PWD))
ifndef GITHUB_REF
export PATH := $(PWD)/venv/bin:$(PATH)
endif

all: build

build: system-files python ui test
	cd build && python3 -m build

ifdef GITHUB_REF
release-github: build
	cd build && twine upload -u __token__ dist/*
endif


dirs:
	rm -rf build
	mkdir -p build/binp

python: dirs
	cp -r $(PROJECT) build/

system-files: dirs
	cp LICENSE setup.py README.md MANIFEST.in build/

test:
	python3 -m unittest discover -s tests

docs:
	rm -rf docs/_build
	cd docs && SOURCEDIR=../$(PROJECT) $(MAKE) html

.PHONY: all build docs