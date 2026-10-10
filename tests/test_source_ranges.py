import unittest
from workflow.source_ranges import expand,adjacent_replays,replay_runs

class SourceRangeTests(unittest.TestCase):
    def test_nested_trim_preserves_underlying_offsets(self):
        recipes={'s7':[{'source_sha256':'palm','start_frame':48,'frames':48},{'source_sha256':'hero','frames':48}],
                 'copy':[{'source_sha256':'s7','start_frame':24,'frames':72}]}
        self.assertEqual(expand('copy',0,48,recipes),[{'source_sha256':'palm','start_frame':72,'frames':24},{'source_sha256':'hero','start_frame':0,'frames':24}])
    def test_invalid_provenance_fails_closed(self):
        with self.assertRaises(ValueError):expand('s',0,10,{'s':[{'source_sha256':'s','frames':10}]})
        with self.assertRaises(ValueError):expand('s',0,11,{'s':[{'source_sha256':'raw','frames':10}]})
    def test_replay_is_distinct_from_continuation_and_nonadjacent_reuse(self):
        left={'shot':'S6','source_sha256':'palm','start_frame':0,'frames':48,'global_start_frame':480}
        right={'shot':'S7','source_sha256':'palm','start_frame':0,'frames':48,'global_start_frame':528}
        self.assertEqual(adjacent_replays([left,right])[0]['overlap_frames'],48)
        self.assertEqual(len(replay_runs([left,right])),1)
        self.assertEqual(adjacent_replays([left,{**right,'start_frame':48}]),[])
        self.assertEqual(adjacent_replays([left,{**right,'global_start_frame':600}]),[])
