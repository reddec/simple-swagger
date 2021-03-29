#!/usr/bin/env python3
from argparse import ArgumentParser
from pathlib import Path
from subprocess import check_call

from jinja2 import Environment, FileSystemLoader
from yaml import safe_load


def map_type(definition: dict):
    if 'schema' in definition:
        # work-around for parameters
        return map_type(definition['schema'])

    ref = definition.get('$ref')
    if ref is not None:
        return ref.split('/')[-1]

    type_name = definition['type']

    if type_name == 'string':
        return 'string'
    if type_name == 'boolean':
        return 'bool'
    if type_name == 'array':
        return '[]' + map_type(definition['items'])
    return 'interface{}'


def path(text: str) -> str:
    import re
    return re.sub(r'{(.*?)}', ':\\1', text)


def main():
    parser = ArgumentParser(description='Zombie swagger 2.0')
    parser.add_argument('--swagger', '-s', type=Path, default=(Path.cwd() / "swagger.yaml"),
                        help='Location of swagger file')
    parser.add_argument('--output', '-o', type=Path, default=(Path.cwd() / "api" / "handler.go"),
                        help='Location of handler file')
    parser.add_argument('--templates', '-t', type=Path, default=(Path(__file__).parent.absolute() / 'templates'),
                        help='Templates location')
    parser.add_argument('--package', '-p', type=str, default='api', help='Package name')
    args = parser.parse_args()

    env = Environment(loader=FileSystemLoader(args.templates))
    env.filters['map_type'] = map_type
    env.filters['label'] = lambda x: x[0].upper() + x[1:]
    env.filters['path'] = path

    swagger = safe_load(args.swagger.read_text())
    args.output.parent.absolute().mkdir(parents=True, exist_ok=True)
    args.output.write_text(env.get_template('model.jinja2').render(swagger=swagger, package=args.package))
    check_call(['gofmt', '-w', '-s', str(args.output)])


if __name__ == '__main__':
    main()
