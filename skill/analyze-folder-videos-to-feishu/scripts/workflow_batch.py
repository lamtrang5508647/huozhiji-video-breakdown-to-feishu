#!/usr/bin/env python3
"""Resumable local video workflow. Analysis and review remain explicit agent tasks."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
STAGES = ('prepare', 'asr', 'analysis', 'review', 'export', 'publish')
EXTENSIONS = {'.mp4', '.mov', '.m4v', '.avi', '.mkv', '.webm'}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def workspace_lock(workspace):
    """OS-owned lock releases on process exit; never guess liveness from a PID file."""
    workspace = Path(workspace).resolve()
    (workspace / 'state').mkdir(parents=True, exist_ok=True)
    with (workspace / 'state/content-batch.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError('Another batch command owns this workspace; wait for it to finish') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def state_path(workspace):
    return Path(workspace).resolve() / 'state/content-batch.json'


def load_state(workspace):
    path = state_path(workspace)
    if not path.exists():
        return {'schema_version': 'content-batch/1', 'jobs': {}, 'created_at': now()}
    data = read(path)
    if data.get('schema_version') != 'content-batch/1' or not isinstance(data.get('jobs'), dict):
        raise ValueError('Unsupported batch state; existing data was not changed')
    return data


def save(workspace, state):
    state['updated_at'] = now()
    atomic(state_path(workspace), state)


def fresh_stage():
    return {'status': 'pending', 'attempts': 0, 'history': [], 'outputs': {}}


def source_path(job):
    for value in job['source_paths']:
        path = Path(value)
        if path.is_file() and sha(path) == job['source_id']:
            return path
    return None


def select(state, identifier):
    matches = [j for key, j in state['jobs'].items() if key.startswith(identifier)]
    if not identifier or len(matches) != 1:
        raise ValueError('source-id must uniquely identify a registered source SHA-256')
    return matches[0]


def invalidate(job, stage, reason):
    for name in STAGES[STAGES.index(stage):]:
        current = job['stages'][name]
        if current['status'] != 'pending':
            current['status'] = 'stale'
            current['reason'] = reason


def refresh(job):
    job['source_available'] = source_path(job) is not None
    # Check every prior receipt before reusing work, including prepared media.
    for name in STAGES:
        stage = job['stages'][name]
        if stage['status'] == 'running':
            stage['status'] = 'failed' if name != 'publish' else 'uncertain'
            stage['reason'] = 'Previous owner stopped before saving a terminal receipt'
        if stage['status'] == 'complete':
            for path, digest in stage.get('outputs', {}).items():
                if not Path(path).is_file() or sha(path) != digest:
                    invalidate(job, name, 'Recorded output changed or is missing: ' + path)
                    break
    for label, record in job.get('attachments', {}).items():
        if not Path(record['path']).is_file() or sha(record['path']) != record['sha256']:
            invalidate(job, 'analysis', 'Attached input changed; attach the new version: ' + label)
            job['attachment_changed'] = True
            break


def summary(state, stopped=False):
    jobs = []
    for job in state['jobs'].values():
        stages = job['stages']
        if job.get('source_available') is False:
            status, action = 'source_unavailable', 'Restore the original source bytes or add the changed source as a new job'
        elif job.get('attachment_changed'):
            status, action = 'waiting_reattach', 'Reattach the revised manifest and review receipt; old exports are preserved'
        elif any(s['status'] in ('failed', 'uncertain') for s in stages.values()):
            name = next(n for n, s in stages.items() if s['status'] in ('failed', 'uncertain'))
            status, action = 'failed_' + name, stages[name].get('reason', '')
        elif stages['publish']['status'] == 'complete' and stages['export']['status'] == 'complete':
            status, action = 'remote_metadata_verified', 'Base metadata readback passed; full document/gallery/playback acceptance remains separate'
        elif stages['export']['status'] == 'complete':
            status, action = 'local_export_complete', 'Local clips exported; Feishu publication is a separate explicit step'
        elif stages['prepare']['status'] != 'complete':
            status, action = 'waiting_prepare', 'Run batch run --through prepare'
        elif stages['asr']['status'] == 'waiting':
            status, action = 'waiting_asr', stages['asr'].get('reason', 'Supply a local ASR model')
        elif not job.get('attachments', {}).get('manifest'):
            status, action = 'waiting_analysis', 'Inspect evidence, transcribe/review and create the existing script manifest; then batch attach'
        elif not job.get('attachments', {}).get('review'):
            status, action = 'waiting_review', 'Complete content/provenance review and attach its manifest-bound receipt'
        else:
            status, action = 'ready_for_review_validation', 'Run batch run --through export to validate the attached review and export'
        jobs.append({'source_id': job['source_id'], 'source_paths': job['source_paths'], 'batches': job['batches'],
                     'state': status, 'next_action': action,
                     'stages': {n: {'status': s['status'], 'attempts': s['attempts'], 'reason': s.get('reason')}
                                for n, s in stages.items()},
                     'attachments': job.get('attachments', {}),
                     'latest_outputs': {n: list(s.get('outputs', {})) for n, s in stages.items() if s.get('outputs')}})
    return {'jobs_total': len(jobs), 'stopped': stopped, 'jobs': jobs,
            'scope': 'Process/artifact tracking only; no automatic semantic, listening, advertising or UI verification'}


def add_sources(workspace, sources, batch='default'):
    workspace = Path(workspace).resolve()
    if not isinstance(batch, str) or not batch.strip():
        raise ValueError('Batch name must be nonempty')
    resolved = [Path(p).expanduser().resolve() for p in sources]
    if not resolved:
        raise ValueError('No video sources found')
    for p in resolved:
        if not p.is_file() or p.suffix.lower() not in EXTENSIONS:
            raise ValueError('Not a supported local video: ' + str(p))
        if p.is_relative_to(workspace):
            raise ValueError('Source videos must be outside the batch workspace to prevent recursive ingestion')
    with workspace_lock(workspace):
        state = load_state(workspace)
        for path in sorted(resolved, key=lambda p: str(p).casefold()):
            digest = sha(path)
            if digest not in state['jobs']:
                state['jobs'][digest] = {'source_id': digest, 'source_paths': [], 'batches': [],
                                         'created_at': now(), 'source_available': True,
                                         'attachments': {}, 'stages': {name: fresh_stage() for name in STAGES}}
            job = state['jobs'][digest]
            for key, value in (('source_paths', str(path)), ('batches', batch)):
                if value not in job[key]:
                    job[key].append(value)
            refresh(job)
        save(workspace, state)
        return summary(state)


def attached_file(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError('Missing attachment: ' + str(path))
    return {'path': str(path), 'sha256': sha(path)}


def attach(workspace, identifier, manifest_path, review_path=None):
    with workspace_lock(workspace):
        state = load_state(workspace)
        job = select(state, identifier)
        record = attached_file(manifest_path)
        manifest = read(record['path'])
        source = manifest.get('source', {})
        if source.get('sha256', '').lower() != job['source_id']:
            raise ValueError('Manifest belongs to a different source video')
        path = Path(source.get('path', ''))
        if not path.is_absolute():
            path = Path(record['path']).parent / path
        if not path.is_file() or sha(path) != job['source_id']:
            raise ValueError('Manifest source bytes are unavailable or changed')
        timeline = source.get('timeline_manifest_path')
        if not timeline:
            raise ValueError('Manifest requires a reviewed timeline')
        timeline_path = Path(timeline)
        if not timeline_path.is_absolute():
            timeline_path = Path(record['path']).parent / timeline_path
        timeline_record = attached_file(timeline_path)
        if timeline_record['sha256'] != source.get('timeline_manifest_sha256'):
            raise ValueError('Timeline hash mismatch')
        attachments = {'manifest': record, 'timeline': timeline_record}
        if review_path:
            review_record = attached_file(review_path)
            review = read(review_record['path'])
            if (review.get('manifest_sha256') != record['sha256'] or review.get('content_and_provenance') != 'PASS'
                    or not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip()
                    or 'render continuous segment clips' not in review.get('approved_actions', [])):
                raise ValueError('Review must identify reviewer, exact manifest hash, PASS and approved rendering action')
            attachments['review'] = review_record
        if attachments != job['attachments']:
            invalidate(job, 'analysis', 'A revised analysis was attached')
        job['attachments'] = attachments
        job['attachment_changed'] = False
        job['stages']['analysis']['status'] = 'attached'
        job['stages']['analysis']['reason'] = 'Supplied analysis; not yet validated by batch review stage'
        save(workspace, state)
        return summary(state)


def status(workspace):
    with workspace_lock(workspace):
        state = load_state(workspace)
        for job in state['jobs'].values():
            refresh(job)
        save(workspace, state)
        return summary(state)


def prepare_one(source, output, ffmpeg):
    """Worker process uses existing extraction and verifies full decode first."""
    import prepare_batch
    output.mkdir(parents=True, exist_ok=False)
    prepare_batch.run_ffmpeg(ffmpeg, ('-hide_banner','-loglevel','error','-i',str(source),'-f','null','-'))
    metadata = prepare_batch.probe_video(ffmpeg, source)
    assets = prepare_batch.extract_video(ffmpeg, source, output, metadata, output)
    atomic(output / 'prepared.json', {'source_path': str(source), 'source_sha256': sha(source), **metadata, **assets,
                                    'semantic_review': 'not_performed', 'audio_review': 'not_performed'})


def execute(command, attempt_dir, timeout):
    started = time.monotonic()
    try:
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding='utf-8', errors='replace', **options)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Stop the whole locally launched tree before permitting a retry.
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            (attempt_dir / 'stdout.log').write_text(stdout, encoding='utf-8')
            (attempt_dir / 'stderr.log').write_text(stderr, encoding='utf-8')
            return {'ok': False, 'reason': 'Stage timed out; local process tree terminated', 'seconds': time.monotonic() - started}
        (attempt_dir / 'stdout.log').write_text(stdout, encoding='utf-8')
        (attempt_dir / 'stderr.log').write_text(stderr, encoding='utf-8')
        if process.returncode:
            return {'ok': False, 'reason': (stderr or stdout)[-1800:], 'seconds': time.monotonic() - started}
        return {'ok': True, 'seconds': time.monotonic() - started}
    except (OSError, subprocess.SubprocessError) as exc:
        return {'ok': False, 'reason': str(exc), 'seconds': time.monotonic() - started}


def stage_inputs(job, name, ffmpeg, ffprobe, asr_model):
    inputs = {'source_sha256': job['source_id'], 'workflow_sha256': sha(__file__),
              'script_sha256': {p.name: sha(p) for p in sorted(HERE.glob('*.py'))
                                if p.name not in ('creative_workflow.py', 'performance_feedback.py')}}
    if name in ('prepare', 'export'):
        path = shutil.which(ffmpeg)
        inputs.update(ffmpeg=ffmpeg, ffmpeg_sha256=sha(path) if path else None)
    if name in ('review', 'export'):
        path = shutil.which(ffprobe)
        inputs.update(ffprobe=ffprobe, ffprobe_sha256=sha(path) if path else None, attachments=job.get('attachments', {}))
    if name == 'asr':
        from offline_asr import resolve_openai_model, AsrFailure
        try:
            model = Path(resolve_openai_model(asr_model)) if asr_model else None
        except AsrFailure:
            model = None
        inputs.update(model=asr_model, model_sha256=sha(model) if model and model.is_file() else None,
                      prepared=job['stages']['prepare'].get('outputs', {}))
    return inputs


def make_command(job, name, output, ffmpeg, ffprobe, asr_model):
    source = source_path(job)
    base = [sys.executable, '-B']
    if name == 'prepare':
        return base + [str(Path(__file__).resolve()), '_prepare', '--source', str(source), '--output', str(output), '--ffmpeg', ffmpeg]
    if name == 'asr':
        prepared = next(Path(p) for p in job['stages']['prepare']['outputs'] if p.endswith('prepared.json'))
        audio = read(prepared).get('audio_wav')
        if not audio:
            raise ValueError('Source has no extracted audio; do not invent a transcript')
        return base + [str(HERE / 'offline_asr.py'), str(prepared.parent / audio), '--model', str(asr_model), '--output-dir', str(output)]
    manifest = job['attachments']['manifest']['path']
    review = job['attachments']['review']['path']
    if name == 'review':
        return base + [str(HERE / 'validate_script_v3.py'), '--manifest', manifest, '--review-receipt', review,
                       '--require-render-approval', '--ffprobe', ffprobe]
    return base + [str(Path(__file__).resolve()), '_export', '--manifest', manifest, '--review-receipt', review,
                   '--output', str(output), '--render', '--ffmpeg', ffmpeg, '--ffprobe', ffprobe]


def export_one(manifest, review, output, ffmpeg, ffprobe):
    data = read(manifest)
    timeline = Path(data['source']['timeline_manifest_path'])
    if not timeline.is_absolute():
        timeline = manifest.parent / timeline
    # Validate before producing either required deliverable.
    subprocess.run([sys.executable, '-B', str(HERE / 'validate_script_v3.py'), '--manifest', str(manifest),
                    '--review-receipt', str(review), '--require-render-approval', '--ffprobe', ffprobe], check=True, capture_output=True)
    for script, arguments in (
        ('export_script_v3.py', ['--manifest', str(manifest), '--review-receipt', str(review), '--output', str(output / 'semantic')]),
        ('export_reviewed_timeline.py', ['--manifest', str(timeline), '--output', str(output / 'shots')])):
        subprocess.run([sys.executable, '-B', str(HERE / script), *arguments, '--render', '--ffmpeg', ffmpeg, '--ffprobe', ffprobe], check=True)


def verify_stage(job, name, attempt):
    output = attempt / 'artifacts'
    if name == 'prepare':
        receipt = read(output / 'prepared.json')
        if receipt.get('source_sha256') != job['source_id'] or not receipt.get('opening_frames'):
            raise ValueError('Preparation receipt lacks source identity or evidence frames')
    elif name == 'asr':
        receipt = read(output / 'transcript.json')
        if receipt.get('transcript_status') != 'asr_draft' or not receipt.get('text', '').strip():
            raise ValueError('ASR has no usable draft transcript')
    elif name == 'review':
        if read(attempt / 'stdout.log').get('status') != 'valid_draft':
            raise ValueError('Validation did not return its expected technical receipt')
    elif name == 'export':
        receipt = read(output / 'semantic/receipt.json')
        shots = read(output / 'shots/timeline.json')
        if receipt.get('status') != 'complete' or not shots.get('segments'):
            raise ValueError('Export is missing complete semantic and shot receipts')
        if not list((output / 'semantic/clips').glob('*.mp4')) or not list((output / 'shots/clips').glob('*.mp4')):
            raise ValueError('Export must contain both semantic and numbered shot clips')


def run_batch(workspace, through='prepare', workers=2, retry_failed=False, ffmpeg='ffmpeg', ffprobe='ffprobe',
              asr_model=None, stop_file=None, timeout=600, max_attempts=3):
    if through not in ('prepare', 'asr', 'review', 'export') or not 1 <= workers <= 8 or timeout <= 0 or max_attempts < 1:
        raise ValueError('Invalid stage, worker count (1..8), timeout or attempt limit')
    workspace = Path(workspace).resolve()
    stop = Path(stop_file).resolve() if stop_file else None
    with workspace_lock(workspace):
        state = load_state(workspace)
        for job in state['jobs'].values():
            refresh(job)
        save(workspace, state)
        if stop and stop.exists():
            return summary(state, True)
        names = ['prepare']
        if through == 'asr' or asr_model:
            names.append('asr')
        if through in ('review', 'export'):
            names.append('review')
        if through == 'export':
            names.append('export')
        for name in names:
            pending = []
            for job in state['jobs'].values():
                refresh(job)
                if not job.get('source_available') or job.get('attachment_changed'):
                    continue
                stage = job['stages'][name]
                inputs = stage_inputs(job, name, ffmpeg, ffprobe, asr_model)
                if stage['status'] == 'complete' and stage.get('inputs') != inputs:
                    invalidate(job, name, 'Stage configuration or dependency changed')
                if stage['status'] == 'complete':
                    continue
                if stage['status'] in ('failed', 'uncertain') and not retry_failed:
                    continue
                if stage['status'] == 'failed' and stage['attempts'] >= max_attempts:
                    continue
                if name != 'prepare' and job['stages']['prepare']['status'] != 'complete':
                    continue
                if name in ('review', 'export'):
                    if not job['attachments'].get('manifest'):
                        job['stages']['analysis'].update(status='waiting', reason='Source-grounded analysis required')
                        continue
                    if not job['attachments'].get('review'):
                        job['stages']['review'].update(status='waiting', reason='Manifest-bound content review required')
                        continue
                    if name == 'export' and job['stages']['review']['status'] != 'complete':
                        continue
                if name == 'asr' and not asr_model:
                    stage.update(status='waiting', reason='Supply an installed local --asr-model; no download is performed')
                    continue
                pending.append(job)
            # Submit in bounded waves so STOP prevents new work, not just reporting.
            for offset in range(0, len(pending), workers):
                if stop and stop.exists():
                    save(workspace, state)
                    return summary(state, True)
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {}
                    for job in pending[offset:offset + workers]:
                        stage = job['stages'][name]
                        stage['attempts'] += 1
                        attempt = workspace / 'jobs' / job['source_id'] / name / f"{stage['attempts']:04d}-{uuid.uuid4().hex[:8]}"
                        attempt.mkdir(parents=True, exist_ok=False)
                        output = attempt / 'artifacts'
                        stage.update(status='running', reason='', output_dir=str(output), started_at=now())
                        stage['inputs'] = stage_inputs(job, name, ffmpeg, ffprobe, asr_model)
                        save(workspace, state)
                        try:
                            command = make_command(job, name, output, ffmpeg, ffprobe, asr_model)
                        except (OSError, ValueError, KeyError, StopIteration) as exc:
                            stage.update(status='failed', reason=str(exc))
                            stage['history'].append({'attempt': stage['attempts'], 'status': 'failed', 'reason': str(exc), 'at': now()})
                            save(workspace, state)
                            continue
                        futures[pool.submit(execute, command, attempt, timeout)] = (job, attempt)
                    for future in as_completed(futures):
                        job, attempt = futures[future]
                        stage = job['stages'][name]
                        result = future.result()
                        if result['ok']:
                            try:
                                verify_stage(job, name, attempt)
                            except (OSError, ValueError, KeyError, TypeError) as exc:
                                result.update(ok=False, reason='Stage receipt verification failed: ' + str(exc))
                        if source_path(job) is None:
                            result.update(ok=False, reason='Source changed during processing')
                        if any(not Path(v['path']).is_file() or sha(v['path']) != v['sha256'] for v in job.get('attachments', {}).values()):
                            result.update(ok=False, reason='Attached analysis or review changed during processing; attach the current version')
                            job['attachment_changed'] = True
                        if result['ok']:
                            files = [p for p in attempt.rglob('*') if p.is_file()]
                            stage.update(status='complete', outputs={str(p): sha(p) for p in files}, reason='')
                            if name == 'review':
                                stage['outputs'].update({v['path']: v['sha256'] for v in job['attachments'].values()})
                                job['stages']['analysis']['status'] = 'complete'
                                job['stages']['analysis']['reason'] = 'Input supplied; technical validation passed, semantic review remains attributed to its reviewer'
                        else:
                            stage.update(status='failed', reason=result['reason'])
                        stage['history'].append({'attempt': stage['attempts'], 'status': stage['status'],
                                                 'seconds': round(result['seconds'], 3), 'at': now(), 'directory': str(attempt)})
                        save(workspace, state)
            save(workspace, state)
        return summary(state)


def publish(workspace, identifier, plan, project, approved_hash=None, apply=False, timeout=600, provenance_workspace=None):
    """One serial Base write handoff. Existing sync owns reconciliation and remote journaling."""
    workspace = Path(workspace).resolve()
    with workspace_lock(workspace):
        state = load_state(workspace)
        job = select(state, identifier)
        refresh(job)
        if not job.get('source_available') or job.get('attachment_changed') or job['stages']['export']['status'] != 'complete':
            raise ValueError('Current local export must be complete before publishing')
        plan = Path(plan).resolve()
        plan_data = read(plan)
        manifest = read(job['attachments']['manifest']['path'])
        if plan_data.get('video_id') != manifest.get('video_id'):
            raise ValueError('Publication plan belongs to a different video')
        if apply and approved_hash != sha(plan):
            raise ValueError('Apply requires the exact --approved-plan-sha256')
        stage = job['stages']['publish']
        provenance = Path(provenance_workspace).resolve() if provenance_workspace else workspace
        command = [sys.executable, '-B', str(HERE / 'sync_video_base.py'), '--plan', str(plan), '--project', str(Path(project).resolve()),
                   '--workspace', str(provenance), '--state', str(workspace / 'jobs' / job['source_id'] / 'sync-state.json')]
        if apply:
            command += ['--apply', '--approved-plan-sha256', approved_hash]
        else:
            command += ['--dry-run']
        attempt = workspace / 'jobs' / job['source_id'] / 'publish' / uuid.uuid4().hex
        attempt.mkdir(parents=True)
        if apply:
            stage.update(status='running', attempts=stage['attempts'] + 1, plan_sha256=sha(plan))
            save(workspace, state)
        result = execute(command, attempt, timeout)
        if apply and result['ok']:
            try:
                if read(attempt / 'stdout.log').get('status') != 'technical_remote_qc_passed':
                    raise ValueError('Remote process stopped without a complete metadata readback receipt')
            except (OSError, ValueError, TypeError) as exc:
                result.update(ok=False, reason=str(exc))
        if apply:
            stage.update(status='complete' if result['ok'] else 'uncertain', reason=result.get('reason', ''),
                         outputs={str(p): sha(p) for p in attempt.iterdir() if p.is_file()})
            stage['history'].append({'at': now(), 'status': stage['status'], 'plan_sha256': sha(plan), 'directory': str(attempt)})
            save(workspace, state)
        return {**summary(state), 'publication_apply': apply, 'command_succeeded': result['ok'], 'log_directory': str(attempt)}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='command', required=True)
    add = commands.add_parser('add', help='Register source files without modifying them; deduplicate by SHA-256')
    add.add_argument('--workspace', required=True, type=Path)
    add.add_argument('--folder', required=True, type=Path)
    add.add_argument('--batch', default='default')
    run = commands.add_parser('run', help='Run local stages, waiting for analysis/review instead of inventing them')
    run.add_argument('--workspace', required=True, type=Path)
    run.add_argument('--through', choices=('prepare', 'asr', 'review', 'export'), default='prepare')
    run.add_argument('--workers', type=int, default=2)
    run.add_argument('--retry-failed', action='store_true')
    run.add_argument('--max-attempts', type=int, default=3)
    run.add_argument('--timeout', type=int, default=600)
    run.add_argument('--ffmpeg', default='ffmpeg')
    run.add_argument('--ffprobe', default='ffprobe')
    run.add_argument('--asr-model', help='Installed local model path or cached name; never downloaded')
    run.add_argument('--stop-file', type=Path)
    show = commands.add_parser('status', help='Verify receipts and show next action for each source')
    show.add_argument('--workspace', required=True, type=Path)
    att = commands.add_parser('attach', help='Attach existing analysis and optional analyst review by exact hashes')
    att.add_argument('--workspace', required=True, type=Path)
    att.add_argument('--source-id', required=True)
    att.add_argument('--manifest', type=Path, required=True)
    att.add_argument('--review-receipt', type=Path)
    pub = commands.add_parser('publish', help='Serial handoff to existing Base sync; read-only unless --apply')
    pub.add_argument('--workspace', required=True, type=Path)
    pub.add_argument('--source-id', required=True)
    pub.add_argument('--plan', required=True, type=Path)
    pub.add_argument('--project', required=True, type=Path)
    pub.add_argument('--apply', action='store_true')
    pub.add_argument('--approved-plan-sha256')
    pub.add_argument('--provenance-workspace', type=Path, help='Common parent containing source media, exports and plans for existing sync path checks')
    worker = commands.add_parser('_prepare', help=argparse.SUPPRESS)
    worker.add_argument('--source', required=True, type=Path)
    worker.add_argument('--output', required=True, type=Path)
    worker.add_argument('--ffmpeg', required=True)
    exporter = commands.add_parser('_export', help=argparse.SUPPRESS)
    exporter.add_argument('--manifest', type=Path, required=True)
    exporter.add_argument('--review-receipt', type=Path, required=True)
    exporter.add_argument('--output', type=Path, required=True)
    exporter.add_argument('--render', action='store_true')
    exporter.add_argument('--ffmpeg', required=True)
    exporter.add_argument('--ffprobe', required=True)
    args = p.parse_args(argv)
    try:
        if args.command == 'add':
            folder = args.folder.resolve()
            if not folder.is_dir():
                raise ValueError('Source folder does not exist')
            result = add_sources(args.workspace, [x for x in folder.rglob('*') if x.is_file() and x.suffix.lower() in EXTENSIONS], args.batch)
        elif args.command == 'run':
            result = run_batch(**{k: v for k, v in vars(args).items() if k != 'command'})
        elif args.command == 'status':
            result = status(args.workspace)
        elif args.command == 'attach':
            result = attach(args.workspace, args.source_id, args.manifest, args.review_receipt)
        elif args.command == 'publish':
            result = publish(args.workspace, args.source_id, args.plan, args.project, args.approved_plan_sha256, args.apply,
                             provenance_workspace=args.provenance_workspace)
        elif args.command == '_export':
            export_one(args.manifest, args.review_receipt, args.output, args.ffmpeg, args.ffprobe)
            result = {'status': 'exported_both_clip_types'}
        else:
            prepare_one(args.source, args.output, args.ffmpeg)
            result = {'status': 'prepared_unreviewed'}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == 'run' and any(j['state'].startswith('failed_') or j['state'] == 'source_unavailable' for j in result['jobs']):
            return 2
        if args.command == 'publish' and not result['command_succeeded']:
            return 2
        return 0
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        print(json.dumps({'status': 'error', 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
