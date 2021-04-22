#!/usr/bin/env python3
from argparse import ArgumentParser
from pathlib import Path
from collections import defaultdict
from subprocess import check_call, SubprocessError
import sys
from jinja2 import Environment, FileSystemLoader
from yaml import safe_load
import re


def map_type(definition: dict, imported: bool = False):
    if 'schema' in definition:
        # work-around for parameters
        return map_type(definition['schema'], imported)

    ref = definition.get('$ref')
    if ref is not None:
        type_name = ref.split('/')[-1]
        if imported:
            return 'api.' + type_name
        return type_name

    type_name = definition['type']

    if type_name == 'string':
        return 'string'
    if type_name == 'boolean':
        return 'bool'
    if type_name == 'array':
        return '[]' + map_type(definition['items'], imported)
    if type_name == 'number':
        fmt = definition.get('format')
        if fmt == 'float':
            return 'float32'
        return 'float64'

    if type_name == 'integer':
        fmt = definition.get('format')
        minimum = definition.get('minimum')
        prefix = ''
        if minimum == 0:
            prefix = 'u'
        if fmt == 'int32':
            return prefix + 'int32'
        if fmt == 'int64':
            return prefix + 'int64'
        return prefix + 'int'

    return 'interface{}'


def from_string(definition: dict, param: str) -> str:
    type_name = map_type(definition)
    if type_name == 'boolean':
        return f'strconv.ParseBool({param})'
    if type_name == 'float64':
        return f'strconv.ParseFloat({param}, 64)'
    if type_name == 'float32':
        return f'strconv.ParseFloat({param}, 32)'
    if type_name == 'int64' or type_name == 'int':
        return f'strconv.ParseInt({param}, 10, 64)'
    if type_name == 'int32':
        return f'strconv.ParseInt({param}, 10, 32)'
    if type_name == 'uint64' or type_name == 'uint':
        return f'strconv.ParseUint({param}, 10, 64)'
    if type_name == 'uint32':
        return f'strconv.ParseUint({param}, 10, 32)'
    return param


def to_string(definition: dict, param: str) -> str:
    type_name = map_type(definition)
    if type_name == 'string':
        return param
    if type_name == 'boolean':
        return f'strconv.FormatBool({param})'
    if type_name == 'float64':
        return f'strconv.FormatFloat({param}, \'f\', -1, 32)'
    if type_name == 'float32':
        return f'strconv.FormatFloat(float64({param}), \'f\', -1, 64)'
    if type_name == 'int64':
        return f'strconv.FormatInt({param}, 10)'
    if type_name == 'int32' or type_name == 'int':
        return f'strconv.FormatInt(int64({param}), 10)'
    if type_name == 'uint64':
        return f'strconv.FormatUint({param}, 10)'
    if type_name == 'uint32' or type_name == 'uint':
        return f'strconv.FormatUint(uint64({param}), 10)'
    return f'fmt.Sprint({param})'


def path(text: str) -> str:
    return re.sub(r'{(.*?)}', ':\\1', text)


def detect_package(location: Path) -> str:
    if not location.is_absolute():
        location = location.absolute()
    if location.parent == location:
        raise FileNotFoundError("go.mod not found in all hierarchy")

    go_mod = location / 'go.mod'
    try:
        content = go_mod.read_text()
        return re.findall(r'^module\s+"?(.*?)"?$', content, re.MULTILINE | re.DOTALL)[0]

    except FileNotFoundError:
        return detect_package(location.parent) + "/" + location.name


def iter_enums(swagger: dict):
    for name, definition in swagger.get('definitions', {}).items():
        if 'enum' in definition:
            yield name, definition


def cast(value, definition: dict) -> str:
    if definition.get('type') == 'string':
        return f'"{value}"'  # TODO: fix to proper escaping
    return value


def main():
    parser = ArgumentParser(description='Zombie swagger 2.0')
    parser.add_argument('--swagger', '-s', type=Path, default=(Path.cwd() / "swagger.yaml"),
                        help='Location of swagger file')
    parser.add_argument('--output', '-o', type=Path, default=(Path.cwd() / "api"),
                        help='Output directory')
    parser.add_argument('--templates', '-t', type=Path, default=(Path(__file__).parent.absolute() / 'templates'),
                        help='Templates location')
    parser.add_argument('--credential', '-c', type=str, default='interface{}', help='Credential type')
    args = parser.parse_args()

    env = Environment(loader=FileSystemLoader(args.templates))
    env.filters['map_type'] = map_type
    env.filters['label'] = lambda x: x[0].upper() + x[1:]
    env.filters['path'] = path
    env.filters['cast'] = cast
    env.filters['from_string'] = from_string
    env.filters['to_string'] = to_string
    env.filters['secured'] = lambda x: len(x.get('security', [])) > 0
    env.filters['sec_def'] = lambda x: swagger['securityDefinitions'][x]
    env.filters['has_payload'] = lambda x: any(param for param in x.get('parameters', []) if param['in'] == 'body')
    swagger = safe_load(args.swagger.read_text())

    enums = tuple(iter_enums(swagger))

    base_file = args.output / "interfaces.go"
    client_file = args.output / "client" / "client.go"
    server_file = args.output / "server" / "server.go"
    base_file.parent.absolute().mkdir(parents=True, exist_ok=True)
    client_file.parent.absolute().mkdir(parents=True, exist_ok=True)
    server_file.parent.absolute().mkdir(parents=True, exist_ok=True)
    api_package = detect_package(args.output)
    package = api_package.split('/')[-1]

    tags = defaultdict(set)
    methods = {}
    for http_methods in swagger.get('paths', {}).values():
        for endpoint in http_methods.values():
            methods[endpoint['operationId']] = endpoint
            for tag in endpoint.get('tags', []):
                tags[tag].add(endpoint['operationId'])

    base_file.write_text(env.get_template('base.jinja2').render(
        header=f"// Code generated by simple-swagger {' '.join(sys.argv[1:])} DO NOT EDIT.",
        swagger=swagger,
        package=package,
        credential_type=args.credential,
        api_package=api_package,
        enums=enums,
        has_security=len(
            swagger.get('securityDefinitions', {})) > 0,
        tags=tags,
        methods=methods,
    ))

    server_file.write_text(env.get_template('server.jinja2').render(
        header=f"// Code generated by simple-swagger {' '.join(sys.argv[1:])} DO NOT EDIT.",
        swagger=swagger,
        package="server",
        enums=enums,
        credential_type=args.credential,
        api_package=api_package,
        has_security=len(
            swagger.get('securityDefinitions', {})) > 0))

    client_file.write_text(env.get_template('client.jinja2').render(
        header=f"// Code generated by simple-swagger {' '.join(sys.argv[1:])} DO NOT EDIT.",
        swagger=swagger,
        package="client",
        enums=enums,
        credential_type=args.credential,
        api_package=api_package,
        has_security=len(
            swagger.get('securityDefinitions', {})) > 0))

    files = [
        str(base_file),
        str(client_file),
        str(server_file)
    ]

    try:
        check_call(['goimports', '-w'] + files)
    except SubprocessError:
        check_call(['gofmt', '-w', '-s'] + files)


if __name__ == '__main__':
    main()
