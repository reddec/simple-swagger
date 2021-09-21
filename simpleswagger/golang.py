import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from subprocess import check_call, SubprocessError
from typing import Optional, List

from jinja2 import Environment


@dataclass(frozen=True)
class GoType:
    type: str
    import_path: str = ''
    suggested_package: Optional[str] = None

    @property
    def fqdn(self) -> str:
        package = self.package
        if package != "":
            return package + '.' + self.type
        return self.type

    @property
    def package(self) -> str:
        if self.suggested_package is not None:
            return self.suggested_package
        if self.import_path != "":
            return self.import_path.split('/')[-1].replace('-', '_').lower()
        return ''

    @staticmethod
    def parse(qualified_name: str, suggested_package: Optional[str] = None) -> 'GoType':
        if '.' in qualified_name:
            imp, tp = qualified_name.rsplit('.', 1)
            return GoType(tp, imp, suggested_package)
        return GoType(qualified_name, suggested_package=suggested_package)


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
        return default_value(definition['schema'], swagger)

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


def formatter(files: List[str], *gofmt: List[str]):
    for app in gofmt:
        try:
            check_call(app + files)
            return
        except SubprocessError:
            pass


def render(swagger: dict, env: Environment, output: Path):
    env.filters['map_type'] = map_type
    env.filters['label'] = label
    env.filters['private'] = private
    env.filters['path'] = path
    env.filters['from_string'] = from_string
    env.filters['to_string'] = to_string
    env.filters['default_value'] = lambda x: default_value(x, swagger)
    env.filters['patterns'] = lambda x: patterns_cache[x]
    security_type = GoType.parse(swagger.get('x-go-credential-type', 'Credential'), 'security')

    base_file = output / "interfaces.go"
    validations_file = output / "validations.go"
    client_file = output / "client" / "client.go"
    server_file = output / "server" / "server.go"
    base_file.parent.absolute().mkdir(parents=True, exist_ok=True)
    client_file.parent.absolute().mkdir(parents=True, exist_ok=True)
    server_file.parent.absolute().mkdir(parents=True, exist_ok=True)
    api_package = detect_package(output)
    package = api_package.split('/')[-1]
    patterns_cache = defaultdict(lambda: f"pattern{len(patterns_cache)}")

    base_file.write_text(env.get_template('base.jinja2').render(
        header=f"// Code generated by simple-swagger {' '.join(sys.argv[1:])} DO NOT EDIT.",
        package=package,
        credential_type=security_type,
        api_package=api_package,
    ))

    validations_file.write_text(env.get_template('validations.jinja2').render(
        header=f"// Code generated by simple-swagger {' '.join(sys.argv[1:])} DO NOT EDIT.",
        package=package,
        credential_type=security_type,
        api_package=api_package,
        patterns_cache=patterns_cache,
    ))

    patterns_cache.clear()
    server_file.write_text(env.get_template('server.jinja2').render(
        header=f"// Code generated by simple-swagger {' '.join(sys.argv[1:])} DO NOT EDIT.",
        package="server",
        credential_type=security_type,
        api_package=api_package,
        patterns_cache=patterns_cache,
    ))

    client_file.write_text(env.get_template('client.jinja2').render(
        header=f"// Code generated by simple-swagger {' '.join(sys.argv[1:])} DO NOT EDIT.",
        package="client",
        credential_type=security_type,
        api_package=api_package,
    ))

    files = [
        str(base_file),
        str(validations_file),
        str(client_file),
        str(server_file)
    ]

    formatter(
        files,
        ['goimports', '-w'],
        ['gofmt', '-w', '-s']
    )
