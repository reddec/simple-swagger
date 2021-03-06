export VERSION ?= dev-$(shell git rev-parse --short HEAD)
export PROJECT ?= simpleswagger
OPATH := $(PATH)
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

install: build
	PATH=$(OPATH) pip3 install --user --force-reinstall build/dist/simple_swagger-dev_*-py3-none-any.whl

test-gen:
	rm -rf test-api && mkdir -p test-api
	echo 'module testapi' > test-api/go.mod
	echo 'go 1.16' >> test-api/go.mod
	./simpleswagger/generator.py -s test-data/swagger.yaml -o test-api
.PHONY: all build docs