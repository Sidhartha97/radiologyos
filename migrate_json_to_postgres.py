import json
import os
from pathlib import Path

from server import save_postgres

if not os.getenv('DATABASE_URL'):
    raise SystemExit('Set DATABASE_URL before running this migration.')

source = Path(__file__).parent / 'data.json'
if not source.exists():
    raise SystemExit(f'No source file found: {source}')

save_postgres(json.loads(source.read_text()))
print('Migrated data.json into PostgreSQL app_entities.')
