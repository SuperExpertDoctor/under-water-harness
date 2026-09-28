import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from uuv_game.api import create_app
from uuv_game.runtime import MissionRuntime


class AdversaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.game = MissionRuntime(Path(self.directory.name) / 'mission.sqlite')

    def tearDown(self):
        self.game.close()
        self.directory.cleanup()

    def test_out_of_range_and_immutable_reads(self):
        game = self.game
        game.targets[0]['pose'] = [2000, 2000, 0]
        game.start()
        game.tick()
        before = copy.deepcopy(game.adversary)
        observation = game.adversary_observation()
        self.assertEqual(observation['detections'], [])
        self.assertNotIn('uuvs', observation)
        observation['own_pose'][0] = -100
        self.assertEqual(game.adversary, before)
        self.assertGreater(game.targets[0]['pose'][0], 0)

    def test_noisy_asymmetric_observations_and_no_truth_fields(self):
        game = self.game
        game.targets[0]['pose'] = [2000, 2000, 0]
        game.uuvs[0]['pose'] = [2500, 2000, 0]
        game.start()
        game.tick()
        observation = game.adversary_observation()
        self.assertEqual(len(game.targets), 1)
        self.assertEqual(game.config.enemy_sensor_range, 700)
        self.assertEqual(game.config.sensor_range, 350)
        self.assertEqual(game.config.enemy_max_speed, 3)
        self.assertEqual(len(observation['detections']), 1)
        self.assertNotEqual(observation['detections'][0]['range_m'], 500)
        for forbidden in ('UUV-1', 'remaining_range_m', 'energy', 'task', 'targets', 'uuvs'):
            self.assertNotIn(forbidden, json.dumps(observation))
        self.assertEqual(game.contacts, {})

    def test_controller_does_not_read_undetected_fleet_state(self):
        game = self.game
        game.targets[0]['pose'] = [2000, 2000, 0]
        game.start()
        game.tick()
        first_pose = game.targets[0]['pose'][:]
        game.targets[0]['pose'] = [2000, 2000, 0]
        game.sim_time = 0
        game.frame_id = 0
        for boat in game.uuvs:
            boat['pose'][0] += 100
            boat['remaining_range_m'] = 1234
        game.tick()
        self.assertEqual(game.targets[0]['pose'], first_pose)

    def test_old_checkpoint_migration_preserves_human_state(self):
        game = self.game
        episode = game.episode
        game.mode = 'full'
        game.messages = [{'id': 'human', 'text': 'keep mission'}]
        game.targets.append({'id': 'extra', 'pose': [10, 20, 0], 'speed': 2.5})
        game.targets[0].update(ais_enabled=True, vessel_class='type_ii')
        game.vessels = [{'scenario_entity_id': 'extra'}]
        game.close()
        self.game = MissionRuntime(Path(self.directory.name) / 'mission.sqlite')
        self.assertEqual(self.game.episode, episode)
        self.assertEqual(self.game.mode, 'full')
        self.assertEqual(self.game.messages, [{'id': 'human', 'text': 'keep mission'}])
        self.assertEqual(len(self.game.targets), 1)
        self.assertFalse(self.game.targets[0].get('ais_enabled', False))
        self.assertEqual(self.game.vessels, [])

    def test_frame_does_not_report_stale_tracking_phase_as_effective(self):
        game = self.game
        game.contacts['CONTACT-1'] = {'contact_id': 'CONTACT-1', 'state': 'degraded',
            'x': 1000, 'y': 1000, 'vx': 0, 'vy': 0, 'samples': [], 'observers': ['UUV-1']}
        game.active['UUV-1'] = {'kind': 'track', 'phase': 'tracking', 'contact_id': 'CONTACT-1'}
        self.assertFalse(game.frame()['uavs'][0]['effective_tracking'])
        game.contacts['CONTACT-1']['state'] = 'tracking'
        self.assertTrue(game.frame()['uavs'][0]['effective_tracking'])

    def test_migration_retires_removed_contact_tracks_without_stalling_coverage(self):
        game = self.game
        game.set_mode('full')
        candidate = game.calculate('plan_search', {'members': ['UUV-1'], 'bbox': [300, 300, 1700, 1700]})
        search = game.submit(candidate['result_id'], 'migration-search', game.episode)
        game.targets.append({'id': 'legacy-scene', 'pose': [2000, 2000, 0], 'speed': 2.5})
        game.contact_mapping['legacy-scene'] = 'CONTACT-9'
        game.contacts['CONTACT-9'] = {'contact_id': 'CONTACT-9', 'state': 'tracking', 'samples': []}
        game.active['UUV-2'] = {'kind': 'track', 'phase': 'tracking', 'generation': 1,
            'contact_id': 'CONTACT-9', 'plan_id': 'legacy-track'}
        game.plans['legacy-track'] = {'kind': 'track', 'status': 'active', 'plan_id': 'legacy-track',
            'contact_id': 'CONTACT-9', 'members': ['UUV-2']}
        game.plans['legacy-pending'] = {**game.plans['legacy-track'], 'plan_id': 'legacy-pending', 'status': 'pending_approval'}
        game.start()
        preserved = copy.deepcopy({'episode': game.episode, 'regions': game.regions,
            'search': game.plans[search['plan_id']], 'action': game.active['UUV-1'],
            'standing_policy': game.standing_policy, 'mode': game.mode})
        game.close()
        self.game = MissionRuntime(Path(self.directory.name) / 'mission.sqlite')
        restored = self.game
        self.assertEqual(restored.episode, preserved['episode'])
        self.assertEqual(restored.regions, preserved['regions'])
        self.assertEqual(restored.plans[search['plan_id']], preserved['search'])
        self.assertEqual(restored.active['UUV-1'], preserved['action'])
        self.assertEqual(restored.standing_policy, preserved['standing_policy'])
        self.assertEqual(restored.mode, preserved['mode'])
        self.assertNotIn('UUV-2', list(restored.active))
        self.assertEqual(restored.plans['legacy-track']['status'], 'superseded')
        self.assertEqual(restored.plans['legacy-pending']['status'], 'superseded')
        before = restored.uuvs[0]['pose'][:]
        restored.tick()
        self.assertEqual(restored.status, 'running')
        self.assertGreater(restored.sim_time, 0)
        self.assertNotEqual(restored.uuvs[0]['pose'], before)
        self.assertNotIn('CONTACT-9', restored.contacts)
        self.assertNotIn('legacy-scene', json.dumps(restored.frame()))

    def test_expired_parameters_use_safe_default(self):
        from uuv_game.adversary import control
        game = self.game
        pose, speed, curvature = control([2000, 2000, 0], [],
            {'speed_mps': 4, 'turn_bias': 1, 'expires_at_s': 5}, [], game.config, 6)
        self.assertEqual(speed, 2.5)
        self.assertLessEqual(abs(curvature), 1/game.config.enemy_turn_radius)
        self.assertTrue(all(math.isfinite(value) for value in pose))

    def test_boundary_and_obstacle_avoidance(self):
        from uuv_game.adversary import control
        from uuv_game.algorithms.planning import path_safe
        game = self.game
        for initial in ([3970, 2000, 0], [2300, 2300, 0], [40, 2000, math.pi]):
            pose = list(initial)
            for _ in range(100):
                next_pose, speed, curvature = control(pose, [], None, game.obstacles, game.config, 0)
                self.assertTrue(path_safe([pose, next_pose], game.obstacles, [0, 0, 4000, 4000], margin=20))
                self.assertLessEqual(speed, game.config.enemy_max_speed)
                self.assertLessEqual(abs(curvature), 1/game.config.enemy_turn_radius)
                pose = next_pose


class AdversaryApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(Path(self.directory.name) / 'mission.sqlite', worker_token='friendly', adversary_token='enemy', ticking=False))
        self.client.__enter__()
        self.client.get('/api/health')
        self.game = self.client.app.state.runtime
        self.game.start()
        self.headers = {'Authorization': 'Bearer enemy'}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def post(self, path, data):
        return self.client.post('/internal/adversary/' + path, json=data, headers=self.headers)

    def test_authentication_cross_side_runs_and_strict_parameters(self):
        self.assertEqual(self.client.post('/internal/adversary/next', json={}).status_code, 403)
        self.assertEqual(self.client.post('/internal/agent/next', json={}, headers=self.headers).status_code, 403)
        self.assertEqual(self.client.post('/internal/adversary/next', json={}, headers={'Authorization': 'Bearer friendly'}).status_code, 403)
        job = self.post('next', {}).json()['job']
        ids = {'episode_id': self.game.episode, 'run_id': job['run_id']}
        self.assertEqual(self.post('observation', ids).status_code, 200)
        self.assertEqual(self.post('observation', {**ids, 'run_id': 'run-friendly'}).status_code, 409)
        self.assertEqual(self.post('parameters', {**ids, 'speed_mps': 3, 'turn_bias': 0, 'duration_s': 10, 'target_position': [1, 2]}).status_code, 422)
        for value in (-1, 5):
            self.assertEqual(self.post('parameters', {**ids, 'speed_mps': value, 'turn_bias': 0, 'duration_s': 10}).status_code, 422)
        before = copy.deepcopy(self.game.events)
        self.assertEqual(self.post('parameters', {**ids, 'speed_mps': 3, 'turn_bias': .2, 'duration_s': 10}).status_code, 200)
        self.assertEqual(self.game.events, before)
        self.assertNotIn('detections', json.dumps(self.game.frame()))
        self.game.adversary['job']['lease_deadline'] = 0
        self.assertEqual(self.post('parameters', {**ids, 'speed_mps': 3, 'turn_bias': 0, 'duration_s': 10}).status_code, 409)

    def test_single_target_endpoints_reject_mutations(self):
        ids = {'episode_id': self.game.episode, 'command_id': 'scene'}
        for method, path in (('POST', '/api/vessels'), ('DELETE', '/api/vessels/TARGET-1'), ('PATCH', '/api/vessels/TARGET-1/ais')):
            response = self.client.request(method, path, json=ids)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()['error_code'], 'single_target_scene_locked')
        self.assertEqual(len(self.game.targets), 1)
        self.assertFalse(self.client.get('/api/scene').json()['vessel_mutation_allowed'])

    def test_reset_stop_and_expired_lease_cannot_keep_control(self):
        job = self.post('next', {}).json()['job']
        ids = {'episode_id': self.game.episode, 'run_id': job['run_id']}
        parameters = {**ids, 'speed_mps': 3, 'turn_bias': 1, 'duration_s': 60}
        self.post('parameters', parameters).raise_for_status()
        self.game.adversary['job']['lease_deadline'] = 0
        self.game.tick()
        self.assertIsNone(self.game.adversary['parameters'])
        self.assertEqual(self.post('heartbeat', ids).status_code, 409)
        self.game.reset()
        self.assertEqual(self.post('observation', ids).status_code, 409)
        self.game.start()
        new_job = self.post('next', {}).json()['job']
        self.game.stop()
        self.assertEqual(self.post('observation', new_job).status_code, 409)

    def test_nonfinite_parameters_and_actual_friendly_run_are_rejected(self):
        enemy_job = self.post('next', {}).json()['job']
        self.game.queue_agent('friendly-only')
        friendly_job = self.client.post('/internal/agent/next', json={}, headers={'Authorization': 'Bearer friendly'}).json()['job']
        self.assertEqual(self.post('observation', {'episode_id': self.game.episode, 'run_id': friendly_job['run_id']}).status_code, 409)
        self.assertEqual(self.client.post('/internal/tools/get_mission_state', json=enemy_job, headers={'Authorization': 'Bearer friendly'}).status_code, 409)
        for number in ('NaN', 'Infinity', '-Infinity', '1e999'):
            content = json.dumps(enemy_job)[:-1] + ',"speed_mps":' + number + ',"turn_bias":0,"duration_s":30}'
            response = self.client.post('/internal/adversary/parameters', content=content, headers={**self.headers, 'Content-Type': 'application/json'})
            self.assertEqual(response.status_code, 422)

    def test_parameterized_evasion_can_be_detected_and_cooperatively_tracked(self):
        self._run_parameterized_evasion(2.5)

    def test_maximum_speed_evasion_can_be_detected_and_cooperatively_tracked(self):
        self._run_parameterized_evasion(3.0)

    def _run_parameterized_evasion(self, speed):
        game = self.game
        game.set_mode('full')
        fleet = game.calculate('plan_search', {'standing_policy': True})
        game.submit(fleet['result_id'], 'enemy-scenario-search', game.episode)
        job = self.post('next', {}).json()['job']
        ids = {'episode_id': game.episode, 'run_id': job['run_id']}
        tracking = False
        confirmed_at = None
        while game.sim_time < 2400:
            if game.frame_id % 150 == 0:
                self.post('heartbeat', ids).raise_for_status()
                self.post('parameters', {**ids, 'speed_mps': speed, 'turn_bias': .2, 'duration_s': 60}).raise_for_status()
            game.tick()
            self.assertEqual(game.status, 'running', game.events[-3:])
            self.assertEqual(len(game.uuvs), 8)
            self.assertEqual(len(game.targets), 1)
            contact = next((contact for contact in game.contacts.values() if contact['state'] == 'confirmed'), None)
            if contact and not tracking:
                confirmed_at = game.sim_time
                candidate = game.calculate('plan_tracking', {'contact_id': contact['contact_id']})
                self.assertEqual(candidate['status'], 'succeeded')
                game.submit(candidate['result_id'], 'enemy-scenario-track', game.episode)
                tracking = True
            self.assertLessEqual(sum(action['kind'] == 'track' for action in game.active.values()), 3)
            if game.metrics['effective_tracking_seconds'] >= 5:
                break
        self.assertIsNotNone(confirmed_at)
        self.assertGreaterEqual(game.metrics['effective_tracking_seconds'], 5)
        print(json.dumps({'enemy_speed': speed, 'turn_bias': .2, 'confirmed_at_s': confirmed_at,
                          'sim_time_s': game.sim_time, 'effective_tracking_seconds': game.metrics['effective_tracking_seconds']}))


if __name__ == '__main__':
    unittest.main()
