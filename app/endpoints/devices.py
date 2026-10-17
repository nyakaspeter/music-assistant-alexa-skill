from datetime import datetime
from pathlib import Path

from flask import Blueprint, Response, current_app, redirect, request, url_for
from markupsafe import escape
from skill import device_mapping

devices_bp = Blueprint('devices_bp', __name__)


def _extract_device_id(payload):
    try:
        return (
            payload.get('context', {})
            .get('System', {})
            .get('device', {})
            .get('deviceId')
        )
    except Exception:
        return None


def _extract_intent_name(payload):
    try:
        req = payload.get('request', {})
        intent = req.get('intent')
        if isinstance(intent, dict):
            return intent.get('name')
        return req.get('type')
    except Exception:
        return None


def _seen_devices():
    """Return {device_id: {'last_seen': ts, 'last_request': name}} from intent logs."""
    intent_logs = current_app.config.get('INTENT_LOGS', [])
    seen = {}
    for entry in intent_logs:
        payload = entry.get('incoming')
        if not isinstance(payload, dict):
            continue
        device_id = _extract_device_id(payload)
        if not device_id:
            continue
        ts = entry.get('ts')
        name = _extract_intent_name(payload)
        prev = seen.get(device_id)
        if prev is None or (ts and (prev.get('last_seen') or 0) <= ts):
            seen[device_id] = {'last_seen': ts, 'last_request': name}
    return seen


def _format_ts(ts):
    if not ts:
        return '(unknown)'
    try:
        return datetime.fromtimestamp(float(ts)).strftime('%H:%M:%S %Y-%m-%d')
    except Exception:
        return str(ts)


@devices_bp.route('/devices', methods=['GET'])
def devices_page():
    mapping = device_mapping.load_mapping()
    seen = _seen_devices()

    all_device_ids = sorted(set(mapping.keys()) | set(seen.keys()),
                             key=lambda d: -(seen.get(d, {}).get('last_seen') or 0))

    rows = []
    for device_id in all_device_ids:
        info = seen.get(device_id, {})
        current_player = mapping.get(device_id, '')
        rows.append(f"""
        <tr>
            <td><code title="{escape(device_id)}">{escape(device_id[-12:])}</code></td>
            <td>{escape(_format_ts(info.get('last_seen')))}</td>
            <td>{escape(info.get('last_request') or '(not seen this session)')}</td>
            <td>
                <form method="POST" action="/devices" class="mapping-form">
                    <input type="hidden" name="device_id" value="{escape(device_id)}">
                    <input type="text" name="player_id" value="{escape(current_player)}"
                           placeholder="MA player_id">
                    <button type="submit">Save</button>
                </form>
            </td>
        </tr>""")

    rows_html = '\n'.join(rows) or '<tr><td colspan="4" class="muted">No devices seen yet. Say “Alexa, open Music Assistant” on an Echo, then refresh this page.</td></tr>'

    body = f"""
    <h2>Add an Echo</h2>
    <ol>
        <li>On the Echo you want to add, say <strong>“Alexa, open Music Assistant.”</strong></li>
        <li><a href="/devices">Refresh this page</a>. Its device ID should appear at the top with a recent time.</li>
        <li>Enter its Music Assistant player ID (for example <strong>Echo Dot</strong>) and click Save.</li>
    </ol>
    <p class="muted">Add one Echo at a time so the most recent request identifies the device.
    Alexa provides an opaque device ID, so the bridge cannot automatically match it to a player name.
    A plain “next” command may not invoke this skill. Newly detected devices are shown for this bridge session;
    saved mappings survive restarts. Clearing a player field and saving removes that mapping.</p>
    <details>
        <summary>Add manually using a known device ID</summary>
        <p class="muted">Use the full Alexa device ID, not its name or the last 12 characters shown below.</p>
        <form method="POST" action="/devices" class="mapping-form">
            <label>Full Alexa device ID <input type="text" name="device_id" required></label>
            <label>Music Assistant player ID <input type="text" name="player_id" required placeholder="Echo Dot"></label>
            <button type="submit">Add mapping</button>
        </form>
    </details>
    <table>
        <thead><tr><th>Device id (last 12 chars)</th><th>Last seen</th><th>Last request</th><th>MA player_id</th></tr></thead>
        <tbody>
        {rows_html}
        </tbody>
    </table>
    """

    try:
        tpl_path = Path(__file__).parent.parent / 'templates' / 'devices.html'
        tpl = tpl_path.read_text()
        tpl = tpl.replace('__DEVICES_BODY__', body)
        return Response(tpl, status=200, mimetype='text/html')
    except Exception:
        html = f"<!doctype html><html><head><meta charset='utf-8'><title>Device Mapping</title></head><body><h1>Device Mapping</h1>{body}</body></html>"
        return Response(html, status=200, mimetype='text/html')


@devices_bp.route('/devices', methods=['POST'])
def devices_save():
    device_id = (request.form.get('device_id') or '').strip()
    player_id = (request.form.get('player_id') or '').strip()
    if device_id:
        device_mapping.set_player_for_device(device_id, player_id or None)
    return redirect(url_for('devices_bp.devices_page'))
