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
        fmt = definition.get('format')
        if fmt == 'date-time':
            return 'time.Time'
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
    if type_name == 'time.Time':
        return f'time.Parse(time.RFC3339, {param})'
    if type_name == 'bool':
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


def default_value(definition: dict, swagger: dict) -> str:
    if 'schema' in definition:
        # work-around for parameters
        return default_value(definition['schema'])

    ref = definition.get('$ref')
    if ref is not None:
        type_name = ref.split('/')[-1]
        child = swagger.get('definitions', {}).get(type_name, {})
        if child.get("type", 'object') == 'object':
            return type_name + "{}"
        return default_value(child, swagger)

    type_name = definition['type']

    if type_name == 'string':
        fmt = definition.get('format')
        if fmt == 'date-time':
            return 'time.Time{}'
        return '""'
    if type_name == 'boolean':
        return 'false'
    if type_name == 'array':
        return 'nil'
    if type_name == 'number' or type_name == 'integer':
        return '0'
    return type_name + '"{}"'


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


def label(text: str) -> str:
    if '_' in text:
        text = "".join(label(t) for t in text.split('_'))
    text = text[0].upper() + text[1:]
    return text


def private(text: str) -> str:
    if '_' in text:
        text = "".join(label(t) for t in text.split('_'))
    text = text[0].lower() + text[1:]
    return text


def comment(text: str) -> str:
    return '\n'.join('// ' + line for line in text.splitlines())


def resolve(definition: dict, swagger: dict):
    if 'schema' in definition:
        return resolve(definition['schema'], swagger)
    if '$ref' in definition:
        name = definition['$ref'].split('/')[-1]
        return swagger['definitions'][name]
    return definition


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

    patterns_cache = defaultdict(lambda: f"pattern{len(patterns_cache)}")

    env = Environment(loader=FileSystemLoader(args.templates))
    env.filters['map_type'] = map_type
    env.filters['label'] = label
    env.filters['private'] = private
    env.filters['path'] = path
    env.filters['cast'] = cast
    env.filters['comment'] = comment
    env.filters['from_string'] = from_string
    env.filters['to_string'] = to_string
    env.filters['default_value'] = lambda x: default_value(x, swagger)
    env.filters['secured'] = lambda x: len(x.get('security', [])) > 0
    env.filters['sec_def'] = lambda x: swagger['securityDefinitions'][x]
    env.filters['resolve'] = lambda x: resolve(x, swagger)
    env.filters['patterns'] = lambda x: patterns_cache[x]
    env.filters['has_payload'] = lambda x: any(param for param in x.get('parameters', []) if param['in'] == 'body')
    env.filters['is_ref_to_type'] = lambda x: '$ref' in x or '$ref' in x.get('schema', {})
    env.filters['has_query_params'] = lambda x: any(
        param for param in x.get('parameters', []) if param['in'] == 'query')
    # filter params by place (body, query, ...)
    env.filters['inside'] = lambda params, place: (p for p in params if p['in'] == place)
    swagger = safe_load(args.swagger.read_text())

    security_type = swagger.get('x-go-credential-type', args.credential)

    # apply default security
    default_security = swagger.get('security', [])
    if len(default_security) > 0:
        for methods in swagger.get('paths', {}).values():
            for endpoint in methods.values():
                if 'security' not in endpoint:
                    endpoint['security'] = default_security

    enums = tuple(iter_enums(swagger))

    base_file = args.output / "interfaces.go"
    validations_file = args.output / "validations.go"
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

    type_aliases = dict((name, definition) for (name, definition) in swagger.get('definitions', {}).items()
                        if 'enum' not in definition and definition.get('type', '') != 'object')

    base_file.write_text(env.get_template('base.jinja2').render(
        header=f"// Code generated by simple-swagger {' '.join(sys.argv[1:])} DO NOT EDIT.",
        swagger=swagger,
        package=package,
        credential_type=security_type,
        api_package=api_package,
        enums=enums,
        type_aliases=type_aliases,
        has_security=len(
            swagger.get('securityDefinitions', {})) > 0,
        tags=tags,
        methods=methods,
    ))

    validations_file.write_text(env.get_template('validations.jinja2').render(
        header=f"// Code generated by simple-swagger {' '.join(sys.argv[1:])} DO NOT EDIT.",
        swagger=swagger,
        package=package,
        credential_type=security_type,
        api_package=api_package,
        enums=enums,
        type_aliases=type_aliases,
        has_security=len(
            swagger.get('securityDefinitions', {})) > 0,
        tags=tags,
        methods=methods,
        patterns_cache=patterns_cache,
    ))

    server_file.write_text(env.get_template('server.jinja2').render(
        header=f"// Code generated by simple-swagger {' '.join(sys.argv[1:])} DO NOT EDIT.",
        swagger=swagger,
        package="server",
        enums=enums,
        credential_type=security_type,
        api_package=api_package,
        has_security=len(
            swagger.get('securityDefinitions', {})) > 0))

    client_file.write_text(env.get_template('client.jinja2').render(
        header=f"// Code generated by simple-swagger {' '.join(sys.argv[1:])} DO NOT EDIT.",
        swagger=swagger,
        package="client",
        enums=enums,
        credential_type=security_type,
        api_package=api_package,
        has_security=len(
            swagger.get('securityDefinitions', {})) > 0))

    files = [
        str(base_file),
        str(validations_file),
        str(client_file),
        str(server_file)
    ]

    try:
        check_call(['goimports', '-w'] + files)
    except SubprocessError:
        check_call(['gofmt', '-w', '-s'] + files)


if __name__ == '__main__':
    main()
