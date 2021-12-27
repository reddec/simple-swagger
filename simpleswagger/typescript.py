from pathlib import Path
from subprocess import check_call, SubprocessError

from jinja2 import Environment

__help = '''
integer	integer	int32	signed 32 bits
long	integer	int64	signed 64 bits
float	number	float	
double	number	double	
string	string		
byte	string	byte	base64 encoded characters
binary	string	binary	any sequence of octets
boolean	boolean		
date	string	date	As defined by full-date - RFC3339
dateTime	string	date-time	As defined by date-time - RFC3339
password	string	password	Used to hint UIs the input needs to be obscured.
'''


def map_basic_type(schema: dict) -> str:
    if '$ref' in schema:
        return schema['$ref'].split('/')[-1]
    type_def = (schema['type'], schema.get('format', ''))
    if type_def[0] == 'array':
        return map_basic_type(schema['items']) + '[]'
    if type_def[0] in ('integer', 'number'):
        return 'number'
    if type_def in (('string', ''), ('string', 'binary'), ('string', 'byte'), ('string', 'password')):
        return 'string'
    if type_def[0] == 'boolean':
        return 'boolean'
    if type_def in (('string', 'date'), ('string', 'date-time')):
        return 'Date'
    print("unknown basic type:", schema)
    return 'any'


def render(swagger: dict, env: Environment, output: Path):
    env.filters['map_type'] = map_basic_type
    content = env.get_template('typescript/types.jinja2').render()
    output.mkdir(parents=True, exist_ok=True)
    types_file = (output / "index.ts")
    types_file.write_text(content)

    files = [
        types_file
    ]

    try:
        check_call(['prettier', '--write'] + [str(f) for f in files])
    except (SubprocessError, FileNotFoundError) as err:
        print("formatter not executed:", err)
