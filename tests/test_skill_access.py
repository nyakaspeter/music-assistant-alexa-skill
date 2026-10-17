import json

import private_access as access


def test_access_setting_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('SKILL_DEPLOYMENT_PATH', str(tmp_path / 'state.json'))
    access.save_allowed_skill_id('amzn1.ask.skill.mine')
    assert access.get_allowed_skill_id() == 'amzn1.ask.skill.mine'
    assert access.access_path().stat().st_mode & 0o777 == 0o600


def test_upgrade_trusts_only_completed_deployment(tmp_path, monkeypatch):
    state = tmp_path / 'state.json'
    monkeypatch.setenv('SKILL_DEPLOYMENT_PATH', str(state))
    state.write_text(json.dumps({'phase': 'ready', 'skill_id': 'unverified'}))
    assert access.get_allowed_skill_id() is None
    state.write_text(json.dumps({'phase': 'complete', 'skill_id': 'verified'}))
    assert access.get_allowed_skill_id() == 'verified'
    assert json.loads(access.access_path().read_text()) == {'skill_id': 'verified'}


def test_corrupt_access_file_does_not_fall_back(tmp_path, monkeypatch):
    state = tmp_path / 'state.json'
    monkeypatch.setenv('SKILL_DEPLOYMENT_PATH', str(state))
    state.write_text(json.dumps({'phase': 'complete', 'skill_id': 'old'}))
    access.access_path().write_text('invalid')
    assert access.get_allowed_skill_id() is None
