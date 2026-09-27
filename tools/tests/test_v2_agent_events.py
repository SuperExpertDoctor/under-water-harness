from fastapi.testclient import TestClient
import pytest

from uuv_game.api import create_app


def test_native_stream_finalizes_one_message_and_routes_feedback(tmp_path):
    with TestClient(create_app(tmp_path/'events.sqlite', worker_token='test', ticking=False)) as client:
        client.get('/api/health')
        episode = client.get('/api/state').json()['episode_id']
        client.post('/api/pi-agent/messages', json={'episode_id': episode, 'text': 'observe'})
        headers = {'Authorization': 'Bearer test'}
        job = client.post('/internal/agent/next', json={}, headers=headers).json()['job']
        metadata = {'episode_id': episode, 'run_id': job['run_id']}
        for kind, text in [('message_start', ''), ('message_update', 'Observed'), ('message_end', 'Observed')]:
            result = client.post('/internal/agent/event', headers=headers, json={**metadata,
                'type': 'session_event', 'event': {'type': kind, 'message_id': 'native-1', 'text': text}})
            assert result.status_code == 200, result.text
        response = client.post('/api/pi-agent/messages', json={'episode_id': episode, 'text': 'keep scanning',
            'delivery': 'steer', 'annotation': {'message_id': 'native-1', 'quote': 'Observed'}})
        assert response.status_code == 200
        heartbeat = client.post('/internal/agent/heartbeat', headers=headers, json=metadata).json()
        assert heartbeat['feedback'][0]['delivery'] == 'steer'
        feedback_id = heartbeat['feedback'][0]['id']
        user_message = client.get('/api/pi-agent/messages').json()['messages'][-1]
        assert user_message['feedback_id'] == feedback_id
        assert user_message['status'] == 'queued'
        heartbeat = client.post('/internal/agent/heartbeat', headers=headers, json={**metadata, 'acknowledged_feedback_ids': [feedback_id]}).json()
        assert heartbeat['feedback'] == []
        delivered = client.get('/api/pi-agent/messages').json()['messages'][-1]
        assert delivered['status'] == 'completed'
        assert delivered['delivery_status'] == 'delivered'
        client.post('/internal/agent/event', headers=headers, json={**metadata, 'type': 'completed', 'text': 'Observed'})
        messages = client.get('/api/pi-agent/messages').json()['messages']
        assert len([m for m in messages if m['role'] == 'assistant']) == 1
        assert messages[1]['status'] == 'completed'


def test_rendered_markdown_selection_is_feedback_not_approval(tmp_path):
    with TestClient(create_app(tmp_path/'annotation.sqlite', worker_token='test', ticking=False)) as client:
        client.get('/api/health')
        runtime = client.app.state.runtime
        runtime.messages.append({'id': 'markdown-message', 'role': 'assistant', 'text': '**Observed** `CONTACT-1`',
            'time': 0, 'model': runtime.config.model, 'status': 'completed'})
        response = client.post('/api/pi-agent/messages', json={'episode_id': runtime.episode, 'text': 'explain this decision',
            'annotation': {'message_id': 'markdown-message', 'quote': 'Observed CONTACT-1'}})
        response.raise_for_status()
        annotation = runtime.messages[-1]['annotation']
        assert annotation['message_id'] == 'markdown-message'
        assert annotation['quote'] == 'Observed CONTACT-1'
        assert annotation['quote_source'] == 'operator_selection'
        assert 'not approval' in runtime.agent_jobs[-1]['text']
        assert not runtime.active
        assert not runtime.plans


def test_acknowledged_feedback_survives_worker_loss_before_settled(tmp_path):
    with TestClient(create_app(tmp_path/'ack-crash.sqlite', worker_token='test', ticking=False)) as client:
        client.get('/api/health')
        runtime = client.app.state.runtime
        runtime.queue_agent('observe')
        headers = {'Authorization': 'Bearer test'}
        job = client.post('/internal/agent/next', json={}, headers=headers).json()['job']
        client.post('/api/pi-agent/messages', json={'episode_id': runtime.episode, 'text': 'preserve scanning'}).raise_for_status()
        feedback_id = runtime.agent_jobs[0]['feedback'][0]['id']
        client.post('/internal/agent/heartbeat', headers=headers, json={'episode_id': runtime.episode,
            'run_id': job['run_id'], 'acknowledged_feedback_ids': [feedback_id]}).raise_for_status()
        runtime.agent_jobs[0]['lease_deadline'] = 0
        recovered = client.post('/internal/agent/next', json={}, headers=headers).json()['job']
        assert recovered is not None
        assert recovered['text'] == 'preserve scanning'
        assert recovered['source'] == 'feedback'


def test_feedback_acknowledgment_rolls_back_when_persistence_fails(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path/'ack-rollback.sqlite', worker_token='test', ticking=False)) as client:
        client.get('/api/health')
        runtime = client.app.state.runtime
        runtime.queue_agent('observe')
        headers = {'Authorization': 'Bearer test'}
        job = client.post('/internal/agent/next', json={}, headers=headers).json()['job']
        client.post('/api/pi-agent/messages', json={'episode_id': runtime.episode, 'text': 'keep this feedback'}).raise_for_status()
        feedback_id = runtime.agent_jobs[0]['feedback'][0]['id']
        before = runtime.store.load()
        save = runtime.store.save

        def fail_after_save(data):
            save(data)
            raise OSError('injected_ack_save_failure')

        with monkeypatch.context() as patch:
            patch.setattr(runtime.store, 'save', fail_after_save)
            with pytest.raises(OSError, match='injected_ack_save_failure'):
                client.post('/internal/agent/heartbeat', headers=headers, json={'episode_id': runtime.episode,
                    'run_id': job['run_id'], 'acknowledged_feedback_ids': [feedback_id]})
        assert runtime.agent_jobs[0]['feedback'][0]['id'] == feedback_id
        assert runtime.messages[-1]['status'] == 'queued'
        assert runtime.store.load() == before


def test_invalid_annotation_and_truth_injection_rejected(tmp_path):
    with TestClient(create_app(tmp_path/'events.sqlite', worker_token='test', ticking=False)) as client:
        client.get('/api/health')
        episode = client.get('/api/state').json()['episode_id']
        response = client.post('/api/pi-agent/messages', json={'episode_id': episode, 'text': 'hi',
            'annotation': {'message_id': 'missing', 'quote': 'not a real reference'}})
        assert response.status_code == 422


def test_observation_cursor_pages_equal_time_samples_without_regenerating(tmp_path):
    with TestClient(create_app(tmp_path/'cursor.sqlite', worker_token='test', ticking=False)) as client:
        client.get('/api/health')
        runtime = client.app.state.runtime
        runtime.observations = [{'sequence': n, 'sample_id': f'sample-{n}', 'time_s': 1.0,
            'contact_id': 'CONTACT-1', 'mode': 'passive', 'bearing_rad': .2, 'observer_pose': [100, 100, 0],
            'observer_id': 'UUV-1', 'generation': 1} for n in range(1, 5)]
        runtime.observation_cursor = 4
        runtime.queue_agent('read measurements')
        headers = {'Authorization': 'Bearer test'}
        job = client.post('/internal/agent/next', json={}, headers=headers).json()['job']
        metadata = {'episode_id': runtime.episode, 'run_id': job['run_id'], 'limit': 2}
        page = client.post('/internal/tools/get_observations', headers=headers, json={**metadata, 'cursor': 0}).json()
        assert [item['sample_id'] for item in page['observations']] == ['sample-1', 'sample-2']
        assert page['cursor'] == 2
        page = client.post('/internal/tools/get_observations', headers=headers, json={**metadata, 'cursor': page['cursor']}).json()
        assert [item['sample_id'] for item in page['observations']] == ['sample-3', 'sample-4']
        final = client.post('/internal/tools/get_observations', headers=headers, json={**metadata, 'cursor': page['cursor']}).json()
        assert final == {'observations': [], 'cursor': 4}
        assert len(runtime.observations) == runtime.observation_cursor == 4
        assert runtime.sim_time == 0


@pytest.mark.parametrize('kind', ['compaction_start', 'compaction_end', 'auto_retry_start', 'auto_retry_end',
    'summarization_retry_scheduled', 'summarization_retry_attempt_start', 'summarization_retry_finished', 'agent_settled'])
def test_native_lifecycle_projection_preserves_public_fields_only(tmp_path, kind):
    with TestClient(create_app(tmp_path/'projection.sqlite', worker_token='test', ticking=False)) as client:
        client.get('/api/health')
        runtime = client.app.state.runtime
        runtime.queue_agent('observe')
        headers = {'Authorization': 'Bearer test'}
        job = client.post('/internal/agent/next', json={}, headers=headers).json()['job']
        response = client.post('/internal/agent/event', headers=headers, json={'episode_id': runtime.episode,
            'run_id': job['run_id'], 'type': 'session_event', 'event': {'type': kind, 'attempt': 2,
                'will_retry': True, 'status': 'failed', 'text': 'public result', 'thinking': 'hidden', 'headers': {'authorization': 'secret'}}})
        response.raise_for_status()
        event = runtime.events[-1]
        assert event['type'] == kind
        assert event['data']['attempt'] == 2
        assert event['data']['will_retry'] is True
        assert event['data']['text'] == 'public result'
        assert 'thinking' not in event['data']
        assert 'headers' not in event['data']
        assert runtime.agent_jobs[0]['status'] == 'running'


def test_failed_stream_status_is_not_overwritten_before_terminal_receipt(tmp_path):
    with TestClient(create_app(tmp_path/'failed-stream.sqlite', worker_token='test', ticking=False)) as client:
        client.get('/api/health')
        runtime = client.app.state.runtime
        runtime.queue_agent('observe')
        headers = {'Authorization': 'Bearer test'}
        job = client.post('/internal/agent/next', json={}, headers=headers).json()['job']
        metadata = {'episode_id': runtime.episode, 'run_id': job['run_id']}
        for kind, text in [('message_start', ''), ('message_update', 'part'), ('message_update', 'ial'), ('message_end', 'partial')]:
            client.post('/internal/agent/event', headers=headers, json={**metadata, 'type': 'session_event',
                'event': {'type': kind, 'message_id': 'failed-message', 'text': text, 'status': 'failed'}}).raise_for_status()
        messages = [m for m in runtime.messages if m['id'] == 'failed-message']
        assert len(messages) == 1
        assert messages[0]['text'] == 'partial'
        assert messages[0]['status'] == 'failed'
