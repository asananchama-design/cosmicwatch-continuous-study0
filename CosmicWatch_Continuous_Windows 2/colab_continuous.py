"""Paste this entire file into a Google Colab code cell.
Upload the daily *_master_events.csv, *_slave_events.csv, *_host_health.csv
and session.json from ONE continuous recording session. No raw .bin files.
This plots recorded counts and link diagnostics, not corrected muon flux.
"""
import io
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MaxNLocator
from google.colab import files

uploaded = files.upload()
sessions = [name for name in uploaded if name.endswith('.json') and 'session' in name]
if len(sessions) != 1:
    raise ValueError('Upload exactly one session.json plus CSV files from that SAME session.')
session = json.loads(uploaded[sessions[0]])
if session.get('schema') != 'CW_STUDY_LOG_V1':
    raise ValueError('This notebook needs the new continuous study format.')
if not session.get('closed_utc'):
    raise ValueError('Use a cleanly stopped session with final totals; keep recording files unchanged.')
expected_counts = {d['role']: d['saved_events'] for d in session.get('detector_summary', [])}
if set(expected_counts) != {'MASTER_SINGLES','SLAVE_COINCIDENCE'}:
    raise ValueError('Session metadata does not describe both detectors. Check the recording errors.')

frames = {'MASTER_SINGLES': [], 'SLAVE_COINCIDENCE': []}
health = []
seen = set()
for name, raw in uploaded.items():
    if not name.endswith('.csv'):
        continue
    if '_master_events' in name or '_slave_events' in name:
        frame = pd.read_csv(io.BytesIO(raw))
        required = {'received_utc','host_monotonic_ns','role','port','segment','event_id','device_uptime_us','adc_sample'}
        if not required <= set(frame.columns):
            raise ValueError(f'{name}: unexpected columns.')
        if frame.empty:
            continue
        roles = set(frame['role'])
        if len(roles) != 1 or not roles <= set(frames):
            raise ValueError(f'{name}: invalid role.')
        for key in frame[['port','segment','event_id']].itertuples(index=False, name=None):
            if key in seen:
                raise ValueError('Duplicate events detected. Upload each daily file once from ONE session.')
            seen.add(key)
        frame['received_utc'] = pd.to_datetime(frame['received_utc'], utc=True, errors='raise')
        frames[next(iter(roles))].append(frame)
    elif '_host_health' in name:
        frame = pd.read_csv(io.BytesIO(raw))
        frame['received_utc'] = pd.to_datetime(frame['received_utc'], utc=True, errors='raise')
        health.append(frame)
if not health:
    raise ValueError('Include daily *_host_health.csv files so gaps are visible.')
h = pd.concat(health, ignore_index=True).sort_values('host_monotonic_ns')
if h['host_monotonic_ns'].duplicated().any():
    raise ValueError('Duplicate health samples; check that files were uploaded only once.')
if (h['received_utc'].diff().dt.total_seconds().dropna() < 0).any():
    raise ValueError('UTC moved backwards. Review operations.jsonl before time-based plotting.')
if not session.get('pair_ready_utc'):
    raise ValueError('This session never established a healthy pair.')
start = pd.to_datetime(session['pair_ready_utc'], utc=True)
end = pd.to_datetime(session.get('closed_utc') or h['received_utc'].max(), utc=True)
hours = pd.date_range(start.floor('h'), end.floor('h'), freq='h')

fig, axes = plt.subplots(3, 1, figsize=(12, 11), sharex=True, constrained_layout=True)
summary = []
tables = []
for role, label, color in [('MASTER_SINGLES','Master: local singles','#1479CB'),
                            ('SLAVE_COINCIDENCE','Slave: accepted coincidences','#E77816')]:
    if not frames[role]:
        if expected_counts[role] != 0:
            raise ValueError(f'Missing {role} files. Upload all daily event CSVs from this session.')
        axes[0].plot([start,end], [0,0], label=f'{label}: 0 saved', color=color)
        tables.append(pd.Series(0, index=hours, name=role))
        summary.append(f'{label}: 0 saved rows (confirmed by session totals)')
        continue
    data = pd.concat(frames[role], ignore_index=True).sort_values('host_monotonic_ns')
    if len(data) != expected_counts[role]:
        raise ValueError(f'{role}: uploaded {len(data)} rows; session saved {expected_counts[role]}. Upload all daily files.')
    if (data['received_utc'].diff().dt.total_seconds().dropna() < 0).any():
        raise ValueError('UTC moved backwards; review operations.jsonl before combining bins.')
    event_times = data['received_utc'].tolist()
    axes[0].step([min(start,event_times[0]), *event_times, max(end,event_times[-1])],
                 np.r_[0, np.arange(1,len(data)+1), len(data)], where='post',
                 label=f'{label}: {len(data)} saved', color=color)
    counts = data.groupby(data['received_utc'].dt.floor('h')).size().reindex(hours, fill_value=0)
    # Do not display a measured zero for an hour with no healthy host telemetry.
    healthy_hours = set(h.loc[h['both_links_healthy'] == 1, 'received_utc'].dt.floor('h'))
    displayed = counts.astype(float).where(counts.index.isin(healthy_hours) | (counts > 0))
    axes[1].step(displayed.index, displayed.values, where='post', label=label, color=color)
    tables.append(counts.rename(role))
    summary.append(f'{label}: {len(data)} uploaded event rows')
axes[0].set(title='Cumulative recorded events', ylabel='Saved events')
axes[1].set(title='Recorded counts per UTC hour (partial hours and gaps are NOT corrected)', ylabel='Counts / calendar-hour bin')

# NaN breaks the line across absent telemetry; healthy samples are not live-time calibration.
age = h['host_monotonic_ns'].diff() / 1e9
link = h['both_links_healthy'].astype(float).copy()
link.loc[age > 20] = np.nan
axes[2].plot(h['received_utc'], link, marker='.', markersize=2, linewidth=0.8, color='#555555')
axes[2].scatter(h['received_utc'], h['both_links_healthy'], s=5, color='#555555')
axes[2].set(title='Computer link diagnostic; review operations.jsonl for interruptions',
            ylabel='Both links healthy', xlabel='UTC receipt time', yticks=[0,1], ylim=(-0.1,1.1))
for ax in axes:
    ax.grid(alpha=0.25)
for ax in axes[:2]:
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    if ax.get_legend_handles_labels()[0]: ax.legend()
locator = mdates.AutoDateLocator()
axes[2].xaxis.set_major_locator(locator)
axes[2].xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
plt.show()
output = Path('continuous_study_plots')
output.mkdir(exist_ok=True)
fig.savefig(output/'continuous_counts.png', dpi=200)
fig.savefig(output/'continuous_counts.pdf')
plt.close(fig)
if tables:
    pd.concat(tables, axis=1).to_csv(output/'uploaded_hourly_counts.csv', index_label='utc_hour_start')
summary.extend([
    'These figures use only the uploaded files. Check them against saved_events in session.json.',
    'Missing uploads and data gaps must NOT be interpreted as zero physical flux.',
    'Zero-filled hourly bins indicate no uploaded rows, not necessarily zero detected particles.',
    'First/last hours can be partial. No exposure, geometry, dead-time, efficiency, or pressure corrections applied.',
    'UTC is computer receipt time. USB delay and computer clock adjustments affect timing.',
    'Master singles and slave coincidences are different selections.',
    'Keep original raw files. Review operations.jsonl, device_status CSV and field notes before scientific submission.'
])
text = '\n'.join(summary)
print(text)
(output/'plot_notes.txt').write_text(text, encoding='utf-8')
archive = Path('continuous_study_plots.zip')
with ZipFile(archive, 'w', ZIP_DEFLATED) as z:
    for path in output.iterdir():
        if path.is_file(): z.write(path, path.name)
files.download(str(archive))
