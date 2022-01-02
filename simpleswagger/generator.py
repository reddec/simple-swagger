#!/usr/bin/env python3
from argparse import ArgumentParser
from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Iterable, List, Dict, Optional

from jinja2 import Environment, FileSystemLoader
from yaml import safe_load


def iter_enums(swagger: dict):
    for name, definition in swagger.get('definitions', {}).items():
        if 'enum' in definition:
            yield name, definition


def calc_endpoint_name(method: str, path: str) -> str:
    chops = [method.title()]
    for x in path.split('/'):
        if x.startswith('{'):
            param = x[1:-1].strip()
            chops.append("By" + param.title())
        else:
            chops.append(x.title())
    return "".join(chops)


@dataclass
class Parameter:
    name: str
    type: dict
    swagger: dict
    definition: dict

    @property
    def location(self) -> str:
        return self.definition['in']

    @property
    def is_ref(self) -> bool:
        return '$ref' in self.type

    def __post_init__(self):
        if 'schema' in self.type:
            self.type = self.type['schema']


@dataclass
class PathPart:
    value: str
    param: Optional[Parameter] = None


@dataclass(frozen=True)
class Method:
    method: str
    path: str
    swagger: dict
    definition: dict

    @property
    def has_tags(self) -> bool:
        return len(self.tags) > 0

    @cached_property
    def body(self) -> Optional[Parameter]:
        for p in self.parameters:
            if p.definition['in'] == 'body':
                return p
        return None

    @property
    def description(self) -> str:
        return self.definition.get('description', '')

    @cached_property
    def name(self) -> str:
        return self.definition.get('operationId') or calc_endpoint_name(self.method, self.path)

    @property
    def tags(self) -> List[str]:
        return self.definition.get('tags', [])

    @property
    def has_response(self) -> bool:
        return 200 in self.definition.get('responses', {})

    @property
    def response_is_array(self) -> bool:
        return self.definition.get('responses', {}).get(200, {}).get('schema', {}).get('type', '') == 'array'

    @property
    def response_type(self) -> dict:
        return self.definition['responses'][200]['schema']

    @property
    def secured(self) -> bool:
        return len(self.definition.get('security', [])) > 0

    @cached_property
    def has_query_params(self) -> bool:
        return any(x for x in self.parameters if x.location == 'query')

    @property
    def security(self) -> List:
        return self.definition.get('security', [])

    @cached_property
    def parameters(self) -> List[Parameter]:
        ans = []
        for p in self.definition.get('parameters', []):
            ans.append(Parameter(p['name'], p, definition=p, swagger=self.swagger))
        return ans

    @cached_property
    def consumes(self) -> List[str]:
        return self.definition.get('consumes', []) or ['application/json']

    @cached_property
    def consumes_json(self) -> bool:
        return 'application/json' in self.consumes

    @cached_property
    def consumes_text(self) -> bool:
        return 'text/plain' in self.consumes

    def param_by_name(self, name: str) -> Optional[Parameter]:
        for p in self.parameters:
            if p.name == name:
                return p
        return None

    @cached_property
    def path_parts(self) -> List[PathPart]:
        ans = []
        for p in self.path.lstrip('/').split('/'):
            if p.startswith('{') and p.endswith('}'):
                param = self.param_by_name(p[1:-1].strip())
                assert param is not None
                ans.append(PathPart(p, param))
            elif len(ans) > 0 and ans[-1].param is None:
                # merge
                ans[-1].value += '/' + p
            else:
                ans.append(PathPart('/' + p))
        padded = []
        for i, p in enumerate(ans):
            if p.param is not None:
                if i == 0 or ans[i - 1].param is not None:
                    padded.append(PathPart('/'))
                else:
                    ans[i - 1].value += "/"
            padded.append(p)
        return padded


def iter_methods(swagger: dict) -> Iterable[Method]:
    # collect methods
    for path, methods in swagger.get('paths', {}).items():
        for method, defintion in methods.items():
            yield Method(method, path, swagger, defintion)


@dataclass
class Object:
    name: str
    definition: dict
    swagger: dict


def move_schema(swagger: dict, schema: dict, object_name: str, ignore_top_object=False):
    type_name = schema.get('type')
    if type_name == 'object':
        obj = schema
        if not ignore_top_object:
            obj = move_object_to_definition(swagger, schema, object_name)
        for field_name, field_definition in obj.get('properties', {}).items():
            nested_object_name = object_name + field_name.title()
            move_schema(swagger, field_definition, nested_object_name)
    elif type_name == 'array':
        move_schema(swagger, schema['items'], object_name)


def move_objects_to_definitions(swagger: dict):
    # collect unnamed objects in params and responses
    for methods in swagger.get('paths', {}).values():
        for definition in methods.values():
            # params
            for parameter in definition.get('parameters', []):
                if parameter['in'] == 'body':
                    name = definition['operationId'] + parameter['name'].title()
                    move_schema(swagger, parameter['schema'], name)

            # responses
            for code, response in definition.get('responses', {}).items():
                name = definition['operationId'] + "Response" + str(code)
                move_schema(swagger, response.get('schema', {}), name)

    # recursive unpack of all definitions
    for name, definition in swagger.get('definitions', {}).items():
        move_schema(swagger, definition, name, ignore_top_object=True)


def move_object_to_definition(swagger: dict, definition: dict, name) -> dict:
    if 'definitions' not in swagger:
        swagger['definitions'] = {}
    assert name not in swagger['definitions'], f'object {name} already defined'
    cp = swagger['definitions'][name] = definition.copy()
    definition.clear()
    definition['$ref'] = {
        '$ref': '#/definitions/' + name
    }
    return cp


def cast(value, definition: dict) -> str:
    if definition.get('type') == 'string':
        return f'"{value}"'  # TODO: fix to proper escaping
    return value


def comment(text: str) -> str:
    return '\n'.join('// ' + line for line in text.splitlines())


def resolve(definition: dict, swagger: dict):
    if 'schema' in definition:
        return resolve(definition['schema'], swagger)
    if '$ref' in definition:
        name = definition['$ref'].split('/')[-1]
        return swagger['definitions'][name]
    return definition


def pascal_case(text: str) -> str:
    return "".join(x[:1].upper() + x[1:] for x in text.split('_'))


def main():
    parser = ArgumentParser(description='Zombie swagger 2.0')
    parser.add_argument('--swagger', '-s', type=Path, default=(Path.cwd() / "swagger.yaml"),
                        help='Location of swagger file')
    parser.add_argument('--output', '-o', type=Path, default=(Path.cwd() / "api"),
                        help='Output directory')
    parser.add_argument('--templates', '-t', type=Path, default=(Path(__file__).parent.absolute() / 'templates'),
                        help='Templates location')
    parser.add_argument('--lang', '-l', type=str, default='golang', help='Target generator')
    args = parser.parse_args()

    env = Environment(loader=FileSystemLoader(args.templates))
    env.filters['cast'] = cast
    env.filters['comment'] = comment
    env.filters['pascal'] = pascal_case
    env.filters['secured'] = lambda x: len(x.get('security', [])) > 0
    env.filters['sec_def'] = lambda x: swagger['securityDefinitions'][x]
    env.filters['resolve'] = lambda x: resolve(x, swagger)
    env.filters['has_payload'] = lambda x: any(param for param in x.get('parameters', []) if param['in'] == 'body')
    env.filters['is_ref_to_type'] = lambda x: '$ref' in x or '$ref' in x.get('schema', {})
    env.filters['has_query_params'] = lambda x: any(
        param for param in x.get('parameters', []) if param['in'] == 'query')
    # filter params by place (body, query, ...)
    env.filters['inside'] = lambda params, place: (p for p in params if p['in'] == place)
    swagger = safe_load(args.swagger.read_text())

    # apply default security
    default_security = swagger.get('security', [])
    if len(default_security) > 0:
        for methods in swagger.get('paths', {}).values():
            for endpoint in methods.values():
                if 'security' not in endpoint:
                    endpoint['security'] = default_security

    # calculated missed operationId
    for path, pathDef in swagger.get('paths', {}).items():
        for method, endpoint in pathDef.items():
            if 'operationId' not in endpoint:
                endpoint['operationId'] = calc_endpoint_name(method, path)

                # remove anonymous object definitions
    move_objects_to_definitions(swagger)

    methods = tuple(sorted(iter_methods(swagger), key=lambda m: m.name))
    enums = tuple(sorted(iter_enums(swagger), key=lambda kv: kv[0]))
    methods_by_name: Dict[str, Method] = dict((m.name, m) for m in methods)
    objects = dict(
        definition for definition in swagger.get('definitions', {}).items() if definition[1].get('type') == 'object')

    methods_by_tag: Dict[str, List[Method]] = defaultdict(list)
    for method in methods:
        for tag in method.tags:
            methods_by_tag[tag].append(method)
    methods_by_tag = dict(sorted(methods_by_tag.items(), key=lambda kv: kv[0]))

    type_aliases = dict((name, definition) for (name, definition) in swagger.get('definitions', {}).items()
                        if 'enum' not in definition and definition.get('type', '') != 'object')

    env.globals = {
        'swagger': swagger,
        'methods': methods,
        'tags': methods_by_tag,
        'methods_by_name': methods_by_name,
        'enums': enums,
        'objects': objects,
        'type_aliases': type_aliases,
        'has_security': len(swagger.get('securityDefinitions', {})) > 0,
    }

    lang = args.lang

    if lang == 'golang':
        from .golang import render
        render(swagger, env, args.output)
    elif lang == 'typescript':
        from .typescript import render
        render(swagger, env, args.output)
    else:
        raise AssertionError('unknown language ' + lang)


if __name__ == '__main__':
    main()
