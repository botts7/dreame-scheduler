"""External-history regressions; run with Python, without Home Assistant."""
import ast
import asyncio
import copy
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parents[1] / 'custom_components/dreame_scheduler'
spec = importlib.util.spec_from_file_location('external_history', BASE / 'external_history.py')
eh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eh)
UTC = timezone.utc
START = int(datetime(2026, 11, 19, 12, tzinfo=UTC).timestamp())


def record(**changes):
    return dict(timestamp=START, status='Room cleaning', completed=True,
                cleaning_time='20 min', cleaned_area='18.5 m²', mop_pad='Not installed', **changes)


def state():
    return {'history': [], 'week_start': '2026-11-16', 'maps': {'default': {
        'cleaned': {}, 'confirmed': {}, 'unreachable': {'1': 2}, 'manual_clean': {'1': 'blocked'}}}}


def cleaning(start=START, rooms=None):
    return dict(start=start, end=start+1200, rooms=['1'] if rooms is None else rooms,
                pad='Not installed', area='18.5 m²', map_verified=True, scope='Room cleaning', blocked=[])


class Records(unittest.TestCase):
    def passes(self, raw):
        return list(eh.completed_passes({'record': raw, 'friendly_name': 'History'}, START-1, START+3600))

    def test_valid_pass_identity(self):
        self.assertEqual(self.passes(record())[0][:3], (f'dreame_pass_{START}', START, START+1200))
        self.assertEqual(self.passes(record(multiple_cleaning_time=START-20))[0][0], f'dreame_pass_{START}')

    def test_invalid_records(self):
        for key, values in {
            'timestamp': [None, 'today', True, float('nan'), float('inf'), START-10, START+4000],
            'completed': [False, None, 'true', 1],
            'status': ['Mapping', 'Returning', [], {}],
            'cleaning_time': ['unknown', '-1 min', '120 min'],
            'cleaned_area': ['0 m²', 'unknown', '-1 m²'],
            'interrupt_reason': ['User', 'Fault'],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    raw=record(); raw[key]=value
                    self.assertEqual(self.passes(raw), [])

    def test_no_area_or_target_only_fallback(self):
        raw=record(); raw['status']='Cleaning'; raw['cleaned_area']='200 m²'
        for evidence in [None, {}, {'targeted': [1, 2]}, {'cleaned': []}]:
            self.assertEqual(eh.credit(raw, {'1','2'}, {'1','2'}, evidence), [])

    def test_evidence_intersection_and_blocked(self):
        raw=record(blocked_rooms={'2':'blocked'})
        self.assertEqual(eh.credit(raw, {'1','2','3'}, {'1','2','4'},
                                  {'cleaned':[1,2,3,4], 'targeted':[1,2,3]}), ['1'])
        raw['skipped_rooms']=['1']
        self.assertEqual(eh.credit(raw, {'1','2'}, {'1','2'}, {'cleaned':[1,2]}), [])
        for value in ['1', ['bad'], [1,'bad']]:
            raw=record(blocked_rooms=value)
            self.assertEqual(eh.credit(raw, {'1'}, {'1'}, {'cleaned':[1]}), [])

    def test_zone_and_spot_do_not_credit(self):
        for scope in ('Zone cleaning','Spot cleaning','Partial cleaning'):
            raw=record();raw['status']=scope
            self.assertEqual(eh.credit(raw, {'1'}, {'1'}, {'cleaned':[1]}), [])

    def test_dispatch_boundaries(self):
        for start in (START, START+60):
            self.assertTrue(eh.owned_by_scheduler(start, [(START+0.9,START+60)]))
        self.assertFalse(eh.owned_by_scheduler(START-1, [(START,START+60)]))


class Reconciliation(unittest.TestCase):
    def apply(self, s, passes=None, key='session', tz=UTC):
        return eh.apply_pass(s,key,(passes or [cleaning()])[0],{'1':'Room one'},tz)

    def test_credit_and_idempotence_across_reload(self):
        s=state();self.assertTrue(self.apply(s))
        slot=s['maps']['default']; ts=datetime.fromtimestamp(START+1200,UTC).isoformat()
        self.assertEqual(slot['confirmed'], {'1':ts});self.assertEqual(slot['cleaned'], {'1':ts})
        self.assertEqual(slot['manual_clean'],{});self.assertEqual(slot['unreachable'],{})
        self.assertEqual(s['last_clean'],ts)
        snapshot=copy.deepcopy(s);self.assertFalse(self.apply(s));self.assertEqual(s,snapshot)
        import json
        reloaded=json.loads(json.dumps(s));self.assertFalse(self.apply(reloaded))

    def test_cross_week_preserves_confirmation_without_current_week_credit(self):
        s=state();s['week_start']='2026-11-23';self.apply(s)
        slot=s['maps']['default'];self.assertTrue(slot['confirmed']);self.assertEqual(slot['cleaned'],{})

    def test_newer_confirmation_is_not_overwritten(self):
        s=state();newer=datetime.fromtimestamp(START+3000,UTC).isoformat()
        slot=s['maps']['default'];slot['confirmed']['1']=newer;slot['cleaned']['1']=newer
        s['last_clean']=newer;self.apply(s)
        self.assertEqual(slot['confirmed']['1'],newer);self.assertEqual(slot['cleaned']['1'],newer)
        self.assertEqual(s['last_clean'],newer)

    def test_later_misses_are_preserved(self):
        for map_key, preserved in [('default',True),('other',False)]:
            s=state();s['history']=[{'ts':datetime.fromtimestamp(START+2400,UTC).isoformat(),
                'map':map_key,'per_room':{'1':{'status':'failed'}}}]
            self.apply(s)
            self.assertEqual('1' in s['maps']['default']['manual_clean'],preserved)
            self.assertEqual('1' in s['maps']['default']['unreachable'],preserved)

    def test_sequential_passes_keep_room_times_and_unknown_mop_mode(self):
        s=state();self.apply(s);p=cleaning(START+2400,['2']);p['pad']='Installed'
        self.apply(s,[p],key='second');self.assertEqual(len(s['history']),2)
        self.assertEqual(set(s['history'][0]['per_room']),{'1'})
        self.assertIsNone(s['history'][1]['per_room']['2']['will_mop'])
        self.assertEqual(s['maps']['default']['confirmed']['1'],datetime.fromtimestamp(START+1200,UTC).isoformat())

    def test_later_pass_does_not_move_earlier_room_past_failure(self):
        s=state();self.apply(s)
        s['history'].append({'ts':datetime.fromtimestamp(START+1800,UTC).isoformat(),
                             'map':'default','per_room':{'1':{'status':'failed'}}})
        self.apply(s,[cleaning(START+2400,['2'])],key='second')
        spec=importlib.util.spec_from_file_location('history_analytics',BASE/'history_analytics.py')
        analytics=importlib.util.module_from_spec(spec);spec.loader.exec_module(analytics)
        self.assertEqual(analytics.fails_since(s['history'],'1',
                         s['maps']['default']['confirmed']['1']),1)

    def test_week_is_local_and_dst_aware(self):
        tz=ZoneInfo('America/Vancouver');start=int(datetime(2026,11,22,23,0,tzinfo=tz).timestamp())
        s=state();self.apply(s,[cleaning(start)],tz=tz)
        self.assertEqual(s['history'][0]['weekday'],6);self.assertIn('1',s['maps']['default']['cleaned'])

    def test_transient_missing_evidence_preserves_prior_pass(self):
        s=state();self.apply(s);p=cleaning(rooms=[]);p['map_verified']=False
        self.assertFalse(self.apply(s,[p]));self.assertIn('1',s['history'][0]['per_room'])

    def test_history_cap_and_foreign_source(self):
        s=state();s['history']=[{'source_id':'session','source':'other'}]
        self.assertFalse(self.apply(s))
        s=state()
        for i in range(205):self.apply(s,[cleaning(START+i*60)],key=str(i))
        self.assertEqual(len(s['history']),200);self.assertEqual(s['history'][0]['source_id'],'5')


# Execute the actual engine method against minimal HA/coordinator doubles. This
# tests orchestration without importing the full HA runtime or calling the robot.
pkg=ModuleType('external_test_package');pkg.external_history=eh
maps=ModuleType('external_test_package.dreame_mapdata')
maps.resolve_coordinator=lambda hass, entity: hass.coordinator
sys.modules[pkg.__name__]=pkg;sys.modules[maps.__name__]=maps
engine_ast=ast.parse((BASE/'engine.py').read_text())
engine_class=next(n for n in engine_ast.body if isinstance(n,ast.ClassDef) and n.name=='SchedulerEngine')
method=next(n for n in engine_class.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_credit_external_history')
namespace={'__package__':pkg.__name__, 'entity_of':lambda domain,prefix,suffix:f'{domain}.{prefix}_{suffix}',
           'all_enabled_segments':lambda rooms:list(rooms)}
exec(compile(ast.Module(body=[method],type_ignores=[]),str(BASE/'engine.py'),'exec'),namespace)


class Engine(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.s=state();self.s['external_since']=START-1;self.saves=0
        async def save(): self.saves+=1
        self.map=NS(last_updated=START,saved_map_id=10,active_segments=[1],cleaned_segments=[1])
        self.status=NS(map_list=[10],multi_map=False,_history_map_data={'map':self.map},
                       _cleaning_history=[NS(date=datetime.fromtimestamp(START,UTC),object_name='map')])
        self.entities={'sensor.test_cleaning_history':NS(attributes={'record':record()}),
                       'vacuum.test':NS(attributes={'rooms':{'Floor':[{'id':1,'name':'Room one'}]}})}
        self.engine=NS(hass=NS(states=NS(get=self.entities.get),coordinator=NS(device=NS(status=self.status))),
                       tracker=NS(state=self.s,active_map='default',async_save=save),_prefix='test',
                       _vacuum_entity='vacuum.test',_external_save_pending=False,_rooms=lambda:{'1':{}},_push=lambda:None)

    async def run_credit(self):
        await namespace['_credit_external_history'](self.engine,datetime.fromtimestamp(START+3600,UTC))

    async def test_end_to_end_and_delayed_map(self):
        self.status._history_map_data={};await self.run_credit();self.assertEqual(self.saves,0)
        self.status._history_map_data={'map':self.map};await self.run_credit()
        self.assertIn('1',self.s['maps']['default']['confirmed']);self.assertEqual(self.saves,1)
        await self.run_credit();self.assertEqual(self.saves,1)

    async def test_wrong_unknown_stale_maps_and_multifloor(self):
        for field,value in [('saved_map_id',20),('saved_map_id',None),('last_updated',START-1)]:
            before=getattr(self.map,field);setattr(self.map,field,value)
            await self.run_credit();self.assertEqual(self.saves,0);setattr(self.map,field,before)
        self.status.multi_map=True;await self.run_credit();self.assertEqual(self.saves,0)
        self.status.multi_map=False;self.engine.tracker.active_map='20'
        await self.run_credit();self.assertEqual(self.saves,0)

    async def test_scheduler_owned_history_is_excluded(self):
        self.s['dispatch_windows']=[[datetime.fromtimestamp(START-1,UTC).isoformat(),datetime.fromtimestamp(START+1500,UTC).isoformat()]]
        await self.run_credit();self.assertEqual(self.saves,0)

    async def test_failed_save_is_retried_even_if_history_disappears(self):
        async def fail():raise OSError('disk unavailable')
        self.engine.tracker.async_save=fail
        with self.assertRaises(OSError):await self.run_credit()
        self.assertTrue(self.engine._external_save_pending)
        async def save():self.saves+=1
        self.engine.tracker.async_save=save;self.entities.clear();await self.run_credit()
        self.assertEqual(self.saves,1);self.assertFalse(self.engine._external_save_pending)


class Ownership(unittest.TestCase):
    def test_record_and_prune_ownership_windows(self):
        tree=ast.parse((BASE/'week_tracker.py').read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='WeekTracker')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_remember_dispatch')
        ns={'datetime':datetime,'timezone':timezone}
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(BASE/'week_tracker.py'),'exec'),ns)
        tracker=NS(_state={'external_since':START-100})
        remember=ns['_remember_dispatch']
        remember(tracker,{'started':datetime.fromtimestamp(START,UTC).isoformat()},None)
        self.assertEqual(len(tracker._state['dispatch_windows']),1)
        remember(tracker,None,None);self.assertEqual(len(tracker._state['dispatch_windows']),1)
        windows=[[datetime.fromtimestamp(START+i,UTC).isoformat(),
                  datetime.fromtimestamp(START+i+1,UTC).isoformat()] for i in range(200)]
        tracker._state['dispatch_windows']=windows
        remember(tracker,{'started':datetime.fromtimestamp(START+300,UTC).isoformat()},None)
        self.assertEqual(len(tracker._state['dispatch_windows']),200)
        self.assertEqual(tracker._state['external_since'],START+1)


if __name__=='__main__':unittest.main(verbosity=2)
