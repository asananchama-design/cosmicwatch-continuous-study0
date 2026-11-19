"""Add a timestamped observation without modifying raw measurement files."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--session', type=Path, required=True, help='Path to an existing study_* folder.')
p.add_argument('--kind', required=True, choices=['interruption_start','interruption_end','reposition','observation'])
p.add_argument('--note', required=True)
p.add_argument('--occurred-utc', help='Optional actual occurrence time, e.g. 2026-11-20T18:00:00Z; otherwise now.')
a = p.parse_args()
if not (a.session/'session.json').is_file():
    p.error('Choose an existing study session folder containing session.json.')
now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
occurred = a.occurred_utc or now
try:
    parsed = datetime.fromisoformat(occurred.replace('Z','+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('timezone required')
    occurred = parsed.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
except ValueError:
    p.error('Use a timestamp with timezone, preferably YYYY-MM-DDTHH:MM:SSZ.')
record = {'recorded_utc':now, 'occurred_utc':occurred, 'kind':a.kind, 'note':a.note}
with (a.session/'field_notes.jsonl').open('a', encoding='utf-8') as f:
    f.write(json.dumps(record)+'\n')
print('Saved field note:', record)
